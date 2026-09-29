"""Transitions persistantes entre deux clips vidéo d'une même piste."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .project_model import Clip, Project, Track


class TransitionType(str, Enum):
    """Catalogue des types de transitions vidéo supportés (tâche 26).

    Les valeurs historiques (``crossfade``, ``fade_black``, ``wipe_left``,
    ``wipe_right``) sont conservées telles quelles pour garantir la
    rétro‑compatibilité avec les fichiers ``.kut`` et les snapshots
    d'historique existants : tout projet enregistré avant la tâche 26
    se charge sans perte. Les 14 nouveaux types complètent le
    catalogue à 18 transitions natives.

    Le mapping vers les filtres ``xfade`` FFmpeg correspondants est
    défini dans :func:`core.export_engine._ffmpeg_transition_name`.
    """

    CROSSFADE = "crossfade"
    FADE_BLACK = "fade_black"
    WIPE_LEFT = "wipe_left"
    WIPE_RIGHT = "wipe_right"
    # --- Tâche 26 : catalogue étendu ---
    # Balayages
    WIPE_UP = "wipe_up"
    WIPE_DOWN = "wipe_down"
    SLIDE_UP = "slide_up"
    SLIDE_DOWN = "slide_down"
    SLIDE_LEFT = "slide_left"
    SLIDE_RIGHT = "slide_right"
    # Ouvertures / fermetures
    CIRCLE_OPEN = "circle_open"
    CIRCLE_CLOSE = "circle_close"
    # Dissolutions
    DISSOLVE = "dissolve"
    PIXELIZE = "pixelize"
    # Radial
    RADIAL = "radial"
    # Fondus au blanc
    FADE_WHITE = "fade_white"
    # Glissements fluides
    SMOOTH_LEFT = "smooth_left"
    SMOOTH_RIGHT = "smooth_right"


# Catégorie large (utilisée par les filtres de la bibliothèque et par
# ``_infer_category``). On regroupe les 18 types en cinq familles pour
# faciliter la navigation : ``fade`` (fondus), ``wipe`` (balayages et
# glissements), ``shape`` (cercle, radial, pixelisation), ``dissolve``
# (dissolutions), ``smooth`` (glissements fluides). Cette taxonomie est
# stable : elle ne sert qu'à organiser la bibliothèque, pas à filtrer
# l'export FFmpeg.
TRANSITION_FAMILIES: dict[str, tuple[TransitionType, ...]] = {
    "fade": (
        TransitionType.CROSSFADE,
        TransitionType.FADE_BLACK,
        TransitionType.FADE_WHITE,
    ),
    "wipe": (
        TransitionType.WIPE_LEFT,
        TransitionType.WIPE_RIGHT,
        TransitionType.WIPE_UP,
        TransitionType.WIPE_DOWN,
        TransitionType.SLIDE_LEFT,
        TransitionType.SLIDE_RIGHT,
        TransitionType.SLIDE_UP,
        TransitionType.SLIDE_DOWN,
    ),
    "shape": (
        TransitionType.CIRCLE_OPEN,
        TransitionType.CIRCLE_CLOSE,
        TransitionType.RADIAL,
    ),
    "dissolve": (
        TransitionType.DISSOLVE,
        TransitionType.PIXELIZE,
    ),
    "smooth": (
        TransitionType.SMOOTH_LEFT,
        TransitionType.SMOOTH_RIGHT,
    ),
}


def transition_family(transition_type: TransitionType | str) -> str:
    """Famille d'un type de transition (clé de :data:`TRANSITION_FAMILIES`).

    Les types inconnus retombent sur ``"wipe"`` (choix conservateur,
    cohérent avec la catégorie historique de la tâche 23).
    """
    try:
        ttype = TransitionType(transition_type)
    except ValueError:
        return "wipe"
    for family, members in TRANSITION_FAMILIES.items():
        if ttype in members:
            return family
    return "wipe"


@dataclass(frozen=True)
class Transition:
    id: str
    from_clip_id: str
    to_clip_id: str
    type: TransitionType = TransitionType.CROSSFADE
    duration: float = 0.5

    def __post_init__(self) -> None:
        if not self.id or not self.from_clip_id or not self.to_clip_id:
            raise ValueError("Une transition doit identifier ses deux clips.")
        if self.from_clip_id == self.to_clip_id:
            raise ValueError("Une transition requiert deux clips distincts.")
        if self.duration <= 0.0:
            raise ValueError("La durée d'une transition doit être positive.")


def transition_pairs(project: "Project", clip_id: str) -> list[Transition]:
    return [t for t in project.transitions if clip_id in {t.from_clip_id, t.to_clip_id}]


def add_transition(
    project: "Project",
    from_clip_id: str,
    to_clip_id: str,
    transition_type: TransitionType = TransitionType.CROSSFADE,
    duration: float = 0.5,
) -> Transition:
    from_track, from_clip = _find_video_clip(project, from_clip_id)
    to_track, to_clip = _find_video_clip(project, to_clip_id)
    if from_track.id != to_track.id:
        raise ValueError("Les deux clips d'une transition doivent être sur la même piste.")
    if to_clip.timeline_start < from_clip.timeline_start:
        raise ValueError("Le clip entrant doit suivre le clip sortant.")
    overlap = from_clip.timeline_start + from_clip.duration - to_clip.timeline_start
    if overlap < -1e-6:
        raise ValueError("Les clips doivent être adjacents ou se chevaucher.")
    maximum = min(from_clip.duration, to_clip.duration) / 2.0
    if duration > maximum + 1e-6:
        raise ValueError("La transition dépasse la moitié du clip le plus court.")
    if abs(overlap) <= 1e-6:
        # Une jonction simple devient un vrai recouvrement : c'est la
        # condition nécessaire au fondu vidéo et audio pendant l'export.
        to_clip.timeline_start -= float(duration)
    elif abs(overlap - duration) > 1e-6:
        raise ValueError("Le chevauchement des clips doit correspondre à la durée de transition.")
    for current in project.transitions:
        if current.from_clip_id == from_clip_id and current.to_clip_id == to_clip_id:
            raise ValueError("Une transition existe déjà sur cette jonction.")
        if current.to_clip_id == from_clip_id or current.from_clip_id == to_clip_id:
            raise ValueError("Un clip ne peut pas avoir deux transitions sur la même jonction.")
    transition = Transition(
        id=f"transition-{uuid.uuid4().hex[:12]}",
        from_clip_id=from_clip_id,
        to_clip_id=to_clip_id,
        type=TransitionType(transition_type),
        duration=float(duration),
    )
    project.transitions.append(transition)
    from_clip.set_fade_out(float(duration))
    to_clip.set_fade_in(float(duration))
    return transition


def update_transition(
    project: "Project", transition_id: str, *, transition_type: TransitionType | None = None,
    duration: float | None = None,
) -> Transition:
    current = _find_transition(project, transition_id)
    _track, from_clip = _find_video_clip(project, current.from_clip_id)
    _to_track, to_clip = _find_video_clip(project, current.to_clip_id)
    new_duration = current.duration if duration is None else float(duration)
    if new_duration > min(from_clip.duration, to_clip.duration) / 2.0 + 1e-6:
        raise ValueError("La transition dépasse la moitié du clip le plus court.")
    replacement = Transition(
        id=current.id,
        from_clip_id=current.from_clip_id,
        to_clip_id=current.to_clip_id,
        type=current.type if transition_type is None else TransitionType(transition_type),
        duration=new_duration,
    )
    to_clip.timeline_start = from_clip.timeline_start + from_clip.duration - new_duration
    from_clip.set_fade_out(new_duration)
    to_clip.set_fade_in(new_duration)
    project.transitions[project.transitions.index(current)] = replacement
    return replacement


def remove_transition(project: "Project", transition_id: str) -> Transition:
    current = _find_transition(project, transition_id)
    project.transitions.remove(current)
    # Les fondus audio sont appliqués avec la transition afin que le
    # chevauchement sonne comme le fondu vidéo. Ils ne doivent pas
    # survivre lorsque la dernière transition concernée est supprimée.
    try:
        _track, outgoing = _find_video_clip(project, current.from_clip_id)
        _track, incoming = _find_video_clip(project, current.to_clip_id)
    except KeyError:
        return current
    if not any(item.from_clip_id == current.from_clip_id for item in project.transitions):
        outgoing.set_fade_out(0.0)
    if not any(item.to_clip_id == current.to_clip_id for item in project.transitions):
        incoming.set_fade_in(0.0)
    return current


def remove_transitions_for_clips(project: "Project", clip_ids: set[str]) -> list[Transition]:
    removed = [t for t in project.transitions if t.from_clip_id in clip_ids or t.to_clip_id in clip_ids]
    project.transitions[:] = [t for t in project.transitions if t not in removed]
    return removed


def _find_transition(project: "Project", transition_id: str) -> Transition:
    for transition in project.transitions:
        if transition.id == transition_id:
            return transition
    raise KeyError(f"Transition '{transition_id}' introuvable.")


def _find_video_clip(project: "Project", clip_id: str) -> tuple["Track", "Clip"]:
    for track in project.tracks:
        for clip in track.clips:
            if clip.id == clip_id:
                if track.type != "video":
                    raise ValueError("Une transition ne peut concerner que des clips vidéo.")
                return track, clip
    raise KeyError(f"Clip '{clip_id}' introuvable.")
