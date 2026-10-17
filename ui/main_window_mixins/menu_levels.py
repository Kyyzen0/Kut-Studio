"""Niveau d'usage des menus de la fenêtre principale (ADR-0006, lot 4).

Un niveau ne fait que **retirer l'entrée du menu** (``QMenu.removeAction``) ou **la remettre à sa place d'origine**
(``QMenu.insertAction``) : rien n'est désactivé (``setEnabled``), rien n'est recréé, aucun widget n'est reparenté. Les
raccourcis clavier restent **toujours actifs** : un ``QAction`` caché par ``setVisible(False)`` perd son raccourci, et un
``QAction`` retiré de tous les menus (ou d'un sous-menu retiré) ne le reçoit plus de la fenêtre. Toutes les actions des commandes
(``ShortcutManager.command_actions``) sont donc attachées à la fenêtre (``QWidget.addAction``) ; leur raccourci reste actif
à tous les niveaux, entrée visible ou non. Les verrous (piste, calque) ne dépendent pas du niveau : ce sont les mêmes gardes
côté code.

La table ci-dessous donne le niveau minimal de chaque entrée (``docs/usage-levels.md``) : ce qui n'y figure pas est Simple.
Une entrée est repérée par la clé de traduction de son libellé ; un sous-menu par sa clé suivie de ``>`` (« Guides »
est à la fois une action cochable et un sous-menu du menu Calques). ``_menu_level_unresolved`` doit rester vide : une clé qui
ne retrouve aucune entrée (libellé renommé) fait échouer ``tests/test_usage_level_menus.py``.

Les séparateurs sont gardés dans la liste d'origine et ne sont montrés qu'entre deux entrées visibles ; au niveau Avancé la liste
est exactement celle d'avant le lot. Les entrées retirées restent dans ``_translated_actions`` / ``_translated_menus``
(retraduction à chaud). Le rail de la bibliothèque, la barre de la timeline et les menus contextuels suivent le même niveau
(``set_usage_level`` de chaque panneau).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Union

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu

from core.usage_level import UsageLevel
from core.workspace_state import PAGE_AUDIO, PAGE_COLOR, PAGE_COMPOSITION, PAGE_EDIT
from ui import i18n
from ui.side_rail import SECTION_LEVELS

_STUDIO, _ADVANCED = UsageLevel.STUDIO, UsageLevel.ADVANCED
Gate = Union[UsageLevel, tuple[UsageLevel, dict]]

MENU_GATES: dict[str, dict[str, Gate]] = {
    "edit_menu": {"action.ripple_delete": _STUDIO},
    "timeline_menu": {
        **{key: _ADVANCED for key in (
            "sequence.action.new", "sequence.action.nest_selection", "comp.action.convert", "comp.panel.new",
            "sequence.action.open_nested", "sequence.action.parent", "sequence.action.back", "sequence.action.forward",
            "sequence.action.rename", "sequence.action.duplicate", "sequence.action.delete",
            "multicam.menu.create", "multicam.menu.open_source", "multicam.menu.flatten", "multicam.menu.viewer",
            "multicam.menu.settings")},
        "menu.item.scene_cut": _STUDIO,
        "proxy.action.generate_selection": _STUDIO,
    },
    "layers_menu": {
        "common.add>": (UsageLevel.SIMPLE, {
            "menu.item.add_shape_layer": _STUDIO, "menu.item.add_null_layer": _STUDIO,
            "menu.item.add_adjustment_layer": _STUDIO}),
        **{key: _STUDIO for key in (
            "menu.item.group_layers", "menu.item.ungroup_layers", "menu.item.copy_layers", "menu.item.paste_layers",
            "menu.item.copy_attributes", "menu.item.paste_attributes", "menu.item.save_preset",
            "menu.item.safe_areas", "menu.item.show_guides", "menu.item.show_grid", "menu.item.viewer_snapping",
            "menu.item.show_center", "menu.item.align_to_frame>", "menu.item.show_guides>")},
        **{key: _ADVANCED for key in (
            "menu.item.motion_blur", "menu.item.motion_blur_settings", "menu.item.tracking_panel")},
    },
    "social_menu": {key: _STUDIO for key in (
        "beat.menu.title>", "social.menu.follow_reframe", "social.menu.ken_burns", "impact.menu.title>",
        "social.menu.audio>", "social.menu.format_versions", "leaderboard.menu", "social.menu.photos>")},
    "window_menu": {
        "shortcuts.command.page_color": _STUDIO, "menu.item.show_scopes": _STUDIO, "menu.item.use_proxies": _STUDIO,
        "shortcuts.command.page_composition": _ADVANCED, "menu.item.graph_editor": _ADVANCED,
        "debug.toggle": _ADVANCED,
    },
}


# Niveau minimal de chaque page (boutons de pages de la barre du haut et entrées du menu Fenêtre).
PAGE_LEVELS: dict[str, UsageLevel] = {
    PAGE_EDIT: UsageLevel.SIMPLE, PAGE_AUDIO: UsageLevel.SIMPLE, PAGE_COLOR: _STUDIO, PAGE_COMPOSITION: _ADVANCED,
}


@dataclass
class _LevelMenu:
    """Un menu (ou sous-menu) géré : sa liste complète d'origine et le niveau minimal de chaque entrée."""

    menu: QMenu
    items: list[QAction]
    levels: dict[int, UsageLevel] = field(default_factory=dict)

    def target(self, level: UsageLevel) -> list[QAction]:
        """Entrées à montrer, dans l'ordre d'origine ; séparateurs seulement entre deux entrées visibles."""
        shown = [a for a in self.items if a.isSeparator() or level.at_least(self.levels.get(id(a), UsageLevel.SIMPLE))]
        if len(shown) == len(self.items):
            return list(self.items)                                   # rien de caché : exactement la liste d'origine
        out: list[QAction] = []
        for action in shown:
            if action.isSeparator() and (not out or out[-1].isSeparator()):
                continue
            out.append(action)
        while out and out[-1].isSeparator():
            out.pop()
        return out

    def apply(self, level: UsageLevel) -> None:
        target = self.target(level)
        wanted = {id(a) for a in target}
        for action in self.items:
            if id(action) not in wanted and action in self.menu.actions():
                self.menu.removeAction(action)
        present = {id(a) for a in self.menu.actions()}
        before: QAction | None = None
        for action in reversed(target):                      # de la fin vers le début : ``before`` est déjà dans le menu
            if id(action) not in present:
                self.menu.insertAction(before, action)
            before = action


class MenuLevelsMixin:
    """Mixin de ``MainWindow`` : entrées de menu, rail, barre de la timeline et menus contextuels par niveau d'usage."""

    _menu_levels: list[_LevelMenu]
    _menu_level_unresolved: list[str]

    def _install_menu_levels(self) -> None:
        """À appeler quand tous les menus et panneaux sont construits : relève les listes d'origine puis applique le niveau."""
        self._menu_levels = []
        self._menu_level_unresolved = []
        by_action = {id(action): key for action, key in self._translated_actions}  # type: ignore[attr-defined]
        for bar_action in self.menuBar().actions():  # type: ignore[attr-defined]
            menu = bar_action.menu()
            spec = MENU_GATES.get(menu.objectName()) if menu is not None else None
            if menu is not None and spec:
                self._register_level_menu(menu, spec, by_action, menu.objectName())
        # Les raccourcis restent actifs à tous les niveaux : toute action de commande est attachée à la fenêtre.
        attached = {id(action) for action in self.actions()}  # type: ignore[attr-defined]
        for action in self.shortcuts.command_actions():  # type: ignore[attr-defined]
            if id(action) not in attached:
                self.addAction(action)  # type: ignore[attr-defined]
        self.usage_level_changed.connect(self._apply_menu_levels)  # type: ignore[attr-defined]
        self._apply_menu_levels()

    def _register_level_menu(self, menu: QMenu, spec: dict, by_action: dict, path: str) -> None:
        entry = _LevelMenu(menu, list(menu.actions()))
        resolved: set[str] = set()
        for action in entry.items:
            if action.isSeparator():
                continue
            submenu = action.menu()
            key = None
            if submenu is not None:
                key = next((k for k in spec if k.endswith(">") and i18n.translate(k[:-1]) == action.text()), None)
            else:
                key = by_action.get(id(action))
            gate = spec.get(key) if key is not None else None
            if gate is None:
                continue
            resolved.add(key)
            level, children = gate if isinstance(gate, tuple) else (gate, {})
            entry.levels[id(action)] = level
            if children and submenu is not None:
                self._register_level_menu(submenu, children, by_action, f"{path}/{key}")
        self._menu_level_unresolved.extend(f"{path}:{key}" for key in spec if key not in resolved)
        self._menu_levels.append(entry)

    def _apply_menu_levels(self, _value: str = "") -> None:
        level = self.usage_level  # type: ignore[attr-defined]
        self._leave_hidden_surfaces(level)
        for entry in self._menu_levels:
            entry.apply(level)
        for page, button in self.page_buttons.items():  # type: ignore[attr-defined]
            button.setVisible(level.at_least(PAGE_LEVELS.get(page, UsageLevel.SIMPLE)))
        for panel in (self.side_rail, self.timeline_panel, self.project_panel):  # type: ignore[attr-defined]
            panel.set_usage_level(level)

    def _leave_hidden_surfaces(self, level: UsageLevel) -> None:
        """Si la page ou la section du rail actives disparaissent au nouveau niveau, retombe d'abord sur Montage / Médias.

        Seul l'affichage change : le projet, la sélection, la position de lecture et le moniteur ne sont pas touchés.
        """
        page = self.workspace.page  # type: ignore[attr-defined]
        if not level.at_least(PAGE_LEVELS.get(page, UsageLevel.SIMPLE)):
            self.switch_page(PAGE_EDIT)  # type: ignore[attr-defined]
        rail = self.side_rail  # type: ignore[attr-defined]
        active = rail.active()
        if active is not None and not level.at_least(SECTION_LEVELS.get(active, UsageLevel.SIMPLE)):
            rail.set_active("media")
            self._on_side_rail_changed("media")  # type: ignore[attr-defined]


__all__ = ["MENU_GATES", "PAGE_LEVELS", "MenuLevelsMixin"]
