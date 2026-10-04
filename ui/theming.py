"""Changement de thème en direct : re-teinter ce que la feuille de style globale ne rattrape pas.

La feuille de style globale (``ThemeManager.apply_to``) suit le thème tout de suite. Mais de nombreux widgets construisent un style
**local** avec les couleurs du thème *au moment de leur création* (``setStyleSheet(f"... {COLORS['panel']} ...")``) : sans
traitement, un passage du sombre au clair laissait la moitié de l'interface dans l'ancien thème, avec du texte sombre sur fond sombre.

:func:`retheme_application` fait ce traitement après un changement de palette :

* chaque style local est relu, et chaque couleur de l'**ancienne** palette y est remplacée par la couleur du **même jeton** dans
  la nouvelle (:func:`remap_stylesheet`). La transparence propre au widget (``#rrggbbaa``) est conservée ;
* les widgets qui ont un ``refresh_theme()`` (états vides, aperçus peints…) le reçoivent ;
* tout est repeint ; les icônes, qui lisent la palette au moment de peindre (:class:`ui.icons._PaletteIconEngine`), suivent seules.

**Condition de validité** (gardée par ``tests/test_theme_live_switch.py``) : la correspondance doit être sans ambiguïté, donc deux
jetons de rôles différents ne partagent jamais la même valeur dans une palette (sinon on ne saurait pas lequel remplacer ; les
palettes les écartent d'une unité sur un canal, ce qui ne se voit pas). Une couleur qui n'est pas dans l'ancienne palette (un
style volontairement figé, une surcouche toujours sombre) n'est jamais touchée.
"""

from __future__ import annotations

import re
from dataclasses import fields

from ui.theme import ThemePalette

_COLOR = re.compile(r"#[0-9A-Fa-f]{6}(?:[0-9A-Fa-f]{2})?\b")


def color_map(old: ThemePalette, new: ThemePalette) -> dict[str, str]:
    """``{ancienne couleur #RRGGBB: nouvelle couleur #RRGGBB}`` pour chaque jeton de la palette (et chaque rang des palettes)."""
    mapping: dict[str, str] = {}
    for field in fields(old):
        before, after = getattr(old, field.name), getattr(new, field.name)
        pairs = zip(before, after) if isinstance(before, tuple) else [(before, after)]
        for left, right in pairs:
            if isinstance(left, str) and left.startswith("#"):
                mapping.setdefault(left[:7].upper(), right[:7].upper())
    return mapping


def ambiguities(old: ThemePalette, new: ThemePalette) -> dict[str, dict[str, list[str]]]:
    """Les couleurs de ``old`` qui ont plusieurs équivalents dans ``new`` : ``{couleur: {équivalent: [jetons]}}`` (vide : sans ambiguïté)."""
    table: dict[str, dict[str, list[str]]] = {}
    for field in fields(old):
        before, after = getattr(old, field.name), getattr(new, field.name)
        pairs = zip(before, after) if isinstance(before, tuple) else [(before, after)]
        for left, right in pairs:
            if isinstance(left, str) and left.startswith("#"):
                table.setdefault(left[:7].upper(), {}).setdefault(right[:7].upper(), []).append(field.name)
    return {color: targets for color, targets in table.items() if len(targets) > 1}


def remap_stylesheet(text: str, mapping: dict[str, str]) -> str:
    """Remplace dans ``text`` chaque couleur de ``mapping`` ; garde l'éventuelle transparence (``#rrggbbaa``) du texte d'origine."""

    def replace(match: re.Match[str]) -> str:
        color = match.group(0).upper()
        base, alpha = color[:7], color[7:]
        target = mapping.get(base)
        return match.group(0) if target is None else target + alpha

    return _COLOR.sub(replace, text)


def retheme_application(old: ThemePalette, new: ThemePalette) -> int:
    """Re-teinte tous les widgets de l'application après un passage de ``old`` à ``new`` ; retourne le nombre de styles réécrits."""
    from PySide6.QtWidgets import QApplication, QWidget

    mapping = color_map(old, new)
    rewritten = 0
    for top in QApplication.topLevelWidgets():
        for widget in (top, *top.findChildren(QWidget)):
            sheet = widget.styleSheet()
            if sheet:
                updated = remap_stylesheet(sheet, mapping)
                if updated != sheet:
                    widget.setStyleSheet(updated)
                    rewritten += 1
            hook = getattr(widget, "refresh_theme", None)
            if callable(hook):
                hook()
            widget.update()
    return rewritten


__all__ = ["ambiguities", "color_map", "remap_stylesheet", "retheme_application"]
