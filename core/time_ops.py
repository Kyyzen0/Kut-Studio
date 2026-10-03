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

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from enum import Enum

from . import keyframe_editing as keyframes
from .animation import InterpolationType, Keyframe
from .keyframe_editing import KeyframeRef
from .project_model import Clip, Project
from .time_editing import free_map
from .time_map import SPEED_LIMIT, SPEED_PROPERTY
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


@contextmanager
def _transaction(project: Project, clip: Clip, mode: RippleMode) -> Iterator[None]:
    """Applique une édition de vitesse ; la restaure si le clip obtenu est refusé.

    Après l'édition : politique de ripple, découpe des keyframes d'après la fin, puis contrôle de la durée.
    """
    saved = (
        list(clip.animation), clip.time_remapping, clip.source_in, clip.source_out,
        list(clip.transform_keyframes), clip.timeline_start,
    )
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
        (clip.animation, clip.time_remapping, clip.source_in, clip.source_out,
         clip.transform_keyframes, clip.timeline_start) = saved
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


def set_speed_point_interpolation(
    project: Project, clip_id: str, keyframe_ids: list[str], interpolation: InterpolationType | str,
    *, mode: RippleMode = RippleMode.SOURCE,
) -> int:
    """Change l'interpolation (palier, linéaire, ease, Bézier) du segment qui part de chaque point."""
    clip = _editable_clip(project, clip_id)
    refs = [KeyframeRef(clip_id, SPEED_PROPERTY, kid) for kid in keyframe_ids]
    with _transaction(project, clip, mode):
        return keyframes.set_interpolation(project, refs, interpolation)


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


def set_clip_interpolation(
    project: Project, clip_id: str, interpolation: TimeInterpolation | str, quality: FlowQuality | str | None = None
) -> Clip:
    """Mode de production des images intermédiaires (échantillonnage, mélange, flux optique) et qualité du flux."""
    track, index = _find_track_for_clip(project, clip_id)
    _ensure_track_editable(project, track)
    clip = track.clips[index]
    if clip.time_remapping.freeze_mode == FreezeFrameMode.FREEZE:
        raise ValueError("Un arrêt sur image n'a pas d'images intermédiaires : aucun calcul n'est nécessaire.")
    remapping = replace(clip.time_remapping, interpolation=TimeInterpolation(interpolation))
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
    "MIN_CLIP_SECONDS",
    "RippleMode",
    "add_speed_point",
    "clear_speed_curve",
    "move_speed_point",
    "remove_speed_point",
    "set_clip_interpolation",
    "set_clip_preserve_pitch",
    "set_clip_remap_audio",
    "set_speed_point_interpolation",
    "set_speed_point_value",
    "speed_points",
    "speed_ref",
]
