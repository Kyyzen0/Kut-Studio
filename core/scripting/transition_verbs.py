"""Transitions, suite : ``update_transition`` et ``apply_transition_preset`` (lot 8 de l'extension 0.8.0).

Ils complètent ``add_transition`` / ``remove_transition`` (``core/scripting/verbs.py``) et appellent les fonctions du modèle de l'inspecteur de
transition (``ui/main_window_mixins/timeline_editing.py`` : :func:`core.transitions.update_transition`) et de la bibliothèque
(``ui/main_window_mixins/presets.py``, qui pose le type du préréglage avec la durée choisie : :func:`core.transition_presets.apply_transition_preset`).

- **verrous** : les DEUX clips de la jonction doivent être des clips vidéo éditables (piste et calque déverrouillés), comme l'interface
  (``_transition_edit_refused``) ; le modèle refuse déjà le verrou, le verbe le dit avant de rien lire d'autre ;
- **la durée est celle du chevauchement** : changer la durée de la transition recale le clip entrant (``timeline_start``), et le modèle ne regarde
  pas ses voisins. Un script reçoit refusé tout recalage qui ferait recouvrir le clip entrant par un autre clip de la piste (un tiers qu'il ne
  voulait pas toucher), ou qui défairait la jonction d'une autre transition ;
- **bornes** : au moins :data:`core.transitions.MIN_TRANSITION_SECONDS`, au plus la moitié du plus court des deux clips ; hors bornes : refusé ;
- **préréglage** : un identifiant de la bibliothèque **intégrée** (énumération), jamais un préréglage de l'utilisateur (configuration hors projet) ;
  sans `duration`, la durée par défaut du préréglage.

Hors périmètre (défaut relevé) : ``add_transition`` (verbe du lot 0) n'a pas ce contrôle de recalage.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..project_model import Clip, Project
from ..transition_presets import apply_transition_preset, builtin_transition_presets
from ..transitions import MIN_TRANSITION_SECONDS, Transition, TransitionType, update_transition
from .verbs import MAX_TIME, Kind, Operation, Param, Verb, _editable_clip, _owner_track, _pick

_TOLERANCE = 1e-6
_PRESET_IDS = tuple(item.id for item in builtin_transition_presets())
_BUILTIN = {item.id: item for item in builtin_transition_presets()}


def _junction_clips(project: Project, from_selector: str, to_selector: str) -> tuple[Clip, Clip]:
    """Les deux clips d'une jonction, vidéo et éditables (verrous de piste et de calque)."""
    first = _editable_clip(project, from_selector, track_types=("video",))
    second = _editable_clip(project, to_selector, track_types=("video",))
    track = _owner_track(project, first)
    clips = {clip.id: clip for clip in track.clips}
    if second not in clips:
        raise ValueError("Les deux clips d'une transition doivent être sur la même piste.")
    return clips[first], clips[second]


def _require_room(project: Project, from_clip: Clip, to_clip: Clip, duration: float, verb: str) -> None:
    """Le clip entrant, recalé pour que le chevauchement vaille ``duration``, ne recouvre aucun autre clip de la piste ; et ne défait pas une autre jonction."""
    start = from_clip.timeline_start + from_clip.duration - duration
    if abs(start - to_clip.timeline_start) <= _TOLERANCE:
        return                                                            # le clip entrant ne bouge pas
    end = start + to_clip.duration
    track = _owner_track(project, to_clip.id)
    for other in track.clips:
        if other.id in (from_clip.id, to_clip.id):
            continue
        if other.timeline_start < end - _TOLERANCE and other.timeline_start + other.duration > start + _TOLERANCE:
            raise ValueError(
                f"{verb} : recaler le clip '{to_clip.id}' à {start:g} s le ferait recouvrir le clip '{other.id}' ; "
                "la transition n'a pas la place (le modèle ne déplace pas les autres clips)."
            )
    for current in project.transitions:
        if to_clip.id == current.from_clip_id:
            raise ValueError(f"{verb} : le clip '{to_clip.id}' a lui-même une transition sortante ; le recaler la défairait.")


def _update_transition(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        current: Transition = _pick(project.transitions, a["transition"], "Transition", names=False)
        if "type" not in a and "duration" not in a:
            raise ValueError("update_transition : donnez `type` et/ou `duration`.")
        from_clip, to_clip = _junction_clips(project, current.from_clip_id, current.to_clip_id)
        duration = current.duration if "duration" not in a else float(a["duration"])
        maximum = min(from_clip.duration, to_clip.duration) / 2.0
        if duration > maximum + _TOLERANCE:
            raise ValueError(f"update_transition : {duration:g} s dépasse la moitié du plus court des deux clips ({maximum:g} s).")
        _require_room(project, from_clip, to_clip, duration, "update_transition")
        return update_transition(
            project, str(current.id), transition_type=TransitionType(a["type"]) if "type" in a else None,
            duration=float(a["duration"]) if "duration" in a else None,
        )

    return operation


def _apply_transition_preset(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        preset = _BUILTIN[str(a["preset"])]
        first = _editable_clip(project, a["from_clip"], track_types=("video",))
        second = _editable_clip(project, a["to_clip"], track_types=("video",))
        duration = float(preset.default_duration) if "duration" not in a else float(a["duration"])
        track = _owner_track(project, first)
        by_id = {clip.id: clip for clip in track.clips}
        if second in by_id:
            from_clip, to_clip = by_id[first], by_id[second]
            overlap = from_clip.timeline_start + from_clip.duration - to_clip.timeline_start
            # Le modèle refuse lui-même un trou, un chevauchement qui n'est pas la durée, ou une durée trop grande : ses messages valent mieux.
            if abs(overlap) <= _TOLERANCE or abs(overlap - duration) <= _TOLERANCE:
                _require_room(project, from_clip, to_clip, duration, "apply_transition_preset")
        return apply_transition_preset(project, first, second, preset, duration if "duration" in a else None, transition_id=a.get("id"))

    return operation


TRANSITION_VERBS: tuple[Verb, ...] = (
    Verb("update_transition", "Change le type et/ou la durée d'une transition (la durée recale le clip entrant) ; refusé si un clip est verrouillé, si la durée dépasse la moitié du plus court clip, ou si le recalage recouvrirait un autre clip.",
         (Param("transition", Kind.SELECTOR, "La transition, par identifiant.", of="transition"),
          Param("type", Kind.ENUM, "Le genre de transition.", required=False, choices=tuple(item.value for item in TransitionType)),
          Param("duration", Kind.NUMBER, "Durée en secondes (au plus la moitié du plus court des deux clips).", required=False,
                minimum=MIN_TRANSITION_SECONDS, maximum=MAX_TIME)),
         _update_transition, group="edit"),
    Verb("apply_transition_preset", "Pose un préréglage de transition INTÉGRÉ entre deux clips vidéo adjacents d'une même piste (son type, sa durée par défaut sauf `duration`).",
         (Param("preset", Kind.ENUM, "Le préréglage intégré de la bibliothèque de transitions.", choices=_PRESET_IDS),
          Param("from_clip", Kind.SELECTOR, "Le clip sortant, par identifiant.", of="clip"),
          Param("to_clip", Kind.SELECTOR, "Le clip entrant, par identifiant.", of="clip"),
          Param("duration", Kind.NUMBER, "Durée en secondes (absente : celle du préréglage ; au plus la moitié du plus court des deux clips).", required=False,
                minimum=MIN_TRANSITION_SECONDS, maximum=MAX_TIME)),
         _apply_transition_preset, creates="transition", group="edit"),
)

__all__ = ["TRANSITION_VERBS"]
