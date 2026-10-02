"""Interface du tracking : panneau Suivi, viewer, analyse en tâche de fond, historique."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt

from core.project_model import Clip, MediaAsset, Project, Track
from core.tracking_model import SampleStatus, TrackTarget
from tests.tracking_media import HAS_FFMPEG, moving_points_video

pytest.importorskip("numpy")
pytestmark = pytest.mark.skipif(not HAS_FFMPEG, reason="FFmpeg absent")

W, H, FPS, FRAMES = 320, 180, 25.0, 30


def _points(i):
    return [(80.0 + 2.0 * i, 60.0 + 1.0 * i)]


@pytest.fixture(scope="module")
def video(tmp_path_factory):
    return moving_points_video(
        tmp_path_factory.mktemp("tracking-ui") / "clip.mkv", width=W, height=H, fps=FPS, count=FRAMES,
        points_at=_points,
    )


@pytest.fixture
def window(qtbot, monkeypatch, video, tmp_path):
    from PySide6.QtWidgets import QMessageBox

    from ui.main_window import MainWindow

    monkeypatch.setenv("KUT_STUDIO_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr("ui.main_window.QMessageBox.information", lambda *_a, **_k: None)
    monkeypatch.setattr("ui.main_window.QMessageBox.question", lambda *_a, **_k: QMessageBox.Yes)
    win = MainWindow()
    qtbot.addWidget(win)
    win.timeline_timer.stop()
    monkeypatch.setattr(win.preview_panel, "preview_at", lambda *_a, **_k: None)
    project = Project(name="Suivi", width=W, height=H, fps=FPS)
    project.media_assets.append(MediaAsset("a", str(video), "clip", FRAMES / FPS, W, H, FPS, "video"))
    project.tracks.insert(0, Track("V1", "V1", "video", clips=[Clip("v", "a", "V1", 0.0, 0.0, FRAMES / FPS)]))
    win.project = project
    win._timeline_index = None
    win._reload_timeline_preserving_selection()
    win.history.reset(win.project)
    win.resize(1400, 900)
    win.show()
    win._restore_clip_selection("v")
    win.show_tracking_panel()
    return win


def _clip(win, clip_id="v"):
    from core.timeline_operations import find_clip

    return find_clip(win.project, clip_id)


def _tracking(win):
    return _clip(win).tracking


def _wait_jobs(qtbot, win):
    qtbot.waitUntil(lambda: not win._tracking_jobs, timeout=20000)


def test_tracking_tab_shows_panel_and_viewer_trackers(window):
    panel = window.tracking_panel
    assert panel.isVisible() and panel.tracker_box.isVisible()
    entries = len(window.history)
    panel.add_button.click()
    assert len(window.history) == entries + 1
    tracker = _tracking(window).trackers[0]
    assert panel.tracker_list.count() == 1 and panel.tracker_list.item(0).text() == tracker.name
    overlay = window.preview_panel.tracking_overlay
    assert overlay.isVisible() and overlay.active
    assert overlay.trackers[0].point == pytest.approx((W / 2, H / 2))
    # Les poignées de transform du clip cèdent la place aux trackers.
    assert window.preview_panel.overlay.selection is None
    # Double-clic dans le viewer : nouveau tracker à cet endroit.
    overlay.add_requested.emit(80.0, 60.0)
    assert len(_tracking(window).trackers) == 2
    assert _tracking(window).trackers[1].data.sample(0).x == pytest.approx(80.0)
    # Retour à l'onglet Clip : plus de trackers dans le viewer.
    window.properties_panel._select_inspector_tab(0)
    assert not overlay.active


def test_viewer_drag_corrects_point_and_resizes_zones_with_one_entry_each(window):
    window.add_tracker_at_playhead(80.0, 60.0)
    tracker = _tracking(window).trackers[0]
    overlay = window.preview_panel.tracking_overlay
    entries = len(window.history)
    for step in range(1, 5):
        overlay.point_dragged.emit(tracker.id, 80.0 + step, 60.0)
    overlay.drag_finished.emit(tracker.id, "point")
    assert len(window.history) == entries + 1
    data = _tracking(window).tracker(tracker.id).data
    assert data.sample(0).x == pytest.approx(84.0) and data.status_at(0) is SampleStatus.MANUAL
    overlay.zone_dragged.emit(tracker.id, "search", 90.0, 70.0)
    overlay.drag_finished.emit(tracker.id, "search")
    settings = _tracking(window).tracker(tracker.id).settings
    assert (settings.search_width, settings.search_height) == (90.0, 70.0)
    assert len(window.history) == entries + 2
    window.undo_last()
    assert _tracking(window).tracker(tracker.id).settings.search_width != 90.0


def test_background_analysis_is_one_history_entry_and_undoable(window, qtbot):
    window.add_tracker_at_playhead(80.0, 60.0)
    panel = window.tracking_panel
    entries = len(window.history)
    panel.forward_button.click()
    assert window._tracking_jobs  # lancée hors du thread de l'interface
    assert panel.stop_button.isEnabled() and not panel.forward_button.isEnabled()
    _wait_jobs(qtbot, window)
    assert len(window.history) == entries + 1
    tracker = _tracking(window).trackers[0]
    assert len(tracker.data.valid_indices()) == FRAMES
    assert "fin du clip" in panel.status.text()
    assert len(window.preview_panel.tracking_overlay.trackers[0].path) == FRAMES
    window.undo_last()
    assert len(_tracking(window).trackers[0].data.valid_indices()) == 1
    window.redo_last()
    assert len(_tracking(window).trackers[0].data.valid_indices()) == FRAMES


def test_link_and_bake_from_the_panel(window, qtbot):
    window.add_tracker_at_playhead(80.0, 60.0)
    window.tracking_panel.forward_button.click()
    _wait_jobs(qtbot, window)
    window.add_layer_at_playhead("text", "")
    text = next(c for t in window.project.tracks if t.type == "graphics" for c in t.clips)
    window._restore_clip_selection("v")
    window.show_tracking_panel()  # ajouter un calque ouvre l'onglet Graphiques
    panel = window.tracking_panel
    labels = [panel.target.itemText(i) for i in range(panel.target.count())]
    assert any("transform" in label for label in labels)
    index = next(i for i in range(panel.target.count())
                 if (panel.target.itemData(i) or {}).get("clip_id") == text.id)
    panel.target.setCurrentIndex(index)
    entries = len(window.history)
    panel.link_button.click()
    assert len(window.history) == entries + 1
    link = text.tracking.links[0]
    assert link.target == TrackTarget.TRANSFORM and link.source_clip_id == "v"
    # L'inspecteur du calque lié liste la liaison ; « Figer » la convertit en images-clés.
    window._restore_clip_selection(text.id)
    window.show_tracking_panel()
    assert panel.links_box.isVisible() and not panel.tracker_box.isVisible()
    assert panel.links_list.count() == 1 and panel.freeze_button.isEnabled()
    panel.freeze_button.click()
    assert text.tracking is None and len(text.transform_keyframes) >= 2


def test_stabilization_toggle_reports_zoom(window, qtbot):
    window.add_tracker_at_playhead(80.0, 60.0)
    window.tracking_panel.forward_button.click()
    _wait_jobs(qtbot, window)
    panel = window.tracking_panel
    assert panel.stab_enabled.isEnabled()
    panel.stab_enabled.setChecked(True)
    stab = _tracking(window).stabilization
    assert stab is not None and stab.enabled
    panel.stab_smoothing.setCurrentIndex(panel.stab_smoothing.findData("locked"))
    assert _tracking(window).stabilization.smoothing == "locked"
    assert "%" in panel.stab_info.text()  # « La stabilisation agrandit l'image de … % »


def test_stop_cancels_and_project_switch_abandons_jobs(window, qtbot, monkeypatch):
    window.add_tracker_at_playhead(80.0, 60.0)
    started = []

    def slow_schedule(key, fn, **_kwargs):  # la tâche reste en file : on peut l'arrêter à coup sûr
        started.append(fn)

    monkeypatch.setattr(window.runtime, "schedule_analysis", slow_schedule)
    window.tracking_panel.forward_button.click()
    job = window._tracking_jobs["v"]
    window.tracking_panel.stop_button.click()
    assert job.cancelled
    started[0](None)  # le worker la prend : elle s'arrête aussitôt
    _wait_jobs(qtbot, window)
    assert "Arrêté" in window.tracking_panel.status.text()
    window.tracking_panel.forward_button.click()
    job = window._tracking_jobs["v"]
    window._cancel_tracking_jobs()
    assert job.cancelled and not window._tracking_jobs


def test_tracking_panel_lists_trackers_with_visibility_and_rename(window, qtbot):
    window.add_tracker_at_playhead(80.0, 60.0)
    window.add_tracker_at_playhead(200.0, 100.0)
    panel = window.tracking_panel
    entries = len(window.history)
    panel.tracker_list.item(1).setText("Œil droit")
    qtbot.waitUntil(lambda: _tracking(window).trackers[1].name == "Œil droit")
    panel.tracker_list.item(1).setCheckState(Qt.Unchecked)
    qtbot.waitUntil(lambda: not _tracking(window).trackers[1].visible)
    assert len(window.history) == entries + 2
    assert len(window.preview_panel.tracking_overlay.trackers) == 1
    colors = {t.color for t in _tracking(window).trackers}
    assert len(colors) == 2


def test_dragging_a_stabilized_clip_edits_the_user_transform(window, qtbot):
    from core.tracking_model import BorderMode, Smoothing

    window.add_tracker_at_playhead(80.0, 60.0)
    window.tracking_panel.forward_button.click()
    _wait_jobs(qtbot, window)
    from core import tracking_ops as ops

    ops.set_stabilization(window.project, "v", tracker_ids=(_tracking(window).trackers[0].id,),
                          smoothing=Smoothing.LOCKED, borders=BorderMode.BLACK)
    window._commit_layer_edit("Stabilisation")
    window.seek_to_position(20 / FPS)
    window.properties_panel._select_inspector_tab(0)  # mode transform : poignées du clip
    window._refresh_viewer_graphics()
    selection = window.preview_panel.overlay.selection
    assert selection is not None
    # Les valeurs à éditer sont la saisie (0), pas la position stabilisée (≈ −40 px).
    assert selection.values["position_x"] == pytest.approx(0.0)
    overlay = window.preview_panel.overlay
    overlay.transform_dragged.emit("v", {"position_x": selection.values["position_x"] + 0.1})
    overlay.transform_released.emit("v", "Déplacer le calque")
    assert _clip(window).transform.position_x == pytest.approx(0.1)
