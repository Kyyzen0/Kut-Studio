"""Méthodes de ``MainWindow`` regroupées : timeline_editing."""

from __future__ import annotations

from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QInputDialog, QMenu
from core.effects import play_crossfade_preview
from core.timeline_editing import (
    add_marker,
    delete_clips,
    move_clips,
    neighbor_marker,
    ripple_trim_left,
    roll_edit,
    shift_track_after,
    slide_clip,
    slip_clip,
)
from core.timeline_operations import (
    cut_clip,
    delete_clip,
    duplicate_clip,
    find_clip,
    move_clip,
    ripple_delete_clip,
    set_clip_enabled,
    trim_clip_left,
    trim_clip_right,
)


def _main_window():
    """Module ``ui.main_window`` résolu à l'appel (noms remplaçables en test)."""
    from ui import main_window

    return main_window


class TimelineEditingMixin:
    """Mixin de ``MainWindow`` (timeline_editing)."""

    def duplicate_selected_clip(self) -> None:
        """Duplique le clip sélectionné juste après sa fin."""
        clip_id = self.timeline_panel.selected_clip_id
        if clip_id is None:
            return
        try:
            new_clip = duplicate_clip(self.project, clip_id)
        except (KeyError, ValueError) as exc:
            _main_window().QMessageBox.critical(
                self,
                "Duplication impossible",
                f"Impossible de dupliquer le clip :\n\n{exc}",
            )
            return
        self._record_history("Dupliquer le clip")
        self.timeline_panel.set_project(self.project)
        self._update_timeline_duration()
        self.timeline_panel.select_clip(new_clip.id)
        self._mark_dirty()

    def ripple_delete_selected_clip(self) -> None:
        """Supprime le clip sélectionné et ramène à gauche les clips suivants."""
        clip_id = self.timeline_panel.selected_clip_id
        if clip_id is None:
            return
        try:
            moved_ids = ripple_delete_clip(self.project, clip_id)
        except KeyError as exc:
            _main_window().QMessageBox.critical(
                self,
                "Suppression impossible",
                f"Impossible de supprimer le clip :\n\n{exc}",
            )
            return
        self._record_history("Supprimer avec ripple")
        self.timeline_panel.selected_clip_id = None
        self.active_subtitle_clip = None
        self.properties_panel.set_clip(None, "")
        self.timeline_panel.set_project(self.project)
        self._update_timeline_duration()
        # Sélectionner le clip précédent sur V1 s'il existe.
        previous_clip = self._find_previous_v1_clip()
        if previous_clip is not None:
            self.timeline_panel.select_clip(previous_clip.id)
            self.on_clip_selected(previous_clip.id)
        self._mark_dirty()

    def delete_selected_clip_with_check(self) -> None:
        """Supprime la sélection. En mode ripple, les clips suivants se rapprochent."""
        ids = list(self.timeline_panel.selected_clip_ids)
        if not ids and self.timeline_panel.selected_clip_id:
            ids = [self.timeline_panel.selected_clip_id]
        if not ids:
            return
        if len(ids) == 1 and not self.timeline_panel.ripple_enabled:
            self.delete_selected_clip(ids[0])
            return
        try:
            delete_clips(self.project, ids, ripple=self.timeline_panel.ripple_enabled)
        except (KeyError, ValueError) as exc:
            print(f"[MainWindow] suppression groupée refusée : {exc}")
            return
        self._record_history("Supprimer la sélection")
        self.timeline_panel.selected_clip_id = None
        self.timeline_panel.selected_clip_ids = set()
        self.active_subtitle_clip = None
        self.properties_panel.set_clip(None, "")
        self.timeline_panel.set_project(self.project)
        self._update_timeline_duration()
        self._mark_dirty()

    def toggle_selected_clip_enabled(self) -> None:
        """Bascule l'état ``enabled`` du clip sélectionné."""
        clip_id = self.timeline_panel.selected_clip_id
        if clip_id is None:
            return
        try:
            clip = find_clip(self.project, clip_id)
        except KeyError:
            return
        was_enabled = clip.enabled
        try:
            set_clip_enabled(self.project, clip_id, not was_enabled)
        except KeyError:
            return
        self._record_history(
            "Désactiver le clip" if was_enabled else "Activer le clip"
        )
        self._reload_timeline_preserving_selection(clip_id)
        self._update_timeline_duration()
        self._mark_dirty()

    def on_move_clip_requested(self, clip_id: str, new_timeline_start: float) -> None:
        """Applique un déplacement demandé par la timeline."""
        try:
            move_clip(self.project, clip_id, new_timeline_start)
        except (KeyError, ValueError) as exc:
            print(f"[MainWindow] move refusé : {exc}")
            return
        self._record_history("Déplacer le clip")
        self._reload_timeline_preserving_selection(clip_id)
        self._update_timeline_duration()
        self._mark_dirty()

    def on_trim_left_requested(self, clip_id: str, new_timeline_start: float) -> None:
        try:
            if self.timeline_panel.ripple_enabled:
                ripple_trim_left(self.project, clip_id, new_timeline_start)
            else:
                trim_clip_left(self.project, clip_id, new_timeline_start)
        except (KeyError, ValueError) as exc:
            print(f"[MainWindow] trim gauche refusé : {exc}")
            return
        self._record_history("Trim gauche")
        self._reload_timeline_preserving_selection(clip_id)
        self._update_timeline_duration()
        self._mark_dirty()

    def on_trim_right_requested(self, clip_id: str, new_timeline_end: float) -> None:
        try:
            clip = find_clip(self.project, clip_id)
        except KeyError:
            clip = None
        old_end = None if clip is None else clip.timeline_start + clip.duration
        track_id = None if clip is None else clip.track_id
        try:
            trim_clip_right(self.project, clip_id, new_timeline_end)
        except (KeyError, ValueError) as exc:
            print(f"[MainWindow] trim droit refusé : {exc}")
            return
        if self.timeline_panel.ripple_enabled and old_end is not None and track_id:
            shift_track_after(
                self.project,
                track_id,
                old_end,
                new_timeline_end - old_end,
                exclude_ids={clip_id},
            )
        self._record_history("Trim droit")
        self._reload_timeline_preserving_selection(clip_id)
        self._update_timeline_duration()
        self._mark_dirty()

    def cut_at_playhead(self):
        clip_id = self.timeline_panel.selected_clip_id
        if clip_id is None:
            print("[MainWindow] Cut : aucun clip sélectionné")
            return
        self.cut_selected_clip(clip_id, self.timeline_panel.playhead_seconds)

    def cut_selected_clip(self, clip_id, playhead_pos):
        try:
            cut_clip(self.project, clip_id, playhead_pos)
        except (KeyError, ValueError) as exc:
            print(f"[MainWindow] cut refusé : {exc}")
            return
        self._record_history("Couper le clip")
        self.timeline_panel.set_project(self.project)
        self._update_timeline_duration()
        self._mark_dirty()
        # Tenter de conserver la sélection : si l'ancien id existe encore
        # (clip gauche de la coupe), on le re-sélectionne, sinon on prend
        # le clip V1 actif autour du playhead.
        new_id = clip_id
        if self.timeline_panel.find_view_by_id(new_id) is None:
            view_at_playhead = next(
                (
                    v
                    for v in self.timeline_panel.clip_views
                    if v.track_id == "V1" and v.start <= playhead_pos <= v.end
                ),
                None,
            )
            new_id = view_at_playhead.id if view_at_playhead is not None else None
        if new_id is not None:
            self._restore_clip_selection(new_id)
        else:
            self.properties_panel.set_clip(None, "")
            self.timeline_panel.selected_clip_id = None

    def delete_selected_clip(self, clip_id):
        try:
            delete_clip(self.project, clip_id)
        except KeyError as exc:
            print(f"[MainWindow] delete refusé : {exc}")
            return
        self._record_history("Supprimer le clip")
        self.timeline_panel.selected_clip_id = None
        self.active_subtitle_clip = None
        self.properties_panel.set_clip(None, "")
        self.timeline_panel.set_project(self.project)
        self._update_timeline_duration()
        self._mark_dirty()

    def offer_transition(self, transition_time):
        menu = QMenu(self)
        menu.addAction(f"Jonction à {transition_time:.2f}s")
        menu.addSeparator()
        crossfade = menu.addAction("Fondu enchaîné · 0.5 s")
        if menu.exec(QCursor.pos()) is crossfade:
            self.transition_seconds = transition_time
            self.transition_animation = play_crossfade_preview(self.preview_panel.preview_transition_overlay, self)

    def on_transition_selected(self, transition_id: str) -> None:
        """Affiche les réglages de la transition choisie sur la timeline."""
        transition = next(
            (item for item in self.project.transitions if item.id == transition_id), None
        )
        if transition is None:
            self.properties_panel.clear_transition()
            return
        outgoing = self.timeline_panel.find_view_by_id(transition.from_clip_id)
        incoming = self.timeline_panel.find_view_by_id(transition.to_clip_id)
        if outgoing is None or incoming is None:
            self.properties_panel.clear_transition()
            return
        track = next((item for item in self.project.tracks if item.id == outgoing.track_id), None)
        self.properties_panel.show_transition(
            transition, outgoing, incoming, track.name if track is not None else outgoing.track_id
        )

    def on_transition_type_changed(self, transition_id: str, transition_type: str) -> None:
        self._update_transition(transition_id, transition_type=transition_type)

    def on_transition_duration_changed(self, transition_id: str, duration: float) -> None:
        self._update_transition(transition_id, duration=duration)

    def _update_transition(
        self, transition_id: str, *, transition_type: str | None = None,
        duration: float | None = None,
    ) -> None:
        from core.transitions import TransitionType, update_transition

        try:
            update_transition(
                self.project,
                transition_id,
                transition_type=TransitionType(transition_type) if transition_type else None,
                duration=duration,
            )
        except (KeyError, ValueError) as error:
            self.statusBar().showMessage(f"Modification refusée : {error}", 6000)
            self.on_transition_selected(transition_id)
            return
        self._record_history("Modifier une transition")
        self.timeline_panel.set_project(self.project)
        self._update_timeline_duration()
        self._mark_dirty()
        self.timeline_panel.select_transition(transition_id)

    def remove_selected_transition(self, transition_id: str) -> None:
        from core.transitions import remove_transition

        try:
            remove_transition(self.project, transition_id)
        except KeyError:
            return
        self._record_history("Supprimer une transition")
        self.timeline_panel.set_project(self.project)
        self._update_timeline_duration()
        self._mark_dirty()
        self.properties_panel.clear_transition()
        self.statusBar().showMessage("Transition supprimée.", 3000)

    def on_slip_requested(self, clip_id: str, delta: float) -> None:
        try:
            slip_clip(self.project, clip_id, delta)
        except (KeyError, ValueError) as exc:
            print(f"[MainWindow] slip refusé : {exc}")
            self._reload_timeline_preserving_selection()
            return
        self._record_history("Slip")
        self._reload_timeline_preserving_selection(clip_id)
        self._mark_dirty()

    def on_slide_requested(self, clip_id: str, new_start: float) -> None:
        try:
            slide_clip(self.project, clip_id, new_start)
        except (KeyError, ValueError) as exc:
            print(f"[MainWindow] slide refusé : {exc}")
            self._reload_timeline_preserving_selection()
            return
        self._record_history("Slide")
        self._reload_timeline_preserving_selection(clip_id)
        self._update_timeline_duration()
        self._mark_dirty()

    def on_roll_requested(self, clip_id: str, edge: str, new_time: float) -> None:
        try:
            roll_edit(self.project, clip_id, edge, new_time)
        except (KeyError, ValueError) as exc:
            print(f"[MainWindow] roll refusé : {exc}")
            self._reload_timeline_preserving_selection()
            return
        self._record_history("Roll")
        self._reload_timeline_preserving_selection(clip_id)
        self._update_timeline_duration()
        self._mark_dirty()

    def on_clips_move_requested(self, placements) -> None:
        try:
            move_clips(self.project, list(placements))
        except (KeyError, ValueError) as exc:
            print(f"[MainWindow] déplacement refusé : {exc}")
            self._reload_timeline_preserving_selection()
            return
        self._record_history("Déplacer les clips")
        self._reload_timeline_preserving_selection()
        self._update_timeline_duration()
        self._mark_dirty()

    def on_blade_cut_requested(self, clip_id: str, instant: float) -> None:
        self.cut_selected_clip(clip_id, instant)

    def add_marker_at(self, seconds: float) -> None:
        marker = add_marker(self.project, seconds)
        self._record_history("Ajouter un marqueur")
        self._reload_timeline_preserving_selection()
        self._mark_dirty()
        del marker

    def rename_marker(self, marker_id: str) -> None:
        marker = next((item for item in self.project.markers if item.id == marker_id), None)
        if marker is None:
            return
        name, accepted = QInputDialog.getText(
            self,
            "Marqueur",
            "Nom du marqueur",
            text=marker.name,
        )
        if not accepted:
            return
        marker.name = name.strip()
        self._record_history("Renommer un marqueur")
        self._reload_timeline_preserving_selection()
        self._mark_dirty()

    def goto_marker(self, direction: int) -> None:
        marker = neighbor_marker(self.project, self.playhead_seconds, direction)
        if marker is not None:
            self.seek_to_position(marker.time_seconds)
