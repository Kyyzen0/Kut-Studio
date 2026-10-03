"""Méthodes de ``MainWindow`` regroupées : presets."""

from __future__ import annotations

from PySide6.QtWidgets import QInputDialog, QMessageBox

from core.effects_library import (
    EffectCategory,
    apply_preset_to_clip,
    builtin_presets,
    snapshot_clip_preset,
)
from core.text_presets import TextPreset, builtin_text_presets, make_user_text_preset
from core.text_style import TextStyle
from core.timeline_operations import find_clip
from core.transition_presets import make_user_transition_preset
from ui import i18n
from ui.project_panel import SavePresetDialog, SaveTransitionPresetDialog


def _main_window():
    """Module ``ui.main_window`` résolu à l'appel (noms remplaçables en test)."""
    from ui import main_window

    return main_window


class PresetsMixin:
    """Mixin de ``MainWindow`` (presets)."""

    def _sync_effects_library_context(self) -> None:
        """Synchronise la bibliothèque d'effets avec le clip sélectionné."""
        clip_id = self._selected_video_clip_id()
        clip_has_effects = False
        if clip_id is not None:
            try:
                clip = find_clip(self.project, clip_id)
            except KeyError:
                clip = None
            clip_has_effects = bool(clip and clip.effects)
        self.project_panel.update_effects_clip_context(
            has_video_clip=clip_id is not None,
            clip_has_effects=clip_has_effects,
        )
        # La bibliothèque de transitions partage la même notion de
        # sélection : on l'aligne dans la foulée.
        self._sync_transitions_library_context()
        audio_clip_id = self._selected_audio_capable_clip_id()
        audio_clip_has_effects = False
        if audio_clip_id is not None:
            try:
                audio_clip_has_effects = bool(
                    find_clip(self.project, audio_clip_id).audio_effects
                )
            except KeyError:
                pass
        self.project_panel.update_audio_effects_clip_context(
            has_audio_clip=audio_clip_id is not None,
            clip_has_audio_effects=audio_clip_has_effects,
        )

    def _on_user_presets_changed(self) -> None:
        """Répercute les mutations du store vers la bibliothèque."""
        self.project_panel.set_user_effect_presets(
            self.user_preset_store.all()
        )

    def _sync_transitions_library_context(self) -> None:
        """Synchronise la bibliothèque de transitions avec la sélection."""
        selected = [
            view for view in self.timeline_panel.clip_views
            if view.id in self.timeline_panel.selected_clip_ids
            and view.track_type == "video"
        ]
        has_two_video_clips = (
            len(selected) == 2 and selected[0].track_id == selected[1].track_id
        )
        self.project_panel.update_transitions_clip_context(
            has_two_video_clips=has_two_video_clips,
        )

    def on_effect_preset_apply_requested(self, preset_id: str) -> None:
        """Applique un preset (intégré ou utilisateur) au clip vidéo courant."""
        clip_id = self._selected_video_clip_id()
        if clip_id is None:
            return
        # On cherche d'abord chez les intégrés, puis chez l'utilisateur.
        preset = next(
            (p for p in builtin_presets() if p.id == preset_id),
            None,
        )
        if preset is None:
            preset = self.user_preset_store.get(preset_id)
        if preset is None:
            self._report_edit_refused(i18n.translate("status.preset.not_found"))
            return
        try:
            apply_preset_to_clip(self.project, clip_id, preset)
        except (KeyError, ValueError) as exc:
            QMessageBox.warning(self, i18n.translate("dialog.title.effects_preset"), str(exc))
            return
        self._record_history(i18n.translate("history.preset.effects_apply"))
        self._refresh_effects_after_change(clip_id)

    def _selected_audio_capable_clip_id(self) -> str | None:
        """Clip sélectionné pouvant porter de l'audio (piste vidéo ou audio)."""
        view = getattr(self.properties_panel, "selected_clip", None)
        clip_id = getattr(view, "id", None)
        if clip_id is None or getattr(view, "track_type", None) not in {
            "video",
            "audio",
        }:
            return None
        return clip_id

    def _on_audio_effect_presets_changed(self) -> None:
        """Répercute presets utilisateur et favoris vers la bibliothèque."""
        store = self.audio_effect_preset_store
        self.project_panel.set_user_audio_effect_presets(store.all_user_presets())
        self.project_panel.set_audio_effect_favorites(store.favorites())

    def on_audio_effect_preset_apply_requested(self, preset_id: str) -> None:
        """Applique un préréglage d'effet audio au clip sélectionné."""
        from core.audio_effects_model import add_audio_effect_to_clip

        clip_id = self._selected_audio_capable_clip_id()
        if clip_id is None:
            return
        preset = self.audio_effect_preset_store.get_preset(preset_id)
        if preset is None:
            self._report_edit_refused(i18n.translate("status.preset.audio_not_found"))
            return
        try:
            add_audio_effect_to_clip(
                self.project,
                clip_id,
                preset.effect_type,
                params=preset.resolved_params(),
            )
        except (KeyError, ValueError) as exc:
            QMessageBox.warning(self, i18n.translate("dialog.title.audio_effect"), str(exc))
            return
        self._record_history(i18n.translate("history.preset.audio_apply"))
        self._refresh_effects_after_change(clip_id)

    def on_audio_effect_preset_save_requested(self) -> None:
        """Enregistre l'effet audio sélectionné du clip comme preset."""
        from core.audio_effects_library import make_user_audio_effect_preset

        clip_id = self._selected_audio_capable_clip_id()
        if clip_id is None:
            return
        try:
            clip = find_clip(self.project, clip_id)
        except KeyError:
            return
        effects = list(clip.audio_effects)
        if not effects:
            QMessageBox.information(
                self, i18n.translate("dialog.title.audio_effect"), i18n.translate("dialog.preset.no_audio_effect")
            )
            return
        selected_id = getattr(
            self.properties_panel, "_selected_audio_effect_id", None
        )
        effect = next((e for e in effects if e.id == selected_id), effects[0])
        name, accepted = QInputDialog.getText(
            self, i18n.translate("dialog.preset.save_audio_title"), i18n.translate("dialog.preset.name_label")
        )
        name = name.strip()
        if not accepted or not name:
            return
        try:
            preset = make_user_audio_effect_preset(
                name, "", effect.type, dict(effect.params)
            )
            self.audio_effect_preset_store.add_user_preset(preset)
        except ValueError as exc:
            QMessageBox.warning(self, i18n.translate("dialog.title.audio_effect"), str(exc))

    def on_audio_effect_preset_delete_requested(self, preset_id: str) -> None:
        """Supprime un preset audio utilisateur après confirmation."""
        preset = self.audio_effect_preset_store.get_preset(preset_id)
        if preset is None or preset.builtin:
            return
        confirm = QMessageBox.question(
            self,
            i18n.translate("dialog.preset.delete_title"),
            i18n.translate("dialog.preset.delete_text", name=preset.name),
        )
        if confirm != QMessageBox.Yes:
            return
        try:
            self.audio_effect_preset_store.remove_user_preset(preset_id)
        except KeyError:
            return

    def on_effect_preset_save_requested(self) -> None:
        """Ouvre le dialogue d'enregistrement d'un preset utilisateur."""
        clip_id = self._selected_video_clip_id()
        if clip_id is None:
            return
        try:
            clip = find_clip(self.project, clip_id)
        except KeyError:
            return
        if not clip.effects:
            _main_window().QMessageBox.information(
                self,
                i18n.translate("effects.library.dialog.title"),
                i18n.translate("effects.library.no_effects_to_save"),
            )
            return
        default_name = ""
        if clip.label:
            default_name = f"Preset « {clip.label} »"
        dialog = SavePresetDialog(
            self,
            default_name=default_name,
            default_category=EffectCategory.LOOK,
        )
        if dialog.exec() != dialog.Accepted:
            return
        name, description, category = dialog.result_data()
        if not name:
            return
        try:
            preset = snapshot_clip_preset(
                clip,
                name=name,
                description=description,
                category=category,
            )
        except ValueError as exc:
            self._report_edit_refused(exc)
            return
        try:
            self.user_preset_store.add(preset)
        except ValueError as exc:
            self._report_edit_refused(exc)
            return
        self._record_history(i18n.translate("history.preset.user_save"))

    def on_effect_preset_delete_requested(self, preset_id: str) -> None:
        """Supprime un preset utilisateur après confirmation."""
        preset = self.user_preset_store.get(preset_id)
        if preset is None:
            return
        if preset.builtin:
            _main_window().QMessageBox.information(
                self,
                i18n.translate("effects.library.delete"),
                i18n.translate("effects.library.user_builtin_lock"),
            )
            return
        confirm = _main_window().QMessageBox.question(
            self,
            i18n.translate("effects.library.delete"),
            i18n.translate("effects.library.delete_confirm").format(name=preset.name),
            _main_window().QMessageBox.Yes | _main_window().QMessageBox.No,
            _main_window().QMessageBox.No,
        )
        if confirm != _main_window().QMessageBox.Yes:
            return
        try:
            self.user_preset_store.remove(preset_id)
        except KeyError as exc:
            self._report_edit_refused(exc)
            return
        self._record_history(i18n.translate("history.preset.user_delete"))

    def add_transition_from_library(self, preset_id: str, duration: float) -> None:
        """Pose un preset de transition entre les deux clips sélectionnés.

        Conservé comme façade de compatibilité : la nouvelle
        bibliothèque publie ``transition_apply_requested(preset_id, duration)``
        qui aboutit ici. On garde aussi l'ancien nom ``add_transition_from_library``
        pour ne pas casser d'éventuels appels externes (tests).
        """
        self.on_transition_preset_apply_requested(preset_id, duration)

    def _on_transition_presets_changed(self) -> None:
        """Répercute les mutations du store vers la bibliothèque."""
        self.project_panel.set_transition_presets(
            self.transition_preset_store.all_presets(),
            favorites=self.transition_preset_store.favorites(),
        )

    def on_transition_preset_apply_requested(
        self, preset_id: str, duration: float
    ) -> None:
        """Applique un preset (intégré ou utilisateur) entre les deux clips sélectionnés."""
        preset = self.transition_preset_store.get_preset(preset_id)
        if preset is None:
            self.statusBar().showMessage(
                i18n.translate("transitions.library.no_results"), 5000
            )
            return
        selected = [
            view for view in self.timeline_panel.clip_views
            if view.id in self.timeline_panel.selected_clip_ids
            and view.track_type == "video"
        ]
        if len(selected) != 2:
            self.statusBar().showMessage(
                i18n.translate("transitions.library.two_clips_required"),
                5000,
            )
            return
        selected.sort(key=lambda view: view.start)
        if selected[0].track_id != selected[1].track_id:
            self.statusBar().showMessage(
                i18n.translate("status.transition.same_track"), 5000
            )
            return
        from core.transitions import add_transition
        try:
            transition = add_transition(
                self.project,
                selected[0].id,
                selected[1].id,
                preset.transition_type,
                float(duration),
            )
        except (KeyError, ValueError) as error:
            self.statusBar().showMessage(
                i18n.translate("status.transition.refused", error=error), 6000
            )
            return
        self._record_history(i18n.translate("history.transition.add"))
        self.timeline_panel.set_project(self.project)
        self._update_timeline_duration()
        self._mark_dirty()
        self.timeline_panel.select_transition(transition.id)
        self.statusBar().showMessage(
            i18n.translate("status.transition.added_named", name=preset.name), 3000
        )

    def on_transition_preset_save_requested(self) -> None:
        """Ouvre le dialogue d'enregistrement d'un preset utilisateur."""
        selected = [
            view for view in self.timeline_panel.clip_views
            if view.id in self.timeline_panel.selected_clip_ids
            and view.track_type == "video"
        ]
        if len(selected) != 2:
            self.statusBar().showMessage(
                i18n.translate("transitions.library.two_clips_required"),
                5000,
            )
            return
        selected.sort(key=lambda view: view.start)
        if selected[0].track_id != selected[1].track_id:
            self.statusBar().showMessage(
                i18n.translate("status.transition.same_track"), 5000
            )
            return
        from core.timeline_operations import find_clip

        # Si une transition existe déjà entre ces deux clips, on en
        # capture le type et la durée pour pré-remplir le dialogue.
        existing_type = "crossfade"
        existing_duration = 0.5
        for transition in self.project.transitions:
            if (
                transition.from_clip_id == selected[0].id
                and transition.to_clip_id == selected[1].id
            ):
                existing_type = transition.type.value
                existing_duration = float(transition.duration)
                break
        try:
            from_clip = find_clip(self.project, selected[0].id)
            to_clip = find_clip(self.project, selected[1].id)
        except (KeyError, ValueError) as exc:
            self.statusBar().showMessage(str(exc), 5000)
            return
        default_name = (
            f"{from_clip.label or 'Plan'} → {to_clip.label or 'Plan'}"
        )
        dialog = SaveTransitionPresetDialog(
            self,
            default_name=default_name,
            default_type=existing_type,
            default_duration=existing_duration,
        )
        if dialog.exec() != dialog.Accepted:
            return
        name, description, transition_type, duration = dialog.result_data()
        if not name:
            return
        try:
            preset = make_user_transition_preset(
                name=name,
                description=description,
                transition_type=transition_type,
                default_duration=duration,
            )
        except ValueError as exc:
            self.statusBar().showMessage(str(exc), 5000)
            return
        try:
            self.transition_preset_store.add_user_preset(preset)
        except ValueError as exc:
            self.statusBar().showMessage(str(exc), 5000)
            return
        self._record_history(i18n.translate("history.transition.save_custom"))

    def on_transition_preset_delete_requested(self, preset_id: str) -> None:
        """Supprime un preset utilisateur après confirmation."""
        preset = self.transition_preset_store.get_preset(preset_id)
        if preset is None:
            return
        if preset.builtin:
            _main_window().QMessageBox.information(
                self,
                i18n.translate("transitions.library.delete"),
                i18n.translate("transitions.library.user_builtin_lock"),
            )
            return
        confirm = _main_window().QMessageBox.question(
            self,
            i18n.translate("transitions.library.delete"),
            i18n.translate(
                "transitions.library.delete_confirm"
            ).format(name=preset.name),
            _main_window().QMessageBox.Yes | _main_window().QMessageBox.No,
            _main_window().QMessageBox.No,
        )
        if confirm != _main_window().QMessageBox.Yes:
            return
        try:
            self.transition_preset_store.remove_user_preset(preset_id)
        except KeyError as exc:
            self.statusBar().showMessage(str(exc), 5000)
            return
        self._record_history(i18n.translate("history.transition.delete_custom"))

    def on_transition_favorite_toggled(self, preset_id: str) -> None:
        """Bascule l'état favori d'un preset (intégré ou utilisateur)."""
        try:
            self.transition_preset_store.toggle_favorite(preset_id)
        except KeyError as exc:
            self.statusBar().showMessage(str(exc), 5000)
            return
        self.statusBar().showMessage(i18n.translate("status.transition.added"), 3000)

    def _on_text_presets_changed(self) -> None:
        """Répercute les mutations du store vers la bibliothèque."""
        self.project_panel.subtitle_view.set_presets(
            self.text_preset_store.all_presets()
        )

    def _resolve_text_preset(self, preset_id: str) -> TextPreset | None:
        """Cherche un modèle dans la bibliothèque complète."""
        for preset in builtin_text_presets():
            if preset.id == preset_id:
                return preset
        return self.text_preset_store.get(preset_id)

    def _apply_text_preset_to_clip(
        self, clip_id: str, preset: TextPreset
    ) -> bool:
        """Applique ``preset.style`` (et son texte par défaut si vide) au clip."""
        from core.timeline_operations import find_clip

        try:
            clip = find_clip(self.project, clip_id)
        except KeyError as exc:
            self.statusBar().showMessage(str(exc), 5000)
            return False
        clip.text_style = preset.style
        if not (clip.text or "").strip() and preset.default_text:
            clip.text = preset.default_text
        self._record_history(i18n.translate("history.subtitle.apply_template"))
        self._reload_timeline_preserving_selection(clip_id)
        self._refresh_subtitle_overlay()
        return True

    def _restore_clip_style(self, clip_id: str, style: TextStyle) -> None:
        """Restaure le style ``style`` sur ``clip_id`` (Undo / Redo)."""
        from core.timeline_operations import find_clip

        try:
            clip = find_clip(self.project, clip_id)
        except KeyError:
            return
        clip.text_style = style
        self._reload_timeline_preserving_selection(clip_id)
        self._refresh_subtitle_overlay()

    def on_text_preset_apply_requested(self, preset_id: str) -> None:
        """Applique un modèle au clip sélectionné (sélection timeline)."""
        preset = self._resolve_text_preset(preset_id)
        if preset is None:
            return
        # Cible = clip sélectionné, ou clip actif si le sous-titre
        # actif est connu.
        target_clip_id = self._resolve_subtitle_target_clip_id()
        if target_clip_id is None:
            self.statusBar().showMessage(
                i18n.translate("effects.library.apply_hint"), 5000
            )
            return
        self._apply_text_preset_to_clip(target_clip_id, preset)

    def on_text_preset_new_clip_requested(self, preset_id: str) -> None:
        """Crée un nouveau clip de sous-titre au playhead avec le modèle."""
        preset = self._resolve_text_preset(preset_id)
        if preset is None:
            return
        text = preset.default_text or preset.name
        self.add_subtitle_at_playhead(text, 3.0)
        # Si un clip vient d'être créé, on lui applique le style du modèle.
        from core.timeline_operations import find_clip

        subtitle_track = next(
            (t for t in self.project.tracks if t.type == "subtitle"), None
        )
        if subtitle_track is None:
            return
        for clip in reversed(subtitle_track.clips):
            if (clip.text or "").strip() == text:
                self._apply_text_preset_to_clip(clip.id, preset)
                break

    def on_text_preset_save_requested(
        self,
        name: str,
        description: str,
        style: TextStyle,
        default_text: str,
    ) -> None:
        """Ouvre un dialogue d'enregistrement d'un modèle utilisateur."""
        from PySide6.QtWidgets import QInputDialog

        new_name, accepted = QInputDialog.getText(
            self,
            i18n.translate("text.library.save_dialog.title"),
            i18n.translate("text.library.save_dialog.name"),
            text=name,
        )
        if not accepted or not new_name.strip():
            return
        try:
            preset = make_user_text_preset(
                name=new_name,
                description=description,
                style=style,
                default_text=default_text,
            )
        except ValueError as exc:
            self.statusBar().showMessage(str(exc), 5000)
            return
        try:
            self.text_preset_store.add(preset)
        except ValueError as exc:
            self.statusBar().showMessage(str(exc), 5000)
            return
        self.statusBar().showMessage(
            i18n.translate("status.template.saved", name=preset.name), 3000
        )

    def on_text_preset_delete_requested(self, preset_id: str) -> None:
        """Supprime un modèle utilisateur après confirmation."""
        from PySide6.QtWidgets import QMessageBox

        preset = self.text_preset_store.get(preset_id)
        if preset is None:
            return
        if preset.builtin:
            QMessageBox.information(
                self,
                i18n.translate("text.library.delete"),
                i18n.translate("text.library.user_builtin_lock"),
            )
            return
        confirm = QMessageBox.question(
            self,
            i18n.translate("text.library.delete"),
            i18n.translate("text.library.delete_confirm").format(
                name=preset.name
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        try:
            self.text_preset_store.remove(preset_id)
        except KeyError as exc:
            self.statusBar().showMessage(str(exc), 5000)
