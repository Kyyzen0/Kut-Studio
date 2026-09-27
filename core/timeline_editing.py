"""Éditions de timeline qui dépassent un seul clip.

Sélection, déplacement groupé, changement de piste, ripple ciblé et
marqueurs vivent ici. Les opérations élémentaires (trim, coupe,
suppression) restent dans :mod:`core.timeline_operations`.

Le ripple de trim ne concerne que la piste du clip, et seulement
le bord droit : raccourcir la tête d'un clip ne laisse pas de trou
après lui. La suppression ripple de tout le montage reste
``ripple_delete_clip``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from .project_model import Marker, Project
from .timeline_operations import (
    _ensure_track_editable,
    _find_asset,
    _find_track_for_clip,
    _validate_track_asset_compatibility,
    delete_clip,
    find_track,
    ripple_delete_clip,
    snap_timeline_position,
)
from .timeline_view_model import TimelineClipView


def clip_ids_in_range(
    views: list[TimelineClipView],
    anchor_id: str,
    target_id: str,
) -> list[str]:
    """Clips situés entre deux ancres, sur la même piste.

    Si les deux clips ne partagent pas la piste, la sélection se limite
    aux deux identifiants. L'ordre renvoyé suit le temps.
    """
    by_id = {view.id: view for view in views}
    anchor = by_id.get(anchor_id)
    target = by_id.get(target_id)
    if anchor is None or target is None:
        return [clip_id for clip_id in (anchor_id, target_id) if clip_id in by_id]
    if anchor.track_id != target.track_id:
        return [anchor.id, target.id]
    start = min(anchor.start, target.start)
    end = max(anchor.start, target.start)
    chosen = [
        view
        for view in views
        if view.track_id == anchor.track_id and start - 1e-9 <= view.start <= end + 1e-9
    ]
    chosen.sort(key=lambda view: view.start)
    return [view.id for view in chosen]


def snap_edit_position(
    project: Project,
    proposed: float,
    threshold_seconds: float,
    *,
    excluded_clip_ids: set[str] | None = None,
    playhead_seconds: float | None = None,
    include_markers: bool = True,
) -> float:
    """Snap existant, plus les marqueurs et un ensemble de clips exclus."""
    extra = [marker.time_seconds for marker in project.markers] if include_markers else []
    excluded = set(excluded_clip_ids or ())
    # ``snap_timeline_position`` n'exclut qu'un identifiant. On retire
    # les autres en les passant comme temps supplémentaires négatifs ?
    # Non : on appelle le snap clip par clip exclu via le paramètre
    # historique en ignorant d'abord tous les clips exclus nous-mêmes.
    if not excluded and not extra:
        return snap_timeline_position(
            project,
            proposed,
            threshold_seconds,
            playhead_seconds=playhead_seconds,
        )
    return _snap_custom(
        project,
        proposed,
        threshold_seconds,
        excluded,
        playhead_seconds,
        extra,
    )


def _snap_custom(
    project: Project,
    proposed: float,
    threshold_seconds: float,
    excluded: set[str],
    playhead_seconds: float | None,
    extra: list[float],
) -> float:
    if proposed < 0.0:
        proposed = 0.0
    if threshold_seconds <= 0.0:
        return proposed
    candidates: list[float] = [0.0, *extra]
    if playhead_seconds is not None and playhead_seconds >= 0.0:
        candidates.append(float(playhead_seconds))
    for track in project.tracks:
        for clip in track.clips:
            if clip.id in excluded:
                continue
            candidates.append(float(clip.timeline_start))
            candidates.append(float(clip.timeline_start + clip.duration))
            for keyframe in clip.transform_keyframes:
                candidates.append(float(clip.timeline_start + keyframe.time_seconds))
    best = proposed
    best_distance = threshold_seconds
    for candidate in candidates:
        distance = abs(candidate - proposed)
        if distance <= best_distance:
            best = candidate
            best_distance = distance
    return best


@dataclass(frozen=True)
class ClipPlacement:
    """Destination d'un clip après un déplacement."""

    clip_id: str
    timeline_start: float
    track_id: str


def relocate_clip(
    project: Project,
    clip_id: str,
    track_id: str,
    new_timeline_start: float,
):
    """Déplace un clip dans le temps et, si besoin, vers une autre piste.

    La piste de destination doit accepter le type du média et ne pas
    être verrouillée. La durée du clip ne change pas.
    """
    if new_timeline_start < 0.0:
        raise ValueError(
            f"Impossible de déplacer le clip '{clip_id}' avant 0.0 seconde."
        )
    source, index = _find_track_for_clip(project, clip_id)
    _ensure_track_editable(project, source)
    destination = find_track(project, track_id)
    _ensure_track_editable(project, destination)
    clip = source.clips[index]
    if destination.id != source.id:
        asset = _find_asset(project, clip.asset_id)
        _validate_track_asset_compatibility(asset, destination)
        source.clips.pop(index)
        clip.track_id = destination.id
        destination.clips.append(clip)
    clip.timeline_start = float(new_timeline_start)
    return clip


def move_clips(project: Project, placements: list[ClipPlacement]) -> None:
    """Applique plusieurs déplacements. Tout est validé avant la première écriture."""
    checked: list[ClipPlacement] = []
    for placement in placements:
        if placement.timeline_start < 0.0:
            raise ValueError(
                f"Position invalide pour '{placement.clip_id}'."
            )
        source, _index = _find_track_for_clip(project, placement.clip_id)
        _ensure_track_editable(project, source)
        destination = find_track(project, placement.track_id)
        _ensure_track_editable(project, destination)
        if destination.id != source.id:
            clip = next(clip for clip in source.clips if clip.id == placement.clip_id)
            asset = _find_asset(project, clip.asset_id)
            _validate_track_asset_compatibility(asset, destination)
        checked.append(placement)
    for placement in checked:
        relocate_clip(
            project,
            placement.clip_id,
            placement.track_id,
            placement.timeline_start,
        )


def shift_track_after(
    project: Project,
    track_id: str,
    boundary: float,
    delta: float,
    exclude_ids: set[str] | None = None,
) -> list[str]:
    """Décale les clips d'une piste qui commencent à ``boundary`` ou après.

    ``delta`` négatif ferme un trou. Un clip ne passe pas avant 0.
    """
    if abs(delta) < 1e-9:
        return []
    track = find_track(project, track_id)
    excluded = exclude_ids or set()
    moved: list[str] = []
    for clip in track.clips:
        if clip.id in excluded:
            continue
        if clip.timeline_start + 1e-9 >= boundary:
            clip.timeline_start = max(0.0, clip.timeline_start + delta)
            moved.append(clip.id)
    return moved


def delete_clips(
    project: Project,
    clip_ids: list[str],
    *,
    ripple: bool = False,
) -> None:
    """Supprime plusieurs clips. En ripple, on part de la fin vers le début."""
    found = []
    for clip_id in clip_ids:
        try:
            _track, index = _find_track_for_clip(project, clip_id)
        except KeyError:
            continue
        found.append(_track.clips[index])
    found.sort(key=lambda clip: clip.timeline_start, reverse=True)
    for clip in found:
        if ripple:
            try:
                ripple_delete_clip(project, clip.id)
            except (KeyError, ValueError):
                continue
        else:
            try:
                delete_clip(project, clip.id)
            except KeyError:
                continue


def add_marker(
    project: Project,
    time_seconds: float,
    name: str = "",
    category: str = "standard",
) -> Marker:
    """Ajoute un marqueur et garde la liste triée par temps."""
    marker = Marker(
        id=f"marker-{uuid.uuid4().hex[:8]}",
        time_seconds=max(0.0, float(time_seconds)),
        name=name.strip(),
        category=category or "standard",
    )
    project.markers.append(marker)
    project.markers.sort(key=lambda item: (item.time_seconds, item.id))
    return marker


def remove_marker(project: Project, marker_id: str) -> bool:
    """Retire un marqueur. Retourne ``False`` s'il n'existait pas."""
    kept = [marker for marker in project.markers if marker.id != marker_id]
    if len(kept) == len(project.markers):
        return False
    project.markers = kept
    return True


def marker_near(project: Project, time_seconds: float, max_distance: float = 0.35) -> Marker | None:
    """Marqueur le plus proche, s'il est à moins de ``max_distance``."""
    best: Marker | None = None
    best_distance = max_distance
    for marker in project.markers:
        distance = abs(marker.time_seconds - time_seconds)
        if distance <= best_distance:
            best = marker
            best_distance = distance
    return best


def neighbor_marker(project: Project, time_seconds: float, direction: int) -> Marker | None:
    """Marqueur strictement avant (``-1``) ou après (``1``) ``time_seconds``."""
    ordered = sorted(project.markers, key=lambda item: item.time_seconds)
    if direction < 0:
        previous = [marker for marker in ordered if marker.time_seconds < time_seconds - 1e-6]
        return previous[-1] if previous else None
    following = [marker for marker in ordered if marker.time_seconds > time_seconds + 1e-6]
    return following[0] if following else None


def apply_solo(project: Project, active_clips: list) -> list:
    """Retire les clips d'un type dont une piste solo existe et qui n'y sont pas.

    L'audio solo ne masque pas la vidéo, et l'inverse. Les sous-titres
    ne sont filtrés que si une piste sous-titre est en solo.
    """
    solo_ids: dict[str, set[str]] = {}
    for track in project.tracks:
        if track.solo:
            solo_ids.setdefault(track.type, set()).add(track.id)
    if not solo_ids:
        return list(active_clips)
    visible = []
    for clip in active_clips:
        allowed = solo_ids.get(clip.track_type)
        if allowed is not None and clip.track_id not in allowed:
            continue
        visible.append(clip)
    return visible
