"""Boîtes de dialogue, messages d'erreur et messages de la barre d'état (fusionnés dans :mod:`ui.i18n`).

Titres et textes des ``QMessageBox`` / ``QInputDialog`` / ``QFileDialog`` de la fenêtre principale, refus d'opérations
et confirmations affichés dans la barre d'état. Les messages venus du cœur (``str(exc)``) restent ceux de
l'exception : voir la dette « erreurs du cœur » dans ``docs/i18n.md``.
"""

from __future__ import annotations


def _t(fr: str, en: str, es: str) -> dict[str, str]:
    return {"fr": fr, "en": en, "es": es}


DIALOGS_TRANSLATIONS: dict[str, dict[str, str]] = {
}

__all__ = ["DIALOGS_TRANSLATIONS"]
