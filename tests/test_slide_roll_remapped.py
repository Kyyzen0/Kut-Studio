"""Slide et roll respectent la vitesse et le sens des clips voisins.

Régression : ils ajoutaient un décalage de **timeline** à des secondes de **source** (``source_out += delta``), ce qui n'est
juste qu'à vitesse 1 et sans lecture inverse : à ×2 un roll de 5 s déplaçait la coupe de 10 s de source, en reverse il rognait
l'autre extrémité. Le critère est celui des trims : l'image source montrée à chaque instant de timeline qui n'est pas
concerné par l'édition est la même avant et après.
"""

from __future__ import annotations

import pytest

from core.animation import InterpolationType, Keyframe
from core.project_model import Clip, MediaAsset, Project, Track
from core.time_map import SPEED_PROPERTY
from core.time_remapping import TimeRemapping
from core.timeline_editing import roll_edit, slide_clip


def _project(left: TimeRemapping, right: TimeRemapping, *, middle: TimeRemapping | None = None):
    asset = MediaAsset(id="a", path="/tmp/x.mp4", name="x", duration=500.0, width=1920, height=1080,
                       fps=25.0, media_type="video", has_audio=False)
    clips = [Clip(id="L", asset_id="a", track_id="V1", timeline_start=0.0, source_in=100.0, source_out=140.0,
                  time_remapping=left)]
    start = clips[0].duration
    if middle is not None:
        clips.append(Clip(id="M", asset_id="a", track_id="V1", timeline_start=start, source_in=200.0, source_out=220.0,
                          time_remapping=middle))
        start += clips[-1].duration
    clips.append(Clip(id="R", asset_id="a", track_id="V1", timeline_start=start, source_in=300.0, source_out=340.0,
                      time_remapping=right))
    project = Project(name="p", width=1280, height=720, fps=25.0, media_assets=[asset],
                      tracks=[Track(id="V1", name="V1", type="video", clips=clips)])
    return project, {clip.id: clip for clip in clips}


def _shown(clips, absolute_time: float):
    """``(clip, temps source)`` montré à l'instant ``absolute_time`` (dernier clip qui le couvre)."""
    found = None
    for clip in clips.values():
        if clip.timeline_start <= absolute_time < clip.timeline_start + clip.duration:
            found = (clip.id, clip.time_map.source_time(absolute_time - clip.timeline_start))
    return found


CASES = [
    pytest.param(TimeRemapping(speed=2.0), TimeRemapping(speed=0.5), id="x2-x0.5"),
    pytest.param(TimeRemapping(speed=2.0, reverse=True), TimeRemapping(speed=0.5), id="reverse-x2-x0.5"),
    pytest.param(TimeRemapping(speed=0.5), TimeRemapping(speed=4.0, reverse=True), id="x0.5-reverse-x4"),
]


@pytest.mark.parametrize("left, right", CASES)
@pytest.mark.parametrize("delta", [3.0, -2.0])
def test_a_roll_moves_the_cut_without_changing_the_frames_on_either_side(left, right, delta):
    project, clips = _project(left, right)
    cut = clips["L"].duration
    far_left, far_right = cut * 0.4, cut + clips["R"].duration * 0.7
    right_end = clips["R"].timeline_start + clips["R"].duration                  # la fin de R ne doit pas bouger
    before = {t: _shown(clips, t) for t in (far_left, far_right)}
    roll_edit(project, "L", "right", cut + delta)
    assert clips["R"].timeline_start == pytest.approx(cut + delta, abs=1e-9)
    assert clips["R"].timeline_start + clips["R"].duration == pytest.approx(right_end, abs=1e-9)
    for t, expected in before.items():
        shown = _shown(clips, t)
        assert shown[0] == expected[0] and shown[1] == pytest.approx(expected[1], abs=1e-9), t


@pytest.mark.parametrize("left, right", CASES)
def test_a_slide_keeps_the_sliding_clip_whole_and_the_neighbours_frames_in_place(left, right):
    project, clips = _project(left, right, middle=TimeRemapping(speed=1.0))
    start = clips["M"].timeline_start
    middle_duration = clips["M"].duration
    probes = [clips["L"].duration * 0.3, start + middle_duration + clips["R"].duration * 0.8]
    before = [_shown(clips, t) for t in probes]
    middle_before = clips["M"].time_map.source_time(middle_duration * 0.5)
    slide_clip(project, "M", start + 2.0)
    assert clips["M"].duration == pytest.approx(middle_duration, abs=1e-9)
    assert clips["M"].time_map.source_time(middle_duration * 0.5) == pytest.approx(middle_before, abs=1e-9)
    for t, expected in zip(probes, before):
        shown = _shown(clips, t)
        assert shown[0] == expected[0] and shown[1] == pytest.approx(expected[1], abs=1e-9), t


def test_a_roll_that_leaves_the_media_is_refused_and_changes_nothing():
    project, clips = _project(TimeRemapping(speed=2.0), TimeRemapping(speed=0.5))
    windows = {name: (clip.source_in, clip.source_out, clip.timeline_start) for name, clip in clips.items()}
    with pytest.raises(ValueError):
        roll_edit(project, "L", "right", clips["L"].duration + 400.0)
    assert {name: (clip.source_in, clip.source_out, clip.timeline_start) for name, clip in clips.items()} == windows


def test_slide_and_roll_are_refused_on_a_clip_whose_speed_is_animated():
    ramp = [Keyframe(SPEED_PROPERTY, 0.0, 1.0, InterpolationType.LINEAR), Keyframe(SPEED_PROPERTY, 4.0, 2.0)]
    project, clips = _project(TimeRemapping(), TimeRemapping())
    clips["R"].animation = list(ramp)
    with pytest.raises(ValueError, match="vitesse est animée"):
        roll_edit(project, "L", "right", clips["L"].duration + 1.0)
    with pytest.raises(ValueError, match="vitesse est animée"):
        slide_clip(project, "L", 1.0)
