"""Les commandes du temps d'un clip, de l'interface au cœur : un seul chemin pour le menu, l'inspecteur et les raccourcis.

L'interface ne connaît pas les opérations d'édition : elle émet ``(clip, commande, argument)`` et la fenêtre appelle
:func:`apply_time_command`, qui exécute l'opération de cœur correspondante et retourne la **clé du libellé d'historique**
(une entrée annulable par geste). « Vitesse → 25 % », le champ de vitesse de l'inspecteur et un point posé dans le Graph Editor
passent par les mêmes fonctions (:mod:`core.timeline_operations`, :mod:`core.time_ops`, :mod:`core.time_presets`) : un seul
moteur, que le geste soit simple ou avancé.

Ce module est pur (aucun Qt) : toutes les commandes se testent sans interface.
"""

from __future__ import annotations

from enum import Enum

from . import keyframe_editing as keyframes
from .project_model import Project
from .time_ops import (
    RippleMode,
    add_speed_point,
    clear_speed_curve,
    insert_hold,
    set_clip_interpolation,
    set_clip_preserve_pitch,
    set_clip_remap_audio,
)
from .time_presets import TimePreset, apply_preset
from .timeline_operations import find_clip, reset_clip_time_remapping, set_clip_reverse, set_clip_speed

SPEED_CHOICES = (0.25, 0.5, 1.0, 2.0, 4.0)
"""Vitesses du menu « Vitesse » (25 %, 50 %, 100 %, 200 %, 400 %)."""


class TimeCommand(str, Enum):
    SPEED = "speed"
    REVERSE = "reverse"
    INTERPOLATION = "interpolation"
    QUALITY = "quality"
    PRESERVE_PITCH = "preserve_pitch"
    REMAP_AUDIO = "remap_audio"
    PRESET = "preset"
    ADD_POINT = "add_point"
    HOLD = "hold"
    CLEAR_CURVE = "clear_curve"
    RESET = "reset"


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f"Une valeur numérique est attendue (reçu : {value!r}).")
    return float(value)


def apply_time_command(
    project: Project,
    clip_id: str,
    command: TimeCommand | str,
    argument: object = None,
    *,
    local_time: float = 0.0,
    fps: float | None = None,
    mode: RippleMode = RippleMode.SOURCE,
) -> str:
    """Exécute ``command`` sur le clip ; retourne la clé du libellé d'historique de ce geste.

    Raises:
        KeyError: clip introuvable.
        ValueError: opération refusée (piste verrouillée, clip figé, valeur invalide, durée dégénérée) ; le clip est alors
            laissé tel qu'il était.
    """
    command = TimeCommand(command)
    if command is TimeCommand.SPEED:
        set_clip_speed(project, clip_id, _number(argument))
        return "history.speed.edit"
    if command is TimeCommand.REVERSE:
        enabled = bool(argument)
        set_clip_reverse(project, clip_id, enabled)
        return "history.clip.reverse" if enabled else "history.clip.unreverse"
    if command is TimeCommand.INTERPOLATION:
        set_clip_interpolation(project, clip_id, str(argument))
        return "history.time.interpolation"
    if command is TimeCommand.QUALITY:
        clip = find_clip(project, clip_id)
        set_clip_interpolation(project, clip_id, clip.time_remapping.interpolation, str(argument))
        return "history.time.quality"
    if command is TimeCommand.PRESERVE_PITCH:
        set_clip_preserve_pitch(project, clip_id, bool(argument))
        return "history.time.pitch"
    if command is TimeCommand.REMAP_AUDIO:
        set_clip_remap_audio(project, clip_id, bool(argument))
        return "history.time.audio"
    if command is TimeCommand.PRESET:
        apply_preset(project, clip_id, TimePreset(str(argument)), local_time=local_time, fps=fps, mode=mode)
        return f"history.time.preset.{TimePreset(str(argument)).value}"
    if command is TimeCommand.ADD_POINT:
        clip = find_clip(project, clip_id)
        moment = keyframes.snap_to_frame(clip, local_time, fps) if fps else local_time
        add_speed_point(project, clip_id, moment, None, mode=mode)
        return "history.time.add_point"
    if command is TimeCommand.HOLD:
        insert_hold(project, clip_id, local_time, fps=fps, mode=mode)
        return "history.time.hold"
    if command is TimeCommand.CLEAR_CURVE:
        clear_speed_curve(project, clip_id)
        return "history.time.clear_curve"
    reset_clip_time_remapping(project, clip_id)
    return "history.time.reset"


__all__ = ["SPEED_CHOICES", "TimeCommand", "apply_time_command"]
