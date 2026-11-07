"""Étape 8 de l'ADR-0008, côté modèle : état des losanges, bascule, saisie, suppression de l'animation d'étalonnage.

L'interface (inspecteur, page Couleur, historique) : ``tests/test_color_keyframes_ui.py`` et ``tests/test_color_wheel_keyframes_ui.py``.
Ici : fonctions pures de ``core.grade_keyframing`` sur un vrai ``Project`` (aucune fenêtre).
"""

from __future__ import annotations

import pytest

from core.animation import InterpolationType, Keyframe
from core.color_grading import ColorGrade, Wheel
from core.color_nodes import ColorNode, ColorNodeGraph
from core.compositing_guard import ClipEditRefused
from core.grade_keyframing import (
    ADDED,
    REMOVED,
    clear_grade_animation,
    grade_animation_info,
    grade_keyframes,
    key_state,
    live_grade_ids,
    scalar_ids,
    set_grade_keys,
    toggle_grade_keys,
    wheel_property_ids,
)
from core.grade_targets import ANIMATABLE_GRADE_PARAMS, WHEEL_CHANNELS, effective_grade
from core.project_model import Clip, MediaAsset, Project, Track
from core.timeline_view_model import build_clip_views


def _project(*, locked: bool = False, track_type: str = "video") -> tuple[Project, Clip]:
    project = Project(name="G", width=1280, height=720, fps=25.0)
    project.media_assets.append(MediaAsset(id="a1", path="/x.mp4", name="x", media_type="video", duration=10.0,
                                           width=640, height=360, fps=25.0))
    track = Track(id="t1", name="T1", type=track_type, locked=locked)
    clip = Clip(id="c1", asset_id="a1", track_id="t1", timeline_start=0.0, source_in=0.0, source_out=4.0)
    track.clips.append(clip)
    project.tracks.append(track)
    return project, clip


def _kf(prop: str, time: float, value: float) -> Keyframe:
    return Keyframe(prop, time, value, InterpolationType.LINEAR)


def _times(clip: Clip, prop: str) -> list[float]:
    return sorted(k.time_seconds for k in clip.animation if k.property_name == prop)


# ---------------------------------------------------------------- état d'un losange


def test_scalar_ids_and_wheel_ids_cover_the_twenty_three_registered_properties():
    wheels = [pid for name in ("lift", "gamma", "gain", "offset") for pid in wheel_property_ids(name)]
    assert sorted((*scalar_ids(), *wheels)) == sorted(ANIMATABLE_GRADE_PARAMS) and len(wheels) == 16 and len(scalar_ids()) == 7
    assert wheel_property_ids("lift") == tuple(f"color.lift.{c}" for c in WHEEL_CHANNELS)
    with pytest.raises(KeyError):
        wheel_property_ids("sepia")


def test_key_state_is_filled_outlined_or_empty_with_neighbours():
    frames = [_kf("color.exposure", 1.0, 0.1), _kf("color.exposure", 3.0, 0.5), _kf("color.hue", 2.0, 5.0)]
    ids = ["color.exposure"]
    assert key_state(frames, ids, 1.0).keyed_all and key_state(frames, ids, 1.0).animated
    between = key_state(frames, ids, 2.0)
    assert not between.keyed_any and between.animated and between.previous and between.next          # « entre-deux »
    assert not key_state(frames, ids, 0.5).previous and key_state(frames, ids, 0.5).next
    assert not key_state(frames, ids, 3.0).next and key_state(frames, ids, 3.0).previous
    assert not key_state([], ids, 1.0).animated
    assert key_state(frames, ["color.hue"], 2.0004).keyed_all, "la tolérance des losanges de l'inspecteur (1 ms)"
    assert not key_state(frames, ["color.hue"], 2.01).keyed_any


def test_a_wheel_is_filled_only_when_its_four_channels_are_keyed_under_the_playhead():
    ids = wheel_property_ids("gain")
    frames = [_kf(pid, 1.0, 0.2) for pid in ids[:3]]                    # trois canaux sur quatre
    state = key_state(frames, ids, 1.0)
    assert state.keyed_any and not state.keyed_all and state.animated
    assert key_state(frames + [_kf(ids[3], 1.0, 0.1)], ids, 1.0).keyed_all


# ---------------------------------------------------------------- bascule


def test_toggle_adds_one_key_per_property_keeping_the_current_value_then_removes_them():
    project, clip = _project()
    clip.color_grade = ColorGrade(exposure=0.4, gain=Wheel(0.1, 0.0, -0.1, 0.2))
    assert toggle_grade_keys(project, "c1", ["color.exposure"], 1.0) == ADDED
    assert [(k.property_name, k.time_seconds, k.value) for k in clip.animation] == [("color.exposure", 1.0, 0.4)]
    assert toggle_grade_keys(project, "c1", wheel_property_ids("gain"), 2.0) == ADDED
    gains = {k.property_name: k.value for k in clip.animation if k.time_seconds == 2.0}
    assert gains == {"color.gain.r": 0.1, "color.gain.g": 0.0, "color.gain.b": -0.1, "color.gain.y": 0.2}
    assert toggle_grade_keys(project, "c1", wheel_property_ids("gain"), 2.0004) == REMOVED      # sous la tête (1 ms)
    assert [k.property_name for k in clip.animation] == ["color.exposure"]
    assert toggle_grade_keys(project, "c1", ["color.exposure"], 1.0) == REMOVED
    assert not clip.animation and clip.color_grade.exposure == 0.4, "la valeur montrée reste la valeur du réglage"


def test_toggle_on_a_never_graded_clip_keys_the_neutral_value():
    project, clip = _project()
    assert clip.color_grade is None
    toggle_grade_keys(project, "c1", ["color.saturation"], 0.0)
    assert [(k.property_name, k.value) for k in clip.animation] == [("color.saturation", 1.0)]


def test_toggle_on_an_animated_property_inserts_a_key_that_keeps_the_curve():
    project, clip = _project()
    clip.animation = [_kf("color.exposure", 0.0, -1.0), _kf("color.exposure", 4.0, 1.0)]
    toggle_grade_keys(project, "c1", ["color.exposure"], 2.0)
    assert _times(clip, "color.exposure") == [0.0, 2.0, 4.0]
    assert effective_grade(clip, 2.0).exposure == pytest.approx(0.0)
    assert effective_grade(clip, 1.0).exposure == pytest.approx(-0.5)


def test_toggle_refuses_before_writing_when_the_clip_cannot_be_edited():
    for kwargs, error in (({"locked": True}, ClipEditRefused), ({"track_type": "audio"}, ClipEditRefused)):
        project, clip = _project(**kwargs)
        with pytest.raises(error):
            toggle_grade_keys(project, "c1", ["color.exposure"], 1.0)
        assert not clip.animation
    project, clip = _project()
    for ids, error in ((["color.curves"], KeyError), (["fx.a.b"], KeyError)):
        with pytest.raises(error):
            toggle_grade_keys(project, "c1", ids, 1.0)
    with pytest.raises(ValueError):
        toggle_grade_keys(project, "c1", ["color.exposure", "color.hue"], 99.0)           # hors du clip
    assert not clip.animation, "rien d'écrit sur un refus"


def test_a_node_graph_cannot_be_toggled_and_keeps_what_it_has():
    project, clip = _project()
    clip.color_grade = ColorNodeGraph.serial((ColorNode("n1", ColorGrade()), ColorNode("n2", ColorGrade())))
    clip.animation = [_kf("color.exposure", 1.0, 0.5)]
    with pytest.raises(KeyError):
        toggle_grade_keys(project, "c1", ["color.exposure"], 2.0)
    with pytest.raises(KeyError):
        set_grade_keys(project, "c1", {"color.hue": 3.0}, 2.0)
    assert _times(clip, "color.exposure") == [1.0] and len(clip.animation) == 1
    assert live_grade_ids(clip, ["color.exposure"]) == [], "ignorée au rendu : elle n'est pas « vivante »"
    assert [k.property_name for k in grade_keyframes(clip)] == ["color.exposure"]


# ---------------------------------------------------------------- saisie


def test_set_keys_writes_clamps_and_updates_in_place_without_a_double():
    project, clip = _project()
    assert set_grade_keys(project, "c1", {"color.exposure": 9.0, "color.lift.y": -0.3}, 1.0) == 2
    assert {k.property_name: k.value for k in clip.animation} == {"color.exposure": 2.0, "color.lift.y": -0.3}   # EXPOSURE_MAX
    set_grade_keys(project, "c1", {"color.exposure": 0.7}, 1.0004)
    assert _times(clip, "color.exposure") == [1.0], "mise à jour à son propre instant, pas de doublon à 0,4 ms"
    assert [k.value for k in clip.animation if k.property_name == "color.exposure"] == [0.7]


def test_set_keys_refuses_a_non_finite_value_before_writing_anything():
    project, clip = _project()
    for bad in (float("nan"), float("inf")):
        with pytest.raises(ValueError):
            set_grade_keys(project, "c1", {"color.hue": 4.0, "color.exposure": bad}, 1.0)
    assert not clip.animation


def test_set_keys_respects_a_locked_track():
    project, clip = _project(locked=True)
    with pytest.raises(ClipEditRefused):
        set_grade_keys(project, "c1", {"color.hue": 4.0}, 1.0)
    assert not clip.animation


# ---------------------------------------------------------------- suppression


def test_clear_keeps_the_value_shown_at_the_playhead_or_leaves_the_base():
    project, clip = _project()
    clip.color_grade = ColorGrade(saturation=1.5)
    clip.animation = [_kf("color.exposure", 0.0, 0.0), _kf("color.exposure", 4.0, 1.0), _kf("color.lift.r", 1.0, 0.3)]
    assert clear_grade_animation(project, "c1", 2.0) == 2
    assert not clip.animation
    assert clip.color_grade.exposure == pytest.approx(0.5) and clip.color_grade.lift.r == pytest.approx(0.3)
    assert clip.color_grade.saturation == 1.5, "ce qui n'était pas animé ne bouge pas"
    clip.animation = [_kf("color.exposure", 0.0, 0.0), _kf("color.exposure", 4.0, 1.0)]
    clip.color_grade = ColorGrade()
    assert clear_grade_animation(project, "c1") == 1 and clip.color_grade.exposure == 0.0, "sans temps : la base n'est pas touchée"


def test_clear_leaves_other_animation_alone_and_drops_ignored_keys_of_a_node_graph():
    project, clip = _project()
    other = Keyframe("fx.e1.intensity", 1.0, 0.5, InterpolationType.LINEAR)
    clip.animation = [_kf("color.hue", 1.0, 3.0), other, _kf("color.curves", 1.0, 1.0)]
    assert clear_grade_animation(project, "c1", 1.0) == 1
    assert clip.animation == [other, _kf("color.curves", 1.0, 1.0)], "un nom inconnu du registre n'est pas touché"
    clip.color_grade = ColorNodeGraph.serial((ColorNode("n1", ColorGrade()), ColorNode("n2", ColorGrade())))
    clip.animation = [_kf("color.hue", 1.0, 3.0)]
    assert clear_grade_animation(project, "c1", 1.0) == 1 and not clip.animation
    assert clear_grade_animation(project, "c1", 1.0) == 0


def test_clear_respects_a_locked_track():
    project, clip = _project(locked=True)
    clip.animation = [_kf("color.hue", 1.0, 3.0)]
    with pytest.raises(ClipEditRefused):
        clear_grade_animation(project, "c1", 1.0)
    assert len(clip.animation) == 1


# ---------------------------------------------------------------- ce que la page Couleur montre


def test_animation_info_gives_the_evaluated_wheels_and_the_state_of_each_diamond():
    _project_, clip = _project()
    clip.color_grade = ColorGrade(gamma=Wheel(0.0, 0.0, 0.0, 0.5))
    clip.animation = [_kf("color.lift.y", 0.0, 0.0), _kf("color.lift.y", 4.0, 0.4)]
    info = grade_animation_info(clip, 2.0)
    assert info.animatable and info.has_keys and info.key_count == 2
    assert info.shown["lift"].y == pytest.approx(0.2) and info.shown["gamma"].y == 0.5
    assert info.wheels["lift"].animated and not info.wheels["lift"].keyed_any and not info.wheels["gamma"].animated
    clip.animation = []
    assert not grade_animation_info(clip, 2.0).shown and not grade_animation_info(clip, 2.0).has_keys


def test_animation_info_of_a_node_graph_says_not_animatable_and_counts_the_ignored_keys():
    _project_, clip = _project()
    clip.color_grade = ColorNodeGraph.serial((ColorNode("n1", ColorGrade()), ColorNode("n2", ColorGrade())))
    clip.animation = [_kf("color.hue", 1.0, 3.0), _kf("color.hue", 2.0, 4.0)]
    info = grade_animation_info(clip, 1.0)
    assert not info.animatable and info.key_count == 2 and not info.wheels and not info.shown


def test_the_timeline_view_carries_the_grade_keyframes_and_only_those():
    project, clip = _project()
    other = Keyframe("fx.e1.intensity", 1.0, 0.5, InterpolationType.LINEAR)
    clip.animation = [_kf("color.exposure", 1.0, 0.2), other, _kf("color.curves", 1.0, 1.0)]
    (view,) = [v for v in build_clip_views(project) if v.id == "c1"]
    assert [k.property_name for k in view.grade_keyframes] == ["color.exposure"]
    assert view.effect_keyframes == (other,)
