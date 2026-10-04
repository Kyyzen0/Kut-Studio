"""Les commandes du temps : une seule porte d'entrée pour le menu, l'inspecteur et les raccourcis (sans Qt)."""

from __future__ import annotations

import pytest

from core.project_model import Clip, MediaAsset, Project, Sequence, Track
from core.time_commands import SPEED_CHOICES, TimeCommand, apply_time_command
from core.time_ops import speed_points
from core.time_remapping import MAX_SPEED, MIN_SPEED, FlowQuality, TimeInterpolation, TimeRemapping
from core.timeline_operations import set_clip_freeze_frame

FPS = 30.0


def _project(remapping=None):
    asset = MediaAsset(id="a", path="/tmp/x.mp4", name="x", duration=60.0, width=1920, height=1080, fps=FPS, media_type="video",
                       has_audio=True)
    clip = Clip(id="c1", asset_id="a", track_id="V1", timeline_start=0.0, source_in=0.0, source_out=10.0,
                time_remapping=remapping or TimeRemapping())
    project = Project(name="p", width=1920, height=1080, fps=FPS, media_assets=[asset],
                      tracks=[Track(id="V1", name="V1", type="video", clips=[clip])])
    return project, clip


@pytest.mark.parametrize("speed", SPEED_CHOICES)
def test_every_speed_of_the_menu_sets_the_static_speed(speed):
    project, clip = _project()
    assert apply_time_command(project, "c1", TimeCommand.SPEED, speed) == "history.speed.edit"
    assert clip.time_remapping.speed == speed


def test_reverse_returns_the_matching_history_label():
    project, clip = _project()
    assert apply_time_command(project, "c1", "reverse", True) == "history.clip.reverse" and clip.time_remapping.reverse
    assert apply_time_command(project, "c1", "reverse", False) == "history.clip.unreverse" and not clip.time_remapping.reverse


def test_the_interpolation_and_its_quality_are_set_independently():
    project, clip = _project()
    assert apply_time_command(project, "c1", "interpolation", "optical_flow") == "history.time.interpolation"
    assert apply_time_command(project, "c1", "quality", "best") == "history.time.quality"
    assert clip.time_remapping.interpolation is TimeInterpolation.OPTICAL_FLOW and clip.time_remapping.flow_quality is FlowQuality.BEST
    apply_time_command(project, "c1", "interpolation", "blending")
    assert clip.time_remapping.flow_quality is FlowQuality.BEST                         # la qualité survit au changement de mode


def test_the_audio_choices_do_not_change_the_clip_duration():
    project, clip = _project(TimeRemapping(speed=2.0))
    duration = clip.duration
    apply_time_command(project, "c1", "preserve_pitch", False)
    apply_time_command(project, "c1", "remap_audio", False)
    assert not clip.time_remapping.preserve_pitch and not clip.time_remapping.remap_audio and clip.duration == duration


def test_a_speed_point_is_added_at_the_playhead_snapped_to_a_frame_without_changing_the_curve():
    project, clip = _project(TimeRemapping(speed=2.0))
    before = clip.time_map.source_time(3.0)
    assert apply_time_command(project, "c1", "add_point", local_time=1.0004, fps=FPS) == "history.time.add_point"
    points = speed_points(clip)
    assert len(points) == 1 and points[0].time_seconds == pytest.approx(1.0) and points[0].value == 2.0
    assert clip.time_map.source_time(3.0) == pytest.approx(before)


def test_a_hold_lengthens_the_clip_and_a_preset_is_named_in_the_history():
    project, clip = _project()
    duration = clip.duration
    assert apply_time_command(project, "c1", "hold", local_time=2.0, fps=FPS) == "history.time.hold"
    assert clip.duration == pytest.approx(duration + 1.0)
    assert apply_time_command(project, "c1", "preset", "slow_25") == "history.time.preset.slow_25"
    assert clip.time_remapping.speed == 0.25 and not clip.has_speed_curve


def test_clearing_the_curve_and_resetting_return_to_a_plain_clip():
    project, clip = _project()
    apply_time_command(project, "c1", "add_point", local_time=1.0, fps=FPS)
    apply_time_command(project, "c1", "clear_curve")
    assert not clip.has_speed_curve
    apply_time_command(project, "c1", "interpolation", "blending")
    assert apply_time_command(project, "c1", "reset") == "history.time.reset"
    assert clip.time_remapping.is_normal


def test_an_unknown_command_or_a_bad_value_is_refused_and_leaves_the_clip_untouched():
    project, clip = _project()
    snapshot = (clip.time_remapping, list(clip.animation))
    with pytest.raises(ValueError):
        apply_time_command(project, "c1", "turbo")
    with pytest.raises(ValueError):
        apply_time_command(project, "c1", "speed", None)
    with pytest.raises(KeyError):
        apply_time_command(project, "missing", "speed", 2.0)
    assert (clip.time_remapping, list(clip.animation)) == snapshot


def test_a_speed_out_of_range_is_bounded_like_the_inspector_field_does():
    project, clip = _project()
    apply_time_command(project, "c1", "speed", 500.0)
    assert clip.time_remapping.speed == MAX_SPEED
    apply_time_command(project, "c1", "speed", 0.0001)
    assert clip.time_remapping.speed == MIN_SPEED


def test_a_frozen_clip_refuses_the_interpolation_commands():
    project, _clip = _project()
    set_clip_freeze_frame(project, "c1", 5.0, 2.0)
    with pytest.raises(ValueError):
        apply_time_command(project, "c1", "interpolation", "optical_flow")


def test_a_nested_sequence_refuses_frame_interpolation_but_accepts_sampling():
    from core.sequences import insert_sequence_clip

    asset = MediaAsset(id="a", path="/tmp/x.mp4", name="x", duration=60.0, width=1280, height=720, fps=FPS, media_type="video")
    inner = Sequence(id="inner", name="Inner", width=1280, height=720, fps=FPS, tracks=[
        Track(id="V1", name="V1", type="video", clips=[Clip(id="i1", asset_id="a", track_id="V1", timeline_start=0.0, source_in=0.0,
                                                           source_out=5.0)])])
    main = Sequence(id="main", name="Main", width=1280, height=720, fps=FPS, tracks=[Track(id="V1", name="V1", type="video")])
    project = Project(name="p", width=1280, height=720, fps=FPS, media_assets=[asset], sequences=[main, inner],
                      active_sequence_id="main")
    nested = insert_sequence_clip(project, "inner", "V1", 0.0)
    with pytest.raises(ValueError, match="séquence imbriquée"):
        apply_time_command(project, nested.id, "interpolation", "optical_flow")
    apply_time_command(project, nested.id, "interpolation", "sampling")
    apply_time_command(project, nested.id, "speed", 2.0)
