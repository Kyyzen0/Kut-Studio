"""Tracé des repères posés sur l'image : un halo sombre sous chaque trait, pour qu'il se lise sur une image claire comme sombre.

Un trait cyan ou blanc de 1 px disparaît sur un ciel ou une neige. ``halo_stroke`` dessine d'abord le trait en sombre, légèrement plus
épais et translucide, puis le trait lui-même par-dessus : la couleur reste celle du rôle (voir :data:`ui.theme.OVERLAY`), le contour
sombre fait le contraste quand l'image est claire et se fond dans une image sombre.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPen

from ui.theme import OVERLAY, overlay_qcolor

HALO_ALPHA = 150
"""Opacité (0-255) du halo sombre."""
HALO_EXTRA = 2.0
"""Épaisseur ajoutée au halo par rapport au trait (px) : un liseré d'un pixel de chaque côté."""


def halo_stroke(
    painter: QPainter, color: QColor, width: float, draw: Callable[[], None], *, style: Qt.PenStyle = Qt.SolidLine,
) -> None:
    """Appelle ``draw()`` deux fois : avec un halo sombre, puis avec ``color`` (le pinceau de remplissage actuel est conservé)."""
    painter.setPen(QPen(overlay_qcolor(OVERLAY.halo, HALO_ALPHA), width + HALO_EXTRA, Qt.SolidLine))
    draw()
    painter.setPen(QPen(color, width, style))
    draw()


__all__ = ["HALO_ALPHA", "HALO_EXTRA", "halo_stroke"]
