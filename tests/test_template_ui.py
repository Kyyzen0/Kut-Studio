"""Interface des templates : galerie de l'assistant, dépôt d'un média sur un emplacement, éditeur de classement."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QDialog

from core.leaderboard import LeaderboardRow, leaderboard_of
from core.project_model import MediaAsset
from core.project_templates import TEMPLATES
from ui import i18n
from ui.leaderboard_dialog import LeaderboardDialog
from ui.social_dialogs import SocialProjectChoice, SocialProjectDialog


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
    return win


def _new_from_template(window, monkeypatch, template_id: str) -> None:
    monkeypatch.setattr(SocialProjectDialog, "exec", lambda self: QDialog.Accepted)
    monkeypatch.setattr(SocialProjectDialog, "choice",
                        lambda self: SocialProjectChoice("Run", "vertical", 30.0, "tiktok", template_id))
    window.new_social_project()


def test_the_wizard_shows_every_template_with_its_thumbnail(window, qtbot):
    dialog = SocialProjectDialog(window, templates=window._social_template_entries())
    qtbot.addWidget(dialog)
    gallery = dialog.template_list
    assert gallery.count() == len(TEMPLATES) + 1 and dialog.choice().template_id == ""
    for row, template in enumerate(TEMPLATES, start=1):
        item = gallery.item(row)
        assert item.text() == i18n.translate(f"template.{template.id}.name") and item.toolTip()
        image = item.icon().pixmap(gallery.iconSize()).toImage()
        lit = sum(1 for x in range(0, image.width(), 4) for y in range(0, image.height(), 4)
                  if image.pixelColor(x, y).lightness() > 60)
        assert lit > 20, template.id                                    # une vraie vignette, pas un carré noir
    gallery.setCurrentRow(1)
    assert dialog.choice().template_id == TEMPLATES[0].id


def test_a_template_project_opens_with_its_slots_titles_and_music(window, monkeypatch):
    _new_from_template(window, monkeypatch, "night_race")
    project = window.project
    assert project.name == "Run" and len(project.tracks[0].clips) == 19
    titles = {clip.graphic.text for track in project.tracks if track.type == "graphics" for clip in track.clips}
    assert i18n.translate("template.text.this_weekend_caps") in titles
    assert window.preview_panel.format_status.text() == i18n.translate(
        "preview.frame_format", width=1080, height=1920, fps="30", ratio="9:16")   # l'en-tête suit la séquence
    assert window.current_project_path is None and not window.project_dirty


def test_dropping_a_video_on_a_slot_fills_it_in_one_undo_step(window, monkeypatch, tmp_path):
    _new_from_template(window, monkeypatch, "cta_comments")
    window.project.media_assets.append(MediaAsset("v", str(tmp_path / "v.mp4"), "v", 9.0, 1920, 1080, 30.0, "video"))
    window.history.reset(window.project)
    window.on_asset_dropped("v", "V1", 2.0)
    (slot,) = window.project.tracks[0].clips
    assert (slot.asset_id, slot.timeline_start, slot.duration) == ("v", 0.0, 6.0)
    window.undo_last()
    assert window.project.tracks[0].clips[0].asset_id == ""
    window.project.media_assets.append(MediaAsset("a", str(tmp_path / "a.wav"), "a", 9.0, 0, 0, 0.0, "audio", True))
    window.on_asset_dropped("a", "V1", 2.0)
    assert window.project.tracks[0].clips[0].asset_id == ""
    assert window.statusBar().currentMessage() == i18n.translate("template.message.slot_needs_video")


def test_the_export_reports_the_slots_still_empty(window, monkeypatch):
    _new_from_template(window, monkeypatch, "leaderboard")
    assert window.empty_slot_warning() == i18n.translate("template.message.empty_slots", count=1)


def test_a_leaderboard_is_edited_as_a_table(window, monkeypatch, qtbot):
    _new_from_template(window, monkeypatch, "leaderboard")
    group = next(iter(window.project.active_sequence.generated_groups.values()))
    window.timeline_panel.selected_clip_id = group["clips"][0]
    edited = (LeaderboardRow("1", "ANA", "40 PTS", "#FF2433"), LeaderboardRow("2", "BO", "12 PTS", "#22B8FF"))

    def accept(self):
        assert self.table.rowCount() == 5                                # le tableau du template
        assert self.table.item(0, 1).text().startswith(i18n.translate("template.text.row_name"))
        return QDialog.Accepted

    monkeypatch.setattr(LeaderboardDialog, "exec", accept)
    monkeypatch.setattr(LeaderboardDialog, "rows", lambda self: edited)
    window.edit_leaderboard()
    (group_id, group), = window.project.active_sequence.generated_groups.items()
    board = leaderboard_of(window.project, group["clips"][0])
    assert board.rows == edited and len(group["clips"]) == 10
    window.undo_last()
    assert len(next(iter(window.project.active_sequence.generated_groups.values()))["clips"]) == 25


def test_the_leaderboard_dialog_reads_back_its_table(qtbot):
    rows = (LeaderboardRow("1", "A", "9", "#FF2433"),)
    dialog = LeaderboardDialog(rows, 6.0)
    qtbot.addWidget(dialog)
    assert dialog.rows() == rows and not dialog.remove_button.isEnabled()
    dialog.add_button.click()
    assert len(dialog.rows()) == 2 and dialog.rows()[1].rank == "2"
    combo = dialog.table.cellWidget(0, 3)
    assert combo.currentText() == i18n.translate("leaderboard.color.red")
    dialog.table.setCurrentCell(1, 0)
    dialog.remove_button.click()
    assert dialog.rows() == rows and dialog.duration() == 6.0
    assert dialog.windowTitle() and not dialog.table.horizontalHeaderItem(0).text().startswith("leaderboard.")
