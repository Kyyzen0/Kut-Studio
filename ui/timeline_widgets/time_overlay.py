"""Ce que la timeline montre du temps d'un clip : la courbe de vitesse et les badges (vitesse, sens, arrêt, images intermédiaires).

La courbe est discrète : une ligne dans le bas du clip, à l'échelle logarithmique (la ligne pointillée est 100 %, au-dessus
plus vite, en dessous plus lent ; un arrêt touche le bas). Elle ne se dessine que si le clip a une courbe : un clip à vitesse
constante reste net. Chaque point de vitesse est un **losange**, comme toute image-clé : on le sélectionne, on le glisse dans le
temps (aimantation, une entrée d'historique) et on le supprime ; sa valeur et ses tangentes se règlent dans le Graph Editor.
« Ajouter un point de vitesse » (menu, inspecteur, raccourci) en pose un à la tête de lecture.

Les fonctions de calcul (:func:`curve_samples`, :func:`badge_texts`) sont pures : elles se testent sans peindre.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF

from core.animation import AnimationCurve
from core.time_remapping import FreezeFrameMode, TimeInterpolation, TimeRemapping
from ui.i18n import translate
from ui.timeline_widgets.common import _current_palette

if TYPE_CHECKING:
    from ui.timeline_widgets.clip_widget import ClipWidget

SAMPLES = 160
"""Points maximum d'une courbe dessinée (un par 2 px d'un clip large, jamais plus)."""
_FLOOR, _CEILING = 0.1, 10.0
"""Vitesses affichées (10 % … 1000 %) : au-delà, la courbe reste aux bords."""
_BADGES = {TimeInterpolation.BLENDING: "time.badge.blending", TimeInterpolation.OPTICAL_FLOW: "time.badge.optical_flow"}
"""Clé de traduction du badge de chaque mode d'images intermédiaires (court : il tient sur un petit clip)."""


def level_of(speed: float) -> float:
    """Position verticale (0 = bas du clip, 1 = haut) d'une vitesse, à l'échelle logarithmique ; 100 % vaut 0,5."""
    if speed <= 0.0:
        return 0.0
    clamped = min(max(speed, _FLOOR), _CEILING)
    return 0.5 + 0.5 * math.log10(clamped)


def curve_samples(points, duration: float, count: int = SAMPLES) -> list[tuple[float, float]]:
    """``[(part du clip 0…1, vitesse)]`` de la courbe, à un pas régulier ; vide si le clip n'a pas de courbe ou pas de durée."""
    if not points or duration <= 0.0:
        return []
    curve = AnimationCurve(points)
    steps = max(2, int(count))
    return [(index / (steps - 1), float(curve.evaluate(duration * index / (steps - 1), 1.0))) for index in range(steps)]


def badge_texts(view) -> list[str]:
    """Textes des badges d'un clip, de droite à gauche : vitesse (ou « ~ » + vitesse moyenne si courbe), sens, arrêt ; mode d'images."""
    remapping = getattr(view, "time_remapping", None) or TimeRemapping()
    texts: list[str] = []
    points = getattr(view, "speed_points", ()) or ()
    span = max(1e-9, float(view.end) - float(view.start))
    if remapping.freeze_mode == FreezeFrameMode.FREEZE:
        texts.append("F")
    elif remapping.reverse:
        texts.append("R")
    elif points:
        texts.append(f"~{float(view.source_duration) / span:.1f}x")
    elif remapping.speed != 1.0:
        texts.append(f"{remapping.speed:.1f}x")
    if remapping.interpolation in _BADGES and remapping.freeze_mode != FreezeFrameMode.FREEZE:
        texts.append(translate(_BADGES[remapping.interpolation]))
    return texts


def paint_time_overlays(widget: ClipWidget) -> None:
    """Courbe de vitesse puis badges du clip ``widget`` (rien si le clip n'a aucun remappage)."""
    view = widget.view
    remapping = getattr(view, "time_remapping", None) or TimeRemapping()
    points = getattr(view, "speed_points", ()) or ()
    if remapping.is_normal and not points:
        return
    palette = _current_palette()
    painter = QPainter(widget)
    painter.setRenderHint(QPainter.Antialiasing)
    try:
        if points:
            _paint_curve(painter, widget, view, points, palette)
        _paint_badges(painter, widget, view, palette)
    finally:
        painter.end()


def _paint_curve(painter: QPainter, widget: ClipWidget, view, points, palette) -> None:
    width, height = float(widget.width()), float(widget.height())
    top, bottom = height * 0.55, height - 3.0
    duration = float(view.end) - float(view.start)
    samples = curve_samples(points, duration, min(SAMPLES, max(2, int(width / 2))))
    if len(samples) < 2 or bottom <= top:
        return
    ink = QColor(palette.clip_text)
    ink.setAlpha(150)
    guide = QPen(ink, 1.0, Qt.DotLine)
    guide.setColor(QColor(ink.red(), ink.green(), ink.blue(), 70))
    painter.setPen(guide)
    middle = bottom - (bottom - top) * 0.5
    painter.drawLine(QPointF(0.0, middle), QPointF(width, middle))
    line = QPolygonF([QPointF(part * width, bottom - (bottom - top) * level_of(speed)) for part, speed in samples])
    painter.setPen(QPen(ink, 1.6))
    painter.drawPolyline(line)                      # les points eux-mêmes sont des losanges (``ClipWidget._paint_keyframes``)


def _paint_badges(painter: QPainter, widget: ClipWidget, view, palette) -> None:
    texts = badge_texts(view)
    if not texts:
        return
    active_effects = [effect for effect in getattr(view, "effects", ()) if getattr(effect, "enabled", False)]
    badge_y = 26 if active_effects else 4
    badge_width, badge_height, gap = 40, 18, 5
    background = QColor(palette.clip_text_dim)
    background.setAlpha(220)
    painter.setFont(widget.font())
    metrics = painter.fontMetrics()
    right = widget.width() - 5
    for text in texts:
        width = max(badge_width, metrics.horizontalAdvance(text) + 10)
        left = right - width
        painter.setBrush(background)
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(left, badge_y, width, badge_height, 4, 4)
        painter.setPen(QColor(palette.clip_text))
        painter.drawText(int(left + (width - metrics.horizontalAdvance(text)) / 2), int(badge_y + (badge_height + metrics.height()) / 2 - 2), text)
        right = left - gap
