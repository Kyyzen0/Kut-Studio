"""Traductions du mode nuit (fusionnées dans :mod:`ui.i18n`) : commande, menu, Préférences."""

from __future__ import annotations


def _t(fr: str, en: str, es: str) -> dict[str, str]:
    return {"fr": fr, "en": en, "es": es}


NIGHT_TRANSLATIONS: dict[str, dict[str, str]] = {
    "shortcuts.command.night_mode": _t("Basculer le mode nuit", "Toggle night mode", "Alternar el modo noche"),
    "night.menu.toggle": _t("Mode nuit", "Night mode", "Modo noche"),
    "night.prefs.title": _t("Mode nuit", "Night mode", "Modo noche"),
    "night.prefs.enabled": _t("Activer le mode nuit", "Turn on night mode", "Activar el modo noche"),
    "night.prefs.intensity": _t("Intensité", "Intensity", "Intensidad"),
    "night.prefs.value": _t("{value} %", "{value}%", "{value} %"),
    "night.prefs.auto": _t("Automatique selon l'heure", "Automatic by time of day", "Automático según la hora"),
    "night.prefs.from": _t("De", "From", "Desde"),
    "night.prefs.to": _t("à", "to", "hasta"),
    "night.prefs.note": _t(
        "Seules les couleurs de l'interface changent : le moniteur, l'export, les scopes, la page Couleur et les miniatures "
        "gardent leurs couleurs d'origine. Une heure de début égale à l'heure de fin désactive l'horaire.",
        "Only the interface colors change: the monitor, export, scopes, Color page and thumbnails keep their original colors. "
        "A start time equal to the end time turns the schedule off.",
        "Solo cambian los colores de la interfaz: el monitor, la exportación, los scopes, la página Color y las miniaturas "
        "conservan sus colores originales. Una hora de inicio igual a la de fin desactiva el horario.",
    ),
}
