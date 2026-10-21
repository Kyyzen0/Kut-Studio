"""Découpe au plan dans la fenêtre : un vrai média, une vraie détection, la timeline coupée et annulable.

Sans FFmpeg, les tests réels sont sautés.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from multicam_stubs import keep_preview_player_off_the_disk

from core.project_model import Clip, MediaAsset, Project, Track
from ui import i18n

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg absent")


@pytest.fixture
def window(qtbot, monkeypatch):
    from ui.main_window import MainWindow

    i18n.set_language("fr")
    monkeypatch.setattr("ui.main_window.QMessageBox.information", lambda *_, **__: None)
    monkeypatch.setattr("ui.main_window.QMessageBox.critical", lambda *_, **__: None)
    main = MainWindow()
    qtbot.addWidget(main)
    main.timeline_timer.stop()
    keep_preview_player_off_the_disk(main, monkeypatch)
    return main


def _load(window, media: Path, duration: float) -> None:
    asset = MediaAsset("a", str(media), "A", duration, 160, 90, 25.0, "video", False)
    track = Track(id="V1", name="V1", type="video", clips=[
        Clip(id="c1", asset_id="a", track_id="V1", timeline_start=0.0, source_in=0.0, source_out=duration),
    ])
    project = Project(name="Plans", width=160, height=90, fps=25.0, media_assets=[asset], tracks=[track])
    window.project = project
    window.history.reset(project)
    window.timeline_panel.set_project(project)
    window._update_timeline_duration()


def _two_colour_clip(path: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=red:s=160x90:d=1:r=25",
         "-f", "lavfi", "-i", "color=blue:s=160x90:d=2:r=25",
         "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0,format=yuv420p", "-t", "3", str(path)],
        check=True,
    )


def test_without_a_selected_clip_nothing_is_detected(window, tmp_path):
    _load(window, tmp_path / "absent.mp4", 3.0)
    window.timeline_panel.selected_clip_id = None
    window.cut_at_scene_changes()
    assert getattr(window, "_scene_detections", {}) == {}
    assert len(window.project.tracks[0].clips) == 1


@needs_ffmpeg
def test_the_selected_clip_is_cut_at_the_real_scene_change(qtbot, window, tmp_path):
    media = tmp_path / "cut.mp4"
    _two_colour_clip(media)
    _load(window, media, 3.0)
    window.timeline_panel.selected_clip_id = "c1"
    window.cut_at_scene_changes()
    qtbot.waitUntil(lambda: len(window.project.tracks[0].clips) == 2, timeout=60000)

    clips = sorted(window.project.tracks[0].clips, key=lambda clip: clip.timeline_start)
    assert clips[0].id == "c1" and abs(clips[0].duration - 1.0) < 0.1
    assert abs(clips[1].timeline_start - 1.0) < 0.1
    assert getattr(window, "_scene_detections", {}) == {}      # la boîte de progression et le minuteur sont libérés


@needs_ffmpeg
def test_a_detection_is_not_started_twice_for_the_same_clip(qtbot, window, tmp_path):
    media = tmp_path / "cut.mp4"
    _two_colour_clip(media)
    _load(window, media, 3.0)
    window.timeline_panel.selected_clip_id = "c1"
    window.cut_at_scene_changes()
    window.cut_at_scene_changes()
    assert len(window._scene_detections) == 1
    qtbot.waitUntil(lambda: len(window.project.tracks[0].clips) == 2, timeout=60000)
