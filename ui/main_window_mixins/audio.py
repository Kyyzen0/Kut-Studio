"""Méthodes de ``MainWindow`` regroupées : audio."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtWidgets import QMessageBox

from ui import i18n


def _main_window():
    """Module ``ui.main_window`` résolu à l'appel (noms remplaçables en test)."""
    from ui import main_window

    return main_window


class AudioMixin:
    """Mixin de ``MainWindow`` (audio)."""

    def _connect_audio_controls(self) -> None:
        """Branche le mixeur et l'inspecteur sur les handlers audio.

        Aucun de ces widgets n'écrit dans le projet : ils émettent une
        intention, et les handlers ci-dessous appliquent le changement,
        enregistrent l'historique et marquent le projet modifié. C'est
        le même chemin que pour le déplacement d'un clip, ce qui évite
        deux conventions d'annulation.
        """
        mixer = self.mixer_panel
        mixer.volume_changed.connect(self.on_track_volume_changed)
        mixer.pan_changed.connect(self.on_track_pan_changed)
        mixer.mute_toggled.connect(self.on_track_mute_toggled)
        mixer.solo_toggled.connect(self.on_track_solo_toggled)
        mixer.arm_toggled.connect(self.on_track_arm_toggled)
        mixer.reset_requested.connect(self.on_track_audio_reset)
        mixer.inserts_requested.connect(self.on_track_inserts_requested)
        mixer.track_route_changed.connect(self.on_track_route_changed)
        mixer.bus_add_requested.connect(self.on_bus_add_requested)
        mixer.bus_volume_changed.connect(self.on_bus_volume_changed)
        mixer.bus_mute_toggled.connect(self.on_bus_mute_toggled)
        mixer.bus_inserts_requested.connect(self.on_bus_inserts_requested)
        mixer.bus_remove_requested.connect(self.on_bus_remove_requested)
        mixer.bus_rename_requested.connect(self.on_bus_rename_requested)
        mixer.project_refreshed.connect(self._refresh_inserts_dialog)
        mixer.project_refreshed.connect(self._refresh_bus_inserts_dialog)
        mixer.master_volume_changed.connect(self.on_master_volume_changed)
        mixer.master_mute_toggled.connect(self.on_master_mute_toggled)
        mixer.master_reset_requested.connect(self.on_master_reset)

        properties = self.properties_panel
        properties.audio_gain_changed.connect(self.on_clip_gain_changed)
        properties.audio_pan_changed.connect(self.on_clip_pan_changed)
        properties.audio_fade_changed.connect(self.on_clip_fade_changed)
        properties.audio_fades_reset.connect(self.on_clip_fades_reset)

        timeline = self.timeline_panel
        timeline.fade_changed_requested.connect(self.on_clip_fade_from_timeline)
        timeline.reset_clip_fades_requested.connect(self.on_clip_fades_reset)
        # Courbe de volume (bande sous les clips d'une piste audio) et menu ⋯ des pistes audio.
        timeline.automation_point_added.connect(self.on_track_automation_point_added)
        timeline.automation_point_removed.connect(self.on_track_automation_point_removed)
        timeline.automation_point_updated.connect(self.on_track_automation_point_updated)
        timeline.automation_point_moved.connect(self.on_track_automation_point_moved)
        timeline.automation_cleared.connect(self.on_track_automation_cleared)
        timeline.track_role_changed.connect(self.on_track_role_changed)
        timeline.ducking_pair_toggled.connect(self.on_ducking_pair_toggled)

        # Le volume Master est une préférence d'interface, pas du projet :
        # il vit dans les réglages utilisateur, jamais dans le ``.kut``.
        # Les valeurs initiales viennent des préférences chargées.
        mixer.set_master(self._master_gain_db, self._master_muted)
        return

    def on_track_volume_changed(self, track_id: str, value: float) -> None:
        track = self._find_audio_track(track_id)
        if track is None or track.locked:
            self.mixer_panel.refresh_track(track) if track else None
            return
        track.set_volume_db(value)
        self._record_audio_change(i18n.translate("mixer.volume"), merge_key=f"track-volume:{track_id}")

    def on_track_role_changed(
        self, track_id: str, role: str
    ) -> None:
        """Définit le rôle sémantique d'une piste audio."""
        from core.audio_automation import (
            AudioAutomationError,
            AudioAutomationService,
            TrackRole,
        )
        track = self._find_audio_track(track_id)
        if track is None:
            return
        if track.locked:
            self.mixer_panel.refresh_track(track)
            return
        service = AudioAutomationService()
        try:
            service.set_track_role(self.project, track.id, TrackRole(role))
        except AudioAutomationError as exc:
            _main_window().QMessageBox.warning(self, i18n.translate("dialog.title.mix"), str(exc))
            self.mixer_panel.refresh_track(track)
            return
        self._record_audio_change(i18n.translate("history.audio.track_role"))

    def on_track_automation_point_added(
        self, track_id: str, time_seconds: float,
        gain_db: float, fade_seconds: float,
    ) -> None:
        """Ajoute un point-clé d'automation sur une piste."""
        from core.audio_automation import (
            AudioAutomationError,
            AudioAutomationService,
        )
        track = self._find_audio_track(track_id)
        if track is None or track.locked:
            return
        service = AudioAutomationService()
        try:
            service.add_automation_point(
                self.project, track.id, time_seconds, gain_db, fade_seconds
            )
        except AudioAutomationError as exc:
            self._refuse_automation_edit(exc)
            return
        self._after_automation_edit(track.id, "history.audio.automation_add")

    def on_track_automation_point_removed(
        self, track_id: str, time_seconds: float
    ) -> None:
        track = self._find_audio_track(track_id)
        if track is None or track.locked:
            return
        from core.audio_automation import (
            AudioAutomationError,
            AudioAutomationService,
        )
        service = AudioAutomationService()
        try:
            service.remove_automation_point(
                self.project, track.id, time_seconds
            )
        except AudioAutomationError as exc:
            self._refuse_automation_edit(exc)
            return
        self._after_automation_edit(track.id, "history.audio.automation_remove")

    def on_track_automation_point_updated(
        self, track_id: str, time_seconds: float,
        gain_db: float, fade_seconds: float,
    ) -> None:
        track = self._find_audio_track(track_id)
        if track is None or track.locked:
            return
        from core.audio_automation import (
            AudioAutomationError,
            AudioAutomationService,
        )
        service = AudioAutomationService()
        try:
            service.update_automation_point(
                self.project, track.id, time_seconds,
                gain_db=gain_db, fade_seconds=fade_seconds,
            )
        except AudioAutomationError as exc:
            self._refuse_automation_edit(exc)
            return
        self._after_automation_edit(track.id, "history.audio.automation_edit")

    def on_track_automation_point_moved(
        self, track_id: str, time_seconds: float, new_time: float, gain_db: float,
    ) -> None:
        """Glisser d'un point dans la bande : nouvel instant et nouveau gain, une entrée d'historique."""
        track = self._find_audio_track(track_id)
        if track is None or track.locked:
            return
        from core.audio_automation import AudioAutomationError, AudioAutomationService

        try:
            AudioAutomationService().move_automation_point(self.project, track.id, time_seconds, new_time, gain_db)
        except AudioAutomationError as exc:
            self._refuse_automation_edit(exc)
            return
        self._after_automation_edit(track.id, "history.audio.automation_edit")

    def on_track_automation_cleared(self, track_id: str) -> None:
        """« Effacer la courbe » : la piste revient à son volume seul."""
        track = self._find_audio_track(track_id)
        if track is None or track.locked or track.automation.is_empty():
            return
        from core.audio_automation import AudioAutomationService

        AudioAutomationService().clear_automation(self.project, track.id)
        self._after_automation_edit(track.id, "history.audio.automation_clear")

    def _refuse_automation_edit(self, error: Exception) -> None:
        _main_window().QMessageBox.warning(self, i18n.translate("dialog.title.mix"), str(error))
        self.timeline_panel.refresh_clip_widgets()          # la bande retrouve la courbe du modèle

    def _after_automation_edit(self, track_id: str, label_key: str) -> None:
        """Historique, bande de la piste (gardée visible même vidée) et aperçu, qui entend la nouvelle courbe."""
        self._record_audio_change(i18n.translate(label_key))
        self.timeline_panel.keep_automation_visible(track_id)
        self.timeline_panel.refresh_clip_widgets()
        self._sync_preview_to_timeline()

    def on_ducking_pair_toggled(self, music_track_id: str, voice_track_id: str, enabled: bool) -> None:
        """Menu ⋯ « Baisser sous… » : crée ou retire l'association de ducking de cette paire de pistes."""
        if enabled:
            self.on_ducking_sidechain_added(music_track_id, voice_track_id)
            return
        sidechain = next(
            (item for item in getattr(self.project, "ducking_sidechains", [])
             if item.music_track_id == music_track_id and item.voice_track_id == voice_track_id),
            None,
        )
        if sidechain is not None:
            self.on_ducking_sidechain_removed(sidechain.id)

    def on_ducking_sidechain_added(
        self, music_track_id: str, voice_track_id: str,
    ) -> None:
        """Crée une association de ducking musique ← voix."""
        from core.audio_automation import (
            AudioAutomationError,
            AudioAutomationService,
            DuckingConfig,
        )
        music = self._find_audio_track(music_track_id)
        voice = self._find_audio_track(voice_track_id)
        if music is None or voice is None:
            return
        if music.locked or voice.locked:
            return
        service = AudioAutomationService()
        try:
            service.add_ducking_sidechain(
                self.project, music.id, voice.id,
                config=DuckingConfig(),
            )
        except AudioAutomationError as exc:
            _main_window().QMessageBox.warning(self, i18n.translate("dialog.title.mix"), str(exc))
            return
        self._record_audio_change(i18n.translate("history.audio.ducking_add"))
        self._sync_preview_to_timeline()

    def on_ducking_sidechain_removed(self, sidechain_id: str) -> None:
        from core.audio_automation import (
            AudioAutomationError,
            AudioAutomationService,
        )
        service = AudioAutomationService()
        try:
            service.remove_ducking_sidechain(self.project, sidechain_id)
        except AudioAutomationError as exc:
            _main_window().QMessageBox.warning(self, i18n.translate("dialog.title.mix"), str(exc))
            return
        self._record_audio_change(i18n.translate("history.audio.ducking_remove"))
        self._sync_preview_to_timeline()

    def on_track_pan_changed(self, track_id: str, value: float) -> None:
        track = self._find_audio_track(track_id)
        if track is None or track.locked:
            if track is not None:
                self.mixer_panel.refresh_track(track)
            return
        track.set_pan(value)
        self._record_audio_change(i18n.translate("mixer.pan"), merge_key=f"track-pan:{track_id}")

    def on_track_mute_toggled(self, track_id: str, muted: bool) -> None:
        track = self._find_audio_track(track_id)
        if track is None:
            return
        if track.locked:
            self.mixer_panel.refresh_track(track)
            self.timeline_panel.refresh_headers()
            return
        track.muted = bool(muted)
        self.timeline_panel.refresh_headers()
        self._record_audio_change(i18n.translate("mixer.mute"))

    def on_track_solo_toggled(self, track_id: str, solo: bool) -> None:
        track = self._find_audio_track(track_id)
        if track is None:
            return
        if track.locked:
            self.mixer_panel.refresh_track(track)
            self.timeline_panel.refresh_headers()
            return
        track.solo = bool(solo)
        # Le solo est un état global : toutes les tranches doivent refléter.
        self.timeline_panel.refresh_headers()
        self._sync_preview_to_timeline()
        self._record_audio_change(i18n.translate("mixer.solo"))

    def on_track_arm_toggled(self, track_id: str, armed: bool) -> None:
        track = self._find_audio_track(track_id)
        if track is None:
            return
        if track.locked:
            self.mixer_panel.refresh_track(track)
            self.timeline_panel.refresh_headers()
            return
        track.armed = bool(armed)
        self.timeline_panel.refresh_headers()
        self._record_audio_change(i18n.translate("mixer.arm"))

    def on_track_audio_reset(self, track_id: str) -> None:
        track = self._find_audio_track(track_id)
        if track is None or track.locked:
            if track is not None:
                self.mixer_panel.refresh_track(track)
            return
        track.reset_audio()
        self.mixer_panel.refresh_track(track)
        self._record_audio_change(i18n.translate("mixer.reset"))

    def on_master_volume_changed(self, value: float) -> None:
        self._master_gain_db = float(value)
        self._save_master_state()

    def on_master_mute_toggled(self, muted: bool) -> None:
        self._master_muted = bool(muted)
        self._save_master_state()

    def on_master_reset(self) -> None:
        self._master_gain_db = 0.0
        self._master_muted = False
        self.mixer_panel.set_master(0.0, False)
        self._save_master_state()

    def _save_master_state(self) -> None:
        """Persiste l'état Master dans les préférences utilisateur.

        Le Master n'appartient pas au projet : il décrit la session de
        mixage de l'utilisateur, pas le montage livré.
        """
        try:
            _main_window().save_user_settings(
                replace(
                    self._settings_snapshot(),
                    master_gain_db=self._master_gain_db,
                    master_muted=self._master_muted,
                )
            )
        except OSError:
            pass

    def _selected_audio_clip(self):
        """Clip audio sélectionné et sa piste, ou ``(None, None)``."""
        clip_id = self.timeline_panel.selected_clip_id
        if not clip_id or self.project is None:
            return None, None
        for track in self.project.tracks:
            for clip in track.clips:
                if clip.id == clip_id:
                    return clip, track
        return None, None

    def on_clip_gain_changed(self, value: float) -> None:
        clip, track = self._selected_audio_clip()
        if clip is None or track is None or track.locked:
            self._sync_audio_inspector()
            return
        clip.gain_db = float(value)
        self._record_audio_change(i18n.translate("audio.gain"), merge_key=f"clip-gain:{clip.id}")

    def on_clip_pan_changed(self, value: float) -> None:
        clip, track = self._selected_audio_clip()
        if clip is None or track is None or track.locked:
            self._sync_audio_inspector()
            return
        clip.pan = float(value)
        self._record_audio_change(i18n.translate("mixer.pan"), merge_key=f"clip-pan:{clip.id}")

    def on_clip_fade_changed(self, which: str, value: float) -> None:
        clip, track = self._selected_audio_clip()
        if clip is None or track is None or track.locked:
            self._sync_audio_inspector()
            return
        if which == "in":
            clip.set_fade_in(float(value))
        else:
            clip.set_fade_out(float(value))
        self.timeline_panel.refresh_clip_widgets()
        self._record_audio_change(
            i18n.translate("audio.fade_in" if which == "in" else "audio.fade_out"),
            merge_key=f"clip-fade-{which}:{clip.id}",
        )

    def on_clip_fade_from_timeline(self, clip_id: str, which: str, value: float) -> None:
        """Applique un fondu glisse dans la timeline."""
        clip, track = self._find_clip_and_track(clip_id)
        if clip is None or track is None or track.locked:
            return
        if which == "in":
            clip.set_fade_in(float(value))
        else:
            clip.set_fade_out(float(value))
        self._sync_audio_inspector()
        self._record_audio_change(
            i18n.translate("audio.fade_in" if which == "in" else "audio.fade_out"),
            merge_key=f"clip-fade-{which}:{clip_id}",
        )

    def on_clip_fades_reset(self, *args) -> None:
        """Double-clic ou bouton Réinitialiser : fondus à zéro."""
        clip_id = args[0] if args else self.timeline_panel.selected_clip_id
        clip, track = self._find_clip_and_track(clip_id) if clip_id else (None, None)
        if clip is None or track is None or track.locked:
            return
        clip.reset_fades()
        self.timeline_panel.refresh_clip_widgets()
        self._sync_audio_inspector()
        self._record_audio_change(i18n.translate("audio.reset_fades"))

    # ------------------------------------------------------------------
    # Inserts de piste
    # ------------------------------------------------------------------

    def on_track_inserts_requested(self, track_id: str) -> None:
        """Ouvre (ou ramène) la boîte des inserts de la piste."""
        track = self._find_audio_track(track_id)
        if track is None:
            return
        dialog = getattr(self, "_inserts_dialog", None)
        if dialog is None:
            from ui.track_inserts_dialog import TrackInsertsDialog

            dialog = TrackInsertsDialog(self)
            dialog.add_requested.connect(self.on_track_insert_added)
            dialog.remove_requested.connect(self.on_track_insert_removed)
            dialog.move_requested.connect(self.on_track_insert_moved)
            dialog.enabled_changed.connect(self.on_track_insert_enabled_changed)
            dialog.parameter_changed.connect(self.on_track_insert_parameter_changed)
            self._inserts_dialog = dialog
        dialog.set_track(track.id, track.name, track.inserts, locked=track.locked)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _refresh_inserts_dialog(self) -> None:
        """Remet la boîte d'inserts d'accord avec le modèle (annulation, ouverture d'un autre projet…)."""
        dialog = getattr(self, "_inserts_dialog", None)
        if dialog is None or not dialog.isVisible():
            return
        track = self._find_audio_track(dialog.track_id) if self.project is not None else None
        if track is None:
            dialog.hide()
            return
        dialog.set_track(track.id, track.name, track.inserts, locked=track.locked)

    def _edit_track_insert(self, operation, label_key: str, track_id: str, *args, merge_key: str | None = None):
        """Applique une opération pure sur les inserts, enregistre l'historique ; ``None`` si le modèle la refuse."""
        try:
            result = operation(self.project, track_id, *args)
        except (KeyError, ValueError) as exc:
            self._report_edit_refused(exc)
            self._refresh_inserts_dialog()
            return None
        self._record_audio_change(i18n.translate(label_key), merge_key=merge_key)
        return result

    def on_track_insert_added(self, track_id: str, effect_type: str) -> None:
        from core.audio_effects_model import AudioEffectType, add_track_insert

        added = self._edit_track_insert(
            lambda project, tid: add_track_insert(project, tid, AudioEffectType(effect_type)),
            "history.audio.effect_add", track_id,
        )
        if added is not None and getattr(self, "_inserts_dialog", None) is not None:
            self._inserts_dialog.select_insert(added.id)
            self._refresh_inserts_dialog()

    def on_track_insert_removed(self, track_id: str, effect_id: str) -> None:
        from core.audio_effects_model import remove_track_insert

        self._edit_track_insert(remove_track_insert, "history.audio.effect_remove", track_id, effect_id)

    def on_track_insert_moved(self, track_id: str, effect_id: str, delta: int) -> None:
        from core.audio_effects_model import move_track_insert

        self._edit_track_insert(move_track_insert, "history.audio.effect_move", track_id, effect_id, int(delta))

    def on_track_insert_enabled_changed(self, track_id: str, effect_id: str, enabled: bool) -> None:
        from core.audio_effects_model import set_track_insert_enabled

        self._edit_track_insert(
            set_track_insert_enabled, "history.audio.effect_edit", track_id, effect_id, bool(enabled)
        )

    def on_track_insert_parameter_changed(self, track_id: str, effect_id: str, name: str, value: float) -> None:
        from core.audio_effects_model import update_track_insert_parameters

        self._edit_track_insert(
            update_track_insert_parameters, "history.audio.effect_edit", track_id, effect_id, {name: value},
            merge_key=f"track-insert:{track_id}:{effect_id}:{name}",
        )

    # ------------------------------------------------------------------
    # Bus audio
    # ------------------------------------------------------------------

    def _edit_bus(self, operation, label_key: str, *args, merge_key: str | None = None):
        """Applique une opération pure sur les bus et enregistre l'historique ; ``None`` si le modèle la refuse."""
        if self.project is None:
            return None
        try:
            result = operation(self.project, *args)
        except (KeyError, ValueError) as exc:
            self._report_edit_refused(exc)
            self.mixer_panel.set_project(self.project)
            return None
        self._record_audio_change(i18n.translate(label_key), merge_key=merge_key)
        return result

    def on_bus_add_requested(self) -> None:
        from core.audio_buses import add_bus

        self._edit_bus(add_bus, "history.audio.bus_add")

    def on_bus_remove_requested(self, bus_id: str) -> None:
        from core.audio_buses import remove_bus

        self._edit_bus(remove_bus, "history.audio.bus_remove", bus_id)

    def on_bus_rename_requested(self, bus_id: str) -> None:
        from PySide6.QtWidgets import QInputDialog

        from core.audio_buses import find_bus, rename_bus

        try:
            bus = find_bus(self.project, bus_id)
        except KeyError:
            return
        name, accepted = QInputDialog.getText(
            self, i18n.translate("bus.rename.title"), i18n.translate("bus.rename.label"), text=bus.name
        )
        if accepted and name.strip() and name.strip() != bus.name:
            self._edit_bus(rename_bus, "history.audio.bus_rename", bus_id, name)

    def on_bus_volume_changed(self, bus_id: str, value: float) -> None:
        from core.audio_buses import set_bus_volume

        self._edit_bus(
            set_bus_volume, "history.audio.bus_volume", bus_id, float(value), merge_key=f"bus-volume:{bus_id}"
        )

    def on_bus_mute_toggled(self, bus_id: str, muted: bool) -> None:
        from core.audio_buses import set_bus_muted

        self._edit_bus(set_bus_muted, "history.audio.bus_mute", bus_id, bool(muted))

    def on_track_route_changed(self, track_id: str, bus_id: str) -> None:
        from core.audio_buses import route_track

        self._edit_bus(route_track, "history.audio.bus_route", track_id, bus_id)

    def on_bus_inserts_requested(self, bus_id: str) -> None:
        """Ouvre (ou ramène) la boîte des inserts du bus."""
        from core.audio_buses import find_bus

        try:
            bus = find_bus(self.project, bus_id)
        except KeyError:
            return
        dialog = getattr(self, "_bus_inserts_dialog", None)
        if dialog is None:
            from ui.track_inserts_dialog import TrackInsertsDialog

            dialog = TrackInsertsDialog(self, title_key="inserts.bus_title", hint_key="inserts.bus_hint")
            dialog.add_requested.connect(self.on_bus_insert_added)
            dialog.remove_requested.connect(self.on_bus_insert_removed)
            dialog.move_requested.connect(self.on_bus_insert_moved)
            dialog.enabled_changed.connect(self.on_bus_insert_enabled_changed)
            dialog.parameter_changed.connect(self.on_bus_insert_parameter_changed)
            self._bus_inserts_dialog = dialog
        dialog.set_track(bus.id, bus.name, bus.inserts)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _refresh_bus_inserts_dialog(self) -> None:
        dialog = getattr(self, "_bus_inserts_dialog", None)
        if dialog is None or not dialog.isVisible():
            return
        from core.audio_buses import find_bus

        try:
            bus = find_bus(self.project, dialog.track_id)
        except KeyError:
            dialog.hide()
            return
        dialog.set_track(bus.id, bus.name, bus.inserts)

    def on_bus_insert_added(self, bus_id: str, effect_type: str) -> None:
        from core.audio_buses import add_bus_insert
        from core.audio_effects_model import AudioEffectType

        added = self._edit_bus(
            lambda project, bid: add_bus_insert(project, bid, AudioEffectType(effect_type)),
            "history.audio.effect_add", bus_id,
        )
        if added is not None and getattr(self, "_bus_inserts_dialog", None) is not None:
            self._bus_inserts_dialog.select_insert(added.id)
            self._refresh_bus_inserts_dialog()

    def on_bus_insert_removed(self, bus_id: str, effect_id: str) -> None:
        from core.audio_buses import remove_bus_insert

        self._edit_bus(remove_bus_insert, "history.audio.effect_remove", bus_id, effect_id)

    def on_bus_insert_moved(self, bus_id: str, effect_id: str, delta: int) -> None:
        from core.audio_buses import move_bus_insert

        self._edit_bus(move_bus_insert, "history.audio.effect_move", bus_id, effect_id, int(delta))

    def on_bus_insert_enabled_changed(self, bus_id: str, effect_id: str, enabled: bool) -> None:
        from core.audio_buses import set_bus_insert_enabled

        self._edit_bus(set_bus_insert_enabled, "history.audio.effect_edit", bus_id, effect_id, bool(enabled))

    def on_bus_insert_parameter_changed(self, bus_id: str, effect_id: str, name: str, value: float) -> None:
        from core.audio_buses import update_bus_insert_parameters

        self._edit_bus(
            update_bus_insert_parameters, "history.audio.effect_edit", bus_id, effect_id, {name: value},
            merge_key=f"bus-insert:{bus_id}:{effect_id}:{name}",
        )

    def _record_audio_change(self, label: str, *, merge_key: str | None = None) -> None:
        """Enregistre une modification audio dans l'historique (``merge_key`` : geste continu, une seule entrée)."""
        if self.project is None:
            return
        self._record_history(label, merge_key=merge_key)
        self.mixer_panel.set_project(self.project)
        self.mixer_panel.set_master(self._master_gain_db, self._master_muted)

    def _sync_audio_inspector(self) -> None:
        """Rafraîchit le groupe audio de l'inspecteur depuis le modèle."""
        clip, track = self._selected_audio_clip()
        if clip is None or getattr(track, "type", None) != "audio":
            self.properties_panel.set_audio_clip(None)
            return
        self.properties_panel.set_audio_clip(clip, locked=bool(track.locked))

    def on_clip_audio_effect_added(
        self, clip_id: str, effect_type: str
    ) -> None:
        """Ajoute un effet audio choisi dans l'inspecteur au clip."""
        from core.audio_effects_model import (
            AudioEffectType,
            add_audio_effect_to_clip,
        )

        try:
            add_audio_effect_to_clip(
                self.project, clip_id, AudioEffectType(effect_type)
            )
        except (KeyError, ValueError) as exc:
            QMessageBox.warning(self, i18n.translate("dialog.title.audio_effect"), str(exc))
            return
        self._record_history(i18n.translate("history.audio.effect_add"))
        self._refresh_effects_after_change(clip_id)

    def on_clip_audio_effect_removed(
        self, clip_id: str, effect_id: str
    ) -> None:
        """Supprime un effet audio depuis l'inspecteur."""
        from core.audio_effects_model import remove_audio_effect_from_clip
        try:
            remove_audio_effect_from_clip(
                self.project, clip_id, effect_id
            )
        except (KeyError, ValueError) as exc:
            self._report_edit_refused(exc)
            return
        self._record_history(i18n.translate("history.audio.effect_remove"))
        self._refresh_effects_after_change(clip_id)

    def on_clip_audio_effect_moved(
        self, clip_id: str, effect_id: str, delta: int
    ) -> None:
        """Réordonne un effet audio depuis l'inspecteur."""
        from core.audio_effects_model import move_clip_audio_effect
        try:
            move_clip_audio_effect(
                self.project, clip_id, effect_id, int(delta)
            )
        except (KeyError, ValueError) as exc:
            self._report_edit_refused(exc)
            return
        self._record_history(i18n.translate("history.audio.effect_move"))
        self._refresh_effects_after_change(clip_id)

    def on_clip_audio_effect_enabled_changed(
        self, clip_id: str, effect_id: str, enabled: bool
    ) -> None:
        """Active ou désactive un effet audio."""
        from core.audio_effects_model import set_clip_audio_effect_enabled
        try:
            set_clip_audio_effect_enabled(
                self.project, clip_id, effect_id, bool(enabled)
            )
        except (KeyError, ValueError) as exc:
            self._report_edit_refused(exc)
            return
        self._record_history(i18n.translate("history.audio.effect_edit"))
        self._refresh_effects_after_change(clip_id)

    def on_clip_audio_effect_parameter_changed(
        self, clip_id: str, effect_id: str, name: str, value: float
    ) -> None:
        """Met à jour un paramètre d'effet audio depuis l'inspecteur."""
        from core.audio_effects_model import update_clip_audio_effect_parameters
        try:
            update_clip_audio_effect_parameters(
                self.project, clip_id, effect_id, {name: value}
            )
        except (KeyError, ValueError) as exc:
            self._report_edit_refused(exc)
            return
        self._record_history(i18n.translate("history.audio.effect_edit"))
        self._refresh_effects_after_change(clip_id)
