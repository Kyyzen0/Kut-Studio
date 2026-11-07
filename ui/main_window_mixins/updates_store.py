"""Méthodes de ``MainWindow`` pour la version Mac App Store : aucune recherche de mises à jour (ADR-0013, étape 4).

Même interface publique que :mod:`ui.main_window_mixins.updates`, mais **aucun** module de mise à jour n'est importé
(``core.updates``, ``core.update_*``, ``ui.update_*``) et aucun ``QNetworkAccessManager`` n'est construit : le Store
est le seul canal de mise à jour (Guideline 2.4.5 [A]). Chaque méthode qui pourrait être atteinte par un signal ou un
appel direct est donc un refus explicite, pas seulement un bouton caché.

Les quatre champs « mises à jour » des préférences restent relus et réécrits tels quels : le format du fichier de
réglages ne change pas, et l'interface ne les montre pas."""

from __future__ import annotations

import platform

import PySide6
from PySide6.QtCore import qVersion

from core.app_version import APP_VERSION
from core.user_settings import DEFAULT_CHECK_UPDATES, DEFAULT_INCLUDE_PRERELEASES
from ui import i18n


def _main_window():
    """Module ``ui.main_window`` résolu à l'appel (nom remplaçable en test, comme pour la version complète)."""
    from ui import main_window

    return main_window


def _system_label() -> str:
    version = platform.mac_ver()[0]
    name = f"macOS {version}" if version else platform.system()
    return f"{name} {platform.machine()}"


class UpdatesMixin:
    """Mixin de ``MainWindow`` (construction Store) : mises à jour absentes, boîte « À propos » seule."""

    def _init_updates(self, settings) -> None:
        self._update_check_enabled = False                      # jamais lu : aucune recherche, quelle que soit la valeur
        self._update_include_prereleases = False
        self._stored_update_fields = {
            "check_updates": bool(settings.check_updates),
            "include_prereleases": bool(settings.include_prereleases),
            "skipped_update_version": settings.skipped_update_version,
            "last_update_check": float(settings.last_update_check),
        }

    def _update_settings_fields(self) -> dict:
        """Les valeurs lues au démarrage, réécrites sans changement (le format du fichier ne bouge pas)."""
        return dict(getattr(self, "_stored_update_fields", {
            "check_updates": DEFAULT_CHECK_UPDATES,
            "include_prereleases": DEFAULT_INCLUDE_PRERELEASES,
            "skipped_update_version": "",
            "last_update_check": 0.0,
        }))

    def _apply_update_settings(self, settings) -> None:
        return None

    def set_update_check_enabled(self, enabled: bool) -> None:
        return None                                             # un signal ne peut rien activer ici

    def set_update_include_prereleases(self, enabled: bool) -> None:
        return None

    def check_for_updates(self) -> None:
        return None

    def open_update_offer(self) -> None:
        return None

    def _build_update_notice(self):
        """Pas de bouton « Mise à jour X » dans la barre supérieure."""
        return None

    def _retranslate_update_notice(self) -> None:
        return None

    def _shutdown_updates(self) -> None:
        return None

    def about_text(self) -> str:
        """Version, Mac App Store, système et bibliothèques : ni installation, ni clés, ni page de releases."""
        return i18n.translate(
            "update.about.store_text",
            version=APP_VERSION,
            system=_system_label(),
            qt=qVersion(),
            pyside=PySide6.__version__,
            python=platform.python_version(),
        )

    def show_about(self) -> None:
        _main_window().QMessageBox.about(self, i18n.translate("update.about.title"), self.about_text())
