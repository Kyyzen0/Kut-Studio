"""Raccourcir un clip par sa vitesse ou son arrêt sur image ne change pas l'animation visible.

Régression : ces opérations raccourcissaient le clip sans toucher à ses keyframes. L'aperçu et l'inspecteur
ignoraient alors ceux d'après la fin, alors que le Graph Editor et l'export les gardaient : à t = 1 s d'une
rampe 0 → 1 sur 3 s, l'aperçu montrait 0,000 et l'export 0,333. Ces opérations se comportent maintenant
comme un trim droit : les keyframes au-delà de la nouvelle fin sont découpés, l'animation visible reste.
"""

from __future__ import annotations

import pytest

from core.project_model import Clip, MediaAsset, Project, Track
from core.timeline_operations import (
    remove_clip_freeze_frame,
    reset_clip_time_remapping,
    set_clip_freeze_duration,
    set_clip_freeze_frame,
    set_clip_speed,
)
from core.visual_effects import ClipTransform, TransformKeyframe, evaluate_transform

INSTANTS = (0.0, 0.4, 0.75, 1.3, 1.9, 2.5)


def _project():
    asset = MediaAsset(id="a", path="/tmp/a.mp4", name="a", duration=60.0, width=1920, height=1080, fps=25.0,
                       media_type="video", has_audio=False)
    clip = Clip(id="c", asset_id="a", track_id="V1", timeline_start=0.0, source_in=0.0, source_out=6.0)
    clip.transform = ClipTransform()
    clip.transform_keyframes = [TransformKeyframe("scale", 0.0, 1.0), TransformKeyframe("scale", 6.0, 2.0),
                                TransformKeyframe("opacity", 0.0, 0.2), TransformKeyframe("opacity", 5.0, 1.0)]
    project = Project(name="p", width=1920, height=1080, fps=25.0, media_assets=[asset],
                      tracks=[Track(id="V1", name="V1", type="video", clips=[clip])])
    return project, clip


def _visible(clip, instants=INSTANTS):
    """Animation visible : ``(scale, opacity)`` à chaque instant, évaluée comme l'aperçu."""
    return [
        (round(evaluate_transform(clip.transform, clip.transform_keyframes, t, clip.duration).scale, 6),
         round(evaluate_transform(clip.transform, clip.transform_keyframes, t, clip.duration).opacity, 6))
        for t in instants
    ]


def _no_keyframe_after_the_end(clip):
    return all(k.time_seconds <= clip.duration + 1e-6 for k in clip.transform_keyframes)


def test_a_faster_clip_keeps_the_visible_animation():
    project, clip = _project()
    before = _visible(clip)
    set_clip_speed(project, "c", 2.0)                       # durée 6 s → 3 s : keyframes à 6 s et 5 s hors du clip
    assert clip.duration == pytest.approx(3.0)
    assert _no_keyframe_after_the_end(clip)
    assert _visible(clip) == pytest.approx(before)


def test_a_shorter_freeze_keeps_the_visible_animation():
    project, clip = _project()
    set_clip_freeze_frame(project, "c", 2.0, 4.0)
    before = _visible(clip, (0.0, 0.9, 1.7, 2.6))
    set_clip_freeze_duration(project, "c", 3.0)
    assert clip.duration == pytest.approx(3.0)
    assert _no_keyframe_after_the_end(clip)
    assert _visible(clip, (0.0, 0.9, 1.7, 2.6)) == pytest.approx(before)


def test_removing_a_freeze_or_resetting_a_slow_clip_keeps_the_visible_animation():
    project, clip = _project()
    set_clip_speed(project, "c", 0.5)                       # 12 s : les keyframes (6 s, 5 s) y tiennent
    before = _visible(clip)
    reset_clip_time_remapping(project, "c")                 # retour à ×1 : 6 s
    assert clip.duration == pytest.approx(6.0)
    assert _no_keyframe_after_the_end(clip)
    assert _visible(clip) == pytest.approx(before)

    project, clip = _project()
    set_clip_freeze_frame(project, "c", 2.0, 4.0)
    before = _visible(clip, (0.0, 1.0, 2.0, 3.5))
    remove_clip_freeze_frame(project, "c")
    assert _no_keyframe_after_the_end(clip)
    assert _visible(clip, (0.0, 1.0, 2.0, 3.5)) == pytest.approx(before)


def test_a_longer_clip_keeps_every_keyframe():
    """Ralentir ne supprime rien : l'animation est conservée telle quelle."""
    project, clip = _project()
    count = len(clip.transform_keyframes)
    before = _visible(clip)
    set_clip_speed(project, "c", 0.5)
    assert len(clip.transform_keyframes) == count
    assert _visible(clip) == pytest.approx(before)
