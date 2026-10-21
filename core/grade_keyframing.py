"""Images clés de l'étalonnage côté interface : état des losanges, bascule, suppression (ADR-0008, étape 8).

Fonctions **pures** (aucun Qt) : l'inspecteur (les sept réglages), la page Couleur (les quatre roues) et la fenêtre principale
appellent les mêmes, puis la fenêtre enregistre **une** étape d'historique. Elles passent par :mod:`core.keyframe_editing` (même
garde de verrou de piste, même registre :mod:`core.animation_targets`) ; aucune écriture n'est faite si le clip n'est pas
modifiable.

Une **roue** (``lift`` / ``gamma`` / ``gain`` / ``offset``) a **un** losange pour ses quatre canaux ``r``, ``g``, ``b``, ``y`` : le
palet déplace les trois couleurs ensemble, la molette le maître ; les identifiants ``color.<roue>.<canal>`` restent quatre courbes
indépendantes (Graph Editor, collage), le losange les pose et les retire ensemble.

Seul le **réglage simple** s'anime (:func:`core.grade_targets.is_animatable_grade`) : un clip étalonné par un graphe de nœuds garde
ses images clés mais le rendu les ignore ; :func:`grade_animation_info` le dit à l'interface (``animatable`` faux, ``has_keys`` vrai).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from .animation import Keyframe, normalize_time
from .animation_targets import get_target
from .color_grading import WHEELS, ColorGrade, Wheel
from .compositing_guard import require_clip_editable
from .grade_targets import (
    SCALAR_NAMES,
    WHEEL_CHANNELS,
    effective_grade,
    is_animatable_grade,
    is_grade_property,
    scalar_property_id,
    wheel_property_id,
)
from .keyframe_editing import add_keyframe, find_clip, remove_keyframe_at

KEY_MATCH_TOLERANCE = 1e-3
"""Écart (secondes) sous lequel une image clé est « sous la tête » : celui des losanges de l'inspecteur."""

ADDED = "added"
REMOVED = "removed"


def wheel_property_ids(wheel: str) -> tuple[str, ...]:
    """Les quatre identifiants ``color.<roue>.<canal>`` d'une roue ; ``KeyError`` si ``wheel`` n'est pas une roue."""
    if wheel not in WHEELS:
        raise KeyError(f"Roue inconnue : {wheel!r}.")
    return tuple(wheel_property_id(wheel, channel) for channel in WHEEL_CHANNELS)


def grade_keyframes(clip: Any) -> list[Keyframe]:
    """Les images clés d'étalonnage **rendues** du clip (le registre décide, jamais un préfixe)."""
    return [k for k in getattr(clip, "animation", None) or () if is_grade_property(k.property_name)]


def live_grade_ids(clip: Any, property_ids: Iterable[str]) -> list[str]:
    """Parmi ``property_ids``, ceux qui ont au moins une image clé **et** que le rendu applique (réglage simple)."""
    if not is_animatable_grade(getattr(clip, "color_grade", None)):
        return []
    animated = {k.property_name for k in grade_keyframes(clip)}
    return [property_id for property_id in property_ids if property_id in animated]


# ---------------------------------------------------------------------------
# État d'un losange
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KeyState:
    """État d'un losange (une propriété, ou les quatre canaux d'une roue) à un instant local du clip."""

    keyed_all: bool = False       # une image clé sous la tête sur chaque propriété : losange plein
    keyed_any: bool = False       # au moins une : un clic les retire
    animated: bool = False        # au moins une image clé n'importe où : contour accentué (« entre-deux »)
    previous: bool = False        # une image clé avant la tête
    next: bool = False            # une image clé après la tête


def key_state(
    keyframes: Iterable[Keyframe], property_ids: Sequence[str], local_time: float, tolerance: float = KEY_MATCH_TOLERANCE,
) -> KeyState:
    """Calculé depuis la liste d'images clés seule (l'inspecteur n'a pas le clip, seulement ses images clés)."""
    wanted = set(property_ids)
    times: dict[str, list[float]] = {property_id: [] for property_id in wanted}
    for keyframe in keyframes:
        if keyframe.property_name in wanted:
            times[keyframe.property_name].append(float(keyframe.time_seconds))
    local = float(local_time)
    here = [any(abs(t - local) <= tolerance for t in values) for values in times.values()]
    every = [t for values in times.values() for t in values]
    return KeyState(
        keyed_all=bool(here) and all(here), keyed_any=any(here), animated=bool(every),
        previous=any(t < local - tolerance for t in every), next=any(t > local + tolerance for t in every),
    )


# ---------------------------------------------------------------------------
# Écriture
# ---------------------------------------------------------------------------


def _checked_ids(project: Any, clip_id: str, property_ids: Iterable[str]) -> tuple[Any, list[str]]:
    """Le clip et les identifiants, tous d'étalonnage et résolus avec ce clip. Le verrou est refusé par la première écriture
    (:func:`core.keyframe_editing.add_keyframe` et ``remove_keyframe_at`` appellent la garde avant de toucher au clip)."""
    clip = find_clip(project, clip_id)
    ids = list(dict.fromkeys(property_ids))
    for property_id in ids:
        if not is_grade_property(property_id):
            raise KeyError(f"Propriété d'étalonnage inconnue : {property_id!r}.")
        get_target(property_id, clip)          # KeyError : étalonnage découpé en nœuds, non animable
    return clip, ids


def toggle_grade_keys(
    project: Any, clip_id: str, property_ids: Iterable[str], local_time: float, tolerance: float = KEY_MATCH_TOLERANCE,
) -> str:
    """Losange : retire les images clés sous la tête (``REMOVED``), sinon en pose une sur chaque propriété (``ADDED``).

    Poser une image clé garde la valeur actuelle (et la forme d'une courbe déjà animée) ; la première image clé d'une propriété
    **active** son animation. Retirer la dernière image clé d'une propriété lui laisse la valeur qu'elle montrait. Tout est vérifié
    (verrou, genre de piste, étalonnage animable, temps dans le clip) avant la première écriture.
    """
    clip, ids = _checked_ids(project, clip_id, property_ids)
    present = []
    for property_id in ids:
        keyframe = get_target(property_id, clip).curve(clip).keyframe_at(float(local_time), tolerance=tolerance)
        if keyframe is not None:
            present.append((property_id, float(keyframe.time_seconds)))
    if present:
        for property_id, time in present:
            remove_keyframe_at(project, clip_id, property_id, time)
        return REMOVED
    for property_id in ids:
        add_keyframe(project, clip_id, property_id, local_time)
    return ADDED


def set_grade_keys(
    project: Any, clip_id: str, values: dict[str, float], local_time: float, tolerance: float = KEY_MATCH_TOLERANCE,
) -> int:
    """Écrit (ou met à jour) l'image clé de chaque propriété de ``values`` à ``local_time``, bornée par le registre.

    Une image clé déjà présente à ``tolerance`` près est mise à jour **à son propre instant** (jamais de doublon à deux
    millisecondes). Retourne le nombre de propriétés écrites. Tout est vérifié avant la première écriture.
    """
    clip, ids = _checked_ids(project, clip_id, values)
    written = 0
    for property_id in ids:
        value = float(values[property_id])
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError(f"Valeur non finie pour {property_id!r}.")
    for property_id in ids:
        existing = get_target(property_id, clip).curve(clip).keyframe_at(float(local_time), tolerance=tolerance)
        time = float(existing.time_seconds) if existing is not None else normalize_time(float(local_time))
        add_keyframe(project, clip_id, property_id, time, float(values[property_id]))
        written += 1
    return written


def clear_grade_animation(project: Any, clip_id: str, local_time: float | None = None) -> int:
    """Retire **toutes** les images clés d'étalonnage du clip ; retourne le nombre de propriétés qui en avaient.

    Avec ``local_time`` chaque propriété animable garde la valeur qu'elle montrait à cet instant (« Supprimer l'animation ») ;
    sans, les valeurs de base ne sont pas touchées (la réinitialisation les remet à zéro de toute façon). Un clip étalonné par
    nœuds (images clés ignorées au rendu) ou une propriété que ce fichier n'anime plus : l'image clé est simplement retirée.
    Verrous de piste et de calque respectés, rien n'est écrit en cas de refus.
    """
    clip = find_clip(project, clip_id)
    require_clip_editable(project, clip_id)
    names = sorted({k.property_name for k in grade_keyframes(clip)})
    if not names:
        return 0
    if local_time is not None:
        for property_id in names:
            target = _target_or_none(property_id, clip)
            if target is not None and target.get_keyframes(clip):
                target.set_static(clip, target.value_at(clip, float(local_time)))
    clip.animation = [k for k in clip.animation if not is_grade_property(k.property_name)]
    return len(names)


def _target_or_none(property_id: str, clip: Any) -> Any:
    try:
        return get_target(property_id, clip)
    except KeyError:
        return None


# ---------------------------------------------------------------------------
# Ce que la page Couleur montre
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GradeAnimationInfo:
    """L'animation de l'étalonnage d'un clip à un instant, pour la page Couleur (roues) et son avis de nœuds."""

    animatable: bool                                   # réglage simple : les images clés s'appliquent
    key_count: int = 0                                 # images clés d'étalonnage du clip (ignorées si ``animatable`` est faux)
    wheels: dict[str, KeyState] = field(default_factory=dict)
    shown: dict[str, Wheel] = field(default_factory=dict)     # valeurs évaluées à l'instant (vide : valeurs de base)

    @property
    def has_keys(self) -> bool:
        return self.key_count > 0


def grade_animation_info(clip: Any, local_time: float) -> GradeAnimationInfo:
    """Pour ``clip`` à ``local_time`` : animable ou non, nombre d'images clés, état du losange de chaque roue et, si une courbe
    anime l'étalonnage, les roues évaluées (celles que le rendu applique à cet instant, bornées)."""
    grade = getattr(clip, "color_grade", None)
    keys = grade_keyframes(clip)
    animatable = is_animatable_grade(grade)
    if not animatable:
        return GradeAnimationInfo(False, len(keys))
    wheels = {name: key_state(keys, wheel_property_ids(name), local_time) for name in WHEELS}
    shown: dict[str, Wheel] = {}
    if keys:
        evaluated = effective_grade(clip, local_time)
        if isinstance(evaluated, ColorGrade):
            shown = {name: getattr(evaluated, name) for name in WHEELS}
    return GradeAnimationInfo(True, len(keys), wheels, shown)


def scalar_ids() -> tuple[str, ...]:
    """Les sept réglages simples animables (exposition, contraste…)."""
    return tuple(scalar_property_id(name) for name in SCALAR_NAMES)


__all__ = [
    "ADDED",
    "KEY_MATCH_TOLERANCE",
    "REMOVED",
    "GradeAnimationInfo",
    "KeyState",
    "clear_grade_animation",
    "grade_animation_info",
    "grade_keyframes",
    "key_state",
    "live_grade_ids",
    "scalar_ids",
    "set_grade_keys",
    "toggle_grade_keys",
    "wheel_property_ids",
]
