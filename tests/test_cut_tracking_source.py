"""Couper ou supprimer la source d'un tracking : l'interface dit ce qu'il advient des clips qui la suivent.

Le suivi continue sur les deux moitiés (voir ``test_tracking_split.py`` pour le comportement) ; ce fichier vérifie
ce que l'utilisateur en apprend dans la barre d'état, y compris quand un défaut subsiste.
"""

from __future__ import annotations

from test_scopes import _window

from core.project_model import Clip, MediaAsset, Project, Track
from core.tracking_model import ClipTracking, Sample, SampleStatus, TrackData, Tracker, TrackLink
from core.tracking_ops import tracking_dependents


def _tracker() -> Tracker:
    samples = {i: Sample(20.0 + i, 30.0, 1.0, SampleStatus.TRACKED) for i in range(60)}
    return Tracker(id="t1", data=TrackData.from_samples(10.0, samples, source_size=(160, 90)))


def _project(*, enabled: bool = True) -> Project:
    media = MediaAsset("m", "/tmp/m.mp4", "m", 10.0, 160, 90, 10.0, "video", False)
    source = Clip("source", "m", "V1", 0.0, 0.0, 6.0)
    source.tracking = ClipTracking(trackers=(_tracker(),))
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


def test_cutting_a_tracking_source_says_the_followers_now_follow_both_parts(qtbot, monkeypatch):
    window = _window(qtbot, monkeypatch)
    window.project = _project()
    window.timeline_panel.set_project(window.project)
    window.statusBar().clearMessage()

    window.cut_selected_clip("source", 3.0)

    message = window.statusBar().currentMessage()
    assert "tracking" in message and "1 autre" in message and "deux parties" in message
    assert len(window.project.tracks[0].clips) == 2                        # la coupe a bien eu lieu
    link = window.project.tracks[1].clips[0].tracking.links[0]
    assert link.continuation_ids == ("source-split-2",)                    # et la liaison suit la moitié droite


def test_cutting_names_the_gap_left_by_a_source_that_does_not_cover_the_follower(qtbot, monkeypatch):
    window = _window(qtbot, monkeypatch)
    window.project = _project()
    window.project.tracks[0].clips[0].source_out = 4.0                     # la source s'arrête avant la fin du suiveur
    window.timeline_panel.set_project(window.project)
    window.statusBar().clearMessage()

    window.cut_selected_clip("source", 2.0)

    message = window.statusBar().currentMessage()
    assert "ne couvre pas toute" in message and "figé" in message


def test_deleting_the_only_source_announces_the_broken_links(qtbot, monkeypatch):
    window = _window(qtbot, monkeypatch)
    window.project = _project()
    window.timeline_panel.set_project(window.project)
    window.statusBar().clearMessage()

    window.delete_selected_clip("source")

    message = window.statusBar().currentMessage()
    assert "1 clip(s) suivaient" in message and "interrompue" in message


def test_deleting_one_half_when_the_other_still_covers_the_follower_stays_silent(qtbot, monkeypatch):
    window = _window(qtbot, monkeypatch)
    window.project = _project()
    window.project.tracks[1].clips[0].source_out = 2.0                     # le suiveur ne voit que la moitié gauche
    window.timeline_panel.set_project(window.project)
    window.cut_selected_clip("source", 3.0)
    window.statusBar().clearMessage()

    window.delete_selected_clip("source-split-2")

    assert window.statusBar().currentMessage() == ""


def test_cutting_an_ordinary_clip_stays_silent(qtbot, monkeypatch):
    window = _window(qtbot, monkeypatch)
    window.project = _project()
    window.timeline_panel.set_project(window.project)
    window.statusBar().clearMessage()

    window.cut_selected_clip("follower", 3.0)                              # personne ne le suit

    assert "tracking" not in window.statusBar().currentMessage()


def test_the_tracking_panel_flags_a_link_whose_source_does_not_cover_the_follower(qtbot, monkeypatch):
    window = _window(qtbot, monkeypatch)
    window.project = _project()
    follower = window.project.tracks[1].clips[0]
    link = follower.tracking.links[0]
    assert window._describe_link(follower, link)["warning"] == ""          # source complète : rien à signaler

    window.project.tracks[0].clips[0].source_out = 4.0

    assert "ne couvre pas toute la durée" in window._describe_link(follower, link)["warning"]


def test_every_way_of_deleting_the_source_announces_the_broken_links(qtbot, monkeypatch):
    """Suppression simple, ripple et multiple : la même annonce (un seul point d'entrée côté fenêtre)."""
    for action in ("ripple", "selection"):
        window = _window(qtbot, monkeypatch)
        window.project = _project()
        window.timeline_panel.set_project(window.project)
        window.timeline_panel.selected_clip_id = "source"
        window.statusBar().clearMessage()

        if action == "ripple":
            window.ripple_delete_selected_clip()
        else:
            window.timeline_panel.selected_clip_ids = {"source", "inconnu"}
            window.delete_selected_clip_with_check()

        message = window.statusBar().currentMessage()
        assert "1 clip(s) suivaient" in message, action
