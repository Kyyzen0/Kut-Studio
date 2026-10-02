"""Méthodes de ``MainWindow`` regroupées : track_management."""

from __future__ import annotations

from PySide6.QtWidgets import QInputDialog
from core.track_operations import (
    add_track as track_operations_add_track,
    move_track as track_operations_move_track,
    remove_track as track_operations_remove_track,
    rename_track as track_operations_rename_track,
    set_track_locked,
    set_track_muted,
    set_track_visible,
)
from ui import i18n


def _main_window():
    """Module ``ui.main_window`` résolu à l'appel (noms remplaçables en test)."""
    from ui import main_window

    return main_window


class TrackManagementMixin:
    """Mixin de ``MainWindow`` (track_management)."""

    def on_track_solo(self, track_id: str, enabled: bool) -> None:
        track = next((item for item in self.project.tracks if item.id == track_id), None)
        if track is None:
            return
        if track.locked:
            self.timeline_panel.refresh_headers()
            return
        track.solo = bool(enabled)
        self._record_history("Solo de piste")
        self._refresh_after_track_change()
        self._mark_dirty()

    def on_track_armed(self, track_id: str, enabled: bool) -> None:
        track = next((item for item in self.project.tracks if item.id == track_id), None)
        if track is None or track.type != "audio":
            return
        if track.locked:
            self.timeline_panel.refresh_headers()
            return
        track.armed = bool(enabled)
        self._record_history("Armer la piste")
        self._refresh_after_track_change()
        self._mark_dirty()

    def on_track_height_cycle(self, track_id: str) -> None:
        track = next((item for item in self.project.tracks if item.id == track_id), None)
        if track is None:
            return
        order = ("compact", "normal", "large")
        current = track.height_mode if track.height_mode in order else "normal"
        track.height_mode = order[(order.index(current) + 1) % len(order)]
        self._record_history("Hauteur de piste")
        self._refresh_after_track_change()
        self._mark_dirty()

    def on_track_collapsed(self, track_id: str, collapsed: bool) -> None:
        track = next((item for item in self.project.tracks if item.id == track_id), None)
        if track is None:
            return
        track.collapsed = bool(collapsed)
        self._record_history("Réduire la piste")
        self._refresh_after_track_change()
        self._mark_dirty()

    def on_add_track_requested(self, track_type: str) -> None:
        """Ajoute une piste du type demandé, enregistre l'opération."""
        try:
            track = track_operations_add_track(self.project, track_type)
        except ValueError as exc:
            _main_window().QMessageBox.warning(self, i18n.translate("prefs.title"), str(exc))
            return
        label_keys = {
            "video": "tracks.add_video_long",
            "audio": "tracks.add_audio_long",
            "subtitle": "tracks.add_subtitle_long",
        }
        self.history.record(self.project, self._with_nested_clamp(i18n.translate(label_keys[track.type])))
        self._refresh_after_track_change()
        self._mark_dirty()

    def on_remove_track_requested(self, track_id: str) -> None:
        try:
            track_operations_remove_track(self.project, track_id)
        except (KeyError, ValueError) as exc:
            _main_window().QMessageBox.warning(self, i18n.translate("prefs.title"), str(exc))
            return
        self.history.record(self.project, self._with_nested_clamp(i18n.translate("tracks.remove")))
        self._refresh_after_track_change()
        self._mark_dirty()

    def on_rename_track_requested(self, track_id: str, new_name: str) -> None:
        if not new_name.strip():
            return
        try:
            track_operations_rename_track(self.project, track_id, new_name.strip())
        except (KeyError, ValueError) as exc:
            _main_window().QMessageBox.warning(self, i18n.translate("prefs.title"), str(exc))
            return
        self.history.record(self.project, self._with_nested_clamp(i18n.translate("tracks.rename")))
        self._refresh_after_track_change()
        self._mark_dirty()

    def on_toggle_track_lock(self, track_id: str, locked: bool) -> None:
        try:
            set_track_locked(self.project, track_id, locked)
        except KeyError as exc:
            print(f"[MainWindow] lock : {exc}")
            return
        self.history.record(self.project, self._with_nested_clamp(i18n.translate("tracks.toggle_lock")))
        self._refresh_after_track_change()
        self._mark_dirty()

    def on_toggle_track_visible(self, track_id: str, visible: bool) -> None:
        try:
            set_track_visible(self.project, track_id, visible)
        except KeyError as exc:
            print(f"[MainWindow] visible : {exc}")
            return
        self.history.record(self.project, self._with_nested_clamp(i18n.translate("tracks.toggle_visible")))
        self._refresh_after_track_change()
        self._mark_dirty()

    def on_toggle_track_muted(self, track_id: str, muted: bool) -> None:
        track = next((item for item in self.project.tracks if item.id == track_id), None)
        if track is not None and track.locked:
            self.timeline_panel.refresh_headers()
            return
        try:
            set_track_muted(self.project, track_id, muted)
        except KeyError as exc:
            print(f"[MainWindow] muted : {exc}")
            return
        self.history.record(self.project, self._with_nested_clamp(i18n.translate("tracks.toggle_mute")))
        self._refresh_after_track_change()
        self._mark_dirty()

    def on_move_track_up(self, track_id: str) -> None:
        self._move_track_relative(track_id, delta=-1)

    def on_move_track_down(self, track_id: str) -> None:
        self._move_track_relative(track_id, delta=1)

    def _move_track_relative(self, track_id: str, *, delta: int) -> None:
        try:
            current_index = next(
                i for i, track in enumerate(self.project.tracks) if track.id == track_id
            )
        except StopIteration:
            return
        target = max(0, min(len(self.project.tracks) - 1, current_index + delta))
        if target == current_index:
            return
        try:
            track_operations_move_track(self.project, track_id, target)
        except (KeyError, ValueError) as exc:
            _main_window().QMessageBox.warning(self, i18n.translate("prefs.title"), str(exc))
            return
        label_key = (
            "tracks.move_up" if delta < 0 else "tracks.move_down"
        )
        self.history.record(self.project, self._with_nested_clamp(i18n.translate(label_key)))
        self._refresh_after_track_change()
        self._mark_dirty()

    def remove_selected_track(self) -> None:
        sel = self._selected_track_id()
        if sel is None:
            _main_window().QMessageBox.information(
                self,
                i18n.translate("prefs.title"),
                i18n.translate("tracks.remove"),
            )
            return
        self.on_remove_track_requested(sel)

    def rename_selected_track(self) -> None:
        sel = self._selected_track_id()
        if sel is None:
            return
        track = next((t for t in self.project.tracks if t.id == sel), None)
        if track is None:
            return
        new_name, ok = QInputDialog.getText(
            self,
            i18n.translate("tracks.rename"),
            i18n.translate("tracks.rename"),
            text=track.name,
        )
        if ok and new_name and new_name != track.name:
            self.on_rename_track_requested(sel, new_name.strip())

    def _move_selected_track(self, *, direction: int) -> None:
        sel = self._selected_track_id()
        if sel is None:
            return
        self._move_track_relative(sel, delta=direction)

    def toggle_selected_track_lock(self) -> None:
        sel = self._selected_track_id()
        if sel is None:
            return
        track = next((t for t in self.project.tracks if t.id == sel), None)
        if track is None:
            return
        self.on_toggle_track_lock(sel, not track.locked)

    def toggle_selected_track_visible(self) -> None:
        sel = self._selected_track_id()
        if sel is None:
            return
        track = next((t for t in self.project.tracks if t.id == sel), None)
        if track is None:
            return
        self.on_toggle_track_visible(sel, not track.visible)

    def toggle_selected_track_muted(self) -> None:
        sel = self._selected_track_id()
        if sel is None:
            return
        track = next((t for t in self.project.tracks if t.id == sel), None)
        if track is None:
            return
        self.on_toggle_track_muted(sel, not track.muted)

    def _selected_track_id(self) -> str | None:
        if self.properties_panel.selected_clip is not None:
            return self.properties_panel.selected_clip.track_id
        if self.timeline_panel.selected_clip_id is not None:
            clip = next(
                (
                    clip
                    for track in self.project.tracks
                    for clip in track.clips
                    if clip.id == self.timeline_panel.selected_clip_id
                ),
                None,
            )
            if clip is not None:
                return clip.track_id
        return None

    def _refresh_after_track_change(self) -> None:
        """Reconstruit la timeline, l'aperçu, le mixeur et la bibliothèque.

        ``set_project`` efface la sélection. On la restaure sans passer
        par ``on_clip_selected``, qui déplacerait la tête de lecture au
        début du clip. L'appel suivant lisait donc toujours une
        sélection vide, et l'inspecteur restait sur l'ancien clip.
        """
        self._reload_timeline_preserving_selection()
        self._update_timeline_duration()
        self._sync_preview_to_timeline()
        self._refresh_project_library()
        mixer = getattr(self, "mixer_panel", None)
        if mixer is not None:
            mixer.set_project(self.project)
            mixer.set_master(self._master_gain_db, self._master_muted)
        self._refresh_undo_redo_state()
