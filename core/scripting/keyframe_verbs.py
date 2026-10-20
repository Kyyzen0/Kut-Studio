"""Images clés : celles des effets, et l'édition de toute image clé (lot 8 de l'extension 0.8.0).

Aucune sémantique propre : chaque verbe appelle la fonction de :mod:`core.keyframe_editing` que l'inspecteur, la timeline et le Graph Editor appellent
(``ui/main_window_mixins/animation.py``, ``ui/graph_editor.py``). Les règles sont dans :mod:`keyframe_rules` ; en bref :

- **une image clé se désigne par `property` + `at`** (le ``property_name`` et le ``time_seconds`` de ``describe``), jamais par son identifiant ;
- **`property`** est le nom du registre d'animation tel que la description le donne : propriété du transform, ``graphic.*``, ``mask.<id>.<nom>``,
  ``fx.<effet>.<paramètre>``. La vitesse a ses verbes (transaction du temps) ;
- **les effets** : ``add_effect_keyframe`` / ``remove_effect_keyframe`` désignent l'effet et son paramètre séparément (comme ``add_mask_keyframe`` pour un
  masque) ; seuls les paramètres que le modèle rend animables (:data:`core.effect_targets.ANIMATABLE_EFFECT_PARAMS`) sont acceptés ;
- **sans aimantation** : l'interface aligne un déplacement sur une image de la timeline ; un script donne l'instant exact (à la microseconde) ;
- **rien n'est ramené en silence** : valeur hors bornes, déplacement hors du clip, arrivée sur une autre image clé, pente ignorée par le rendu : refusés.

Équivalents écartés : ``set_value_at`` (valeur statique si la propriété n'est pas animée, image clé sinon) est ``set_transform`` / ``set_graphic`` /
``update_effect`` puis ``add_keyframe`` ; ``set_transform_keyframe`` et ``remove_transform_keyframe`` sont ``add_keyframe`` / ``remove_keyframe`` sur le
transform.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..animation import TIME_EPSILON, InterpolationType, TangentMode, normalize_time
from ..effect_targets import ANIMATABLE_EFFECT_PARAMS
from ..effects_model import clip_effects, fx_property_id
from ..keyframe_editing import (
    KeyframeRef, add_keyframe, clear_effect_animation, move_keyframes, remove_keyframe_at, set_animation_enabled, set_interpolation,
    set_keyframe_time, set_keyframe_values, set_tangents,
)
from ..project_model import Project
from ..timeline_operations import find_clip
from .effect_verbs import _effect_id, _video_clip
from .keyframe_rules import (
    MAX_BLOCK, MAX_SLOPE, keyframe_at, require_effective_tangents, require_free_arrivals, require_value, require_within,
    resolve_keyframe_target,
)
from .resource_limits import require_keyframe_slot
from .verbs import MAX_TIME, Kind, Operation, Param, Verb, _CLIP

_INTERPOLATIONS = tuple(item.value for item in InterpolationType)
_PARAMETERS = tuple(dict.fromkeys(name for names in ANIMATABLE_EFFECT_PARAMS.values() for name in names))

_EFFECT = Param("effect", Kind.SELECTOR, "L'effet du clip, par identifiant.", of="effect")
_PROPERTY = Param("property", Kind.STRING, "La propriété animée, telle que `describe` la nomme (`property_name`) : transform, `graphic.*`, `mask.<id>.<nom>`, `fx.<effet>.<paramètre>`.")
_AT = Param("at", Kind.NUMBER, "Instant de l'image clé, en secondes depuis le début du clip.", minimum=0.0, maximum=MAX_TIME)
_PARAMETER = Param("parameter", Kind.ENUM, "Le paramètre animé de l'effet (seuls ceux que le modèle rend animables).", choices=_PARAMETERS)


# --- Images clés d'un effet ------------------------------------------------------------------------------------------------


def _effect_property(project: Project, a: Mapping[str, Any], verb: str) -> tuple[str, str]:
    """``(clip, identifiant de la propriété fx.<effet>.<paramètre>)`` pour un clip vidéo éditable ; refuse un paramètre non animable."""
    clip_id = _video_clip(project, a["clip"])
    effect_id = _effect_id(project, clip_id, a["effect"])
    effect = next(item for item in clip_effects(project, clip_id) if item.id == effect_id)
    name = str(a["parameter"])
    if name not in ANIMATABLE_EFFECT_PARAMS.get(effect.type, ()):
        known = ", ".join(ANIMATABLE_EFFECT_PARAMS.get(effect.type, ())) or "aucun"
        raise ValueError(f"{verb} : le paramètre {name!r} de l'effet '{effect_id}' ({effect.type.value}) n'est pas animable (animables : {known}).")
    return clip_id, fx_property_id(effect_id, name)


def _add_effect_keyframe(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        clip_id, property_id = _effect_property(project, a, "add_effect_keyframe")
        _id, _clip, target = resolve_keyframe_target(project, clip_id, property_id, "add_effect_keyframe")
        value = require_value(target, a["value"], str(a["parameter"]))
        require_keyframe_slot(_clip, target, float(a["at"]), "add_effect_keyframe")
        # L'instant hors du clip est refusé par le modèle (``add_keyframe`` : « Temps hors du clip »).
        return add_keyframe(project, clip_id, property_id, float(a["at"]), value, interpolation=a.get("interpolation"))

    return operation


def _remove_effect_keyframe(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        clip_id, property_id = _effect_property(project, a, "remove_effect_keyframe")
        if not remove_keyframe_at(project, clip_id, property_id, float(a["at"])):
            raise KeyError(f"Aucune image clé de {a['parameter']!r} à {a['at']} s sur cet effet.")
        return None

    return operation


def _clear_effect_animation(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        clip_id = _video_clip(project, a["clip"])
        effect_id = _effect_id(project, clip_id, a["effect"])
        at = float(a["at"])
        require_within(find_clip(project, clip_id), at, "clear_effect_animation")
        return clear_effect_animation(project, clip_id, effect_id, at)

    return operation


# --- Édition d'une image clé ------------------------------------------------------------------------------------------------


def _ref(clip_id: str, property_name: str, keyframe_id: str) -> KeyframeRef:
    return KeyframeRef(clip_id, property_name, keyframe_id)


def _set_keyframe_value(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        name = str(a["property"])
        clip_id, clip, target = resolve_keyframe_target(project, a["clip"], name, "set_keyframe_value")
        found = keyframe_at(clip, target, float(a["at"]), name)
        value = require_value(target, a["value"], name)
        set_keyframe_values(project, {_ref(clip_id, name, found.id): value})
        return None

    return operation


def _arrival(clip: Any, found: Any, delta: float, what: str) -> float:
    arrival = normalize_time(found.time_seconds + delta)
    require_within(clip, arrival, what)
    return arrival


def _move_checked(project: Project, clip_id: str, items: list[tuple[str, Any]], delta: float, what: str) -> None:
    """Contrôle un déplacement d'un bloc, puis le fait par le modèle (qui, sans ces contrôles, bornerait le décalage et remplacerait l'image clé rejointe)."""
    clip = find_clip(project, clip_id)
    by_property: dict[str, list[Any]] = {}
    for name, found in items:
        by_property.setdefault(name, []).append(found)
    for name, frames in by_property.items():
        _id, _clip, target = resolve_keyframe_target(project, clip_id, name, what)
        arrivals = {frame.id: _arrival(clip, frame, delta, what) for frame in frames}
        require_free_arrivals(target.curve(clip), set(arrivals), arrivals, what)
    move_keyframes(project, [_ref(clip_id, name, found.id) for name, found in items], delta, fps=None)


def _set_keyframe_time(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        name = str(a["property"])
        clip_id, clip, target = resolve_keyframe_target(project, a["clip"], name, "set_keyframe_time")
        found = keyframe_at(clip, target, float(a["at"]), name)
        delta = normalize_time(float(a["to"])) - found.time_seconds
        if abs(delta) <= TIME_EPSILON:
            raise ValueError("set_keyframe_time : l'image clé est déjà à cet instant.")
        _arrival(clip, found, delta, "set_keyframe_time")
        arrivals = {found.id: normalize_time(float(a["to"]))}
        require_free_arrivals(target.curve(clip), {found.id}, arrivals, "set_keyframe_time")
        set_keyframe_time(project, _ref(clip_id, name, found.id), float(a["to"]))
        return None

    return operation


def _move_keyframes(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        delta = float(a["delta"])
        if abs(delta) <= TIME_EPSILON:
            raise ValueError("move_keyframes : un décalage nul ne déplace rien.")
        items: list[tuple[str, Any]] = []
        clip_id = ""
        seen: set[tuple[str, str]] = set()
        for entry in a["keyframes"]:
            name = str(entry["property"])
            clip_id, clip, target = resolve_keyframe_target(project, a["clip"], name, "move_keyframes")
            found = keyframe_at(clip, target, float(entry["at"]), name)
            if (name, found.id) in seen:
                raise ValueError(f"move_keyframes : l'image clé de {name!r} à {entry['at']:g} s est citée deux fois.")
            seen.add((name, found.id))
            items.append((name, found))
        _move_checked(project, clip_id, items, delta, "move_keyframes")
        return None

    return operation


def _set_keyframe_interpolation(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        name = str(a["property"])
        clip_id, clip, target = resolve_keyframe_target(project, a["clip"], name, "set_keyframe_interpolation")
        found = keyframe_at(clip, target, float(a["at"]), name)
        return set_interpolation(project, [_ref(clip_id, name, found.id)], str(a["interpolation"]))

    return operation


def _set_keyframe_tangents(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        name = str(a["property"])
        clip_id, clip, target = resolve_keyframe_target(project, a["clip"], name, "set_keyframe_tangents")
        found = keyframe_at(clip, target, float(a["at"]), name)
        in_slope = None if a.get("in_slope") is None else float(a["in_slope"])
        out_slope = None if a.get("out_slope") is None else float(a["out_slope"])
        mode = a.get("mode")
        auto = bool(a.get("auto", False))
        require_effective_tangents(
            target.curve(clip), found, in_slope=in_slope, out_slope=out_slope, mode=mode, auto=auto, what="set_keyframe_tangents",
        )
        updated = set_tangents(
            project, _ref(clip_id, name, found.id), in_slope=in_slope, out_slope=out_slope,
            mode=None if mode is None else TangentMode(mode), auto=auto,
        )
        return updated

    return operation


def _set_animation_enabled(a: Mapping[str, Any]) -> Operation:
    def operation(project: Project) -> Any:
        name = str(a["property"])
        clip_id, clip, _target = resolve_keyframe_target(project, a["clip"], name, "set_animation_enabled")
        at = float(a["at"])
        require_within(clip, at, "set_animation_enabled")
        set_animation_enabled(project, clip_id, name, bool(a["enabled"]), at)
        return None

    return operation


KEYFRAME_VERBS: tuple[Verb, ...] = (
    Verb("add_effect_keyframe", "Pose (ou remplace) une image clé d'un paramètre animable d'un effet vidéo (correction de couleur, vignette, flou, aberration chromatique, rayon du Bloom) ; le temps est relatif au clip.",
         (_CLIP, _EFFECT, _PARAMETER, _AT,
          Param("value", Kind.NUMBER, "La valeur (dans les bornes du paramètre de l'effet).", minimum=-MAX_TIME, maximum=MAX_TIME),
          Param("interpolation", Kind.ENUM, "Interpolation vers l'image clé suivante.", required=False, choices=_INTERPOLATIONS)),
         _add_effect_keyframe, group="animation"),
    Verb("remove_effect_keyframe", "Retire l'image clé d'un paramètre d'effet à un instant donné ; le paramètre garde la valeur qu'il montrait.",
         (_CLIP, _EFFECT, _PARAMETER, _AT), _remove_effect_keyframe, group="animation"),
    Verb("clear_effect_animation", "Retire toute l'animation d'un effet : chaque paramètre garde la valeur qu'il montrait à l'instant `at`.",
         (_CLIP, _EFFECT, Param("at", Kind.NUMBER, "Instant dont la valeur est gardée, en secondes depuis le début du clip.", minimum=0.0, maximum=MAX_TIME)),
         _clear_effect_animation, group="animation"),
    Verb("set_keyframe_value", "Change la valeur d'une image clé existante (désignée par `property` et `at`) ; hors bornes : refusé.",
         (_CLIP, _PROPERTY, _AT, Param("value", Kind.NUMBER, "La valeur (dans les bornes de la propriété).", minimum=-MAX_TIME, maximum=MAX_TIME)),
         _set_keyframe_value, group="animation"),
    Verb("set_keyframe_time", "Place une image clé existante à un autre instant ; refusé hors du clip ou sur une autre image clé de la propriété.",
         (_CLIP, _PROPERTY, _AT, Param("to", Kind.NUMBER, "Nouvel instant, en secondes depuis le début du clip.", minimum=0.0, maximum=MAX_TIME)),
         _set_keyframe_time, group="animation"),
    Verb("move_keyframes", "Décale des images clés du même temps, d'un bloc (de 1 à 64) ; refusé si l'une sortirait du clip ou arriverait sur une image clé qui ne bouge pas.",
         (_CLIP, Param("keyframes", Kind.LIST, f"Les images clés à déplacer (1 à {MAX_BLOCK}), chacune une fois.", minimum=1, maximum=MAX_BLOCK,
                       fields=(_PROPERTY, _AT)),
          Param("delta", Kind.NUMBER, "Décalage en secondes (non nul ; négatif = plus tôt).", minimum=-MAX_TIME, maximum=MAX_TIME)),
         _move_keyframes, group="animation"),
    Verb("set_keyframe_interpolation", "Change l'interpolation du segment qui part d'une image clé (désignée par `property` et `at`).",
         (_CLIP, _PROPERTY, _AT, Param("interpolation", Kind.ENUM, "Interpolation vers l'image clé suivante.", choices=_INTERPOLATIONS)),
         _set_keyframe_interpolation, group="animation"),
    Verb("set_keyframe_tangents",
         "Règle les pentes Bézier d'une image clé : `in_slope` / `out_slope` (unités par seconde), `mode` (`linked` : pentes égales, `broken`), ou `auto` (pentes automatiques) ; au moins un réglage.",
         (_CLIP, _PROPERTY, _AT,
          Param("in_slope", Kind.NUMBER, "Pente entrante (la précédente image clé doit être « bezier »).", required=False, minimum=-MAX_SLOPE, maximum=MAX_SLOPE),
          Param("out_slope", Kind.NUMBER, "Pente sortante (l'image clé doit être « bezier » et suivie d'une autre).", required=False, minimum=-MAX_SLOPE, maximum=MAX_SLOPE),
          Param("mode", Kind.ENUM, "Lien entre les pentes.", required=False, choices=tuple(item.value for item in TangentMode)),
          Param("auto", Kind.BOOLEAN, "Revenir aux pentes automatiques (exclut `in_slope` et `out_slope`).", required=False)),
         _set_keyframe_tangents, group="animation"),
    Verb("set_animation_enabled", "Anime une propriété (une image clé à `at`, à sa valeur actuelle) ou retire son animation (elle garde la valeur qu'elle montrait à `at`).",
         (_CLIP, _PROPERTY, Param("enabled", Kind.BOOLEAN, "Animée ou non."),
          Param("at", Kind.NUMBER, "Instant de l'image clé posée, ou dont la valeur est gardée, en secondes depuis le début du clip.", minimum=0.0, maximum=MAX_TIME)),
         _set_animation_enabled, group="animation"),
)

__all__ = ["KEYFRAME_VERBS"]
