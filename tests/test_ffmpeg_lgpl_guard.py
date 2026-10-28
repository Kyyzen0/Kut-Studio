"""Garde permanent (ADR-0013) : l'application n'emploie aucun filtre FFmpeg **GPL**.

La version Mac App Store embarque un FFmpeg sans ``--enable-gpl`` : ``eq`` (``eq_filter_deps="gpl"`` dans le ``configure``), ``boxblur``,
``hqdn3d``, ``perspective``… n'y existent pas, et le rendu échouerait ("No such filter"). Deux lectures se complètent :

- les **littéraux** du code de l'application (``core``, ``ui``, ``kut3d``, ``cli``), lus par ``tools.ffmpeg_license_audit`` ;
- les **chaînes réellement construites** pour tous les effets, étalonnages (fixes et animés) et couches, dont les noms de filtres peuvent
  être calculés à l'exécution.

La liste des filtres GPL est celle des ``configure`` de FFmpeg 6.1, 7.1 et 8.0 ; ``tools/ffmpeg_license_audit.py`` en dit plus et sert
aussi à auditer un FFmpeg donné (filtres employés que cette construction n'a pas).
"""

from __future__ import annotations

import os
import re
from dataclasses import replace

import pytest

from core.animation import InterpolationType, Keyframe
from core.color_grading import ColorGrade, Wheel
from core.effect_sendcmd import command_files, context_for
from core.effects_model import EFFECT_PARAMETER_SPECS, EffectType, create_effect, fx_property_id
from core.export_engine import _build_clip_effect_filters, _build_color_grade_filters, _build_layer_filter
from core.grade_targets import ANIMATABLE_GRADE_PARAMS, grade_spec
from core.render_plan import RenderLayer
from tools.ffmpeg_license_audit import GPL_FILTERS, filters_in, gpl_uses, scan_source


def chain_filter_names(graph: str) -> set[str]:
    """Tous les noms de filtres d'un graphe FFmpeg : segments séparés par ``,`` et ``;`` hors apostrophes et crochets, étiquettes ôtées,
    nom lu jusqu'à ``=`` ou ``@`` (les filtres sans option, comme ``null``, y compris)."""
    names: set[str] = set()
    depth, quoted, current = 0, False, []
    segments = []
    for char in graph:
        if char == "'":
            quoted = not quoted
        elif not quoted and char == "[":
            depth += 1
        elif not quoted and char == "]":
            depth -= 1
        elif not quoted and depth == 0 and char in ",;":
            segments.append("".join(current))
            current = []
            continue
        current.append(char)
    segments.append("".join(current))
    for segment in segments:
        text = re.sub(r"^(\s*\[[^\]]*\])+", "", segment).strip()
        match = re.match(r"[A-Za-z][A-Za-z0-9_]*", text)
        if match:
            names.add(match.group(0))
    return names


def assert_lgpl(graph: str, where: str) -> None:
    assert graph, where
    found = chain_filter_names(graph) & GPL_FILTERS
    assert not found, f"{where} : filtre(s) GPL {sorted(found)} dans {graph[:200]!r}"


# ---------------------------------------------------------------------------
# La lecture elle-même
# ---------------------------------------------------------------------------


def test_the_gpl_list_holds_what_the_configure_of_ffmpeg_says():
    assert {"eq", "boxblur", "hqdn3d", "colormatrix", "cropdetect", "perspective", "histeq", "smartblur"} <= GPL_FILTERS
    assert {"rubberband", "vidstabdetect", "vidstabtransform", "frei0r"} <= GPL_FILTERS                  # bibliothèques externes GPL
    # Les filtres LGPL que l'application emploie ne doivent jamais y figurer.
    assert not GPL_FILTERS & {"lut", "lutrgb", "lutyuv", "hue", "colorbalance", "colorchannelmixer", "curves", "lut3d", "gblur", "unsharp",
                              "vignette", "geq", "sendcmd", "xfade", "overlay", "scale", "format", "subtitles", "colorlevels"}


@pytest.mark.parametrize("text, expected", [
    ("eq=contrast=1.2:saturation=1.3", {"eq"}),
    ("format=yuv420p,eq=gamma=2,format=rgba", {"format", "eq"}),
    ("[0:v]eq=1[v]", {"eq"}),
    ("[a]hqdn3d=4[b];[b]boxblur=2", {"hqdn3d", "boxblur"}),
    ("lut@v0fx0=y=val,eq@named=brightness=0.1", {"lut", "eq"}),
    ("scale=w=1280:h=720:eq=3", {"scale"}),                                  # une option n'est pas un filtre
    ("select='eq(n,3)'", {"select"}),                                         # la fonction d'expression eq(n,…) n'est pas le filtre
    ("sendcmd=f='x.cmd',eq=contrast=1", {"sendcmd", "eq"}),
])
def test_the_reader_finds_filters_and_only_filters(text, expected):
    assert filters_in(text) == expected


def test_the_reader_sees_through_python_string_forms():
    source = (
        'def f(a):\n'
        '    """eq=contrast=1 dans un docstring : ignoré."""\n'
        '    one = "format=yuv420p,eq=contrast=1"\n'
        '    two = f"{a}hqdn3d=3"\n'
        '    three = "boxblur=" + "2"\n'
        '    four = f"[{a}]perspective=x0=1"\n'
        '    return "scale=1:1"\n'
    )
    found = scan_source(source)
    assert set(found) == {"format", "eq", "hqdn3d", "boxblur", "perspective", "scale"}
    assert found["eq"] == [3] and "docstring" not in str(found)


# ---------------------------------------------------------------------------
# Le code de l'application
# ---------------------------------------------------------------------------


def test_no_literal_of_the_application_names_a_gpl_filter():
    assert gpl_uses() == {}, (
        "filtre GPL dans le code de l'application : le FFmpeg de la version App Store (LGPL) ne l'a pas "
        "(ADR-0013, python -m tools.ffmpeg_license_audit)")


# ---------------------------------------------------------------------------
# Les chaînes réellement construites
# ---------------------------------------------------------------------------

RATE = 30
CONTEXT = context_for(RATE, 0.0, 3.0)


def _ramp(prop: str, low: float, high: float) -> list[Keyframe]:
    return [Keyframe(prop, 0.0, low, InterpolationType.LINEAR), Keyframe(prop, 2.0, high, InterpolationType.EASE_IN_OUT)]


def _all_effects():
    return tuple(create_effect(kind, effect_id=f"e-{kind.value}") for kind in EffectType)


def _effect_animation(effect):
    frames: list[Keyframe] = []
    for spec in EFFECT_PARAMETER_SPECS[effect.type]:
        frames += _ramp(fx_property_id(effect.id, spec.name), spec.minimum + 0.2 * (spec.maximum - spec.minimum),
                        spec.minimum + 0.8 * (spec.maximum - spec.minimum))
    return frames


@pytest.mark.parametrize("kind", list(EffectType), ids=lambda kind: kind.value)
@pytest.mark.parametrize("animated", [False, True], ids=["fixe", "anime"])
def test_every_effect_builds_a_chain_without_gpl_filter(kind, animated):
    effect = create_effect(kind, effect_id=f"e-{kind.value}")
    written: list[str] = []
    with command_files(written):
        graph = _build_clip_effect_filters(
            (effect,), 1.0, label="v0fx", animation=tuple(_effect_animation(effect)) if animated else None,
            sendcmd=CONTEXT if animated else None)
    for path in written:
        os.remove(path)
    if graph:                           # un effet sans réglage visible (aucun ici) pourrait ne rien émettre
        assert_lgpl(graph, f"{kind.value} {'animé' if animated else 'fixe'}")


def _busy_grade() -> ColorGrade:
    return ColorGrade(exposure=0.4, contrast=0.3, saturation=1.4, temperature=25.0, hue=14.0, shadows=0.2, highlights=-0.3,
                      lift=Wheel(0.05, 0.0, -0.05, 0.02), gamma=Wheel(0.0, 0.04, 0.0, -0.03), gain=Wheel(0.1, 0.0, 0.0, 0.05),
                      offset=Wheel(0.0, 0.02, 0.0, 0.0))


def test_every_grade_stage_builds_a_chain_without_gpl_filter():
    assert_lgpl(_build_color_grade_filters(_busy_grade()), "étalonnage fixe")
    for field, value in (("exposure", 0.5), ("contrast", 0.4), ("contrast", -0.4), ("saturation", 0.0), ("saturation", 2.0), ("hue", 30.0),
                         ("temperature", -40.0), ("shadows", 0.3), ("highlights", 0.3)):
        assert_lgpl(_build_color_grade_filters(replace(ColorGrade(), **{field: value})), f"étalonnage {field}={value}")


def test_every_animated_grade_stage_builds_a_chain_without_gpl_filter():
    animation: list[Keyframe] = []
    for property_id in ANIMATABLE_GRADE_PARAMS:
        spec = grade_spec(property_id)
        animation += _ramp(property_id, spec.minimum + 0.3 * (spec.maximum - spec.minimum), spec.minimum + 0.7 * (spec.maximum - spec.minimum))
    written: list[str] = []
    with command_files(written):
        graph = _build_color_grade_filters(_busy_grade(), "v0cg", animation=tuple(animation), sendcmd=CONTEXT)
    for path in written:
        os.remove(path)
    assert "sendcmd=" in graph and "lut@" in graph
    assert_lgpl(graph, "étalonnage animé")


def test_a_full_layer_builds_a_graph_without_gpl_filter():
    effects = _all_effects()
    animation = [frame for effect in effects for frame in _effect_animation(effect)]
    animation += [Keyframe("color.contrast", 0.0, -0.4, InterpolationType.LINEAR), Keyframe("color.contrast", 2.0, 0.4, InterpolationType.LINEAR),
                  Keyframe("color.saturation", 0.0, 0.2, InterpolationType.LINEAR), Keyframe("color.saturation", 2.0, 1.8, InterpolationType.LINEAR)]
    for animated in (False, True):
        layer = RenderLayer(
            clip_id="clip", asset_id="asset", track_id="V1", track_index=0, source_path="/tmp/source.mp4", source_in=0.0, source_out=3.0,
            timeline_start=0.0, timeline_end=3.0, effects=effects, color_grade=_busy_grade(), animation=tuple(animation) if animated else (),
        )
        written: list[str] = []
        with command_files(written):
            graph = _build_layer_filter(0, layer, 0, 640, 360, RATE)
        for path in written:
            os.remove(path)
        assert_lgpl(graph, f"couche {'animée' if animated else 'fixe'}")


def test_the_graphic_layers_chain_has_no_gpl_filter():
    from core.mograph_ffmpeg import _effect_chain

    chain = _effect_chain(_all_effects(), _busy_grade(), preserve_alpha=True)
    assert chain
    for item in chain:
        assert_lgpl(item.replace("__ALPHA_SAFE__", ""), "calque graphique")
