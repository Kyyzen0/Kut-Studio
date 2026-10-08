"""Méthodes de ``MainWindow`` regroupées : preferences."""

from __future__ import annotations

import logging

from PySide6.QtWidgets import QApplication, QMessageBox
from core.user_settings import UserSettings, VALID_LANGUAGES, VALID_THEME_MODES
from dataclasses import replace
from ui.theming import retheme_application
from ui import i18n
from ui.preferences_dialog import PreferencesDialog

LOGGER = logging.getLogger(__name__)


def _main_window():
    """Module ``ui.main_window`` résolu à l'appel (noms remplaçables en test)."""
    from ui import main_window

    return main_window


class PreferencesMixin:
    """Mixin de ``MainWindow`` (preferences)."""

    def _settings_snapshot(self) -> UserSettings:
        # L'état des scopes est lu depuis le panneau : c'est lui qui
        # fait foi, comme le thème ou la qualité d'aperçu pour le
        # reste. Les attributs sont lus de façon défensive car
        # l'instantané est aussi pris avant la construction du
        # panneau (au démarrage de la fenêtre).
        scopes_panel = getattr(self, "scopes_panel", None)
        if scopes_panel is None:
            scopes_layout = "quad"
            scopes_view = "waveform"
            scopes_levels = "video"
            scopes_alerts = False
        else:
            scopes_layout = scopes_panel.layout_mode().value
            scopes_view = scopes_panel.single_view().value
            scopes_levels = scopes_panel.levels().value
            scopes_alerts = scopes_panel.alerts_enabled()
        return UserSettings(
            theme_mode=self.theme_manager.requested_mode,
            language=i18n.current_language(),
            performance_profile=self.runtime.requested_profile,
            preview_quality=self.runtime.requested_quality,
            render_quality=self._render_quality,
            master_gain_db=self._master_gain_db,
            master_muted=self._master_muted,
            scopes_visible=bool(getattr(self, "_scopes_visible", False)),
            scopes_layout=scopes_layout,
            scopes_view=scopes_view,
            scopes_levels=scopes_levels,
            scopes_alerts_enabled=scopes_alerts,
            shortcuts=self.shortcuts.overrides(),
            **self._performance_settings_fields(),
            **self._update_settings_fields(),
            **self._social_settings_fields(),
            **self._auto_captions_settings_fields(),
        )

    def _performance_settings_fields(self) -> dict:
        """Réglages de performance courants (défauts avant la création des gestionnaires)."""
        proxies = getattr(self, "proxies", None)
        manager = getattr(self, "cache_manager", None)
        fields: dict = {}
        if proxies is not None:
            fields["proxies_enabled"] = proxies.enabled
            fields["proxy_profile"] = proxies.profile.id
        if manager is not None:
            fields["cache_max_gb"] = manager.max_bytes / (1024 ** 3)
        fields["export_encoder"] = getattr(self, "_export_encoder", "auto")
        decode = getattr(self, "_decode_mode", None)
        if decode is not None:
            fields["decode_mode"] = getattr(decode, "value", str(decode))
        fields["preview_backend"] = getattr(self, "_preview_backend_request", "auto")
        fields["flow_backend"] = self._flow_backend_request
        fields["time_ripple_timeline"] = self._time_ripple_timeline
        return fields

    def show_preferences(self) -> None:
        """Ouvre la fenêtre ``Préférences``."""
        dialog = PreferencesDialog(
            current_theme=self.theme_manager.requested_mode,
            current_language_code=i18n.current_language(),
            current_performance=self.runtime.requested_profile,
            current_preview_quality=self.runtime.requested_quality,
            current_render_quality=self._render_quality,
            current_check_updates=self._update_check_enabled,
            current_include_prereleases=self._update_include_prereleases,
            current_whisper_path=getattr(self, "_whisper_path", ""),
            current_whisper_model=getattr(self, "_whisper_model", ""),
            shortcut_manager=self.shortcuts,
            performance_host=self,
            parent=self,
        )
        dialog.theme_changed.connect(self.on_user_setting_changed)
        dialog.language_changed.connect(self.on_user_setting_changed)
        dialog.performance_changed.connect(self.on_performance_setting_changed)
        dialog.preview_quality_changed.connect(self.on_preview_quality_changed)
        dialog.render_quality_changed.connect(self.on_render_quality_changed)
        dialog.restore_defaults_requested.connect(self._restore_default_preferences)
        dialog.update_check_changed.connect(self.set_update_check_enabled)
        dialog.update_prereleases_changed.connect(self.set_update_include_prereleases)
        dialog.file_association_requested.connect(self._associate_project_files)
        dialog.whisper_path_changed.connect(self.set_whisper_path)
        dialog.whisper_model_changed.connect(self.set_whisper_model)
        self._preferences_dialog = dialog
        # « finished » part à la fermeture, avant que WA_DeleteOnClose ne détruise l'objet C++ :
        # attendre le retour d'exec() laisserait une fenêtre où la référence pointe sur un objet mort.
        dialog.finished.connect(lambda _result=0: setattr(self, "_preferences_dialog", None))
        try:
            dialog.exec()
        finally:
            self._preferences_dialog = None

    def _associate_project_files(self) -> None:
        """Associe ``.kut`` à l'application (Windows, Linux) sur demande, et annonce le résultat."""
        from core.file_association import associate_project_files

        result = associate_project_files()
        parent = self._preferences_dialog or self
        if result.status == "associated":
            QMessageBox.information(
                parent, i18n.translate("prefs.files.done_title"), i18n.translate("prefs.files.done_text"),
            )
        else:
            QMessageBox.warning(
                parent, i18n.translate("prefs.files.failed_title"),
                i18n.translate("prefs.files.failed_text", error=result.detail or result.status),
            )

    def _restore_default_preferences(self) -> None:
        """Restaure les réglages affichés par la boîte Préférences.

        Seuls les réglages visibles par l'utilisateur sont
        réinitialisés : le Master (gain/muet) est un réglage de session
        qui n'apparaît pas dans le dialogue — le reconstruire depuis
        ``UserSettings()`` ferait sauter un gain que l'utilisateur
        n'avait pas demandé de restaurer.
        """
        snapshot = self._settings_snapshot()
        self._apply_settings(
            replace(
                UserSettings(),
                master_gain_db=snapshot.master_gain_db,
                master_muted=snapshot.master_muted,
                # Les raccourcis ont leur propre réinitialisation.
                shortcuts=snapshot.shortcuts,
                # Proxies et cache ont leur onglet et leurs propres purges.
                proxies_enabled=snapshot.proxies_enabled,
                proxy_profile=snapshot.proxy_profile,
                cache_max_gb=snapshot.cache_max_gb,
                # Mémoire des mises à jour, pas des réglages : la version ignorée et l'heure de la dernière recherche.
                skipped_update_version=snapshot.skipped_update_version,
                last_update_check=snapshot.last_update_check,
                # Emplacement de whisper.cpp et de son modèle : une installation, pas un goût.
                whisper_path=snapshot.whisper_path,
                whisper_model=snapshot.whisper_model,
            )
        )

    def on_user_setting_changed(self, value: str) -> None:
        """Applique un thème ou une langue sans oublier les autres préférences."""
        settings = self._settings_snapshot()
        if value in VALID_THEME_MODES:
            settings = replace(settings, theme_mode=value)
        elif value in VALID_LANGUAGES:
            settings = replace(settings, language=value)
        self._apply_settings(settings)

    def on_performance_setting_changed(self, value: str) -> None:
        self._apply_settings(replace(self._settings_snapshot(), performance_profile=value))

    def on_preview_quality_changed(self, value: str) -> None:
        self._apply_settings(replace(self._settings_snapshot(), preview_quality=value))

    def on_render_quality_changed(self, value: str) -> None:
        """Qualité de rendu d'aperçu (tache 30) : persiste + invalide."""
        from core.preview_render import coerce_render_quality

        quality = coerce_render_quality(value)
        self._apply_settings(replace(self._settings_snapshot(), render_quality=quality))
        engine = getattr(self, "preview_engine", None)
        if engine is not None:
            try:
                engine.cancel_all()
            except Exception:
                LOGGER.warning(
                    "Annulation des rendus d'aperçu en échec après le changement de qualité : des rendus à l'ancienne qualité peuvent continuer",
                    exc_info=True,
                )
        self._refresh_preview_cache_state()

    def _apply_settings(self, settings: UserSettings) -> None:
        # Application du thème dans Qt : la feuille globale tout de suite, puis la re-teinte des styles locaux, que la feuille
        # globale ne rattrape pas (sans elle, un passage sombre → clair laissait la moitié de l'interface dans l'ancien thème).
        previous_palette = self.theme_manager.effective_palette
        self.theme_manager.set_mode(settings.theme_mode)
        self.theme_manager.apply_to(QApplication.instance())
        if self.theme_manager.effective_palette is not previous_palette:
            retheme_application(previous_palette, self.theme_manager.effective_palette)
        self.runtime.set_requested_profile(settings.performance_profile)
        self.runtime.set_preview_quality(settings.preview_quality)
        self._render_quality = settings.render_quality
        self._apply_runtime_hints()
        # État Master : preference de session, jamais du projet.
        self._master_gain_db = float(settings.master_gain_db)
        self._master_muted = bool(settings.master_muted)
        mixer = getattr(self, "mixer_panel", None)
        if mixer is not None:
            mixer.set_master(self._master_gain_db, self._master_muted)
        # Proxies et cache (aperçu seulement : l'export lit toujours les originaux).
        self._apply_performance_settings(settings)
        self._apply_update_settings(settings)
        # Application de la langue.
        if i18n.current_language() != settings.language:
            i18n.set_language(settings.language)
        # Persistance (écriture atomique dans le répertoire de
        # configuration, jamais dans le dépôt du projet).
        _main_window().save_user_settings(settings)
        # Mise à jour des libellés dépendant de la langue.
        self._retranslate_ui()

    def on_language_changed(self, code: str) -> None:
        """Callback i18n : retraduit l'interface à chaud."""
        self._retranslate_ui()
        # Persistance immédiate : la langue doit suivre les changements
        # sans effacer le profil de performance ni la qualité d'aperçu.
        _main_window().save_user_settings(replace(self._settings_snapshot(), language=code))
