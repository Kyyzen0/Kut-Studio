"""Rogner, couper et étendre un clip dont le temps suit une courbe de vitesse.

Pour un clip à vitesse constante, rogner ou couper revient à convertir un décalage de timeline en décalage de source
(``delta × vitesse``) : les anciennes opérations le font toujours. Avec une courbe de vitesse cette conversion n'existe
plus (la vitesse change, le mapping peut revenir en arrière) : on **restreint** le mapping à une portion ``[t0, t1]`` de
sa durée, ce qui fixe trois choses pour le clip restant :

- sa **fenêtre source** : la portion de média parcourue (``TimeMap.window_for``, retournements compris) ;
- son **ancre** : le temps source à son premier instant, ``M(t0)`` ;
- sa **durée imposée**, seulement si la dérivation naturelle (la source est épuisée) ne la reproduit pas.

Les keyframes de vitesse (``Clip.animation``) sont, elles, re-basées par les opérations existantes
(:func:`core.animation_targets.retime_animation` / :func:`~core.animation_targets.split_animation`), qui conservent la
forme des courbes Bézier : l'animation temporelle visible ne bouge pas, de part et d'autre d'une coupe ou d'un trim.

L'ancre et la durée ne sont écrites que si elles sont nécessaires : un clip monotone, qui épuise sa source, reste décrit
par ses seules bornes ``source_in`` / ``source_out``, comme avant.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from .time_map import PiecewiseTimeMap, TimeMap, speed_keyframes, time_map_for

if TYPE_CHECKING:
    from .animation import Keyframe
    from .project_model import Clip
    from .time_remapping import TimeRemapping

TOLERANCE = 1e-9
"""Écart (secondes) en deçà duquel deux temps sont le même instant."""

MIN_WINDOW = 1e-3
"""Largeur minimale de la fenêtre source : un clip dont la source est tenue (vitesse nulle) reste un clip valide."""


def needs_general_path(clip: "Clip") -> bool:
    """Le clip est-il décrit par une courbe, une ancre ou une durée imposée (sinon : les anciennes formules suffisent) ?"""
    remapping = clip.time_remapping
    return clip.has_speed_curve or remapping.anchor is not None or remapping.duration is not None


@dataclass(frozen=True)
class Restriction:
    """Ce qui décrit un clip restreint : fenêtre source et remappage (ancre / durée imposée si nécessaires)."""

    source_in: float
    source_out: float
    remapping: "TimeRemapping"


def restrict(
    time_map: TimeMap,
    remapping: "TimeRemapping",
    t0: float,
    t1: float,
    keyframes: tuple["Keyframe", ...],
) -> Restriction:
    """Restreint ``time_map`` à ``[t0, t1]`` (temps local de l'original).

    ``keyframes`` sont les keyframes de vitesse **du clip restreint** (déjà re-basées à zéro) : elles décident si la
    dérivation naturelle suffit à retrouver l'ancre et la durée.
    """
    anchor = time_map.source_time(t0)
    low, high = time_map.window_for(t0, t1)
    if high - low < 1e-6:
        # Source tenue (vitesse nulle) : une fenêtre de largeur nulle n'est pas un clip valide.
        pad = MIN_WINDOW / 2.0
        low, high = max(0.0, low - pad), high + pad
    target = t1 - t0
    base = replace(remapping, anchor=None, duration=None)
    natural = time_map_for(low, high, base, keyframes)
    candidate = natural
    final = base
    if abs(natural.source_time(0.0) - anchor) > TOLERANCE:
        final = replace(base, anchor=anchor)
        candidate = time_map_for(low, high, final, keyframes)
    if getattr(candidate, "underdetermined", False) or candidate.duration > target + TOLERANCE:
        final = replace(final, duration=target)
    return Restriction(low, high, final)


def free_map(clip: "Clip", source_limit: float, new_duration: float) -> TimeMap:
    """Le mapping du clip libéré de sa fenêtre actuelle : borné seulement par le média ``[0, source_limit]``.

    Sert à prolonger un clip (trim droit) : jusqu'où le mapping peut-il aller avant de sortir du média ? Si la source n'est
    jamais épuisée (vitesse finale nulle), la durée demandée est la seule limite.
    """
    start = clip.time_map.source_time(0.0)
    keyframes = speed_keyframes(clip)
    free = time_map_for(0.0, source_limit, replace(clip.time_remapping, anchor=start, duration=None), keyframes)
    if isinstance(free, PiecewiseTimeMap) and free.underdetermined:
        free = time_map_for(0.0, source_limit, replace(clip.time_remapping, anchor=start, duration=new_duration), keyframes)
    return free


__all__ = ["MIN_WINDOW", "TOLERANCE", "Restriction", "free_map", "needs_general_path", "restrict"]
