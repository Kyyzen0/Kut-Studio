"""Couper la source d'un tracking : l'interface le dit au lieu de laisser le suivi s'arrêter en silence."""

from __future__ import annotations

from test_scopes import _window

from core.project_model import Clip, MediaAsset, Project, Track
from core.tracking_model import ClipTracking, TrackLink
from core.tracking_ops import tracking_dependents


def _project(*, enabled: bool = True) -> Project:
    media = MediaAsset("m", "/tmp/m.mp4", "m", 10.0, 160, 90, 10.0, "video", False)
    source = Clip("source", "m", "V1", 0.0, 0.0, 6.0)
    follower = Clip("follower", "m", "V2", 0.0, 0.0, 6.0)
    follower.tracking = ClipTracking(
        links=(TrackLink(id="link", source_clip_id="source", tracker_ids=("t1",), enabled=enabled),)
    )
    return Project(
        name="Suivi", width=160, height=90, fps=10.0, media_assets=[media],
        tracks=[Track("V1", "V1", "video", clips=[source]), Track("V2", "V2", "video", clips=[follower])],
    )


def test_dependents_lists_the_clips_that_follow_the_source():
    project = _project()
    assert tracking_dependents(project, "source") == ["follower"]
    assert tracking_dependents(project, "follower") == []
    assert tracking_dependents(project, "inconnu") == []


def test_a_disabled_link_does_not_count_and_a_self_link_is_not_a_dependent():
    assert tracking_dependents(_project(enabled=False), "source") == []
    project = _project()
    project.tracks[0].clips[0].tracking = ClipTracking(links=(TrackLink(id="own", tracker_ids=("t1",)),))
    assert tracking_dependents(project, "source") == ["follower"]          # le lien « sur lui-même » ne compte pas


def test_cutting_a_tracking_source_warns_in_the_status_bar(qtbot, monkeypatch):
    window = _window(qtbot, monkeypatch)
    window.project = _project()
    window.timeline_panel.set_project(window.project)
    window.statusBar().clearMessage()

    window.cut_selected_clip("source", 3.0)

    message = window.statusBar().currentMessage()
    assert "tracking" in message and "1 autre" in message
    assert len(window.project.tracks[0].clips) == 2                        # la coupe a bien eu lieu


def test_cutting_an_ordinary_clip_stays_silent(qtbot, monkeypatch):
    window = _window(qtbot, monkeypatch)
    window.project = _project()
    window.timeline_panel.set_project(window.project)
    window.statusBar().clearMessage()

    window.cut_selected_clip("follower", 3.0)                              # personne ne le suit

    assert "tracking" not in window.statusBar().currentMessage()
