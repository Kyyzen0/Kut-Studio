"""Composition GPU de l'aperçu : description d'une image, passes, uniformes, référence.

Ce module ne parle à aucun GPU. Il décrit **ce qu'il faut dessiner** :

1. :class:`CompositeFrame` — le cadre et ses calques vidéo (source décodée,
   matrice calque → cadre identique à l'export, opacité, mode de fusion,
   effets, matte de masques) ;
2. :func:`plan_frame` — la suite de passes (:class:`PassSpec`) et les textures
   intermédiaires nécessaires, avec leurs **uniformes déjà empaquetés** (std140) ;
3. :func:`reference_frame` — le même calcul en numpy (oracle des tests).

Le backend (``ui/gpu_preview.py``, QRhi : Metal, Direct3D, Vulkan, OpenGL)
se contente d'exécuter les passes. Un backend factice exécute le même plan dans
les tests : la logique entière est vérifiable sans GPU.

Étapes d'un calque (séparées, comme le demande l'architecture de l'aperçu) ::

    décodage (plans YUV) ─▶ prep : YUV → calque (adaptation + bandes du pad,
                              suréchantillonnage, opérations ponctuelles)
                         ─▶ voisinage : flou H/V, netteté (0..n passes)
                         ─▶ grade : étalonnage, LUT 3D cuite par FFmpeg (core.gpu_grade)
                         ─▶ composite : matrice inverse, matte, opacité, fusion
    toutes les couches ─▶ present : cadre → widget (letterbox)

Repère : un calque vidéo a la **taille du cadre** (comme à l'export : média
adapté + ``pad`` noir opaque). Les textures de calque et de cadre sont à la
résolution de rendu (``render_scale`` × taille de la séquence).
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field

from .blend_modes import BlendMode, coerce_blend_mode
from .gpu_effects import (
    MAX_BLUR_RADIUS,
    MAX_POINT_OPS,
    SPACE_RGB,
    SPACE_YUV,
    BlurOp,
    EffectProgram,
    GlowOp,
    HazeOp,
    PointOp,
    SharpenOp,
    ShiftOp,
    gblur_weights,
)
from .gpu_frames import LAYOUTS, column_major, invert4, yuv_to_rgb_matrix

Affine = tuple[float, float, float, float, float, float]
"""``(a, b, c, d, e, f)`` : ``x' = a·x + c·y + e``, ``y' = b·x + d·y + f`` (convention de Qt)."""

BLEND_CODES: dict[BlendMode, int] = {
    BlendMode.NORMAL: 0,
    BlendMode.MULTIPLY: 1,
    BlendMode.SCREEN: 2,
    BlendMode.OVERLAY: 3,
    BlendMode.DARKEN: 4,
    BlendMode.LIGHTEN: 5,
    BlendMode.ADD: 6,
    BlendMode.DIFFERENCE: 7,
}
"""Les huit modes du produit, tous composés sur GPU (formules W3C séparables)."""

MAX_SUPERSAMPLE = 4
"""Taps par axe quand une source est fortement réduite (4K affichée en 960 px)."""

UNIFORM_FLOATS = 16 * 3 + 4 * 9 + 4 * 25 * 2 + 4 * 32
UNIFORM_BYTES = UNIFORM_FLOATS * 4
"""Taille du bloc ``Params`` commun à tous les shaders (std140, uniquement vec4/mat4)."""

WORKING_FORMAT = "RGBA16F"
CANVAS_FORMAT = "RGBA8"


# --- Description ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class VideoSource:
    """Une image décodée telle qu'elle arrive (format, taille, couleur)."""

    id: str
    layout: str
    width: int
    height: int
    colorspace: str = ""
    color_range: str = ""

    @property
    def chroma_scale(self) -> tuple[float, float]:
        spec = LAYOUTS[self.layout]
        if not spec.is_yuv or len(spec.planes) < 2:
            return (1.0, 1.0)
        plane = spec.planes[1]
        return (float(plane.width_divisor), float(plane.height_divisor))


@dataclass(frozen=True)
class CompositeLayer:
    """Un calque vidéo à composer.

    Attributes:
        source: identifiant d'une :class:`VideoSource`.
        matrix: calque (pixels de la séquence) → cadre (pixels de la séquence),
            :func:`core.tracking_motion.video_layer_matrix` — la même que l'export.
        fit: rectangle utile du média dans le calque (``x0, y0, x1, y1`` en pixels
            de la séquence) ; hors du rectangle : bandes noires opaques (``pad``).
        opacity: opacité 0..1.
        blend: mode de fusion avec ce qui est dessous.
        program: effets GPU (:func:`core.gpu_effects.program_for`).
        effect_scale: échelle du calque à l'export (σ du flou en pixels de l'export).
        matte: clé d'une texture de matte (alpha des masques, espace calque) ou ``""``.
        grade: étalonnage du clip (:class:`core.color_grading.ColorGrade`) que le moniteur doit montrer ; le
            moniteur le résout en ``grade_lut`` une fois l'espace du média connu (:mod:`core.gpu_grade`).
        grade_lut: clé de l'atlas de la LUT d'étalonnage (texture téléversée comme une matte) ou ``""`` : une passe
            ``grade`` s'insère alors entre les effets et la composition, comme à l'export.
        grade_split: comparaison avant / après (page Couleur) : la part gauche **du cadre** (0..1, là où le viewer
            trace son trait) reste sans étalonnage, quel que soit le transform du calque ; 0 : tout est étalonné.
    """

    source: str
    matrix: Affine
    fit: tuple[float, float, float, float]
    opacity: float = 1.0
    blend: BlendMode = BlendMode.NORMAL
    program: EffectProgram = field(default_factory=EffectProgram)
    effect_scale: tuple[float, float] = (1.0, 1.0)
    matte: str = ""
    grade: object = None
    grade_lut: str = ""
    grade_split: float = 0.0


@dataclass(frozen=True)
class AdjustmentLayer:
    """Calque d'effets : ses effets s'appliquent à **tout ce qui est dessous**.

    Comme à l'export (:func:`core.mograph_ffmpeg._compose_adjustment`) : la
    composition courante est copiée, les effets s'appliquent à la copie, qui est
    reposée à travers la couverture du calque (``matte`` : alpha, espace du cadre).
    """

    program: EffectProgram
    matte: str = ""
    opacity: float = 1.0
    grade: object = None
    """Étalonnage du calque d'effets (appliqué après ses effets, en RVB, comme à l'export)."""
    grade_lut: str = ""


@dataclass(frozen=True)
class CompositeFrame:
    """Une image du moniteur : taille de la séquence, résolution de rendu, calques (bas → haut)."""

    canvas_width: int
    canvas_height: int
    render_scale: float = 1.0
    layers: tuple[CompositeLayer, ...] = ()
    sources: tuple[VideoSource, ...] = ()
    background: tuple[float, float, float] = (0.0, 0.0, 0.0)
    adjustments: tuple[AdjustmentLayer, ...] = ()

    @property
    def render_size(self) -> tuple[int, int]:
        scale = max(1e-3, float(self.render_scale))
        return (max(2, int(round(self.canvas_width * scale))), max(2, int(round(self.canvas_height * scale))))

    def source(self, source_id: str) -> VideoSource | None:
        for item in self.sources:
            if item.id == source_id:
                return item
        return None


# --- Plan de passes ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class TextureSpec:
    format: str
    width: int
    height: int


@dataclass(frozen=True)
class PassSpec:
    """Une passe : shader, cible, textures lues (jusqu'à 3), uniformes empaquetés."""

    shader: str
    target: str
    inputs: tuple[str, ...]
    uniforms: bytes
    layer: int = -1


@dataclass(frozen=True)
class FramePlan:
    passes: tuple[PassSpec, ...]
    textures: dict[str, TextureSpec]
    canvas: str
    """Texture finale du cadre (entrée de la passe ``present``)."""

    @property
    def pass_count(self) -> int:
        return len(self.passes)


@dataclass
class Uniforms:
    """Contenu du bloc ``Params`` (voir ``tools/gpu/shader_sources.py``)."""

    yuv_to_rgb: tuple = field(default_factory=lambda: yuv_to_rgb_matrix())
    rgb_to_yuv: tuple | None = None
    inverse_map: Affine = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    target: tuple[float, float, float, float] = (1.0, 1.0, 1.0, 0.0)
    source: tuple[float, float, float, float] = (1.0, 1.0, 1.0, 0.0)
    fit: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0)
    pad: tuple[float, float, float, float] = (16 / 255, 128 / 255, 128 / 255, 1.0)
    state: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    comp: tuple[float, float, float, float] = (1.0, 0.0, 0.0, 1.0)
    blur: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    misc: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    reserved: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    weights_luma: tuple[float, ...] = ()
    weights_chroma: tuple[float, ...] = ()
    ops: tuple[PointOp, ...] = ()

    def pack(self) -> bytes:
        rgb_to_yuv = self.rgb_to_yuv or invert4(self.yuv_to_rgb)
        a, b, c, d, e, f = self.inverse_map
        inverse = ((a, c, 0.0, e), (b, d, 0.0, f), (0.0, 0.0, 1.0, 0.0), (0.0, 0.0, 0.0, 1.0))
        data: list[float] = [
            *column_major(self.yuv_to_rgb), *column_major(rgb_to_yuv), *column_major(inverse),
            *self.target, *self.source, *self.fit, *self.pad, *self.state, *self.comp, *self.blur,
            *self.misc, *self.reserved,
        ]
        data += _padded(self.weights_luma, 100) + _padded(self.weights_chroma, 100)
        ops = [value for op in self.ops for value in op.packed()]
        data += _padded(tuple(ops), 128)
        if len(data) != UNIFORM_FLOATS:
            raise AssertionError(f"bloc d'uniformes : {len(data)} flottants au lieu de {UNIFORM_FLOATS}")
        return struct.pack(f"<{UNIFORM_FLOATS}f", *data)


def _padded(values: tuple[float, ...], size: int) -> list[float]:
    return list(values[:size]) + [0.0] * max(0, size - len(values))


def affine_mul(m: Affine, n: Affine) -> Affine:
    """``m ∘ n`` (``n`` d'abord)."""
    a1, b1, c1, d1, e1, f1 = m
    a2, b2, c2, d2, e2, f2 = n
    return (
        a1 * a2 + c1 * b2, b1 * a2 + d1 * b2,
        a1 * c2 + c1 * d2, b1 * c2 + d1 * d2,
        a1 * e2 + c1 * f2 + e1, b1 * e2 + d1 * f2 + f1,
    )


def affine_inverse(m: Affine) -> Affine | None:
    a, b, c, d, e, f = m
    det = a * d - b * c
    if abs(det) < 1e-12 or not math.isfinite(det):
        return None
    ia, ib, ic, id_ = d / det, -b / det, -c / det, a / det
    return (ia, ib, ic, id_, -(ia * e + ic * f), -(ib * e + id_ * f))


def _supersample(source: VideoSource, render: tuple[int, int], fit: tuple[float, ...], scale: float) -> float:
    """Taps par axe pour qu'une forte réduction ne scintille pas (bilinéaire seul : alias)."""
    shown_w = max(1.0, (fit[2] - fit[0]) * scale)
    ratio = source.width / shown_w
    return float(max(1, min(MAX_SUPERSAMPLE, int(math.ceil(ratio - 0.25)))))


def plan_frame(frame: CompositeFrame) -> FramePlan:
    """Passes nécessaires pour ``frame`` (ordre d'exécution) et textures intermédiaires."""
    width, height = frame.render_size
    scale = width / float(max(1, frame.canvas_width))
    textures: dict[str, TextureSpec] = {
        "canvas0": TextureSpec(CANVAS_FORMAT, width, height),
        "canvas1": TextureSpec(CANVAS_FORMAT, width, height),
    }
    passes: list[PassSpec] = []
    canvas = "canvas0"
    background = (*frame.background, 1.0)
    passes.append(PassSpec("clear", canvas, (), Uniforms(pad=background).pack()))
    to_render = (scale, 0.0, 0.0, scale, 0.0, 0.0)
    from_render = (1.0 / scale, 0.0, 0.0, 1.0 / scale, 0.0, 0.0)
    for index, layer in enumerate(frame.layers):
        source = frame.source(layer.source)
        if source is None or source.layout not in LAYOUTS:
            continue
        forward = affine_mul(to_render, affine_mul(layer.matrix, from_render))
        inverse = affine_inverse(forward)
        if inverse is None or layer.opacity <= 0.0:
            continue
        layout = LAYOUTS[source.layout]
        matrix = yuv_to_rgb_matrix(source.colorspace, source.color_range)
        fit_uv = (layer.fit[0] / frame.canvas_width, layer.fit[1] / frame.canvas_height,
                  layer.fit[2] / frame.canvas_width, layer.fit[3] / frame.canvas_height)
        pixel = (scale / max(1e-6, abs(layer.effect_scale[0])), scale / max(1e-6, abs(layer.effect_scale[1])))
        current, space = _effect_passes(
            passes, textures, f"layer{index}", (f"src:{layer.source}",), layout, source.width, source.height,
            matrix, fit_uv, layer.program, pixel, source.chroma_scale if layout.is_yuv else (1.0, 1.0),
            (width, height), _supersample(source, (width, height), layer.fit, scale), index, (scale, scale),
        )
        if layer.grade_lut:
            domain = SPACE_YUV if layout.is_yuv else SPACE_RGB
            current, space = _grade_pass(passes, textures, f"layer{index}", current, space, domain, layer.grade_lut,
                                         matrix, (width, height), index, split=layer.grade_split, to_canvas=forward)
        target = "canvas1" if canvas == "canvas0" else "canvas0"
        inputs = (current, f"matte:{layer.matte}" if layer.matte else "none", canvas)
        passes.append(PassSpec("composite", target, inputs, Uniforms(
            yuv_to_rgb=matrix,
            inverse_map=inverse,
            target=(float(width), float(height), 1.0, 0.0),
            source=(float(width), float(height), 1.0, 0.0),
            state=(float(space), float(SPACE_RGB), 0.0, 0.0),
            comp=(max(0.0, min(1.0, float(layer.opacity))), float(BLEND_CODES[coerce_blend_mode(layer.blend)]),
                  1.0 if layer.matte else 0.0, 1.0),
        ).pack(), index))
        canvas = target
    for number, adjustment in enumerate(frame.adjustments):
        if (adjustment.program.is_identity and not adjustment.grade_lut) or adjustment.opacity <= 0.0:
            continue
        index = len(frame.layers) + number
        current, space = _effect_passes(
            passes, textures, f"adjust{number}", (canvas,), LAYOUTS["rgba"], width, height,
            yuv_to_rgb_matrix(), (0.0, 0.0, 1.0, 1.0), adjustment.program, (scale, scale), (1.0, 1.0),
            (width, height), 1.0, index,
        )
        if adjustment.grade_lut:
            current, space = _grade_pass(passes, textures, f"adjust{number}", current, space, SPACE_RGB,
                                         adjustment.grade_lut, yuv_to_rgb_matrix(), (width, height), index)
        target = "canvas1" if canvas == "canvas0" else "canvas0"
        inputs = (current, f"matte:{adjustment.matte}" if adjustment.matte else "none", canvas)
        passes.append(PassSpec("composite", target, inputs, Uniforms(
            inverse_map=(1.0, 0.0, 0.0, 1.0, 0.0, 0.0),
            target=(float(width), float(height), 1.0, 0.0),
            source=(float(width), float(height), 1.0, 0.0),
            state=(float(space), float(SPACE_RGB), 0.0, 0.0),
            comp=(max(0.0, min(1.0, float(adjustment.opacity))), 0.0, 1.0 if adjustment.matte else 0.0, 0.0),
        ).pack(), index))
        canvas = target
    return FramePlan(tuple(passes), textures, canvas)


def _effect_passes(passes, textures, name, prep_inputs, layout, source_width, source_height, matrix, fit_uv,
                   program, pixel, chroma, size, taps, index, layer_pixel=None):
    """Passes « prep » puis voisinage d'un calque ; retourne ``(texture, espace)`` du résultat.

    ``pixel`` : texels par pixel de l'export **à l'échelle du clip** (flou, bloom, aberration) ; ``layer_pixel`` :
    texels par pixel du calque avant son échelle (heat haze, qui déforme l'image du clip elle-même)."""
    layer_pixel = layer_pixel or pixel
    width, height = size
    work_a, work_b = f"{name}a", f"{name}b"
    textures[work_a] = TextureSpec(WORKING_FORMAT, width, height)
    start_space = SPACE_YUV if layout.is_yuv else SPACE_RGB
    pad = (16 / 255, 128 / 255, 128 / 255, 1.0) if layout.is_yuv else (0.0, 0.0, 0.0, 1.0)
    segment = program.segments[0]
    out_space = SPACE_YUV if program.neighborhood else _end_space(start_space, segment)
    passes.append(PassSpec("prep", work_a, prep_inputs, Uniforms(
        yuv_to_rgb=matrix,
        target=(float(width), float(height), 1.0, taps),
        source=(float(source_width), float(source_height), layout.value_scale, float(layout.mode)),
        fit=fit_uv, pad=pad,
        state=(float(start_space), float(out_space), float(len(segment)), 1.0 if layout.swizzle_bgra else 0.0),
        ops=segment,
    ).pack(), index))
    current, other = work_a, work_b
    space = out_space
    for step, op in enumerate(program.neighborhood):
        textures.setdefault(work_b, TextureSpec(WORKING_FORMAT, width, height))
        following = program.segments[step + 1]
        next_space = SPACE_YUV if step + 1 < len(program.neighborhood) else _end_space(SPACE_YUV, following)
        if isinstance(op, BlurOp):
            for axis, (dx, dy) in enumerate(((1.0, 0.0), (0.0, 1.0))):
                rl, wl = gblur_weights(op.sigma * pixel[axis])
                rc, wc = gblur_weights(op.sigma * pixel[axis] * chroma[axis])
                last = axis == 1
                passes.append(PassSpec("blur", other, (current,), Uniforms(
                    yuv_to_rgb=matrix,
                    target=(float(width), float(height), 1.0, 0.0),
                    state=(float(space), float(next_space if last else SPACE_YUV),
                           float(len(following) if last else 0), 0.0),
                    blur=(dx, dy, float(rl), float(rc)),
                    weights_luma=wl, weights_chroma=wc,
                    ops=following if last else (),
                ).pack(), index))
                current, other = other, current
        elif isinstance(op, SharpenOp):
            passes.append(PassSpec("sharpen", other, (current,), Uniforms(
                yuv_to_rgb=matrix,
                target=(float(width), float(height), 1.0, 0.0),
                state=(float(space), float(next_space), float(len(following)), 0.0),
                misc=(float(op.amount), 0.0, 0.0, 0.0),
                ops=following,
            ).pack(), index))
            current, other = other, current
        elif isinstance(op, ShiftOp):
            passes.append(PassSpec("shift", other, (current,), Uniforms(
                yuv_to_rgb=matrix,
                target=(float(width), float(height), 1.0, 0.0),
                state=(float(space), float(next_space), float(len(following)), 0.0),
                misc=(float(op.pixels * pixel[0]), 0.0, 0.0, 0.0),
                ops=following,
            ).pack(), index))
            current, other = other, current
        elif isinstance(op, HazeOp):
            passes.append(PassSpec("haze", other, (current,), Uniforms(
                yuv_to_rgb=matrix,
                target=(float(width), float(height), 1.0, 0.0),
                state=(float(space), float(next_space), float(len(following)), 0.0),
                misc=(float(op.amplitude), float(op.frequency), float(op.phase), float(layer_pixel[0])),
                reserved=(float(op.top), float(op.span), float(layer_pixel[1]), 0.0),
                ops=following,
            ).pack(), index))
            current, other = other, current
        elif isinstance(op, GlowOp):
            current = _glow_passes(passes, textures, name, op, current, other, space, next_space, following, matrix,
                                   pixel, size, index)
            other = work_b if current == work_a else work_a
        space = next_space
    return current, space


def _glow_passes(passes, textures, name, op: GlowOp, current, other, space, next_space, following, matrix, pixel,
                 size, index) -> str:
    """Bloom : seuil et gain (RVB) → flou horizontal → flou vertical → ajout à l'image ; retourne la texture du
    résultat. Deux textures de travail en plus (le halo et son flou intermédiaire)."""
    width, height = size
    bright, scratch = f"{name}c", f"{name}d"
    textures.setdefault(bright, TextureSpec(WORKING_FORMAT, width, height))
    textures.setdefault(scratch, TextureSpec(WORKING_FORMAT, width, height))
    base = dict(yuv_to_rgb=matrix, target=(float(width), float(height), 1.0, 0.0))
    passes.append(PassSpec("glow", bright, (current,), Uniforms(
        **base, state=(float(space), float(SPACE_RGB), 0.0, 0.0),
        misc=(0.0, float(op.threshold), float(op.gain), 0.0),
    ).pack(), index))
    source, target = bright, scratch
    for dx, dy, scale in ((1.0, 0.0, pixel[0]), (0.0, 1.0, pixel[1])):
        radius, weights = gblur_weights(op.sigma * scale)
        passes.append(PassSpec("blur", target, (source,), Uniforms(
            **base, state=(float(SPACE_RGB), float(SPACE_RGB), 0.0, 0.0),
            blur=(dx, dy, float(radius), float(radius)), weights_luma=weights, weights_chroma=weights,
        ).pack(), index))
        source, target = target, source
    passes.append(PassSpec("glow", other, (current, source), Uniforms(
        **base, state=(float(space), float(next_space), float(len(following)), 0.0),
        misc=(1.0, 0.0, 0.0, 0.0), ops=following,
    ).pack(), index))
    return other


def _grade_pass(passes, textures, name, current, space, domain, lut, matrix, size, index,
                *, split: float = 0.0, to_canvas: Affine = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)) -> tuple[str, int]:
    """Étalonnage : la couleur du calque (après ses effets) passe dans la LUT cuite ; le résultat est en RVB.

    ``domain`` : espace d'entrée de la LUT (celui du calque à l'export : YUV pour une vidéo YUV, RVB sinon).
    ``split`` : la part gauche du cadre (0..1) laissée sans étalonnage (comparaison avant / après) ; ``to_canvas`` :
    pixel du calque → pixel du cadre (la matrice que la composition inverse), pour la décider là où le viewer trace
    son trait, même pour un calque déplacé ou tourné.
    """
    from .gpu_grade import LUT_SIZE

    width, height = size
    target = f"{name}b" if current == f"{name}a" else f"{name}a"
    textures.setdefault(target, TextureSpec(WORKING_FORMAT, width, height))
    passes.append(PassSpec("grade", target, (current, f"matte:{lut}"), Uniforms(
        yuv_to_rgb=matrix,
        inverse_map=to_canvas,
        target=(float(width), float(height), 1.0, 0.0),
        state=(float(space), float(SPACE_RGB), 0.0, 0.0),
        misc=(float(LUT_SIZE), float(domain), float(min(1.0, max(0.0, split))), 0.0),
    ).pack(), index))
    return target, SPACE_RGB


def _end_space(space: int, ops) -> int:
    for op in ops:
        space = op.space
    return space


def present_uniforms(viewport: tuple[float, float], rect: tuple[float, float, float, float],
                     background: tuple[float, float, float]) -> bytes:
    """Passe finale : le cadre dans ``rect`` (pixels du widget), fond ailleurs."""
    x, y, w, h = rect
    vw, vh = max(1.0, viewport[0]), max(1.0, viewport[1])
    return Uniforms(
        target=(vw, vh, 1.0, 0.0),
        fit=(x / vw, y / vh, (x + w) / vw, (y + h) / vh),
        pad=(*background, 1.0),
    ).pack()


# --- Référence CPU (numpy) ----------------------------------------------------------------------------------

def _blend(np, mode: int, cb, cs):
    """Formules W3C séparables (fond ``cb``, calque ``cs``, RVB 0..1)."""
    if mode == 1:
        return cb * cs
    if mode == 2:
        return cb + cs - cb * cs
    if mode == 3:  # overlay = hardlight(calque, fond) : teste le fond
        return np.where(cb <= 0.5, 2.0 * cb * cs, 1.0 - 2.0 * (1.0 - cb) * (1.0 - cs))
    if mode == 4:
        return np.minimum(cb, cs)
    if mode == 5:
        return np.maximum(cb, cs)
    if mode == 6:
        return np.minimum(1.0, cb + cs)
    if mode == 7:
        return np.abs(cb - cs)
    return cs


def _bilinear(np, image, x, y):
    """Échantillonnage bilinéaire, bords recopiés (``ClampToEdge``), centres de texels à +0,5."""
    height, width = image.shape[:2]
    fx = np.clip(x - 0.5, 0.0, width - 1.0)
    fy = np.clip(y - 0.5, 0.0, height - 1.0)
    x0 = np.floor(fx).astype(int)
    y0 = np.floor(fy).astype(int)
    x1 = np.minimum(x0 + 1, width - 1)
    y1 = np.minimum(y0 + 1, height - 1)
    tx = (fx - x0)[..., None]
    ty = (fy - y0)[..., None]
    top = image[y0, x0] * (1 - tx) + image[y0, x1] * tx
    bottom = image[y1, x0] * (1 - tx) + image[y1, x1] * tx
    return top * (1 - ty) + bottom * ty


def reference_frame(frame: CompositeFrame, sources: dict, mattes: dict | None = None, luts: dict | None = None):
    """Image RVB (H, W, 3) que les passes de :func:`plan_frame` doivent produire.

    ``sources[id]`` : tableau (h, w, 3) des codes YUV normalisés (chroma pleine
    résolution) — ou RVB pour une source ``rgba``/``bgra``. ``mattes[clé]`` :
    tableau (H, W) d'alpha 0..1 à la résolution de rendu. ``luts[clé]`` : atlas
    d'étalonnage (:func:`core.gpu_grade.atlas_array`) des calques qui ont un ``grade_lut``.
    """
    import numpy as np

    from .gpu_effects import reference_layer
    from .gpu_grade import sample_atlas

    tables = luts or {}

    def graded(pixels, space: int, domain: int, key: str, matrix, split: float = 0.0, to_canvas=None):
        """La passe ``grade`` : couleur passée dans l'espace de la LUT (``domain``), puis lecture de l'atlas ; là où le
        pixel tombe à gauche de ``split`` (part de la largeur **du cadre**, par ``to_canvas``), la couleur d'origine en
        RVB (comparaison avant / après)."""
        original = pixels
        if space != domain:
            m = np.array(matrix if domain == SPACE_RGB else invert4(matrix), dtype=np.float64)
            pixels = np.clip(pixels @ m[:3, :3].T + m[:3, 3], 0.0, 1.0)
        result = sample_atlas(tables[key], pixels)
        if split > 0.0:
            rgb = original
            if space != SPACE_RGB:
                m = np.array(matrix, dtype=np.float64)
                rgb = original @ m[:3, :3].T + m[:3, 3]
            a, _b, c, _d, e, _f = to_canvas or (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
            rows, columns = np.mgrid[0:result.shape[0], 0:result.shape[1]] + 0.5
            before = (a * columns + c * rows + e) < split * result.shape[1]
            result = np.where(before[..., None], np.clip(rgb, 0.0, 1.0), result)
        return result

    width, height = frame.render_size
    scale = width / float(max(1, frame.canvas_width))
    canvas = np.empty((height, width, 3))
    canvas[...] = frame.background
    ys, xs = np.mgrid[0:height, 0:width]
    cx, cy = xs + 0.5, ys + 0.5
    for layer in frame.layers:
        source = frame.source(layer.source)
        if source is None:
            continue
        layout = LAYOUTS[source.layout]
        matrix = yuv_to_rgb_matrix(source.colorspace, source.color_range)
        data = np.asarray(sources[layer.source], dtype=np.float64)
        # prep : calque à la résolution de rendu (adaptation + pad)
        u = cx / width
        v = cy / height
        fu = (u - layer.fit[0] / frame.canvas_width) / ((layer.fit[2] - layer.fit[0]) / frame.canvas_width)
        fv = (v - layer.fit[1] / frame.canvas_height) / ((layer.fit[3] - layer.fit[1]) / frame.canvas_height)
        inside = (fu >= 0) & (fu <= 1) & (fv >= 0) & (fv <= 1)
        sampled = _bilinear(np, data, fu * source.width, fv * source.height)
        pad = np.array((16 / 255, 128 / 255, 128 / 255) if layout.is_yuv else (0.0, 0.0, 0.0))
        layer_px = np.where(inside[..., None], sampled, pad)
        grading = bool(layer.grade_lut) and layer.grade_lut in tables
        forward = affine_mul((scale, 0, 0, scale, 0, 0), affine_mul(layer.matrix, (1 / scale, 0, 0, 1 / scale, 0, 0)))
        if layout.is_yuv:
            rgb = reference_layer(
                layer_px, layer.program, yuv_to_rgb=matrix,
                pixel_scale=(scale / abs(layer.effect_scale[0]), scale / abs(layer.effect_scale[1])),
                layer_scale=(scale, scale),
                chroma_scale=source.chroma_scale,
                keep_space=grading,
            )
            if grading:
                pixels, space = rgb
                rgb = graded(pixels, space, SPACE_YUV, layer.grade_lut, matrix, layer.grade_split, forward)
        else:
            rgb = graded(layer_px, SPACE_RGB, SPACE_RGB, layer.grade_lut, matrix, layer.grade_split, forward) \
                if grading else layer_px
        # composite : matrice inverse, bord adouci, matte, opacité, fusion
        inverse = affine_inverse(forward)
        if inverse is None:
            continue
        a, b, c, d, e, f = inverse
        lx = a * cx + c * cy + e
        ly = b * cx + d * cy + f
        color = _bilinear(np, rgb, lx, ly)
        coverage = np.clip(np.minimum(np.minimum(lx, width - lx), np.minimum(ly, height - ly)) + 0.5, 0.0, 1.0)
        alpha = coverage * float(layer.opacity)
        if layer.matte and mattes is not None and layer.matte in mattes:
            matte = np.asarray(mattes[layer.matte], dtype=np.float64)[..., None]
            alpha = alpha * _bilinear(np, matte, lx, ly)[..., 0]
        blended = _blend(np, BLEND_CODES[coerce_blend_mode(layer.blend)], canvas, np.clip(color, 0, 1))
        canvas = canvas * (1.0 - alpha[..., None]) + blended * alpha[..., None]
    for adjustment in frame.adjustments:
        if (adjustment.program.is_identity and not adjustment.grade_lut) or adjustment.opacity <= 0.0:
            continue
        processed = reference_layer(canvas, adjustment.program, yuv_to_rgb=yuv_to_rgb_matrix(),
                                    pixel_scale=(scale, scale), chroma_scale=(1.0, 1.0), start_space=SPACE_RGB)
        if adjustment.grade_lut and adjustment.grade_lut in tables:
            processed = graded(np.clip(processed, 0, 1), SPACE_RGB, SPACE_RGB, adjustment.grade_lut,
                               yuv_to_rgb_matrix())
        alpha = np.full((height, width), float(adjustment.opacity))
        if adjustment.matte and mattes is not None and adjustment.matte in mattes:
            alpha = alpha * np.asarray(mattes[adjustment.matte], dtype=np.float64)
        canvas = canvas * (1.0 - alpha[..., None]) + np.clip(processed, 0, 1) * alpha[..., None]
    return canvas


__all__ = [
    "AdjustmentLayer",
    "BLEND_CODES",
    "MAX_BLUR_RADIUS",
    "MAX_POINT_OPS",
    "UNIFORM_BYTES",
    "UNIFORM_FLOATS",
    "CompositeFrame",
    "CompositeLayer",
    "FramePlan",
    "PassSpec",
    "TextureSpec",
    "Uniforms",
    "VideoSource",
    "affine_inverse",
    "affine_mul",
    "plan_frame",
    "present_uniforms",
    "reference_frame",
]
