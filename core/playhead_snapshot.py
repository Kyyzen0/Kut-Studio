"""Le projet « tel qu'il est à la tête de lecture », ramené à l'origine du temps.

Extraire l'image d'un instant ``T`` d'un graphe FFmpeg coûte O(T) : tout ce qui précède ``T`` traverse le
graphe avant d'être jeté (mesuré : 66 s à 1080p pour ``T`` = 10 minutes). Les scopes ont besoin d'une
image en quelques centaines de millisecondes, où que soit la tête de lecture.

:func:`project_at_playhead` renvoie donc une **copie** de la séquence active : les clips finis avant
``T`` ou commencés bien après disparaissent, ceux qui chevauchent ``T`` sont rognés à gauche (le
``trim_clip_left`` de l'édition : vitesse, sens de lecture, arrêt sur image et keyframes sont déjà
traités), puis tout est décalé de ``-T``. L'image voulue est la première du rendu, à l'instant 0.
Le projet d'origine n'est jamais modifié.
"""

from __future__ import annotations

import copy

from .project_model import Project
from .timeline_operations import trim_clip_left
from .transitions import remove_transitions_for_clips

_EPSILON = 1e-6
DEFAULT_SPAN_SECONDS = 2.0
"""Clips commençant juste après ``T`` : gardés, ils peuvent entrer dans une transition en cours."""


def project_at_playhead(project: Project, playhead: float, *, span: float = DEFAULT_SPAN_SECONDS) -> Project:
    """Copie de ``project`` dont l'instant ``playhead`` de la séquence active devient l'instant 0.

    Args:
        project: Projet source (non modifié).
        playhead: Instant à ramener à l'origine (secondes de timeline, ``>= 0``).
        span: Durée conservée après ``playhead`` (secondes) : les clips qui commencent plus tard sont retirés.
    """
    origin = max(0.0, float(playhead))
    snapshot = copy.deepcopy(project)
    sequence = snapshot.active_sequence
    for track in sequence.tracks:
        track.locked = False          # le rognage refuse les pistes verrouillées ; la copie n'est jamais éditée
    removed: set[str] = set()
    for track in sequence.tracks:
        for clip in list(track.clips):
            start = clip.timeline_start
            end = start + clip.duration
            if end <= origin + _EPSILON or start >= origin + span:
                track.clips.remove(clip)
                removed.add(clip.id)
                continue
            if start < origin:
                try:
                    trim_clip_left(snapshot, clip.id, origin)
                except (ValueError, KeyError):
                    track.clips.remove(clip)          # trop court pour être rogné : il ne montre plus rien
                    removed.add(clip.id)
                    continue
            clip.timeline_start = max(0.0, clip.timeline_start - origin)
    remove_transitions_for_clips(snapshot, removed)
    return snapshot
