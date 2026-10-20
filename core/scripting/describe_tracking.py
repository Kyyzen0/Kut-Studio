"""Ce que ``describe`` dit du suivi (tracking) d'un clip (ADR-0005, étape 5, partie C) : trackers, liaisons, stabilisation.

Les données d'un tracker sont une **grille dense d'échantillons** (positions, confiance, état, une valeur par image source) : les écrire
toutes rendrait la sortie énorme et illisible, et un script ne sait de toute façon poser que des points « manuels »
(``set_tracker_position``, un par appel). La description donne donc l'essentiel : l'identifiant, l'état (``counts``, ``valid_range``),
les réglages, les liaisons, la stabilisation, et **les points posés à la main** (``manual_points``, bornés à
:data:`MAX_MANUAL_POINTS`, avec ``manual_points_truncated``) avec l'instant de timeline (``timeline_time``) que le verbe attend. Les
échantillons analysés (état « suivi », « incertain », « perdu ») ne sont jamais écrits : seul leur nombre l'est.

Lecture seule, sans récursion et sans accès disque ; les comptes se font sur les octets de la grille, à la vitesse du C (une grille de
plusieurs millions d'images reste instantanée).
"""

from __future__ import annotations

import math
from typing import Any

from ..tracking_model import FIXED_POINT, SampleStatus
from ..tracking_motion import local_time_for_source
from .describe_values import number, optional_text, text

MAX_MANUAL_POINTS = 256
"""Points « manuels » écrits par tracker : au-delà, ``manual_points_truncated`` vaut ``true`` (le total reste dans ``counts.manual``)."""

_VALID = (SampleStatus.TRACKED, SampleStatus.UNCERTAIN, SampleStatus.MANUAL)


class ClipLookup:
    """Les clips d'une séquence par identifiant, construits à la première demande (une séquence sans liaison ne paie rien)."""

    def __init__(self, sequence: Any) -> None:
        self._sequence = sequence
        self._index: dict[str, Any] | None = None

    def get(self, clip_id: str) -> Any:
        if self._index is None:
            self._index = {clip.id: clip for track in self._sequence.tracks for clip in track.clips}
        return self._index.get(clip_id)


def frame_time(clip: Any, index: Any, rate: Any) -> float | None:
    """Instant de timeline (secondes) du **milieu** de l'image source ``index`` montrée par ``clip``, ou ``null`` s'il n'est pas dans le clip.

    Le milieu de l'image, pas son début : le modèle arrondit l'instant vers l'image (``floor`` avec 1e-6 de tolérance) et la sortie
    est arrondie à 6 décimales ; viser le milieu d'une image donne la même image après cet arrondi, à toute cadence."""
    try:
        if clip is None or not float(rate) > 0:
            return None
        local = float(local_time_for_source(clip, (int(index) + 0.5) / float(rate)))
        length = float(clip.duration)
    except (TypeError, ValueError, ArithmeticError, AttributeError):
        return None
    if not (math.isfinite(local) and math.isfinite(length)) or local < 0.0 or local >= length:
        return None
    return float(number(float(clip.timeline_start) + local))


def _settings(item: Any) -> dict[str, Any]:
    return {
        "pattern_width": number(item.pattern_width), "pattern_height": number(item.pattern_height),
        "search_width": number(item.search_width), "search_height": number(item.search_height),
        "min_confidence": number(item.min_confidence), "good_confidence": number(item.good_confidence),
        "adapt": text(item.adapt), "precision": text(item.precision), "stop_on_loss": bool(item.stop_on_loss),
    }


def _valid_range(status: bytes, first: int) -> list[int] | None:
    """Première et dernière image dont la position est utilisée (suivie, incertaine ou manuelle), sans parcourir la grille en Python."""
    found = [status.find(int(kind)) for kind in _VALID]
    last = [status.rfind(int(kind)) for kind in _VALID]
    found = [position for position in found if position >= 0]
    if not found:
        return None
    return [first + min(found), first + max(last)]


def _manual_points(clip: Any, data: Any) -> tuple[list[dict[str, Any]], bool]:
    status, manual = bytes(data.status), int(SampleStatus.MANUAL)
    xs, ys = memoryview(data.xs).cast("i"), memoryview(data.ys).cast("i")
    points: list[dict[str, Any]] = []
    position = status.find(manual)
    while position >= 0 and len(points) < MAX_MANUAL_POINTS:
        index = data.first + position
        points.append({
            "index": index, "timeline_time": frame_time(clip, index, data.rate),
            "x": number(xs[position] / FIXED_POINT), "y": number(ys[position] / FIXED_POINT),
        })
        position = status.find(manual, position + 1)
    return points, position >= 0


def _data(clip: Any, data: Any) -> dict[str, Any]:
    status = bytes(data.status)
    points, truncated = _manual_points(clip, data)
    return {
        "rate": number(data.rate), "first": number(data.first), "count": len(status),
        "source_width": number(data.source_width), "source_height": number(data.source_height),
        "valid_range": _valid_range(status, int(data.first)),
        "counts": {kind.name.lower(): status.count(int(kind)) for kind in SampleStatus},
        "manual_points": points, "manual_points_truncated": truncated,
    }


def _tracker(clip: Any, item: Any) -> dict[str, Any]:
    return {
        "id": text(item.id), "name": text(item.name), "color": text(item.color), "visible": bool(item.visible),
        "show_path": bool(item.show_path), "kind": text(item.kind), "settings": _settings(item.settings), "data": _data(clip, item.data),
    }


def _rate(clip: Any, tracker_ids: Any) -> float:
    """La cadence des données du premier tracker cité qui en a une (celle de l'index d'image de référence)."""
    for tracker_id in tracker_ids:
        tracker = clip.tracking.tracker(tracker_id) if clip is not None and clip.tracking is not None else None
        if tracker is not None and tracker.data.rate > 0:
            return float(tracker.data.rate)
    return 0.0


def _link(clip: Any, item: Any, lookup: ClipLookup) -> dict[str, Any]:
    source = clip if not item.source_clip_id else lookup.get(item.source_clip_id)
    return {
        "id": text(item.id), "source_clip_id": optional_text(item.source_clip_id),
        "tracker_ids": [text(tracker_id) for tracker_id in item.tracker_ids], "target": text(item.target),
        "mask_id": optional_text(item.mask_id), "position": bool(item.position), "rotation": bool(item.rotation),
        "scale": bool(item.scale), "reference_index": number(item.reference_index), "enabled": bool(item.enabled),
        "continuation_ids": [text(clip_id) for clip_id in item.continuation_ids],
        "reference_time": frame_time(source, item.reference_index, _rate(source, item.tracker_ids)),
    }


def _stabilization(clip: Any, item: Any) -> dict[str, Any] | None:
    if item is None:
        return None
    return {
        "enabled": bool(item.enabled), "tracker_ids": [text(tracker_id) for tracker_id in item.tracker_ids], "mode": text(item.mode),
        "smoothing": text(item.smoothing), "smoothing_frames": number(item.smoothing_frames), "borders": text(item.borders),
        "reference_index": number(item.reference_index),
        "shared_range": None if item.shared_range is None else [number(bound) for bound in item.shared_range],
        "reference_time": frame_time(clip, item.reference_index, _rate(clip, item.tracker_ids)),
    }


def tracking(clip: Any, lookup: ClipLookup) -> dict[str, Any] | None:
    """Le suivi d'un clip (``null`` : aucun tracker, aucune liaison, aucune stabilisation)."""
    item = clip.tracking
    if item is None or item.is_empty:
        return None
    return {
        "trackers": [_tracker(clip, tracker) for tracker in item.trackers],
        "links": [_link(clip, link, lookup) for link in item.links],
        "stabilization": _stabilization(clip, item.stabilization),
    }


__all__ = ["MAX_MANUAL_POINTS", "ClipLookup", "frame_time", "tracking"]
