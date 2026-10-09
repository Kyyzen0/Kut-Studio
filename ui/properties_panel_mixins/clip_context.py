"""Sélection d'un clip ou d'une transition dans l'inspecteur."""

from __future__ import annotations


from core.time_remapping import FreezeFrameMode, TimeRemapping
from core.visual_effects import (
    ClipTransform,
    TransformKeyframe,
)
from ui.i18n import translate


class ClipContextMixin:
    """Mixin de ``PropertiesPanel`` : sélection d'un clip ou d'une transition dans l'inspecteur."""

    def _emit_graphic_property(self, field_name: str, value: object) -> None:
        if (
            self.selected_clip is not None
            and self.selected_clip_track_type == "graphics"
        ):
            self.graphic_property_changed.emit(
                self.selected_clip.id, field_name, value
            )

    def _emit_advanced_transform(self, name: str, value: object) -> None:
        if self.selected_clip is not None and self.selected_clip_track_type in ("video", "graphics"):
            self.advanced_transform_changed.emit(self.selected_clip.id, name, value)

    def _emit_advanced_keyframe(self, name: str) -> None:
        if self.selected_clip is not None and self.selected_clip_track_type in ("video", "graphics"):
            self.advanced_keyframe_toggled.emit(self.selected_clip.id, name)

    def update_graphic_from_clip(self, graphic: object) -> None:
        self.graphics_group.set_graphic(graphic)

    def show_clip(self, view):
        pending_transform = None
        pending_keyframes: list[TransformKeyframe] | None = None
        self._push_signal_block()
        try:
            self.selected_transition_id = None
            # Changer de clip invalide toute transition affichée.
            self._set_group_condition(self.transition_group, False)
            if view is None:
                self.selected_clip = None
                self.selected_clip_track_type = None
                self._current_transform = None
                self._current_keyframes = []
                self.clip_name.setText(translate("no_clip_selected"))
                self.time_section.set_clip(None)
                self.clip_duration.setText("--")
                self.clip_position.setText("--")
                self._set_group_condition(self.subtitle_group, False)
                self._set_group_condition(self.graphics_group, False)
                self.update_graphic_from_clip(None)
                self.compositing_group.set_value(None)
                self.color_group.setEnabled(False)
                self.update_color_grade_from_clip(None)
                self.movement_group.setEnabled(False)
                for name, spin in self._spin_boxes.items():
                    spin.setEnabled(False)
                    spin.setValue(self._default_value_for(name))
                for name, slider in self._slider_widgets.items():
                    slider.setEnabled(False)
                    slider.setValue(
                        self._slider_position(name, self._default_value_for(name))
                    )
                for name, diamond in self._diamonds.items():
                    diamond.setEnabled(False)
                    self._set_diamond_checked(name, False)
                self.reset_movement_button.setEnabled(False)
                # Time remapping
                self.speed_group.setEnabled(False)
                # Effets : aucun clip vidéo sélectionné.
                self.update_effects_from_clip([], None)
                return

            self.selected_clip = view
            self.selected_clip_track_type = getattr(view, "track_type", None)
            # Effets : la vue porte déjà la liste d'effets du clip.
            self.update_effects_from_clip(
                list(getattr(view, "effects", ()) or ()),
                self.selected_clip_track_type,
            )
            duration = view.end - view.start
            self.clip_name.setText(view.label)
            self.clip_duration.setText(f"{duration:.2f}s")
            self.clip_position.setText(f"{view.start:.2f}s")
            is_subtitle = getattr(view, "track_type", None) == "subtitle"
            self._set_group_condition(self.subtitle_group, is_subtitle)
            if is_subtitle:
                style = getattr(view, "text_style", None)
                self.subtitle_editor.set_state(getattr(view, "text", ""), style)

            is_video_clip = getattr(view, "track_type", None) == "video"
            is_graphic_clip = getattr(view, "track_type", None) == "graphics"
            is_visual_clip = is_video_clip or is_graphic_clip
            self._set_group_condition(self.graphics_group, is_graphic_clip)
            self.update_graphic_from_clip(getattr(view, "graphic", None))
            self.compositing_group.set_value(getattr(view, "compositing", None))
            self.compositing_group.set_chroma_visible(is_video_clip)
            self.advanced_transform.set_skew_available(is_graphic_clip)
            self.graphics_group.setEnabled(
                is_graphic_clip and not bool(getattr(view, "locked", False))
            )
            color_editable = is_video_clip and not bool(getattr(view, "locked", False))
            self.color_group.setEnabled(color_editable)
            self.update_color_grade_from_clip(getattr(view, "color_grade", None))
            self.movement_group.setEnabled(is_visual_clip)
            for spin in self._spin_boxes.values():
                spin.setEnabled(is_visual_clip)
            for slider in self._slider_widgets.values():
                slider.setEnabled(is_visual_clip)
            for diamond in self._diamonds.values():
                diamond.setEnabled(is_visual_clip)
            self.reset_movement_button.setEnabled(is_visual_clip)
            if not is_visual_clip:
                self._current_transform = None
                self._current_keyframes = []
                for name, spin in self._spin_boxes.items():
                    spin.setValue(self._default_value_for(name))
                for name, slider in self._slider_widgets.items():
                    slider.setValue(
                        self._slider_position(name, self._default_value_for(name))
                    )
                for name in self._diamonds:
                    self._set_diamond_checked(name, False)
            if is_visual_clip and isinstance(getattr(view, "transform", None), ClipTransform):
                pending_transform = view.transform
                pending_keyframes = list(getattr(view, "keyframes", ()) or ())

            # Time remapping
            time_remapping = getattr(view, "time_remapping", None) or TimeRemapping()
            is_frozen = time_remapping.freeze_mode == FreezeFrameMode.FREEZE
            is_audio_clip = self.selected_clip_track_type == "audio"
            locked = getattr(view, "locked", False)
            enabled_tr = not locked
            
            # Vitesse
            self.speed_spinbox.blockSignals(True)
            self.speed_spinbox.setValue(time_remapping.speed)
            self.speed_spinbox.blockSignals(False)
            self.speed_spinbox.setEnabled(enabled_tr and not is_frozen)
            
            # Boutons preset
            for btn in [
                self.speed_0_25x_button,
                self.speed_0_5x_button,
                self.speed_1x_button,
                self.speed_2x_button,
                self.speed_4x_button,
            ]:
                btn.setEnabled(enabled_tr and not is_frozen)
            
            # Reverse
            self.reverse_button.blockSignals(True)
            self.reverse_button.setChecked(time_remapping.reverse)
            self.reverse_button.blockSignals(False)
            self.reverse_button.setEnabled(enabled_tr and not is_frozen)
            
            # Freeze frame
            self.freeze_frame_button.setEnabled(enabled_tr and not is_audio_clip)
            self.freeze_frame_button.setChecked(is_frozen)
            
            # Durée freeze frame
            self.freeze_duration_spinbox.blockSignals(True)
            self.freeze_duration_spinbox.setValue(time_remapping.freeze_duration)
            self.freeze_duration_spinbox.blockSignals(False)
            self.freeze_duration_row.setVisible(is_frozen)
            self.freeze_duration_spinbox.setEnabled(enabled_tr and is_frozen)
            self.freeze_duration_label.setText(f"{time_remapping.freeze_duration:.2f} s")
            self.freeze_duration_label.setVisible(is_frozen)
            
            # Bouton reset
            self.reset_speed_button.setEnabled(enabled_tr)
            self.time_section.set_clip(view)
            
            # Affichage des durées
            source_duration = getattr(view, "source_duration", 0.0)
            timeline_duration = duration
            self.source_duration_label.setText(translate("inspector.source_duration", seconds=format(source_duration, '.2f')))
            self.timeline_duration_label.setText(translate("inspector.timeline_duration", seconds=format(timeline_duration, '.2f')))
            
            # Désactiver les contrôles incompatibles
            if is_audio_clip and is_frozen:
                self._on_time_remapping_reset()
                
            self.speed_group.setEnabled(enabled_tr)
        finally:
            self._pop_signal_block()
            self.clip_shown.emit()
        if pending_transform is not None and pending_keyframes is not None:
            self.update_transform_from_clip(
                pending_transform,
                pending_keyframes,
                self._current_playhead_seconds,
            )

    def set_clip(self, view, track_name=None):
        self.show_clip(view)

    def show_transition(self, transition, outgoing_view, incoming_view, track_name: str) -> None:
        """Affiche l'édition d'une transition sans créer d'état métier local."""
        self.show_clip(None)
        self.selected_transition_id = transition.id
        self.transition_type_combo.blockSignals(True)
        index = self.transition_type_combo.findData(transition.type.value)
        self.transition_type_combo.setCurrentIndex(max(0, index))
        self.transition_type_combo.blockSignals(False)
        maximum = max(0.1, min(
            outgoing_view.end - outgoing_view.start,
            incoming_view.end - incoming_view.start,
        ) / 2.0)
        self.transition_duration_spin.blockSignals(True)
        self.transition_duration_spin.setMaximum(maximum)
        self.transition_duration_spin.setValue(min(transition.duration, maximum))
        self.transition_duration_spin.blockSignals(False)
        self.transition_from_label.setText(outgoing_view.label)
        self.transition_to_label.setText(incoming_view.label)
        self.transition_track_label.setText(track_name)
        self._set_group_condition(self.transition_group, True)

    def clear_transition(self) -> None:
        self.selected_transition_id = None
        self._set_group_condition(self.transition_group, False)
