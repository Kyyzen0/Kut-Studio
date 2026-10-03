"""Propriété animable « vitesse » d'un clip (``time.speed``).

Importé par :mod:`core.animation_targets` : dès qu'un consommateur (inspecteur, timeline, Graph Editor) ouvre le registre,
la vitesse y figure comme n'importe quelle autre propriété. Ses keyframes vivent dans ``Clip.animation`` (déjà sérialisé),
en **temps local du clip**, comme tous les keyframes : couper un clip sépare donc la courbe de vitesse sans changer
l'animation temporelle des deux moitiés (:func:`core.animation_targets.split_animation`).

La valeur de la propriété est la vitesse **avant** application du sens : ``reverse`` inverse toute la courbe. Elle peut
être négative (la lecture repart en arrière) et nulle (arrêt). La valeur **statique** est ``TimeRemapping.speed``, lue
tant qu'il n'y a pas de keyframe. Le mapping qui en résulte (intégrale, durée, retournements) est dans
:mod:`core.time_map`.
"""

from __future__ import annotations

from dataclasses import replace

from .animation import AnimatableProperty, ValueKind
from .animation_targets import generic_target, register_target
from .time_map import SPEED_LIMIT, SPEED_PROPERTY
from .time_remapping import MAX_SPEED, MIN_SPEED, FreezeFrameMode

SPEED_SPEC = AnimatableProperty(
    SPEED_PROPERTY, "time.property.speed", ValueKind.FLOAT,
    default=1.0, minimum=-SPEED_LIMIT, maximum=SPEED_LIMIT, step=0.05, group="time",
)


def _applies_to_track(track_type: str) -> bool:
    return track_type in {"video", "audio"}


def _applies_to_clip(clip) -> bool:
    return clip.time_remapping.freeze_mode != FreezeFrameMode.FREEZE


def _get_static(clip) -> float:
    return float(clip.time_remapping.speed)


def _set_static(clip, value) -> None:
    """Vitesse statique : toujours positive (le sens est ``reverse``), bornée comme ``TimeRemapping``."""
    magnitude = min(max(abs(float(value)), MIN_SPEED), MAX_SPEED)
    clip.time_remapping = replace(clip.time_remapping, speed=magnitude)


SPEED_TARGET = generic_target(
    SPEED_SPEC, get_static=_get_static, set_static=_set_static,
    applies_to=_applies_to_track, applies_to_clip=_applies_to_clip,
)
register_target(SPEED_TARGET)
