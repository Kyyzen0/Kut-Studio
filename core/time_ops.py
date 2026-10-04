"""Opérations d'édition du temps d'un clip : points de vitesse, interpolation des images, audio.

Elles s'appuient sur les primitives de keyframes existantes (:mod:`core.keyframe_editing`) : un point de vitesse est un
keyframe ``time.speed`` comme un autre, avec ses interpolations (palier, linéaire, ease, Bézier) et ses tangentes. Ce qui
est propre au temps tient ici :

**Durée dérivée, donc ripple.** La durée d'un clip se déduit de sa courbe (la source est épuisée) : modifier un point de
vitesse change donc la durée. Deux politiques (:class:`RippleMode`) :

- ``SOURCE`` (défaut, comportement historique de « vitesse ») : la portion de source est conservée, la durée sur la
  timeline change — ralentir allonge le clip ;
- ``TIMELINE`` : la durée sur la timeline est conservée, la portion de source utilisée change (le clip consomme plus ou
  moins de média ; limité par la fin du média) — c'est un trim droit après l'édition.

**Transactionnel.** Chaque opération valide le clip obtenu (durée non dégénérée) et restaure l'état d'avant si elle le
refuse : jamais de clip à moitié modifié. Les keyframes situés après la nouvelle fin sont découpés, comme pour tout
changement de durée (:func:`core.timeline_operations._fit_animation_to_duration`).

Aucune de ces opérations n'enregistre d'historique : l'appelant (la fenêtre) le fait, une entrée par geste.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any

from . import keyframe_editing as keyframes
from .animation import TIME_EPSILON, AnimationCurve, InterpolationType, Keyframe, normalize_time
from .keyframe_editing import KeyframeRef
from .project_model import Clip, Project
from .time_editing import free_map
from .time_map import SPEED_LIMIT, SPEED_PROPERTY
from .time_targets import SPEED_TARGET
from .time_remapping import FlowQuality, FreezeFrameMode, TimeInterpolation
from .timeline_operations import (
    _clear_speed_keyframes,
    _ensure_track_editable,
    _find_track_for_clip,
    _fit_animation_to_duration,
    _restore_extent,
    _source_bounds,
    trim_clip_right,
)

MIN_CLIP_SECONDS = 0.04
"""Durée minimale d'un clip après une édition de vitesse (en deçà : édition refusée, état restauré)."""

HOLD_SECONDS = 1.0
"""Durée par défaut d'un arrêt sur image inséré dans la courbe de vitesse."""
DEFAULT_RATE = 30.0
"""Cadence supposée quand l'appelant n'en donne pas : sert seulement à placer la première image du palier."""


class RippleMode(str, Enum):
    """Ce que conserve une édition de vitesse quand elle change la durée du clip."""

    SOURCE = "source"
    """La portion de source ; la durée sur la timeline change (défaut)."""
    TIMELINE = "timeline"
    """La durée sur la timeline ; la portion de source utilisée change."""


def _editable_clip(project: Project, clip_id: str) -> Clip:
    track, index = _find_track_for_clip(project, clip_id)
    _ensure_track_editable(project, track)
    clip = track.clips[index]
    if clip.time_remapping.freeze_mode == FreezeFrameMode.FREEZE:
        raise ValueError("Un arrêt sur image n'a pas de vitesse : retirez-le d'abord.")
    return clip


@dataclass(frozen=True)
class TimeState:
    """Tout ce qu'une édition de vitesse peut changer d'un clip (les keyframes sont immuables : une copie de liste suffit)."""

    animation: tuple[Any, ...]
    time_remapping: Any
    source_in: float
    source_out: float
    transform_keyframes: tuple[Any, ...]
    timeline_start: float


def capture_time_state(clip: Clip) -> TimeState:
    """Instantané du temps du clip, pour y revenir (:func:`restore_time_state`)."""
    return TimeState(
        tuple(clip.animation), clip.time_remapping, clip.source_in, clip.source_out,
        tuple(clip.transform_keyframes), clip.timeline_start,
    )


def restore_time_state(clip: Clip, state: TimeState) -> None:
    """Remet le clip dans l'état de ``state`` (sa courbe de vitesse, sa fenêtre source, ses keyframes, sa position)."""
    clip.animation = list(state.animation)
    clip.time_remapping = state.time_remapping
    clip.source_in = state.source_in
    clip.source_out = state.source_out
    clip.transform_keyframes = list(state.transform_keyframes)
    clip.timeline_start = state.timeline_start


@contextmanager
def _transaction(project: Project, clip: Clip, mode: RippleMode) -> Iterator[None]:
    """Applique une édition de vitesse ; la restaure si le clip obtenu est refusé.

    Après l'édition : politique de ripple, découpe des keyframes d'après la fin, puis contrôle de la durée.
    """
    saved = capture_time_state(clip)
    old_duration = clip.duration
    try:
        yield
        if mode is RippleMode.TIMELINE and abs(clip.duration - old_duration) > 1e-9:
            # Durée conservée, dans la limite du média : si la source manque, le clip est plus court (jamais refusé).
            _name, media_limit = _source_bounds(project, clip)
            free = free_map(clip, max(media_limit, clip.source_out), old_duration)
            reachable = old_duration if getattr(free, "underdetermined", False) else min(old_duration, free.duration)
            trim_clip_right(project, clip.id, clip.timeline_start + reachable)
        _fit_animation_to_duration(clip)
        if clip.duration < MIN_CLIP_SECONDS:
            raise ValueError(
                "Cette vitesse épuiserait la source presque tout de suite : le clip serait vide "
                f"(durée {clip.duration:.3f} s)."
            )
    except Exception:
        restore_time_state(clip, saved)
        raise


def speed_points(clip: Clip) -> list[Keyframe]:
    """Points de vitesse du clip, triés par temps."""
    return sorted(
        (k for k in clip.animation if getattr(k, "property_name", "") == SPEED_PROPERTY), key=lambda k: k.time_seconds
    )


def speed_ref(clip: Clip, keyframe: Keyframe) -> KeyframeRef:
    return KeyframeRef(clip.id, SPEED_PROPERTY, keyframe.id)


def _check_value(value: float) -> float:
    number = float(value)
    if number != number or abs(number) > SPEED_LIMIT:
        raise ValueError(f"Une vitesse doit être entre {-SPEED_LIMIT:g}x et {SPEED_LIMIT:g}x (reçu : {value}).")
    return number


def add_speed_point(
    project: Project,
    clip_id: str,
    local_time: float,
    value: float | None = None,
    *,
    interpolation: InterpolationType | str | None = None,
    mode: RippleMode = RippleMode.SOURCE,
) -> Keyframe:
    """Ajoute un point de vitesse à ``local_time`` (temps local du clip).

    Sans ``value`` : la vitesse **actuelle** à cet instant est conservée (le point est inséré sans changer le mapping) ;
    c'est ainsi qu'on amorce une rampe. Une propriété jusque-là constante devient animée.
    """
    clip = _editable_clip(project, clip_id)
    with _transaction(project, clip, mode):
        if value is not None and not speed_points(clip) and keyframes.normalize_time(local_time) > 0.0:
            # Première courbe, posée avec une valeur : la partie qui précède garde la vitesse actuelle. Sans ce point de
            # référence, la valeur avant le premier point étant tenue, tout le clip passerait à cette vitesse dès 0 s. C'est
            # un palier (hold) : le changement est brutal à ``local_time`` ; l'utilisateur l'adoucit (linéaire, ease,
            # Bézier) en changeant l'interpolation, ou en posant un point d'amorce de rampe.
            keyframes.add_keyframe(project, clip_id, SPEED_PROPERTY, 0.0, interpolation=InterpolationType.HOLD)
        point = keyframes.add_keyframe(
            project, clip_id, SPEED_PROPERTY, local_time, None if value is None else _check_value(value),
            interpolation=interpolation,
        )
    return point


def set_speed_point_value(
    project: Project, clip_id: str, keyframe_id: str, value: float, *, mode: RippleMode = RippleMode.SOURCE
) -> None:
    """Change la vitesse d'un point existant."""
    clip = _editable_clip(project, clip_id)
    ref = KeyframeRef(clip_id, SPEED_PROPERTY, keyframe_id)
    with _transaction(project, clip, mode):
        keyframes.set_keyframe_values(project, {ref: _check_value(value)})


def move_speed_point(
    project: Project,
    clip_id: str,
    keyframe_id: str,
    local_time: float,
    *,
    fps: float | None = None,
    mode: RippleMode = RippleMode.SOURCE,
) -> float:
    """Déplace un point de vitesse dans le temps ; retourne son nouveau temps (borné au clip)."""
    clip = _editable_clip(project, clip_id)
    ref = KeyframeRef(clip_id, SPEED_PROPERTY, keyframe_id)
    current = keyframes.keyframe_by_id(clip, SPEED_PROPERTY, keyframe_id)
    if current is None:
        raise KeyError(f"Point de vitesse introuvable : {keyframe_id!r}.")
    with _transaction(project, clip, mode):
        moved = keyframes.move_keyframes(project, [ref], local_time - current.time_seconds, fps=fps)
    return moved.get(ref, current.time_seconds)


def remove_speed_point(
    project: Project, clip_id: str, keyframe_id: str, *, mode: RippleMode = RippleMode.SOURCE
) -> None:
    """Supprime un point de vitesse ; le dernier retiré laisse la vitesse constante qu'il affichait."""
    clip = _editable_clip(project, clip_id)
    ref = KeyframeRef(clip_id, SPEED_PROPERTY, keyframe_id)
    if keyframes.keyframe_by_id(clip, SPEED_PROPERTY, keyframe_id) is None:
        raise KeyError(f"Point de vitesse introuvable : {keyframe_id!r}.")
    with _transaction(project, clip, mode):
        keyframes.remove_keyframes(project, [ref])
        if not speed_points(clip):
            # Plus de courbe : la fenêtre redevient la portion de média réellement parcourue.
            _restore_extent(clip)


def _by_clip(project: Project, refs: Sequence[KeyframeRef]) -> dict[str, list[KeyframeRef]]:
    """Regroupe des références par clip, après avoir vérifié que ce sont bien des points de vitesse de clips modifiables."""
    grouped: dict[str, list[KeyframeRef]] = {}
    for ref in refs:
        if ref.property_id != SPEED_PROPERTY:
            raise ValueError(f"Ce n'est pas un point de vitesse : {ref.property_id!r}.")
        _editable_clip(project, ref.clip_id)
        grouped.setdefault(ref.clip_id, []).append(ref)
    return grouped


def move_speed_points(
    project: Project, refs: Sequence[KeyframeRef], delta_seconds: float, *, fps: float | None = None,
    mode: RippleMode = RippleMode.SOURCE,
) -> dict[KeyframeRef, float]:
    """Déplace des points de vitesse (d'un ou plusieurs clips) du même décalage, d'un bloc ; retourne leurs nouveaux temps.

    Une seule transaction pour tous les clips : si l'un est refusé (durée dégénérée…), **aucun** n'est modifié.
    """
    grouped = _by_clip(project, refs)
    result: dict[KeyframeRef, float] = {}
    with ExitStack() as stack:
        for clip_id, group in grouped.items():
            stack.enter_context(_transaction(project, project_clip(project, clip_id), mode))
        for group in grouped.values():
            result.update(keyframes.move_keyframes(project, group, delta_seconds, fps=fps))
    return result


def edit_speed_points(
    project: Project,
    refs: Sequence[KeyframeRef],
    *,
    delta_seconds: float = 0.0,
    values: Mapping[KeyframeRef, float] | None = None,
    fps: float | None = None,
    mode: RippleMode = RippleMode.SOURCE,
    origin: Mapping[str, TimeState] | None = None,
) -> dict[KeyframeRef, float]:
    """Déplace et/ou change la valeur de points de vitesse, **dans une seule transaction** par clip ; retourne leurs temps.

    C'est ce qu'appelle le Graph Editor : le déplacement et les valeurs d'un même geste sont validés ensemble (l'état
    intermédiaire d'un déplacement seul pourrait être refusé alors que le résultat final ne l'est pas).

    ``origin`` (identifiant de clip → :class:`TimeState`) rend un glissement **idempotent** : chaque clip est d'abord remis dans son
    état du début du geste, puis le déplacement total lui est appliqué. Sans cela, un clip raccourci en cours de geste perdrait
    pour de bon les keyframes découpés d'après sa fin, même si le geste le rallonge ensuite.
    """
    grouped = _by_clip(project, refs)
    checked = {ref: _check_value(value) for ref, value in (values or {}).items()}      # refuse avant de toucher à quoi que ce soit
    for clip_id in grouped:
        state = (origin or {}).get(clip_id)
        if state is not None:
            restore_time_state(project_clip(project, clip_id), state)
    result: dict[KeyframeRef, float] = {}
    with ExitStack() as stack:
        for clip_id in grouped:
            stack.enter_context(_transaction(project, project_clip(project, clip_id), mode))
        for group in grouped.values():
            if abs(delta_seconds) > 1e-12:
                result.update(keyframes.move_keyframes(project, group, delta_seconds, fps=fps))
        if checked:
            keyframes.set_keyframe_values(project, checked)
    return result


def set_speed_tangents(
    project: Project, refs: Sequence[KeyframeRef], *, ripple: RippleMode = RippleMode.SOURCE,
    origin: Mapping[str, TimeState] | None = None, **tangents: Any,
) -> None:
    """Fixe les tangentes Bézier de points de vitesse (``in_slope``, ``out_slope``, ``mode``, ``auto``) ; transactionnel.

    Une tangente change la courbe, donc l'intégrale de la vitesse, donc la durée du clip : elle passe par la même transaction
    que tout point de vitesse (durée minimale, ripple, keyframes d'après la fin). ``origin`` : voir :func:`edit_speed_points`.
    """
    grouped = _by_clip(project, refs)
    for clip_id in grouped:
        state = (origin or {}).get(clip_id)
        if state is not None:
            restore_time_state(project_clip(project, clip_id), state)
    with ExitStack() as stack:
        for clip_id in grouped:
            stack.enter_context(_transaction(project, project_clip(project, clip_id), ripple))
        for group in grouped.values():
            for ref in group:
                keyframes.set_tangents(project, ref, **tangents)


def remove_speed_points(project: Project, refs: Sequence[KeyframeRef], *, mode: RippleMode = RippleMode.SOURCE) -> int:
    """Supprime des points de vitesse ; un clip qui n'en garde aucun reprend la vitesse constante qu'il montrait.

    Une seule transaction pour tous les clips. Retourne le nombre de points supprimés.
    """
    grouped = _by_clip(project, refs)
    removed = 0
    with ExitStack() as stack:
        for clip_id in grouped:
            stack.enter_context(_transaction(project, project_clip(project, clip_id), mode))
        for clip_id, group in grouped.items():
            removed += keyframes.remove_keyframes(project, group)
            clip = project_clip(project, clip_id)
            if not speed_points(clip):
                _restore_extent(clip)
    return removed


def project_clip(project: Project, clip_id: str) -> Clip:
    track, index = _find_track_for_clip(project, clip_id)
    return track.clips[index]


def set_speed_point_interpolation(
    project: Project, clip_id: str, keyframe_ids: list[str], interpolation: InterpolationType | str,
    *, mode: RippleMode = RippleMode.SOURCE,
) -> int:
    """Change l'interpolation (palier, linéaire, ease, Bézier) du segment qui part de chaque point."""
    clip = _editable_clip(project, clip_id)
    refs = [KeyframeRef(clip_id, SPEED_PROPERTY, kid) for kid in keyframe_ids]
    with _transaction(project, clip, mode):
        return keyframes.set_interpolation(project, refs, interpolation)


def insert_hold(
    project: Project,
    clip_id: str,
    local_time: float,
    duration: float = HOLD_SECONDS,
    *,
    fps: float | None = None,
    mode: RippleMode = RippleMode.SOURCE,
) -> Keyframe:
    """Arrêt sur image **dans la courbe de vitesse** : à ``local_time`` l'image affichée reste ``duration`` s, puis la lecture
    reprend à la vitesse qu'elle avait. Le clip s'allonge de ``duration`` (le reste de la courbe est décalé, sans être déformé).

    Le palier est un vrai palier de vitesse (``0 %`` en interpolation « palier ») : il se modifie comme n'importe quel point
    (durée, vitesse de reprise, interpolation) dans le Graph Editor. L'image figée est celle que montre la tête de lecture :
    la vitesse tombe à zéro **à** ``local_time``, un tick après une dernière image à la vitesse d'origine. Un segment non
    linéaire qui précède est ré-adouci jusqu'au palier (il garde ses valeurs aux extrémités). Retourne le point d'arrêt.
    """
    clip = _editable_clip(project, clip_id)
    if not duration > 0.0:
        raise ValueError("La durée d'un arrêt sur image doit être positive.")
    tick = 1.0 / (float(fps) if fps and fps > 0 else DEFAULT_RATE)
    moment = normalize_time(keyframes.snap_to_frame(clip, local_time, fps) if fps else local_time)
    if moment < 0.0 or moment > clip.duration + TIME_EPSILON:
        raise ValueError(f"Temps hors du clip : {local_time} (durée {clip.duration}).")
    curve = SPEED_TARGET.curve(clip)
    resume = float(SPEED_TARGET.value_at(clip, moment))
    segment = curve.segment_index(moment)
    template = curve.keyframes[segment].interpolation if segment is not None else InterpolationType.HOLD
    before = float(SPEED_TARGET.value_at(clip, moment - tick)) if curve and moment - tick >= 0.0 else None
    with _transaction(project, clip, mode):
        clip.animation = [
            replace(k, time_seconds=normalize_time(k.time_seconds + duration), id=k.id)
            if getattr(k, "property_name", "") == SPEED_PROPERTY and k.time_seconds >= moment - TIME_EPSILON else k
            for k in clip.animation
        ]
        added = []
        if not curve and moment > 0.0:
            added.append(SPEED_TARGET.make_keyframe(SPEED_PROPERTY, 0.0, resume, InterpolationType.HOLD))
        if curve and before is not None:
            added.append(SPEED_TARGET.make_keyframe(SPEED_PROPERTY, moment - tick, before, InterpolationType.HOLD))
        stop = SPEED_TARGET.make_keyframe(SPEED_PROPERTY, moment, 0.0, InterpolationType.HOLD)
        added += [stop, SPEED_TARGET.make_keyframe(SPEED_PROPERTY, moment + duration, resume, template)]
        updated = AnimationCurve(SPEED_TARGET.get_keyframes(clip), SPEED_TARGET.spec.kind)
        for point in added:
            updated = updated.with_keyframe(point)
        SPEED_TARGET.set_keyframes(clip, list(updated.keyframes))
        if clip.time_remapping.duration is not None:
            # Un clip coupé garde une durée imposée : elle grandit de la durée du palier, sinon la fin serait rognée.
            clip.time_remapping = replace(clip.time_remapping, duration=clip.time_remapping.duration + duration)
    return next(k for k in speed_points(clip) if abs(k.time_seconds - moment) < TIME_EPSILON * 2)


def clear_speed_curve(project: Project, clip_id: str) -> None:
    """Supprime la courbe de vitesse : vitesse constante (la statique), portion de média conservée."""
    clip = _editable_clip(project, clip_id)
    if not speed_points(clip):
        return
    with _transaction(project, clip, RippleMode.SOURCE):
        _restore_extent(clip)
        _clear_speed_keyframes(clip)


# ---------------------------------------------------------------------------
# Choix qui ne changent pas la durée : interpolation des images, audio
# ---------------------------------------------------------------------------


def ensure_interpolation_possible(clip: Clip, interpolation: TimeInterpolation) -> None:
    """Refuse un mode d'images intermédiaires que ce clip ne saurait produire (jamais accepté pour échouer à l'export)."""
    if clip.is_nested and interpolation is not TimeInterpolation.SAMPLING:
        raise ValueError(
            "Le mélange d'images et le flux optique ne s'appliquent pas à une séquence imbriquée (son image n'est pas un "
            "fichier) : ouvrez-la et réglez ses clips, ou exportez-la d'abord."
        )


def set_clip_interpolation(
    project: Project, clip_id: str, interpolation: TimeInterpolation | str, quality: FlowQuality | str | None = None
) -> Clip:
    """Mode de production des images intermédiaires (échantillonnage, mélange, flux optique) et qualité du flux."""
    track, index = _find_track_for_clip(project, clip_id)
    _ensure_track_editable(project, track)
    clip = track.clips[index]
    if clip.time_remapping.freeze_mode == FreezeFrameMode.FREEZE:
        raise ValueError("Un arrêt sur image n'a pas d'images intermédiaires : aucun calcul n'est nécessaire.")
    mode = TimeInterpolation(interpolation)
    ensure_interpolation_possible(clip, mode)
    remapping = replace(clip.time_remapping, interpolation=mode)
    if quality is not None:
        remapping = replace(remapping, flow_quality=FlowQuality(quality))
    clip.time_remapping = remapping
    return clip


def set_clip_preserve_pitch(project: Project, clip_id: str, enabled: bool) -> Clip:
    """L'audio garde sa hauteur quand la vitesse change (sinon : effet bande, la hauteur suit la vitesse)."""
    track, index = _find_track_for_clip(project, clip_id)
    _ensure_track_editable(project, track)
    clip = track.clips[index]
    clip.time_remapping = replace(clip.time_remapping, preserve_pitch=bool(enabled))
    return clip


def set_clip_remap_audio(project: Project, clip_id: str, enabled: bool) -> Clip:
    """Remappe la vidéo **et** l'audio (défaut) ou la vidéo seule (l'audio garde son temps : montages musicaux)."""
    track, index = _find_track_for_clip(project, clip_id)
    _ensure_track_editable(project, track)
    clip = track.clips[index]
    clip.time_remapping = replace(clip.time_remapping, remap_audio=bool(enabled))
    return clip


__all__ = [
    "HOLD_SECONDS",
    "MIN_CLIP_SECONDS",
    "RippleMode",
    "TimeState",
    "add_speed_point",
    "capture_time_state",
    "clear_speed_curve",
    "edit_speed_points",
    "ensure_interpolation_possible",
    "insert_hold",
    "move_speed_point",
    "move_speed_points",
    "project_clip",
    "remove_speed_point",
    "remove_speed_points",
    "restore_time_state",
    "set_clip_interpolation",
    "set_clip_preserve_pitch",
    "set_clip_remap_audio",
    "set_speed_point_interpolation",
    "set_speed_point_value",
    "set_speed_tangents",
    "speed_points",
    "speed_ref",
]
