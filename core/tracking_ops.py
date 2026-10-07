"""Opérations d'édition du tracking sur un projet.

Chaque fonction modifie le projet **en place** et lève :class:`TrackingError`
(message lisible) si l'opération est impossible. L'interface enregistre
ensuite **une** entrée d'historique : une analyse complète, une liaison, un
bake ou un geste de correction dans le viewer n'en font qu'une, jamais une
par image analysée.
"""

from __future__ import annotations

import math
from dataclasses import replace

from .tracking_model import (
    SELF_CLIP,
    BorderMode,
    ClipTracking,
    Sample,
    SampleStatus,
    Stabilization,
    StabilizationMode,
    TrackData,
    Tracker,
    TrackerSettings,
    TrackLink,
    TrackTarget,
    next_tracker_color,
)
from .tracking_motion import clip_source_indices, source_time


class TrackingError(ValueError):
    """Opération de tracking impossible (message pour l'utilisateur)."""


# ---------------------------------------------------------------------------
# Accès
# ---------------------------------------------------------------------------


def find_clip_and_track(project, clip_id: str, sequence=None):
    """Le clip et sa piste : séquence donnée, sinon active, sinon toutes.

    Une analyse peut se terminer après un changement de séquence : son
    résultat retrouve quand même son clip.
    """
    sequences = [sequence if sequence is not None else project.active_sequence]
    if sequence is None:
        sequences += [s for s in getattr(project, "sequences", ()) if s is not sequences[0]]
    for current in sequences:
        for track in current.tracks:
            for clip in track.clips:
                if clip.id == clip_id:
                    return clip, track
    raise TrackingError(f"Clip introuvable : {clip_id}")


def media_of(project, clip):
    return next((a for a in project.media_assets if a.id == clip.asset_id), None)


def trackable_clip(project, clip_id: str):
    """Le clip vidéo (média vidéo, non imbriqué) qui peut porter des trackers."""
    clip, track = find_clip_and_track(project, clip_id)
    if track.type != "video" or clip.sequence_id:
        raise TrackingError("Le tracking s'applique à un clip vidéo.")
    asset = media_of(project, clip)
    if asset is None or asset.media_type not in ("video", "image") or asset.width <= 0:
        raise TrackingError("Le média de ce clip n'a pas d'image à suivre.")
    if asset.media_type == "video" and asset.fps <= 0:
        raise TrackingError("Cadence du média inconnue.")
    return clip, asset


def media_rate(asset) -> float:
    return float(asset.fps) if asset.fps > 0 else 30.0


def tracking_of(clip) -> ClipTracking:
    return clip.tracking if clip.tracking is not None else ClipTracking()


def _store(clip, tracking: ClipTracking) -> None:
    clip.tracking = None if tracking.is_empty else tracking


def source_index_at(clip, rate: float, timeline_time: float) -> int:
    """Image source montrée par ``clip`` à ``timeline_time`` (bornée au clip)."""
    local = max(0.0, min(float(clip.duration) - 1e-6, float(timeline_time) - float(clip.timeline_start)))
    return max(0, int(math.floor(source_time(clip, local) * rate + 1e-6)))


def get_tracker(project, clip_id: str, tracker_id: str) -> tuple[object, Tracker]:
    clip, _track = find_clip_and_track(project, clip_id)
    tracker = tracking_of(clip).tracker(tracker_id)
    if tracker is None:
        raise TrackingError("Tracker introuvable.")
    return clip, tracker


def _put_tracker(clip, tracker: Tracker) -> None:
    _store(clip, tracking_of(clip).with_tracker(tracker))


# ---------------------------------------------------------------------------
# Trackers
# ---------------------------------------------------------------------------


def add_tracker(
    project,
    clip_id: str,
    *,
    timeline_time: float,
    x: float | None = None,
    y: float | None = None,
    name: str = "",
    settings: TrackerSettings | None = None,
) -> Tracker:
    """Ajoute un point (pixels du média ; centre par défaut) à l'image affichée."""
    clip, asset = trackable_clip(project, clip_id)
    rate = media_rate(asset)
    tracking = tracking_of(clip)
    index = source_index_at(clip, rate, timeline_time)
    px = float(asset.width) / 2.0 if x is None else float(x)
    py = float(asset.height) / 2.0 if y is None else float(y)
    if not (math.isfinite(px) and math.isfinite(py)):
        raise TrackingError("Position invalide.")
    px = max(0.0, min(float(asset.width), px))
    py = max(0.0, min(float(asset.height), py))
    data = TrackData.from_samples(
        rate, {index: Sample(px, py, 1.0, SampleStatus.MANUAL)}, source_size=(asset.width, asset.height),
    )
    number = len(tracking.trackers) + 1
    tracker = Tracker(
        name=name or f"Tracker {number}", color=next_tracker_color(tracking.trackers),
        settings=settings or _default_settings(asset), data=data,
    )
    _store(clip, tracking.with_tracker(tracker))
    return tracker


def _default_settings(asset) -> TrackerSettings:
    """Zones proportionnées au média (≈ 2,5 % / 7,5 % de la largeur, bornées)."""
    width = float(asset.width)
    pattern = max(24.0, min(160.0, round(width * 0.025 / 2) * 2))
    search = max(pattern * 2.5, min(480.0, round(width * 0.075 / 2) * 2))
    return TrackerSettings(pattern_width=pattern, pattern_height=pattern, search_width=search, search_height=search)


def remove_tracker(project, clip_id: str, tracker_id: str) -> None:
    """Supprime le tracker, ses usages par la stabilisation et les liaisons qui en dépendent."""
    clip, _tracker = get_tracker(project, clip_id, tracker_id)
    _store(clip, tracking_of(clip).without_tracker(tracker_id))
    for track in project.active_sequence.tracks:
        for other in track.clips:
            if other is clip or other.tracking is None:
                continue
            links = []
            for link in other.tracking.links:
                if link.follows(clip_id) and tracker_id in link.tracker_ids and not _still_tracked_elsewhere(
                    project, link, clip_id, tracker_id
                ):
                    remaining = tuple(t for t in link.tracker_ids if t != tracker_id)
                    if not remaining:
                        continue
                    link = replace(link, tracker_ids=remaining)
                links.append(link)
            _store(other, replace(other.tracking, links=tuple(links)))


def _still_tracked_elsewhere(project, link: TrackLink, removed_clip_id: str, tracker_id: str) -> bool:
    """Une autre partie de la source (après une coupe) porte-t-elle encore ce tracker ?

    Les moitiés d'un clip coupé ont chacune leur copie des trackers : en supprimer un sur l'une ne doit pas
    défaire la liaison qui suit encore l'autre.
    """
    for source_id in link.source_ids:
        if source_id == removed_clip_id:
            continue
        try:
            part, _track = find_clip_and_track(project, source_id)
        except TrackingError:
            continue
        if tracking_of(part).tracker(tracker_id) is not None:
            return True
    return False


def update_tracker(project, clip_id: str, tracker_id: str, **changes) -> Tracker:
    """Nom, couleur, visibilité, affichage du chemin."""
    clip, tracker = get_tracker(project, clip_id, tracker_id)
    allowed = {"name", "color", "visible", "show_path"}
    unknown = set(changes) - allowed
    if unknown:
        raise TrackingError(f"Champ de tracker inconnu : {', '.join(sorted(unknown))}")
    updated = replace(tracker, **changes)
    _put_tracker(clip, updated)
    return updated


def set_tracker_settings(project, clip_id: str, tracker_id: str, **changes) -> Tracker:
    """Zones et seuils (les données déjà suivies restent ; relancer pour les recalculer)."""
    clip, tracker = get_tracker(project, clip_id, tracker_id)
    try:
        settings = replace(tracker.settings, **changes)
    except TypeError as exc:
        raise TrackingError(str(exc)) from exc
    updated = replace(tracker, settings=settings)
    _put_tracker(clip, updated)
    return updated


def set_tracker_position(project, clip_id: str, tracker_id: str, timeline_time: float, x: float, y: float) -> int:
    """Correction manuelle à l'image affichée ; retourne l'image source corrigée.

    Les autres images (suivies ou corrigées) ne sont pas touchées : relancer
    l'analyse depuis cette image recalcule la suite.
    """
    clip, asset = trackable_clip(project, clip_id)
    _clip, tracker = get_tracker(project, clip_id, tracker_id)
    if not (math.isfinite(x) and math.isfinite(y)):
        raise TrackingError("Position invalide.")
    rate = tracker.data.rate or media_rate(asset)
    index = source_index_at(clip, rate, timeline_time)
    fx, fy = tracker.data.scaled_to(asset.width, asset.height)
    sample = Sample(
        max(-asset.width, min(2.0 * asset.width, float(x))) / fx,
        max(-asset.height, min(2.0 * asset.height, float(y))) / fy,
        1.0, SampleStatus.MANUAL,
    )
    _put_tracker(clip, replace(tracker, data=tracker.data.with_samples({index: sample}, rate=rate)))
    return index


def remove_correction(project, clip_id: str, tracker_id: str, timeline_time: float) -> bool:
    """Retire la correction manuelle de l'image affichée (si c'en est une)."""
    clip, asset = trackable_clip(project, clip_id)
    _clip, tracker = get_tracker(project, clip_id, tracker_id)
    index = source_index_at(clip, tracker.data.rate or media_rate(asset), timeline_time)
    if tracker.data.status_at(index) is not SampleStatus.MANUAL:
        return False
    if len(tracker.data.valid_indices()) <= 1:
        raise TrackingError("Le dernier point d'un tracker ne peut pas être retiré.")
    data = tracker.data.cleared(index, index, keep_manual=False)
    _put_tracker(clip, replace(tracker, data=data))
    return True


def reset_tracker(
    project, clip_id: str, tracker_id: str, *, timeline_time: float | None = None, direction: int = 0,
) -> int:
    """Efface les images **analysées** (les corrections manuelles restent).

    ``direction`` : ``0`` tout le tracker ; ``+1`` après l'image affichée ;
    ``-1`` avant. Retourne le nombre d'images effacées.
    """
    clip, asset = trackable_clip(project, clip_id)
    _clip, tracker = get_tracker(project, clip_id, tracker_id)
    data = tracker.data
    if data.count == 0:
        return 0
    low, high = data.first, data.last
    if direction and timeline_time is not None:
        index = source_index_at(clip, data.rate or media_rate(asset), timeline_time)
        low, high = (index + 1, data.last) if direction > 0 else (data.first, index - 1)
    if high < low:
        return 0
    before = sum(1 for _i, s in data.samples() if s.status is not SampleStatus.EMPTY)
    cleared = data.cleared(low, high, keep_manual=True)
    if cleared.is_empty:
        raise TrackingError("Le tracker doit garder au moins un point.")
    after = sum(1 for _i, s in cleared.samples() if s.status is not SampleStatus.EMPTY)
    _put_tracker(clip, replace(tracker, data=cleared))
    return before - after


# ---------------------------------------------------------------------------
# Analyse
# ---------------------------------------------------------------------------


def analysis_request(
    project,
    clip_id: str,
    tracker_ids,
    *,
    timeline_time: float,
    direction: int,
    proxy_path: str = "",
):
    """Requête d'analyse depuis l'image affichée jusqu'au bord du clip.

    Seules les images montrées par le clip sont analysées (hors trim : rien).
    Les trackers sans position valide à l'image de départ partent de leur
    image valide la plus proche dans le sens demandé.
    """
    from .cache_keys import source_signature
    from .tracking_engine import TrackingRequest

    clip, asset = trackable_clip(project, clip_id)
    tracking = tracking_of(clip)
    candidates = [tracking.tracker(t) for t in tracker_ids]
    trackers = [t for t in candidates if t is not None and t.visible]
    if not trackers:
        raise TrackingError("Aucun tracker à analyser.")
    rate = media_rate(asset)
    first, last = clip_source_indices(clip, rate)
    start = max(first, min(last, source_index_at(clip, rate, timeline_time)))
    end = last if direction >= 0 else first
    ready = []
    for tracker in trackers:
        if not tracker.data.sample(start).valid:
            raise TrackingError(
                f"« {tracker.name} » n'a pas de position à cette image : placez-le, "
                "ou placez la tête de lecture sur une image suivie."
            )
        ready.append(tracker)
    path = proxy_path or asset.path
    signature = source_signature(path)
    return TrackingRequest(
        clip_id=clip.id, media_path=asset.path, media_size=(int(asset.width), int(asset.height)),
        rate=rate, trackers=tuple(ready), start_index=start, end_index=end,
        decode_path=proxy_path, source_token=signature.token if signature is not None else "missing",
    )


def apply_tracking_result(project, result) -> dict[str, int]:
    """Fusionne un résultat d'analyse ; retourne le nombre d'images écrites par tracker.

    Les corrections manuelles ne sont jamais écrasées. Un tracker supprimé
    pendant l'analyse est ignoré.
    """
    try:
        clip, _track = find_clip_and_track(project, result.request.clip_id)
    except TrackingError:
        return {}
    written: dict[str, int] = {}
    for tracker_id, outcome in result.outcomes.items():
        tracker = tracking_of(clip).tracker(tracker_id)
        if tracker is None or not outcome.samples:
            continue
        updates = {
            index: sample for index, sample in outcome.samples.items()
            if tracker.data.status_at(index) is not SampleStatus.MANUAL
        }
        if not updates:
            continue
        data = tracker.data.with_samples(updates, rate=result.request.rate)
        _put_tracker(clip, replace(tracker, data=data))
        written[tracker_id] = len(updates)
    return written


# ---------------------------------------------------------------------------
# Liaisons
# ---------------------------------------------------------------------------


def tracking_dependents(project, source_clip_id: str) -> list[str]:
    """Identifiants des **autres** clips de la séquence active dont une liaison active suit ``source_clip_id``.

    Une liaison suit la source **et** ses prolongements après coupe (:attr:`TrackLink.source_ids`) : la liste
    sert à prévenir avant de couper ou de supprimer une source, et à dire qui suit après une coupe.
    """
    return [
        clip.id
        for track in project.active_sequence.tracks
        for clip in track.clips
        if clip.id != source_clip_id
        and any(
            link.enabled and link.follows(source_clip_id)
            for link in getattr(getattr(clip, "tracking", None), "links", ()) or ()
        )
    ]


# ---------------------------------------------------------------------------
# Coupe et suppression de la source d'un tracking
# ---------------------------------------------------------------------------


def tracking_for_cut(clip) -> ClipTracking | None:
    """Tracking que reçoivent **les deux** moitiés d'un clip coupé.

    Les trackers sont en temps source et immuables : les deux moitiés les partagent sans copie, chacune en
    lit la portion que son intervalle montre (``source_in`` / ``source_out`` et remappage de temps).
    Seule la stabilisation à zoom ou recadrage automatique reçoit une plage commune (``shared_range`` :
    le plan d'origine) : sans elle, chaque moitié calculerait son propre agrandissement et l'image
    sauterait au point de coupe.
    """
    tracking: ClipTracking | None = getattr(clip, "tracking", None)
    if tracking is None or tracking.stabilization is None:
        return tracking
    stabilization = tracking.stabilization
    if stabilization.borders == BorderMode.BLACK:
        return tracking
    datas = [t.data for t in (tracking.tracker(tid) for tid in stabilization.tracker_ids) if t is not None]
    rate = next((d.rate for d in datas if d.rate > 0), 0.0)
    if rate <= 0:
        return tracking
    first, last = clip_source_indices(clip, rate)
    shared = stabilization.shared_range
    if shared is not None:
        first, last = min(first, shared[0]), max(last, shared[1])
    return replace(tracking, stabilization=replace(stabilization, shared_range=(first, last)))


def follow_cut(project, left_id: str, right_id: str) -> list[str]:
    """Après la coupe de ``left_id`` : les liaisons qui le suivaient suivent aussi ``right_id``.

    Retourne les identifiants des clips dont une liaison a été étendue. À chaque instant, la liaison
    suit celle des deux moitiés qui couvre l'instant (:class:`core.tracking_bindings.LinkMotion`) : le
    mouvement ne s'arrête plus à la coupe. À appeler dans la même opération que la coupe (une seule
    entrée d'historique).
    """
    updated: list[str] = []
    for track in project.all_tracks():
        for clip in track.clips:
            tracking = clip.tracking
            if tracking is None or not any(link.follows(left_id) for link in tracking.links):
                continue
            links = tuple(link.after_cut(left_id, right_id) for link in tracking.links)
            if links != tracking.links:
                clip.tracking = replace(tracking, links=links)
                updated.append(clip.id)
    return updated


def release_source(project, clip_id: str) -> list[str]:
    """Après la suppression de ``clip_id`` : les liaisons suivent les parties restantes de la source.

    Retourne les clips dont la liaison **n'a plus aucune source** (ni ``clip_id`` ni prolongement) : ils
    sont rendus tels quels (liaison signalée « source introuvable », jamais effacée en silence), et
    l'interface le dit à l'utilisateur.
    """
    orphaned: list[str] = []
    for track in project.all_tracks():
        for clip in track.clips:
            tracking = clip.tracking
            if tracking is None or not any(link.follows(clip_id) for link in tracking.links):
                continue
            links = tuple(link.without_source(clip_id) for link in tracking.links)
            if links != tracking.links:
                clip.tracking = replace(tracking, links=links)
            if any(link.source_clip_id == clip_id and link.enabled for link in links):
                orphaned.append(clip.id)
    return orphaned


def link_targets(project, source_clip_id: str) -> list[dict]:
    """Cibles possibles des trackers de ``source_clip_id`` (interface).

    Chaque entrée : ``{"clip_id", "label", "kind": "graphics"|"video", "targets": [...]}``
    où ``targets`` liste ``("transform", "")``, ``("anchor", "")``, ``("mask", mask_id)``.
    """
    result = []
    for track in project.active_sequence.tracks:
        if track.type not in ("video", "graphics"):
            continue
        for clip in track.clips:
            if clip.sequence_id:
                continue  # un clip imbriqué ne reçoit pas de liaison (limite documentée)
            targets = []
            if clip.id == source_clip_id:
                targets.append((TrackTarget.ANCHOR, ""))
            else:
                targets.append((TrackTarget.TRANSFORM, ""))
            for mask in getattr(clip.compositing, "masks", ()) or ():
                targets.append((TrackTarget.MASK, mask.id))
            result.append({
                "clip_id": clip.id, "label": clip.label or clip.id, "kind": track.type, "targets": targets,
                "masks": {m.id: (m.name or m.shape.value) for m in getattr(clip.compositing, "masks", ()) or ()},
            })
    return result


def add_link(
    project,
    target_clip_id: str,
    source_clip_id: str,
    tracker_ids,
    *,
    target: str = TrackTarget.TRANSFORM,
    mask_id: str = "",
    position: bool = True,
    rotation: bool = False,
    scale: bool = False,
    timeline_time: float = 0.0,
) -> TrackLink:
    """Lie une propriété de ``target_clip_id`` aux trackers de ``source_clip_id``.

    L'image de référence est celle affichée : la cible ne bouge pas à cet
    instant, elle suit ensuite le mouvement mesuré.
    """
    source, asset = trackable_clip(project, source_clip_id)
    target_clip, target_track = find_clip_and_track(project, target_clip_id)
    if target_track.type not in ("video", "graphics") or target_clip.sequence_id:
        raise TrackingError("Seuls les clips vidéo et les calques peuvent suivre un tracker.")
    tracking = tracking_of(source)
    ids = tuple(dict.fromkeys(tracker_ids))
    found = [tracking.tracker(t) for t in ids]
    trackers = [t for t in found if t is not None]
    if not ids or len(trackers) != len(found):
        raise TrackingError("Tracker introuvable.")
    if any(len(t.data.valid_indices()) < 2 for t in trackers):
        raise TrackingError("Analysez le tracker avant de l'appliquer.")
    same = target_clip is source
    if target == TrackTarget.TRANSFORM and same:
        raise TrackingError(
            "Un clip ne peut pas suivre ses propres trackers : utilisez la stabilisation "
            "ou liez le point d'ancrage."
        )
    if target == TrackTarget.ANCHOR and not same:
        raise TrackingError("Le point d'ancrage ne peut suivre que les trackers de son propre clip.")
    if target == TrackTarget.MASK:
        compositing = getattr(target_clip, "compositing", None)
        if compositing is None or compositing.mask_by_id(mask_id) is None:
            raise TrackingError("Masque introuvable.")
    if (rotation or scale) and len(ids) < 2:
        raise TrackingError("Rotation et échelle demandent au moins deux trackers.")
    rate = trackers[0].data.rate or media_rate(asset)
    reference = source_index_at(source, rate, timeline_time)
    link = TrackLink(
        source_clip_id=SELF_CLIP if same else source.id, tracker_ids=ids, target=target, mask_id=mask_id,
        position=position or target == TrackTarget.ANCHOR, rotation=rotation, scale=scale,
        reference_index=reference,
    )
    current = tracking_of(target_clip)
    # Une propriété ne suit qu'une liaison : la nouvelle remplace l'ancienne.
    kept = tuple(
        existing for existing in current.links
        if not (existing.target == link.target and existing.mask_id == link.mask_id)
    )
    _store(target_clip, replace(current, links=kept + (link,)))
    return link


def find_link(project, clip_id: str, link_id: str):
    clip, _track = find_clip_and_track(project, clip_id)
    link = tracking_of(clip).link(link_id)
    if link is None:
        raise TrackingError("Liaison introuvable.")
    return clip, link


def remove_link(project, clip_id: str, link_id: str) -> None:
    clip, _link = find_link(project, clip_id, link_id)
    _store(clip, tracking_of(clip).without_link(link_id))


def set_link_enabled(project, clip_id: str, link_id: str, enabled: bool) -> None:
    clip, link = find_link(project, clip_id, link_id)
    _store(clip, tracking_of(clip).with_link(replace(link, enabled=bool(enabled))))


def bake_link(project, clip_id: str, link_id: str) -> int:
    """Convertit la liaison en images-clés indépendantes ; retourne leur nombre."""
    from .tracking_bindings import baked_keyframes

    clip, link = find_link(project, clip_id, link_id)
    baked = baked_keyframes(project, clip, link_id)
    if baked is None:
        raise TrackingError("Liaison introuvable.")
    transform_frames, animation = baked
    changed = _changed_properties(clip, transform_frames, animation)
    if not changed:
        raise TrackingError("Aucune donnée de tracking à convertir sur la durée du clip.")
    clip.transform_keyframes = transform_frames
    clip.animation = animation
    _store(clip, tracking_of(clip).without_link(link.id))
    return sum(1 for kf in [*transform_frames, *animation] if kf.property_name in changed)


def _changed_properties(clip, transform_frames, animation) -> set[str]:
    def group(frames):
        grouped: dict[str, list] = {}
        for kf in frames:
            grouped.setdefault(kf.property_name, []).append(kf)
        return {k: tuple(v) for k, v in grouped.items()}

    before = {**group(clip.transform_keyframes), **group(clip.animation)}
    after = {**group(transform_frames), **group(animation)}
    return {name for name in set(before) | set(after) if before.get(name) != after.get(name)}


# ---------------------------------------------------------------------------
# Stabilisation
# ---------------------------------------------------------------------------


def set_stabilization(project, clip_id: str, **fields) -> Stabilization:
    """Active / règle la stabilisation du clip (crée l'objet au besoin)."""
    clip, _asset = trackable_clip(project, clip_id)
    tracking = tracking_of(clip)
    current = tracking.stabilization or Stabilization(tracker_ids=tuple(t.id for t in tracking.trackers[:2]))
    try:
        updated = replace(current, **fields)
    except TypeError as exc:
        raise TrackingError(str(exc)) from exc
    missing = [t for t in updated.tracker_ids if tracking.tracker(t) is None]
    if missing or not updated.tracker_ids:
        raise TrackingError("Choisissez au moins un tracker analysé pour stabiliser.")
    if updated.mode != StabilizationMode.POSITION and len(updated.tracker_ids) < 2:
        raise TrackingError("Rotation et échelle demandent deux trackers.")
    _store(clip, replace(tracking, stabilization=updated))
    return updated


def remove_stabilization(project, clip_id: str) -> None:
    clip, _track = find_clip_and_track(project, clip_id)
    if clip.tracking is not None:
        _store(clip, replace(clip.tracking, stabilization=None))


def stabilization_reference(project, clip_id: str, timeline_time: float) -> int:
    clip, asset = trackable_clip(project, clip_id)
    return source_index_at(clip, media_rate(asset), timeline_time)


def stabilization_report(project, clip_id: str) -> dict:
    """Zoom de compensation et état, pour l'interface."""
    from .tracking_bindings import TrackingContext

    clip, _track = find_clip_and_track(project, clip_id)
    result = TrackingContext(project).stabilization(clip)
    if result is None:
        return {"active": False, "zoom": 1.0, "message": ""}
    return {"active": True, "zoom": result.zoom, "message": result.message, "crop": result.crop_rect}


__all__ = [
    "TrackingError", "add_link", "add_tracker", "analysis_request", "apply_tracking_result",
    "bake_link", "find_clip_and_track", "find_link", "get_tracker", "link_targets", "media_of",
    "media_rate", "remove_correction", "remove_link", "remove_stabilization", "remove_tracker",
    "reset_tracker", "set_link_enabled", "set_stabilization", "set_tracker_position",
    "set_tracker_settings", "source_index_at", "stabilization_reference", "stabilization_report",
    "trackable_clip", "tracking_of", "update_tracker",
]
