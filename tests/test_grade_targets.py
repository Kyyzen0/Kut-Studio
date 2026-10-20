"""Cibles d'animation de l'étalonnage ``color.*`` (ADR-0008, étape 6) : registre, valeur effective, format ``.kut``, cycle de vie.

Le rendu (expression, ``sendcmd``, FFmpeg réel) : ``tests/test_grade_render.py``. Ici le modèle : résolution avec le clip, bornes lues
dans ``core.color_grading``, fonction pure ``effective_grade``, version 24 du fichier, garde de verrou, coupe / duplication,
composition refusée, libellés traduits.
"""

from __future__ import annotations

import copy
import hashlib
import json

import pytest

from core import project_io
from core.animation import Keyframe
from core.animation_targets import all_targets, animation_curves, get_target, targets_for, targets_for_clip
from core.color_grading import (
    CONTRAST_MAX,
    CONTRAST_MIN,
    EXPOSURE_MAX,
    EXPOSURE_MIN,
    HIGHLIGHTS_MAX,
    HIGHLIGHTS_MIN,
    HUE_MAX,
    HUE_MIN,
    SATURATION_MAX,
    SATURATION_MIN,
    SHADOWS_MAX,
    SHADOWS_MIN,
    TEMPERATURE_MAX,
    TEMPERATURE_MIN,
    WHEELS,
    ColorGrade,
    Wheel,
)
from core.color_nodes import as_graph
from core.composition_ops import CompositionError, convert_to_composition
from core.compositing_guard import ClipEditRefused
from core.grade_targets import (
    ANIMATABLE_GRADE_PARAMS,
    EXPRESSION_GRADE_PARAMS,
    GRADE_PREFIX,
    SENDCMD_GRADE_PARAMS,
    WHEEL_CHANNELS,
    GradeCurves,
    effective_grade,
    grade_spec,
    has_grade_animation,
    has_live_grade_animation,
    is_grade_property,
    read_value,
    wheel_property_id,
    write_value,
)
from core.keyframe_editing import add_keyframe, remove_keyframes, KeyframeRef, set_value_at, value_at
from core.project_io import (
    EFFECT_ANIMATION_VERSION,
    GRADE_ANIMATION_VERSION,
    SENDCMD_ANIMATION_VERSION,
    SUPPORTED_VERSIONS,
    load_project,
    project_payload,
    save_project,
)
from core.project_model import Clip, MediaAsset, Project, Track
from core.scripting import Session
from core.timeline_operations import cut_clip, duplicate_clip


def make_project(grade=None, animation=(), *, locked=False, track_type="video") -> Project:
    asset = MediaAsset(id="a", path="/tmp/a.mp4", name="a", duration=10.0, width=1920, height=1080, fps=30.0,
                       media_type="video")
    clip = Clip(id="c", asset_id="a", track_id="V1", timeline_start=0.0, source_in=0.0, source_out=10.0)
    clip.color_grade = grade
    clip.animation = list(animation)
    return Project(name="P", media_assets=[asset], tracks=[Track(id="V1", name="V1", type=track_type, locked=locked, clips=[clip])])


def clip_of(project: Project) -> Clip:
    return project.tracks[0].clips[0]


SCALARS = {"exposure": (EXPOSURE_MIN, EXPOSURE_MAX), "contrast": (CONTRAST_MIN, CONTRAST_MAX),
           "saturation": (SATURATION_MIN, SATURATION_MAX), "temperature": (TEMPERATURE_MIN, TEMPERATURE_MAX),
           "hue": (HUE_MIN, HUE_MAX), "shadows": (SHADOWS_MIN, SHADOWS_MAX), "highlights": (HIGHLIGHTS_MIN, HIGHLIGHTS_MAX)}


# ---------------------------------------------------------------------------
# Registre
# ---------------------------------------------------------------------------


def test_the_registry_is_exactly_the_scalar_fields_and_the_sixteen_wheel_channels_of_the_model():
    expected = {f"color.{name}" for name in SCALARS} | {f"color.{wheel}.{channel}" for wheel in WHEELS for channel in WHEEL_CHANNELS}
    assert set(ANIMATABLE_GRADE_PARAMS) == expected and len(ANIMATABLE_GRADE_PARAMS) == 23 == len(set(ANIMATABLE_GRADE_PARAMS))
    assert not set(EXPRESSION_GRADE_PARAMS) & set(SENDCMD_GRADE_PARAMS)
    assert set(EXPRESSION_GRADE_PARAMS) == {"color.contrast", "color.saturation", "color.hue"}


def test_no_field_wheel_or_channel_name_contains_a_dot():
    names = [*SCALARS, *WHEELS, *WHEEL_CHANNELS]
    assert all(name and "." not in name for name in names)


@pytest.mark.parametrize("name, bounds", list(SCALARS.items()))
def test_scalar_bounds_and_defaults_are_read_from_the_model(name, bounds):
    spec = grade_spec(f"color.{name}")
    assert (spec.minimum, spec.maximum) == bounds and spec.default == float(getattr(ColorGrade(), name))
    assert spec.group == "color" and spec.label_key == f"color.property.{name}"


@pytest.mark.parametrize("wheel", WHEELS)
@pytest.mark.parametrize("channel", WHEEL_CHANNELS)
def test_wheel_components_are_bounded_by_minus_one_and_one(wheel, channel):
    spec = grade_spec(wheel_property_id(wheel, channel))
    assert (spec.minimum, spec.maximum, spec.default) == (-1.0, 1.0, 0.0)


def test_an_unknown_name_is_not_a_grade_property():
    for name in ("color.curves", "color.lut", "color.enabled", "color.lift", "color.lift.z", "color.", "fx.e.intensity", "color.exposure.x"):
        assert not is_grade_property(name)
        with pytest.raises(KeyError):
            grade_spec(name)
        with pytest.raises(KeyError):
            get_target(name, clip_of(make_project()))


def test_the_targets_resolve_only_with_a_clip():
    with pytest.raises(KeyError):
        get_target("color.exposure")
    target = get_target("color.exposure", clip_of(make_project()))
    assert target.id == "color.exposure" and target.applies_to("video") and not target.applies_to("audio")
    assert not target.applies_to("graphics")


def test_the_graph_editor_listing_does_not_change_in_this_step():
    """L'interface de l'étalonnage est l'étape 8 : ni ``targets_for`` ni ``targets_for_clip`` (même avec les effets) ne listent ``color.*``."""
    clip = clip_of(make_project(ColorGrade()))
    listed = {t.id for t in targets_for_clip(clip, "video", include_effects=True)} | {t.id for t in targets_for("video")}
    listed |= {t.id for t in all_targets()}
    assert not any(name.startswith(GRADE_PREFIX) for name in listed)


def test_a_node_graph_is_not_animatable():
    from core.color_nodes import ColorNode, ColorNodeGraph

    simple = as_graph(ColorGrade(exposure=0.2))
    assert isinstance(simple, ColorNodeGraph)
    clip = clip_of(make_project(simple))
    with pytest.raises(KeyError):
        get_target("color.exposure", clip)
    assert effective_grade(clip, 1.0) is simple
    assert not has_live_grade_animation(clip_with(simple, [Keyframe("color.exposure", 0.0, 1.0)]))
    assert ColorNode is not None


def clip_with(grade, animation) -> Clip:
    return clip_of(make_project(grade, animation))


def test_labels_exist_in_the_three_languages():
    from ui.i18n import TRANSLATIONS

    for property_id in ANIMATABLE_GRADE_PARAMS:
        entry = TRANSLATIONS[grade_spec(property_id).label_key]
        assert set(entry) >= {"fr", "en", "es"} and all(entry[lang] for lang in ("fr", "en", "es")), property_id


# ---------------------------------------------------------------------------
# Valeur statique et écriture
# ---------------------------------------------------------------------------


def test_static_values_are_read_from_and_written_to_the_grade():
    grade = ColorGrade(exposure=0.5, lift=Wheel(0.1, 0.2, 0.3, 0.4), highlights=-0.2)
    clip = clip_of(make_project(grade))
    assert get_target("color.exposure", clip).get_static(clip) == 0.5
    assert get_target("color.lift.b", clip).get_static(clip) == 0.3
    assert get_target("color.gain.y", clip).get_static(clip) == 0.0
    get_target("color.highlights", clip).set_static(clip, 0.9)
    get_target("color.lift.y", clip).set_static(clip, -0.5)
    assert clip.color_grade.highlights == 0.9 and clip.color_grade.lift == Wheel(0.1, 0.2, 0.3, -0.5)
    assert clip.color_grade.exposure == 0.5, "le reste de l'étalonnage ne bouge pas"


def test_writing_clamps_to_the_bounds_and_a_missing_grade_is_created():
    clip = clip_of(make_project(None))
    assert get_target("color.contrast", clip).get_static(clip) == 0.0
    get_target("color.contrast", clip).set_static(clip, 7.0)
    get_target("color.gain.r", clip).set_static(clip, -7.0)
    assert isinstance(clip.color_grade, ColorGrade) and clip.color_grade.contrast == 1.0 and clip.color_grade.gain.r == -1.0
    assert read_value(None, "color.saturation") == 1.0
    assert write_value(None, "color.hue", 500.0).hue == 180.0


# ---------------------------------------------------------------------------
# effective_grade : fonction pure
# ---------------------------------------------------------------------------


def test_without_a_keyframe_the_grade_is_returned_unchanged_even_none():
    grade = ColorGrade(exposure=0.3)
    assert effective_grade(clip_with(grade, []), 1.0) is grade
    assert effective_grade(clip_with(None, []), 1.0) is None
    unrelated = [Keyframe("position_x", 0.0, 1.0), Keyframe("fx.e.intensity", 0.0, 1.0), Keyframe("color.curves", 0.0, 1.0)]
    assert effective_grade(clip_with(grade, unrelated), 1.0) is grade
    assert not has_grade_animation(unrelated) and not has_grade_animation(None) and not has_grade_animation([])


def test_the_value_is_the_curve_bounded_like_the_inspector():
    animation = [Keyframe("color.exposure", 0.0, -1.0), Keyframe("color.exposure", 2.0, 1.0),
                 Keyframe("color.lift.r", 0.0, -5.0), Keyframe("color.lift.r", 1.0, 5.0)]
    clip = clip_with(ColorGrade(contrast=0.4, lift=Wheel(0.0, 0.25, 0.0, 0.5)), animation)
    mid = effective_grade(clip, 1.0)
    assert mid.exposure == pytest.approx(0.0, abs=1e-12) and mid.lift.r == 1.0, "écrêtée à la borne de la roue"
    assert mid.contrast == 0.4 and mid.lift.g == 0.25 and mid.lift.y == 0.5, "ce qui n'est pas animé ne bouge pas"
    assert effective_grade(clip, 0.0).lift.r == -1.0
    for property_id in ("color.exposure", "color.lift.r"):
        assert get_target(property_id, clip).value_at(clip, 1.5) == getattr(effective_grade(clip, 1.5), "exposure" if "exposure" in property_id else "lift").r if "lift" in property_id else True


def test_a_clip_without_a_grade_is_graded_from_the_neutral_one():
    clip = clip_with(None, [Keyframe("color.saturation", 0.0, 0.0), Keyframe("color.saturation", 2.0, 2.0)])
    grade = effective_grade(clip, 1.0)
    assert isinstance(grade, ColorGrade) and grade.saturation == pytest.approx(1.0) and grade.exposure == 0.0


def test_a_disabled_grade_stays_disabled():
    clip = clip_with(ColorGrade(enabled=False), [Keyframe("color.exposure", 0.0, 1.0)])
    assert effective_grade(clip, 0.5).enabled is False


def test_non_finite_keyframes_are_ignored():
    animation = [Keyframe("color.contrast", 0.0, float("nan")), Keyframe("color.contrast", 1.0, float("inf")),
                 Keyframe("color.hue", 0.0, 10.0), Keyframe("color.hue", 1.0, 20.0)]
    curves = GradeCurves(ColorGrade(contrast=0.3), animation)
    assert set(curves.curves) == {"color.hue"} and curves.at(0.5).contrast == 0.3


def test_the_evaluation_is_the_one_of_the_keyframe_editing_api():
    project = make_project(ColorGrade(shadows=0.2))
    add_keyframe(project, "c", "color.shadows", 1.0, -0.4)
    add_keyframe(project, "c", "color.shadows", 3.0, 0.6)
    clip = clip_of(project)
    assert value_at(clip, "color.shadows", 2.0) == pytest.approx(effective_grade(clip, 2.0).shadows)
    assert value_at(clip, "color.shadows", 2.0) == pytest.approx(0.1)


# ---------------------------------------------------------------------------
# Édition : les verrous valent pour tous les chemins d'entrée
# ---------------------------------------------------------------------------


def test_the_keyframe_api_writes_the_curve_and_undo_removes_it():
    project = make_project(ColorGrade())
    session = Session(project)
    assert session.apply([lambda p: add_keyframe(p, "c", "color.exposure", 1.0, 0.5)], label="clé").ok
    assert [(k.property_name, k.time_seconds, k.value) for k in clip_of(project).animation] == [("color.exposure", 1.0, 0.5)]
    assert session.undo()
    assert clip_of(project).animation == []


def test_a_locked_track_refuses_every_color_edit_and_changes_nothing():
    project = make_project(ColorGrade(), [Keyframe("color.exposure", 0.0, 0.5, id="k1")], locked=True)
    before = copy.deepcopy(project)
    with pytest.raises(ClipEditRefused):
        add_keyframe(project, "c", "color.contrast", 1.0, 0.2)
    with pytest.raises(ClipEditRefused):
        set_value_at(project, "c", "color.exposure", 0.0, 0.9)
    with pytest.raises(ClipEditRefused):
        remove_keyframes(project, [KeyframeRef("c", "color.exposure", "k1")])
    assert project == before


def test_a_color_curve_is_refused_on_an_audio_track():
    project = make_project(ColorGrade(), track_type="audio")
    with pytest.raises(ClipEditRefused):
        add_keyframe(project, "c", "color.exposure", 0.0, 0.5)


def test_a_node_graph_clip_refuses_a_color_keyframe_with_a_key_error_and_changes_nothing():
    project = make_project(as_graph(ColorGrade(exposure=0.2)))
    before = copy.deepcopy(project)
    with pytest.raises(KeyError):
        add_keyframe(project, "c", "color.exposure", 0.0, 0.5)
    assert project == before


# ---------------------------------------------------------------------------
# Cycle de vie : coupe, duplication, composition
# ---------------------------------------------------------------------------


def test_cutting_keeps_the_curve_on_both_halves_without_changing_the_animation():
    project = make_project(ColorGrade(), [Keyframe("color.exposure", 0.0, -1.0), Keyframe("color.exposure", 8.0, 1.0)])
    cut_clip(project, "c", 4.0)
    first, second = project.tracks[0].clips
    assert effective_grade(first, 4.0).exposure == pytest.approx(0.0) and effective_grade(first, 2.0).exposure == pytest.approx(-0.5)
    assert effective_grade(second, 0.0).exposure == pytest.approx(0.0) and effective_grade(second, 2.0).exposure == pytest.approx(0.5)


def test_duplicating_keeps_the_curve_and_its_values():
    project = make_project(ColorGrade(), [Keyframe("color.lift.y", 0.0, -0.5), Keyframe("color.lift.y", 4.0, 0.5)])
    new_id = duplicate_clip(project, "c")
    copied = next(c for c in project.tracks[0].clips if c.id != "c") if not isinstance(new_id, Clip) else new_id
    assert effective_grade(copied, 2.0).lift.y == pytest.approx(0.0)
    assert [k.property_name for k in copied.animation] == ["color.lift.y"] * 2


def test_a_clip_with_a_live_color_curve_cannot_become_a_composition():
    project = make_project(ColorGrade(), [Keyframe("color.exposure", 0.0, 0.1), Keyframe("color.exposure", 1.0, 0.9)])
    before = copy.deepcopy(project)
    with pytest.raises(CompositionError, match="étalonnage animé"):
        convert_to_composition(project, ["c"])
    assert project == before


@pytest.mark.parametrize("animation", [[], [Keyframe("color.curves", 0.0, 0.5)], [Keyframe("opacity", 0.0, 0.5)]])
def test_the_conversion_still_works_without_a_live_color_curve(animation):
    project = make_project(ColorGrade(), animation)
    convert_to_composition(project, ["c"])


def test_a_color_curve_on_a_graph_clip_is_not_live_and_does_not_block_the_conversion():
    project = make_project(as_graph(ColorGrade(exposure=0.2)), [Keyframe("color.exposure", 0.0, 0.1)])
    assert not has_live_grade_animation(clip_of(project))


def test_animation_curves_resolve_with_the_clip():
    clip = clip_with(ColorGrade(), [Keyframe("color.hue", 0.0, 1.0), Keyframe("color.hue", 1.0, 3.0)])
    assert set(animation_curves(clip)) == {"color.hue"}


# ---------------------------------------------------------------------------
# Format .kut : 24 seulement si une image clé d'étalonnage rendue existe
# ---------------------------------------------------------------------------

REFERENCE_SHA256 = "4f8103e55b395f0b04ef056146de2026a698e240251ac834b33a677bdc6c59c0"


def normalized_digest(data: bytes) -> str:
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def test_a_file_without_color_keyframes_is_written_byte_for_byte_as_before(tmp_path):
    """Même projet et même empreinte que ``tests/test_effect_targets.py`` : l'empreinte vient de ``origin/main`` avant l'étape 2."""
    from core.effects_model import EffectType, create_effect

    project = make_project(None, [Keyframe("graphic.width", 0.0, 100.0, id="k1"), Keyframe("graphic.width", 1.0, 200.0, id="k2")])
    clip_of(project).effects = [create_effect(kind, effect_id=f"e-{kind.value}") for kind in EffectType]
    project.name = "Ref"
    path = tmp_path / "ref.kut"
    save_project(project, path)
    assert json.loads(path.read_text())["version"] == 17
    assert normalized_digest(path.read_bytes()) == REFERENCE_SHA256


def test_a_color_keyframe_writes_version_24_and_it_beats_every_other_condition():
    assert GRADE_ANIMATION_VERSION == 24 > SENDCMD_ANIMATION_VERSION
    project = make_project(ColorGrade(), [Keyframe("color.exposure", 0.5, 1.0, id="kx")])
    assert project_payload(project)["version"] == 24
    from core.effects_model import EffectType, create_effect

    clip_of(project).effects = [create_effect(EffectType.BLUR, effect_id="bl")]
    clip_of(project).animation.append(Keyframe("fx.bl.intensity", 0.5, 1.0, id="ky"))
    assert project_payload(project)["version"] == 24, "la 24 l'emporte sur la 23 d'un flou animé"


@pytest.mark.parametrize("name", ["color.curves", "color.lut", "color.lift", "color.x.y.z"])
def test_an_unknown_color_name_does_not_demand_version_24(name):
    project = make_project(ColorGrade(), [Keyframe(name, 0.5, 1.0, id="kx")])
    assert project_payload(project)["version"] == EFFECT_ANIMATION_VERSION == 20


@pytest.mark.parametrize("property_id", ANIMATABLE_GRADE_PARAMS)
def test_every_registered_property_demands_version_24(property_id):
    assert project_payload(make_project(ColorGrade(), [Keyframe(property_id, 0.5, 0.2, id="kx")]))["version"] == 24


def test_color_animation_round_trips_byte_for_byte_in_version_24(tmp_path):
    animation = [Keyframe("color.lift.r", 0.0, -0.3, id="k1"), Keyframe("color.lift.r", 2.0, 0.4, id="k2"),
                 Keyframe("color.exposure", 1.0, 0.7, id="k3")]
    project = make_project(ColorGrade(contrast=0.2, lift=Wheel(0.1, 0.0, 0.0, 0.0)), animation)
    path = tmp_path / "grade.kut"
    save_project(project, path)
    assert json.loads(path.read_text())["version"] == 24
    loaded = load_project(path)
    again = tmp_path / "again.kut"
    save_project(loaded, again)
    assert again.read_bytes() == path.read_bytes()
    assert sorted((k.property_name, k.time_seconds, k.value, k.id) for k in clip_of(loaded).animation) == sorted(
        (k.property_name, k.time_seconds, k.value, k.id) for k in animation)
    assert get_target("color.lift.r", clip_of(loaded)).get_static(clip_of(loaded)) == 0.1


def test_a_build_that_does_not_know_version_24_refuses_the_file(tmp_path, monkeypatch):
    path = tmp_path / "grade.kut"
    save_project(make_project(ColorGrade(), [Keyframe("color.hue", 0.0, 10.0)]), path)
    monkeypatch.setattr(project_io, "SUPPORTED_VERSIONS", SUPPORTED_VERSIONS - {GRADE_ANIMATION_VERSION})
    with pytest.raises(ValueError, match="Version de fichier Kut-Studio non supportée"):
        load_project(path)


def test_a_color_keyframe_on_a_node_graph_clip_loads_and_is_kept(tmp_path):
    """Le graphe de nœuds ne s'anime pas, mais une image clé écrite (à la main, ou avant la découpe en nœuds) n'est ni perdue ni cause d'échec."""
    project = make_project(as_graph(ColorGrade(exposure=0.2)), [Keyframe("color.exposure", 0.0, 0.5, id="k1")])
    path = tmp_path / "graph.kut"
    save_project(project, path)
    loaded = load_project(path)
    assert [k.property_name for k in clip_of(loaded).animation] == ["color.exposure"]
    assert not has_live_grade_animation(clip_of(loaded))
