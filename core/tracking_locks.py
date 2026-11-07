"""Verrou des suiveurs d'un suivi : un clip verrouillé n'est jamais réécrit par la coupe ou la suppression de sa source.

Couper la source d'un suivi ajoute la moitié droite aux liaisons de ses suiveurs (:func:`core.tracking_ops.follow_cut`) ; la supprimer retire
la source de leurs liaisons (:func:`core.tracking_ops.release_source`). Ces deux réécritures atteignent des clips qui ne sont pas ceux que
l'utilisateur vise, sur TOUTES les séquences. Un verrou (piste verrouillée, calque graphique verrouillé) vaut pour tous les chemins d'entrée :
le refus est donc dans le **modèle** (``cut_clip``, ``delete_clip``, ``ripple_delete_clip``, ``delete_clips``), et l'interface, les verbes de
script et les menus le partagent. Il passe AVANT la première écriture, et ne vise que les suiveurs que l'opération modifierait réellement
(``TrackLink.after_cut`` / ``TrackLink.without_source`` donnent un résultat différent) : une liaison qui suit une autre source, ou qu'une
suppression laisse telle quelle (aucune prolongation), ne bloque rien. Aucun Qt.

Supprimer un tracker (:func:`core.tracking_ops.remove_tracker` / ``remove_trackers``) réécrit de même les liaisons qui l'utilisent (séquence
du clip porteur, comme l'écriture : une liaison ne vit que dans une séquence, voir docs/tracking.md) : même refus, calculé par la règle d'écriture elle-même (``tracker_removal_rewrites``).

Déplacer, rogner, ripple-trimmer, changer la vitesse ou dupliquer la source ne réécrit aucun suiveur (seul le rendu, qui dérive les images clés du
suiveur de sa source, change) : aucune garde ici pour ces opérations, ni dans l'interface, ni dans les verbes (tests/test_follower_move_parity.py).
"""

from __future__ import annotations

from collections.abc import Collection

from .project_model import Clip, Project, Track

_UNTAKEN = "\x00nouvelle-moitié"             # identifiant qu'aucun clip ne porte : pour savoir si une liaison serait étendue


def is_locked(track: Track, clip: Clip) -> bool:
    """Le clip est figé : sa piste est verrouillée, ou son calque graphique."""
    return bool(track.locked) or bool(getattr(getattr(clip, "graphic", None), "locked", False))


def clip_lock_refusal(track: Track, clip: Clip) -> str | None:
    """Message (français, nomme le clip) si le suivi de ``clip`` ne peut pas être modifié parce que sa piste ou son calque est verrouillé."""
    if track.locked:
        return f"Le clip '{clip.id}' est sur la piste '{track.id}', verrouillée : son suivi ne peut pas être modifié. Déverrouillez-la d'abord."
    if is_locked(track, clip):
        return f"Le calque '{clip.id}' est verrouillé : son suivi ne peut pas être modifié. Déverrouillez-le d'abord."
    return None


def require_clip_editable(project: Project, clip_id: str) -> None:
    """Refuse (``ValueError``), AVANT d'écrire, de modifier le suivi d'un clip verrouillé (piste ou calque graphique).

    Le clip PORTEUR est celui qu'une écriture remplace : le propriétaire d'un tracker (ajout, déplacement, effacement, réglage, analyse,
    stabilisation) ou la cible d'une liaison (pose, retrait, activation, conversion). La SOURCE d'une liaison n'est pas écrite : elle n'est
    pas vérifiée. Règle unique du modèle : l'interface, les verbes de script et les menus l'appellent via ``core.tracking_ops``.
    """
    from .tracking_ops import find_clip_and_track

    clip, track = find_clip_and_track(project, clip_id)
    message = clip_lock_refusal(track, clip)
    if message is not None:
        raise ValueError(message)


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


def tracker_removal_locked_followers(project: Project, clip_id: str, tracker_ids: Collection[str]) -> list[str]:
    """Clips verrouillés dont la liaison serait réécrite par la suppression des trackers ``tracker_ids`` de ``clip_id`` (même règle que l'écriture)."""
    from .tracking_ops import _sequence_of_clip, tracker_removal_rewrites

    tracks = _sequence_of_clip(project, clip_id).tracks
    found: list[str] = []
    for tracker_id in dict.fromkeys(tracker_ids):
        for other in tracker_removal_rewrites(project, clip_id, tracker_id):
            track = next((t for t in tracks if other in t.clips), None)
            if track is not None and is_locked(track, other) and other.id not in found:
                found.append(other.id)
    return found


def require_tracker_removal_unlocked(project: Project, clip_id: str, tracker_ids: Collection[str]) -> None:
    """Refuse, AVANT d'écrire, de supprimer ces trackers si la liaison d'un clip verrouillé qui les suit serait réécrite."""
    locked = tracker_removal_locked_followers(project, clip_id, tracker_ids)
    if locked:
        names = ", ".join(f"'{tracker_id}'" for tracker_id in list(dict.fromkeys(tracker_ids))[:3])
        raise ValueError(
            f"Impossible de supprimer le tracker {names} du clip '{clip_id}' : le clip verrouillé '{locked[0]}' le suit (suivi) et "
            f"verrait sa liaison réécrite. Déverrouillez-le d'abord."
        )


__all__ = [
    "clip_lock_refusal", "require_clip_editable", "cut_locked_followers", "is_locked", "release_locked_followers", "require_cut_followers_unlocked", "require_release_followers_unlocked",
    "require_tracker_removal_unlocked", "tracker_removal_locked_followers",
]
