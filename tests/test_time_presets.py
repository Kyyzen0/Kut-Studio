"""Arrêt sur image dans la courbe, préréglages et copier / coller du temps : vérifiés contre le modèle exact (``TimeMap``)."""

from __future__ import annotations

import pytest

from core.animation import InterpolationType
from core.project_model import Clip, MediaAsset, Project, Track
from core.time_ops import HOLD_SECONDS, RippleMode, add_speed_point, insert_hold, speed_points
from core.time_presets import (
    CONSTANT_SPEEDS,
    RAMP_FACTOR,
    RAMP_SECONDS,
    TimePreset,
    apply_preset,
    copy_time,
    paste_time,
)
from core.time_remapping import FlowQuality, FreezeFrameMode, TimeInterpolation, TimeRemapping
from core.timeline_operations import cut_clip, set_clip_freeze_frame

FPS = 30.0


def _project(*, source=(2.0, 12.0), remapping=None, media=60.0):
    asset = MediaAsset(id="a", path="/tmp/x.mp4", name="x", duration=media, width=1920, height=1080, fps=FPS,
                       media_type="video", has_audio=True)
    clip = Clip(id="c1", asset_id="a", track_id="V1", timeline_start=1.0, source_in=source[0], source_out=source[1],
                time_remapping=remapping or TimeRemapping())
    project = Project(name="p", width=1920, height=1080, fps=FPS, media_assets=[asset],
                      tracks=[Track(id="V1", name="V1", type="video", clips=[clip])])
    return project, clip


# ---------------------------------------------------------------------------
# Arrêt sur image dans la courbe
# ---------------------------------------------------------------------------


def test_a_hold_freezes_the_displayed_image_then_resumes_at_the_same_speed():
    project, clip = _project()                                            # 10 s de source à 100 %
    before = clip.duration
    insert_hold(project, "c1", 4.0, 1.5, fps=FPS)
    time_map = clip.time_map
    assert clip.duration == pytest.approx(before + 1.5)                   # le clip s'allonge du palier
    frozen = time_map.source_time(4.0)
    assert frozen == pytest.approx(2.0 + 4.0)                             # l'image de la tête de lecture
    for moment in (4.0, 4.5, 5.0, 5.4):
        assert time_map.source_time(moment) == pytest.approx(frozen)      # rien ne bouge pendant le palier
    assert time_map.speed_at(3.9) == pytest.approx(1.0) and time_map.speed_at(5.6) == pytest.approx(1.0)
    assert time_map.source_time(7.0) == pytest.approx(frozen + 1.5)       # la lecture reprend là où elle s'était arrêtée


def test_the_hold_of_a_clip_whose_speed_is_not_one_resumes_at_that_speed():
    project, clip = _project(remapping=TimeRemapping(speed=2.0))
    insert_hold(project, "c1", 2.0, 1.0, fps=FPS)
    time_map = clip.time_map
    assert time_map.source_time(2.0) == pytest.approx(2.0 + 4.0)
    assert time_map.source_time(2.9) == pytest.approx(time_map.source_time(2.0))
    assert time_map.speed_at(3.5) == pytest.approx(2.0)


def test_a_hold_in_the_middle_of_a_ramp_shifts_the_rest_of_the_curve_without_reshaping_it():
    project, clip = _project(source=(0.0, 40.0))
    add_speed_point(project, "c1", 0.0, 1.0)
    add_speed_point(project, "c1", 4.0, 3.0)
    original = clip.time_map
    insert_hold(project, "c1", 1.0, 1.0, fps=FPS)
    held = clip.time_map
    assert held.source_time(1.0) == pytest.approx(original.source_time(1.0), abs=1e-3)
    for moment in (1.0, 1.5, 2.0):
        assert held.source_time(moment) == pytest.approx(held.source_time(1.0), abs=1e-3)
    for moment in (2.5, 3.0, 4.0):
        assert held.source_time(moment + 1.0) == pytest.approx(original.source_time(moment), abs=0.05)   # décalé d'une seconde


def test_the_hold_point_is_a_plain_speed_point_at_zero_with_the_hold_interpolation():
    project, _clip = _project()
    stop = insert_hold(project, "c1", 3.0, 2.0, fps=FPS)
    assert stop.value == 0.0 and stop.interpolation is InterpolationType.HOLD
    assert stop.time_seconds == pytest.approx(3.0) and stop in speed_points(project.tracks[0].clips[0])


def test_a_hold_at_the_very_start_needs_no_reference_point():
    project, clip = _project()
    insert_hold(project, "c1", 0.0, 1.0, fps=FPS)
    assert clip.time_map.source_time(0.5) == pytest.approx(clip.source_in)
    assert clip.time_map.source_time(1.5) == pytest.approx(clip.source_in + 0.5)


def test_a_hold_in_a_cut_clip_also_lengthens_its_imposed_duration():
    """Un clip coupé garde une durée imposée : elle grandit du palier, sinon la fin serait rognée."""
    project, clip = _project(source=(0.0, 20.0))
    add_speed_point(project, "c1", 0.0, 1.0)
    add_speed_point(project, "c1", 5.0, 2.0)
    _left, right = cut_clip(project, "c1", 1.0 + 3.0)
    before = right.duration
    insert_hold(project, right.id, 1.0, 1.0, fps=FPS)
    assert right.duration == pytest.approx(before + 1.0, abs=1e-3)


@pytest.mark.parametrize("bad", [0.0, -1.0])
def test_a_hold_needs_a_positive_duration(bad):
    project, clip = _project()
    snapshot = (list(clip.animation), clip.time_remapping)
    with pytest.raises(ValueError):
        insert_hold(project, "c1", 1.0, bad)
    assert (list(clip.animation), clip.time_remapping) == snapshot


def test_a_hold_is_refused_outside_the_clip_and_leaves_it_untouched():
    project, clip = _project()
    with pytest.raises(ValueError):
        insert_hold(project, "c1", clip.duration + 5.0, 1.0)
    assert not clip.has_speed_curve


def test_a_hold_in_timeline_mode_keeps_the_duration_and_loses_the_end_of_the_source():
    project, clip = _project(source=(0.0, 20.0))
    before, original_out = clip.duration, clip.source_out
    insert_hold(project, "c1", 4.0, 2.0, fps=FPS, mode=RippleMode.TIMELINE)
    assert clip.duration == pytest.approx(before, abs=1e-3)
    assert clip.time_map.source_time(clip.duration) == pytest.approx(original_out - 2.0, abs=0.05)   # le palier a mangé 2 s de source


def test_a_frozen_clip_has_no_speed_to_hold():
    project, _clip = _project()
    set_clip_freeze_frame(project, "c1", 5.0, 2.0)
    with pytest.raises(ValueError):
        insert_hold(project, "c1", 1.0, 1.0)


# ---------------------------------------------------------------------------
# Préréglages
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("preset", list(CONSTANT_SPEEDS))
def test_constant_presets_set_the_static_speed(preset):
    project, clip = _project()
    apply_preset(project, "c1", preset)
    assert clip.time_remapping.speed == CONSTANT_SPEEDS[preset] and not clip.has_speed_curve


def test_a_constant_preset_replaces_an_existing_curve():
    project, clip = _project()
    add_speed_point(project, "c1", 0.0, 1.0)
    add_speed_point(project, "c1", 3.0, 2.0)
    apply_preset(project, "c1", TimePreset.SLOW_25)
    assert not clip.has_speed_curve and clip.time_remapping.speed == 0.25


def test_ramp_in_starts_slow_and_reaches_the_clip_speed_within_a_second():
    project, clip = _project(remapping=TimeRemapping(speed=2.0))
    apply_preset(project, "c1", TimePreset.RAMP_IN)
    time_map = clip.time_map
    assert time_map.speed_at(0.0) == pytest.approx(2.0 * RAMP_FACTOR)
    assert time_map.speed_at(RAMP_SECONDS + 0.2) == pytest.approx(2.0)
    assert time_map.speed_at(0.5) < time_map.speed_at(0.9) < 2.0           # monte, sans saut
    points = speed_points(clip)
    assert points[0].interpolation is InterpolationType.EASE_IN_OUT


def test_ramp_out_keeps_the_speed_then_falls_during_the_last_second():
    project, clip = _project()
    original = clip.duration
    apply_preset(project, "c1", TimePreset.RAMP_OUT)
    time_map = clip.time_map
    assert time_map.speed_at(1.0) == pytest.approx(1.0)
    assert time_map.speed_at(original - RAMP_SECONDS / 2) < 1.0
    assert time_map.speed_at(original + 0.1) == pytest.approx(RAMP_FACTOR)
    assert clip.duration > original                                         # la fin lente allonge le clip


def test_ramps_on_a_short_clip_never_exceed_half_of_it():
    project, clip = _project(source=(0.0, 1.0))
    apply_preset(project, "c1", TimePreset.RAMP_IN)
    assert speed_points(clip)[1].time_seconds == pytest.approx(0.5)


def test_the_freeze_preset_inserts_a_one_second_hold_at_the_given_time():
    project, clip = _project()
    before = clip.duration
    apply_preset(project, "c1", TimePreset.FREEZE, local_time=3.0, fps=FPS)
    assert clip.duration == pytest.approx(before + HOLD_SECONDS)
    assert clip.time_map.source_time(3.5) == pytest.approx(clip.time_map.source_time(3.0))


def test_an_unknown_preset_is_refused():
    project, _clip = _project()
    with pytest.raises(ValueError):
        apply_preset(project, "c1", "turbo")


def test_presets_are_refused_on_a_frozen_clip():
    project, _clip = _project()
    set_clip_freeze_frame(project, "c1", 5.0, 2.0)
    with pytest.raises(ValueError):
        apply_preset(project, "c1", TimePreset.SLOW_50)


# ---------------------------------------------------------------------------
# Copier / coller
# ---------------------------------------------------------------------------


def _second_clip(project):
    clip = Clip(id="c2", asset_id="a", track_id="V1", timeline_start=40.0, source_in=20.0, source_out=30.0)
    project.tracks[0].clips.append(clip)
    return clip


def test_pasting_the_time_copies_the_curve_the_reverse_and_the_choices_but_keeps_the_source_range():
    project, source = _project(remapping=TimeRemapping(reverse=True, interpolation=TimeInterpolation.OPTICAL_FLOW,
                                                       flow_quality=FlowQuality.BEST, preserve_pitch=False))
    add_speed_point(project, "c1", 0.0, 1.0)
    add_speed_point(project, "c1", 2.0, 0.25)
    target = _second_clip(project)
    snapshot = copy_time(source)
    paste_time(project, "c2", snapshot)
    assert (target.source_in, target.source_out) == (20.0, 30.0)           # sa portion de média ne bouge pas
    assert target.time_remapping.reverse and target.time_remapping.interpolation is TimeInterpolation.OPTICAL_FLOW
    assert target.time_remapping.flow_quality is FlowQuality.BEST and not target.time_remapping.preserve_pitch
    assert [(k.time_seconds, k.value) for k in speed_points(target)] == [(0.0, 1.0), (2.0, 0.25)]
    assert {k.id for k in speed_points(target)}.isdisjoint({k.id for k in speed_points(source)})   # pas de partage d'identité


def test_pasting_replaces_the_curve_of_the_target_and_is_transactional():
    project, source = _project()
    target = _second_clip(project)
    add_speed_point(project, "c2", 0.0, 3.0)
    add_speed_point(project, "c2", 1.0, 1.0)
    snapshot = copy_time(source)                                           # un clip sans courbe, vitesse 1
    paste_time(project, "c2", snapshot)
    assert not target.has_speed_curve and target.time_remapping.speed == 1.0


def test_a_frozen_clip_cannot_be_copied_or_pasted_onto():
    project, source = _project()
    target = _second_clip(project)
    set_clip_freeze_frame(project, "c2", 25.0, 1.0)
    assert target.time_remapping.freeze_mode == FreezeFrameMode.FREEZE
    with pytest.raises(ValueError):
        paste_time(project, "c2", copy_time(source))
    with pytest.raises(ValueError):
        copy_time(target)


# ---------------------------------------------------------------------------
# Plusieurs points de vitesse à la fois (sélection de la timeline)
# ---------------------------------------------------------------------------


def _ramped(project_clip):
    from core.keyframe_editing import KeyframeRef

    project, clip = project_clip
    add_speed_point(project, "c1", 0.0, 1.0)
    add_speed_point(project, "c1", 4.0, 0.5)
    add_speed_point(project, "c1", 8.0, 2.0)
    return project, clip, [KeyframeRef("c1", "time.speed", point.id) for point in speed_points(clip)]


def test_moving_speed_points_together_changes_the_duration_through_the_time_transaction():
    from core.time_ops import move_speed_points

    project, clip, refs = _ramped(_project())
    before = clip.duration
    moved = move_speed_points(project, refs[1:], 1.0, fps=FPS)
    assert [round(k.time_seconds, 3) for k in speed_points(clip)] == [0.0, 5.0, 9.0]
    assert set(moved.values()) == {5.0, 9.0} and clip.duration != before


def test_a_refused_move_leaves_every_clip_untouched():
    from core.time_ops import move_speed_points
    from core.timeline_operations import set_clip_freeze_frame

    project, clip, refs = _ramped(_project())
    other = Clip(id="c2", asset_id="a", track_id="V1", timeline_start=60.0, source_in=0.0, source_out=10.0)
    project.tracks[0].clips.append(other)
    add_speed_point(project, "c2", 1.0, 0.5)
    set_clip_freeze_frame(project, "c2", 5.0, 1.0)                                          # c2 ne peut plus avoir de vitesse
    from core.keyframe_editing import KeyframeRef

    snapshot = (list(clip.animation), clip.duration)
    ghost = KeyframeRef("c2", "time.speed", "nope")
    with pytest.raises(ValueError):
        move_speed_points(project, [*refs[1:], ghost], 1.0, fps=FPS)
    assert (list(clip.animation), clip.duration) == snapshot                                # c1 non plus : tout ou rien


def test_only_speed_points_are_accepted_by_the_speed_operations():
    from core.keyframe_editing import KeyframeRef
    from core.time_ops import move_speed_points, remove_speed_points

    project, clip, _refs = _ramped(_project())
    not_speed = KeyframeRef("c1", "opacity", "x")
    with pytest.raises(ValueError, match="point de vitesse"):
        move_speed_points(project, [not_speed], 1.0)
    with pytest.raises(ValueError, match="point de vitesse"):
        remove_speed_points(project, [not_speed])


def test_removing_the_last_speed_points_restores_the_constant_speed_the_clip_showed():
    from core.time_ops import remove_speed_points

    project, clip, refs = _ramped(_project())
    duration = clip.duration
    assert remove_speed_points(project, refs[1:]) == 2
    assert [k.time_seconds for k in speed_points(clip)] == [0.0] and clip.has_speed_curve
    assert remove_speed_points(project, refs[:1]) == 1
    assert not clip.has_speed_curve and clip.time_remapping.speed == 1.0
    assert clip.source_in == 2.0 and clip.source_out == pytest.approx(clip.source_out)    # la portion de média est conservée
    assert clip.duration > 0 and duration > 0


def test_removing_points_in_timeline_mode_keeps_the_duration():
    from core.time_ops import remove_speed_points

    project, clip, refs = _ramped(_project())
    before = clip.duration
    remove_speed_points(project, refs[1:2], mode=RippleMode.TIMELINE)
    assert clip.duration == pytest.approx(before, abs=1e-3)
