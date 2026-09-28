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
