"""« Recadrer en suivant le tracker » dans la fenêtre : menu Réseaux sociaux, une entrée d'historique, refus expliqués."""

from __future__ import annotations

import pytest
from multicam_stubs import keep_preview_player_off_the_disk
from test_follow_reframe import FPS, SEQ_H, SEQ_W, SRC_H, SRC_W, _crossing, _tracker

from core.project_model import Clip, MediaAsset, Project, Track
from core.tracking_model import ClipTracking
from ui import i18n


@pytest.fixture
def window(qtbot, monkeypatch, tmp_path):
    from ui.main_window import MainWindow

    i18n.set_language("fr")
    monkeypatch.setattr("ui.main_window.QMessageBox.information", lambda *_, **__: None)
    monkeypatch.setattr("ui.main_window.QMessageBox.critical", lambda *_, **__: None)
    main = MainWindow()
    qtbot.addWidget(main)
    main.timeline_timer.stop()
    keep_preview_player_off_the_disk(main, monkeypatch)
    asset = MediaAsset(id="a", path=str(tmp_path / "plan.mp4"), name="plan", duration=2.0, width=SRC_W, height=SRC_H,
                       fps=float(FPS), media_type="video")
    clip = Clip(id="c", asset_id="a", track_id="V1", timeline_start=0.0, source_in=0.0, source_out=2.0)
    project = Project(name="9:16", width=SEQ_W, height=SEQ_H, fps=float(FPS), media_assets=[asset],
                      tracks=[Track(id="V1", name="V1", type="video", clips=[clip])])
    main.project = project
    main.history.reset(project)
    main.timeline_panel.set_project(project)
    main._update_timeline_duration()
    main.timeline_panel.selected_clip_id = "c"
    return main


def _clip(window) -> Clip:
    return window.project.tracks[0].clips[0]


def _pan_frames(window) -> list:
    return [frame for frame in _clip(window).transform_keyframes if frame.property_name == "pan_x"]


def test_the_command_sits_in_the_social_menu(window):
    labels = [action.text() for action in window.social_menu.actions()]
    assert i18n.translate("social.menu.follow_reframe") in labels


def test_the_selected_clip_follows_its_tracker_in_one_undo_step(window):
    _clip(window).tracking = ClipTracking(trackers=(_tracker(_crossing),))
    window.history.reset(window.project)
    window.follow_subject_in_selection()
    assert _clip(window).transform.fill is True and len(_pan_frames(window)) >= 2
    assert window.statusBar().currentMessage() == i18n.translate("social.message.reframed", count=1)
    window.undo_last()
    assert _clip(window).transform.fill is False and _pan_frames(window) == []


def test_without_tracker_the_user_is_told_what_to_do(window):
    window.follow_subject_in_selection()
    assert window.statusBar().currentMessage() == i18n.translate("social.message.reframe_no_tracker")
    assert _clip(window).transform.fill is False


def test_an_unanalysed_tracker_is_explained(window):
    from core.tracking_model import Tracker

    _clip(window).tracking = ClipTracking(trackers=(Tracker(id="vide"),))
    window.follow_subject_in_selection()
    assert window.statusBar().currentMessage() == i18n.translate("social.message.reframe_no_positions")


def test_without_selection_nothing_happens(window):
    window.timeline_panel.selected_clip_id = None
    window.follow_subject_in_selection()
    assert window.statusBar().currentMessage() == i18n.translate("social.message.no_video")
