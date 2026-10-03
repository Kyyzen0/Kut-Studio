"""Réglages d'une source Multicam : angles (nom, couleur, décalage), politique audio, ajout, suppression, relink, resynchronisation.

Chaque réglage est une opération de ``core`` annulable (une entrée d'historique) ; la boîte se relit après chacun, y compris
après Annuler / Rétablir. Les médias de la resynchronisation sont de vrais fichiers synthétiques décodés par FFmpeg.
"""

from __future__ import annotations

import shutil

import numpy as np
import pytest
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt

from audio_scenes import speech
from core.multicam import angle_offset
from multicam_stubs import keep_preview_player_off_the_disk
from core.multicam_model import AudioMode, MulticamAudio, SyncStatus
from core.multicam_ops import AngleSpec, create_multicam_source, insert_multicam_clip, switch_angle
from core.project_model import MediaAsset, Project, Track
from test_multicam_creation import _media_project
from ui import i18n


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


def _asset(name: str, *, kind: str = "video", path: str = "") -> MediaAsset:
    if kind == "audio":
        return MediaAsset(name, path or f"/media/{name}.wav", name, 60.0, 0, 0, 0.0, "audio", True)
    return MediaAsset(name, path or f"/media/{name}.mp4", name, 60.0, 1920, 1080, 25.0, "video", True)


def _load(window, project: Project) -> None:
    window.project = project
    window.history.reset(project)
    window.timeline_panel.set_project(project)
    window._update_timeline_duration()


def _setup(window, tmp_path=None):
    project = Project("p", media_assets=[_asset("camA"), _asset("camB"), _asset("camC"), _asset("rec", kind="audio"),
                                         _asset("spare")],
                      tracks=[Track("V1", "V1", "video"), Track("A1", "A1", "audio")])
    source = create_multicam_source(
        project, [AngleSpec(asset_id="camA", name="Wide"), AngleSpec(asset_id="camB", name="Close-up", offset=2.0),
                  AngleSpec(asset_id="camC", name="Drone", offset=1.0)], name="Concert")
    segment = insert_multicam_clip(project, source.id, "V1", 0.0, angle_id="angle-2")
    _load(window, project)
    return project, source, segment


def _open(window, source):
    window.show_multicam_settings(source.id)
    return window._multicam_settings        # noqa: SLF001


def test_the_dialog_lists_every_angle_with_its_offset_and_status(window):
    _project, source, _segment = _setup(window)
    dialog = _open(window, source)
    rows = dialog.row_widgets()
    assert [row.name_edit.text() for row in rows] == ["Wide", "Close-up", "Drone"]
    assert [round(row.offset_spin.value(), 3) for row in rows] == [0.0, 2.0, 1.0]
    assert dialog.source_label.text() == "Concert"
    assert rows[0].status_label.text()                                     # un état est toujours dit
    assert all(row.relink_button.isVisibleTo(dialog) for row in rows)          # médias fictifs : tous hors ligne, donc « Relier… »
    assert dialog.audio_combo.count() >= 3 and dialog.audio_combo.currentIndex() == 0


def test_the_source_is_found_from_the_selected_segment_the_playhead_or_the_only_source(window):
    project, source, segment = _setup(window)
    window.timeline_panel._set_selection([segment.id], segment.id, announce=False)
    assert window._multicam_source_for_settings(None) == source.id         # noqa: SLF001
    window.timeline_panel._set_selection([], None, announce=False)
    window.playhead_seconds = 5.0
    assert window._multicam_source_for_settings(None) == source.id         # noqa: SLF001
    window.playhead_seconds = 500.0
    assert window._multicam_source_for_settings(None) == source.id         # la seule source du projet
    other = create_multicam_source(project, [AngleSpec(asset_id="camA"), AngleSpec(asset_id="camB")], name="Autre")
    assert window._multicam_source_for_settings(None) is None              # deux sources : il faut choisir
    window.show_multicam_settings()
    assert window.statusBar().currentMessage() == "Ce projet ne contient aucune source Multicam."
    assert other.multicam is not None


def test_renaming_an_angle_is_one_undoable_entry_and_the_dialog_follows(window):
    project, source, _segment = _setup(window)
    dialog = _open(window, source)
    row = dialog.row_widgets()[1]
    row.name_edit.setText("  Plan serré ")
    row.name_edit.editingFinished.emit()
    assert source.multicam.angles[1].name == "Plan serré" and source.tracks[1].name == "Plan serré"
    assert window.history.undo_label == "Renommer l'angle « Close-up »"
    assert dialog.row_widgets()[1].name_edit.text() == "Plan serré"
    window.undo_last()
    assert window.project.get_sequence(source.id).multicam.angles[1].name == "Close-up"
    assert dialog.row_widgets()[1].name_edit.text() == "Close-up"            # la boîte se relit après Annuler
    window.redo_last()
    assert dialog.row_widgets()[1].name_edit.text() == "Plan serré"


def test_an_empty_or_unchanged_name_does_nothing(window):
    _project, source, _segment = _setup(window)
    dialog = _open(window, source)
    entries = len(window.history)
    row = dialog.row_widgets()[0]
    row.name_edit.setText("   ")
    row.name_edit.editingFinished.emit()
    assert len(window.history) == entries and window.project.get_sequence(source.id).multicam.angles[0].name == "Wide"
    assert dialog.row_widgets()[0].name_edit.text() == "Wide"


def test_the_offset_is_corrected_by_typing_a_value_and_moves_the_clips(window):
    _project, source, _segment = _setup(window)
    dialog = _open(window, source)
    row = dialog.row_widgets()[2]
    row.offset_spin.setValue(3.25)
    row.offset_spin.editingFinished.emit()
    live = window.project.get_sequence(source.id)
    assert angle_offset(live, live.multicam.angles[2]) == pytest.approx(3.25)
    assert live.multicam.angles[2].sync_status is SyncStatus.MANUAL
    assert window.history.undo_label == "Décaler l'angle « Drone »"
    window.undo_last()
    undone = window.project.get_sequence(source.id)
    assert angle_offset(undone, undone.multicam.angles[2]) == pytest.approx(1.0)
    assert dialog.row_widgets()[2].offset_spin.value() == pytest.approx(1.0)


def test_a_refused_setting_says_so_and_the_dialog_shows_the_real_value_again(window):
    _project, source, _segment = _setup(window)
    dialog = _open(window, source)
    assert window.set_multicam_angle_offset("angle-1", -4.0) is False
    assert "négatif" in window.statusBar().currentMessage()
    assert dialog.row_widgets()[0].offset_spin.value() == 0.0
    assert window.history.undo_label is None or "Décaler" not in window.history.undo_label


def test_the_colour_button_cycles_the_angle_colour(window):
    from ui.theme import active_palette

    _project, source, _segment = _setup(window)
    dialog = _open(window, source)
    before = source.multicam.angles[0].color_index
    dialog.row_widgets()[0].color_button.click()
    live = window.project.get_sequence(source.id)
    assert live.multicam.angles[0].color_index == (before + 1) % len(active_palette().angle_colors)
    assert window.history.undo_label == "Changer la couleur de l'angle « Wide »"


def test_the_audio_policy_is_chosen_in_the_dialog_and_undone_with_the_history(window):
    _project, source, _segment = _setup(window)
    dialog = _open(window, source)
    labels = [dialog.audio_combo.itemText(i) for i in range(dialog.audio_combo.count())]
    assert labels[0] == "Le son suit l'image" and "Son de « Drone » en continu" in labels
    dialog.audio_combo.setCurrentIndex(labels.index("Son de « Drone » en continu"))
    live = window.project.get_sequence(source.id)
    assert live.multicam.audio == MulticamAudio(AudioMode.FIXED, ("angle-3",))
    assert window.history.undo_label == "Changer la politique audio Multicam"
    window.undo_last()
    assert window.project.get_sequence(source.id).multicam.audio.mode is AudioMode.FOLLOW_VIDEO
    assert dialog.audio_combo.currentIndex() == 0
    dialog.audio_combo.setCurrentIndex(len(labels) - 1)                          # tout mixer
    assert window.project.get_sequence(source.id).multicam.audio.mode is AudioMode.MIX


def test_an_angle_can_be_added_from_the_library_media_and_removed_with_its_segments_redirected(window, monkeypatch):
    project, source, segment = _setup(window)
    dialog = _open(window, source)
    texts = [action.text() for action in dialog._add_menu.actions()]               # noqa: SLF001
    assert "spare" in texts and "camA" not in texts
    window.add_multicam_angle("spare")
    live = window.project.get_sequence(source.id)
    assert [a.name for a in live.multicam.angles][-1] == "spare" and window.history.undo_label == "Ajouter l'angle « spare »"
    assert len(dialog.row_widgets()) == 4
    # supprimer l'angle montré par le segment : on demande par quoi le remplacer
    asked = []
    monkeypatch.setattr(window, "_ask_replacement_angle", lambda name, candidates: asked.append(name) or candidates[0].id)
    window.remove_multicam_angle("angle-2")
    live = window.project.get_sequence(source.id)
    assert asked == ["Close-up"] and "angle-2" not in {a.id for a in live.multicam.angles}
    assert next(c for t in window.project.tracks for c in t.clips if c.id == segment.id).angle_id == "angle-1"
    window.undo_last()
    assert "angle-2" in {a.id for a in window.project.get_sequence(source.id).multicam.angles}
    assert len(dialog.row_widgets()) == 4


def test_cancelling_the_replacement_choice_keeps_the_angle(window, monkeypatch):
    _project, source, _segment = _setup(window)
    _open(window, source)
    monkeypatch.setattr(window, "_ask_replacement_angle", lambda *_a: None)
    assert window.remove_multicam_angle("angle-2") is False
    assert "angle-2" in {a.id for a in window.project.get_sequence(source.id).multicam.angles}


def test_an_offline_angle_offers_relink_and_the_others_stay_usable(window, monkeypatch):
    _project, source, _segment = _setup(window)
    relinked = []
    monkeypatch.setattr(window, "_on_asset_relink_requested", lambda asset_id: relinked.append(asset_id), raising=False)
    dialog = _open(window, source)
    row = dialog.row_widgets()[0]
    assert row.status_label.text() == "MÉDIA HORS LIGNE" and row.relink_button.isVisibleTo(dialog)   # médias factices introuvables
    row.relink_button.click()
    assert relinked == ["camA"]


def test_the_last_angle_cannot_be_removed_from_the_dialog(window):
    project = Project("p", media_assets=[_asset("camA")], tracks=[Track("V1", "V1", "video")])
    source = create_multicam_source(project, [AngleSpec(asset_id="camA")], name="Seule")
    _load(window, project)
    dialog = _open(window, source)
    assert not dialog.row_widgets()[0].remove_button.isEnabled()


def test_the_settings_open_from_the_command_the_viewer_and_the_clip_menu_and_close_with_the_window(window):
    _project, source, segment = _setup(window)
    window.timeline_panel._set_selection([segment.id], segment.id, announce=False)
    window.timeline_panel.multicam_settings_requested.emit()
    assert window._multicam_settings is not None and window._multicam_settings.isVisible()   # noqa: SLF001
    window._close_multicam_settings()                                                # noqa: SLF001
    assert window._multicam_settings is None                                          # noqa: SLF001
    window.multicam_viewer.settings_button.click()
    assert window._multicam_settings is not None and window._multicam_settings_source == source.id    # noqa: SLF001
    window.multicam_viewer.shutdown()
    window._close_multicam_settings()                                                # noqa: SLF001
    assert window.shortcuts.shortcut_map.sequences("multicam_settings") == ()          # sans raccourci par défaut


# --- resynchronisation par le son (vrais médias) ------------------------------------------------------------------------------


needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg absent")


@pytest.fixture(scope="module")
def scene():
    return speech(np.random.default_rng(8), 60.0)


@needs_ffmpeg
def test_resynchronising_by_sound_moves_the_angles_and_is_one_undoable_entry(window, monkeypatch, tmp_path, scene):
    project = _media_project(tmp_path, scene)
    project.tracks.append(Track("V2", "V2", "video"))
    source = create_multicam_source(
        project, [AngleSpec(asset_id="camA", name="Wide"), AngleSpec(asset_id="camB", name="Close-up", offset=9.0),
                  AngleSpec(asset_id="rec", name="Recorder", offset=4.0)], name="Concert")
    insert_multicam_clip(project, source.id, "V1", 0.0, angle_id="angle-1")
    _load(window, project)
    monkeypatch.setattr(window.runtime, "schedule_analysis", lambda key, fn, **_k: fn())
    dialog = _open(window, source)
    key = window.resync_multicam_source()
    window._poll_multicam_sync(key)                                                      # noqa: SLF001
    live = window.project.get_sequence(source.id)
    by_name = {a.name: a for a in live.multicam.angles}
    assert all(a.sync_method.value == "audio" for a in live.multicam.angles)
    # B contient le son 2,4 s plus tard (démarré 2,4 s plus tôt), l'enregistreur 3 s plus tôt : offsets 0,6 / 3,0 / 0
    assert angle_offset(live, by_name["Close-up"]) == pytest.approx(0.6, abs=0.02)
    assert angle_offset(live, by_name["Wide"]) == pytest.approx(3.0, abs=0.02)
    assert angle_offset(live, by_name["Recorder"]) == pytest.approx(0.0, abs=0.02)
    assert window.history.undo_label.startswith("Synchroniser les angles de « Concert »")
    assert dialog.row_widgets()[0].offset_spin.value() == pytest.approx(3.0, abs=0.02)
    window.undo_last()
    undone = window.project.get_sequence(source.id)
    assert angle_offset(undone, undone.multicam.angles[1]) == pytest.approx(9.0)


@needs_ffmpeg
def test_resynchronising_needs_two_angles_with_media(window):
    project = Project("p", media_assets=[_asset("camA"), _asset("camB")], tracks=[Track("V1", "V1", "video")])
    source = create_multicam_source(project, [AngleSpec(asset_id="camA"), AngleSpec(asset_id="camB")], name="C")
    project.media_assets = [a for a in project.media_assets if a.id != "camB"]            # un angle sans média
    _load(window, project)
    _open(window, source)
    assert window.resync_multicam_source() is None
    assert window.statusBar().currentMessage() == "Choisissez au moins deux sources."


def test_keyboard_navigation_reaches_the_settings_controls(window, qtbot):
    _project, source, _segment = _setup(window)
    dialog = _open(window, source)
    dialog.show()
    first = dialog.row_widgets()[0].name_edit
    first.setFocus(Qt.TabFocusReason)
    QTest.keyClick(dialog, Qt.Key_Tab)
    assert dialog.focusWidget() is not first
