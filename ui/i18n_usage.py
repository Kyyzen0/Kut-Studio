"""Traductions du niveau d'usage (fusionnées dans :mod:`ui.i18n`) : Préférences, menu Fenêtre, barre du haut."""

from __future__ import annotations


def _t(fr: str, en: str, es: str) -> dict[str, str]:
    return {"fr": fr, "en": en, "es": es}


USAGE_TRANSLATIONS: dict[str, dict[str, str]] = {
    # Boîte Préférences : titre du groupe, noms des niveaux, courte description en infobulle.
    "prefs.usage": _t("Niveau d'usage", "Usage level", "Nivel de uso"),
    "prefs.usage.simple": _t("Simple", "Simple", "Simple"),
    "prefs.usage.studio": _t("Studio", "Studio", "Studio"),
    "prefs.usage.advanced": _t("Avancé", "Advanced", "Avanzado"),
    "prefs.usage.simple.hint": _t("L'essentiel pour monter vite.", "The essentials, to edit fast.", "Lo esencial para editar rápido."),
    "prefs.usage.studio.hint": _t(
        "Plus de réglages pour un montage soigné.", "More controls for a polished edit.", "Más ajustes para un montaje cuidado."
    ),
    "prefs.usage.advanced.hint": _t("Tous les réglages.", "Every control.", "Todos los ajustes."),
    # Menu Fenêtre : sous-menu et trois choix (un mnémonique par choix, unique dans le sous-menu).
    "usage.menu.title": _t("Niveau d'usage", "Usage level", "Nivel de uso"),
    "usage.menu.simple": _t("&Simple", "&Simple", "&Simple"),
    "usage.menu.studio": _t("S&tudio", "S&tudio", "S&tudio"),
    "usage.menu.advanced": _t("&Avancé", "&Advanced", "A&vanzado"),
    # Barre du haut : puce (infobulle et nom accessible).
    "usage.chip.tooltip": _t("Niveau d'usage", "Usage level", "Nivel de uso"),
    "usage.chip.label": _t("Niveau : {level}", "Level: {level}", "Nivel: {level}"),
    "usage.chip.name": _t("Niveau d'usage : {level}", "Usage level: {level}", "Nivel de uso: {level}"),
    # Inspecteur : lien du bas (groupes réservés aux niveaux supérieurs) et sous-bloc « Avancé » d'un groupe.
    "usage.inspector.more": _t("Afficher plus d'options", "Show more options", "Mostrar más opciones"),
    "usage.inspector.fewer": _t("Afficher moins d'options", "Show fewer options", "Mostrar menos opciones"),
    "usage.inspector.more.tip": _t(
        "Montre les groupes de réglages d'un niveau d'usage supérieur.",
        "Shows the setting groups of a higher usage level.",
        "Muestra los grupos de ajustes de un nivel de uso superior.",
    ),
    "usage.inspector.fold": _t("Avancé", "Advanced", "Avanzado"),
    "usage.inspector.fold.name": _t("Réglages avancés : {group}", "Advanced settings: {group}", "Ajustes avanzados: {group}"),
    "usage.inspector.fold.tip": _t(
        "Montre ou replie les réglages de ce groupe d'un niveau d'usage supérieur.",
        "Shows or folds this group's settings of a higher usage level.",
        "Muestra o pliega los ajustes de este grupo de un nivel de uso superior.",
    ),
}
