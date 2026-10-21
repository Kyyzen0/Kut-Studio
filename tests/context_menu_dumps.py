"""Relevés des menus contextuels de l'ADR-0006 (lot « menus contextuels ») : Calques, dossiers, bande d'automation, nœuds.

Chaque fonction construit le **vrai** menu par le chemin du produit (``build_context_menu``, ``build_menu``) et rend son arbre
(``tests/menu_order.menu_tree``). Ces relevés ne sont PAS dans ``tests/data/menus_before_usage_levels.json`` (produit sur
``origin/main`` d'avant les niveaux, jamais régénéré sur une branche) : ils n'entrent que dans le manifeste.
"""

from __future__ import annotations

from menu_order import menu_tree
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QTreeWidgetItemIterator

from core.audio_automation import AutomationPoint
from core.graphics import GraphicType
from core.mograph_layers import LayerNode
from core.workspace_state import PAGE_COLOR, PAGE_COMPOSITION
from ui.layers_panel import COL_NAME, ID_ROLE


def _node(clip_id: str, name: str, kind: GraphicType, *, group: bool = False) -> LayerNode:
    return LayerNode(clip_id, name, kind, 0, "", "", True, False, 0.0, 5.0, "V1", group)


def layers_dumps(window) -> dict[str, list]:
    panel = window.project_panel.graphics_view.layers_panel
    panel.set_layers(
        [_node("t1", "Titre", GraphicType.TEXT), _node("g1", "Groupe", GraphicType.GROUP, group=True)],
        parent_choices={"t1": [("g1", "Groupe")]})
    items = {panel.tree.topLevelItem(i).data(COL_NAME, ID_ROLE): panel.tree.topLevelItem(i)
             for i in range(panel.tree.topLevelItemCount())}
    return {
        "ctx_layer": menu_tree(panel.build_context_menu(items["t1"])),
        "ctx_layer_group": menu_tree(panel.build_context_menu(items["g1"])),
        "ctx_layer_background": menu_tree(panel.build_context_menu(None)),
        "ctx_layer_add_button": menu_tree(panel.add_button.menu()),
    }


def layers_buttons(window) -> list[str]:
    """Boutons de la barre du panneau Calques que l'écran ne cache pas (``isHidden`` : l'état du bouton lui-même)."""
    panel = window.project_panel.graphics_view.layers_panel
    return [name for name in ("add_button", "group_button", "presets_button") if not getattr(panel, name).isHidden()]


def folder_dumps(window) -> dict[str, list]:
    """Menus de l'arbre de dossiers ; la bibliothèque (dossier « Rushes ») est posée par ``asset_menu_dump``."""
    tree = window.project_panel.folder_tree
    out = {"ctx_folder_empty": menu_tree(tree.build_context_menu(None))}
    iterator = QTreeWidgetItemIterator(tree.tree)
    while iterator.value() is not None:
        item = iterator.value()
        kind = item.data(0, tree_kind_role())
        if kind == tree._KIND_FOLDER:
            out.setdefault("ctx_folder", menu_tree(tree.build_context_menu(item)))
        else:
            out.setdefault("ctx_folder_synthetic", menu_tree(tree.build_context_menu(item)))
        iterator += 1
    return out


def tree_kind_role() -> int:
    from ui.library_organization_widgets import _FOLDER_KIND_ROLE

    return int(_FOLDER_KIND_ROLE)


def lane_dumps(window) -> dict[str, list]:
    panel = window.timeline_panel
    panel.set_automation_visible("A1", True)
    lane = panel.automation_lanes["A1"]
    lane.set_state([AutomationPoint(1.0, -3.0, 0.0)], locked=False)
    out = {
        "ctx_lane_point": menu_tree(lane.build_menu(QPointF(lane.x_of(1.0), lane.y_of(-3.0)))),
        "ctx_lane_band": menu_tree(lane.build_menu(QPointF(lane.x_of(4.0), lane.y_of(0.0)))),
    }
    panel.set_automation_visible("A1", False)
    panel._automation_choice.pop("A1", None)
    return out


def node_dumps(window) -> dict[str, list]:
    """Menus des éditeurs de nœuds, relevés seulement si la page est affichée au niveau courant."""
    from core.color_grading import ColorGrade
    from core.color_nodes import ColorNode, ColorNodeGraph
    from core.composition import CompositionGraph, SolidNode

    from ui.main_window_mixins.menu_levels import PAGE_LEVELS

    level, out = window.usage_level, {}
    if level.at_least(PAGE_LEVELS[PAGE_COLOR]):
        editor = window.color_panel.nodes
        editor.set_graph(ColorNodeGraph.serial(ColorNode(f"n{i + 1}", ColorGrade()) for i in range(2)), "n1")
        out["ctx_color_node"] = menu_tree(editor.build_menu("n1"))
        out["ctx_color_background"] = menu_tree(editor.build_menu(None))
    if level.at_least(PAGE_LEVELS[PAGE_COMPOSITION]):
        view = window.composition_panel.nodes
        view.set_graph(CompositionGraph.empty().inserted(SolidNode("s")), "s")
        out["ctx_comp_node"] = menu_tree(view.build_menu()[0])
    return out


def all_dumps(window) -> dict[str, list]:
    return {**layers_dumps(window), **folder_dumps(window), **lane_dumps(window), **node_dumps(window)}


__all__ = ["all_dumps", "layers_buttons"]
