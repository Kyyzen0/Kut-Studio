"""Effets d'impact posés en images-clés : zoom d'impact, secousse de caméra, flash blanc, et « sur chaque cut ».

Rien ici n'est un filtre : un zoom d'impact est une courbe d'échelle, une secousse des courbes de position et de
rotation, un flash un calque de lumière (``flash``) dont l'opacité retombe. L'aperçu, le moniteur GPU et l'export les
lisent comme toute animation, et l'utilisateur les retouche ensuite.

Valeurs par défaut reprises de l'edit F1 : un plan entre zoomé de 8 % (14 % sur les plans les plus forts) et revient
à 100 % en 0,3 s ; un flash dure 0,18 s.
"""

from __future__ import annotations

import hashlib
import math
import random

from .animation import InterpolationType
from .project_model import Clip, Project
from .timeline_operations import find_track
from .visual_effects import TransformKeyframe

DEFAULT_ZOOM = 0.08
ZOOM_SECONDS = 0.3
FLASH_SECONDS = 0.18
SHAKE_SECONDS = 0.45
SHAKE_FREQUENCY = 15.0


def _replace(clip: Clip, frames: list[TransformKeyframe], properties: set[str], start: float, end: float) -> None:
    """Remplace les images-clés de ``properties`` comprises dans ``[start, end]`` par ``frames``."""
    kept = [kf for kf in clip.transform_keyframes
            if kf.property_name not in properties or not start - 1e-6 <= kf.time_seconds <= end + 1e-6]
    clip.transform_keyframes = sorted([*kept, *frames], key=lambda kf: (kf.property_name, kf.time_seconds))


def apply_impact_zoom(clip: Clip, *, strength: float = DEFAULT_ZOOM, start: float = 0.0,
                      length: float = ZOOM_SECONDS) -> None:
    """Le clip entre zoomé de ``strength`` (0,08 = 8 %) et se pose à son échelle en ``length`` secondes (ease-out)."""
    if clip.duration <= start:
        raise ValueError("Le clip est trop court pour un zoom d'impact.")
    base = clip.transform.scale
    end = min(clip.duration, start + length)
    frames = [TransformKeyframe("scale", start, base * (1.0 + max(0.0, strength)), InterpolationType.EASE_OUT),
              TransformKeyframe("scale", end, base, InterpolationType.LINEAR)]
    _replace(clip, frames, {"scale"}, start, end)


def apply_camera_shake(clip: Clip, *, start: float = 0.0, length: float = SHAKE_SECONDS, amplitude: float = 0.012,
                       rotation: float = 1.2, seed: int | None = None) -> None:
    """Secousse : position et rotation tirées au hasard (graine stable) toutes les 1/15 s, amorties jusqu'au repos.

    ``amplitude`` est une fraction du cadre ; une secousse découvre les bords d'un plan plein cadre si elle n'est pas
    accompagnée d'un agrandissement (le zoom d'impact en donne un)."""
    if clip.duration <= start:
        raise ValueError("Le clip est trop court pour une secousse.")
    end = min(clip.duration, start + length)
    rng = random.Random(seed if seed is not None else int(hashlib.sha1(clip.id.encode()).hexdigest()[:8], 16))
    base = clip.transform
    frames: list[TransformKeyframe] = []
    steps = max(2, int(math.ceil((end - start) * SHAKE_FREQUENCY)))
    for step in range(steps + 1):
        u = step / steps
        t = round(start + (end - start) * u, 6)
        decay = (1.0 - u) ** 2
        for name, scale in (("position_x", amplitude), ("position_y", amplitude), ("rotation", rotation)):
            value = getattr(base, name) + (rng.uniform(-1.0, 1.0) * scale * decay if 0 < step < steps else 0.0)
            frames.append(TransformKeyframe(name, t, value, InterpolationType.EASE_IN_OUT))
    _replace(clip, frames, {"position_x", "position_y", "rotation"}, start, end)


def add_flash(project: Project, at: float, *, length: float = FLASH_SECONDS, color: str = "#FFFFFF") -> Clip:
    """Flash blanc : un calque de lumière ``flash`` plein cadre dont l'opacité tombe de 1 à 0 en ``length`` s."""
    from .graphics import add_graphic_clip, update_graphic

    clip = add_graphic_clip(project, "light", timeline_start=max(0.0, at), duration=max(1.0 / project.fps, length))
    update_graphic(clip, "light_kind", "flash")
    update_graphic(clip, "fill_color", color)
    clip.label = "Flash"
    clip.transform_keyframes = [TransformKeyframe("opacity", 0.0, 1.0, InterpolationType.EASE_OUT),
                                TransformKeyframe("opacity", clip.duration, 0.0, InterpolationType.LINEAR)]
    return clip


def cut_times(project: Project, track_id: str) -> list[float]:
    """Instants des cuts d'une piste : le début de chaque clip qui en suit un autre (bord à bord ou presque)."""
    clips = sorted((clip for clip in find_track(project, track_id).clips if clip.enabled),
                   key=lambda clip: clip.timeline_start)
    times = []
    for previous, current in zip(clips, clips[1:]):
        if abs(previous.timeline_start + previous.duration - current.timeline_start) <= 0.05:
            times.append(round(current.timeline_start, 6))
    return times


def apply_impact_on_cuts(
    project: Project, track_id: str, *, zoom: float = DEFAULT_ZOOM, flash: bool = False, shake: bool = False,
) -> int:
    """À chaque cut de la piste : zoom d'impact sur le plan qui arrive (et secousse, flash si demandés).

    Retourne le nombre de cuts traités ; tout est vérifié (piste modifiable) avant la première écriture."""
    track = find_track(project, track_id)
    if track.locked:
        raise ValueError(f"La piste « {track.name} » est verrouillée.")
    times = set(cut_times(project, track_id))
    incoming = [clip for clip in track.clips if round(clip.timeline_start, 6) in times]
    for clip in incoming:
        if zoom > 0:
            apply_impact_zoom(clip, strength=zoom)
        if shake:
            apply_camera_shake(clip)
        if flash:
            add_flash(project, clip.timeline_start)
    return len(incoming)


__all__ = [
    "DEFAULT_ZOOM", "FLASH_SECONDS", "add_flash", "apply_camera_shake", "apply_impact_on_cuts", "apply_impact_zoom",
    "cut_times",
]
