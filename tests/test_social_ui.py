"""Interface de la vidéo sociale : nouveau projet vertical, réglages de séquence, zones de plateforme, cadrage, Ken Burns."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QDialog

from core.canvas_guides import PLATFORM_ZONES, platform_content_rect, platform_zone_rects
from core.project_model import Clip, MediaAsset, Project, Track
from core.social_formats import SOCIAL_FORMATS, create_social_project, format_for_frame, social_format
from core.user_settings import UserSettings, load_user_settings, save_user_settings
from ui.social_dialogs import SequenceSettingsDialog, SocialProjectDialog


# --- Cœur ---------------------------------------------------------------------------------------------------------


def test_a_social_project_has_the_frame_and_the_tracks_of_a_short_video():
    project = create_social_project("vertical", 60, name="Run", track_names={"A1": "Musique"})
    assert (project.width, project.height, project.fps) == (1080, 1920, 60.0)
    roles = {track.id: (track.type, track.audio_role) for track in project.tracks}
    assert roles == {"V1": ("video", "other"), "G1": ("graphics", "other"), "A1": ("audio", "music"),
                     "A2": ("audio", "voice"), "A3": ("audio", "sfx")}
    assert next(t for t in project.tracks if t.id == "A1").name == "Musique"
    with pytest.raises(ValueError):
        create_social_project("vertical", 25)
    with pytest.raises(KeyError):
        social_format("cinema")


def test_formats_are_recognised_from_a_frame_of_the_same_ratio():
    assert format_for_frame(540, 960).id == "vertical"
    assert format_for_frame(1080, 1350).id == "portrait"
    assert format_for_frame(1000, 700) is None


@pytest.mark.parametrize("platform", list(PLATFORM_ZONES))
def test_the_free_area_of_a_platform_avoids_every_masked_zone(platform):
    x, y, w, h = platform_content_rect(platform)
    assert 0.0 <= x and 0.0 <= y and x + w <= 1.0 and y + h <= 1.0 and w > 0.6 and h > 0.5
    for kind, (zx, zy, zw, zh) in platform_zone_rects(1.0, 1.0, platform):
        overlap_w = min(x + w, zx + zw) - max(x, zx)
        overlap_h = min(y + h, zy + zh) - max(y, zy)
        assert overlap_w <= 1e-9 or overlap_h <= 1e-9, kind


def test_social_preferences_survive_a_restart_and_reject_garbage(tmp_path):
    save_user_settings(UserSettings(photo_duration=2.5, photo_fill=True, photo_ken_burns=True, platform_zones="reels"),
                       tmp_path)
    loaded = load_user_settings(tmp_path)
    assert (loaded.photo_duration, loaded.photo_fill, loaded.photo_ken_burns, loaded.platform_zones) == (
        2.5, True, True, "reels")
    (tmp_path / "user_settings.json").write_text('{"photo_duration": -4, "platform_zones": "myspace"}', encoding="utf-8")
    loaded = load_user_settings(tmp_path)
    assert loaded.photo_duration == 0.5 and loaded.platform_zones == ""


# --- Boîtes de dialogue -------------------------------------------------------------------------------------------


def test_the_new_project_dialog_proposes_the_zones_of_the_chosen_format(qtbot):
    dialog = SocialProjectDialog()
    qtbot.addWidget(dialog)
    assert dialog.choice().format_id == "vertical" and dialog.choice().platform == "tiktok"
    dialog.format_buttons["portrait"].setChecked(True)
    assert dialog.choice().platform == "instagram_feed"
    dialog.format_buttons["landscape"].setChecked(True)
    dialog.fps_combo.setCurrentIndex(1)
    choice = dialog.choice()
    assert (choice.format_id, choice.fps, choice.platform) == ("landscape", 60.0, "")
    assert len(dialog.format_buttons) == len(SOCIAL_FORMATS)


def test_the_sequence_dialog_follows_presets_and_keeps_even_sizes(qtbot):
    dialog = SequenceSettingsDialog(1920, 1080, 29.97)
    qtbot.addWidget(dialog)
    assert dialog.preset_combo.currentData() == "landscape"
    dialog.preset_combo.setCurrentIndex(dialog.preset_combo.findData("vertical"))
    assert dialog.values() == (1080, 1920, 29.97)
    dialog.width_spin.setValue(1001)
    assert dialog.values()[0] == 1000 and dialog.preset_combo.currentData() == ""


# --- Fenêtre ------------------------------------------------------------------------------------------------------


@pytest.fixture
def window(qtbot, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QMessageBox

    from ui.main_window import MainWindow

    monkeypatch.setattr("ui.main_window.QMessageBox.question", lambda *_a, **_k: QMessageBox.Yes)
    monkeypatch.setattr("ui.main_window.QMessageBox.warning", lambda *_a, **_k: QMessageBox.Discard)
    win = MainWindow()
    qtbot.addWidget(win)
    win.timeline_timer.stop()
    monkeypatch.setattr(win.preview_panel, "preview_at", lambda *_a, **_k: None)
    asset = MediaAsset(id="a", path=str(tmp_path / "m.mp4"), name="m", duration=10.0, width=1920, height=1080,
                       fps=30.0, media_type="video")
    clips = [Clip(id=f"c{n}", asset_id="a", track_id="V1", timeline_start=2.0 * n, source_in=0.0, source_out=2.0)
             for n in range(2)]
    win.project = Project(name="s", width=1080, height=1920, fps=30.0, media_assets=[asset],
                          tracks=[Track(id="V1", name="V1", type="video", clips=clips)])
    win._reload_timeline_preserving_selection("c0")
    win.history.reset(win.project)
    win.timeline_panel.selected_clip_ids = {"c0", "c1"}
    win.timeline_panel.selected_clip_id = "c0"
    win._mark_clean()
    return win


def test_the_social_menu_creates_a_vertical_project(window, monkeypatch):
    from ui.social_dialogs import SocialProjectChoice

    monkeypatch.setattr(SocialProjectDialog, "exec", lambda self: QDialog.Accepted)
    monkeypatch.setattr(SocialProjectDialog, "choice",
                        lambda self: SocialProjectChoice("Grand Prix", "vertical", 30.0, "shorts"))
    window.new_social_project()
    assert (window.project.width, window.project.height, window.project.name) == (1080, 1920, "Grand Prix")
    assert window.preview_panel.overlay.platform_zones == "shorts"
    assert window._platform_zone_actions["shorts"].isChecked()
    assert window.current_project_path is None and not window.project_dirty


def test_fill_frame_toggles_every_selected_clip_in_one_undo_step(window):
    window.toggle_fill_frame_for_selection()
    assert all(clip.transform.fill for clip in window.project.tracks[0].clips)
    window.undo_last()
    assert not any(clip.transform.fill for clip in window.project.tracks[0].clips)


def test_ken_burns_animates_the_selection_and_is_undoable(window):
    window.apply_ken_burns_to_selection()
    names = {kf.property_name for clip in window.project.tracks[0].clips for kf in clip.transform_keyframes}
    assert "scale" in names or "position_x" in names or "position_y" in names
    window.undo_last()
    assert not any(clip.transform_keyframes for clip in window.project.tracks[0].clips)


def test_sequence_settings_change_the_frame_with_one_undo_step(window, monkeypatch):
    monkeypatch.setattr(SequenceSettingsDialog, "exec", lambda self: QDialog.Accepted)
    monkeypatch.setattr(SequenceSettingsDialog, "values", lambda self: (1080, 1350, 60.0))
    window.edit_sequence_settings()
    sequence = window.project.active_sequence
    assert (sequence.width, sequence.height, sequence.fps) == (1080, 1350, 60.0)
    window.undo_last()
    assert (window.project.width, window.project.height) == (1080, 1920)


def test_the_inspector_shows_the_frame_of_the_active_sequence(window):
    window._update_top_bar()
    texts = [label.text() for label in window.properties_panel._project_info_labels.values()]
    assert any("1080 × 1920" in text for text in texts) and any("9:16" in text for text in texts)


# --- Grille rythmique -----------------------------------------------------------------------------------------------


def test_a_beat_grid_reaches_the_ruler_and_the_magnetism(window):
    from core.beat_grid import BeatGrid

    window._set_beat_grid(BeatGrid(120.0, 0.0), "history.beat.set")
    panel = window.timeline_panel
    assert panel.ruler.beat_grid == BeatGrid(120.0, 0.0)
    panel.ruler.grab()                                             # dessine les temps sans erreur
    scale = panel.pixels_per_second * panel.zoom
    near = 7.47                                                    # 30 ms d'un temps (seuil : 8 px)
    assert abs(7.5 - near) * scale < panel.snap_threshold_pixels
    assert panel.snap_position(near, "c1")[0] == pytest.approx(7.5)
    window.set_snap_to_beats(False)
    assert panel.snap_position(near, "c1")[0] == pytest.approx(near)


def test_cut_and_distribute_on_the_grid_are_single_undo_steps(window):
    from core.beat_grid import BeatGrid

    window._set_beat_grid(BeatGrid(120.0, 0.0), "history.beat.set")
    window.cut_selection_on_beats(1)
    assert len(window.project.tracks[0].clips) == 8                # 2 clips de 2 s, coupés toutes les 0,5 s
    window.undo_last()
    assert len(window.project.tracks[0].clips) == 2
    window.timeline_panel.selected_clip_ids = {"c0", "c1"}
    window.distribute_selection_on_grid(1)
    starts = sorted(clip.timeline_start for clip in window.project.tracks[0].clips)
    assert starts == pytest.approx([0.0, 0.5])


def test_the_beat_dialog_taps_and_detects(qtbot):
    from core.beat_grid import BeatGrid
    from ui.beat_grid_dialog import BeatGridDialog

    dialog = BeatGridDialog(None, playhead=1.25, detect=lambda: BeatGrid(96.0, 0.4))
    qtbot.addWidget(dialog)
    assert not dialog.remove_button.isEnabled()
    dialog.detect_button.click()
    assert dialog.grid() == BeatGrid(96.0, 0.4)
    for _ in range(4):
        dialog.tap_button.click()                                  # quatre frappes rapprochées : un tempo mesuré
    assert dialog.bpm_spin.value() >= 20.0
    dialog.offset_spin.setValue(0.0)
    assert dialog.grid().offset == 0.0
