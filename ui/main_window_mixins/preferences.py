"""Méthodes de ``MainWindow`` regroupées : preferences."""

from __future__ import annotations

from PySide6.QtWidgets import QApplication
from core.user_settings import UserSettings, VALID_LANGUAGES, VALID_THEME_MODES
from dataclasses import replace
from ui import i18n
from ui.preferences_dialog import PreferencesDialog


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
        )

    def show_preferences(self) -> None:
        """Ouvre la fenêtre ``Préférences``."""
        dialog = PreferencesDialog(
            current_theme=self.theme_manager.requested_mode,
            current_language_code=i18n.current_language(),
            current_performance=self.runtime.requested_profile,
            current_preview_quality=self.runtime.requested_quality,
            current_render_quality=self._render_quality,
            parent=self,
        )
        dialog.theme_changed.connect(self.on_user_setting_changed)
        dialog.language_changed.connect(self.on_user_setting_changed)
        dialog.performance_changed.connect(self.on_performance_setting_changed)
        dialog.preview_quality_changed.connect(self.on_preview_quality_changed)
        dialog.render_quality_changed.connect(self.on_render_quality_changed)
        dialog.restore_defaults_requested.connect(self._restore_default_preferences)
        dialog.exec()

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
                pass
        self._refresh_preview_cache_state()

    def _apply_settings(self, settings: UserSettings) -> None:
        # Application du thème dans Qt.
        self.theme_manager.set_mode(settings.theme_mode)
        self.theme_manager.apply_to(QApplication.instance())
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
