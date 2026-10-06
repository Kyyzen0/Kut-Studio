"""Méthodes de ``MainWindow`` regroupées : grille rythmique (tempo de la séquence, montage sur les temps).

La grille vit dans la séquence (:mod:`core.beat_grid`) ; la règle de la timeline la dessine et le magnétisme s'y
accroche. Les commandes « Couper sur les temps » et « Répartir sur la grille » appellent :mod:`core.beat_edit` puis
enregistrent une entrée d'historique.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QMenu

from core.beat_edit import cut_clips_on_beats, distribute_on_grid
from core.timeline_operations import find_clip
from ui import i18n
from ui.beat_grid_dialog import REMOVE, BeatGridDialog

DISTRIBUTE_CHOICES = (1, 2, 4, 8)
"""Temps par clip proposés pour « Répartir sur la grille »."""


class BeatGridMixin:
    """Tempo de la séquence, tap tempo, détection, aimantation aux temps, coupe et répartition."""

    def _beat_shortcut_handlers(self) -> dict:
        return {
            "beat_grid": self.edit_beat_grid,
            "beat_cut": lambda: self.cut_selection_on_beats(1),
            "beat_distribute": lambda: self.distribute_selection_on_grid(2),
            "beat_snap": lambda: self.set_snap_to_beats(not self.timeline_panel.snap_to_beats),
        }

    def _build_beat_menu(self, parent: QMenu) -> QMenu:
        menu = parent.addMenu(i18n.translate("beat.menu.title"))
        menu.addAction(self._command_action("beat_grid", "beat.menu.settings"))
        self.beat_snap_action = self._command_action("beat_snap", "beat.menu.snap", checkable=True)
        self.beat_snap_action.setChecked(True)
        menu.addAction(self.beat_snap_action)
        menu.addSeparator()
        menu.addAction(self._command_action("beat_cut", "beat.menu.cut"))
        menu.addAction(self._command_action("beat_distribute", "beat.menu.distribute"))
        spread = menu.addMenu(i18n.translate("beat.menu.distribute_every"))
        for beats in DISTRIBUTE_CHOICES:
            action = spread.addAction(i18n.translate("beat.menu.beats", count=beats))
            action.triggered.connect(lambda _checked=False, n=beats: self.distribute_selection_on_grid(n))
        return menu

    # -- grille ------------------------------------------------------------------------------------------------------

    def _active_grid(self):
        return getattr(self.project.active_sequence, "beat_grid", None)

    def edit_beat_grid(self) -> None:
        """« Grille rythmique… » : régler, détecter ou supprimer la grille de la séquence ouverte."""
        music = self._music_clip_for_detection()
        dialog = BeatGridDialog(
            self._active_grid(), playhead=float(self.playhead_seconds),
            detect=(lambda clip=music: self._detect_grid(clip)) if music is not None else None, parent=self,
        )
        result = dialog.exec()
        if result == REMOVE:
            self._set_beat_grid(None, "history.beat.remove")
        elif result == QDialog.Accepted:
            try:
                grid = dialog.grid()
            except ValueError as error:
                self._report_edit_refused(error)
                return
            self._set_beat_grid(grid, "history.beat.set")

    def _set_beat_grid(self, grid, history_key: str) -> None:
        if grid == self._active_grid():
            return
        self.project.active_sequence.beat_grid = grid
        self._record_history(i18n.translate(history_key))
        self._reload_timeline_preserving_selection()
        self._mark_dirty()

    def _music_clip_for_detection(self):
        """Clip audio sélectionné, sinon le premier clip d'une piste « musique » (``None`` : aucun)."""
        selected = self.timeline_panel.selected_clip_id
        audio_tracks = [track for track in self.project.tracks if track.type == "audio"]
        for track in audio_tracks:
            for clip in track.clips:
                if clip.id == selected:
                    return clip
        for track in audio_tracks:
            if track.audio_role == "music" and track.clips:
                return min(track.clips, key=lambda clip: clip.timeline_start)
        return None

    def _detect_grid(self, clip):
        """Grille déduite de la musique du clip, ou le message d'erreur à afficher."""
        from core.audio_sync import AudioSyncError
        from core.beat_detection import detect_tempo

        asset = next((item for item in self.project.media_assets if item.id == clip.asset_id), None)
        if asset is None or not asset.path:
            return i18n.translate("beat.dialog.detect_failed")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            estimate = detect_tempo(asset.path, start=float(clip.source_in), duration=float(clip.duration))
        except (AudioSyncError, OSError, ValueError, RuntimeError) as error:
            return i18n.translate("beat.dialog.detect_error", error=error)
        finally:
            QApplication.restoreOverrideCursor()
        return estimate.grid_for_clip(float(clip.timeline_start), float(clip.source_in))

    def set_snap_to_beats(self, enabled: bool) -> None:
        self.timeline_panel.snap_to_beats = bool(enabled)
        action = getattr(self, "beat_snap_action", None)
        if action is not None and action.isChecked() != bool(enabled):
            action.setChecked(bool(enabled))

    # -- montage sur les temps ---------------------------------------------------------------------------------------

    def _require_grid(self):
        grid = self._active_grid()
        if grid is None:
            self._show_social_status("beat.message.no_grid")
        return grid

    def cut_selection_on_beats(self, every: int = 1) -> None:
        """« Couper sur les temps » : chaque clip sélectionné est coupé sur les temps qu'il traverse."""
        grid = self._require_grid()
        if grid is None:
            return
        clips = self._selected_clips()
        if not clips:
            self._show_social_status("social.message.no_video")
            return
        try:
            pieces = cut_clips_on_beats(self.project, [clip.id for clip in clips], grid, every=every)
        except (KeyError, ValueError) as error:
            self._report_edit_refused(error)
            return
        self._after_beat_edit(pieces, "history.beat.cut")

    def distribute_selection_on_grid(self, beats_each: int = 2) -> None:
        """« Répartir sur la grille » : les clips sélectionnés, bout à bout, ``beats_each`` temps chacun."""
        grid = self._require_grid()
        if grid is None:
            return
        ids = [clip.id for clip in self._selected_clips()]
        if not ids:
            self._show_social_status("social.message.no_video")
            return
        try:
            distribute_on_grid(self.project, ids, grid, beats_each=beats_each)
        except (KeyError, ValueError) as error:
            self._report_edit_refused(error)
            return
        self._after_beat_edit(ids, "history.beat.distribute")

    def _after_beat_edit(self, clip_ids: list[str], history_key: str) -> None:
        self._record_history(i18n.translate(history_key))
        for clip_id in clip_ids:
            try:
                find_clip(self.project, clip_id)
            except KeyError:
                continue
            self._invalidate_preview_for_clip(clip_id)
        self._reload_timeline_preserving_selection(clip_ids[0] if clip_ids else None)
        self._update_timeline_duration()
        self._sync_preview_to_timeline()
        self._mark_dirty()


__all__ = ["DISTRIBUTE_CHOICES", "BeatGridMixin"]
