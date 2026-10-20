"""Verrous des suiveurs d'un suivi pour le **déplacement** de leur source (verbes historiques de montage).

Un verrou (piste verrouillée, calque graphique verrouillé) vaut pour tous les chemins d'entrée, et pas seulement pour le clip visé : couper
ou supprimer la **source** d'un suivi réécrit les liaisons (``Clip.tracking``) des clips qui la suivent
(:func:`core.tracking_ops.follow_cut`, :func:`core.tracking_ops.release_source`). Ces deux-là sont refusés par le MODÈLE, donc pour l'interface,
les scripts et les menus à la fois : voir :mod:`core.tracking_locks`, qui en est la seule source de vérité. Il ne reste ici que le
déplacement de la source, qui change l'endroit où le mouvement des suiveurs s'applique (le modèle et l'interface ne le contrôlent pas :
proposé, pas changé) ; un script refuse **avant** d'écrire.

Les suiveurs se cherchent dans TOUTES les séquences (``Project.all_tracks``). Aucun verbe ici, aucun Qt.
"""

from __future__ import annotations

from ..project_model import Project
from ..tracking_locks import is_locked


def locked_followers(project: Project, source_id: str, *, active_only: bool = False) -> list[str]:
    """Identifiants des clips verrouillés (toutes séquences) dont une liaison de suivi suit ``source_id``, source ou prolongement.

    ``active_only`` : seules les liaisons actives comptent (un déplacement ne change que l'apparence des liaisons actives)."""
    found: list[str] = []
    for track in project.all_tracks():
        for clip in track.clips:
            tracking = getattr(clip, "tracking", None)
            if clip.id == source_id or tracking is None or not is_locked(track, clip):
                continue
            if any(link.follows(source_id) and (link.enabled or not active_only) for link in tracking.links):
                found.append(clip.id)
    return found


def require_followers_unlocked(project: Project, source_id: str, action: str, *, active_only: bool = False) -> None:
    """Refuse, AVANT d'écrire, si un clip verrouillé suit ``source_id`` : ``action`` (« déplacer »…) le modifierait."""
    locked = locked_followers(project, source_id, active_only=active_only)
    if locked:
        raise ValueError(
            f"Impossible de {action} le clip '{source_id}' : le clip verrouillé '{locked[0]}' le suit (suivi) et serait modifié. "
            f"Déverrouillez-le d'abord."
        )


__all__ = ["is_locked", "locked_followers", "require_followers_unlocked"]
