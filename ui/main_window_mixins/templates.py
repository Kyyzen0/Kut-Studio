"""Méthodes de ``MainWindow`` regroupées : montage issu d'un template (emplacements à remplir, classement à rééditer).

- Déposer une vidéo sur un emplacement le **remplit** (même place, même animation, mêmes effets) au lieu d'ajouter
  un clip par-dessus ; des photos glissées du Finder sur un emplacement le remplissent, lui et les emplacements vides
  qui suivent (une photo par emplacement, Ken Burns compris) ;
- Réseaux sociaux › Photos › « Remplir les emplacements avec des photos… » fait de même depuis un sélecteur de
  fichiers, et « Diaporama photo… » pose une suite d'emplacements remplis de photos, sur les temps de la grille ;
- « Classement… » édite le tableau du classement sélectionné, ou en crée un à la tête de lecture ;
- à l'ajout d'un export, les emplacements encore vides sont signalés (ils sortent comme des cartes).
"""

from __future__ import annotations

from PySide6.QtWidgets import QDialog

from core.leaderboard import Leaderboard, LeaderboardError, LeaderboardRow, leaderboard_of, update_leaderboard
from core.project_templates import NIGHT_PALETTE
from core.template_slots import (
    SlotError,
    add_photo_slideshow,
    empty_slots,
    fill_slot,
    fill_slots_with_photos,
    slot_at,
)
from ui import i18n
from ui.leaderboard_dialog import ROW_COLORS, LeaderboardDialog

NEW_LEADERBOARD_SECONDS = 6.0


def _main_window():
    """Module ``ui.main_window`` résolu à l'appel (noms remplaçables en test)."""
    from ui import main_window

    return main_window


def photo_sizes(paths) -> list[tuple[str, tuple[int, int]]]:
    """``(chemin, (largeur, hauteur))`` de chaque photo, lus dans l'en-tête du fichier (sans décoder l'image) ;
    ``(0, 0)`` pour un fichier que Qt ne sait pas lire."""
    from PySide6.QtGui import QImageReader

    sizes = []
    for path in paths:
        size = QImageReader(str(path)).size()
        sizes.append((str(path), (size.width(), size.height()) if size.isValid() else (0, 0)))
    return sizes


class TemplatesMixin:
    """Emplacements de template et classements générés."""

    def _templates_shortcut_handlers(self) -> dict:
        return {
            "leaderboard": self.edit_leaderboard,
            "photo_fill_slots": self.fill_slots_with_chosen_photos,
            "photo_slideshow": self.add_photo_slideshow,
        }

    # -- emplacements ------------------------------------------------------------------------------------------------

    def fill_slot_from_drop(self, asset_id: str, track_id: str, seconds: float) -> bool:
        """Média déposé sur un emplacement : il le remplit. ``False`` : pas d'emplacement ici, dépôt ordinaire."""
        slot = slot_at(self.project, track_id, float(seconds))
        if slot is None:
            return False
        try:
            fill_slot(self.project, slot.id, asset_id)
        except SlotError:
            self._show_social_status("template.message.slot_needs_media")
            return True
        except FileNotFoundError:
            # Photo de la bibliothèque déplacée ou hors ligne : refus annoncé, l'emplacement reste tel quel.
            asset = next((item for item in self.project.media_assets if item.id == asset_id), None)
            name = asset.name if asset is not None else asset_id
            self.statusBar().showMessage(i18n.translate("template.message.photo_missing", name=name), 5000)
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

    # -- photos ------------------------------------------------------------------------------------------------------

    def fill_slots_from_photo_drop(self, paths: list, track_id: str, seconds: float) -> None:
        """Photos glissées sur un emplacement : lui, puis les emplacements vides qui le suivent (une photo chacun)."""
        slot = slot_at(self.project, track_id, float(seconds))
        if slot is None:
            return
        self._fill_slots_with(photo_sizes(paths), start_clip_id=slot.id)

    def fill_slots_with_chosen_photos(self) -> None:
        """« Remplir les emplacements avec des photos… » : depuis l'emplacement sélectionné, sinon dans les vides."""
        paths = self._choose_photos()
        if not paths:
            return
        selected = getattr(self.timeline_panel, "selected_clip_id", None)
        start = selected if selected and slot_at_clip(self.project, selected) else None
        if start is None and not empty_slots(self.project):
            self._show_social_status("template.message.no_empty_slot")
            return
        self._fill_slots_with(photo_sizes(paths), start_clip_id=start)

    def _fill_slots_with(self, photos: list, *, start_clip_id: str | None) -> None:
        try:
            filled = fill_slots_with_photos(self.project, photos, start_clip_id=start_clip_id)
        except KeyError:
            return
        except ValueError as error:                         # piste verrouillée : rien n'est rempli
            self._report_edit_refused(error)
            return
        if not filled:
            self._show_social_status("template.message.photos_unreadable")
            return
        self._record_history(i18n.translate("history.template.fill_photos", count=len(filled)))
        self._after_slot_edit(filled[0].id)

    def add_photo_slideshow(self) -> None:
        """« Diaporama photo… » : un emplacement par photo à la tête de lecture, sur les temps de la grille rythmique
        (une mesure par photo) ou, sans grille, à la durée des photos des préférences."""
        paths = self._choose_photos()
        if not paths:
            return
        sequence = self.project.active_sequence
        clips = add_photo_slideshow(
            self.project, photo_sizes(paths), start=float(self.playhead_seconds),
            grid=getattr(sequence, "beat_grid", None), seconds=float(getattr(self, "_photo_duration", 3.0)),
        )
        if not clips:
            return
        self._record_history(i18n.translate("history.template.slideshow", count=len(clips)))
        self._after_slot_edit(clips[0].id)
        self._update_timeline_duration()
        self.timeline_panel.select_clip(clips[0].id)

    def _choose_photos(self) -> list[str]:
        paths, _ = _main_window().QFileDialog.getOpenFileNames(
            self, i18n.translate("template.photos.choose"), "", i18n.translate("dialog.filter.images"),
        )
        return list(paths)

    def _after_slot_edit(self, clip_id: str) -> None:
        self._refresh_project_library()
        self._invalidate_preview_for_clip(clip_id)
        self._reload_timeline_preserving_selection(clip_id)
        self._sync_preview_to_timeline()
        self._mark_dirty()

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


def slot_at_clip(project, clip_id: str) -> bool:
    """Le clip ``clip_id`` est-il un emplacement de template ?"""
    return any(clip.id == clip_id and clip.template_slot for track in project.tracks for clip in track.clips)


__all__ = ["TemplatesMixin", "photo_sizes"]
