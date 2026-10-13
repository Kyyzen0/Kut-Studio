"""Alignement d'un calque sur le cadre et déplacement au pixel (fonctions pures, sans Qt).

Tout est exprimé dans le repère du cadre de la séquence (pixels, origine en haut à gauche), comme
:func:`core.mograph_scene.map_box`. Le moniteur convertit ensuite ce décalage écran en variation de
``position_x`` / ``position_y`` du clip avec :func:`screen_delta_to_position_delta`, qui tient compte
de la matrice du parent (même formule que le glisser du moniteur).
"""

from __future__ import annotations

from enum import Enum

Box = tuple[float, float, float, float]
Matrix = tuple[float, float, float, float, float, float]


class FrameEdge(str, Enum):
    """Les six alignements sur le cadre."""

    LEFT = "left"
    CENTER_H = "center_h"
    RIGHT = "right"
    TOP = "top"
    MIDDLE_V = "middle_v"
    BOTTOM = "bottom"


def align_to_frame(
    box_bounds: Box, canvas_size: tuple[float, float], edge: FrameEdge | str
) -> tuple[float, float]:
    """Décalage écran ``(dx, dy)`` (pixels) qui aligne ``box_bounds`` sur ``edge`` du cadre.

    ``box_bounds`` est la boîte englobante ``(x0, y0, x1, y1)`` du calque (rotation comprise). Un
    seul des deux axes est modifié ; un calque plus grand que le cadre est tout de même collé au bord
    demandé (il dépasse alors de l'autre côté). ``ValueError`` si ``edge`` est inconnu.
    """
    edge = FrameEdge(edge)
    x0, y0, x1, y1 = box_bounds
    width, height = float(canvas_size[0]), float(canvas_size[1])
    if edge is FrameEdge.LEFT:
        return (0.0 - x0, 0.0)
    if edge is FrameEdge.RIGHT:
        return (width - x1, 0.0)
    if edge is FrameEdge.CENTER_H:
        return (width / 2.0 - (x0 + x1) / 2.0, 0.0)
    if edge is FrameEdge.TOP:
        return (0.0, 0.0 - y0)
    if edge is FrameEdge.BOTTOM:
        return (0.0, height - y1)
    return (0.0, height / 2.0 - (y0 + y1) / 2.0)


def screen_delta_to_position_delta(
    parent_world: Matrix, dx: float, dy: float, canvas_size: tuple[float, float]
) -> tuple[float, float] | None:
    """Variation de ``(position_x, position_y)`` (fractions du cadre) qui déplace de ``(dx, dy)`` pixels écran.

    ``parent_world`` est la matrice parent → cadre (partie linéaire utilisée seulement). ``None`` si
    elle n'est pas inversible (déterminant nul) : aucun déplacement n'est alors possible.
    """
    a, b, c, d = parent_world[0], parent_world[1], parent_world[2], parent_world[3]
    det = a * d - b * c
    if abs(det) < 1e-12:
        return None
    px = (d * dx - c * dy) / det
    py = (-b * dx + a * dy) / det
    return px / float(canvas_size[0]), py / float(canvas_size[1])


__all__ = ["FrameEdge", "align_to_frame", "screen_delta_to_position_delta"]
