"""Méthodes de ``MainWindow`` regroupées : history."""

from __future__ import annotations

from core.timeline_evaluator import timeline_duration


class HistoryMixin:
    """Mixin de ``MainWindow`` (history)."""

    def _mark_dirty(self) -> None:
        self.project_dirty = True
        self._update_top_bar()
        self._schedule_autosave()

    def _mark_clean(self) -> None:
        self._finalize_pending_edit_sessions()
        self.project_dirty = False
        self.history.mark_saved()
        self._refresh_undo_redo_state()
        self._update_top_bar()

    def _record_history(self, label: str) -> None:
        """Enregistre l'état courant du projet dans l'historique."""
        # Une autre action clôt la saisie de sous-titre en cours et son
        # état final est inclus dans ce snapshot d'action.
        if getattr(self, "_subtitle_edit_pending", None) is not None:
            timer = getattr(self, "_subtitle_edit_timer", None)
            if timer is not None:
                timer.stop()
            self._subtitle_edit_pending = None
            self._subtitle_edit_history_before = None
        # Une rafale d'étalonnage encore ouverte est déjà dans le projet :
        # elle est incluse dans ce snapshot, sans étape supplémentaire.
        if getattr(self, "_color_session_active", False):
            timer = getattr(self, "_color_session_timer", None)
            if timer is not None:
                timer.stop()
            self._color_session_active = False
        # Même principe pour une saisie graphique encore ouverte : l'action
        # courante capture son état final sans laisser un timer enregistrer un
        # snapshot obsolète plus tard.
        if getattr(self, "_graphic_session_active", False):
            timer = getattr(self, "_graphic_session_timer", None)
            if timer is not None:
                timer.stop()
            self._graphic_session_active = False
        self.history.record(self.project, label)
        self._refresh_undo_redo_state()

    def _finalize_pending_edit_sessions(self) -> None:
        """Ferme les éditions différées avant sauvegarde ou navigation."""
        self._flush_subtitle_history_record()
        self._finalize_transform_session()
        self._finalize_color_history()
        self._finalize_graphic_history()

    def _refresh_undo_redo_state(self) -> None:
        """Synchronise les actions et indicateurs undo/redo."""
        if hasattr(self, "undo_action"):
            self.undo_action.setEnabled(self.history.can_undo)
            label = self.history.undo_label
            self.undo_action.setText(
                f"Annuler : {label}" if label else "Annuler"
            )
        if hasattr(self, "redo_action"):
            self.redo_action.setEnabled(self.history.can_redo)
            label = self.history.redo_label
            self.redo_action.setText(
                f"Rétablir : {label}" if label else "Rétablir"
            )
        # Boutons rapides de la top-bar.
        if hasattr(self, "undo_button"):
            self.undo_button.setEnabled(self.history.can_undo)
            self.undo_button.setToolTip(
                f"Annuler : {self.history.undo_label}" if self.history.undo_label else "Annuler"
            )
        if hasattr(self, "redo_button"):
            self.redo_button.setEnabled(self.history.can_redo)
            self.redo_button.setToolTip(
                f"Rétablir : {self.history.redo_label}" if self.history.redo_label else "Rétablir"
            )
        # Synchronise le flag ``project_dirty`` avec l'historique.
        self.project_dirty = self.history.is_dirty
        self._update_top_bar()
        if self.project_dirty:
            self._schedule_autosave()
        else:
            timer = getattr(self, "_autosave_timer", None)
            if timer is not None:
                timer.stop()

    def _apply_history_snapshot(self, snapshot_project, label: str) -> None:
        """Ré-installe ``snapshot_project`` partout dans l'interface."""
        self.project = snapshot_project
        # La durée affichée vaut au moins une seconde : on borne la tête
        # avec la durée réelle du projet, puis on la repousse au widget.
        duration = timeline_duration(self.project)
        if duration > 0.0:
            self.playhead_seconds = min(max(self.playhead_seconds, 0.0), duration)
        else:
            self.playhead_seconds = max(0.0, self.playhead_seconds)
        self._reload_timeline_preserving_selection()
        self._update_timeline_duration()
        self.timeline_panel.set_playhead_seconds(self.playhead_seconds)
        self.playhead_seconds = self.timeline_panel.playhead_seconds
        self._sync_preview_to_timeline()
        self._refresh_project_library()
        self.properties_panel.set_project_color_presets(
            getattr(self.project, "color_presets", [])
        )
        mixer = getattr(self, "mixer_panel", None)
        if mixer is not None:
            mixer.set_project(self.project)
        self._sync_audio_inspector()
        self._refresh_undo_redo_state()

    def undo_last(self) -> None:
        """Annule la dernière opération enregistrée."""
        self._finalize_pending_edit_sessions()
        snapshot = self.history.undo()
        if snapshot is None:
            return
        self._apply_history_snapshot(snapshot, self.history.undo_label or "")

    def redo_last(self) -> None:
        """Rétablit la dernière opération annulée."""
        self._finalize_pending_edit_sessions()
        snapshot = self.history.redo()
        if snapshot is None:
            return
        self._apply_history_snapshot(snapshot, self.history.redo_label or "")
