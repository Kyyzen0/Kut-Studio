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
    _validate_track_clip_compatibility,
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


def shifted_track_index(tracks, origin: int, delta: int) -> int:
    """Décale une piste de ``delta`` rangées, si la destination est compatible.

    Le groupe se déplace du même nombre de rangées. Une piste verrouillée,
    d'un autre type, ou hors du montage laisse le clip sur sa piste d'origine.
    """
    if delta == 0 or not tracks:
        return origin
    dest = origin + delta
    if not (0 <= origin < len(tracks)) or not (0 <= dest < len(tracks)):
        return origin
    if tracks[dest].locked or tracks[dest].type != tracks[origin].type:
        return origin
    return dest


def ripple_trim_left(project: Project, clip_id: str, new_timeline_start: float):
    """Rogne la tête du clip et referme le trou en aval.

    ``trim_clip_left`` avance le début et laisse la fin en place, ce qui
    ouvre un trou avant le clip. En ripple, le début revient à sa place
    (le contenu a changé) et tout ce qui commençait à l'ancienne fin
    recule de la durée retirée.
    """
    from .timeline_operations import find_clip, trim_clip_left

    clip = find_clip(project, clip_id)
    old_start = float(clip.timeline_start)
    old_end = old_start + float(clip.duration)
    track_id = clip.track_id
    trim_clip_left(project, clip_id, new_timeline_start)
    removed = float(new_timeline_start) - old_start
    if removed <= 1e-9:
        return clip
    clip.timeline_start = old_start
    shift_track_after(
        project,
        track_id,
        old_end,
        -removed,
        exclude_ids={clip_id},
    )
    return clip


def _neighbors(project: Project, clip_id: str):
    track, _index = _find_track_for_clip(project, clip_id)
    ordered = sorted(track.clips, key=lambda item: (item.timeline_start, item.id))
    position = next(index for index, item in enumerate(ordered) if item.id == clip_id)
    previous = ordered[position - 1] if position > 0 else None
    following = ordered[position + 1] if position + 1 < len(ordered) else None
    return track, previous, ordered[position], following


def _asset_duration(project: Project, clip) -> float:
    """Durée de la source du clip (média, séquence imbriquée ou composition)."""
    if getattr(clip, "sequence_id", ""):
        from .sequences import clip_source_limit

        limit = clip_source_limit(project, clip)
        return float(limit) if limit is not None else float(clip.source_out)
    if getattr(clip, "composition", None) is not None:      # pas de média : la durée de la composition
        return float(clip.composition.duration)
    return float(_find_asset(project, clip.asset_id).duration)


def slip_clip(project: Project, clip_id: str, delta_seconds: float):
    """Décale la fenêtre source sans bouger le clip sur la timeline.

    Le delta est borné par le début du média et sa durée. La durée du
    clip sur la timeline ne change pas.
    """
    _track, _previous, clip, _following = _neighbors(project, clip_id)
    _ensure_track_editable(project, _track)
    if abs(delta_seconds) < 1e-9:
        return clip
    limit = _asset_duration(project, clip)
    if delta_seconds > 0:
        delta_seconds = min(delta_seconds, limit - clip.source_out)
    else:
        delta_seconds = max(delta_seconds, -clip.source_in)
    clip.source_in += delta_seconds
    clip.source_out += delta_seconds
    if clip.time_remapping.anchor is not None:
        # L'ancre est un temps source : elle suit la fenêtre (le mapping est translaté, sa durée ne change pas).
        from dataclasses import replace

        clip.time_remapping = replace(clip.time_remapping, anchor=clip.time_remapping.anchor + delta_seconds)
    return clip


_MIN_EDGE_SECONDS = 0.05
"""Durée minimale (secondes de timeline) d'un clip voisin après un slide ou un roll."""


def _refuse_curve(clip, operation: str) -> None:
    from .time_editing import needs_general_path

    if needs_general_path(clip):
        raise ValueError(
            f"{operation} : indisponible sur un clip dont la vitesse est animée (rognez-le : ses keyframes de vitesse "
            "suivent le trim)."
        )


def _shifted_edge(project: Project, clip, edge: str, delta: float):
    """Fenêtre source (et durée d'arrêt) d'un clip dont un bord se déplace de ``delta`` secondes de **timeline**.

    ``edge`` : ``"in"`` (début sur la timeline) ou ``"out"`` (fin) ; ``delta`` est le déplacement du bord (positif = plus
    tard). Un décalage de timeline se convertit en source par la vitesse, et le bord de la timeline qui montre
    ``source_in`` n'est pas le même en lecture inverse : c'est la seule conversion, partagée par ``slide`` et ``roll``
    (l'ancien code ajoutait ``delta`` tel quel aux secondes de source).

    Returns:
        ``(source_in, source_out, freeze_duration | None)`` ; rien n'est modifié.

    Raises:
        ValueError: la source sortirait du média, ou le clip deviendrait (presque) vide.
    """
    if clip.is_frozen:
        duration = clip.duration + (delta if edge == "out" else -delta)
        if duration <= _MIN_EDGE_SECONDS:
            raise ValueError("Le clip voisin serait vide.")
        return clip.source_in, clip.source_out, duration
    span = delta * clip.speed
    low, high = clip.source_in, clip.source_out
    if clip.is_reversed:
        if edge == "out":
            low -= span
        else:
            high -= span
    elif edge == "out":
        high += span
    else:
        low += span
    if low < -1e-6 or high > _asset_duration(project, clip) + 1e-6:
        raise ValueError("Le déplacement sort de la source du clip voisin.")
    if (high - low) / clip.speed <= _MIN_EDGE_SECONDS:
        raise ValueError("Le clip voisin serait vide.")
    return max(0.0, low), high, None


def _apply_shifted_edge(clip, shifted) -> None:
    from .timeline_operations import _set_freeze_duration

    clip.source_in, clip.source_out, freeze = shifted
    if freeze is not None:
        _set_freeze_duration(clip, freeze)


def slide_clip(project: Project, clip_id: str, new_timeline_start: float):
    """Glisse un clip et ajuste les voisins pour garder le montage joint.

    Sans voisin, le clip est simplement déplacé. Avec un voisin, sa
    durée source ne change pas : c'est le clip d'avant qui change de
    fin, et celui d'après qui change de début.
    """
    track, previous, clip, following = _neighbors(project, clip_id)
    _ensure_track_editable(project, track)
    for neighbour in (previous, following):
        if neighbour is not None:
            _refuse_curve(neighbour, "Slide")
    if new_timeline_start < 0.0:
        raise ValueError("Un slide ne peut pas commencer avant 0.")
    delta = float(new_timeline_start) - float(clip.timeline_start)
    if abs(delta) < 1e-9:
        return clip
    # Tout est validé avant le moindre changement (le slide est atomique).
    previous_edge = following_edge = None
    if previous is not None:
        previous_edge = _shifted_edge(project, previous, "out", delta)
    if following is not None:
        if following.timeline_start + delta < 0.0:
            raise ValueError("Le slide pousserait le clip suivant avant 0.")
        following_edge = _shifted_edge(project, following, "in", delta)
    if previous is not None and previous_edge is not None:
        _apply_shifted_edge(previous, previous_edge)
    clip.timeline_start = float(new_timeline_start)
    if following is not None and following_edge is not None:
        _apply_shifted_edge(following, following_edge)
        following.timeline_start += delta
    return clip


def roll_edit(project: Project, clip_id: str, edge: str, new_time: float):
    """Déplace la coupe entre ce clip et son voisin, sans changer la durée totale.

    ``edge`` vaut ``"right"`` (coupe sortante) ou ``"left"`` (coupe
    entrante). S'il n'y a pas de voisin, l'opération retombe sur un trim.
    """
    from .timeline_operations import trim_clip_left, trim_clip_right

    _track, previous, clip, following = _neighbors(project, clip_id)
    _ensure_track_editable(project, _track)
    if edge == "right":
        if following is None:
            return trim_clip_right(project, clip_id, new_time)
        delta = float(new_time) - (clip.timeline_start + clip.duration)
        _roll_pair(project, clip, following, delta)
        return clip
    if previous is None:
        return trim_clip_left(project, clip_id, new_time)
    delta = float(new_time) - clip.timeline_start
    _roll_pair(project, previous, clip, delta)
    return clip


def _roll_pair(project: Project, left, right, delta: float) -> None:
    if abs(delta) < 1e-9:
        return
    _refuse_curve(left, "Roll")
    _refuse_curve(right, "Roll")
    if right.timeline_start + delta < 0.0:
        raise ValueError("Le roll passerait avant le début de la timeline.")
    left_edge = _shifted_edge(project, left, "out", delta)
    right_edge = _shifted_edge(project, right, "in", delta)
    _apply_shifted_edge(left, left_edge)
    _apply_shifted_edge(right, right_edge)
    right.timeline_start += delta


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
        _validate_track_clip_compatibility(project, clip, destination)
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
            _validate_track_clip_compatibility(project, clip, destination)
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


def set_cover_marker(project: Project, time_seconds: float, name: str = "") -> Marker:
    """Pose **le** marqueur de couverture (catégorie ``cover``) : il n'y en a qu'un, l'ancien est remplacé."""
    project.markers = [marker for marker in project.markers if marker.category != "cover"]
    return add_marker(project, time_seconds, name, category="cover")


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
    ne sont filtrés que si une piste sous-titre est en solo. Les entrées
    issues d'une séquence imbriquée portent la piste de leur clip racine :
    elles suivent donc le solo de la séquence évaluée.
    """
    from .timeline_evaluator import apply_track_solo

    return apply_track_solo(project.tracks, active_clips)
