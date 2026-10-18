"""Rendu des paramètres d'effets animés par expression (ADR-0008, étape 3) : export, moniteur, base de temps, cache.

Sans interface : on construit des clips dont ``Clip.animation`` porte des images clés ``fx.<effet>.<paramètre>`` et l'on
vérifie ce que l'export (graphe FFmpeg réel) et le moniteur (programme GPU, référence numpy) en font.
"""

from __future__ import annotations

import math
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
from ffmpeg_expr import evaluate

from core.animation import InterpolationType, Keyframe
from core.animation_targets import targets_for_clip
from core.effect_expressions import GEQ_TIME, animated_effect_expressions, heat_haze_shift
from core.effect_targets import (
    ANIMATABLE_EFFECT_PARAMS,
    PENDING_EFFECT_PARAMS,
    effective_effect_parameters,
    evaluated_effects,
)
from core.effects_model import EFFECT_PARAMETER_SPECS, EffectType, create_effect, fx_property_id
from core.export_engine import _build_clip_effect_filters, _build_layer_filter, _format_seconds
from core.gpu_effects import program_for
from core.project_model import Clip, MediaAsset, Project, Track
from core.render_plan import RenderLayer, build_render_plan

HAS_FFMPEG = shutil.which("ffmpeg") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="FFmpeg absent")

L, B, H_, E_ = InterpolationType.LINEAR, InterpolationType.BEZIER, InterpolationType.HOLD, InterpolationType.EASE_IN_OUT


def kf(prop, time, value, interpolation=L, **slopes):
    return Keyframe(prop, time, value, interpolation, **slopes)


def clip_with(effects, animation) -> Clip:
    clip = Clip(id="c", asset_id="a", track_id="V1", timeline_start=0.0, source_in=0.0, source_out=5.0)
    clip.effects = list(effects)
    clip.animation = list(animation)
    return clip


def rich_curve(effect, name, *, overshoot=True):
    """Quatre images clés aux interpolations variées, qui touchent les deux bornes ; la Bézier dépasse la borne (écrêtée)."""
    spec = next(s for s in EFFECT_PARAMETER_SPECS[effect.type] if s.name == name)
    lo, hi = spec.minimum, spec.maximum
    span = hi - lo
    prop = fx_property_id(effect.id, name)
    slope = 6.0 * span if overshoot else None
    return [
        kf(prop, 0.5, lo + 0.2 * span, B, out_slope=slope, in_slope=slope),
        kf(prop, 1.5, lo + 0.9 * span, E_),
        kf(prop, 2.5, lo + 0.1 * span, H_),
        kf(prop, 4.0, hi, L),
    ]


SAMPLES = [-1.0, 0.0, 0.25, 0.5, 0.6, 0.9, 1.0, 1.25, 1.5, 1.75, 2.0, 2.4999, 2.5, 2.6, 3.0, 3.7, 4.0, 4.5, 6.0]
RENDERED = [(kind, name) for kind, names in ANIMATABLE_EFFECT_PARAMS.items() for name in names]


# ---------------------------------------------------------------------------
# (a) L'expression produite donne la valeur de la courbe
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind, name", RENDERED)
@pytest.mark.parametrize("var", ["t", "T"])
def test_each_rendered_expression_equals_the_python_curve(kind, name, var):
    effect = create_effect(kind, effect_id="e")
    clip = clip_with([effect], rich_curve(effect, name))
    expression = animated_effect_expressions(effect, clip.animation, var)[name]
    spec = next(s for s in EFFECT_PARAMETER_SPECS[kind] if s.name == name)
    clamped = 0
    for time in SAMPLES + [i / 30 for i in range(0, 181, 7)]:
        expected = effective_effect_parameters(clip, effect, time)[name]
        assert evaluate(expression, **{var: time}) == pytest.approx(expected, abs=1e-6), (kind, name, time)
        clamped += expected in (spec.minimum, spec.maximum)
    assert clamped, "la courbe de test doit toucher une borne (écrêtage)"


def test_a_single_keyframe_is_a_clamped_constant_and_no_keyframe_no_expression():
    effect = create_effect(EffectType.VIGNETTE, effect_id="v")
    assert animated_effect_expressions(effect, [], "t") == {}
    assert animated_effect_expressions(effect, None, "t") == {}
    assert animated_effect_expressions(effect, [kf("fx.other.intensity", 0.0, 0.3)], "t") == {}
    one = animated_effect_expressions(effect, [kf("fx.v.intensity", 2.0, 0.3)], "t")
    assert evaluate(one["intensity"], t=0.0) == pytest.approx(0.3) == evaluate(one["intensity"], t=9.0)


def test_pending_parameters_never_get_an_expression():
    for kind, names in PENDING_EFFECT_PARAMS.items():
        effect = create_effect(kind, effect_id="e")
        animation = [kf(fx_property_id("e", name), 0.0, 1.0) for name in names]
        assert animated_effect_expressions(effect, animation, "t") == {}


def test_filter_text_in_context_matches_the_python_formulas():
    """Le texte de l'export (et pas seulement l'expression nue) : ``eq``, angle de la vignette, décalage de la brume."""
    import re

    eq = create_effect(EffectType.COLOR_CORRECTION, effect_id="cc", params={"saturation": 1.5})
    vig = create_effect(EffectType.VIGNETTE, effect_id="vg")
    animation = rich_curve(eq, "brightness") + rich_curve(eq, "contrast") + rich_curve(vig, "intensity")
    clip = clip_with([eq, vig], animation)
    text = _build_clip_effect_filters(tuple(clip.effects), animation=tuple(clip.animation))
    eq_text, vignette_text = text.split(",vignette=")
    assert eq_text.endswith(":eval=frame") and vignette_text.endswith(":eval=frame")
    assert "saturation=1.5:" in eq_text                          # le paramètre non animé reste une constante
    brightness = re.search(r"brightness='(.*?)':contrast=", eq_text).group(1)
    contrast = re.search(r"contrast='(.*?)':saturation=", eq_text).group(1)
    angle = re.search(r"angle='(.*)':eval=frame", vignette_text).group(1)
    for time in SAMPLES:
        values = effective_effect_parameters(clip, eq, time)
        assert evaluate(brightness, t=time) == pytest.approx(values["brightness"], abs=1e-6)
        assert evaluate(contrast, t=time) == pytest.approx(values["contrast"], abs=1e-6)
        assert evaluate(angle, t=time) == pytest.approx(
            effective_effect_parameters(clip, vig, time)["intensity"] * math.pi / 4, abs=1e-6)


@pytest.mark.parametrize("pixel_scale", [1.0, 0.5])
@pytest.mark.parametrize("animated", [("amplitude",), ("frequency",), ("top", "span"), ("amplitude", "frequency", "top", "span")])
def test_heat_haze_shift_equals_the_static_formula_at_the_evaluated_values(animated, pixel_scale):
    haze = create_effect(EffectType.HEAT_HAZE, effect_id="h", params={"amplitude": 6.0, "frequency": 0.08, "speed": 9.0})
    animation = [frame for name in animated for frame in rich_curve(haze, name, overshoot=False)]
    clip = clip_with([haze], animation)
    expressions = animated_effect_expressions(haze, animation, GEQ_TIME)
    assert set(expressions) == set(animated)
    shift = heat_haze_shift(haze.params, expressions, pixel_scale, _format_seconds)
    for time in (0.0, 0.7, 1.5, 2.6, 4.0, 5.0):
        values = effective_effect_parameters(clip, haze, time)
        for row, height in ((0, 180), (60, 180), (110, 180), (179, 180)):
            reference = math.floor(
                values["amplitude"] * pixel_scale
                * math.sin(row * (values["frequency"] / max(1e-6, pixel_scale)) + time * values["speed"])
                * min(1.0, max(0.0, (row / height - values["top"]) / values["span"])))
            assert evaluate(shift, T=time, Y=row, H=height) == reference, (animated, time, row)


# ---------------------------------------------------------------------------
# (b) Sans animation : le graphe est celui d'avant, caractère pour caractère
# ---------------------------------------------------------------------------

# Calculés sur une copie de origin/main (3aa06b8) : même couche, ``_build_layer_filter`` sans et avec ``origin``.
GOLDEN_FULL = (
    "[0:v]trim=start=0.0:end=3.0,setpts=PTS-STARTPTS,scale=1280:720:force_original_aspect_ratio=decrease,"
    "pad=1280:720:floor((ow-iw)/4)*2:floor((oh-ih)/4)*2:black,fps=30,setpts=PTS-STARTPTS,"
    "scale=flags=bilinear+full_chroma_int+accurate_rnd:in_h_chr_pos=0:in_v_chr_pos=0,format=yuva444p,"
    "geq=lum='p(X-floor(5.0*sin(Y*0.06+T*7.0)*clip((Y/H-0.25)/0.5,0,1)),Y)':"
    "cb='p(X-floor(5.0*sin(Y*0.06+T*7.0)*clip((Y/H-0.25)/0.5,0,1)),Y)':"
    "cr='p(X-floor(5.0*sin(Y*0.06+T*7.0)*clip((Y/H-0.25)/0.5,0,1)),Y)':a='p(X,Y)',"
    "scale=w='trunc(iw*(1.0)*1280.0/iw)':h='trunc(ih*(1.0)*720.0/ih)':eval=frame,"
    "eq=brightness=0.2:contrast=1.3:saturation=0.7,gblur=sigma=4.0,vignette=angle=0.392699,format=rgba,"
    "rgbashift=rh=3:bh=-3:edge=smear,format=rgba,format=yuva444p,format=rgba,setpts=PTS+1.0/TB+0.001[v0]"
)
GOLDEN_SEGMENT = (
    "[0:v]trim=start=1.0:end=3.0,setpts=PTS-STARTPTS+1.0/TB,scale=640:360:force_original_aspect_ratio=decrease,"
    "pad=640:360:floor((ow-iw)/4)*2:floor((oh-ih)/4)*2:black,fps=25,setpts=PTS-STARTPTS+1.0/TB,"
    "scale=flags=bilinear+full_chroma_int+accurate_rnd:in_h_chr_pos=0:in_v_chr_pos=0,format=yuva444p,"
    "geq=lum='p(X-floor(2.5*sin(Y*0.12+T*7.0)*clip((Y/H-0.25)/0.5,0,1)),Y)':"
    "cb='p(X-floor(2.5*sin(Y*0.12+T*7.0)*clip((Y/H-0.25)/0.5,0,1)),Y)':"
    "cr='p(X-floor(2.5*sin(Y*0.12+T*7.0)*clip((Y/H-0.25)/0.5,0,1)),Y)':a='p(X,Y)',"
    "scale=w='trunc(iw*(1.0)*640.0/iw)':h='trunc(ih*(1.0)*360.0/ih)':eval=frame,"
    "eq=brightness=0.2:contrast=1.3:saturation=0.7,gblur=sigma=2.0,vignette=angle=0.392699,format=rgba,"
    "rgbashift=rh=2:bh=-2:edge=smear,format=rgba,format=yuva444p,format=rgba,setpts=PTS+1.0/TB+0.001[v0]"
)


def golden_layer(animation=()):
    effects = (
        create_effect(EffectType.COLOR_CORRECTION, effect_id="color", params={"brightness": 0.2, "contrast": 1.3, "saturation": 0.7}),
        create_effect(EffectType.BLUR, effect_id="blur", params={"intensity": 4}),
        create_effect(EffectType.VIGNETTE, effect_id="vignette", params={"intensity": 0.5}),
        create_effect(EffectType.HEAT_HAZE, effect_id="haze", params={"amplitude": 5.0, "frequency": 0.06, "speed": 7.0,
                                                                     "top": 0.25, "span": 0.5}),
        create_effect(EffectType.CHROMATIC_ABERRATION, effect_id="ca", params={"intensity": 3}),
    )
    return RenderLayer(
        clip_id="clip-effects", asset_id="asset-1", track_id="V1", track_index=0, source_path="/tmp/source.mp4",
        source_in=0.0, source_out=3.0, timeline_start=1.0, timeline_end=4.0, effects=effects, animation=tuple(animation),
    )


# Images clés qui ne doivent RIEN changer : autres propriétés, effet absent, paramètres en attente ou exclus.
IGNORED_ANIMATION = (
    kf("mask.m.feather", 0.0, 3.0), kf("graphic.width", 1.0, 5.0), kf("fx.gone.intensity", 0.0, 0.5),
    kf("fx.blur.intensity", 0.0, 9.0), kf("fx.blur.intensity", 2.0, 30.0),           # flou : en attente (étape 5)
    kf("fx.haze.speed", 0.0, 1.0), kf("fx.haze.speed", 2.0, 30.0),                    # vitesse de la brume : saut de phase
    kf("fx.ca.intensity", 0.0, 9.0),
)


@pytest.mark.parametrize("animation", [(), IGNORED_ANIMATION], ids=["sans-animation", "images-cles-ignorees"])
def test_the_graph_is_identical_to_main_without_rendered_keyframes(animation):
    assert _build_layer_filter(0, golden_layer(animation), 0, 1280, 720, 30) == GOLDEN_FULL
    assert _build_layer_filter(0, golden_layer(animation), 0, 640, 360, 25, pixel_scale=0.5, origin=2.0) == GOLDEN_SEGMENT


def test_the_effect_chain_alone_is_unchanged_with_or_without_the_animation_argument():
    layer = golden_layer()
    assert _build_clip_effect_filters(layer.effects) == _build_clip_effect_filters(layer.effects, animation=())
    assert _build_clip_effect_filters(layer.effects) == _build_clip_effect_filters(layer.effects, animation=IGNORED_ANIMATION)


# ---------------------------------------------------------------------------
# (e) Cohérence : la liste enregistrée = des paramètres rendus
# ---------------------------------------------------------------------------


def _all_effects():
    return [create_effect(kind, effect_id=f"e-{kind.value}") for kind in EffectType]


@pytest.mark.parametrize("kind, name", RENDERED)
def test_every_registered_parameter_changes_the_export_graph(kind, name):
    effect = create_effect(kind, effect_id="e")
    layer = replace(golden_layer(), effects=(effect,))
    static = _build_layer_filter(0, layer, 0, 1280, 720, 30)
    animated = _build_layer_filter(0, replace(layer, animation=tuple(rich_curve(effect, name))), 0, 1280, 720, 30)
    assert animated != static


@pytest.mark.parametrize("kind, names", list(PENDING_EFFECT_PARAMS.items()))
def test_pending_parameters_change_nothing_and_are_not_exposed(kind, names):
    effect = create_effect(kind, effect_id="e")
    layer = replace(golden_layer(), effects=(effect,))
    animation = tuple(kf(fx_property_id("e", name), 0.0, 2.0) for name in names)
    assert _build_layer_filter(0, replace(layer, animation=animation), 0, 1280, 720, 30) == _build_layer_filter(
        0, layer, 0, 1280, 720, 30)
    clip = clip_with(_all_effects(), [])
    exposed = {t.id for t in targets_for_clip(clip, "video", include_effects=True)}
    assert all(fx_property_id(f"e-{kind.value}", name) not in exposed for name in names)


# ---------------------------------------------------------------------------
# Moniteur GPU : paramètres évalués
# ---------------------------------------------------------------------------


def test_the_monitor_receives_evaluated_parameters_and_untouched_effects_without_keyframes():
    effects = tuple(_all_effects())
    assert evaluated_effects(effects, (), 1.0) == effects
    assert all(a is b for a, b in zip(evaluated_effects(effects, IGNORED_ANIMATION[:3], 1.0), effects, strict=True))
    assert program_for(evaluated_effects(effects, (), 1.0), time=0.7) == program_for(effects, time=0.7)
    vig = create_effect(EffectType.VIGNETTE, effect_id="vg")
    animation = [kf("fx.vg.intensity", 0.0, 0.0), kf("fx.vg.intensity", 2.0, 1.0)]
    half = evaluated_effects((vig,), animation, 1.0)[0]
    assert half.params["intensity"] == pytest.approx(0.5) and vig.params["intensity"] == 0.5 and half.id == vig.id
    assert program_for((half,)) != program_for(evaluated_effects((vig,), animation, 2.0))
    assert program_for((half,)) == program_for((create_effect(EffectType.VIGNETTE, effect_id="x", params={"intensity": 0.5}),))


def test_a_disabled_effect_stays_disabled_when_evaluated():
    vig = create_effect(EffectType.VIGNETTE, effect_id="vg", enabled=False)
    out = evaluated_effects((vig,), [kf("fx.vg.intensity", 0.0, 0.9)], 0.0)[0]
    assert out.enabled is False and out.params["intensity"] == 0.9
    assert program_for((out,)) == program_for(())


# ---------------------------------------------------------------------------
# (g) Cache : l'empreinte suit les images clés fx.*
# ---------------------------------------------------------------------------

FINGERPRINT_MAIN = "56beeffd56d2527347dfc095308ee639637615c70e3c615352e1d0086421d40d"   # origin/main (3aa06b8), moteur 12


def _fingerprint(animation=(), monkeypatch=None, version=None):
    import core.filter_graph as filter_graph

    if monkeypatch is not None and version is not None:
        monkeypatch.setattr(filter_graph, "RENDER_ENGINE_VERSION", version)
    project = Project(name="x", width=160, height=90, fps=10.0)
    project.media_assets.append(MediaAsset("a", "/tmp/none.mp4", "src", 6.0, 160, 90, 10.0, "video"))
    clip = Clip("v", "a", "V1", 1.0, 0.0, 4.0)
    clip.effects = [create_effect(EffectType.COLOR_CORRECTION, effect_id="cc", params={"brightness": 0.1}),
                    create_effect(EffectType.VIGNETTE, effect_id="vig", params={"intensity": 0.6}),
                    create_effect(EffectType.HEAT_HAZE, effect_id="hz")]
    clip.animation = list(animation)
    project.tracks.append(Track("V1", "V1", "video", clips=[clip]))
    return filter_graph.fingerprint_plan(build_render_plan(project), width=160, height=90, fps=10, quality="high")


def test_the_engine_version_is_13_and_only_it_changes_the_fingerprint_of_a_clip_without_fx_keyframes(monkeypatch):
    import core.filter_graph as filter_graph

    assert filter_graph.RENDER_ENGINE_VERSION == 13
    assert _fingerprint() != FINGERPRINT_MAIN
    assert _fingerprint(monkeypatch=monkeypatch, version=12) == FINGERPRINT_MAIN


def test_editing_an_fx_keyframe_changes_the_fingerprint():
    base = [kf("fx.vig.intensity", 0.0, 0.2), kf("fx.vig.intensity", 2.0, 0.8)]
    edited = [kf("fx.vig.intensity", 0.0, 0.2), kf("fx.vig.intensity", 2.0, 0.9)]
    moved = [kf("fx.vig.intensity", 0.0, 0.2), kf("fx.vig.intensity", 2.5, 0.8)]
    prints = {_fingerprint(a) for a in ((), base, edited, moved)}
    assert len(prints) == 4


# ---------------------------------------------------------------------------
# FFmpeg réel : parité export / moniteur, export animé = export statique, base de temps
# ---------------------------------------------------------------------------

RATE = 4.0                                              # images par seconde de la mire (durée 2 s : 9 images)
FRAMES = 9


def _ffmpeg_frames(chain, raw, *, pix_fmt, width, height, count):
    """Images RVBA de ``count`` copies de ``raw`` passées dans ``chain`` (PTS de 0 à ``count / RATE``, comme un clip)."""
    np = pytest.importorskip("numpy")
    completed = subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "rawvideo", "-pix_fmt", pix_fmt, "-s", f"{width}x{height}", "-framerate",
         str(RATE), "-i", "-", "-vf", chain, "-f", "rawvideo", "-"],
        input=raw * count, capture_output=True, check=True,
    )
    frames = np.frombuffer(completed.stdout, np.uint8)
    return frames.reshape(count, height, width, 4)[..., :3]


def _chain(effects, animation, *, full_chroma):
    from core.chroma_siting import siting_before_rgb

    text = _build_clip_effect_filters(tuple(effects), animation=tuple(animation))
    graph = (text + "," if text else "") + "format=rgba"
    return graph if full_chroma else siting_before_rgb(graph)


def _source(width, height, full_chroma):
    from tests.gpu_harness import smooth_pattern

    codes = smooth_pattern(width, height)
    if full_chroma:
        return codes, codes[..., 0].tobytes() + codes[..., 1].tobytes() + codes[..., 2].tobytes(), "yuv444p"
    return codes, codes[..., 0].tobytes() + codes[::2, ::2, 1].tobytes() + codes[::2, ::2, 2].tobytes(), "yuv420p"


def _parity_cases():
    cc = create_effect(EffectType.COLOR_CORRECTION, effect_id="cc", params={"saturation": 1.3})
    vg = create_effect(EffectType.VIGNETTE, effect_id="vg")
    hz = create_effect(EffectType.HEAT_HAZE, effect_id="hz", params={"amplitude": 6.0, "frequency": 0.08, "speed": 9.0,
                                                                     "top": 0.2, "span": 0.5})
    def ramp(effect, name, lo, hi, kind=L):
        prop = fx_property_id(effect.id, name)
        return [kf(prop, 0.0, lo, kind), kf(prop, 2.0, hi, kind)]

    return {
        # Luminosité >= 0 : en dessous, le ``eq`` statique s'écarte déjà de la formule GPU (1,9 de moyenne à -0,1, 2,8 à -0,2,
        # mesuré sur la valeur FIXE : ce n'est pas l'animation), voir ``docs/animation.md``.
        "eq": ([cc], ramp(cc, "brightness", 0.0, 0.3) + ramp(cc, "contrast", 0.9, 1.6), False, (0.5, 3.5)),
        "vignette": ([vg], ramp(vg, "intensity", 0.0, 1.0, E_), False, (1.0, 4.0)),
        "haze_amplitude": ([hz], ramp(hz, "amplitude", 1.0, 9.0), True, (0.6, 3.0)),
        "haze_frequency_top_span": ([hz], ramp(hz, "frequency", 0.03, 0.12) + ramp(hz, "top", 0.1, 0.5)
                                    + ramp(hz, "span", 0.3, 0.8), True, (0.6, 3.0)),
    }


@needs_ffmpeg
@pytest.mark.parametrize("case", ["eq", "vignette", "haze_amplitude", "haze_frequency_top_span"])
def test_the_animated_export_matches_the_monitor_reference_like_a_static_effect(case):
    """Parité export / moniteur : à chaque image échantillonnée, l'effet rendu par FFmpeg (expression) et la référence du
    moniteur (paramètres évalués à cet instant) concordent aux seuils d'un effet statique (``test_gpu_pipeline``)."""
    np = pytest.importorskip("numpy")
    from core.gpu_effects import reference_layer
    from core.gpu_frames import yuv_to_rgb_matrix
    from tests.gpu_harness import sited_codes
    from tests.test_gpu_pipeline import _swscale_conversion_error

    effects, animation, full_chroma, (mean_limit, p99_limit) = _parity_cases()[case]
    width, height = 320, 180
    codes, raw, pix_fmt = _source(width, height, full_chroma)
    frames = _ffmpeg_frames(_chain(effects, animation, full_chroma=full_chroma), raw, pix_fmt=pix_fmt, width=width,
                            height=height, count=FRAMES)
    full = codes.astype(float) / 255.0 if full_chroma else sited_codes(codes)
    base_mean, base_p99 = _swscale_conversion_error()
    changed = 0
    clip = clip_with(effects, animation)
    for index in range(FRAMES):
        time = index / RATE
        program = program_for(evaluated_effects(tuple(clip.effects), tuple(clip.animation), time), time=time)
        expected = reference_layer(full, program, yuv_to_rgb=yuv_to_rgb_matrix())
        diff = np.abs(expected - frames[index] / 255.0) * 255
        assert float(diff.mean()) <= mean_limit + base_mean and float(np.percentile(diff, 99)) <= p99_limit + base_p99, (
            case, index, float(diff.mean()), float(np.percentile(diff, 99)))
        if index:
            changed += int(np.abs(frames[index].astype(int) - frames[0].astype(int)).max() > 4)
    assert changed >= FRAMES // 2, "l'effet doit réellement varier d'une image à l'autre"


@needs_ffmpeg
@pytest.mark.parametrize("case", ["eq", "vignette", "haze_amplitude", "haze_frequency_top_span"])
def test_each_animated_frame_equals_the_static_export_at_the_evaluated_value(case):
    """Méthode de ``tools/validate_sendcmd.py`` : l'image ``n`` de la sortie animée égale (écart max <= 1) l'image du rendu
    STATIQUE du même effet réglé sur la valeur de la courbe à l'instant de l'image."""
    np = pytest.importorskip("numpy")
    effects, animation, full_chroma, _limits = _parity_cases()[case]
    width, height = 320, 180
    _codes, raw, pix_fmt = _source(width, height, full_chroma)
    animated = _ffmpeg_frames(_chain(effects, animation, full_chroma=full_chroma), raw, pix_fmt=pix_fmt, width=width,
                              height=height, count=FRAMES)
    clip = clip_with(effects, animation)
    for index in (0, 1, 3, 5, 8):
        time = index / RATE
        fixed = evaluated_effects(tuple(clip.effects), tuple(clip.animation), time)
        # Le rendu statique a le même temps d'image (``T``) : la brume dépend de ``T·vitesse`` ; on le rejoue donc sur
        # ``index + 1`` images et l'on garde la dernière.
        static = _ffmpeg_frames(_chain(fixed, (), full_chroma=full_chroma), raw, pix_fmt=pix_fmt, width=width,
                                height=height, count=index + 1)[index]
        worst = int(np.abs(animated[index].astype(int) - static.astype(int)).max())
        assert worst <= 1, (case, index, worst)


# --- (d) base de temps : clip entier contre segment d'aperçu qui démarre en milieu de clip ------------------------

W, H, FPS, LENGTH = 160, 90, 10, 6.0


def _project(tmp_path: Path, effects, animation, *, source_fps=10, source_in=0.5, timeline_start=1.0, speed=None):
    from core.timeline_operations import set_clip_speed

    source = tmp_path / f"src{source_fps}.mp4"
    if not source.exists():
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        f"testsrc2=s={W}x{H}:r={source_fps}:d={LENGTH}", "-pix_fmt", "yuv420p", str(source)], check=True)
    project = Project(name="Base de temps", width=W, height=H, fps=float(FPS))
    project.media_assets.append(MediaAsset("a", str(source), "src", LENGTH, W, H, float(source_fps), "video"))
    clip = Clip("v", "a", "V1", timeline_start, source_in, source_in + 5.0)
    clip.effects = list(effects)
    clip.animation = list(animation)
    project.tracks.append(Track("V1", "V1", "video", clips=[clip]))
    if speed is not None:
        set_clip_speed(project, "v", speed)
    return project


def _render_frames(plan, tmp_path, name, *, start, duration):
    """Images ``yuv420p`` (liste de tableaux) du segment ``[start, start + duration)``, sans perte : même graphe que l'export."""
    np = pytest.importorskip("numpy")
    from core.filter_graph import build_preview_command

    command = build_preview_command(plan, width=W, height=H, fps=FPS, quality="high", start=start, duration=duration,
                                    output_path=str(tmp_path / f"{name}.mp4"))
    output = tmp_path / f"{name}.mkv"
    tail = min(command.index(option) for option in ("-ss", "-t") if option in command)    # sélection du segment
    lossless = [*command[: command.index("-c:v")], "-c:v", "ffv1", "-c:a", "pcm_s16le", *command[tail:-1], str(output)]
    completed = subprocess.run(lossless, capture_output=True, text=True, timeout=120)
    assert completed.returncode == 0, completed.stderr
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(output), "-f", "rawvideo", "-pix_fmt", "yuv420p", "-"],
                         capture_output=True, check=True).stdout
    size = W * H * 3 // 2
    return [np.frombuffer(raw[i * size:(i + 1) * size], np.uint8).astype(int) for i in range(len(raw) // size)]


TIME_BASE_SCENARIOS = {
    # nom: (arguments de _project, début du segment sur la timeline, durée du segment)
    "clip_decale": (dict(), 3.0, 1.0),                       # le clip commence à 1 s sur la timeline, source rognée à 0,5 s
    "cadence_24_vers_10": (dict(source_fps=24), 3.0, 1.0),   # changement de cadence (fps) avant l'effet
    "clip_retime_x2": (dict(speed=2.0), 2.0, 0.5),           # vitesse : le temps local est celui de la timeline
}


def _time_base_effects(kind):
    if kind == "eq":
        effect = create_effect(EffectType.COLOR_CORRECTION, effect_id="cc")
        prop = fx_property_id("cc", "brightness")
        return [effect], [kf(prop, 0.0, -0.4), kf(prop, 4.0, 0.4)]
    effect = create_effect(EffectType.HEAT_HAZE, effect_id="hz", params={"amplitude": 4.0, "frequency": 0.1, "speed": 6.0,
                                                                        "top": 0.1, "span": 0.4})
    prop = fx_property_id("hz", "amplitude")
    return [effect], [kf(prop, 0.0, 0.0), kf(prop, 4.0, 12.0)]


@needs_ffmpeg
@pytest.mark.parametrize("kind", ["eq", "haze"])
@pytest.mark.parametrize("scenario", list(TIME_BASE_SCENARIOS))
def test_a_segment_starting_mid_clip_renders_the_same_animated_frames_as_the_whole_clip(tmp_path, scenario, kind):
    """Les images clés sont en temps local du clip : un segment qui démarre en milieu de clip (``origin``) garde les
    horodatages locaux (``PTS-STARTPTS+skip/TB``), le clip entier aussi. Les deux donnent les mêmes images, et chacune
    égale le rendu STATIQUE réglé sur la valeur de la courbe à son instant local (la base de temps est absolue)."""
    np = pytest.importorskip("numpy")
    options, start, duration = TIME_BASE_SCENARIOS[scenario]
    effects, animation = _time_base_effects(kind)
    timeline_start = 1.0
    plan = build_render_plan(_project(tmp_path, effects, animation, **options))
    full = _render_frames(plan, tmp_path, "full", start=0.0, duration=6.0)
    segment = _render_frames(plan, tmp_path, "segment", start=start, duration=duration)
    first = round(start * FPS)
    assert len(segment) >= round(duration * FPS)
    for index, frame in enumerate(segment):
        assert int(np.abs(frame - full[first + index]).max()) <= 1, (scenario, kind, index)
    # Valeur absolue : image du segment contre rendu statique de la valeur évaluée à son temps local.
    clip = clip_with(effects, animation)
    for index in (0, len(segment) // 2, len(segment) - 1):
        local = start + index / FPS - timeline_start
        fixed = evaluated_effects(tuple(clip.effects), tuple(clip.animation), local)
        static_plan = build_render_plan(_project(tmp_path, list(fixed), [], **options))
        # Rendu entier (et non un segment d'une image : ``-ss`` y choisit parfois l'image voisine) dont on prend l'image.
        static = _render_frames(static_plan, tmp_path, "static", start=0.0, duration=6.0)[first + index]
        worst = int(np.abs(segment[index] - static).max())
        if kind == "eq":
            assert worst <= 1, (scenario, index, local, worst)
        else:
            # La brume décale des lignes entières : un écart d'un demi-pixel de phase déplace une ligne (255 niveaux sur
            # quelques lignes). On exige donc que presque toutes les lignes coïncident.
            assert float(np.mean(np.abs(segment[index] - static) > 1)) < 0.03, (scenario, index, local)
