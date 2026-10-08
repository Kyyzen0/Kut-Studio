"""Règle des favoris d'une bibliothèque de presets, partagée.

Les bibliothèques d'effets audio et de transitions gardent chacune un ensemble d'identifiants favoris. La règle est la
même : un favori ne peut viser qu'un preset connu, et ajouter ou retirer un favori déjà dans cet état ne change rien.
Chaque bibliothèque reste responsable de sa persistance et de sa notification.
"""

from __future__ import annotations

from typing import Callable


def set_favorite_member(
    favorites: set[str],
    preset_id: str,
    favorite: bool,
    *,
    exists: Callable[[str], bool],
) -> bool:
    """Ajoute ou retire ``preset_id`` de ``favorites``. Retourne ``True`` si l'ensemble a changé.

    Lève ``KeyError`` si on ajoute un favori pour un preset que ``exists`` ne connaît pas.
    """
    if favorite:
        if not exists(preset_id):
            raise KeyError(f"Preset '{preset_id}' introuvable.")
        if preset_id in favorites:
            return False
        favorites.add(preset_id)
        return True
    if preset_id not in favorites:
        return False
    favorites.remove(preset_id)
    return True
