"""Opérations du temps d'un clip : points de vitesse, politiques de ripple, interpolation, audio.

Un point de vitesse est un keyframe ``time.speed`` ordinaire ; ce qui est propre au temps — durée dérivée donc ripple,
édition transactionnelle — est vérifié ici sur les exemples du cahier des charges.
"""

from __future__ import annotations

import pytest

from core.animation import InterpolationType
from core.project_model import Clip, MediaAsset, Project, Track
from core.time_map import SPEED_PROPERTY, PiecewiseTimeMap
from core.time_ops import (
    MIN_CLIP_SECONDS,
    RippleMode,
    add_speed_point,
    clear_speed_curve,
    move_speed_point,
    remove_speed_point,
    set_clip_interpolation,
    set_clip_preserve_pitch,
    set_clip_remap_audio,
    set_speed_point_interpolation,
    set_speed_point_value,
    speed_points,
)
from core.time_remapping import FlowQuality, TimeInterpolation, TimeRemapping
from core.timeline_operations import set_clip_freeze_frame, set_clip_speed


def _project(*, source=(10.0, 40.0), media=300.0, remapping=None):
    asset = MediaAsset(id="a", path="/tmp/x.mp4", name="x", duration=media, width=1920, height=1080,
                       fps=25.0, media_type="video", has_audio=True)
    clip = Clip(id="c1", asset_id="a", track_id="V1", timeline_start=3.0, source_in=source[0], source_out=source[1],
                time_remapping=remapping or TimeRemapping())
    project = Project(name="p", width=1280, height=720, fps=25.0, media_assets=[asset],
                      tracks=[Track(id="V1", name="V1", type="video", clips=[clip])])
    return project, clip


def _brief_ramp(project, **kwargs):
    for t, v in ((0.0, 1.0), (2.0, 1.0), (3.0, 0.25), (6.0, 0.25), (7.0, 2.0)):
        add_speed_point(project, "c1", t, v, **kwargs)


def _state(clip: Clip):
    return (list(clip.animation), clip.time_remapping, clip.source_in, clip.source_out, clip.timeline_start)


# ---------------------------------------------------------------------------
# Rampe du cahier des charges
# ---------------------------------------------------------------------------


def test_the_speed_ramp_of_the_brief_is_built_from_five_points():
    """0 s 100 % · 2 s 100 % · 3 s 25 % · 6 s 25 % · 7 s 200 % : le temps source se calcule à la main."""
    project, clip = _project(source=(10.0, 60.0))
    _brief_ramp(project)
    assert [(k.time_seconds, k.value) for k in speed_points(clip)] == [
        (0.0, 1.0), (2.0, 1.0), (3.0, 0.25), (6.0, 0.25), (7.0, 2.0)
    ]
    tm = clip.time_map
    assert isinstance(tm, PiecewiseTimeMap)
    assert tm.source_time(2.0) == pytest.approx(10.0 + 2.0, abs=1e-9)
    assert tm.source_time(7.0) == pytest.approx(10.0 + 4.5, abs=1e-9)               # 2 + 0,625 + 0,75 + 1,125
    assert clip.duration == tm.duration > 7.0                                      # puis 200 % jusqu'à épuiser la source
    assert tm.source_time(clip.duration) == pytest.approx(60.0, abs=1e-9)


def test_the_first_point_given_a_value_leaves_the_part_before_it_at_the_current_speed():
    project, clip = _project(source=(10.0, 60.0))
    add_speed_point(project, "c1", 3.0, 0.25)                                       # poser « 25 % à 3 s » sur un clip constant
    assert [(k.time_seconds, k.value) for k in speed_points(clip)] == [(0.0, 1.0), (3.0, 0.25)]
    assert clip.time_map.source_time(1.5) == pytest.approx(11.5, abs=1e-9)         # avant le point : toujours 100 %
    assert clip.time_map.speed_at(2.999) == pytest.approx(1.0) and clip.time_map.speed_at(3.001) == pytest.approx(0.25)


def test_adding_a_point_without_a_value_never_changes_the_mapping():
    project, clip = _project(source=(10.0, 60.0))
    add_speed_point(project, "c1", 0.0, 1.0, interpolation=InterpolationType.BEZIER)
    add_speed_point(project, "c1", 4.0, 0.3, interpolation=InterpolationType.BEZIER)
    before = clip.time_map
    probes = [0.4, 1.9, 2.6, 3.7, 5.5]
    reference = [before.source_time(t) for t in probes]
    add_speed_point(project, "c1", 2.2)                                             # insertion qui conserve la courbe
    assert len(speed_points(clip)) == 3
    assert [clip.time_map.source_time(t) for t in probes] == pytest.approx(reference, abs=1e-9)


def test_a_constant_clip_given_a_single_point_keeps_its_duration():
    project, clip = _project(source=(10.0, 40.0), remapping=TimeRemapping(speed=2.0))
    duration = clip.duration
    add_speed_point(project, "c1", 4.0)                                             # amorce une courbe à la vitesse actuelle
    assert clip.has_speed_curve and clip.duration == pytest.approx(duration, abs=1e-9)


# ---------------------------------------------------------------------------
# Ripple : source conservée ou durée de timeline conservée
# ---------------------------------------------------------------------------


def test_slowing_down_lengthens_the_clip_when_the_source_range_is_kept():
    project, clip = _project(source=(10.0, 60.0))
    _brief_ramp(project)
    before_duration, window = clip.duration, (clip.source_in, clip.source_out)
    slowest = next(k for k in speed_points(clip) if k.time_seconds == 3.0)
    set_speed_point_value(project, "c1", slowest.id, 0.1)
    assert clip.duration > before_duration                                          # ralentir allonge le clip
    assert (clip.source_in, clip.source_out) == window                              # la portion de source ne bouge pas


def test_the_timeline_duration_can_be_kept_instead_and_the_source_used_changes():
    project, clip = _project(source=(10.0, 60.0))
    _brief_ramp(project)
    duration = clip.duration
    out_before = clip.source_out
    slowest = next(k for k in speed_points(clip) if k.time_seconds == 3.0)
    set_speed_point_value(project, "c1", slowest.id, 0.1, mode=RippleMode.TIMELINE)
    assert clip.duration == pytest.approx(duration, abs=1e-9)                       # même durée sur la timeline
    assert clip.source_out < out_before                                             # le clip consomme moins de média


def test_keeping_the_timeline_duration_cannot_use_more_media_than_exists():
    project, clip = _project(source=(10.0, 60.0), media=62.0)                        # presque pas de média après la fin
    _brief_ramp(project)
    duration = clip.duration
    fastest = next(k for k in speed_points(clip) if k.time_seconds == 7.0)
    set_speed_point_value(project, "c1", fastest.id, 10.0, mode=RippleMode.TIMELINE)
    assert clip.source_out <= 62.0 + 1e-9                                           # jamais au-delà du média
    assert clip.duration <= duration + 1e-9


# ---------------------------------------------------------------------------
# Déplacer, supprimer, interpolation
# ---------------------------------------------------------------------------


def test_moving_a_point_in_time_moves_the_ramp_and_changes_the_duration():
    project, clip = _project(source=(10.0, 60.0))
    _brief_ramp(project)
    duration = clip.duration
    point = next(k for k in speed_points(clip) if k.time_seconds == 3.0)
    new_time = move_speed_point(project, "c1", point.id, 4.0)
    assert new_time == pytest.approx(4.0)
    assert clip.time_map.speed_at(3.5) != pytest.approx(0.25)                       # la rampe n'est plus là
    assert clip.duration != pytest.approx(duration)


def test_removing_the_last_point_leaves_a_constant_clip_that_looks_the_same_at_that_instant():
    project, clip = _project(source=(10.0, 60.0))
    add_speed_point(project, "c1", 0.0)
    only = speed_points(clip)[0]
    shown = clip.time_map.source_time(5.0)
    remove_speed_point(project, "c1", only.id)
    assert not clip.has_speed_curve and clip.time_remapping.speed == 1.0
    assert clip.time_map.source_time(5.0) == pytest.approx(shown, abs=1e-9)


def test_the_interpolation_of_a_ramp_segment_can_be_changed():
    project, clip = _project(source=(10.0, 60.0))
    _brief_ramp(project)
    ramp_start = next(k for k in speed_points(clip) if k.time_seconds == 2.0)
    mid_linear = clip.time_map.speed_at(2.5)
    assert set_speed_point_interpolation(project, "c1", [ramp_start.id], InterpolationType.HOLD) == 1
    assert clip.time_map.speed_at(2.5) == pytest.approx(1.0)                        # brutal : reste à 100 % jusqu'au point suivant
    assert mid_linear == pytest.approx(0.625)


def test_clearing_the_curve_gives_a_constant_clip_over_the_media_that_was_shown():
    project, clip = _project(source=(10.0, 60.0))
    _brief_ramp(project)
    extent = clip.time_map.extent()
    clear_speed_curve(project, "c1")
    assert not clip.has_speed_curve
    assert (clip.source_in, clip.source_out) == pytest.approx(extent, abs=1e-9)


# ---------------------------------------------------------------------------
# Transactions : jamais de clip à moitié modifié
# ---------------------------------------------------------------------------


def test_a_speed_that_would_empty_the_clip_is_refused_and_everything_is_restored():
    project, clip = _project(source=(10.0, 60.0))
    add_speed_point(project, "c1", 0.0, 1.0)
    before = _state(clip)
    first = speed_points(clip)[0]
    with pytest.raises(ValueError, match="source"):
        set_speed_point_value(project, "c1", first.id, -1.0)                       # recule dès 0 s depuis la borne basse
    assert _state(clip) == before


def test_a_speed_outside_the_limits_is_refused():
    project, clip = _project()
    before = _state(clip)
    with pytest.raises(ValueError, match="entre"):
        add_speed_point(project, "c1", 1.0, 25.0)
    assert _state(clip) == before


def test_the_minimum_duration_is_a_real_floor():
    assert MIN_CLIP_SECONDS > 0.0


def test_speed_points_cannot_be_edited_on_a_locked_track_or_a_frozen_clip():
    project, clip = _project()
    project.tracks[0].locked = True
    with pytest.raises(ValueError, match="verrouill"):
        add_speed_point(project, "c1", 1.0, 2.0)
    project.tracks[0].locked = False
    set_clip_freeze_frame(project, "c1", 20.0, 2.0)
    with pytest.raises(ValueError, match="arrêt sur image"):
        add_speed_point(project, "c1", 1.0, 2.0)


def test_a_point_outside_the_clip_is_refused_without_a_trace():
    project, clip = _project(source=(10.0, 20.0))
    before = _state(clip)
    with pytest.raises(ValueError, match="hors du clip"):
        add_speed_point(project, "c1", 99.0, 2.0)
    assert _state(clip) == before


# ---------------------------------------------------------------------------
# Interpolation des images et audio
# ---------------------------------------------------------------------------


def test_the_interpolation_mode_and_flow_quality_are_clip_choices_that_survive_other_edits():
    project, clip = _project()
    set_clip_interpolation(project, "c1", "optical_flow", "best")
    assert clip.time_remapping.preserve_pitch                                       # défaut : la voix garde sa hauteur (atempo)
    set_clip_preserve_pitch(project, "c1", False)
    set_clip_remap_audio(project, "c1", False)
    set_clip_speed(project, "c1", 0.25)                                             # une autre édition ne les efface pas
    remapping = clip.time_remapping
    assert (remapping.interpolation, remapping.flow_quality) == (TimeInterpolation.OPTICAL_FLOW, FlowQuality.BEST)
    assert not remapping.preserve_pitch and not remapping.remap_audio and remapping.speed == 0.25


def test_a_frozen_clip_has_no_intermediate_images_to_compute():
    project, clip = _project()
    set_clip_freeze_frame(project, "c1", 20.0, 2.0)
    with pytest.raises(ValueError, match="arrêt sur image"):
        set_clip_interpolation(project, "c1", "optical_flow")


def test_an_interpolation_mode_alone_makes_a_clip_time_remapped_even_at_normal_speed():
    project, clip = _project()
    assert not clip.is_time_remapped
    set_clip_interpolation(project, "c1", "blending")
    assert clip.is_time_remapped


def test_the_speed_property_id_is_the_one_the_graph_editor_lists():
    from core.animation_targets import targets_for_clip

    project, clip = _project()
    assert SPEED_PROPERTY in [t.id for t in targets_for_clip(clip, "video")]
