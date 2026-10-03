"""Rogner et couper un clip dont la vitesse est animée ne change jamais l'image montrée.

Même critère que ``test_trim_with_remapping`` (vitesse constante) : l'image source montrée à chaque instant de timeline qui
reste est la même avant et après, ici avec une courbe de vitesse (rampe linéaire, Bézier, palier, retournement) dans les deux
sens de lecture. Une coupe **au milieu d'une rampe** donne aux deux moitiés exactement l'animation temporelle d'origine : les
keyframes de vitesse sont séparées avec les autres (forme conservée), et chaque moitié est une restriction du mapping.
"""

from __future__ import annotations

import pytest

from core.animation import InterpolationType, Keyframe
from core.project_model import Clip, MediaAsset, Project, Track
from core.time_map import SPEED_PROPERTY, PiecewiseTimeMap
from core.time_remapping import TimeRemapping
from core.timeline_operations import (
    cut_clip,
    reset_clip_time_remapping,
    set_clip_speed,
    trim_clip_left,
    trim_clip_right,
)

LINEAR, HOLD, BEZIER = InterpolationType.LINEAR, InterpolationType.HOLD, InterpolationType.BEZIER


def key(time, value, interpolation=LINEAR, **extra) -> Keyframe:
    return Keyframe(SPEED_PROPERTY, time, value, interpolation, **extra)


CURVES = {
    "rampe": (key(0, 1.0), key(2, 1.0), key(3, 0.25), key(6, 0.25), key(7, 2.0)),
    "bézier": (key(0, 1.0, BEZIER, out_slope=1.5), key(3, 0.4, BEZIER, in_slope=-0.5, out_slope=0.8), key(6, 2.0)),
    "palier": (key(0, 1.0, HOLD), key(2, 3.0, HOLD), key(4, 0.5, HOLD)),
    "retournement": (key(0, 1.0), key(4, -1.0)),
}


def _project(keys, *, reverse=False, start=3.0, source=(10.0, 50.0), anchor=None, media=300.0):
    asset = MediaAsset(id="a", path="/tmp/x.mp4", name="x", duration=media, width=1920, height=1080,
                       fps=25.0, media_type="video", has_audio=False)
    remapping = TimeRemapping(reverse=reverse, anchor=anchor)
    clip = Clip(id="c1", asset_id="a", track_id="V1", timeline_start=start, source_in=source[0], source_out=source[1],
                time_remapping=remapping, animation=list(keys))
    project = Project(name="p", width=1280, height=720, fps=25.0, media_assets=[asset],
                      tracks=[Track(id="V1", name="V1", type="video", clips=[clip])])
    return project, clip


def _snapshot(clip: Clip, fractions=(0.05, 0.2, 0.41, 0.5, 0.77, 0.99)):
    """``(instant absolu de timeline, temps source montré)`` à quelques fractions de la durée."""
    tm = clip.time_map
    return [(clip.timeline_start + tm.duration * f, tm.source_time(tm.duration * f)) for f in fractions]


def _source_at(clip: Clip, absolute_time: float) -> float:
    return clip.time_map.source_time(absolute_time - clip.timeline_start)


# ---------------------------------------------------------------------------
# Coupe
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("reverse", [False, True], ids=["avant", "inverse"])
@pytest.mark.parametrize("name", list(CURVES))
@pytest.mark.parametrize("fraction", [0.1, 0.37, 0.5, 0.83])
def test_a_cut_gives_both_halves_exactly_the_original_temporal_animation(name, reverse, fraction):
    anchor = 150.0 if name == "retournement" else None          # le retournement revient en arrière : ancre au milieu
    project, clip = _project(CURVES[name], reverse=reverse, source=(10.0, 290.0), anchor=anchor, media=300.0)
    original = clip.time_map
    duration = original.duration
    cut_local = duration * fraction
    left, right = cut_clip(project, "c1", clip.timeline_start + cut_local)
    assert left.duration + right.duration == pytest.approx(duration, abs=1e-9)
    left_map, right_map = left.time_map, right.time_map
    for f in (0.0, 0.13, 0.5, 0.9, 1.0):
        t = left_map.duration * f
        assert left_map.source_time(t) == pytest.approx(original.source_time(t), abs=1e-9), ("gauche", f)
        assert left_map.speed_at(min(t, left_map.duration - 1e-9)) == pytest.approx(
            original.speed_at(min(t, left_map.duration - 1e-9)), abs=1e-9
        )
        u = right_map.duration * f
        assert right_map.source_time(u) == pytest.approx(original.source_time(cut_local + u), abs=1e-9), ("droite", f)
    assert left_map.source_time(left_map.duration) == pytest.approx(right_map.source_time(0.0), abs=1e-9)  # raccord exact


def test_a_cut_in_the_middle_of_a_ramp_keeps_the_ramp_as_one_continuous_slope():
    project, clip = _project(CURVES["rampe"], source=(10.0, 80.0))
    cut_local = 2.5                                           # au milieu de la rampe 100 % → 25 %
    left, right = cut_clip(project, "c1", clip.timeline_start + cut_local)
    speed_at_cut = clip.time_map.speed_at(cut_local)
    assert left.time_map.speed_at(cut_local - 1e-6) == pytest.approx(speed_at_cut, abs=1e-5)
    assert right.time_map.speed_at(1e-6) == pytest.approx(speed_at_cut, abs=1e-5)


def test_a_monotone_curve_that_runs_out_its_source_stays_described_by_its_bounds_alone():
    """Pas d'ancre ni de durée imposée inutiles : un clip monotone reste décrit par ``source_in`` / ``source_out``."""
    project, clip = _project(CURVES["rampe"], source=(10.0, 50.0))
    left, right = cut_clip(project, "c1", clip.timeline_start + 2.0)
    for half in (left, right):
        assert half.time_remapping.anchor is None and half.time_remapping.duration is None


def test_a_turning_curve_needs_an_explicit_anchor_and_stays_exact_on_both_sides_of_the_turn():
    project, clip = _project(CURVES["retournement"], source=(10.0, 290.0), anchor=150.0)
    original = clip.time_map
    for fraction in (0.2, 0.5, 0.8):                          # avant, sur et après le sommet (t = 2)
        project, clip = _project(CURVES["retournement"], source=(10.0, 290.0), anchor=150.0)
        cut = original.duration * fraction
        left, right = cut_clip(project, "c1", clip.timeline_start + cut)
        assert right.time_remapping.anchor == pytest.approx(original.source_time(cut), abs=1e-9)
        assert right.time_map.source_time(right.time_map.duration * 0.5) == pytest.approx(
            original.source_time(cut + right.time_map.duration * 0.5), abs=1e-9
        )


def test_cutting_inside_a_zero_speed_stretch_still_gives_two_valid_clips():
    keys = (key(0, 1.0, HOLD), key(2, 0.0, HOLD), key(5, 1.0, HOLD))
    project, clip = _project(keys, source=(10.0, 40.0), media=300.0)
    original = clip.time_map
    left, right = cut_clip(project, "c1", clip.timeline_start + 3.0)       # en plein arrêt
    assert left.source_out > left.source_in and right.source_out > right.source_in
    assert left.time_map.source_time(left.time_map.duration) == pytest.approx(original.source_time(3.0), abs=1e-9)
    assert right.time_map.source_time(0.0) == pytest.approx(original.source_time(3.0), abs=1e-9)
    assert right.time_map.duration == pytest.approx(original.duration - 3.0, abs=1e-9)


# ---------------------------------------------------------------------------
# Trims
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("reverse", [False, True], ids=["avant", "inverse"])
@pytest.mark.parametrize("name", ["rampe", "bézier", "palier"])
@pytest.mark.parametrize("fraction", [0.15, 0.4, 0.55, 0.9])
def test_a_left_trim_keeps_the_end_and_the_frames_that_remain(name, reverse, fraction):
    project, clip = _project(CURVES[name], reverse=reverse, source=(10.0, 290.0))
    end, duration = clip.timeline_start + clip.duration, clip.duration
    instants = [clip.timeline_start + duration * f for f in (fraction + 0.01, 0.6, 0.8, 0.99) if f >= fraction]
    before = [_source_at(clip, t) for t in instants]
    trim_clip_left(project, "c1", clip.timeline_start + fraction * duration)
    assert clip.timeline_start + clip.duration == pytest.approx(end, abs=1e-9)
    assert [_source_at(clip, t) for t in instants] == pytest.approx(before, abs=1e-9)


@pytest.mark.parametrize("reverse", [False, True], ids=["avant", "inverse"])
@pytest.mark.parametrize("name", ["rampe", "bézier", "palier"])
@pytest.mark.parametrize("fraction", [0.3, 0.6, 0.85])
def test_a_right_trim_keeps_the_start_and_the_frames_that_remain(name, reverse, fraction):
    project, clip = _project(CURVES[name], reverse=reverse, source=(10.0, 290.0))
    start, duration = clip.timeline_start, clip.duration
    instants = [start + duration * f * fraction for f in (0.0, 0.1, 0.5, 0.97)]
    before = [_source_at(clip, t) for t in instants]
    trim_clip_right(project, "c1", start + fraction * duration)
    assert clip.timeline_start == pytest.approx(start)
    assert clip.duration == pytest.approx(fraction * duration, abs=1e-9)
    assert [_source_at(clip, t) for t in instants] == pytest.approx(before, abs=1e-9)


def test_a_trim_in_the_middle_of_a_ramp_then_another_after_it_stay_consistent():
    project, clip = _project(CURVES["rampe"], source=(10.0, 290.0))
    reference = _snapshot(clip, (0.5, 0.6, 0.7, 0.8, 0.9))
    trim_clip_left(project, "c1", clip.timeline_start + 2.5)       # au milieu de la rampe
    trim_clip_right(project, "c1", clip.timeline_start + clip.duration - 1.0)
    for absolute, expected in reference:
        if clip.timeline_start <= absolute < clip.timeline_start + clip.duration:
            assert _source_at(clip, absolute) == pytest.approx(expected, abs=1e-9)


def test_a_right_trim_can_extend_along_the_curve_until_the_media_runs_out():
    project, clip = _project(CURVES["rampe"], source=(10.0, 40.0), media=300.0)
    original = clip.time_map
    longer = original.duration + 6.0
    trim_clip_right(project, "c1", clip.timeline_start + longer)
    assert clip.duration == pytest.approx(longer, abs=1e-9)
    for f in (0.0, 0.3, 0.6, 0.99):
        t = original.duration * f
        assert clip.time_map.source_time(t) == pytest.approx(original.source_time(t), abs=1e-9)   # le début ne bouge pas
    assert clip.source_out > 40.0                                                              # la source s'est allongée


def test_a_right_trim_cannot_extend_past_the_media():
    project, clip = _project(CURVES["rampe"], source=(10.0, 40.0), media=45.0)
    with pytest.raises(ValueError, match="dépasserait"):
        trim_clip_right(project, "c1", clip.timeline_start + 500.0)
    assert clip.source_out == 40.0                                                             # rien n'a changé


def test_the_keyframes_of_the_speed_curve_follow_a_left_trim_on_the_same_instants():
    project, clip = _project(CURVES["rampe"], source=(10.0, 290.0))
    before = clip.time_map.speed_at(4.5)
    trim_clip_left(project, "c1", clip.timeline_start + 1.5)
    assert clip.time_map.speed_at(4.5 - 1.5) == pytest.approx(before, abs=1e-9)
    assert min(k.time_seconds for k in clip.animation if k.property_name == SPEED_PROPERTY) >= 0.0


# ---------------------------------------------------------------------------
# Opérations de vitesse sur un clip à courbe
# ---------------------------------------------------------------------------


def test_setting_a_constant_speed_replaces_the_curve_and_is_undoable_as_one_snapshot():
    project, clip = _project(CURVES["rampe"], source=(10.0, 50.0))
    extent = clip.time_map.extent()
    set_clip_speed(project, "c1", 2.0)
    assert not clip.has_speed_curve and clip.time_remapping.speed == 2.0
    assert (clip.source_in, clip.source_out) == pytest.approx(extent, abs=1e-9)
    assert clip.duration == pytest.approx((extent[1] - extent[0]) / 2.0, abs=1e-9)


def test_resetting_the_remapping_removes_the_curve_and_keeps_the_media_that_was_shown():
    project, clip = _project(CURVES["rampe"], source=(10.0, 50.0))
    extent = clip.time_map.extent()
    reset_clip_time_remapping(project, "c1")
    assert not clip.has_speed_curve and clip.time_remapping == TimeRemapping.default()
    assert clip.duration == pytest.approx(extent[1] - extent[0], abs=1e-9)


def test_speed_operations_refuse_a_locked_track():
    project, clip = _project(CURVES["rampe"])
    project.tracks[0].locked = True
    with pytest.raises(ValueError, match="verrouill"):
        set_clip_speed(project, "c1", 2.0)
    assert clip.has_speed_curve


def test_a_speed_curve_survives_a_reverse_toggle_as_the_same_curve_played_backwards():
    from core.timeline_operations import set_clip_reverse

    project, clip = _project(CURVES["rampe"], source=(10.0, 50.0))
    forward = clip.time_map
    set_clip_reverse(project, "c1", True)
    backward = clip.time_map
    assert isinstance(backward, PiecewiseTimeMap) and backward.reverse
    assert backward.duration == pytest.approx(forward.duration, abs=1e-9)
    assert backward.source_time(1.0) == pytest.approx(50.0 - (forward.source_time(1.0) - 10.0), abs=1e-9)
