from __future__ import annotations

import pytest

from core.project_io import load_project, save_project
from core.project_model import Clip, MediaAsset, Project, Track
from core.timeline_operations import cut_clip, delete_clip
from core.render_plan import build_render_plan
from core.transitions import (
    TransitionType,
    add_transition,
    remove_transition,
    update_transition,
)


def _project() -> Project:
    asset = MediaAsset("asset", "/tmp/video.mp4", "Video", 10.0, 1920, 1080, 30.0, "video")
    track = Track("V1", "V1", "video", clips=[
        Clip("a", "asset", "V1", 0.0, 0.0, 4.0),
        Clip("b", "asset", "V1", 4.0, 4.0, 8.0),
    ])
    return Project("Transitions", media_assets=[asset], tracks=[track])


def test_transition_roundtrip_and_legacy_default(tmp_path):
    project = _project()
    transition = add_transition(project, "a", "b", TransitionType.WIPE_LEFT, 0.5)
    path = tmp_path / "transition.kut"
    save_project(project, str(path))
    loaded = load_project(str(path))
    assert loaded.transitions == [transition]


def test_transition_rejects_invalid_pair_and_duration():
    project = _project()
    with pytest.raises(ValueError, match="moitié"):
        add_transition(project, "a", "b", duration=2.1)
    add_transition(project, "a", "b")
    with pytest.raises(ValueError, match="existe déjà"):
        add_transition(project, "a", "b")


def test_transition_can_be_updated_and_removed():
    project = _project()
    transition = add_transition(project, "a", "b")
    updated = update_transition(project, transition.id, transition_type=TransitionType.FADE_BLACK, duration=1.0)
    assert updated.type is TransitionType.FADE_BLACK
    assert remove_transition(project, updated.id) == updated
    assert project.transitions == []
    assert project.tracks[0].clips[0].fade_out == 0.0
    assert project.tracks[0].clips[1].fade_in == 0.0


def test_cut_or_delete_removes_stale_transition():
    project = _project()
    add_transition(project, "a", "b")
    cut_clip(project, "a", 2.0)
    assert project.transitions == []


@pytest.mark.parametrize(
    ("transition_type", "ffmpeg_name"),
    [
        (TransitionType.CROSSFADE, "fade"),
        (TransitionType.FADE_BLACK, "fadeblack"),
        (TransitionType.WIPE_LEFT, "wipeleft"),
        (TransitionType.WIPE_RIGHT, "wiperight"),
    ],
)
def test_export_filter_contains_requested_xfade(transition_type, ffmpeg_name):
    from core.export_engine import ExportEngine

    project = _project()
    add_transition(project, "a", "b", transition_type, 0.5)
    plan = build_render_plan(project)
    graph, _video, _audio, _inputs = ExportEngine._build_filter_complex(
        plan, 1920, 1080, 30, None
    )
    assert f"xfade=transition={ffmpeg_name}:duration=0.5" in graph
    project = _project()
    add_transition(project, "a", "b")
    delete_clip(project, "b")
    assert project.transitions == []


# --- Rendu réel : transitions entre calques transformés ----------------------------------------------------------


@pytest.mark.skipif(__import__("shutil").which("ffmpeg") is None, reason="FFmpeg absent : rendu réel impossible")
def test_a_transition_after_an_impact_zoom_renders_and_keeps_each_clip_in_its_place(tmp_path):
    """Avant le 2026-10-09, ``xfade`` fondait les deux calques eux-mêmes : une échelle animée (zoom d'impact, Ken
    Burns) donnait deux cadres de ``rotate`` de tailles différentes et FFmpeg refusait tout l'export ; et le second
    clip aurait été posé à la place du premier. Chaque calque est maintenant posé à sa place, puis les cadres fondus."""
    import numpy as np
    from render_probe import lavfi_video, render_frame

    from core.animation import InterpolationType
    from core.visual_effects import ClipTransform, TransformKeyframe

    width, height = 96, 54
    red = lavfi_video(tmp_path / "red.mp4", "color=c=red", size=(width, height), seconds=2.0)
    blue = lavfi_video(tmp_path / "blue.mp4", "color=c=blue", size=(width, height), seconds=2.0)
    project = Project("t", width=width, height=height, fps=25.0, media_assets=[
        MediaAsset("r", str(red), "r", 2.0, width, height, 25.0, "video"),
        MediaAsset("b", str(blue), "b", 2.0, width, height, 25.0, "video"),
    ], tracks=[Track("V1", "V1", "video", clips=[
        Clip("a", "r", "V1", 0.0, 0.0, 1.0,
             transform_keyframes=[TransformKeyframe("scale", 0.0, 1.2, InterpolationType.EASE_OUT),
                                  TransformKeyframe("scale", 0.3, 1.0, InterpolationType.LINEAR)]),
        Clip("b", "b", "V1", 1.0, 0.0, 1.0, transform=ClipTransform(scale=0.5, position_x=0.25)),
    ])])
    add_transition(project, "a", "b", duration=0.4)
    plan = build_render_plan(project)
    during = render_frame(plan, width, height, 0.8).astype(int)
    after = render_frame(plan, width, height, 0.98).astype(int)
    right, left = after[height // 2, width * 3 // 4], after[height // 2, width // 8]
    assert right[2] > 150 and right[0] < 80, ("le second clip, réduit, est à droite", right)
    assert left.max() < 40, ("rien à gauche à la fin : le premier clip est fondu, le second ailleurs", left)
    assert during[height // 2, width // 8][0] > 40, "pendant le fondu, le premier clip est encore visible"
    assert np.abs(during - after).mean() > 5
