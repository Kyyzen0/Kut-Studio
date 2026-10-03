"""Effets simples de l'aperçu GPU : traduction du modèle, uniformes, référence CPU.

Chaque effet porté sur GPU suit **la formule du filtre FFmpeg de l'export**
(:func:`core.export_engine._build_clip_effect_filters`) et dans **le même espace**.
FFmpeg applique ces filtres sur les plans YUV du calque (après mise à
l'échelle et rotation), le GPU aussi :

=====================  ===================  =========================================
Effet (modèle)         Filtre de l'export   Formule GPU (codes normalisés 0..1)
=====================  ===================  =========================================
``color_correction``   ``eq``               chemin entier d'FFmpeg (MPlayer) :
                                            Y' = ⌊Y·⌊c·4096⌋/4096⌋ + b₈ (voir :func:`eq_plane`)
``blur``               ``gblur``            noyau séparable **exponentiel** ν^|k| : la réponse
                                            impulsionnelle exacte du filtre récursif de
                                            ``gblur`` (steps=1), voir :func:`gblur_weights` ;
                                            σ chroma × 2 en 4:2:0 (FFmpeg floute chaque
                                            plan à sa résolution)
``sharpen``            ``unsharp`` 5×5      Y' = Y + a·(Y − binomial5×5(Y)), luma seule
``vignette``           ``vignette``         f = cos⁴(angle·d/dmax) ; Y·f ; UV vers ½
``black_and_white``    ``hue=s=0``          UV = ½
``sepia``              ``colorchannelmixer``  matrice RVB 3×3
=====================  ===================  =========================================

Écarts connus, couverts par les tests de cohérence (tolérances) : FFmpeg
quantifie sur 8 bits et trame la vignette, le GPU calcule en flottant ; la
chroma est traitée à pleine résolution ; le noyau du flou est tronqué à
:data:`MAX_BLUR_RADIUS` texels ; ``eq`` tronque ``100·b + 100`` en entier, et
selon que le compilateur d'FFmpeg fusionne l'opération (FMA, clang ARM) ou non
(x86), une luminosité comme −0,2 donne un pas d'écart (3 niveaux).

Chaque effet a son repli CPU : le segment d'aperçu fidèle, rendu par
FFmpeg avec le graphe de l'export. :func:`reference_layer` (numpy) reproduit
les shaders **opération par opération** ; les tests la comparent à FFmpeg (en
CI, sans GPU) et au rendu GPU réel (tests matériels optionnels).

Ajouter un effet GPU : une entrée dans :func:`program_for`, son opération dans
``tools/gpu/shader_sources.py`` (même numéro) et dans :func:`_apply_point`,
puis un test de cohérence contre le filtre FFmpeg de l'export.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

OP_NONE = 0
OP_EQ = 1
OP_VIGNETTE = 2
OP_DESATURATE = 3
OP_COLOR_MATRIX = 4

SPACE_YUV = 0
SPACE_RGB = 1

MAX_POINT_OPS = 8
"""Opérations ponctuelles par passe (taille du bloc d'uniformes)."""

MAX_BLUR_RADIUS = 96
"""Rayon maximal (texels) d'une passe de flou ; au-delà le noyau est tronqué (aperçu)."""

VIGNETTE_NATURAL = 0.5
"""``dmax = hypot(w, h) · 0,5`` : le filtre ``vignette`` seul (demi-diagonale de l'image)."""

VIGNETTE_EXPORT = 1.0 / math.sqrt(2.0)
"""Calque vidéo de l'export : la vignette suit le filtre ``rotate``, qui agrandit
l'image en carré de côté ``hypot(w, h)`` ; ``dmax`` vaut alors ``hypot(w, h)/√2``."""

SEPIA_MATRIX = ((0.393, 0.769, 0.189), (0.349, 0.686, 0.168), (0.272, 0.534, 0.131))


@dataclass(frozen=True)
class PointOp:
    """Opération pixel par pixel : 16 flottants (4 ``vec4``) dans le bloc d'uniformes.

    Disposition de ``values`` (après le type) : EQ ``(cy, by, cc, bc, actif_y,
    actif_c)`` ; VIGNETTE ``(angle,)`` ; COLOR_MATRIX ``(0, 0, 0, 12 coefficients)``.
    """

    kind: int
    space: int
    values: tuple[float, ...] = ()

    def packed(self) -> tuple[float, ...]:
        data = (float(self.kind), *(float(v) for v in self.values))
        return (data + (0.0,) * 16)[:16]


@dataclass(frozen=True)
class BlurOp:
    sigma: float
    """Écart-type en pixels **de l'export** (calque à son échelle)."""


@dataclass(frozen=True)
class SharpenOp:
    amount: float


NeighborhoodOp = BlurOp | SharpenOp


@dataclass(frozen=True)
class EffectProgram:
    """Effets d'un calque découpés en passes.

    ``segments[i]`` regroupe les opérations ponctuelles appliquées **après**
    l'opération de voisinage ``neighborhood[i - 1]`` (``segments[0]`` : avant la
    première). Il y a donc ``len(neighborhood) + 1`` segments.
    """

    segments: tuple[tuple[PointOp, ...], ...] = ((),)
    neighborhood: tuple[NeighborhoodOp, ...] = ()
    unsupported: tuple[str, ...] = ()

    @property
    def is_identity(self) -> bool:
        return not self.neighborhood and not any(self.segments)

    @property
    def pass_count(self) -> int:
        """Passes de rendu du calque (préparation + voisinage + composition)."""
        passes = 1
        for op in self.neighborhood:
            passes += 2 if isinstance(op, BlurOp) else 1
        return passes + 1


def program_for(effects, *, vignette_extent: float = VIGNETTE_NATURAL) -> EffectProgram:
    """Programme GPU des effets **actifs** d'un clip, dans leur ordre.

    ``vignette_extent`` : rayon de référence de la vignette en fraction de la
    diagonale du calque (:data:`VIGNETTE_EXPORT` pour un clip vidéo).
    """
    from .effects_model import EffectType

    segments: list[list[PointOp]] = [[]]
    neighborhood: list[NeighborhoodOp] = []
    unsupported: list[str] = []
    for effect in effects or ():
        if not getattr(effect, "enabled", True):
            continue
        kind = getattr(effect, "type", None)
        params = dict(getattr(effect, "params", {}) or {})
        if kind is EffectType.COLOR_CORRECTION:
            luma = eq_plane(float(params.get("contrast", 1.0)), float(params.get("brightness", 0.0)))
            chroma = eq_plane(float(params.get("saturation", 1.0)), 0.0)
            values = (luma[0], luma[1], chroma[0], chroma[1], luma[2], chroma[2])
            segments[-1].append(PointOp(OP_EQ, SPACE_YUV, values))
        elif kind is EffectType.VIGNETTE:
            angle = float(params.get("intensity", 0.5)) * math.pi / 4.0
            segments[-1].append(PointOp(OP_VIGNETTE, SPACE_YUV, (angle, float(vignette_extent))))
        elif kind is EffectType.BLACK_AND_WHITE:
            segments[-1].append(PointOp(OP_DESATURATE, SPACE_YUV))
        elif kind is EffectType.SEPIA:
            rows = [value for row in SEPIA_MATRIX for value in (*row, 0.0)]
            segments[-1].append(PointOp(OP_COLOR_MATRIX, SPACE_RGB, (0.0, 0.0, 0.0, *rows)))
        elif kind is EffectType.BLUR:
            sigma = float(params.get("intensity", 0.0))
            if sigma > 0.0:
                neighborhood.append(BlurOp(sigma))
                segments.append([])
        elif kind is EffectType.SHARPEN:
            amount = float(params.get("intensity", 0.0))
            if amount != 0.0:
                neighborhood.append(SharpenOp(amount))
                segments.append([])
        else:
            unsupported.append(str(getattr(kind, "value", kind)))
    for segment in segments:
        if len(segment) > MAX_POINT_OPS:
            raise ValueError("trop d'opérations ponctuelles pour une passe")
    return EffectProgram(tuple(tuple(s) for s in segments), tuple(neighborhood), tuple(unsupported))


def eq_plane(contrast: float, brightness: float) -> tuple[float, float, float]:
    """``(facteur, décalage, actif)`` du filtre ``eq`` d'FFmpeg pour un plan 8 bits.

    FFmpeg (``process_c``, hérité de MPlayer) calcule en entiers, **troncatures
    flottantes comprises** (``int(100·b + 100)`` donne 79 pour b = −0,2) :
    ``pel = (src·C >> 12) + B`` avec ``C = int(contrast·4096)`` et
    ``B = int(100·b + 100)·511 // 200 − 128 − C // 32``. Le plan est laissé
    intact quand contraste = 1 et luminosité = 0. Le shader et :func:`reference_layer`
    reproduisent ``⌊src·C/4096⌋ + B`` (exact en flottant 32 bits).
    """
    if contrast == 1.0 and brightness == 0.0:
        return (1.0, 0.0, 0.0)
    c = int(contrast * 256 * 16)
    b = (int(100.0 * brightness + 100.0) * 511) // 200 - 128 - c // 32
    return (c / 4096.0, float(b), 1.0)


def gaussian_weights(sigma: float, limit: int = MAX_BLUR_RADIUS) -> tuple[int, tuple[float, ...]]:
    """``(rayon, poids normalisés 0..rayon)`` d'une gaussienne discrète."""
    if sigma <= 0.0:
        return 0, (1.0,)
    radius = max(1, min(limit, int(math.ceil(3.0 * sigma))))
    raw = [math.exp(-0.5 * (i / sigma) ** 2) for i in range(radius + 1)]
    total = raw[0] + 2.0 * sum(raw[1:])
    return radius, tuple(w / total for w in raw)


def gblur_weights(sigma: float, limit: int = MAX_BLUR_RADIUS) -> tuple[int, tuple[float, ...]]:
    """``(rayon, demi-noyau normalisé)`` équivalent au ``gblur`` d'FFmpeg (``steps=1``).

    ``gblur`` applique un filtre récursif du premier ordre aller puis retour
    (Alvarez–Mazorra) : sa réponse impulsionnelle est ``ν^|k|`` avec
    ``λ = σ²/2`` et ``ν = (1 + 2λ − √(1 + 4λ)) / 2λ``. Un échelon 16→235 flouté
    à σ = 3 donne 100 puis 69 de part et d'autre du bord, comme FFmpeg.
    """
    if sigma <= 0.0:
        return 0, (1.0,)
    lam = sigma * sigma / 2.0
    nu = (1.0 + 2.0 * lam - math.sqrt(1.0 + 4.0 * lam)) / (2.0 * lam)
    if nu <= 0.0:
        return 0, (1.0,)
    radius = max(1, min(limit, int(math.ceil(math.log(1e-4) / math.log(nu)))))
    raw = [nu ** i for i in range(radius + 1)]
    total = raw[0] + 2.0 * sum(raw[1:])
    return radius, tuple(w / total for w in raw)


BINOMIAL5 = (6.0 / 16.0, 4.0 / 16.0, 1.0 / 16.0)
"""Demi-noyau (centre d'abord) de ``unsharp`` 5×5 : [1 4 6 4 1]/16 (Waltz–Miller, vérifié)."""


# --- Référence CPU (numpy) : mêmes opérations que les shaders ----------------------------------------------


def _np():
    import numpy as np

    return np


def _apply_point(np, pixels, space: int, op: PointOp, yuv_to_rgb, rgb_to_yuv, coords):
    """Une opération ponctuelle ; ``pixels`` (H, W, 3) en codes normalisés, dans ``space``."""
    if op.space != space:
        pixels = _convert(np, pixels, yuv_to_rgb if op.space == SPACE_RGB else rgb_to_yuv)
        space = op.space
    v = op.values
    if op.kind == OP_EQ:
        pixels = pixels.copy()
        if v[4]:
            y8 = np.floor(pixels[..., 0] * 255.0 + 0.5)
            pixels[..., 0] = np.clip(np.floor(y8 * v[0]) + v[1], 0.0, 255.0) / 255.0
        if v[5]:
            c8 = np.floor(pixels[..., 1:3] * 255.0 + 0.5)
            pixels[..., 1:3] = np.clip(np.floor(c8 * v[2]) + v[3], 0.0, 255.0) / 255.0
    elif op.kind == OP_VIGNETTE:
        x, y, half_w, half_h = coords
        extent = v[1] if len(v) > 1 and v[1] > 0 else VIGNETTE_NATURAL
        dnorm = np.hypot(x - half_w, y - half_h) / (math.hypot(2 * half_w, 2 * half_h) * extent)
        factor = np.where(dnorm > 1.0, 0.0, np.cos(v[0] * dnorm) ** 4)
        pixels = pixels.copy()
        pixels[..., 0] = pixels[..., 0] * factor
        pixels[..., 1:3] = (pixels[..., 1:3] - 127.0 / 255.0) * factor[..., None] + 127.0 / 255.0
    elif op.kind == OP_DESATURATE:
        pixels = pixels.copy()
        pixels[..., 1:3] = 0.5
    elif op.kind == OP_COLOR_MATRIX:
        m = np.array(v[3:15], dtype=np.float64).reshape(3, 4)
        pixels = np.clip(pixels @ m[:, :3].T + m[:, 3], 0.0, 1.0)
    return pixels, space


def _convert(np, pixels, matrix):
    m = np.array(matrix, dtype=np.float64)
    return np.clip(pixels @ m[:3, :3].T + m[:3, 3], 0.0, 1.0)


def _blur_axis(np, pixels, weights, radius, axis):
    padded = np.pad(pixels, [(radius, radius) if a == axis else (0, 0) for a in range(pixels.ndim)], mode="edge")
    out = np.zeros_like(pixels)
    size = pixels.shape[axis]
    for offset in range(-radius, radius + 1):
        w = weights[abs(offset)]
        sl = [slice(None)] * pixels.ndim
        sl[axis] = slice(radius + offset, radius + offset + size)
        out += w * padded[tuple(sl)]
    return out


def reference_layer(
    yuv,
    program: EffectProgram,
    *,
    yuv_to_rgb,
    pixel_scale: tuple[float, float] = (1.0, 1.0),
    chroma_scale: tuple[float, float] = (2.0, 2.0),
    start_space: int = SPACE_YUV,
):
    """Calque après effets, en RVB 0..1 (H, W, 3) — même calcul que les shaders.

    Args:
        yuv: tableau (H, W, 3) des codes Y, U, V normalisés (chroma à pleine résolution).
        program: :func:`program_for`.
        yuv_to_rgb: :func:`core.gpu_frames.yuv_to_rgb_matrix` du flux.
        pixel_scale: texels de ce tableau par pixel de l'export (σ du flou).
        chroma_scale: sous-échantillonnage de la chroma du flux (σ chroma = σ × facteur).
        start_space: espace de ``yuv`` (``SPACE_RGB`` pour un calque d'effets, qui
            part de la composition RVB).
    """
    from .gpu_frames import invert4

    np = _np()
    rgb_to_yuv = invert4(yuv_to_rgb)
    pixels = np.asarray(yuv, dtype=np.float64)
    height, width = pixels.shape[:2]
    ys, xs = np.mgrid[0:height, 0:width]
    coords = (xs + 0.5, ys + 0.5, width / 2.0, height / 2.0)
    space = start_space
    for index, segment in enumerate(program.segments):
        if index > 0:
            op = program.neighborhood[index - 1]
            if space != SPACE_YUV:
                pixels, space = _convert(np, pixels, rgb_to_yuv), SPACE_YUV
            if isinstance(op, BlurOp):
                blurred = []
                for channel, factor in ((0, (1.0, 1.0)), (1, chroma_scale), (2, chroma_scale)):
                    plane = pixels[..., channel]
                    radius_x, wx = gblur_weights(op.sigma * pixel_scale[0] * factor[0])
                    radius_y, wy = gblur_weights(op.sigma * pixel_scale[1] * factor[1])
                    if radius_x:
                        plane = _blur_axis(np, plane, wx, radius_x, 1)
                    if radius_y:
                        plane = _blur_axis(np, plane, wy, radius_y, 0)
                    blurred.append(plane)
                pixels = np.stack(blurred, axis=-1)
            else:
                luma = pixels[..., 0]
                blurred = _blur_axis(np, _blur_axis(np, luma, BINOMIAL5, 2, 1), BINOMIAL5, 2, 0)
                pixels = pixels.copy()
                pixels[..., 0] = np.clip(luma + op.amount * (luma - blurred), 0.0, 1.0)
        for point_op in segment:
            pixels, space = _apply_point(np, pixels, space, point_op, yuv_to_rgb, rgb_to_yuv, coords)
    if space == SPACE_YUV:
        pixels = _convert(np, pixels, yuv_to_rgb)
    return pixels


@dataclass(frozen=True)
class EffectSupport:
    """Ce que l'aperçu GPU sait montrer d'un clip (le reste : segments fidèles)."""

    program: EffectProgram
    color_grade: bool = False
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def complete(self) -> bool:
        return not self.program.unsupported and not self.color_grade


def effect_support(effects, color_grade=None) -> EffectSupport:
    """Programme GPU + ce qui reste réservé au rendu fidèle (étalonnage, effets inconnus)."""
    program = program_for(effects)
    grading = color_grade is not None and not _grade_is_identity(color_grade)
    return EffectSupport(program, grading)


def _grade_is_identity(grade) -> bool:
    checker = getattr(grade, "is_identity", None)
    if callable(checker):
        try:
            return bool(checker())
        except Exception:
            return False
    if isinstance(checker, bool):
        return checker
    return False


__all__ = [
    "BINOMIAL5",
    "MAX_BLUR_RADIUS",
    "MAX_POINT_OPS",
    "OP_COLOR_MATRIX",
    "OP_DESATURATE",
    "OP_EQ",
    "OP_VIGNETTE",
    "SPACE_RGB",
    "SPACE_YUV",
    "VIGNETTE_EXPORT",
    "VIGNETTE_NATURAL",
    "BlurOp",
    "EffectProgram",
    "EffectSupport",
    "PointOp",
    "SharpenOp",
    "effect_support",
    "gaussian_weights",
    "gblur_weights",
    "program_for",
    "reference_layer",
]
