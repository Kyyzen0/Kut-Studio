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
from ..tracking_motion import clip_source_indices, local_time_for_source
from .describe_values import number, optional_text, text

MAX_MANUAL_POINTS = 64
"""Points « manuels » écrits par tracker : au-delà, ``manual_points_truncated`` vaut ``true`` (le total reste dans ``counts.manual``).
Un script pose un point par opération (5 000 opérations au plus) : la sortie ne peut pas valoir plus que ce qu'un script réécrit, et reste
bornée par tracker quel que soit le nombre de clips."""

_VALID = (SampleStatus.TRACKED, SampleStatus.UNCERTAIN, SampleStatus.MANUAL)


class ClipLookup:
    """Les clips d'une séquence par identifiant, construits à la première demande (une séquence sans liaison ne paie rien)."""

    def __init__(self, sequence: Any, assets: dict[str, Any] | None = None) -> None:
        self._sequence = sequence
        self._assets = assets or {}
        self._index: dict[str, Any] | None = None

    def size(self, clip: Any) -> tuple[int, int] | None:
        """Dimensions COURANTES du média du clip (celles que lit ``set_tracker_position``) ; ``None`` si le média manque ou n'a pas de taille."""
        asset = self._assets.get(getattr(clip, "asset_id", ""))
        if asset is None or not asset.width > 0 or not asset.height > 0:
            return None
        return int(asset.width), int(asset.height)

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


def _manual_points(clip: Any, data: Any, size: tuple[int, int] | None) -> tuple[list[dict[str, Any]], bool]:
    """Les points posés à la main, en pixels du média **courant** : un point est stocké dans la taille d'origine des données
    (``source_width`` x ``source_height``) ; ``set_tracker_position`` lit des pixels du média courant et divise par ``scaled_to`` : on multiplie
    par le même facteur (``scaled_to`` rend 1 sans taille connue), donc rejouer un point décrit retombe sur la valeur stockée."""
    fx, fy = data.scaled_to(*size) if size else (1.0, 1.0)
    status, manual = bytes(data.status), int(SampleStatus.MANUAL)
    xs, ys = memoryview(data.xs).cast("i"), memoryview(data.ys).cast("i")
    points: list[dict[str, Any]] = []
    position = status.find(manual)
    while position >= 0 and len(points) < MAX_MANUAL_POINTS:
        index = data.first + position
        points.append({
            "index": index, "timeline_time": frame_time(clip, index, data.rate),
            "x": number(xs[position] / FIXED_POINT * fx), "y": number(ys[position] / FIXED_POINT * fy),
        })
        position = status.find(manual, position + 1)
    return points, position >= 0


def _data(clip: Any, data: Any, size: tuple[int, int] | None) -> dict[str, Any]:
    status = bytes(data.status)
    points, truncated = _manual_points(clip, data, size)
    return {
        "rate": number(data.rate), "first": number(data.first), "count": len(status),
        "source_width": number(data.source_width), "source_height": number(data.source_height),
        "points_width": None if size is None else size[0], "points_height": None if size is None else size[1],
        "valid_range": _valid_range(status, int(data.first)),
        "counts": {kind.name.lower(): status.count(int(kind)) for kind in SampleStatus},
        "manual_points": points, "manual_points_truncated": truncated,
    }


def _tracker(clip: Any, item: Any, size: tuple[int, int] | None) -> dict[str, Any]:
    return {
        "id": text(item.id), "name": text(item.name), "color": text(item.color), "visible": bool(item.visible),
        "show_path": bool(item.show_path), "kind": text(item.kind), "settings": _settings(item.settings), "data": _data(clip, item.data, size),
    }


def _rate(clip: Any, tracker_ids: Any) -> float:
    """La cadence des données du premier tracker cité qui en a une (celle de l'index d'image de référence)."""
    for tracker_id in tracker_ids:
        tracker = clip.tracking.tracker(tracker_id) if clip is not None and clip.tracking is not None else None
        if tracker is not None and tracker.data.rate > 0:
            return float(tracker.data.rate)
    return 0.0


def _reference_clip(clip: Any, item: Any, lookup: ClipLookup) -> Any:
    """Le clip qui montre l'image de référence d'une liaison : celui que le moteur de rendu choisit (``LinkMotion``).

    Une liaison suit sa source et, après une coupe, ses prolongements (``source_ids``), tous dans la **même séquence** que le clip lié
    (``TrackingContext.clip`` ne cherche que dans la séquence rendue : un clip d'une autre séquence n'est pas une source, ``link_issues`` le
    signale). Comme le moteur : les clips qui existent, ont des données de ces trackers et un média de taille connue ; le premier qui montre
    l'image de référence, sinon le premier."""
    if not item.source_clip_id:
        return clip
    usable = []
    for clip_id in item.source_ids:
        found = lookup.get(clip_id)
        if found is None or found.graphic is not None or lookup.size(found) is None or found.tracking is None:
            continue
        if any(found.tracking.tracker(tracker_id) is not None for tracker_id in item.tracker_ids):
            usable.append(found)
    for found in usable:
        rate = _rate(found, item.tracker_ids)
        try:
            low, high = clip_source_indices(found, rate)
        except (TypeError, ValueError, ArithmeticError, AttributeError):
            continue
        if low <= item.reference_index <= high:
            return found
    return usable[0] if usable else None


def _link(clip: Any, item: Any, lookup: ClipLookup) -> dict[str, Any]:
    owner = _reference_clip(clip, item, lookup)
    return {
        "id": text(item.id), "source_clip_id": optional_text(item.source_clip_id),
        "tracker_ids": [text(tracker_id) for tracker_id in item.tracker_ids], "target": text(item.target),
        "mask_id": optional_text(item.mask_id), "position": bool(item.position), "rotation": bool(item.rotation),
        "scale": bool(item.scale), "reference_index": number(item.reference_index), "enabled": bool(item.enabled),
        "continuation_ids": [text(clip_id) for clip_id in item.continuation_ids],
        "reference_clip_id": None if owner is None else text(owner.id),
        "reference_time": frame_time(owner, item.reference_index, _rate(owner, item.tracker_ids)),
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
        "trackers": [_tracker(clip, tracker, lookup.size(clip)) for tracker in item.trackers],
        "links": [_link(clip, link, lookup) for link in item.links],
        "stabilization": _stabilization(clip, item.stabilization),
    }


__all__ = ["MAX_MANUAL_POINTS", "ClipLookup", "frame_time", "tracking"]
