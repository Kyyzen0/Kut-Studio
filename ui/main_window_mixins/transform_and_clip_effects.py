"""Méthodes de ``MainWindow`` regroupées : transform_and_clip_effects."""

from __future__ import annotations

import logging

from PySide6.QtWidgets import QMessageBox

from core.timeline_operations import (
    find_clip,
    remove_transform_keyframe,
    reset_clip_transform,
    set_clip_transform,
    set_transform_keyframe,
)
from ui.i18n import translate

LOGGER = logging.getLogger(__name__)


class TransformEffectsMixin:
    """Mixin de ``MainWindow`` (transform_and_clip_effects)."""

    def on_speed_changed(self, clip_id: str, speed: float) -> None:
        """Modifie la vitesse d'un clip depuis l'inspecteur."""
        from core.timeline_operations import set_clip_speed
        try:
            set_clip_speed(self.project, clip_id, speed)
        except (KeyError, ValueError) as exc:
            self._report_edit_refused(exc)
            return
        self._record_history(translate("history.speed.edit"))
        self._reload_timeline_preserving_selection(clip_id)
        self._update_timeline_duration()
        self._mark_dirty()

    def on_reverse_toggled(self, clip_id: str, reverse: bool) -> None:
        """Modifie le mode reverse d'un clip depuis l'inspecteur."""
        from core.timeline_operations import set_clip_reverse
        try:
            set_clip_reverse(self.project, clip_id, reverse)
        except (KeyError, ValueError) as exc:
            self._report_edit_refused(exc)
            return
        self._record_history(translate("history.clip.reverse") if reverse else translate("history.clip.unreverse"))
        self._reload_timeline_preserving_selection(clip_id)
        self._update_timeline_duration()
        self._mark_dirty()

    def on_freeze_frame_created(self, clip_id: str, freeze_source_time: float, freeze_duration: float) -> None:
        """Crée un arrêt sur image pour un clip depuis l'inspecteur."""
        from core.timeline_operations import set_clip_freeze_frame
        try:
            set_clip_freeze_frame(self.project, clip_id, freeze_source_time, freeze_duration)
        except (KeyError, ValueError) as exc:
            self._report_edit_refused(exc)
            return
        self._record_history(translate("tooltip.freeze_frame"))
        self._reload_timeline_preserving_selection(clip_id)
        self._update_timeline_duration()
        self._mark_dirty()

    def on_freeze_frame_removed(self, clip_id: str) -> None:
        """Supprime un arrêt sur image pour un clip depuis l'inspecteur."""
        from core.timeline_operations import remove_clip_freeze_frame
        try:
            remove_clip_freeze_frame(self.project, clip_id)
        except KeyError as exc:
            self._report_edit_refused(exc)
            return
        self._record_history(translate("history.freeze.remove"))
        self._reload_timeline_preserving_selection(clip_id)
        self._update_timeline_duration()
        self._mark_dirty()

    def on_freeze_duration_changed(self, clip_id: str, freeze_duration: float) -> None:
        """Modifie la durée d'un arrêt sur image depuis l'inspecteur."""
        from core.timeline_operations import set_clip_freeze_duration
        try:
            set_clip_freeze_duration(self.project, clip_id, freeze_duration)
        except (KeyError, ValueError) as exc:
            self._report_edit_refused(exc)
            return
        self._record_history(translate("history.freeze.duration"))
        self._reload_timeline_preserving_selection(clip_id)
        self._update_timeline_duration()
        self._mark_dirty()

    def on_time_remapping_reset(self, clip_id: str) -> None:
        """Réinitialise le remappage temporel d'un clip depuis l'inspecteur."""
        from core.timeline_operations import reset_clip_time_remapping
        try:
            reset_clip_time_remapping(self.project, clip_id)
        except KeyError as exc:
            self._report_edit_refused(exc)
            return
        self._record_history(translate("tooltip.reset_speed"))
        self._reload_timeline_preserving_selection(clip_id)
        self._update_timeline_duration()
        self._mark_dirty()

    def on_clip_effect_enabled_changed(
        self, clip_id: str, effect_id: str, enabled: bool
    ) -> None:
        """Active ou désactive un effet depuis l'inspecteur."""
        from core.effects_model import set_clip_effect_enabled
        try:
            set_clip_effect_enabled(self.project, clip_id, effect_id, enabled)
        except (KeyError, ValueError) as exc:
            self._report_edit_refused(exc)
            return
        self._record_history(
            translate("history.effect.enable") if enabled else translate("history.effect.disable")
        )
        self._refresh_effects_after_change(clip_id)

    def on_clip_effect_added(self, clip_id: str, effect_type: str) -> None:
        """Ajoute un effet choisi dans l'inspecteur au clip vidéo courant."""
        from core.effects_model import add_effect_to_clip
        try:
            add_effect_to_clip(self.project, clip_id, effect_type)
        except (KeyError, ValueError) as exc:
            QMessageBox.warning(self, translate("dialog.title.effects"), str(exc))
            return
        self._record_history(translate("history.effect.add"))
        self._refresh_effects_after_change(clip_id)

    def on_clip_effect_removed(self, clip_id: str, effect_id: str) -> None:
        """Supprime un effet depuis l'inspecteur."""
        from core.effects_model import remove_effect_from_clip
        try:
            remove_effect_from_clip(self.project, clip_id, effect_id)
        except (KeyError, ValueError) as exc:
            self._report_edit_refused(exc)
            return
        self._record_history(translate("history.effect.remove"))
        self._refresh_effects_after_change(clip_id)

    def on_clip_effect_moved(
        self, clip_id: str, effect_id: str, delta: int
    ) -> None:
        """Réordonne un effet depuis l'inspecteur."""
        from core.effects_model import move_clip_effect
        try:
            move_clip_effect(self.project, clip_id, effect_id, delta)
        except (KeyError, ValueError) as exc:
            self._report_edit_refused(exc)
            return
        self._record_history(translate("history.effect.move"))
        self._refresh_effects_after_change(clip_id)

    def on_clip_effect_parameter_changed(
        self, clip_id: str, effect_id: str, name: str, value: float
    ) -> None:
        """Met à jour un paramètre d'effet depuis l'inspecteur."""
        from core.effects_model import update_clip_effect_parameters
        try:
            update_clip_effect_parameters(
                self.project, clip_id, effect_id, {name: value}
            )
        except (KeyError, ValueError) as exc:
            self._report_edit_refused(exc)
            return
        self._record_history(translate("history.effect.edit"))
        self._refresh_effects_after_change(clip_id)

    def on_transform_property_changed(
        self, clip_id: str, property_name: str, value: float
    ):
        """Applique une modification de transform depuis l'inspecteur.

        Les modifications fréquentes (slider de l'inspecteur) sont
        coalescées : on ne crée une entrée d'historique qu'après la
        fin d'une rafale (debounce ~400 ms), pour ne pas polluer
        l'historique avec des dizaines d'entrées par seconde.
        """
        if not bool(getattr(self, "_transform_session_active", False)):
            self._finalize_graphic_history()
        try:
            clip = find_clip(self.project, clip_id)
        except KeyError:
            return
        if clip is None:
            return
        from core.visual_effects import TRANSFORM_PROPERTY_NAMES

        if property_name not in TRANSFORM_PROPERTY_NAMES:
            return
        try:
            # ``with_property`` garde les autres champs (ancrage, miroirs…).
            new_transform = clip.transform.with_property(property_name, value)
        except ValueError as exc:
            self._report_edit_refused(exc)
            return
        try:
            set_clip_transform(self.project, clip_id, new_transform)
        except ValueError as exc:
            self._report_edit_refused(exc)
            return

        # Le snapshot courant de l'historique est l'état avant la rafale.
        # Il suffit d'enregistrer l'état modifié à la fin du debounce.
        self._schedule_transform_history(translate("history.motion.edit"))

        # Rafraîchit l'aperçu immédiatement pour le retour visuel.
        self.properties_panel.update_transform_from_clip(
            clip.transform,
            clip.transform_keyframes,
            playhead_seconds=self.playhead_seconds,
        )
        self._sync_preview_to_timeline()
        try:
            self._invalidate_preview_for_clip(clip_id)
        except Exception:
            LOGGER.warning(
                "Invalidation de l'aperçu en échec après la modification du transform du clip %s : des segments périmés peuvent s'afficher",
                clip_id, exc_info=True,
            )
        self._mark_dirty()

    def _ensure_transform_session_capture(self) -> None:
        """Marque le début d'une rafale d'édition à regrouper dans l'historique."""
        if getattr(self, "_transform_session_active", False):
            return
        self._transform_session_active = True

    def _schedule_transform_history(self, label: str) -> None:
        """Regroupe une rafale d'éditions dans une seule entrée d'historique."""
        first = not getattr(self, "_transform_session_active", False)
        self._ensure_transform_session_capture()
        if first:
            self._transform_session_label = label
        if not hasattr(self, "_transform_session_timer"):
            from PySide6.QtCore import QTimer

            self._transform_session_timer = QTimer(self)
            self._transform_session_timer.setSingleShot(True)
            self._transform_session_timer.timeout.connect(
                self._finalize_transform_session
            )
        self._transform_session_timer.start(400)

    def _finalize_transform_session(self) -> None:
        """Enregistre l'état final d'une rafale d'édition de transform."""
        if not getattr(self, "_transform_session_active", False):
            return
        timer = getattr(self, "_transform_session_timer", None)
        if timer is not None:
            timer.stop()
        self._transform_session_active = False
        label = getattr(self, "_transform_session_label", translate("history.motion.edit"))
        self.history.record(self.project, label)
        invalidate = getattr(self, "_invalidate_nested_dependents", None)
        if callable(invalidate):
            invalidate()  # les séquences qui montrent celle-ci
        self._refresh_undo_redo_state()
        self._reload_timeline_preserving_selection()

    def _refresh_motion_inspector(self) -> None:
        """Aligne l'inspecteur Mouvement sur le clip sélectionné et la tête de lecture."""
        panel = self.properties_panel
        selected = panel.selected_clip
        if (
            selected is None
            or getattr(selected, "track_type", None) not in {"video", "graphics"}
        ):
            return
        try:
            clip = find_clip(self.project, selected.id)
        except KeyError:
            return
        panel.refresh_keyframe_diamonds(
            clip.transform_keyframes,
            self.playhead_seconds,
            transform=clip.transform,
        )
        editor = getattr(self, "graph_editor", None)
        if editor is not None and editor.isVisible():
            if editor.clip_id != clip.id:
                editor.refresh()
            else:
                editor.update_playhead()

    def on_transform_keyframe_added(
        self,
        clip_id: str,
        property_name: str,
        clip_local_time: float,
        value: float,
    ):
        if not bool(getattr(self, "_transform_session_active", False)):
            self._finalize_graphic_history()
        try:
            set_transform_keyframe(
                self.project, clip_id, property_name, clip_local_time, value
            )
        except ValueError as exc:
            self._report_edit_refused(exc)
            self._refresh_motion_inspector()
            return
        try:
            clip = find_clip(self.project, clip_id)
        except KeyError:
            return
        # Même debounce que le slider de la base : un glisser qui réécrit
        # l'image-clé sous la tête ne doit pas empiler une entrée par cran.
        self._schedule_transform_history(translate("history.keyframes.add"))
        self.properties_panel.update_transform_from_clip(
            clip.transform,
            clip.transform_keyframes,
            playhead_seconds=self.playhead_seconds,
        )
        self._sync_preview_to_timeline()
        self._mark_dirty()

    def on_transform_keyframe_removed(
        self, clip_id: str, property_name: str, clip_local_time: float
    ):
        self._finalize_graphic_history()
        self._finalize_transform_session()
        try:
            remove_transform_keyframe(
                self.project, clip_id, property_name, clip_local_time
            )
        except ValueError as exc:
            self._report_edit_refused(exc)
            return
        clip = find_clip(self.project, clip_id)
        self._record_history(translate("history.keyframes.remove"))
        self._reload_timeline_preserving_selection(clip_id)
        if clip is not None:
            self.properties_panel.update_transform_from_clip(
                clip.transform,
                clip.transform_keyframes,
                playhead_seconds=self.playhead_seconds,
            )
        self._sync_preview_to_timeline()
        self._mark_dirty()

    def on_transform_reset(self, clip_id: str) -> None:
        self._finalize_graphic_history()
        self._finalize_transform_session()
        try:
            reset_clip_transform(self.project, clip_id)
        except ValueError as exc:
            self._report_edit_refused(exc)
            return
        clip = find_clip(self.project, clip_id)
        self._record_history(translate("history.motion.reset"))
        self._reload_timeline_preserving_selection(clip_id)
        if clip is not None:
            self.properties_panel.update_transform_from_clip(
                clip.transform,
                clip.transform_keyframes,
                playhead_seconds=self.playhead_seconds,
            )
        self._sync_preview_to_timeline()
        self._mark_dirty()
