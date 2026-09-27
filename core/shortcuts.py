"""Raccourcis clavier de l'éditeur.

Une seule table décide de l'action. L'interface traduit l'événement
Qt en nom de touche et en modificateurs (``ctrl``, ``shift``, ``alt``),
puis appelle :func:`resolve_shortcut`. Les champs de texte ne doivent
pas passer par ici.

Les raccourcis déjà portés par des ``QAction`` (annuler, enregistrer,
supprimer) ne sont pas répétés : les déclencher deux fois appliquerait
l'action en double.
"""

from __future__ import annotations


def resolve_shortcut(key: str, modifiers: set[str] | frozenset[str] | None = None) -> str | None:
    """Retourne l'identifiant d'action, ou ``None`` si la touche est libre."""
    mods = frozenset(modifiers or ())
    normalized = (key or "").lower()
    table = {
        ("space", frozenset()): "play_pause",
        ("k", frozenset()): "play_pause",
        ("left", frozenset()): "frame_back",
        ("right", frozenset()): "frame_forward",
        ("left", frozenset({"shift"})): "second_back",
        ("right", frozenset({"shift"})): "second_forward",
        ("j", frozenset()): "shuttle_back",
        ("l", frozenset()): "shuttle_forward",
        ("equal", frozenset()): "zoom_in",
        ("plus", frozenset()): "zoom_in",
        ("minus", frozenset()): "zoom_out",
        ("0", frozenset({"ctrl"})): "zoom_fit",
        ("z", frozenset({"shift"})): "zoom_fit",
        ("b", frozenset()): "tool_blade",
        ("v", frozenset()): "tool_select",
        ("s", frozenset()): "toggle_snap",
        ("n", frozenset()): "toggle_ripple",
        ("m", frozenset()): "marker_add",
        ("bracketleft", frozenset()): "marker_previous",
        ("bracketright", frozenset()): "marker_next",
        ("k", frozenset({"ctrl"})): "cut_at_playhead",
        ("a", frozenset({"ctrl"})): "select_all",
    }
    return table.get((normalized, mods))
