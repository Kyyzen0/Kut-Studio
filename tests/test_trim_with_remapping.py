"""Un trim ne change jamais l'image montrée ni l'animation, quelle que soit la vitesse ou le sens.

Régression : les trims ajoutaient un delta de *timeline* à des secondes de *source*. À vitesse ×2, un
trim gauche faisait reculer la fin du clip ; en reverse, il coupait la queue au lieu de la tête ; un
arrêt sur image ignorait le trim. Les tests ne comparent pas des formules : ils exigent que l'image
source montrée à chaque instant de timeline restante soit la même avant et après.
"""

from __future__ import annotations

import pytest

from core.project_model import Clip, MediaAsset, Project, Track
from core.time_remapping import FreezeFrameMode, TimeRemapping, timeline_to_source_time
from core.timeline_operations import trim_clip_left, trim_clip_right
from core.visual_effects import TransformKeyframe, evaluate_transform

CASES = [
    pytest.param(1.0, False, id="x1"),
    pytest.param(2.0, False, id="x2"),
    pytest.param(0.5, False, id="x0.5"),
    pytest.param(1.0, True, id="reverse"),
    pytest.param(2.0, True, id="reverse-x2"),
    pytest.param(0.5, True, id="reverse-x0.5"),
]


def _project(speed: float, reverse: bool, *, start: float = 3.0, remapping: TimeRemapping | None = None):
    asset = MediaAsset(id="a", path="/tmp/x.mp4", name="x", duration=100.0, width=1920, height=1080,
                       fps=25.0, media_type="video", has_audio=False)
    clip = Clip(id="c1", asset_id="a", track_id="V1", timeline_start=start, source_in=10.0, source_out=30.0,
                time_remapping=remapping or TimeRemapping(speed=speed, reverse=reverse))
    project = Project(name="p", width=1280, height=720, fps=25.0, media_assets=[asset],
                      tracks=[Track(id="V1", name="V1", type="video", clips=[clip])])
    return project, clip


def _source_at(clip: Clip, absolute_time: float) -> float:
    """Temps de source montré à l'instant ``absolute_time`` de la timeline."""
    remap = clip.time_remapping
    return timeline_to_source_time(absolute_time - clip.timeline_start, clip.source_in, clip.source_out,
                                   remap.speed, remap.reverse, remap.freeze_mode, remap.freeze_source_time)


def _end(clip: Clip) -> float:
    return clip.timeline_start + clip.duration


@pytest.mark.parametrize("speed, reverse", CASES)
def test_a_left_trim_keeps_the_end_and_the_frames_that_remain(speed, reverse):
    project, clip = _project(speed, reverse)
    end, duration = _end(clip), clip.duration
    instants = [clip.timeline_start + duration * f for f in (0.4, 0.5, 0.75, 0.99)]
    before = [_source_at(clip, t) for t in instants]
    trim_clip_left(project, "c1", clip.timeline_start + 0.4 * duration)
    assert _end(clip) == pytest.approx(end)
    assert [_source_at(clip, t) for t in instants] == pytest.approx(before, abs=1e-9)


@pytest.mark.parametrize("speed, reverse", CASES)
def test_a_right_trim_keeps_the_start_and_the_frames_that_remain(speed, reverse):
    project, clip = _project(speed, reverse)
    start, duration = clip.timeline_start, clip.duration
    instants = [start + duration * f for f in (0.0, 0.1, 0.25, 0.5)]
    before = [_source_at(clip, t) for t in instants]
    trim_clip_right(project, "c1", start + 0.6 * duration)
    assert clip.timeline_start == pytest.approx(start)
    assert clip.duration == pytest.approx(0.6 * duration)
    assert [_source_at(clip, t) for t in instants] == pytest.approx(before, abs=1e-9)


@pytest.mark.parametrize("speed, reverse", CASES)
def test_trimming_keeps_the_animation_on_the_same_timeline_instants(speed, reverse):
    """Les keyframes sont en temps local de timeline : la vitesse ne doit pas les déplacer."""
    project, clip = _project(speed, reverse)
    clip.transform_keyframes = [TransformKeyframe("scale", 0.0, 0.5), TransformKeyframe("scale", clip.duration, 1.5)]
    absolute = clip.timeline_start + 0.7 * clip.duration

    def scale_at(t):
        return evaluate_transform(clip.transform, clip.transform_keyframes, t - clip.timeline_start, clip.duration).scale

    expected = scale_at(absolute)
    trim_clip_left(project, "c1", clip.timeline_start + 0.3 * clip.duration)
    assert scale_at(absolute) == pytest.approx(expected, abs=1e-6)


def test_a_right_trim_to_the_requested_end_whatever_the_speed():
    """Le bord demandé est le bord obtenu (à ×2, une fin demandée à 4 donnait 4,5)."""
    for speed in (0.5, 1.0, 2.0, 4.0):
        project, clip = _project(speed, False, start=0.0)
        target = clip.duration - 1.0
        trim_clip_right(project, "c1", target)
        assert _end(clip) == pytest.approx(target)


def test_a_reversed_clip_cannot_be_extended_before_the_start_of_the_media():
    project, clip = _project(1.0, True, start=0.0)
    clip.source_in = 2.0
    with pytest.raises(ValueError):
        trim_clip_right(project, "c1", clip.duration + 5.0)


def test_a_frozen_clip_is_trimmed_by_its_freeze_duration():
    """Avant : le trim d'un arrêt sur image ne faisait rien (la durée vient de freeze_duration)."""
    freeze = TimeRemapping(freeze_mode=FreezeFrameMode.FREEZE, freeze_source_time=12.0, freeze_duration=4.0)
    project, clip = _project(1.0, False, start=1.0, remapping=freeze)
    trim_clip_right(project, "c1", 3.5)
    assert clip.duration == pytest.approx(2.5)
    trim_clip_left(project, "c1", 2.0)
    assert (clip.timeline_start, _end(clip)) == pytest.approx((2.0, 3.5))
    assert (clip.source_in, clip.source_out) == (10.0, 30.0)         # l'image tenue ne change pas
    assert clip.time_remapping.freeze_source_time == 12.0
