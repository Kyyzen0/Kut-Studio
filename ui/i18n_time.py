"""Textes du remappage temporel (fusionnés dans :mod:`ui.i18n`) : propriété de vitesse, interpolation, audio, historique."""

from __future__ import annotations


def _t(fr: str, en: str, es: str) -> dict[str, str]:
    return {"fr": fr, "en": en, "es": es}


TIME_TRANSLATIONS: dict[str, dict[str, str]] = {
    "time.property.speed": _t("Vitesse", "Speed", "Velocidad"),
}
