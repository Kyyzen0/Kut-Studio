"""Photos dans les emplacements depuis l'interface : dépôt depuis le Finder, menu Photos, diaporama, annulation."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QDragMoveEvent, QDropEvent
from PySide6.QtWidgets import QDialog

from core.beat_grid import BeatGrid
from core.template_slots import empty_slots, is_photo_slot
from ui import i18n
from ui.social_dialogs import SocialProjectChoice, SocialProjectDialog


@pytest.fixture
def window(qtbot, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QMessageBox

    from ui.main_window import MainWindow

    monkeypatch.setenv("KUT_STUDIO_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr("ui.main_window.QMessageBox.question", lambda *_a, **_k: QMessageBox.Yes)
    monkeypatch.setattr("ui.main_window.QMessageBox.warning", lambda *_a, **_k: QMessageBox.Discard)
    win = MainWindow()
    qtbot.addWidget(win)
    win.timeline_timer.stop()
    monkeypatch.setattr(win.preview_panel, "preview_at", lambda *_a, **_k: None)
    win.resize(1400, 900)
    win.show()
    return win


@pytest.fixture
def photos(qapp, tmp_path):
    from PySide6.QtGui import QColor, QImage

    paths = []
    for index, color in enumerate(("#E0302A", "#2AE05A", "#2A6AE0")):
        image = QImage(320, 240, QImage.Format_RGB32)
        image.fill(QColor(color))
        path = tmp_path / f"photo-{index}.jpg"
        assert image.save(str(path))
        paths.append(str(path))
    return paths


def _from_template(window, monkeypatch, template_id: str = "city_lights") -> None:
    monkeypatch.setattr(SocialProjectDialog, "exec", lambda self: QDialog.Accepted)
    monkeypatch.setattr(SocialProjectDialog, "choice",
                        lambda self: SocialProjectChoice("Run", "vertical", 30.0, "tiktok", template_id))
    window.new_social_project()
    window.history.reset(window.project)


def _choose(monkeypatch, paths) -> None:
    monkeypatch.setattr("ui.main_window.QFileDialog.getOpenFileNames", lambda *_a, **_k: (list(paths), ""))


def _slots(window):
    return sorted((clip for clip in window.project.tracks[0].clips if clip.template_slot),
                  key=lambda clip: clip.timeline_start)


def _steps(window) -> int:
    return len(window.history._undo_stack)


def _mime(paths) -> QMimeData:
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(path) for path in paths])
    return mime


def _panel_point(window, track_index: int, seconds: float) -> QPoint:
    panel = window.timeline_panel
    track = window.project.tracks[track_index]
    x = panel.left_margin + seconds * panel.pixels_per_second * panel.zoom
    y = panel.row_top(track_index) + panel.clip_height_of(track) / 2
    return panel.timeline_grid.mapTo(panel, QPoint(int(x), int(y)))


def _drop(window, paths, point: QPoint) -> bool:
    panel = window.timeline_panel
    mime = _mime(paths)
    move = QDragMoveEvent(point, Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    panel.dragMoveEvent(move)
    if not move.isAccepted():
        return False
    drop = QDropEvent(QPointF(point), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    panel.dropEvent(drop)
    return drop.isAccepted()


def test_photos_dropped_on_a_slot_fill_it_and_the_next_empty_ones_in_one_undo_step(window, monkeypatch, photos):
    _from_template(window, monkeypatch)
    slots = _slots(window)
    assert _drop(window, photos[:2], _panel_point(window, 0, slots[2].timeline_start + 0.5))

    filled = [clip.id for clip in _slots(window) if is_photo_slot(clip)]
    assert filled == [slots[2].id, slots[3].id]
    assert window.history.undo_label == i18n.translate("history.template.fill_photos", count=2)
    assert _steps(window) == 2
    window.undo_last()
    assert not any(is_photo_slot(clip) for clip in _slots(window))
    assert len(empty_slots(window.project)) == len(slots)


def test_photos_are_refused_outside_a_slot_and_other_files_are_left_to_the_window(window, monkeypatch, photos,
                                                                                tmp_path):
    _from_template(window, monkeypatch)
    end = max(clip.timeline_start + clip.duration for clip in _slots(window))
    assert not _drop(window, photos, _panel_point(window, 0, end + 3.0)), "pas d'emplacement sous le pointeur"
    movie = tmp_path / "clip.mov"
    movie.write_bytes(b"\x00")
    assert window.timeline_panel._dropped_payload(_mime([str(movie)])) is None, "un fichier vidéo : la fenêtre l'importe"
    assert window.timeline_panel._dropped_payload(_mime([*photos, str(movie)])) is None
    assert _steps(window) == 1


def test_the_photos_menu_fills_the_empty_slots_or_says_there_is_none(window, monkeypatch, photos):
    _from_template(window, monkeypatch, "cta_comments")
    _choose(monkeypatch, photos)
    window.fill_slots_with_chosen_photos()
    (slot,) = _slots(window)
    assert is_photo_slot(slot) and slot.graphic.source_path.endswith("photo-0.jpg")
    assert window.history.undo_label == i18n.translate("history.template.fill_photos", count=1)

    window.timeline_panel.selection_cleared.emit()
    window.timeline_panel.selected_clip_id = None
    window.fill_slots_with_chosen_photos()
    assert window.statusBar().currentMessage() == i18n.translate("template.message.no_empty_slot")
    window.timeline_panel.select_clip(slot.id)
    _choose(monkeypatch, photos[1:])
    window.fill_slots_with_chosen_photos()
    assert _slots(window)[0].graphic.source_path.endswith("photo-1.jpg"), "un emplacement sélectionné est re-rempli"


def test_a_photo_slideshow_lands_on_the_bars_of_the_grid_in_one_undo_step(window, monkeypatch, photos):
    window.project.active_sequence.beat_grid = BeatGrid(bpm=120.0)
    window.history.reset(window.project)
    window.playhead_seconds = 0.3
    _choose(monkeypatch, photos)
    window.add_photo_slideshow()

    clips = sorted((clip for track in window.project.tracks for clip in track.clips if clip.id.startswith("photo-")),
                   key=lambda clip: clip.timeline_start)
    assert [(clip.timeline_start, clip.duration) for clip in clips] == [(0.5, 2.0), (2.5, 2.0), (4.5, 2.0)]
    assert all(is_photo_slot(clip) for clip in clips)
    assert window.history.undo_label == i18n.translate("history.template.slideshow", count=3)
    assert window.timeline_panel.selected_clip_id == clips[0].id
    window.undo_last()
    assert not any(clip.id.startswith("photo-") for track in window.project.tracks for clip in track.clips)


def test_the_photo_commands_are_in_the_social_photos_menu_and_rebindable(window, monkeypatch, photos):
    from PySide6.QtWidgets import QMenu

    from core.shortcuts import COMMANDS_BY_ID

    assert {"photo_fill_slots", "photo_slideshow"} <= set(COMMANDS_BY_ID)
    photos_menu = next(action.menu() for action in window.social_menu.actions()
                       if action.menu() is not None and action.text() == i18n.translate("social.menu.photos"))
    assert isinstance(photos_menu, QMenu)
    entries = {action.text().split("\t")[0]: action for action in photos_menu.actions()}
    slideshow = entries[i18n.translate("template.photos.slideshow_menu")]
    assert i18n.translate("template.photos.fill_menu") in entries
    _choose(monkeypatch, photos[:1])
    slideshow.trigger()
    assert window.history.undo_label == i18n.translate("history.template.slideshow", count=1)


def _plain_slot(window, photos, *, start: float = 60.0):
    """Un emplacement rempli d'une photo sur V1, loin des clips du projet d'exemple."""
    from core.project_model import Clip
    from core.template_slots import fill_slot_with_photo

    v1 = window.project.tracks[0]
    v1.clips.append(Clip(id="slot", asset_id="", track_id=v1.id, timeline_start=start, source_in=0.0,
                         source_out=2.0, label="01", template_slot="slot-01"))
    fill_slot_with_photo(window.project, "slot", photos[0], (320, 240))
    window._reload_timeline_preserving_selection()
    window._update_timeline_duration()                  # la tête de lecture est bornée à la durée de la timeline
    return v1


def test_the_live_monitor_does_not_draw_a_photo_that_a_higher_video_track_covers(window, monkeypatch, photos, tmp_path):
    from core.project_model import Clip, MediaAsset, Track

    _plain_slot(window, photos)
    seen = []
    monkeypatch.setattr(window.preview_panel, "set_graphics_present", lambda present: seen.append(present))
    window.playhead_seconds = 60.5
    assert window.playhead_seconds == 60.5
    window._refresh_viewer_graphics()
    assert seen[-1] is True, "seule à cet instant, la photo est dessinée par le moniteur"
    window.project.media_assets.append(MediaAsset("top", str(tmp_path / "top.mp4"), "top", 9.0, 320, 240, 30.0,
                                                  "video"))
    window.project.tracks.insert(1, Track(id="VX", name="VX", type="video", clips=[
        Clip(id="above", asset_id="top", track_id="VX", timeline_start=60.0, source_in=0.0, source_out=2.0)]))
    window._reload_timeline_preserving_selection()
    window._refresh_viewer_graphics()
    assert seen[-1] is False, "la vidéo de la piste au-dessus la couvre : rien à dessiner par-dessus"


def test_dropping_a_library_photo_whose_file_is_gone_reports_it(window, photos, tmp_path):
    from core.project_model import MediaAsset

    v1 = _plain_slot(window, photos)
    slot = next(clip for clip in v1.clips if clip.id == "slot")
    before = slot.asset_id
    window.project.media_assets.append(MediaAsset("gone", str(tmp_path / "partie.jpg"), "partie", 0.0, 320, 240, 30.0,
                                                  "image"))
    window.history.reset(window.project)
    window.on_asset_dropped("gone", v1.id, 60.5)
    assert slot.asset_id == before
    assert window.statusBar().currentMessage() == i18n.translate("template.message.photo_missing", name="partie")
    assert _steps(window) == 1

