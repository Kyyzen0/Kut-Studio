"""Éditeur de courbe de couleur."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPainter, QColor, QPainterPath, QPen
from PySide6.QtCore import QPointF, QRectF
from PySide6.QtWidgets import (
    QWidget,
)

from core.color_grading import (
    CHANNELS,
    ColorCurve,
)
from ui.theme import COLORS


class ColorCurveEditor(QWidget):
    """Éditeur compact de courbe à points, sans dépendance externe."""

    points_changed = Signal(str, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.channel = "master"
        self._curve = ColorCurve.identity()
        self._drag_index: int | None = None
        self.setMinimumHeight(155)
        self.setMinimumWidth(190)
        self.setCursor(Qt.CrossCursor)

    def set_curve(self, channel: str, curve: ColorCurve) -> None:
        self.channel = channel if channel in CHANNELS else "master"
        self._curve = curve if isinstance(curve, ColorCurve) else ColorCurve.identity()
        self._drag_index = None
        self.update()

    @property
    def curve(self) -> ColorCurve:
        return self._curve

    def _plot_rect(self) -> QRectF:
        return QRectF(10.0, 10.0, max(1.0, self.width() - 20.0), max(1.0, self.height() - 20.0))

    def _point_pos(self, point: tuple[float, float]) -> QPointF:
        rect = self._plot_rect()
        return QPointF(rect.left() + point[0] * rect.width(), rect.bottom() - point[1] * rect.height())

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self._plot_rect()
        painter.fillRect(rect, QColor(COLORS["panel_alt"]))
        painter.setPen(QPen(QColor(COLORS["border"]), 1))
        for index in range(1, 4):
            x = rect.left() + rect.width() * index / 4.0
            y = rect.top() + rect.height() * index / 4.0
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
        path = QPainterPath()
        first = self._point_pos(self._curve.points[0])
        path.moveTo(first)
        for point in self._curve.points[1:]:
            path.lineTo(self._point_pos(point))
        channel_colors = {
            "master": COLORS["accent"],
            "red": "#ef4444",
            "green": "#22c55e",
            "blue": "#3b82f6",
        }
        painter.setPen(QPen(QColor(channel_colors[self.channel]), 2))
        painter.drawPath(path)
        painter.setBrush(QColor(channel_colors[self.channel]))
        painter.setPen(QPen(QColor(COLORS["text"]), 1))
        for point in self._curve.points:
            painter.drawEllipse(self._point_pos(point), 3.5, 3.5)

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return
        pos = event.position()
        distances = [
            (self._point_pos(point) - pos).manhattanLength()
            for point in self._curve.points
        ]
        self._drag_index = min(range(len(distances)), key=distances.__getitem__)
        self._update_dragged_point(pos, emit=False)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_index is not None:
            self._update_dragged_point(event.position(), emit=False)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self._drag_index is not None:
            self._update_dragged_point(event.position(), emit=True)
            self._drag_index = None

    def _update_dragged_point(self, pos: QPointF, *, emit: bool) -> None:
        if self._drag_index is None:
            return
        rect = self._plot_rect()
        output = 1.0 - (pos.y() - rect.top()) / rect.height()
        output = max(0.0, min(1.0, output))
        points = list(self._curve.points)
        x = points[self._drag_index][0]
        points[self._drag_index] = (x, output)
        self._curve = ColorCurve(points=tuple(points))
        self.update()
        if emit:
            self.points_changed.emit(self.channel, self._curve.points)
