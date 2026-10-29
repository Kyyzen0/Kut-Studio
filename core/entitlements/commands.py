"""Quelles commandes clavier / de menu restent permises en lecture seule (liste blanche : tout le reste est refusé).

La liste est une **liste blanche** : une commande nouvelle, non classée ici, est refusée en lecture seule jusqu'à ce qu'on décide
qu'elle ne modifie rien (``tests/test_entitlements_ui.py`` échoue si une commande de ``core.shortcuts.COMMANDS`` est à la fois
dans les deux listes, ou si une entrée de la liste n'existe plus). Ce sont des commandes de **lecture, de navigation, de
réglage de l'affichage** et d'ouverture de projet ; une commande qui change le projet (couper, supprimer, calques, réseaux
sociaux, multicam, temps, image clé, annuler / rétablir…) n'y est pas.

Les outils (``tool_*``) et les modes (``toggle_snap``, ``toggle_ripple``) ne changent qu'un état d'interface ; ce que l'outil
*fait* au clic passe par l'historique du projet, qui refuse (``core.edit_history``).
"""

from __future__ import annotations

READ_ONLY_COMMANDS: frozenset[str] = frozenset({
    # Lecture et navigation.
    "play_pause", "shuttle_back", "shuttle_forward", "frame_back", "frame_forward", "second_back", "second_forward",
    "marker_previous", "marker_next", "keyframe_previous", "keyframe_next",
    # Affichage.
    "zoom_in", "zoom_out", "zoom_fit", "toggle_scopes", "view_safe_areas", "view_guides", "view_grid", "mograph_snapping",
    "page_edit", "page_color", "page_composition", "page_audio", "graph_editor", "multicam_viewer",
    # État d'interface (ce que l'outil fait ensuite passe par l'historique, qui refuse).
    "toggle_snap", "toggle_ripple", "tool_select", "tool_blade", "tool_roll", "tool_slip", "tool_slide", "select_all",
    "keyframe_select_all", "keyframe_copy", "layer_copy_attributes",
    # Séquences : se déplacer, pas les modifier.
    "sequence_open_nested", "sequence_parent", "sequence_back", "sequence_forward", "multicam_open_source",
    # Projet : ouvrir est toujours permis (D3) ; enregistrer ne change pas le contenu ; quitter.
    "project_new", "project_open", "project_save", "project_save_as", "quit", "preferences", "command_palette",
})

# Entrées de menu qui n'ont pas de commande clavier (identifiées par la clé de leur libellé, jamais par leur texte).
READ_ONLY_MENU_KEYS: frozenset[str] = frozenset({
    # Disposition, niveau d'usage, affichage et aide : ils ne touchent pas au projet.
    "menu.item.reset_layout", "menu.item.restore_layout", "menu.item.save_layout_as", "menu.item.use_proxies",
    "menu.item.show_center", "debug.toggle", "update.menu.check", "update.menu.about",
    "usage.menu.simple", "usage.menu.studio", "usage.menu.advanced",
    "workspace.panel.timeline", "workspace.panel.viewer", "workspace.panel.media", "workspace.panel.inspector",
    "workspace.panel.mixer", "workspace.panel.history", "workspace.panel.color", "workspace.panel.clips",
    "workspace.panel.composition",
    "social.platform.none", "social.platform.tiktok", "social.platform.reels", "social.platform.shorts",
    "social.platform.instagram_feed",
})


def is_read_only_command(command_id: str) -> bool:
    """La commande est-elle permise en lecture seule ?"""
    return command_id in READ_ONLY_COMMANDS


__all__ = ["READ_ONLY_COMMANDS", "READ_ONLY_MENU_KEYS", "is_read_only_command"]
