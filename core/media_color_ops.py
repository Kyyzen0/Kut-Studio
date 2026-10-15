"""Réglage de l'espace colorimétrique d'un média (``auto`` / ``bt601`` / ``bt709``, :mod:`core.color_matrix`).

Le choix est une propriété du **média** : il change la couleur de tous les clips qui le lisent, dans toutes les séquences
(angles multicam compris). Une seule fonction, que l'inspecteur / la bibliothèque, le verbe de script et la commande de menu
appellent : les mêmes refus pour tous les chemins d'entrée.

Refus (``MediaColorError``) :

- une valeur inconnue (jamais ramenée en silence) ;
- un média qui n'est pas une vidéo (audio, image fixe, sous-titres : aucune matrice YUV à choisir) ;
- un média lu par un clip d'une **piste verrouillée** ou d'un calque verrouillé : le verrou protège ce que l'utilisateur a voulu
  figer, et ce réglage en change l'aspect. Il n'existe pas de verrou de média ; c'est le verrou des clips qui le lisent qui vaut.
"""

from __future__ import annotations

from .color_matrix import normalize_choice
from .project_model import MediaAsset, Project

MAX_REPORTED = 3
"""Clips cités dans le message de refus (le message ne recopie pas toute la liste)."""


class MediaColorError(ValueError):
    """Réglage refusé (valeur inconnue, média qui n'est pas une vidéo, clip verrouillé)."""


def find_asset(project: Project, asset_id: str) -> MediaAsset:
    """Le média d'identifiant ``asset_id`` ; ``KeyError`` s'il n'est pas dans le projet."""
    for asset in project.media_assets:
        if asset.id == asset_id:
            return asset
    raise KeyError(f"Média introuvable : {asset_id!r}.")


def locked_clips(project: Project, asset_id: str) -> list[str]:
    """Identifiants des clips du média qui sont sur une piste verrouillée ou sont un calque verrouillé (toutes séquences)."""
    found: list[str] = []
    for sequence in project.sequences:
        for track in sequence.tracks:
            for clip in track.clips:
                if clip.asset_id != asset_id:
                    continue
                if track.locked or bool(getattr(getattr(clip, "graphic", None), "locked", False)):
                    found.append(clip.id)
    return found


def media_color_refusal(project: Project, asset_id: str) -> str:
    """Le motif du refus (texte, ``""`` si le réglage est permis) pour un média : sert à désactiver l'interface."""
    try:
        asset = find_asset(project, asset_id)
    except KeyError as error:
        return str(error.args[0])
    if asset.media_type != "video":
        return f"Le média « {asset.name} » n'est pas une vidéo : pas d'espace colorimétrique à choisir."
    locked = locked_clips(project, asset_id)
    if locked:
        shown = ", ".join(repr(item) for item in locked[:MAX_REPORTED])
        more = f" (+{len(locked) - MAX_REPORTED})" if len(locked) > MAX_REPORTED else ""
        return f"Le média est lu par des clips verrouillés ({shown}{more}) : déverrouillez-les d'abord."
    return ""


def set_media_color_matrix(project: Project, asset_id: str, value: object) -> bool:
    """Fixe le choix du média ; ``True`` si la valeur a changé. ``MediaColorError`` (refus) ou ``KeyError`` (média inconnu)."""
    try:
        choice = normalize_choice(value)
    except ValueError as error:
        raise MediaColorError(str(error)) from error
    asset = find_asset(project, asset_id)
    refusal = media_color_refusal(project, asset_id)
    if refusal:
        raise MediaColorError(refusal)
    changed = asset.color_matrix != choice
    asset.color_matrix = choice
    return changed


__all__ = ["MediaColorError", "find_asset", "locked_clips", "media_color_refusal", "set_media_color_matrix"]
