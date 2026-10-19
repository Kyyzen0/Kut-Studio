"""Préréglages du temps d'un clip, et copier / coller de son remappage.

Un préréglage n'a **aucune logique propre** : il compose les opérations d'édition du temps (:mod:`core.time_ops`,
:func:`core.timeline_operations.set_clip_speed`), les mêmes que le menu, l'inspecteur et le Graph Editor. « Ralenti 25 % » et une
rampe Bézier posée à la main produisent donc le même objet : une vitesse statique ou des keyframes ``time.speed``. Rien n'est
caché dans le préréglage, tout se modifie ensuite point par point.

``SLOW_50`` / ``SLOW_25`` / ``FAST_2`` / ``FAST_4``  vitesse constante (remplace une courbe existante) ;
``RAMP_IN``   départ au quart de la vitesse du clip, retour à la vitesse du clip en 1 s (au plus la moitié du clip) ;
``RAMP_OUT``  vitesse du clip, puis chute au quart pendant la dernière seconde ; la source qui reste est lue lentement ;
``FREEZE``    arrêt sur image de 1 s à la tête de lecture, dans la courbe (:func:`core.time_ops.insert_hold`).

Les rampes partent de la vitesse **statique** du clip : appliquer un préréglage remplace une courbe existante, comme
« Réinitialiser la courbe » le ferait.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum

from .animation import InterpolationType, Keyframe
from .project_model import Clip, Project
from .time_ops import (
    HOLD_SECONDS,
    RippleMode,
    _editable_clip,
    _transaction,
    add_speed_point,
    clear_speed_curve,
    ensure_interpolation_possible,
    insert_hold,
    speed_points,
)
from .time_remapping import TimeRemapping
from .timeline_operations import _clear_speed_keyframes, _restore_extent, set_clip_speed

RAMP_SECONDS = 1.0
"""Durée d'une rampe de préréglage (au plus la moitié du clip)."""
RAMP_FACTOR = 0.25
"""Vitesse de la partie lente d'une rampe, en part de la vitesse du clip."""


class TimePreset(str, Enum):
    SLOW_50 = "slow_50"
    SLOW_25 = "slow_25"
    FAST_2 = "fast_2"
    FAST_4 = "fast_4"
    RAMP_IN = "ramp_in"
    RAMP_OUT = "ramp_out"
    FREEZE = "freeze"


CONSTANT_SPEEDS: dict[TimePreset, float] = {
    TimePreset.SLOW_50: 0.5,
    TimePreset.SLOW_25: 0.25,
    TimePreset.FAST_2: 2.0,
    TimePreset.FAST_4: 4.0,
}


def apply_preset(
    project: Project,
    clip_id: str,
    preset: TimePreset | str,
    *,
    local_time: float = 0.0,
    fps: float | None = None,
    mode: RippleMode = RippleMode.SOURCE,
) -> None:
    """Applique ``preset`` au clip. ``local_time`` (temps local du clip) ne sert qu'à ``FREEZE``."""
    preset = TimePreset(preset)
    clip = _editable_clip(project, clip_id)
    if preset in CONSTANT_SPEEDS:
        set_clip_speed(project, clip_id, CONSTANT_SPEEDS[preset])
        return
    if preset is TimePreset.FREEZE:
        insert_hold(project, clip_id, local_time, HOLD_SECONDS, fps=fps, mode=mode)
        return
    clear_speed_curve(project, clip_id)
    speed = clip.time_remapping.speed
    duration = clip.duration
    length = min(RAMP_SECONDS, duration / 2.0)
    slow = max(speed * RAMP_FACTOR, 0.0)
    if preset is TimePreset.RAMP_IN:
        add_speed_point(project, clip_id, 0.0, slow, interpolation=InterpolationType.EASE_IN_OUT, mode=mode)
        add_speed_point(project, clip_id, length, speed, interpolation=InterpolationType.LINEAR, mode=mode)
    else:
        add_speed_point(project, clip_id, duration - length, speed, interpolation=InterpolationType.EASE_IN_OUT, mode=mode)
        add_speed_point(project, clip_id, duration, slow, interpolation=InterpolationType.LINEAR, mode=mode)


@dataclass(frozen=True)
class TimeSnapshot:
    """Le temps d'un clip, copiable : réglages de remappage et points de vitesse (en temps local du clip).

    La fenêtre source (``source_in`` / ``source_out``), l'ancre et la durée imposée n'en font pas partie : ce sont ce que le
    clip montre, pas comment il le joue. Coller sur un autre clip garde sa portion de média (« conserver la plage source ») ;
    sa durée sur la timeline change si la courbe change.
    """

    remapping: TimeRemapping
    points: tuple[Keyframe, ...]


def copy_time(clip: Clip) -> TimeSnapshot:
    """Instantané du temps de ``clip``. Un arrêt sur image n'a pas de temps à copier."""
    from .time_remapping import FreezeFrameMode

    if clip.time_remapping.freeze_mode == FreezeFrameMode.FREEZE:
        raise ValueError("Un arrêt sur image n'a pas de vitesse à copier.")
    return TimeSnapshot(replace(clip.time_remapping, anchor=None, duration=None), tuple(speed_points(clip)))


def paste_time(project: Project, clip_id: str, snapshot: TimeSnapshot, *, mode: RippleMode = RippleMode.SOURCE) -> None:
    """Applique ``snapshot`` à ``clip_id`` : ses réglages et sa courbe remplacent ceux du clip (transactionnel)."""
    clip = _editable_clip(project, clip_id)
    ensure_interpolation_possible(clip, snapshot.remapping.interpolation)    # même garde que le réglage direct : refusé ici, pas à l'export
    with _transaction(project, clip, mode):
        _restore_extent(clip)                                   # ce que le clip montrait devient sa fenêtre source
        _clear_speed_keyframes(clip)
        clip.time_remapping = replace(snapshot.remapping, anchor=None, duration=None)
        if snapshot.points:
            from .time_targets import SPEED_TARGET

            copies = [replace(point, id="") for point in snapshot.points]
            SPEED_TARGET.set_keyframes(clip, [*SPEED_TARGET.get_keyframes(clip), *copies])


__all__ = [
    "CONSTANT_SPEEDS",
    "RAMP_FACTOR",
    "RAMP_SECONDS",
    "TimePreset",
    "TimeSnapshot",
    "apply_preset",
    "copy_time",
    "paste_time",
]
