"""Propriétés animables de l'étalonnage d'un clip : ``color.<champ>`` et ``color.<roue>.<canal>`` (ADR-0008, étape 6).

Importé par :mod:`core.animation_targets`. Les images clés vivent dans ``Clip.animation`` (comme ``fx.*``, ``graphic.*``,
``mask.*``) ; la valeur de base est celle du :class:`~core.color_grading.ColorGrade` du clip.

**Vingt-trois propriétés** (toutes lues dans le modèle, jamais recopiées) :

- sept réglages simples : ``color.exposure``, ``color.contrast``, ``color.saturation``, ``color.temperature``, ``color.hue``,
  ``color.shadows``, ``color.highlights`` (bornes : :data:`core.color_grading.EXPOSURE_MIN` etc.) ;
- les quatre roues, canal par canal : ``color.lift.r`` … ``color.offset.y`` (``r`` / ``g`` / ``b`` : le palet, ``y`` : la
  molette ; bornes ``[-1, 1]``).

**Ni les courbes ni la LUT** : elles ne sont pas scalaires (ADR-0008, décision 1). **Seul le réglage simple du clip s'anime** : un
clip étalonné par un graphe de nœuds (:class:`~core.color_nodes.ColorNodeGraph` à plusieurs nœuds, ou un nœud nommé / qualifié /
fenêtré) n'a pas ces cibles (``KeyError`` à la résolution, comme tout identifiant inconnu) ; l'animation par nœud est une suite
possible hors de ce plan. Une image clé ``color.*`` qui reste dans un clip devenu graphe est ignorée par le rendu.

Comment chaque propriété est rendue (choix mesuré : ``docs/adr/0008-*.md``, « Résultats de l'étape 6 ») :

- par **expression** FFmpeg (``eq`` en ``eval=frame``, ``hue``) : contraste, saturation, teinte ;
- par **fichier de commandes** ``sendcmd`` : ombres et hautes lumières (``colorbalance``), température
  (``colorchannelmixer``), exposition et roues (une table de 256 niveaux envoyée à ``curves``, **pas** ``lutrgb`` : voir
  l'ADR, ``lutrgb`` recalcule sa table à chaque commande, ×100 à ×200).

Découpage du nom : ``color.`` puis un champ, ou ``color.`` puis une roue, un point et un canal. Aucun nom de champ ne contient de
point, aucun nom de roue non plus (garde : ``tests/test_grade_targets.py``).
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from dataclasses import replace
from typing import Any

from .animation import AnimatableProperty, AnimationCurve, Keyframe, ValueKind
from .animation_targets import PropertyTarget, generic_target, register_clip_dynamic_targets
from .color_grading import (
    CONTRAST_MAX,
    CONTRAST_MIN,
    EXPOSURE_MAX,
    EXPOSURE_MIN,
    HIGHLIGHTS_MAX,
    HIGHLIGHTS_MIN,
    HUE_MAX,
    HUE_MIN,
    SATURATION_MAX,
    SATURATION_MIN,
    SHADOWS_MAX,
    SHADOWS_MIN,
    TEMPERATURE_MAX,
    TEMPERATURE_MIN,
    WHEEL_MAX,
    WHEEL_MIN,
    WHEELS,
    ColorGrade,
    Wheel,
)

GRADE_PREFIX = "color."
GRADE_GROUP = "color"
WHEEL_CHANNELS: tuple[str, ...] = ("r", "g", "b", "y")

# (champ, minimum, maximum, pas d'édition) des réglages simples ; le défaut est celui de ``ColorGrade()``.
SCALAR_FIELDS: tuple[tuple[str, float, float, float], ...] = (
    ("exposure", EXPOSURE_MIN, EXPOSURE_MAX, 0.05),
    ("contrast", CONTRAST_MIN, CONTRAST_MAX, 0.01),
    ("saturation", SATURATION_MIN, SATURATION_MAX, 0.01),
    ("temperature", TEMPERATURE_MIN, TEMPERATURE_MAX, 1.0),
    ("hue", HUE_MIN, HUE_MAX, 1.0),
    ("shadows", SHADOWS_MIN, SHADOWS_MAX, 0.01),
    ("highlights", HIGHLIGHTS_MIN, HIGHLIGHTS_MAX, 0.01),
)
SCALAR_NAMES: tuple[str, ...] = tuple(row[0] for row in SCALAR_FIELDS)


def scalar_property_id(name: str) -> str:
    return f"{GRADE_PREFIX}{name}"


def wheel_property_id(wheel: str, channel: str) -> str:
    return f"{GRADE_PREFIX}{wheel}.{channel}"


WHEEL_IDS: tuple[str, ...] = tuple(wheel_property_id(wheel, channel) for wheel in WHEELS for channel in WHEEL_CHANNELS)

EXPRESSION_GRADE_PARAMS: tuple[str, ...] = tuple(scalar_property_id(n) for n in ("contrast", "saturation", "hue"))
"""Propriétés rendues par une **expression** FFmpeg (:mod:`core.grade_render`) : ``eq`` (``eval=frame``) et ``hue``."""

SENDCMD_GRADE_PARAMS: tuple[str, ...] = (
    *(scalar_property_id(n) for n in ("exposure", "temperature", "shadows", "highlights")), *WHEEL_IDS,
)
"""Propriétés rendues par un fichier de commandes ``sendcmd`` (:mod:`core.grade_render`) : ``colorbalance``,
``colorchannelmixer`` et ``curves`` (exposition, roues)."""

ANIMATABLE_GRADE_PARAMS: tuple[str, ...] = (*EXPRESSION_GRADE_PARAMS, *SENDCMD_GRADE_PARAMS)
"""Propriétés animables **et rendues** : une seule source (règle de l'ADR-0008 : rien n'est enregistré sans rendu qui tienne
son budget). Chaque identifiant du modèle est dans exactement une des deux listes (garde : ``tests/test_grade_targets.py``)."""

_ANIMATABLE: frozenset[str] = frozenset(ANIMATABLE_GRADE_PARAMS)


def is_grade_property(property_name: str) -> bool:
    """``True`` si ``property_name`` est une image clé d'étalonnage **rendue** (``color.exposure``, ``color.lift.r``…)."""
    return property_name in _ANIMATABLE


def has_grade_animation(animation: Any) -> bool:
    """``True`` si une des images clés est une propriété d'étalonnage rendue (peu coûteux : sert de garde partout)."""
    return bool(animation) and any(kf.property_name in _ANIMATABLE for kf in animation)


def is_animatable_grade(grade: object) -> bool:
    """Seul le réglage simple s'anime (``None`` : étalonnage neutre, que les images clés créent à la première écriture)."""
    return grade is None or isinstance(grade, ColorGrade)


def _spec(property_id: str) -> AnimatableProperty | None:
    if property_id not in _ANIMATABLE:
        return None
    name = property_id[len(GRADE_PREFIX):]
    for field_name, low, high, step in SCALAR_FIELDS:
        if name == field_name:
            return AnimatableProperty(property_id, f"color.property.{name}", ValueKind.FLOAT,
                                      float(getattr(ColorGrade(), name)), low, high, step, group=GRADE_GROUP)
    return AnimatableProperty(property_id, f"color.property.{name}", ValueKind.FLOAT, 0.0, WHEEL_MIN, WHEEL_MAX, 0.01,
                              group=GRADE_GROUP)


def grade_spec(property_id: str) -> AnimatableProperty:
    """Description (bornes, défaut) de ``property_id`` ; ``KeyError`` si ce n'est pas une propriété d'étalonnage rendue."""
    spec = _spec(property_id)
    if spec is None:
        raise KeyError(f"Propriété d'étalonnage inconnue : {property_id!r}.")
    return spec


def read_value(grade: object, property_id: str) -> float:
    """Valeur statique de ``property_id`` dans ``grade`` (``None`` : valeur neutre)."""
    spec = grade_spec(property_id)
    if not isinstance(grade, ColorGrade):
        return float(spec.default)
    name = property_id[len(GRADE_PREFIX):]
    if "." in name:
        wheel, channel = name.split(".")
        return float(getattr(getattr(grade, wheel), channel))
    return float(getattr(grade, name))


def write_value(grade: object, property_id: str, value: float) -> ColorGrade:
    """``grade`` (ou le neutre) avec ``property_id`` remplacé par ``value`` bornée."""
    spec = grade_spec(property_id)
    current = grade if isinstance(grade, ColorGrade) else ColorGrade()
    value = float(spec.clamp(value))
    name = property_id[len(GRADE_PREFIX):]
    if "." in name:
        wheel, channel = name.split(".")
        return current.with_wheel(wheel, replace(getattr(current, wheel), **{channel: value}))
    return current.with_field(name, value)


def _grade_target(clip: Any, property_id: str) -> PropertyTarget | None:
    spec = _spec(property_id)
    if spec is None or not is_animatable_grade(getattr(clip, "color_grade", None)):
        return None

    def get_static(target_clip) -> float:
        return read_value(getattr(target_clip, "color_grade", None), property_id)

    def set_static(target_clip, value) -> None:
        target_clip.color_grade = write_value(getattr(target_clip, "color_grade", None), property_id, value)

    def applies_to_clip(target_clip) -> bool:
        return is_animatable_grade(getattr(target_clip, "color_grade", None))

    return generic_target(
        spec, get_static=get_static, set_static=set_static,
        applies_to=lambda track_type: track_type == "video", applies_to_clip=applies_to_clip,
    )


def _grade_ids(clip: Any) -> Iterator[str]:
    if is_animatable_grade(getattr(clip, "color_grade", None)):
        yield from ANIMATABLE_GRADE_PARAMS


# ``targets_for_clip(..., include_effects=True)`` (le Graph Editor, les menus) ne liste PAS ces propriétés : l'interface de
# l'étalonnage est l'étape 8 du plan (``include_grade=True``). Elles se résolvent avec ``get_target(id, clip)`` (scripts, historique,
# copier / coller).
register_clip_dynamic_targets(GRADE_PREFIX, _grade_target, _grade_ids, group="grade")


# ---------------------------------------------------------------------------
# Valeur effective : une seule fonction pour l'export, l'aperçu et (étape 7) le moniteur
# ---------------------------------------------------------------------------


class GradeCurves:
    """Les courbes ``color.*`` d'un clip, construites **une fois** : ``at(t)`` rend l'étalonnage évalué à l'instant local ``t``.

    Une image clé dont la valeur n'est pas un nombre fini est ignorée (aucun ``nan`` ne doit atteindre une expression ou un
    fichier de commandes). Un identifiant sans image clé garde la valeur statique du réglage de base.
    """

    def __init__(self, grade: object, animation: Any) -> None:
        self.base = grade if isinstance(grade, ColorGrade) else ColorGrade()
        grouped: dict[str, list[Keyframe]] = {}
        for keyframe in animation or ():
            if keyframe.property_name in _ANIMATABLE and _finite(keyframe.value):
                grouped.setdefault(keyframe.property_name, []).append(keyframe)
        self.curves: dict[str, AnimationCurve] = {
            property_id: AnimationCurve(frames, ValueKind.FLOAT) for property_id, frames in grouped.items()
        }
        self.specs = {property_id: grade_spec(property_id) for property_id in self.curves}

    def __bool__(self) -> bool:
        return bool(self.curves)

    def animated(self, *property_ids: str) -> bool:
        return any(property_id in self.curves for property_id in property_ids)

    def value(self, property_id: str, local_time: float) -> float:
        base = read_value(self.base, property_id)
        curve = self.curves.get(property_id)
        if curve is None:
            return base
        return float(self.specs[property_id].evaluate(curve, base, local_time))

    def at(self, local_time: float) -> ColorGrade:
        """L'étalonnage de base dont les champs animés sont remplacés par leur valeur à ``local_time`` (bornée)."""
        scalars: dict[str, float] = {}
        wheels: dict[str, dict[str, float]] = {}
        for property_id in self.curves:
            value = self.value(property_id, local_time)
            name = property_id[len(GRADE_PREFIX):]
            if "." in name:
                wheel, channel = name.split(".")
                wheels.setdefault(wheel, {})[channel] = value
            else:
                scalars[name] = value
        grade = self.base
        for wheel, channels in wheels.items():
            grade = grade.with_wheel(wheel, replace(getattr(grade, wheel), **channels))
        for name, value in scalars.items():
            grade = grade.with_field(name, value)
        return grade


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def effective_grade(clip: Any, local_time: float) -> object:
    """Étalonnage du clip à l'instant local ``local_time`` : la valeur de base, remplacée par chaque courbe ``color.*``
    (évaluée et bornée comme l'inspecteur).

    Fonction pure (sans Qt, sans effet de bord) : le moniteur et l'aperçu l'appellent (ADR-0008, décision 2). **Sans image clé,
    exactement** ``clip.color_grade`` (le même objet, ``None`` compris) ; un graphe de nœuds est rendu tel quel (il ne s'anime
    pas). Un étalonnage contourné (``enabled=False``) reste contourné.
    """
    grade = getattr(clip, "color_grade", None)
    animation = getattr(clip, "animation", None)
    if not is_animatable_grade(grade) or not has_grade_animation(animation):
        return grade
    curves = GradeCurves(grade, animation)
    return curves.at(local_time) if curves else grade


def has_live_grade_animation(clip: Any) -> bool:
    """``True`` si ``clip`` porte une image clé d'étalonnage que le rendu applique (réglage simple, pas un graphe)."""
    return is_animatable_grade(getattr(clip, "color_grade", None)) and has_grade_animation(getattr(clip, "animation", None))


__all__ = [
    "ANIMATABLE_GRADE_PARAMS",
    "EXPRESSION_GRADE_PARAMS",
    "GRADE_PREFIX",
    "SENDCMD_GRADE_PARAMS",
    "GradeCurves",
    "Wheel",
    "effective_grade",
    "grade_spec",
    "has_grade_animation",
    "has_live_grade_animation",
    "is_animatable_grade",
    "is_grade_property",
    "read_value",
    "scalar_property_id",
    "wheel_property_id",
    "write_value",
]
