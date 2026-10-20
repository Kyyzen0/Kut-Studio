"""Verrou des suiveurs d'un suivi : un clip verrouillé n'est jamais réécrit par la coupe ou la suppression de sa source.

Couper la source d'un suivi ajoute la moitié droite aux liaisons de ses suiveurs (:func:`core.tracking_ops.follow_cut`) ; la supprimer retire
la source de leurs liaisons (:func:`core.tracking_ops.release_source`). Ces deux réécritures atteignent des clips qui ne sont pas ceux que
l'utilisateur vise, sur TOUTES les séquences. Un verrou (piste verrouillée, calque graphique verrouillé) vaut pour tous les chemins d'entrée :
le refus est donc dans le **modèle** (``cut_clip``, ``delete_clip``, ``ripple_delete_clip``, ``delete_clips``), et l'interface, les verbes de
script et les menus le partagent. Il passe AVANT la première écriture, et ne vise que les suiveurs que l'opération modifierait réellement
(``TrackLink.after_cut`` / ``TrackLink.without_source`` donnent un résultat différent) : une liaison qui suit une autre source, ou qu'une
suppression laisse telle quelle (aucune prolongation), ne bloque rien. Aucun Qt.
"""

from __future__ import annotations

from collections.abc import Collection

from .project_model import Clip, Project, Track

_UNTAKEN = "\x00nouvelle-moitié"             # identifiant qu'aucun clip ne porte : pour savoir si une liaison serait étendue


def is_locked(track: Track, clip: Clip) -> bool:
    """Le clip est figé : sa piste est verrouillée, ou son calque graphique."""
    return bool(track.locked) or bool(getattr(getattr(clip, "graphic", None), "locked", False))


def _locked_followers(project: Project, source_ids: Collection[str], rewrites, spared: Collection[str]) -> list[str]:
    found: list[str] = []
    for track in project.all_tracks():
        for clip in track.clips:
            tracking = getattr(clip, "tracking", None)
            if tracking is None or clip.id in spared or clip.id in found or not is_locked(track, clip):
                continue
            if any(rewrites(link, source_id) for link in tracking.links for source_id in source_ids):
                found.append(clip.id)
    return found


def cut_locked_followers(project: Project, source_id: str, right_id: str | None = None) -> list[str]:
    """Clips verrouillés (toutes séquences) dont la liaison serait étendue par la coupe de ``source_id`` en ``source_id`` + ``right_id``."""
    new = right_id or _UNTAKEN
    return _locked_followers(project, [source_id], lambda link, sid: link.after_cut(sid, new) != link, (source_id,))


def release_locked_followers(project: Project, doomed: Collection[str]) -> list[str]:
    """Clips verrouillés (hors ``doomed``) dont la liaison serait réécrite par la suppression des clips ``doomed``."""
    gone = list(dict.fromkeys(doomed))
    return _locked_followers(project, gone, lambda link, sid: link.without_source(sid) != link, set(gone))


def require_cut_followers_unlocked(project: Project, source_id: str, right_id: str | None = None) -> None:
    """Refuse, AVANT d'écrire, de couper ``source_id`` si un clip verrouillé suit cette source et serait modifié."""
    locked = cut_locked_followers(project, source_id, right_id)
    if locked:
        raise ValueError(
            f"Impossible de couper le clip '{source_id}' : le clip verrouillé '{locked[0]}' le suit (suivi) et serait modifié. "
            f"Déverrouillez-le d'abord."
        )


def require_release_followers_unlocked(project: Project, doomed: Collection[str]) -> None:
    """Refuse, AVANT d'écrire, de supprimer ``doomed`` si la liaison d'un clip verrouillé qui les suit serait réécrite."""
    locked = release_locked_followers(project, doomed)
    if locked:
        names = ", ".join(f"'{clip_id}'" for clip_id in list(dict.fromkeys(doomed))[:3])
        raise ValueError(
            f"Impossible de supprimer le clip {names} : le clip verrouillé '{locked[0]}' le suit (suivi) et verrait sa liaison réécrite. "
            f"Déverrouillez-le d'abord."
        )


__all__ = [
    "cut_locked_followers", "is_locked", "release_locked_followers", "require_cut_followers_unlocked", "require_release_followers_unlocked",
]
