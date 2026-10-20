"""Verrous des suiveurs et des dépendants des verbes historiques de montage (coupe, suppression, déplacement).

Un verrou (piste verrouillée, calque graphique verrouillé) vaut pour tous les chemins d'entrée, et pas seulement pour le clip visé : couper
ou supprimer la **source** d'un suivi réécrit les liaisons (``Clip.tracking``) des clips qui la suivent
(:func:`core.tracking_ops.follow_cut` ajoute la moitié droite à leurs prolongements, :func:`core.tracking_ops.release_source` retire la
source supprimée), et déplacer cette source change l'endroit où leur mouvement s'applique. Le modèle partagé avec l'interface n'a aucun
contrôle de ce genre (l'interface n'en a pas non plus : proposé, pas changé) ; un script refuse **avant** d'écrire.

Les suiveurs se cherchent dans TOUTES les séquences (``follow_cut`` et ``release_source`` parcourent ``Project.all_tracks``), liaisons
actives ou non pour une coupe ou une suppression (elles sont réécrites dans les deux cas). Aucun verbe ici, aucun Qt.
"""

from __future__ import annotations

from ..project_model import Clip, Project, Track


def is_locked(track: Track, clip: Clip) -> bool:
    """Le clip est figé : sa piste est verrouillée, ou son calque graphique."""
    return bool(track.locked) or bool(getattr(getattr(clip, "graphic", None), "locked", False))


def locked_followers(project: Project, source_id: str, *, active_only: bool = False) -> list[str]:
    """Identifiants des clips verrouillés (toutes séquences) dont une liaison de suivi suit ``source_id``, source ou prolongement.

    ``active_only`` : seules les liaisons actives comptent (un déplacement ne change que l'apparence des liaisons actives ; une coupe ou
    une suppression réécrit aussi les inactives)."""
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
    """Refuse, AVANT d'écrire, si un clip verrouillé suit ``source_id`` : ``action`` (« couper », « déplacer »…) le modifierait."""
    locked = locked_followers(project, source_id, active_only=active_only)
    if locked:
        raise ValueError(
            f"Impossible de {action} le clip '{source_id}' : le clip verrouillé '{locked[0]}' le suit (suivi) et serait modifié. "
            f"Déverrouillez-le d'abord."
        )


__all__ = ["is_locked", "locked_followers", "require_followers_unlocked"]
