"""Inventaire des contrôles classés par niveau d'usage (outil du test d'énumération, lot 5 de l'ADR-0006).

Tout part de la **vraie fenêtre** (hors écran), jamais d'une copie des tables :

* ``declared(window)`` : le niveau que le **code** attribue à chaque contrôle : ``MENU_GATES`` (par les listes d'origine des
  menus de la fenêtre), ``PAGE_LEVELS``, ``SECTION_LEVELS``, les outils de la barre de la timeline, et pour l'inspecteur
  ``_group_levels``, les ``LevelGate`` qui entourent les contrôles, les choix de listes (``_level_limits``). Un contrôle sans
  entrée dans une table est Simple : c'est le défaut du code, d'où le manifeste ci-dessous.
* ``observed(window, level)`` : ce que l'écran **montre** à ce niveau (menus, boutons, menus contextuels ; inspecteur sur
  plusieurs états de clip et onglets), sans geste de l'utilisateur.
* ``tests/data/usage_level_controls.json`` : le **manifeste** des contrôles (clé, niveau). Un contrôle neuf qui n'y figure pas
  fait échouer ``tests/test_usage_level_inventory.py`` : il faut le classer, puis l'ajouter au manifeste (revue visible dans la PR).
  Régénération, après avoir classé le contrôle dans le code :
  ``KUT_REGEN_USAGE_MANIFEST=1 pytest tests/test_usage_level_inventory.py -k regenerate`` (commande complète : ``docs/usage-levels.md``).

Une clé de menu est le chemin des libellés (``layers_menu/Ajouter/Texte``) ; un doublon de libellé dans le même menu reçoit ``#2``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractSlider,
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QGroupBox,
    QLineEdit,
    QListView,
    QPlainTextEdit,
    QTextEdit,
    QWidget,
)

from core.usage_level import UsageLevel

MANIFEST = Path(__file__).parent / "data" / "usage_level_controls.json"
LEVELS = (UsageLevel.SIMPLE, UsageLevel.STUDIO, UsageLevel.ADVANCED)
CONTROL_TYPES = (
    QAbstractButton,
    QAbstractSlider,
    QAbstractSpinBox,
    QComboBox,
    QLineEdit,
    QTextEdit,
    QPlainTextEdit,
)
# Raccourci affiché après le libellé (« Couper [Ctrl+K] »). Hors de l'f-string : un antislash y est une erreur avant Python 3.12.
_SHORTCUT_SUFFIX = re.compile(r" \[[^\]]*\]$")


class _Keys:
    """Clés uniques : un doublon de (chemin, libellé) reçoit ``#2``, ``#3``…"""

    def __init__(self) -> None:
        self.seen: dict[str, int] = {}

    def make(self, base: str) -> str:
        count = self.seen.get(base, 0) + 1
        self.seen[base] = count
        return base if count == 1 else f"{base}#{count}"


# ---------------------------------------------------------------- menus de la barre et menus contextuels


def _flat_tree(tree: list, prefix: str, keys: _Keys, out: list[str]) -> None:
    """Aplati un arbre de ``tests/menu_order.menu_tree`` en chemins de libellés (séparateurs ignorés)."""
    for entry in tree:
        if entry == "-":
            continue
        if (
            entry == ""
        ):  # un QWidgetAction (l'en-tête du menu d'un média) n'a pas de libellé
            out.append(keys.make(f"{prefix}/[en-tête]"))
            continue
        if isinstance(entry, dict):
            ((title, children),) = entry.items()
            path = keys.make(f"{prefix}/{title}")
            out.append(path)
            _flat_tree(children, path, keys, out)
        else:
            out.append(keys.make(f"{prefix}/{_SHORTCUT_SUFFIX.sub('', entry)}"))


def _tree_keys(name: str, tree: list) -> list[str]:
    out: list[str] = []
    _flat_tree(tree, name, _Keys(), out)
    return out


def _walk_menu(
    menu,
    path: str,
    floor: UsageLevel,
    registered: dict,
    keys: _Keys,
    out: dict[str, UsageLevel],
) -> None:
    entry = registered.get(id(menu))
    for action in entry.items if entry is not None else menu.actions():
        if action.isSeparator():
            continue
        own = (
            entry.levels.get(id(action), UsageLevel.SIMPLE)
            if entry is not None
            else UsageLevel.SIMPLE
        )
        level = own if own.at_least(floor) else floor
        key = keys.make(f"{path}/{action.text()}")
        out[key] = level
        if action.menu() is not None:
            _walk_menu(action.menu(), key, level, registered, keys, out)


def _declared_menus(window) -> dict[str, UsageLevel]:
    from ui.main_window_mixins.menu_levels import MENU_GATES

    registered = {id(entry.menu): entry for entry in window._menu_levels}
    out: dict[str, UsageLevel] = {}
    for bar_action in window.menuBar().actions():
        menu = bar_action.menu()
        if menu is not None:
            assert menu.objectName() in MENU_GATES or id(menu) not in registered
            _walk_menu(
                menu, menu.objectName(), UsageLevel.SIMPLE, registered, _Keys(), out
            )
    return out


def _declared_buttons(window) -> dict[str, UsageLevel]:
    from ui.main_window_mixins.menu_levels import PAGE_LEVELS
    from ui.side_rail import SECTION_LEVELS
    from ui.timeline_panel_mixins.usage_levels import STUDIO_TOOLS
    from menu_order import TOOLBAR_BUTTONS

    out: dict[str, UsageLevel] = {}
    for name in TOOLBAR_BUTTONS:
        studio = name.removesuffix("_button") in (*STUDIO_TOOLS, "record")
        out[f"toolbar/{name}"] = UsageLevel.STUDIO if studio else UsageLevel.SIMPLE
    for section in window.side_rail._sections:
        out[f"rail/{section.id}"] = SECTION_LEVELS.get(section.id, UsageLevel.SIMPLE)
    for page in window.page_buttons:
        out[f"pages/{page}"] = PAGE_LEVELS.get(page, UsageLevel.SIMPLE)
    out["layers_panel/add_button"] = (
        UsageLevel.SIMPLE
    )  # barre du panneau Calques ; Grouper et Préréglages : Studio
    out["layers_panel/group_button"] = UsageLevel.STUDIO
    out["layers_panel/presets_button"] = UsageLevel.STUDIO
    return out


def observed_surfaces(
    window, monkeypatch, shown: list, ids: dict[str, str]
) -> set[str]:
    """Menus, boutons et menus contextuels **à l'écran** au niveau courant (relevé de ``tests/menu_order.py``)."""
    from context_menu_dumps import all_dumps, layers_buttons
    from menu_order import asset_menu_dump, clip_menu_dump, track_menu_dump, window_dump

    dump = window_dump(window)
    keys: list[str] = []
    for name, tree in dump["menus"].items():
        keys.extend(_tree_keys(name, tree))
    keys.extend(f"toolbar/{n}" for n in dump["toolbar"])
    keys.extend(f"rail/{n}" for n in dump["rail"])
    keys.extend(f"pages/{n}" for n in dump["pages"])
    context = {
        "ctx_clip": clip_menu_dump(window, ids["plain"], shown),
        "ctx_multicam": clip_menu_dump(window, ids["multicam"], shown),
        "ctx_video_track": track_menu_dump(window, "V1"),
        "ctx_audio_track": track_menu_dump(window, "A1"),
        "ctx_asset": asset_menu_dump(window, monkeypatch, shown),
    }
    context.update(
        all_dumps(window)
    )  # panneau Calques, dossiers, bande d'automation, nœuds (Couleur, Composition)
    keys.extend(f"layers_panel/{n}" for n in layers_buttons(window))
    for name, tree in context.items():
        keys.extend(_tree_keys(name, tree))
    return set(keys)


def declared_surfaces(window) -> dict[str, UsageLevel]:
    return {**_declared_menus(window), **_declared_buttons(window)}


# ---------------------------------------------------------------- inspecteur


def inspector_groups(panel) -> dict[str, QWidget]:
    return {
        "project": panel._all_inspector_groups[0],
        "clip": panel._all_inspector_groups[1],
        "transition": panel.transition_group,
        "color": panel.color_group,
        "movement": panel.movement_group,
        "speed": panel.speed_group,
        "graphics": panel.graphics_group,
        "volume": panel._all_inspector_groups[7],
        "audio": panel.audio_group,
        "subtitle": panel.subtitle_group,
        "effects": panel.effects_group,
        "audio_effects": panel.audio_effects_group,
        "compositing": panel.compositing_group,
        "tracking": panel.tracking_group,
    }


def inspector_controls(panel) -> list[tuple[str, QWidget]]:
    """Les contrôles de l'inspecteur dans l'ordre de création : (clé, widget). Pas d'enfant interne d'un contrôle."""
    names = {id(widget): name for name, widget in inspector_groups(panel).items()}
    keys = _Keys()
    out: list[tuple[str, QWidget]] = []
    for widget in panel.scroll_area.widget().findChildren(QWidget):
        parent = widget.parentWidget()
        if widget.objectName().startswith("qt_") or isinstance(
            parent, (*CONTROL_TYPES, QListView)
        ):
            continue
        if not (
            isinstance(widget, CONTROL_TYPES)
            or (isinstance(widget, QGroupBox) and id(widget) in names)
        ):
            continue
        group = "panel"
        node: QWidget | None = widget
        while node is not None:
            if id(node) in names:
                group = names[id(node)]
                break
            node = node.parentWidget()
        label = widget.objectName() or (
            widget.text().strip() if isinstance(widget, QAbstractButton) else ""
        )  # jamais une valeur
        out.append(
            (keys.make(f"inspector/{group}/{type(widget).__name__}|{label}"), widget)
        )
    return out


def _floor(widget: QWidget, panel) -> UsageLevel:
    """Niveau déclaré d'un contrôle : son groupe (``_group_levels``) et les ``LevelGate`` qui l'entourent."""
    from ui.properties_widgets.level_gate import LevelGate

    level = UsageLevel.SIMPLE
    node: QWidget | None = widget
    while node is not None and node is not panel.scroll_area.widget():
        minimum = None
        if isinstance(node, LevelGate):
            minimum = node.minimum
        elif node in panel._group_levels:
            minimum = panel._group_levels[node]
        if minimum is not None and minimum.at_least(level):
            level = minimum
        node = node.parentWidget()
    return level


EFFECT_SCENARIOS = (
    "color_correction",
    "vignette",
    "sharpen",
    "blur",
    "glow",
    "chromatic_aberration",
)
"""Effets sélectionnés tour à tour (ADR-0008, étapes 4 et 5) : les réglages et les losanges d'images clés d'un effet n'existent
que pour l'effet affiché. La correction de couleur, la vignette, le flou, la lueur (rayon) et l'aberration chromatique ont des
paramètres animables ; la netteté n'en a aucun."""


def effect_view(kind: str):
    """Une vue de clip vidéo qui porte les effets de ``EFFECT_SCENARIOS`` (dans cet ordre)."""
    import dataclasses

    from core.effects_model import EffectType, create_effect
    from inspector_order import clip_states

    effects = tuple(
        create_effect(EffectType(name), effect_id=f"inv-{name}")
        for name in EFFECT_SCENARIOS
    )
    return dataclasses.replace(clip_states()["video"], effects=effects)


def show_effect(panel, kind: str) -> None:
    """Montre le clip aux effets avec ``kind`` sélectionné dans la liste (les réglages sont reconstruits)."""
    panel.show_clip(effect_view(kind))
    panel.effects_list.setCurrentRow(EFFECT_SCENARIOS.index(kind))
    QApplication.processEvents()


def inspector_scenarios(panel):
    """L'inspecteur tel quel, puis une fois par effet de ``EFFECT_SCENARIOS`` ; le dernier état est remis à « aucun clip »."""
    yield
    for kind in EFFECT_SCENARIOS:
        show_effect(panel, kind)
        yield
    panel.show_clip(None)


def declared_inspector(panel) -> dict[str, UsageLevel]:
    """Contrôles et choix de listes de l'inspecteur avec le niveau que le code leur donne (réunion des scénarios d'effets)."""
    out: dict[str, UsageLevel] = {}
    for _ in inspector_scenarios(panel):
        out.update(_declared_inspector_now(panel))
    return out


def _declared_inspector_now(panel) -> dict[str, UsageLevel]:
    controls = inspector_controls(panel)
    key_of = {id(widget): key for key, widget in controls}
    out = {key: _floor(widget, panel) for key, widget in controls}
    for limit in panel._level_limits:
        base = key_of[id(limit.combo)]
        for index in range(limit.combo.count()):
            data = limit.combo.itemData(index)
            wanted = limit.levels.get(data, UsageLevel.SIMPLE)
            out[f"{base}|choix={data}"] = (
                wanted
                if wanted.at_least(_floor(limit.combo, panel))
                else _floor(limit.combo, panel)
            )
    return out


def helper_keys(panel) -> set[str]:
    """Contrôles de navigation qui passent de l'autre côté du niveau à dessein : pliants, lien du bas, doublure d'une action."""
    helpers: set[str] = set()
    for _ in inspector_scenarios(panel):
        helpers |= _helper_keys_now(panel)
    return helpers


def _helper_keys_now(panel) -> set[str]:
    from ui.properties_widgets.level_gate import AdvancedFold, LevelGate

    inverse = {g for g in panel.findChildren(LevelGate) if g.inverse_of is not None}
    helpers: set[str] = set()
    for key, widget in inspector_controls(panel):
        node: QWidget | None = widget
        while node is not None:
            if (
                node in inverse
                or isinstance(node, AdvancedFold)
                or node is panel.more_options_button
            ):
                helpers.add(key)
                break
            node = node.parentWidget()
    return helpers


def visible_inspector(panel, states: dict, *, tabs=range(7)) -> set[str]:
    """Contrôles et choix montrés dans au moins un état de clip et un onglet, au niveau (et aux gestes) courants.

    Un passage de plus par effet de ``EFFECT_SCENARIOS`` : les réglages d'un effet n'existent qu'une fois l'effet choisi."""
    shown: set[str] = set()
    views = [*states.values(), *(effect_view(kind) for kind in EFFECT_SCENARIOS)]
    for position, view in enumerate(views):
        panel.show_clip(view)
        if position >= len(states):
            panel.effects_list.setCurrentRow(position - len(states))
        panel._set_group_condition(
            panel.transition_group, True
        )  # le contexte « une transition est sélectionnée »
        controls = inspector_controls(panel)
        key_of = {id(widget): key for key, widget in controls}
        for tab in tabs:
            panel._select_inspector_tab(tab)
            QApplication.processEvents()
            for key, widget in controls:
                if widget.isVisibleTo(panel):
                    shown.add(key)
            for limit in panel._level_limits:
                combo = limit.combo
                if not combo.isVisibleTo(panel):
                    continue
                view_ = combo.view()
                for index in range(combo.count()):
                    if not (
                        isinstance(view_, QListView)
                        and view_.isRowHidden(index)
                        and index != combo.currentIndex()
                    ):
                        shown.add(f"{key_of[id(combo)]}|choix={combo.itemData(index)}")
    panel.show_clip(None)
    return shown


def open_everything(panel) -> None:
    """Les gestes de l'ADR : « Afficher plus d'options » puis tous les pliants « Avancé » ouverts."""
    if not panel.more_options_button.isHidden():
        panel.more_options_button.setChecked(True)
    for fold in panel._all_folds():
        if not fold.isHidden():  # montré par le niveau ; le groupe qui le porte peut être sur un autre onglet
            fold.setChecked(True)
    QApplication.processEvents()


def close_everything(panel) -> None:
    for fold in panel._all_folds():
        fold.setChecked(False)
    panel.more_options_button.setChecked(False)
    QApplication.processEvents()


def load_manifest() -> dict[str, str]:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))
