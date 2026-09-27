"""Règle temporelle de la timeline.

Elle suit le défilement horizontal sans faire partie du contenu
défilant : les libellés restent nets. Un glisser déplace le playhead
sans reconstruire les clips. Le temps sous le curseur ne change pas
quand on zoome : c'est le scroll qui compense, pas la tête de lecture.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from core.project_model import Marker
from core.timeline_navigation import format_timecode, ruler_ticks


class TimelineRuler(QWidget):
    """Bandeau de timecode, marqueurs et tête de lecture."""

    seek_requested = Signal(float)
    marker_rename_requested = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedHeight(32)
        self.setMouseTracking(True)
        self.scroll_x = 0.0
        self.zoom = 1.0
        self.pixels_per_second = 120.0
        self.duration = 1.0
        self.fps = 30.0
        self.playhead = 0.0
        self.origin = 200.0
        self.markers: list[Marker] = []
        self._dragging = False
        self._background = "#1a1d24"
        self._tick = "#8b93a7"
        self._text = "#c6c9d2"
        self._playhead = "#ff5a36"
        self._marker = "#f2c14e"

    def sync(
        self,
        *,
        scroll_x: float,
        zoom: float,
        pixels_per_second: float,
        duration: float,
        fps: float,
        playhead: float,
        origin: float,
        markers: list[Marker],
        background: str,
        tick: str,
        text: str,
        playhead_color: str,
        marker_color: str,
    ) -> None:
        self.scroll_x = scroll_x
        self.zoom = zoom
        self.pixels_per_second = pixels_per_second
        self.duration = duration
        self.fps = fps
        self.playhead = playhead
        self.origin = origin
        self.markers = list(markers)
        self._background = background
        self._tick = tick
        self._text = text
        self._playhead = playhead_color
        self._marker = marker_color
        self.update()

    def _time_at(self, viewport_x: float) -> float:
        scale = self.pixels_per_second * self.zoom
        if scale <= 0:
            return 0.0
        instant = (self.scroll_x + viewport_x - self.origin) / scale
        return max(0.0, min(float(self.duration), instant))

    def _x_of(self, seconds: float) -> float:
        return self.origin + seconds * self.pixels_per_second * self.zoom - self.scroll_x

    def paintEvent(self, event) -> None:  # noqa: D401 - Qt
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(self._background))
        scale = self.pixels_per_second * self.zoom
        if scale <= 0:
            painter.end()
            return
        start = (self.scroll_x - self.origin) / scale
        end = (self.scroll_x + self.width() - self.origin) / scale
        painter.setPen(QPen(QColor(self._tick), 1))
        for tick in ruler_ticks(start, end, scale, self.fps):
            x = int(self._x_of(tick.seconds))
            if x < -40 or x > self.width() + 40:
                continue
            painter.drawLine(x, self.height() - 8, x, self.height() - 1)
            painter.setPen(QPen(QColor(self._text), 1))
            painter.drawText(x + 4, 14, tick.label)
            painter.setPen(QPen(QColor(self._tick), 1))
        for marker in self.markers:
            x = int(self._x_of(marker.time_seconds))
            if x < -8 or x > self.width() + 8:
                continue
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(self._marker))
            painter.drawPolygon(
                [
                    self._point(x, 16),
                    self._point(x - 5, 26),
                    self._point(x + 5, 26),
                ]
            )
        play_x = int(self._x_of(self.playhead))
        painter.setPen(QPen(QColor(self._playhead), 2))
        painter.drawLine(play_x, 0, play_x, self.height())
        painter.setBrush(QColor(self._playhead))
        painter.drawPolygon(
            [
                self._point(play_x - 6, 0),
                self._point(play_x + 6, 0),
                self._point(play_x, 10),
            ]
        )
        label = format_timecode(self.playhead, self.fps)
        painter.setPen(QPen(QColor(self._playhead), 1))
        text_x = min(max(4, play_x + 8), max(4, self.width() - 78))
        painter.drawText(text_x, 12, label)
        painter.end()

    @staticmethod
    def _point(x: int, y: int):
        from PySide6.QtCore import QPoint

        return QPoint(x, y)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._dragging = True
            self.seek_requested.emit(self._time_at(event.position().x()))
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._dragging:
            self.seek_requested.emit(self._time_at(event.position().x()))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._dragging = False
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        instant = self._time_at(event.position().x())
        nearest = None
        best = 8.0
        for marker in self.markers:
            distance = abs(self._x_of(marker.time_seconds) - event.position().x())
            if distance <= best:
                nearest = marker
                best = distance
        if nearest is not None:
            self.marker_rename_requested.emit(nearest.id)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)
