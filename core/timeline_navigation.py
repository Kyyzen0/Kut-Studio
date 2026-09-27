"""Navigation temporelle de la timeline.

Le zoom, le cadrage et la règle sont des calculs purs : l'interface
s'en sert pour changer le scroll sans modifier le temps du playhead.
Un zoom ne doit jamais déplacer la tête de lecture.

La graduation choisit un pas pour qu'un libellé occupe environ 90 pixels.
À fort zoom le pas descend jusqu'à l'image. À faible zoom il passe
aux secondes, puis aux minutes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


MIN_ZOOM = 0.05
MAX_ZOOM = 48.0


def clamp_zoom(zoom: float) -> float:
    """Borne un facteur de zoom."""
    return max(MIN_ZOOM, min(MAX_ZOOM, float(zoom)))


def scroll_for_anchor(
    old_zoom: float,
    new_zoom: float,
    anchor_in_viewport: float,
    scroll_x: float,
    origin: float,
    pixels_per_second: float,
) -> int:
    """Nouveau scroll pour que l'instant sous ``anchor_in_viewport`` reste fixe.

    ``anchor_in_viewport`` est une position en pixels dans la zone visible,
    pas dans le contenu défilant. ``origin`` est la marge gauche du temps 0.
    """
    old_scale = pixels_per_second * old_zoom
    if old_scale <= 0.0:
        return max(0, int(scroll_x))
    instant = (scroll_x + anchor_in_viewport - origin) / old_scale
    new_scroll = instant * pixels_per_second * new_zoom + origin - anchor_in_viewport
    return max(0, int(round(new_scroll)))


def fit_zoom(
    duration_seconds: float,
    viewport_width: float,
    origin: float,
    pixels_per_second: float,
) -> float:
    """Zoom qui fait tenir ``duration_seconds`` dans la zone visible."""
    usable = max(1.0, float(viewport_width) - float(origin) - 24.0)
    if duration_seconds <= 0.0 or pixels_per_second <= 0.0:
        return 1.0
    return clamp_zoom(usable / (duration_seconds * pixels_per_second))


def step_frames(seconds: float, frames: int, fps: float) -> float:
    """Avance ou recule d'un nombre entier d'images.

    Le temps résultant est calé sur la grille d'images du projet.
    ``fps`` invalide retombe sur 30 pour rester déterministe.
    """
    rate = int(round(fps)) if fps and fps > 0.0 else 30
    rate = max(1, rate)
    index = int(round(float(seconds) * rate))
    return max(0.0, (index + int(frames)) / rate)


def format_timecode(seconds: float, fps: float, *, with_frames: bool = True) -> str:
    """Timecode ``HH:MM:SS:FF`` ou ``MM:SS`` si les images sont omises."""
    rate = int(round(fps)) if fps and fps > 0.0 else 30
    rate = max(1, rate)
    total_frames = max(0, int(round(float(seconds) * rate)))
    frames = total_frames % rate
    whole = total_frames // rate
    hours, remainder = divmod(whole, 3600)
    minutes, secs = divmod(remainder, 60)
    if with_frames:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}:{frames:02d}"
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def format_clock(seconds: float) -> str:
    """Horloge compacte ``MM:SS`` ou ``H:MM:SS``, utilisée par les compteurs."""
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


@dataclass(frozen=True)
class RulerTick:
    """Un repère de la règle temporelle."""

    seconds: float
    label: str
    major: bool


def ruler_ticks(
    start_seconds: float,
    end_seconds: float,
    pixels_per_second: float,
    fps: float,
) -> list[RulerTick]:
    """Repères lisibles entre ``start_seconds`` et ``end_seconds``.

    Le pas est le plus petit cran standard qui laisse environ 90 pixels
    entre deux libellés. La liste est plafonnée pour qu'un zoom extrême
    ne fabrique pas des milliers de textes.
    """
    if end_seconds < start_seconds:
        start_seconds, end_seconds = end_seconds, start_seconds
    span = max(end_seconds - start_seconds, 1e-6)
    scale = max(pixels_per_second, 1e-6)
    width_px = span * scale
    desired = max(width_px / 90.0, 1.0)
    raw_step = span / desired
    rate = max(1, int(round(fps)) if fps and fps > 0 else 30)
    frame = 1.0 / rate
    steps = [
        frame,
        frame * 2,
        frame * 5,
        1.0,
        2.0,
        5.0,
        10.0,
        15.0,
        30.0,
        60.0,
        120.0,
        300.0,
        600.0,
        1800.0,
        3600.0,
    ]
    step = steps[-1]
    for candidate in steps:
        if candidate + 1e-9 >= raw_step:
            step = candidate
            break
    first = math.floor(start_seconds / step) * step
    ticks: list[RulerTick] = []
    instant = first
    for _ in range(240):
        if instant > end_seconds + step:
            break
        if instant >= start_seconds - 1e-6:
            show_frames = step < 0.999
            ticks.append(
                RulerTick(
                    seconds=instant,
                    label=format_timecode(max(0.0, instant), fps, with_frames=show_frames),
                    major=True,
                )
            )
        instant += step
    return ticks
