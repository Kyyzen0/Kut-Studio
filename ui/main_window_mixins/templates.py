"""Méthodes de ``MainWindow`` regroupées : montage issu d'un template (emplacements à remplir, classement à rééditer).

- Déposer une vidéo sur un emplacement le **remplit** (même place, même animation, mêmes effets) au lieu d'ajouter
  un clip par-dessus ;
- « Classement… » édite le tableau du classement sélectionné, ou en crée un à la tête de lecture ;
- à l'ajout d'un export, les emplacements encore vides sont signalés (ils sortent comme des cartes).
"""

from __future__ import annotations

from PySide6.QtWidgets import QDialog

from core.leaderboard import Leaderboard, LeaderboardError, LeaderboardRow, leaderboard_of, update_leaderboard
from core.project_templates import NIGHT_PALETTE
from core.template_slots import SlotError, empty_slots, fill_slot, slot_at
from ui import i18n
from ui.leaderboard_dialog import ROW_COLORS, LeaderboardDialog

NEW_LEADERBOARD_SECONDS = 6.0


class TemplatesMixin:
    """Emplacements de template et classements générés."""

    def _templates_shortcut_handlers(self) -> dict:
        return {"leaderboard": self.edit_leaderboard}

    # -- emplacements ------------------------------------------------------------------------------------------------

    def fill_slot_from_drop(self, asset_id: str, track_id: str, seconds: float) -> bool:
        """Média déposé sur un emplacement : il le remplit. ``False`` : pas d'emplacement ici, dépôt ordinaire."""
        slot = slot_at(self.project, track_id, float(seconds))
        if slot is None:
            return False
        try:
            fill_slot(self.project, slot.id, asset_id)
        except SlotError:
            self._show_social_status("template.message.slot_needs_video")
            return True
        except KeyError:
            return False
        except ValueError as error:                         # piste verrouillée : le dépôt est refusé, rien n'est ajouté
            self._report_edit_refused(error)
            return True
        self._record_history(i18n.translate("history.template.fill_slot", slot=slot.label))
        self._invalidate_preview_for_clip(slot.id)
        self._reload_timeline_preserving_selection(slot.id)
        self._sync_preview_to_timeline()
        self._mark_dirty()
        return True

    def empty_slot_warning(self) -> str:
        """Phrase à afficher à l'ajout d'un export s'il reste des emplacements vides (sinon vide)."""
        count = len(empty_slots(self.project))
        return i18n.translate("template.message.empty_slots", count=count) if count else ""

    # -- classement --------------------------------------------------------------------------------------------------

    def edit_leaderboard(self) -> None:
        """« Classement… » : réédite le classement du calque sélectionné, ou en pose un nouveau à la tête de lecture."""
        selected = getattr(self.timeline_panel, "selected_clip_id", None)
        board = leaderboard_of(self.project, selected) if selected else None
        if board is None:
            name = i18n.translate("template.text.row_name")
            rows = tuple(LeaderboardRow(str(rank), f"{name} {rank}", str(10 * (4 - rank)), NIGHT_PALETTE[ROW_COLORS[rank - 1]])
                         for rank in (1, 2, 3))
            board = Leaderboard(rows, start=float(self.playhead_seconds), duration=NEW_LEADERBOARD_SECONDS)
        dialog = LeaderboardDialog(board.rows, board.duration, self)
        if dialog.exec() != QDialog.Accepted:
            return
        try:
            edited = Leaderboard(dialog.rows(), board.start, dialog.duration(), board.top, board.delay, id=board.id)
        except LeaderboardError as error:
            self._report_edit_refused(error)
            return
        clips = update_leaderboard(self.project, edited)
        self._record_history(i18n.translate("history.template.leaderboard"))
        self._refresh_project_library()
        self._reload_timeline_preserving_selection(clips[0].id)
        self._update_timeline_duration()
        self._sync_preview_to_timeline()
        self._mark_dirty()


__all__ = ["TemplatesMixin"]
