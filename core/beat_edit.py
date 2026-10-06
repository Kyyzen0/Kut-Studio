"""Montage sur la grille rythmique : couper sur les temps, répartir des clips sur la grille.

Fonctions pures sur le projet (l'interface enregistre une entrée d'historique par appel). Elles réutilisent les
opérations de la timeline : une coupe est un :func:`core.timeline_operations.cut_clip`, un rognage un
:func:`core.timeline_operations.trim_clip_right`, un déplacement un :func:`core.timeline_editing.move_clips` (tout est
validé avant la première écriture).
"""

from __future__ import annotations

from collections.abc import Iterable

from .beat_grid import BeatGrid
from .project_model import Project
from .timeline_editing import ClipPlacement, move_clips
from .timeline_operations import _find_track_for_clip, cut_clip, find_clip, trim_clip_right

MIN_PIECE = 1e-3
"""Une coupe ne laisse jamais un morceau plus court qu'une milliseconde."""


def cut_on_beats(project: Project, clip_id: str, grid: BeatGrid, *, every: int = 1) -> list[str]:
    """Coupe le clip sur chaque temps (un sur ``every``) strictement à l'intérieur ; retourne les identifiants des
    morceaux, de gauche à droite (le premier garde l'identifiant d'origine)."""
    clip = find_clip(project, clip_id)
    cuts = cut_times_on_beats(grid, clip.timeline_start, clip.timeline_start + clip.duration, every=every)
    if not cuts:
        raise ValueError("Aucun temps de la grille ne tombe à l'intérieur de ce clip.")
    pieces = [clip_id]
    current = clip_id
    for t in cuts:
        _left, right = cut_clip(project, current, t)
        current = right.id
        pieces.append(current)
    return pieces


def cut_clips_on_beats(project: Project, clip_ids: Iterable[str], grid: BeatGrid, *, every: int = 1) -> list[str]:
    """Coupe plusieurs clips sur les temps : tout est vérifié (clips, pistes modifiables) avant la première coupe ; les
    clips qu'aucun temps ne traverse sont laissés tels quels. Retourne tous les morceaux produits."""
    targets = []
    for clip_id in dict.fromkeys(clip_ids):
        clip = find_clip(project, clip_id)
        track, _index = _find_track_for_clip(project, clip_id)
        if cut_times_on_beats(grid, clip.timeline_start, clip.timeline_start + clip.duration, every=every):
            if track.locked:
                raise ValueError(f"La piste « {track.name} » est verrouillée.")
            targets.append(clip_id)
    if not targets:
        raise ValueError("Aucun temps de la grille ne tombe à l'intérieur des clips sélectionnés.")
    pieces: list[str] = []
    for clip_id in targets:
        pieces.extend(cut_on_beats(project, clip_id, grid, every=every))
    return pieces


def distribute_on_grid(
    project: Project, clip_ids: Iterable[str], grid: BeatGrid, *, beats_each: int = 2, start: float | None = None,
) -> list[ClipPlacement]:
    """Pose les clips bout à bout sur leurs pistes, chacun sur ``beats_each`` temps, à partir du temps le plus proche
    du premier clip (ou de ``start``). Un clip trop long est raccourci ; un clip plus court garde sa durée et le
    suivant commence au temps qui suit sa fin. Retourne les placements appliqués."""
    beats_each = max(1, int(beats_each))
    clips = sorted((find_clip(project, clip_id) for clip_id in dict.fromkeys(clip_ids)),
                   key=lambda clip: (clip.timeline_start, clip.id))
    if not clips:
        raise ValueError("Aucun clip à répartir.")
    cursor = grid.nearest_beat(clips[0].timeline_start if start is None else start)
    if cursor < 0.0:                                    # le temps le plus proche tombait avant 0 : le suivant
        cursor = grid.beat_times(0.0, grid.beat)[0]
    plan: list[tuple[str, float, str, float]] = []      # (clip, début, piste, fin voulue)
    for clip in clips:
        track, _index = _find_track_for_clip(project, clip.id)
        target_end = round(cursor + beats_each * grid.beat, 6)
        end = min(target_end, round(cursor + clip.duration, 6))
        plan.append((clip.id, cursor, track.id, end))
        cursor = target_end if end >= target_end - 1e-6 else grid.beat_times(end, end + grid.beat)[0]
    selected = {clip_id for clip_id, *_rest in plan}
    for clip_id, begin, _track_id, end in plan:
        track, _index = _find_track_for_clip(project, clip_id)
        for other in track.clips:
            if other.id in selected or not other.enabled:
                continue
            if begin < other.timeline_start + other.duration - 1e-6 and other.timeline_start < end - 1e-6:
                raise ValueError(f"La grille poserait « {clip_id} » sur le clip « {other.label or other.id} ».")
    # Rognages d'abord (un clip raccourci libère sa place), puis les déplacements validés ensemble.
    for clip_id, _start, _track, end in plan:
        clip = find_clip(project, clip_id)
        if clip.duration > (end - _start) + 1e-6:
            trim_clip_right(project, clip_id, clip.timeline_start + (end - _start))
    placements = [ClipPlacement(clip_id, start_time, track_id) for clip_id, start_time, track_id, _end in plan]
    move_clips(project, placements)
    return placements


def cut_times_on_beats(grid: BeatGrid, start: float, end: float, *, every: int = 1) -> list[float]:
    """Temps de coupe (sans couper) : ce que « Couper sur les temps » ferait sur ``[start, end]``."""
    return [t for t in grid.beat_times(start, end, every=every) if start + MIN_PIECE < t < end - MIN_PIECE]


__all__ = ["cut_clips_on_beats", "cut_on_beats", "cut_times_on_beats", "distribute_on_grid"]
