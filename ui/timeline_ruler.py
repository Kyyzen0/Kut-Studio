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
from ui.theme import active_palette, mix_colors

MINOR_TICKS = 4
"""Graduations secondaires entre deux repères numérotés (donc cinq intervalles) : discrètes, sans libellé."""
MINOR_MIN_SPACING = 48
"""Écart minimal (px) entre deux repères numérotés pour que les graduations secondaires aient un sens : en dessous, du bruit."""
BEAT_MIN_SPACING = 6
"""Écart minimal (px) entre deux temps de la grille rythmique : en dessous, seules les mesures sont dessinées."""


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
        self.beat_grid = None               # grille rythmique de la séquence (:class:`core.beat_grid.BeatGrid`)
        self._dragging = False
        # Valeurs de repli (avant le premier ``sync``) : celles du thème actif, jamais des couleurs en dur.
        palette = active_palette()
        self._background = palette.ruler_bg
        self._tick = palette.ruler_line
        self._text = palette.muted
        self._playhead = palette.playhead
        self._marker = palette.marker

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
        beat_grid=None,
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
        self.beat_grid = beat_grid
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
        # La zone avant ``origin`` recouvre les en-têtes de pistes : aucun
        # repère n'y a de sens (le temps négatif s'y afficherait « 00:00 »).
        start = max(0.0, (self.scroll_x - self.origin) / scale)
        end = max(start, (self.scroll_x + self.width() - self.origin) / scale)
        ticks = ruler_ticks(start, end, scale, self.fps)
        # Graduations secondaires : plus courtes et plus pâles que les repères numérotés, seulement quand l'écart les justifie.
        painter.setPen(QPen(QColor(mix_colors(self._tick, self._background, 0.5)), 1))
        for left, right in zip(ticks, ticks[1:]):
            left_x, right_x = self._x_of(left.seconds), self._x_of(right.seconds)
            if right_x - left_x < MINOR_MIN_SPACING or right_x < -40 or left_x > self.width() + 40:
                continue
            for index in range(1, MINOR_TICKS + 1):
                x = int(left_x + (right_x - left_x) * index / (MINOR_TICKS + 1))
                painter.drawLine(x, self.height() - 4, x, self.height() - 1)
        painter.setPen(QPen(QColor(self._tick), 1))
        for tick in ticks:
            x = int(self._x_of(tick.seconds))
            if x < -40 or x > self.width() + 40:
                continue
            painter.drawLine(x, self.height() - 8, x, self.height() - 1)
            painter.setPen(QPen(QColor(self._text), 1))
            painter.drawText(x + 4, 14, tick.label)
            painter.setPen(QPen(QColor(self._tick), 1))
        self._paint_beats(painter, start, end, scale)
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
        metrics = painter.fontMetrics()
        text_w = metrics.horizontalAdvance(label)
        text_x = min(max(4, play_x + 8), max(4, self.width() - text_w - 8))
        # Fond opaque derrière le timecode : sans lui, il se superpose au
        # libellé de la graduation voisine et devient illisible.
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(self._background))
        painter.drawRect(text_x - 3, 0, text_w + 6, metrics.height() + 2)
        painter.setPen(QPen(QColor(self._playhead), 1))
        painter.drawText(text_x, metrics.ascent() + 1, label)
        painter.end()

    def _paint_beats(self, painter: QPainter, start: float, end: float, scale: float) -> None:
        """Temps de la grille rythmique (traits courts) et mesures (traits plus longs, couleur des repères)."""
        grid = self.beat_grid
        if grid is None:
            return
        every = 1 if grid.beat * scale >= BEAT_MIN_SPACING else grid.beats_per_bar
        if grid.beat * every * scale < BEAT_MIN_SPACING:
            return
        bar_pen = QPen(QColor(mix_colors(self._marker, self._background, 0.75)), 1)
        beat_pen = QPen(QColor(mix_colors(self._tick, self._background, 0.45)), 1)
        for t in grid.beat_times(start, end, every=every):
            x = int(self._x_of(t))
            downbeat = grid.is_downbeat(round(grid.index_at(t)))
            painter.setPen(bar_pen if downbeat else beat_pen)
            painter.drawLine(x, 17, x, 23 if downbeat else 20)

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
