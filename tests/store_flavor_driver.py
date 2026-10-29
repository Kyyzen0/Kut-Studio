"""Pilote d'un sous-processus de test : démarre l'application hors écran dans une saveur donnée et décrit ce qu'elle montre.

Usage : ``python store_flavor_driver.py store|source`` (``QT_QPA_PLATFORM=offscreen`` et ``KUT_STUDIO_CONFIG_DIR`` isolé
sont posés par l'appelant). La saveur Store est posée **comme le fait le bundle** : par un module
``core._build_flavor_generated`` que ``core.build_flavor`` trouve à l'import (jamais par variable d'environnement).
La dernière ligne de la sortie standard est un JSON : menus, boutons, préférences, À propos, modules de mise à jour importés."""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

UPDATE_MODULES = (
    "core.updates", "core.update_service", "core.update_install", "core.update_helper", "core.update_keys",
    "core.update_signature", "ui.update_auto_install", "ui.update_dialog", "ui.main_window_mixins.updates",
)


def main(flavor: str) -> int:
    if flavor == "store":
        generated = types.ModuleType("core._build_flavor_generated")
        generated.STORE = True  # type: ignore[attr-defined]
        sys.modules["core._build_flavor_generated"] = generated
    sys.argv = ["main.py"]
    report: dict[str, object] = {}

    from PySide6.QtWidgets import QApplication, QMenu

    def inspect(_self: object = None) -> int:
        from ui.main_window import MainWindow
        from ui.preferences_dialog import PreferencesDialog

        window = next(w for w in QApplication.topLevelWidgets() if isinstance(w, MainWindow))
        help_menu = window.findChild(QMenu, "help_menu")
        report["help"] = [a.text() for a in help_menu.actions() if not a.isSeparator()]
        report["notice_button"] = hasattr(window, "update_notice_button")
        report["has_check_action"] = hasattr(window, "check_updates_action")
        report["about"] = window.about_text()
        report["palette"] = [entry.title for entry in window.command_palette_entries()]
        dialog = PreferencesDialog()
        report["prefs_updates_box"] = dialog.updates_box is not None
        dialog.close()
        window.check_for_updates()                       # appel direct : ne doit rien construire en Store
        window.open_update_offer()
        window.set_update_check_enabled(True)
        report["check_enabled_after_signal"] = bool(window._update_check_enabled)
        report["modules"] = sorted(m for m in UPDATE_MODULES if m in sys.modules)
        return 0

    QApplication.exec = inspect  # type: ignore[method-assign,assignment]
    import main as application

    code = application.main()
    print(json.dumps(report))
    return int(code)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
