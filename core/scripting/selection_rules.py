"""Verrous des tiers touchés par une opération sur plusieurs clips (lot 10 de l'extension 0.8.0).

Supprimer des clips n'agit pas que sur eux : les calques dont ils étaient le parent ou le groupe deviennent autonomes, les liaisons de
suivi qui les prenaient pour source perdent cette source (``core.timeline_operations._release_clip_references``). Ces tiers peuvent se
trouver sur une **autre séquence** que la séquence active. Un verrou (piste verrouillée, calque verrouillé) vaut pour tous les chemins
d'entrée : :func:`frozen_clips` photographie les clips verrouillés de TOUT le projet, :func:`require_untouched_clips` refuse si l'un a
disparu ou changé (la session restaure le projet quand le verbe lève, donc rien ne reste d'une opération refusée).
"""

from __future__ import annotations

from copy import deepcopy

from ..project_model import Clip, Project


def frozen_clips(project: Project) -> dict[str, Clip]:
    """Copie des clips verrouillés de toutes les séquences (leur piste l'est, ou leur calque)."""
    frozen: dict[str, Clip] = {}
    for track in project.all_tracks():
        for clip in track.clips:
            if track.locked or bool(getattr(getattr(clip, "graphic", None), "locked", False)):
                frozen[clip.id] = deepcopy(clip)
    return frozen


def require_untouched_clips(project: Project, frozen: dict[str, Clip]) -> None:
    """Lève si un clip de ``frozen`` a disparu ou changé depuis la photographie."""
    present = {clip.id: clip for track in project.all_tracks() for clip in track.clips}
    for clip_id, before in frozen.items():
        now = present.get(clip_id)
        if now is None or now != before:
            raise ValueError(f"Le clip verrouillé '{clip_id}' serait modifié par cette opération : refusée.")


__all__ = ["frozen_clips", "require_untouched_clips"]
