"""Vitesse et remappage du temps d'un clip : vitesse, sens, arrêt sur image, courbe de vitesse, images intermédiaires, audio (lot 2 de l'extension 0.8.0).

Aucune sémantique propre : chaque verbe appelle la fonction publique que l'interface appelle (inspecteur de vitesse :
``ui/main_window_mixins/transform_and_clip_effects.py`` ; menu « Vitesse », Graph Editor : :mod:`core.time_commands`, :mod:`core.time_ops`).

Règles communes (celles des autres verbes) :

- **verrous** : piste verrouillée et calque verrouillé sont refusés (:func:`~core.scripting.verbs._editable_clip`) ; seuls les clips des
  pistes *vidéo* et *audio* ont une vitesse (graphiques et sous-titres sont refusés, comme le registre d'animation) ;
- **rien n'est ramené en silence** : le modèle bornerait une vitesse hors de 0,05x à 10x (``clamp_speed``), un instant de point hors du clip,
  un déplacement de points qui sortirait du clip, et alignerait un instant sur une image (``insert_hold``) : un script reçoit tout cela refusé ;
- **un point de vitesse se désigne par son instant** (``at``, secondes relatives au clip), comme une image clé d'animation
  (:mod:`core.scripting.animation_verbs`) : les identifiants d'image clé sont générés par le modèle et n'ont pas à être cités ;
- **ripple** (``ripple``, comme le réglage de l'interface) : ``source`` (défaut, celui de l'interface) garde la portion de média et la durée
  du clip change ; ``timeline`` garde la durée sur la timeline.

Comme l'interface, aucune de ces opérations ne repousse les clips voisins : un clip ralenti peut en recouvrir un autre.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..animation import TIME_EPSILON, InterpolationType, normalize_time
from ..keyframe_editing import KeyframeRef, find_clip, snap_to_frame
from ..project_model import Project
from ..time_map import SPEED_LIMIT, SPEED_PROPERTY
from ..time_ops import (
    RippleMode, add_speed_point, clear_speed_curve, insert_hold, move_speed_points, remove_speed_point,
    set_clip_interpolation, set_clip_preserve_pitch, set_clip_remap_audio, set_speed_point_value,
)
from ..time_remapping import MAX_SPEED, MIN_SPEED, FlowQuality, FreezeFrameMode, TimeInterpolation
from ..time_targets import SPEED_TARGET
from ..timeline_operations import (
    remove_clip_freeze_frame, reset_clip_time_remapping, set_clip_freeze_duration, set_clip_freeze_frame, set_clip_reverse, set_clip_speed,
)
from .resource_limits import require_keyframes_after_project_edit
from .verbs import MAX_TIME, Kind, Operation, Param, Verb, _CLIP, _editable_clip

_TRACKS = ("video", "audio")
_MAX_POINTS = 64
_FREEZE_MIN = 0.01          # les bornes du champ « durée de l'arrêt sur image » de l'inspecteur
_FREEZE_MAX = 3600.0
_TOLERANCE = 1e-9

_AT = Param("at", Kind.NUMBER, "Instant du point de vitesse, en secondes depuis le début du clip.", minimum=0.0, maximum=MAX_TIME)
_RIPPLE = Param("ripple", Kind.ENUM, "Ce que garde une édition qui change la durée : « source » (la portion de média, défaut) ou « timeline » "
                "(la durée sur la timeline).", required=False, choices=tuple(item.value for item in RippleMode))
_VALUE = Param("value", Kind.NUMBER, "Vitesse (1 = normale, négatif = marche arrière, 0 = arrêt) ; au plus 10x dans chaque sens.",
               minimum=-SPEED_LIMIT, maximum=SPEED_LIMIT)


def _time_clip(project: Project, selector: str) -> str:
    return _editable_clip(project, selector, track_types=_TRACKS)


def _live_clip(project: Project, selector: str, verb: str) -> str:
    """Un clip éditable qui n'est pas figé : l'interface désactive ces réglages pour un arrêt sur image, le modèle les stockerait sans effet."""
    clip_id = _time_clip(project, selector)
    if find_clip(project, clip_id).time_remapping.freeze_mode == FreezeFrameMode.FREEZE:
        raise ValueError(f"{verb} : le clip '{clip_id}' est figé ; retire l'image figée d'abord (remove_clip_freeze_frame).")
    return clip_id


def _mode(a: Mapping[str, Any]) -> RippleMode:
    return RippleMode(a.get("ripple", RippleMode.SOURCE.value))


def _speed_point(project: Project, clip_id: str, at: float) -> str:
    """L'identifiant du point de vitesse du clip à l'instant ``at`` (à la microseconde, comme tout keyframe)."""
    point = SPEED_TARGET.curve(find_clip(project, clip_id)).keyframe_at(normalize_time(at))
    if point is None:
        raise KeyError(f"Aucun point de vitesse à {at:g} s sur ce clip.")
    return str(point.id)


# --- Vitesse constante, sens, arrêt sur image ---------------------------------------------------------------------------


def _set_clip_speed(a: Mapping[str, Any]) -> Operation:
    return lambda p: set_clip_speed(p, _live_clip(p, a["clip"], "set_clip_speed"), float(a["speed"]))


def _set_clip_reverse(a: Mapping[str, Any]) -> Operation:
    return lambda p: set_clip_reverse(p, _live_clip(p, a["clip"], "set_clip_reverse"), bool(a["reverse"]))


def _set_clip_freeze_frame(a: Mapping[str, Any]) -> Operation:
    return lambda p: set_clip_freeze_frame(p, _time_clip(p, a["clip"]), float(a["source_time"]), float(a["duration"]))


def _frozen_clip(project: Project, selector: str, verb: str) -> str:
    clip_id = _time_clip(project, selector)
    if find_clip(project, clip_id).time_remapping.freeze_mode != FreezeFrameMode.FREEZE:
        # Sans cela, le modèle retirerait « l'arrêt sur image » d'un clip qui n'en a pas et remettrait vitesse, sens et images intermédiaires à zéro.
        raise ValueError(f"{verb} : le clip '{clip_id}' n'est pas un arrêt sur image.")
    return clip_id


def _remove_clip_freeze_frame(a: Mapping[str, Any]) -> Operation:
    return lambda p: remove_clip_freeze_frame(p, _frozen_clip(p, a["clip"], "remove_clip_freeze_frame"))


def _set_clip_freeze_duration(a: Mapping[str, Any]) -> Operation:
    return lambda p: set_clip_freeze_duration(p, _frozen_clip(p, a["clip"], "set_clip_freeze_duration"), float(a["duration"]))


def _reset_clip_time_remapping(a: Mapping[str, Any]) -> Operation:
    return lambda p: reset_clip_time_remapping(p, _time_clip(p, a["clip"]))


# --- Courbe de vitesse -------------------------------------------------------------------------------------------------


def _add_speed_point(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        clip_id = _time_clip(project, a["clip"])
        value = None if a.get("value") is None else float(a["value"])

        def add(target: Project) -> Any:
            return add_speed_point(target, clip_id, float(a["at"]), value, interpolation=a.get("interpolation"), mode=_mode(a))

        require_keyframes_after_project_edit(project, find_clip(project, clip_id), add, "add_speed_point")
        return add(project)

    return operation


def _set_speed_point_value(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        clip_id = _time_clip(project, a["clip"])
        return set_speed_point_value(project, clip_id, _speed_point(project, clip_id, float(a["at"])), float(a["value"]), mode=_mode(a))

    return operation


def _remove_speed_point(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        clip_id = _time_clip(project, a["clip"])
        return remove_speed_point(project, clip_id, _speed_point(project, clip_id, float(a["at"])), mode=_mode(a))

    return operation


def _checked_move(project: Project, clip_id: str, moments: list[float], delta: float, verb: str) -> list[str]:
    """Les identifiants des points aux instants ``moments``, après avoir vérifié qu'un déplacement de ``delta`` s les laisse dans le clip et
    sur aucun point qui ne bouge pas. Le modèle borne le groupe entier au clip (un décalage trop grand est réduit) et remplace un point non
    déplacé qu'il rejoint : un script reçoit l'un comme l'autre refusés, jamais un autre déplacement que celui qu'il a écrit."""
    clip = find_clip(project, clip_id)
    ids = [_speed_point(project, clip_id, float(moment)) for moment in moments]
    if len(set(ids)) != len(ids):
        raise ValueError(f"{verb} : deux instants désignent le même point.")
    moved = {str(point.id): point for point in SPEED_TARGET.curve(clip).keyframes}
    duration = float(clip.duration)
    arrivals = []
    for point_id in ids:
        arrival = normalize_time(moved[point_id].time_seconds + delta)
        if arrival < -TIME_EPSILON or arrival > duration + TIME_EPSILON:
            raise ValueError(f"{verb} : le point à {moved[point_id].time_seconds:g} s sortirait du clip (de 0 à {duration:g} s).")
        arrivals.append(arrival)
    for other in moved.values():
        if str(other.id) not in ids and any(abs(other.time_seconds - arrival) <= 2 * TIME_EPSILON for arrival in arrivals):
            raise ValueError(f"{verb} : un point arriverait sur celui de {other.time_seconds:g} s, qu'il remplacerait.")
    return ids


def _move_speed_points(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        clip_id = _time_clip(project, a["clip"])
        delta = float(a["delta"])
        if abs(delta) <= _TOLERANCE:
            raise ValueError("move_speed_points : un décalage nul ne déplace rien.")
        ids = _checked_move(project, clip_id, list(a["points"]), delta, "move_speed_points")
        refs = [KeyframeRef(clip_id, SPEED_PROPERTY, point_id) for point_id in ids]
        return move_speed_points(project, refs, delta, mode=_mode(a))

    return operation


def _clear_speed_curve(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        clip_id = _time_clip(project, a["clip"])
        return clear_speed_curve(project, clip_id)

    return operation


def _require_aligned(project: Project, clip: Any, at: float, verb: str) -> None:
    """L'interface aligne l'instant sur une image de la timeline (et le ramène dans le clip) : un script reçoit un instant non aligné refusé."""
    aligned = snap_to_frame(clip, at, float(project.fps))
    if abs(aligned - normalize_time(at)) > 1e-6:
        raise ValueError(
            f"{verb} : l'instant {at:g} s n'est pas sur une image de la timeline ({project.fps:g} i/s) ou sort du clip ; "
            f"le plus proche est {aligned:g} s."
        )


def _insert_hold(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        clip_id = _time_clip(project, a["clip"])
        clip = find_clip(project, clip_id)
        at = float(a["at"])
        _require_aligned(project, clip, at, "insert_hold")

        def hold(target: Project) -> Any:
            return insert_hold(target, clip_id, at, float(a.get("duration", 1.0)), fps=float(project.fps), mode=_mode(a))

        require_keyframes_after_project_edit(project, clip, hold, "insert_hold")
        return hold(project)

    return operation


# --- Images intermédiaires et audio ------------------------------------------------------------------------------------


def _set_clip_interpolation(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        clip_id = _time_clip(project, a["clip"])
        if "interpolation" not in a and "quality" not in a:
            raise ValueError("set_clip_interpolation : donnez `interpolation` et/ou `quality`.")
        clip = find_clip(project, clip_id)
        mode = a.get("interpolation", clip.time_remapping.interpolation)     # seule la qualité : le mode actuel (la commande « qualité » de l'interface)
        return set_clip_interpolation(project, clip_id, mode, a.get("quality"))

    return operation


def _set_clip_preserve_pitch(a: Mapping[str, Any]) -> Operation:
    return lambda p: set_clip_preserve_pitch(p, _live_clip(p, a["clip"], "set_clip_preserve_pitch"), bool(a["enabled"]))


def _set_clip_remap_audio(a: Mapping[str, Any]) -> Operation:
    return lambda p: set_clip_remap_audio(p, _live_clip(p, a["clip"], "set_clip_remap_audio"), bool(a["enabled"]))


TIME_VERBS: tuple[Verb, ...] = (
    Verb("set_clip_speed", "Vitesse constante d'un clip (la durée change) ; remplace une courbe de vitesse existante.",
         (_CLIP, Param("speed", Kind.NUMBER, "Vitesse, de 0,05 (20 fois plus lent) à 10 ; 1 = normale.", minimum=MIN_SPEED, maximum=MAX_SPEED)),
         _set_clip_speed, group="time"),
    Verb("set_clip_reverse", "Joue un clip à l'envers, ou à l'endroit.", (_CLIP, Param("reverse", Kind.BOOLEAN, "À l'envers ou non.")),
         _set_clip_reverse, group="time"),
    Verb("set_clip_freeze_frame", "Transforme un clip en arrêt sur image (vidéo seulement) ; sa courbe de vitesse est retirée.",
         (_CLIP, Param("source_time", Kind.NUMBER, "Instant du média à figer, en secondes de source, dans la fenêtre actuelle du clip.",
                       minimum=0.0, maximum=MAX_TIME),
          Param("duration", Kind.NUMBER, "Durée de l'arrêt sur image sur la timeline, de 0,01 à 3 600 s.", minimum=_FREEZE_MIN, maximum=_FREEZE_MAX)),
         _set_clip_freeze_frame, group="time"),
    Verb("remove_clip_freeze_frame", "Lève l'arrêt sur image d'un clip : tout son remappage revient à la normale (refusé si le clip n'est pas figé).",
         (_CLIP,), _remove_clip_freeze_frame, group="time"),
    Verb("set_clip_freeze_duration", "Change la durée d'un arrêt sur image (refusé si le clip n'est pas figé).",
         (_CLIP, Param("duration", Kind.NUMBER, "Durée sur la timeline, de 0,01 à 3 600 s.", minimum=_FREEZE_MIN, maximum=_FREEZE_MAX)),
         _set_clip_freeze_duration, group="time"),
    Verb("reset_clip_time_remapping", "Remet à zéro le remappage d'un clip : vitesse normale, sens normal, plus de courbe ni d'arrêt sur image.",
         (_CLIP,), _reset_clip_time_remapping, group="time"),
    Verb("add_speed_point", "Pose un point sur la courbe de vitesse (à `at`, relatif au clip) ; sans `value`, la vitesse actuelle y est conservée.",
         (_CLIP, _AT, Param("value", Kind.NUMBER, _VALUE.doc, required=False, minimum=_VALUE.minimum, maximum=_VALUE.maximum),
          Param("interpolation", Kind.ENUM, "Interpolation vers le point suivant.", required=False,
                choices=tuple(item.value for item in InterpolationType)), _RIPPLE),
         _add_speed_point, group="time"),
    Verb("set_speed_point_value", "Change la vitesse d'un point existant de la courbe.", (_CLIP, _AT, _VALUE, _RIPPLE),
         _set_speed_point_value, group="time"),
    Verb("remove_speed_point", "Retire un point de la courbe de vitesse ; le dernier retiré laisse la vitesse constante qu'il affichait.",
         (_CLIP, _AT, _RIPPLE), _remove_speed_point, group="time"),
    Verb("move_speed_points", "Déplace des points de la courbe de vitesse du même décalage, d'un bloc ; un point qui sortirait du clip ou en recouvrirait un autre est refusé.",
         (_CLIP, Param("points", Kind.LIST, "Les instants des points à déplacer (1 à 64 nombres, relatifs au clip).", minimum=1, maximum=_MAX_POINTS,
                       item=Param("point", Kind.NUMBER, "Instant d'un point.", minimum=0.0, maximum=MAX_TIME)),
          Param("delta", Kind.NUMBER, "Décalage en secondes (non nul ; négatif = plus tôt).", minimum=-MAX_TIME, maximum=MAX_TIME), _RIPPLE),
         _move_speed_points, group="time"),
    Verb("clear_speed_curve", "Supprime la courbe de vitesse : vitesse constante, portion de média conservée.",
         (_CLIP,), _clear_speed_curve, group="time"),
    Verb("insert_hold", "Arrêt sur image dans la courbe de vitesse : l'image reste `duration` s à l'instant `at`, puis la lecture reprend ; le clip s'allonge.",
         (_CLIP, Param("at", Kind.NUMBER, "Instant de l'arrêt, en secondes depuis le début du clip ; il doit tomber sur une image de la timeline.",
                       minimum=0.0, maximum=MAX_TIME),
          Param("duration", Kind.NUMBER, "Durée de l'arrêt en secondes, de 0,01 à 3 600 (défaut : 1).", required=False,
                minimum=_FREEZE_MIN, maximum=_FREEZE_MAX), _RIPPLE),
         _insert_hold, group="time"),
    Verb("set_clip_interpolation", "Choisit comment sont produites les images intermédiaires d'un clip ralenti (échantillonnage, mélange, flux optique) "
                                   "et/ou la qualité du flux optique.",
         (_CLIP, Param("interpolation", Kind.ENUM, "Mode des images intermédiaires (absent : le mode actuel).", required=False,
                       choices=tuple(item.value for item in TimeInterpolation)),
          Param("quality", Kind.ENUM, "Qualité du flux optique.", required=False, choices=tuple(item.value for item in FlowQuality))),
         _set_clip_interpolation, group="time"),
    Verb("set_clip_preserve_pitch", "L'audio d'un clip garde sa hauteur quand la vitesse change.",
         (_CLIP, Param("enabled", Kind.BOOLEAN, "Hauteur conservée ou non.")), _set_clip_preserve_pitch, group="time"),
    Verb("set_clip_remap_audio", "Le remappage s'applique à la vidéo et à l'audio (oui), ou à la vidéo seule (l'audio garde son temps).",
         (_CLIP, Param("enabled", Kind.BOOLEAN, "Audio remappé ou non.")), _set_clip_remap_audio, group="time"),
)

__all__ = ["TIME_VERBS"]
