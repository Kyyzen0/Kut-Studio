"""Pipeline GPU sans GPU : formats, couleurs, effets, passes, cache, mémoire, choix du rendu.

La logique est vérifiée par la **référence numpy** (:func:`core.gpu_composite.reference_frame`),
elle-même comparée aux filtres FFmpeg de l'export ici, et au vrai GPU dans
``tests/test_gpu_hardware.py`` (optionnel).
"""

from __future__ import annotations

import functools
import json
import math
import os
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path

import pytest

from core.blend_modes import BlendMode
from core.effects_model import ClipEffect, EffectType
from core.gpu_backend import (
    FrameStats,
    GpuHealth,
    PreviewBackend,
    graphics_api,
    missing_shaders,
    resolve_preview_backend,
)
from core.gpu_cache import GpuTextureCache, default_budget
from core.gpu_composite import (
    UNIFORM_BYTES,
    CompositeFrame,
    CompositeLayer,
    Uniforms,
    VideoSource,
    affine_inverse,
    plan_frame,
)
from core.gpu_effects import (
    OP_COLOR_MATRIX,
    OP_EQ,
    BlurOp,
    SharpenOp,
    eq_plane,
    gblur_weights,
    program_for,
)
from core.gpu_frames import LAYOUTS, invert4, layout_for, yuv_to_rgb_matrix
from core.memory_monitor import CRITICAL, NORMAL, WARNING, MemoryStatus, MemoryWatch, classify, pressure_actions, read_memory_status

HAS_FFMPEG = shutil.which("ffmpeg") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="FFmpeg absent")


def E(kind, **params):
    return ClipEffect("e" + kind.value, kind, True, params)


# --- Formats et couleur ----------------------------------------------------------------------------------


def test_layouts_describe_planes_and_ten_bit_normalisation():
    nv12 = layout_for("Format_NV12")
    assert [p.texture_format for p in nv12.planes] == ["R8", "RG8"]
    assert nv12.plane_size(1, 1921, 1081) == (961, 541)  # arrondi au-dessus
    assert nv12.bytes_per_frame(1920, 1080) == 1920 * 1080 * 3 // 2
    p010 = layout_for("Format_P010")
    assert p010.value_scale * (940 << 6) / 65535 == pytest.approx(940 / 1023)  # 10 bits alignés en haut
    yuv10 = layout_for("Format_YUV420P10")
    assert yuv10.value_scale * 940 / 65535 == pytest.approx(940 / 1023)          # alignés en bas
    assert layout_for("Format_BGRA8888").swizzle_bgra
    assert layout_for("Format_UYVY") is None  # conversion de Qt (repli)


def _rgb(matrix, y, u, v):
    out = []
    for row in matrix[:3]:
        value = row[0] * y / 255 + row[1] * u / 255 + row[2] * v / 255 + row[3]
        out.append(max(0, min(255, round(value * 255))))
    return tuple(out)


def test_untagged_streams_use_bt601_like_ffmpeg_and_tags_are_honoured():
    # Valeurs relevées sur FFmpeg (swscale) : même RVB au bit près.
    untagged = yuv_to_rgb_matrix("", "")
    assert _rgb(untagged, 81, 90, 240) == (254, 0, 0)
    assert _rgb(untagged, 145, 54, 34) == (0, 255, 1)
    bt709 = yuv_to_rgb_matrix("bt709", "video")
    assert _rgb(bt709, 81, 90, 240) == (255, 24, 0)
    full = yuv_to_rgb_matrix("bt709", "full")
    assert _rgb(full, 0, 128, 128) == (0, 0, 0) and _rgb(full, 255, 128, 128) == (255, 255, 255)


def test_matrix_inverse_round_trips():
    m = yuv_to_rgb_matrix("bt2020")
    inv = invert4(m)
    for y, u, v in ((0.3, 0.5, 0.5), (0.9, 0.2, 0.7)):
        r = [sum(m[i][j] * c for j, c in enumerate((y, u, v, 1.0))) for i in range(3)]
        back = [sum(inv[i][j] * c for j, c in enumerate((*r, 1.0))) for i in range(3)]
        assert back == pytest.approx([y, u, v])


# --- Effets ---------------------------------------------------------------------------------------------


def test_effect_program_splits_passes_in_the_model_order():
    program = program_for([
        E(EffectType.COLOR_CORRECTION, brightness=0.1, contrast=1.2, saturation=1.0),
        E(EffectType.BLUR, intensity=3.0),
        E(EffectType.SEPIA),
        E(EffectType.SHARPEN, intensity=1.0),
        E(EffectType.VIGNETTE, intensity=0.5),
    ])
    assert [op.kind for op in program.segments[0]] == [OP_EQ]
    assert isinstance(program.neighborhood[0], BlurOp) and isinstance(program.neighborhood[1], SharpenOp)
    assert [op.kind for op in program.segments[1]] == [OP_COLOR_MATRIX]
    assert len(program.segments) == 3 and program.pass_count == 1 + 2 + 1 + 1
    assert program_for([E(EffectType.BLUR, intensity=0.0)]).is_identity
    disabled = ClipEffect("x", EffectType.SEPIA, False, {})
    assert program_for([disabled]).is_identity


def test_eq_follows_the_integer_path_of_ffmpeg():
    factor, offset, active = eq_plane(1.4, 0.1)
    assert (factor * 4096, offset, active) == (5734, -26.0, 1.0)
    assert eq_plane(1.0, 0.0)[2] == 0.0  # plan intact, comme FFmpeg
    saturation = eq_plane(0.3, 0.0)
    assert math.floor(40 * saturation[0]) + saturation[1] == 100  # relevé : eq=saturation=0.3, U=40 → 100


def test_blur_kernel_matches_the_step_response_of_gblur():
    radius, weights = gblur_weights(3.0)
    assert radius >= 9 and weights[0] + 2 * sum(weights[1:]) == pytest.approx(1.0)
    # Échelon 16 → 235 flouté à σ = 3 par FFmpeg : 100 puis 69 avant le bord.
    first = 16 + 219 * sum(weights[1:])
    second = 16 + 219 * sum(weights[2:])
    assert round(first) == 100 and round(second) == 69


def _yuv_frame(width, height):
    from tests.gpu_harness import test_pattern

    return test_pattern(width, height)


@needs_ffmpeg
@pytest.mark.parametrize("name, effects, mean_limit, p99_limit", [
    ("none", [], 0.5, 2.0),
    ("eq", [E(EffectType.COLOR_CORRECTION, brightness=0.1, contrast=1.4, saturation=1.8)], 0.5, 2.0),
    ("vignette", [E(EffectType.VIGNETTE, intensity=0.8)], 1.0, 4.0),
    ("black_and_white", [E(EffectType.BLACK_AND_WHITE)], 1.2, 3.0),
    ("sepia", [E(EffectType.SEPIA)], 0.6, 2.0),
    ("sharpen", [E(EffectType.SHARPEN, intensity=1.5)], 0.6, 3.0),
    # Bords de l'image : le filtre récursif de gblur y gère la frontière autrement (p99 seulement).
    ("blur", [E(EffectType.BLUR, intensity=12.0)], 2.0, 14.0),
    ("chain", [E(EffectType.COLOR_CORRECTION, brightness=0.05, contrast=1.2, saturation=1.3),
               E(EffectType.BLUR, intensity=2.0), E(EffectType.VIGNETTE, intensity=0.6), E(EffectType.SEPIA)],
     1.2, 7.0),
])
def _ffmpeg_vs_reference(effects, *, full_chroma: bool = False):
    """``(moyenne, p99)`` de l'écart, en niveaux 8 bits, entre le filtre FFmpeg de l'export et la formule GPU.

    ``full_chroma`` : source 4:4:4, pour un effet qui passe lui-même en 4:4:4 (le comparer à une source 4:2:0
    mesurerait le suréchantillonnage de la chroma de swscale, interpolée, contre celle de la référence, recopiée).
    """
    np = pytest.importorskip("numpy")
    from core.export_engine import _build_clip_effect_filters
    from core.gpu_effects import reference_layer

    width, height = 320, 180
    codes = _yuv_frame(width, height)
    if full_chroma:
        raw, pix_fmt = codes[..., 0].tobytes() + codes[..., 1].tobytes() + codes[..., 2].tobytes(), "yuv444p"
    else:
        raw = codes[..., 0].tobytes() + codes[::2, ::2, 1].tobytes() + codes[::2, ::2, 2].tobytes()
        pix_fmt = "yuv420p"
    chain = _build_clip_effect_filters(tuple(effects))
    completed = subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "rawvideo", "-pix_fmt", pix_fmt, "-s", f"{width}x{height}",
         "-i", "-", "-vf", (chain + "," if chain else "") + "format=rgba", "-f", "rawvideo", "-"],
        input=raw, capture_output=True, check=True,
    )
    # ``rgba`` comme le calque de l'export (``_build_layer_filter``) : swscale ne convertit pas vers ``rgb24`` et ``rgba``
    # avec le même code partout (voir ``_swscale_conversion_error``).
    expected = np.frombuffer(completed.stdout, np.uint8).reshape(height, width, 4)[..., :3] / 255.0
    full = codes.astype(float) / 255.0  # chroma 2×2 dupliquée, comme la conversion de FFmpeg ici
    got = reference_layer(full, program_for(effects), yuv_to_rgb=yuv_to_rgb_matrix())
    diff = np.abs(got - expected) * 255
    return float(diff.mean()), float(np.percentile(diff, 99))


@functools.lru_cache(maxsize=None)
def _swscale_conversion_error():
    """Écart du cas « sans effet » : l'arrondi à virgule fixe de swscale propre à CE FFmpeg.

    Il s'ajoute à chaque cas. La conversion mesurée est celle de l'export, YUV 4:2:0 → ``rgba``. Moyenne / p99 :

    - FFmpeg 9.0 et 7.1 sur arm64 (macOS) : 0,30 / 1,1 ;
    - code C de swscale, sans SIMD (``-cpuflags 0``, FFmpeg 9 comme 7.1) : 1,07 / 2,6, un peu plus d'un niveau ;
    - FFmpeg 6.1 et 7.1 sur x86-64 (Ubuntu, Windows), mesuré vers ``rgb24`` avant le passage à ``rgba`` : 0,87 / 2,4.

    ``rgb24`` n'est pas un équivalent : FFmpeg 7.1 sur arm64 n'a pas de code NEON pour lui et y donnait 1,07 / 2,6,
    alors que l'export n'emprunte jamais cette conversion. Les seuils ne portaient d'abord que sur le FFmpeg de macOS :
    ils faisaient échouer la CI sur les autres systèmes sans qu'aucune formule ne diffère. L'écart reste sous un niveau
    de gris en moyenne : invisible, mais mesuré plutôt qu'ignoré.
    """
    return _ffmpeg_vs_reference(())


@needs_ffmpeg
def test_the_ffmpeg_conversion_alone_stays_within_one_grey_level():
    """Garde-fou du calibrage : sans effet, aucune version de swscale ne doit s'écarter de la référence.

    Le seuil n'est pas relevé pour le code C de swscale (1,07 mesuré) : une plateforme sans SIMD pour cette conversion
    doit se voir ici plutôt que s'ajouter en silence au budget de chaque effet.
    """
    mean, p99 = _swscale_conversion_error()
    assert mean <= 1.0 and p99 <= 3.0, (mean, p99)


@needs_ffmpeg
@pytest.mark.parametrize("name, effects, mean_limit, p99_limit", [
    ("eq", [E(EffectType.COLOR_CORRECTION, brightness=0.1, contrast=1.4, saturation=1.8)], 0.5, 2.0),
    ("vignette", [E(EffectType.VIGNETTE, intensity=0.8)], 1.0, 4.0),
    ("black_and_white", [E(EffectType.BLACK_AND_WHITE)], 1.2, 3.0),
    ("sepia", [E(EffectType.SEPIA)], 0.6, 2.0),
    ("sharpen", [E(EffectType.SHARPEN, intensity=1.5)], 0.6, 3.0),
    # Bords de l'image : le filtre récursif de gblur y gère la frontière autrement (p99 seulement).
    ("blur", [E(EffectType.BLUR, intensity=12.0)], 2.0, 14.0),
    ("chain", [E(EffectType.COLOR_CORRECTION, brightness=0.05, contrast=1.2, saturation=1.3),
               E(EffectType.BLUR, intensity=2.0), E(EffectType.VIGNETTE, intensity=0.6), E(EffectType.SEPIA)],
     1.2, 7.0),
    # Vidéo sociale : bloom (sous-graphe split / lutrgb / gblur / blend), aberration (rgbashift), heat haze (geq).
    ("glow", [E(EffectType.GLOW, threshold=0.5, radius=6.0, intensity=1.2)], 1.5, 8.0),
    ("chromatic_aberration", [E(EffectType.CHROMATIC_ABERRATION, intensity=5.0)], 1.0, 4.0),

    ("glow_then_vignette", [E(EffectType.GLOW, threshold=0.4, radius=3.0, intensity=0.8),
                            E(EffectType.VIGNETTE, intensity=0.5)], 1.5, 8.0),
])
def test_gpu_effects_match_the_ffmpeg_filters_of_the_export(name, effects, mean_limit, p99_limit):
    """Cohérence effet par effet : formule GPU (référence) contre filtre FFmpeg de l'export.

    Les limites sont le budget de la formule ; l'erreur de conversion de ce FFmpeg, mesurée sans effet,
    s'y ajoute (bornes de la somme : les deux erreurs sont indépendantes).
    """
    mean, p99 = _ffmpeg_vs_reference(effects)
    base_mean, base_p99 = _swscale_conversion_error()
    assert mean <= mean_limit + base_mean and p99 <= p99_limit + base_p99, (name, mean, p99, base_mean, base_p99)


@needs_ffmpeg
@pytest.mark.parametrize("speed, time", [(0.0, 0.0), (9.0, 0.0)])
def test_the_gpu_heat_haze_matches_the_geq_of_the_export(speed, time):
    """Heat haze : même décalage entier par ligne que le ``geq`` de l'export (source 4:4:4, voir le helper)."""
    effects = [E(EffectType.HEAT_HAZE, amplitude=6.0, frequency=0.08, speed=speed, top=0.2, span=0.5)]
    mean, p99 = _ffmpeg_vs_reference(effects, full_chroma=True)
    assert mean <= 0.6 and p99 <= 3.0, (mean, p99)


# --- Plan de passes ------------------------------------------------------------------------------------------


def _layer(**kwargs):
    base = dict(source="v", matrix=(1.0, 0.0, 0.0, 1.0, 0.0, 0.0), fit=(0.0, 0.0, 320.0, 180.0))
    base.update(kwargs)
    return CompositeLayer(**base)


SOURCE = VideoSource("v", "nv12", 320, 180)


def test_plan_runs_prep_effects_and_composite_with_ping_pong_canvases():
    program = program_for([E(EffectType.BLUR, intensity=2.0), E(EffectType.SHARPEN, intensity=1.0)])
    frame = CompositeFrame(320, 180, 0.5, (_layer(program=program), _layer(blend=BlendMode.MULTIPLY)), (SOURCE,))
    plan = plan_frame(frame)
    assert [p.shader for p in plan.passes] == [
        "clear", "prep", "blur", "blur", "sharpen", "composite", "prep", "composite",
    ]
    assert all(len(p.uniforms) == UNIFORM_BYTES for p in plan.passes)
    composites = [p for p in plan.passes if p.shader == "composite"]
    assert composites[0].inputs[2] == "canvas0" and composites[0].target == "canvas1"
    assert composites[1].inputs[2] == "canvas1" and plan.canvas == "canvas0"
    assert plan.textures["canvas0"].width == 160  # rendu à l'échelle de l'aperçu
    for step in plan.passes:  # jamais lire et écrire la même texture
        assert step.target not in step.inputs


def test_invisible_or_unknown_layers_cost_nothing():
    frame = CompositeFrame(320, 180, 1.0, (_layer(opacity=0.0), _layer(source="ghost"),
                                           _layer(matrix=(0, 0, 0, 0, 0, 0))), (SOURCE,))
    assert [p.shader for p in plan_frame(frame).passes] == ["clear"]


def test_uniform_block_layout_is_stable():
    data = Uniforms(target=(10.0, 20.0, 1.0, 3.0)).pack()
    assert len(data) == UNIFORM_BYTES
    assert struct.unpack_from("<4f", data, 48 * 4) == (10.0, 20.0, 1.0, 3.0)  # après trois mat4
    from ui.gpu_preview import _FLIP_OFFSET

    assert _FLIP_OFFSET == 50 * 4  # target.z : retournement Y patché par le backend


def test_inverse_map_targets_layer_pixels():
    matrix = (0.5, 0.0, 0.0, 0.5, 40.0, 20.0)
    inverse = affine_inverse(matrix)
    assert inverse == pytest.approx((2.0, 0.0, 0.0, 2.0, -80.0, -40.0))
    assert affine_inverse((0, 0, 0, 0, 1, 1)) is None


@pytest.mark.parametrize("mode, expected", [
    (BlendMode.NORMAL, 0.8), (BlendMode.MULTIPLY, 0.4 * 0.8), (BlendMode.SCREEN, 0.4 + 0.8 - 0.32),
    (BlendMode.OVERLAY, 2 * 0.4 * 0.8), (BlendMode.DARKEN, 0.4), (BlendMode.LIGHTEN, 0.8),
    (BlendMode.ADD, 1.0), (BlendMode.DIFFERENCE, 0.4),
])
def test_blend_modes_follow_the_w3c_formulas(mode, expected):
    np = pytest.importorskip("numpy")
    from core.gpu_composite import BLEND_CODES, _blend

    assert float(_blend(np, BLEND_CODES[mode], np.array(0.4), np.array(0.8))) == pytest.approx(expected)


def test_reference_composites_geometry_opacity_and_mattes():
    np = pytest.importorskip("numpy")
    from core.gpu_composite import reference_frame

    white = np.ones((180, 320, 3)) * np.array([235 / 255, 0.5, 0.5])
    layer = _layer(matrix=(0.5, 0.0, 0.0, 0.5, 80.0, 45.0), opacity=0.5, matte="m")
    frame = CompositeFrame(320, 180, 1.0, (layer,), (VideoSource("v", "yuv420p", 320, 180),), (0.0, 0.0, 0.0))
    matte = np.zeros((180, 320))
    matte[:, :160] = 1.0  # moitié gauche du calque visible
    out = reference_frame(frame, {"v": white}, {"m": matte})
    assert out[10, 10] == pytest.approx([0, 0, 0])               # hors du calque
    assert out[90, 100] == pytest.approx([0.5, 0.5, 0.5], abs=0.01)  # calque, opacité ½
    assert out[90, 200] == pytest.approx([0, 0, 0], abs=0.01)        # masqué


# --- Équivalence avec l'export -------------------------------------------------------------------------------


@needs_ffmpeg
@pytest.mark.parametrize("transform, effects, color_limit", [
    ({}, [], 4.0),
    ({"position_x": 0.1, "position_y": -0.05, "scale": 0.7, "rotation": 15.0, "opacity": 0.8}, [], 5.0),
    ({"scale": 0.8}, [E(EffectType.COLOR_CORRECTION, brightness=0.05, contrast=1.2, saturation=1.4),
                      E(EffectType.VIGNETTE, intensity=0.5)], 6.0),
    ({}, [E(EffectType.BLUR, intensity=2.0), E(EffectType.SEPIA)], 4.0),
    # Effets lumineux sur un clip réduit : le heat haze déforme l'image du clip (avant son échelle), le bloom et
    # l'aberration agissent après (pixels de l'export).
    ({"scale": 0.7}, [E(EffectType.HEAT_HAZE, amplitude=4.0, frequency=0.07, speed=0.0, top=0.1, span=0.5),
                      E(EffectType.GLOW, threshold=0.5, radius=3.0, intensity=1.0),
                      E(EffectType.CHROMATIC_ABERRATION, intensity=2.0)], 6.0),
])
def test_gpu_preview_matches_the_export_frame(tmp_path, transform, effects, color_limit):
    """Même clip, même instant : image de l'export contre image du pipeline GPU.

    L'export positionne les calques à un pixel près (arrondis entiers de ``rotate`` /
    ``overlay``, et ``rotate`` rééchantillonne même à 0°) : on vérifie donc la
    géométrie à 1,5 px près, puis les couleurs une fois l'alignement compensé.
    """
    np = pytest.importorskip("numpy")
    from PySide6.QtGui import QImage

    from core.export_engine import ExportEngine, ExportFormat, ExportPreset, ExportRequest
    from core.gpu_composite import affine_mul, reference_frame
    from core.gpu_effects import VIGNETTE_EXPORT
    from core.project_model import Clip, MediaAsset, Project, Track
    from core.render_plan import build_render_plan
    from core.tracking_motion import fit_box, video_layer_matrix
    from core.visual_effects import ClipTransform
    from tests.gpu_harness import reference_codes, smooth_pattern

    width, height = 320, 180
    codes = smooth_pattern(width, height)
    source = tmp_path / "src.mkv"
    raw = codes[..., 0].tobytes() + codes[::2, ::2, 1].tobytes() + codes[::2, ::2, 2].tobytes()
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "yuv420p", "-s",
                    f"{width}x{height}", "-r", "10", "-i", "-", "-c:v", "ffv1", str(source)],
                   input=raw * 10, check=True)
    values = ClipTransform(**transform)
    project = Project(name="eq", width=width, height=height, fps=10)
    project.media_assets.append(MediaAsset("a", str(source), "src", 1.0, width, height, 10, "video"))
    clip = Clip("v", "a", "V1", 0.0, 0.0, 1.0)
    clip.transform = values
    clip.effects = effects
    project.tracks.insert(0, Track("V1", "V1", "video", clips=[clip]))
    request = ExportRequest(render_plan=build_render_plan(project), output_path=str(tmp_path / "x.mp4"),
                            format=ExportFormat.MP4_H264, preset=ExportPreset("T", (width, height), 18, "64k"),
                            fps=10)
    completed = subprocess.run(ExportEngine().build_frame_command(request, 0.25), capture_output=True, timeout=60)
    assert completed.returncode == 0, completed.stderr
    image = QImage()
    image.loadFromData(completed.stdout)
    image = image.convertToFormat(QImage.Format.Format_RGB888)
    exported = np.frombuffer(image.constBits(), np.uint8).reshape(height, image.bytesPerLine())
    exported = exported[:, : width * 3].reshape(height, width, 3) / 255.0

    program = program_for(effects, vignette_extent=VIGNETTE_EXPORT)
    sources = {"v": reference_codes(codes)}

    def render(shift):
        matrix = affine_mul((1, 0, 0, 1, *shift), tuple(video_layer_matrix(values, width, height)))
        layer = CompositeLayer("v", matrix, fit_box(width, height, width, height).rect, values.opacity,
                               program=program, effect_scale=(values.scale, values.scale))
        frame = CompositeFrame(width, height, 1.0, (layer,), (VideoSource("v", "yuv420p", width, height),))
        return reference_frame(frame, sources)

    interior = (slice(40, -40), slice(60, -60))
    best = min(
        ((np.abs(render((dx, dy)) - exported)[interior].mean() * 255, dx, dy)
         for dx in (-1.5, -1.0, -0.5, 0.0, 0.5) for dy in (-1.5, -1.0, -0.5, 0.0, 0.5)),
    )
    error, dx, dy = best
    assert abs(dx) <= 1.5 and abs(dy) <= 1.5  # géométrie : même placement à 1,5 px près
    assert error <= color_limit, best           # couleurs et effets


# --- Cache de textures --------------------------------------------------------------------------------------


class _Textures:
    def __init__(self):
        self.live = set()
        self.next = 0

    def create(self):
        self.next += 1
        self.live.add(self.next)
        return self.next

    def release(self, handle):
        assert handle in self.live, "texture libérée deux fois"
        self.live.remove(handle)


def test_texture_cache_respects_its_budget_and_lru_order():
    textures = _Textures()
    cache = GpuTextureCache(32 * 2**20, release=textures.release)
    a, _ = cache.acquire("a", 12 * 2**20, textures.create)
    b, _ = cache.acquire("b", 12 * 2**20, textures.create)
    assert cache.get("a") == a  # « a » devient le plus récent
    c, _ = cache.acquire("c", 12 * 2**20, textures.create)
    assert cache.get("b") is None and cache.get("a") == a and cache.get("c") == c
    assert cache.stats().bytes <= cache.budget_bytes and cache.stats().evictions == 1
    big, cached = cache.acquire("big", 64 * 2**20, textures.create)
    assert not cached
    cache.discard_uncached(big)
    assert cache.purge() == 24 * 2**20 and textures.live == set()


def test_no_texture_leaks_over_many_cycles():
    """Seuil de non-régression : N cycles de création / éviction / purge, zéro fuite."""
    import random

    rng = random.Random(4)
    textures = _Textures()
    cache = GpuTextureCache(20 * 2**20, release=textures.release)
    for cycle in range(2000):
        size = rng.randint(1, 8) * 2**20
        handle, cached = cache.acquire(("m", rng.randint(0, 40)), size, textures.create)
        if not cached:
            cache.discard_uncached(handle)
        assert cache.stats().bytes <= cache.budget_bytes
        if cycle % 500 == 499:
            cache.purge()
    cache.purge()
    stats = cache.stats()
    assert textures.live == set() and stats.live == 0 and stats.created == stats.released


def test_auto_budget_scales_with_memory():
    assert default_budget(None) == 256 * 2**20
    assert default_budget(4 * 2**30) == 256 * 2**20
    assert default_budget(64 * 2**30) == 512 * 2**20
    assert default_budget(512 * 2**20) == 64 * 2**20


# --- Mémoire --------------------------------------------------------------------------------------------------


def test_memory_pressure_policy_is_progressive_and_fires_once():
    assert classify(100, 50) == NORMAL and classify(100, 9) == WARNING and classify(100, 3) == CRITICAL
    assert classify(None, None) == NORMAL
    assert pressure_actions(MemoryStatus(pressure=WARNING), gpu_active=True).purge_gpu_cache
    assert not pressure_actions(MemoryStatus(pressure=WARNING), gpu_active=True).disable_gpu
    critical = pressure_actions(MemoryStatus(pressure=CRITICAL), gpu_active=True)
    assert critical.reduce_quality and critical.disable_gpu
    assert not pressure_actions(MemoryStatus(pressure=CRITICAL), gpu_active=False).disable_gpu
    levels = iter([NORMAL, WARNING, WARNING, CRITICAL, CRITICAL, NORMAL, WARNING])
    watch = MemoryWatch(reader=lambda: MemoryStatus(pressure=next(levels)))
    fired = [watch.poll(gpu_active=True).any for _ in range(7)]
    assert fired == [False, True, False, True, False, False, True]


def test_memory_status_never_raises():
    assert read_memory_status("plan9").source == "unavailable"
    status = read_memory_status()
    assert status.pressure in (NORMAL, WARNING, CRITICAL)


# --- Choix du rendu ---------------------------------------------------------------------------------------------


def test_preview_backend_resolution():
    assert not resolve_preview_backend("cpu").is_gpu
    assert not resolve_preview_backend("gpu", environment={"KUT_STUDIO_GPU_PREVIEW": "off"}).is_gpu
    offscreen = resolve_preview_backend("gpu", environment={"QT_QPA_PLATFORM": "offscreen"})
    assert not offscreen.is_gpu and offscreen.fallback_reason
    auto = resolve_preview_backend("auto", environment={"QT_QPA_PLATFORM": "offscreen"})
    assert not auto.is_gpu and auto.fallback_reason == ""  # Auto : repli silencieux
    gpu = resolve_preview_backend("auto", environment={}, platform_name="darwin", platform_plugin="cocoa")
    assert gpu.is_gpu and gpu.api == "metal" and gpu.label == "GPU (Metal)"
    assert graphics_api("win32") == "d3d11" and graphics_api("linux") == "opengl"


def test_gpu_is_disabled_after_repeated_failures():
    health = GpuHealth()
    health.record("render", "pipeline impossible")
    assert resolve_preview_backend("gpu", health=health, environment={}, platform_plugin="cocoa").is_gpu
    health.record("device_lost", "pilote réinitialisé")
    resolved = resolve_preview_backend("gpu", health=health, environment={}, platform_plugin="cocoa")
    assert not resolved.is_gpu and "device_lost" in resolved.fallback_reason
    assert health.device_lost == 1
    health.reset()
    assert not health.disabled


def test_frame_stats_count_drops_and_render_cost():
    stats = FrameStats()
    for i in range(10):
        stats.note_arrival()
        if i % 2:
            stats.note_arrival()  # une image remplacée avant d'être dessinée
        stats.note_render(float(i))
    assert stats.received == 15 and stats.presented == 10 and stats.dropped == 5
    assert stats.average_render_ms() == pytest.approx(4.5) and stats.p95_render_ms() == 9.0


def test_gpu_overload_marks_the_adaptive_window():
    from core.preview_adaptive import AdaptiveConfig, AdaptiveQuality
    from core.preview_governor import gpu_overloaded

    stats = FrameStats()
    for _ in range(30):
        stats.note_arrival()
        stats.note_arrival()
        stats.note_render(5.0)
    assert gpu_overloaded(stats)
    calm = FrameStats()
    for _ in range(30):
        calm.note_arrival()
        calm.note_render(5.0)
    assert not gpu_overloaded(calm)
    quality = AdaptiveQuality(1, AdaptiveConfig(window=3, degrade_after=2, cooldown_seconds=0.0))
    now = 0.0
    changed = None
    for _ in range(2):  # ticks parfaitement à l'heure, mais le GPU perd des images
        quality.note_load(True)
        for _ in range(4):
            now += 0.04
            changed = quality.observe(now) or changed
    assert changed == 2


def test_quality_can_be_forced_down_under_memory_pressure():
    from core.preview_adaptive import AdaptiveQuality

    quality = AdaptiveQuality(1)
    assert quality.force_degrade() == 2 and quality.degraded


# --- Préférences ------------------------------------------------------------------------------------------------


def test_decode_and_preview_settings_persist_and_default_to_auto(tmp_path):
    from core.user_settings import UserSettings, load_user_settings, save_user_settings

    path = tmp_path / "user_settings.json"
    path.write_text(json.dumps({"theme_mode": "dark"}), encoding="utf-8")  # fichier antérieur
    old = load_user_settings(tmp_path)
    assert old.decode_mode == "auto" and old.preview_backend == "auto"
    save_user_settings(UserSettings(decode_mode="videotoolbox", preview_backend="cpu"), tmp_path)
    loaded = load_user_settings(tmp_path)
    assert loaded.decode_mode == "videotoolbox" and loaded.preview_backend == "cpu"
    path.write_text(json.dumps({"decode_mode": "warp", "preview_backend": 3}), encoding="utf-8")
    bad = load_user_settings(tmp_path)
    assert bad.decode_mode == "auto" and bad.preview_backend == "auto"


def test_preview_backend_enum_coercion():
    from core.gpu_backend import coerce_preview_backend

    assert coerce_preview_backend("GPU") is PreviewBackend.GPU
    assert coerce_preview_backend(None) is PreviewBackend.AUTO


# --- Shaders ----------------------------------------------------------------------------------------------------


def test_compiled_shaders_are_present_and_up_to_date():
    from tools.gpu.build_shaders import stale

    assert missing_shaders() == []
    assert stale() == [], "lancer : python -m tools.gpu.build_shaders"


def test_every_shader_variant_loads():
    from PySide6.QtGui import QShader

    from core.gpu_backend import SHADER_NAMES
    from ui.gpu_preview import load_shader

    for name in SHADER_NAMES:
        shader = load_shader(name)
        assert shader.isValid()
        sources = {key.source() for key in shader.availableShaders()}
        # Metal, Direct3D, OpenGL et Vulkan : une source pour chacun.
        assert {QShader.Source.MslShader, QShader.Source.HlslShader, QShader.Source.GlslShader,
                QShader.Source.SpirvShader} <= sources


# --- Seuils de non-régression -----------------------------------------------------------------------------------


def test_planning_a_frame_is_cheap():
    """Budget large (runner partagé) : planifier 4 calques avec effets reste sous 5 ms."""
    program = program_for([E(EffectType.COLOR_CORRECTION, brightness=0.1, contrast=1.2, saturation=1.1),
                           E(EffectType.BLUR, intensity=4.0), E(EffectType.VIGNETTE, intensity=0.4)])
    frame = CompositeFrame(1920, 1080, 0.5, tuple(_layer(program=program) for _ in range(4)), (SOURCE,))
    plan_frame(frame)
    started = time.perf_counter()
    for _ in range(50):
        plan_frame(frame)
    assert (time.perf_counter() - started) / 50 < 0.005


def test_rendering_and_export_modules_never_import_numpy_or_the_gpu_ui():
    code = (
        "import sys; import core.render_plan, core.export_engine, core.gpu_composite, core.gpu_effects, "
        "core.decode_policy, core.hardware_decoding, core.preview_engine;"
        "print('numpy' in sys.modules, 'ui.gpu_preview' in sys.modules)"
    )
    completed = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                               cwd=str(Path(__file__).resolve().parent.parent), env={**os.environ})
    assert completed.stdout.strip() == "False False", completed.stderr


def test_adjustment_layers_process_everything_below_through_their_coverage():
    np = pytest.importorskip("numpy")
    from core.gpu_composite import AdjustmentLayer, reference_frame

    adjustment = AdjustmentLayer(program_for([E(EffectType.BLACK_AND_WHITE)]), matte="cov")
    frame = CompositeFrame(320, 180, 1.0, (_layer(),), (VideoSource("v", "yuv420p", 320, 180),),
                           adjustments=(adjustment,))
    shaders = [p.shader for p in plan_frame(frame).passes]
    assert shaders == ["clear", "prep", "composite", "prep", "composite"]
    adjust_prep = plan_frame(frame).passes[3]
    assert adjust_prep.inputs[0].startswith("canvas")  # part de la composition, pas d'un fichier
    red = np.ones((180, 320, 3)) * np.array([81 / 255, 90 / 255, 240 / 255])  # rouge vif en BT.601
    coverage = np.zeros((180, 320))
    coverage[:, 160:] = 1.0
    out = reference_frame(frame, {"v": red}, {"cov": coverage})
    left, right = out[90, 40], out[90, 280]
    assert left[0] > 0.9 and left[1] < 0.1           # hors couverture : intact
    assert abs(right[0] - right[1]) < 0.02           # sous le calque d'effets : gris
    identity = AdjustmentLayer(program_for([]))
    assert len(plan_frame(CompositeFrame(320, 180, 1.0, (), (), adjustments=(identity,))).passes) == 1


def test_motion_blur_follows_the_preview_level_and_never_slows_playback():
    from core.motion_blur import MotionBlurSettings, preview_quality

    settings = MotionBlurSettings(samples=16)
    assert settings.samples_for(preview_quality(1, playing=True)) == 1       # lecture : coupé
    assert settings.samples_for(preview_quality(1, playing=False)) == 16     # Plein
    assert settings.samples_for(preview_quality(2, playing=False)) == 4      # 1/2
    assert settings.samples_for(preview_quality(4, playing=False)) == 1      # 1/4 et moins
    assert settings.samples_for("export") == 16                              # export inchangé
