"""Niveau d'usage des menus contextuels de Calques, des dossiers, de la bande d'automation (ADR-0006, lot suivant).

Les menus sont construits à chaque ouverture et lisent le niveau du panneau : rien n'est désactivé (``setEnabled``), aucun
widget n'est reparenté. Les raccourcis des commandes de calque restent attachés à la fenêtre à tous les niveaux.
"""

from __future__ import annotations

import pytest
from context_menu_dumps import all_dumps, layers_buttons
from menu_order import menu_tree
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QMenu
from test_scopes import _window

from core.usage_level import UsageLevel
from ui import i18n
from ui.layers_panel import LayersPanel

SIMPLE, STUDIO, ADVANCED = UsageLevel.SIMPLE, UsageLevel.STUDIO, UsageLevel.ADVANCED


@pytest.fixture
def window(qtbot, monkeypatch, tmp_path):
    i18n.set_language("fr")
    main = _window(qtbot, monkeypatch, tmp_path)
    main.set_usage_level("advanced")
    return main


def _texts(tree) -> list[str]:
    out: list[str] = []
    for entry in tree:
        if isinstance(entry, dict):
            for title, children in entry.items():
                out.append(title)
                out.extend(_texts(children))
        elif entry != "-":
            out.append(entry)
    return out


def _clean(tree) -> bool:
    if tree and (tree[0] == "-" or tree[-1] == "-"):
        return False
    return not any(a == "-" and b == "-" for a, b in zip(tree, tree[1:]))


LAYER_ENTRIES = {  # libellé -> niveau minimal (menu d'un calque texte)
    "Renommer": SIMPLE,
    "Dupliquer": SIMPLE,
    "Supprimer": SIMPLE,
    "Grouper": STUDIO,
    "Copier transform, masques et effets": STUDIO,
    "Coller les attributs…": STUDIO,
    "Enregistrer comme preset…": STUDIO,
    "Parent": ADVANCED,
    "Flou de mouvement": ADVANCED,
}


@pytest.mark.parametrize(
    "level", [SIMPLE, STUDIO, ADVANCED], ids=lambda level: level.value
)
def test_the_layer_menu_shows_each_entry_from_its_level(window, level):
    window.set_usage_level(level)
    dumps = all_dumps(window)
    shown = _texts(dumps["ctx_layer"])
    for label, minimum in LAYER_ENTRIES.items():
        assert (label in shown) == level.at_least(minimum), (
            f"{label!r} au niveau {level.value}"
        )
    group = _texts(dumps["ctx_layer_group"])
    for label in ("Dégrouper", "Entrer dans le groupe"):
        assert (label in group) == level.at_least(STUDIO)
    assert "Flou de mouvement" not in group, "un groupe n'a jamais de flou de mouvement"
    for name in (
        "ctx_layer",
        "ctx_layer_group",
        "ctx_layer_background",
        "ctx_layer_add_button",
    ):
        assert _clean(dumps[name]), (
            f"séparateurs mal placés dans {name} : {dumps[name]}"
        )


@pytest.mark.parametrize(
    "level", [SIMPLE, STUDIO, ADVANCED], ids=lambda level: level.value
)
def test_the_add_menu_and_the_toolbar_follow_the_level(window, level):
    window.set_usage_level(level)
    add = _texts(all_dumps(window)["ctx_layer_background"])
    for label in ("Texte", "Aplat de couleur", "Image…"):
        assert label in add
    studio = level.at_least(STUDIO)
    for label in ("Forme", "Contrôleur (null)", "Calque d'effets (adjustment)"):
        assert (label in add) == studio
    toolbar = layers_buttons(window)
    assert (
        ("group_button" in toolbar) == studio
        and ("presets_button" in toolbar) == studio
        and "add_button" in toolbar
    )
    panel = window.project_panel.graphics_view.layers_panel
    assert ("Forme" in [a.text() for a in panel.add_button.menu().actions()]) == studio


def test_going_back_up_restores_the_buttons_without_losing_the_presets(window):
    panel = window.project_panel.graphics_view.layers_panel
    window.set_usage_level(SIMPLE)
    assert panel.group_button.isHidden() and panel.presets_button.isHidden()
    window.set_usage_level(ADVANCED)
    assert not panel.group_button.isHidden() and not panel.presets_button.isHidden()


def test_no_layer_entry_is_disabled_by_the_level(window):
    """Un niveau montre ou cache : aucune entrée du menu d'un calque (même verrouillé) n'est grisée."""
    for level in (SIMPLE, STUDIO, ADVANCED):
        window.set_usage_level(level)
        panel = window.project_panel.graphics_view.layers_panel
        all_dumps(window)
        for item_index in range(panel.tree.topLevelItemCount()):
            menu = panel.build_context_menu(panel.tree.topLevelItem(item_index))
            assert all(a.isEnabled() for a in menu.actions())


def test_the_layer_panel_without_a_window_keeps_the_menu_of_before(qtbot):
    panel = LayersPanel()
    qtbot.addWidget(panel)
    assert panel.usage_level is ADVANCED
    assert "Forme" in [a.text() for a in panel.add_button.menu().actions()]
    assert (
        not panel.group_button.isHidden() or not panel.isVisible()
    )  # jamais caché hors fenêtre


def test_an_invalid_level_leaves_the_layer_panel_unchanged(qtbot):
    panel = LayersPanel()
    qtbot.addWidget(panel)
    panel.set_usage_level("simple")
    panel.set_usage_level("nimporte quoi")
    assert panel.usage_level is SIMPLE


def test_a_hidden_entry_keeps_its_command_attached_to_the_window(window):
    """Grouper, Dégrouper, Ajouter une forme… sortent du menu du panneau au niveau Simple : leurs commandes (donc leurs
    raccourcis) restent attachées à la fenêtre, comme celles des menus de la barre."""
    window.set_usage_level(SIMPLE)
    attached = {id(a) for a in window.actions()}
    commands = window.shortcuts.command_actions()
    assert commands and all(id(a) in attached for a in commands)


def test_what_the_simple_menu_offers_still_acts(window):
    window.set_usage_level(SIMPLE)
    panel = window.project_panel.graphics_view.layers_panel
    all_dumps(window)
    got = []
    panel.delete_requested.connect(got.append)
    panel.duplicate_requested.connect(got.append)
    menu = panel.build_context_menu(panel.tree.topLevelItem(0))
    for action in menu.actions():
        if action.text() in ("Dupliquer", "Supprimer"):
            action.trigger()
    assert len(got) == 2


# ---------------------------------------------------------------- bande d'automation


def _lane(window):
    from core.audio_automation import AutomationPoint

    panel = window.timeline_panel
    panel.set_automation_visible("A1", True)
    lane = panel.automation_lanes["A1"]
    lane.set_state([AutomationPoint(1.0, -3.0, 0.0)], locked=False)
    return lane


@pytest.mark.parametrize(
    "level", [SIMPLE, STUDIO, ADVANCED], ids=lambda level: level.value
)
def test_the_automation_lane_menu_keeps_the_cleanup_and_gates_the_editing(
    window, level
):
    from menu_order import load_scenario

    load_scenario(window, pytest.MonkeyPatch())
    window.set_usage_level(level)
    lane = _lane(window)
    on_point = _texts(
        menu_tree(lane.build_menu(QPointF(lane.x_of(1.0), lane.y_of(-3.0))))
    )
    on_band = _texts(
        menu_tree(lane.build_menu(QPointF(lane.x_of(4.0), lane.y_of(0.0))))
    )
    advanced = level.at_least(ADVANCED)
    assert (
        "Supprimer le point" in on_point
        and "Effacer la courbe" in on_point
        and "Effacer la courbe" in on_band
    )
    assert ("Remettre à 0 dB" in on_point) == advanced
    assert ("Ajouter un point ici" in on_band) == advanced


def test_a_locked_lane_opens_no_menu_at_any_level(window, monkeypatch):
    from PySide6.QtGui import QContextMenuEvent

    lane = _lane(window)
    lane.set_state(lane.points, locked=True)
    opened = []
    monkeypatch.setattr(QMenu, "exec", lambda self, *a, **k: opened.append(self))
    for level in (SIMPLE, STUDIO, ADVANCED):
        window.set_usage_level(level)
        lane.contextMenuEvent(
            QContextMenuEvent(
                QContextMenuEvent.Mouse,
                lane.rect().center(),
                lane.mapToGlobal(lane.rect().center()),
            )
        )
    assert opened == []


def test_a_menu_without_entry_is_not_opened(window, monkeypatch):
    from PySide6.QtGui import QContextMenuEvent

    lane = _lane(window)
    monkeypatch.setattr(lane, "build_menu", lambda position: QMenu(lane))
    opened = []
    monkeypatch.setattr(QMenu, "exec", lambda self, *a, **k: opened.append(self))
    lane.contextMenuEvent(
        QContextMenuEvent(
            QContextMenuEvent.Mouse,
            lane.rect().center(),
            lane.mapToGlobal(lane.rect().center()),
        )
    )
    assert opened == []


# ---------------------------------------------------------------- dossiers et nœuds : aucun retrait


def test_the_folder_menus_are_the_same_at_every_level(window):
    from core.library_organization import LibraryOrganization

    organization = LibraryOrganization(window.project)
    organization.create_folder("Rushes", folder_id="f")
    window.project_panel.set_library(organization)
    seen = {}
    for level in (SIMPLE, STUDIO, ADVANCED):
        window.set_usage_level(level)
        dumps = all_dumps(window)
        seen[level] = {k: v for k, v in dumps.items() if k.startswith("ctx_folder")}
    assert seen[SIMPLE] == seen[STUDIO] == seen[ADVANCED]
    assert _texts(seen[SIMPLE]["ctx_folder"]) == [
        "Renommer le dossier…",
        "Nouveau sous-dossier…",
        "Supprimer le dossier…",
    ]


def test_the_node_editors_appear_only_with_their_page(window):
    window.set_usage_level(SIMPLE)
    assert not any(k.startswith(("ctx_color", "ctx_comp")) for k in all_dumps(window))
    window.set_usage_level(STUDIO)
    keys = set(all_dumps(window))
    assert "ctx_color_node" in keys and "ctx_comp_node" not in keys
    window.set_usage_level(ADVANCED)
    assert {"ctx_color_node", "ctx_comp_node"} <= set(all_dumps(window))
