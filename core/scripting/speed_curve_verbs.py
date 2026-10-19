"""Courbe de vitesse, suite : interpolation, tangentes, édition groupée, retrait groupé, préréglages (lot 8 de l'extension 0.8.0).

Même socle que :mod:`core.scripting.time_verbs` (dont ces verbes réutilisent les contrôles) : les fonctions de :mod:`core.time_ops` et
:mod:`core.time_presets` que l'éditeur de courbes et le menu « Vitesse » appellent (``ui/graph_editor_time.py``, :mod:`core.time_commands`).

- **un point se désigne par son instant** (``at``, relatif au clip), une liste de points par leurs instants (1 à 64, chacun une fois) ;
- **`edit_speed_points`** déplace et/ou change la valeur de points *dans une seule transaction*, comme le glissement de l'éditeur : l'état
  intermédiaire d'un déplacement seul peut être refusé (durée dégénérée) alors que le résultat final ne l'est pas ;
- **tangentes** : mêmes contrôles que l'éditeur de courbes (:func:`keyframe_rules.require_effective_tangents`), appliqués à chaque point ;
- **préréglages** : l'énumération :class:`core.time_presets.TimePreset` (jamais un préréglage de l'utilisateur) ; ``freeze`` pose un arrêt sur image
  d'une seconde à l'instant ``at``, qui doit tomber sur une image de la timeline (``insert_hold``) ; les autres refusent ``at``.

Équivalent écarté : ``move_speed_point`` est ``move_speed_points`` avec un seul point.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..animation import InterpolationType, TangentMode
from ..keyframe_editing import KeyframeRef, find_clip
from ..project_model import Project
from ..time_map import SPEED_LIMIT, SPEED_PROPERTY
from ..time_ops import edit_speed_points, remove_speed_points, set_speed_point_interpolation, set_speed_tangents
from ..time_presets import TimePreset, apply_preset
from ..time_targets import SPEED_TARGET
from .keyframe_rules import MAX_BLOCK, MAX_SLOPE, require_effective_tangents
from .time_verbs import (
    _AT, _MAX_POINTS, _RIPPLE, _TOLERANCE, _checked_move, _live_clip, _mode, _require_aligned, _speed_point, _time_clip,
)
from .verbs import MAX_TIME, Kind, Operation, Param, Verb, _CLIP

_POINTS = Param("points", Kind.LIST, f"Les instants des points (1 à {_MAX_POINTS} nombres, relatifs au clip), chacun une fois.", minimum=1, maximum=_MAX_POINTS,
                item=Param("point", Kind.NUMBER, "Instant d'un point.", minimum=0.0, maximum=MAX_TIME))


def _point_refs(project: Project, clip_id: str, moments: list[float], verb: str) -> list[KeyframeRef]:
    ids = [_speed_point(project, clip_id, float(moment)) for moment in moments]
    if len(set(ids)) != len(ids):
        raise ValueError(f"{verb} : deux instants désignent le même point.")
    return [KeyframeRef(clip_id, SPEED_PROPERTY, point_id) for point_id in ids]


def _set_speed_point_interpolation(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        clip_id = _time_clip(project, a["clip"])
        refs = _point_refs(project, clip_id, list(a["points"]), "set_speed_point_interpolation")
        return set_speed_point_interpolation(project, clip_id, [ref.keyframe_id for ref in refs], str(a["interpolation"]), mode=_mode(a))

    return operation


def _set_speed_tangents(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        clip_id = _time_clip(project, a["clip"])
        refs = _point_refs(project, clip_id, list(a["points"]), "set_speed_tangents")
        in_slope = None if a.get("in_slope") is None else float(a["in_slope"])
        out_slope = None if a.get("out_slope") is None else float(a["out_slope"])
        mode = a.get("mode")
        auto = bool(a.get("auto", False))
        curve = SPEED_TARGET.curve(find_clip(project, clip_id))
        for ref in refs:
            keyframe = next(point for point in curve.keyframes if point.id == ref.keyframe_id)
            require_effective_tangents(curve, keyframe, in_slope=in_slope, out_slope=out_slope, mode=mode, auto=auto, what="set_speed_tangents")
        tangents: dict[str, Any] = {"in_slope": in_slope, "out_slope": out_slope, "auto": auto}
        tangents["mode"] = None if mode is None else TangentMode(mode)
        set_speed_tangents(project, refs, ripple=_mode(a), **tangents)
        return None

    return operation


def _edit_speed_points(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        clip_id = _time_clip(project, a["clip"])
        entries = list(a["points"])
        delta = float(a.get("delta", 0.0))
        moments = [float(entry["at"]) for entry in entries]
        values = {index: float(entry["value"]) for index, entry in enumerate(entries) if "value" in entry}
        if abs(delta) <= _TOLERANCE and not values:
            raise ValueError("edit_speed_points : donnez un `delta` non nul et/ou une `value` pour au moins un point.")
        refs = _point_refs(project, clip_id, moments, "edit_speed_points")
        if abs(delta) > _TOLERANCE:
            _checked_move(project, clip_id, moments, delta, "edit_speed_points")
        by_ref = {refs[index]: value for index, value in values.items()}
        edit_speed_points(project, refs, delta_seconds=delta if abs(delta) > _TOLERANCE else 0.0, values=by_ref or None, fps=None, mode=_mode(a))
        return len(refs)

    return operation


def _remove_speed_points(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        clip_id = _time_clip(project, a["clip"])
        refs = _point_refs(project, clip_id, list(a["points"]), "remove_speed_points")
        return remove_speed_points(project, refs, mode=_mode(a))

    return operation


def _apply_time_preset(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        clip_id = _live_clip(project, a["clip"], "apply_time_preset")
        preset = TimePreset(a["preset"])
        at = a.get("at")
        if preset is TimePreset.FREEZE:
            if at is None:
                raise ValueError("apply_time_preset : `freeze` demande `at`, l'instant de l'arrêt sur image.")
            _require_aligned(project, find_clip(project, clip_id), float(at), "apply_time_preset")
        elif at is not None:
            raise ValueError(f"apply_time_preset : `at` ne sert qu'au préréglage `freeze` (pas à `{preset.value}`).")
        apply_preset(project, clip_id, preset, local_time=0.0 if at is None else float(at), fps=float(project.fps), mode=_mode(a))
        return None

    return operation


_INTERPOLATIONS = tuple(item.value for item in InterpolationType)

SPEED_CURVE_VERBS: tuple[Verb, ...] = (
    Verb("set_speed_point_interpolation", "Change l'interpolation du segment qui part de chaque point de vitesse cité (échelon, linéaire, adouci, Bézier).",
         (_CLIP, _POINTS, Param("interpolation", Kind.ENUM, "Interpolation vers le point suivant.", choices=_INTERPOLATIONS), _RIPPLE),
         _set_speed_point_interpolation, group="time"),
    Verb("set_speed_tangents",
         "Règle les pentes Bézier de points de vitesse (unités de vitesse par seconde) : `in_slope`, `out_slope`, `mode` ou `auto` ; au moins un réglage, mêmes contrôles que l'éditeur de courbes.",
         (_CLIP, _POINTS,
          Param("in_slope", Kind.NUMBER, "Pente entrante (le point précédent doit être « bezier »).", required=False, minimum=-MAX_SLOPE, maximum=MAX_SLOPE),
          Param("out_slope", Kind.NUMBER, "Pente sortante (le point doit être « bezier » et suivi d'un autre).", required=False, minimum=-MAX_SLOPE, maximum=MAX_SLOPE),
          Param("mode", Kind.ENUM, "Lien entre les pentes.", required=False, choices=tuple(item.value for item in TangentMode)),
          Param("auto", Kind.BOOLEAN, "Revenir aux pentes automatiques (exclut `in_slope` et `out_slope`).", required=False), _RIPPLE),
         _set_speed_tangents, group="time"),
    Verb("edit_speed_points",
         "Déplace des points de vitesse de `delta` secondes et/ou change leur vitesse, en une seule transaction (comme le glissement de l'éditeur de courbes).",
         (_CLIP, Param("points", Kind.LIST, f"Les points (1 à {MAX_BLOCK}) : leur instant et, facultativement, leur nouvelle vitesse.", minimum=1, maximum=MAX_BLOCK,
                       fields=(_AT, Param("value", Kind.NUMBER, "Nouvelle vitesse du point (1 = normale, négatif = marche arrière).", required=False,
                                          minimum=-SPEED_LIMIT, maximum=SPEED_LIMIT))),
          Param("delta", Kind.NUMBER, "Décalage commun en secondes (absent ou nul : aucun déplacement).", required=False, minimum=-MAX_TIME, maximum=MAX_TIME),
          _RIPPLE),
         _edit_speed_points, group="time"),
    Verb("remove_speed_points", "Retire des points de la courbe de vitesse en une seule transaction ; le dernier retiré laisse la vitesse constante qu'il affichait.",
         (_CLIP, _POINTS, _RIPPLE), _remove_speed_points, group="time"),
    Verb("apply_time_preset",
         "Applique un préréglage de vitesse intégré : `slow_50`, `slow_25`, `fast_2`, `fast_4` (vitesse constante), `ramp_in`, `ramp_out` (rampes), `freeze` (arrêt sur image d'une seconde à `at`) ; remplace la courbe existante.",
         (_CLIP, Param("preset", Kind.ENUM, "Le préréglage.", choices=tuple(item.value for item in TimePreset)),
          Param("at", Kind.NUMBER, "Instant de l'arrêt sur image (`freeze` seulement), sur une image de la timeline.", required=False, minimum=0.0, maximum=MAX_TIME), _RIPPLE),
         _apply_time_preset, group="time"),
)

__all__ = ["SPEED_CURVE_VERBS"]
