"""Surface portant les pistes (bandes, filets, playhead)."""

from __future__ import annotations


from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QWidget,
)

from ui.timeline_widgets.common import _CONTENT_TOP, _current_palette

# ---------------------------------------------------------------------------
# Grille de pistes
# ---------------------------------------------------------------------------


class _TrackGrid(QWidget):
    """Surface portant les pistes.

    Peint les bandes de piste en alternance et un filet de séparation
    sous chaque piste. Le rendu se fait ici — et non dans
    :class:`TimelinePanel` — pour rester synchrone avec le défilement
    vertical et horizontal de la zone.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.track_count = 0
        self.track_height = 0
        self.track_gap = 0
        self.ruler_height = 0
        self.left_margin = 0
        self.lanes: list[tuple[int, int]] = []
        self.playhead_x: float | None = None
        self.snap_x: float | None = None
        self.host = None

    def configure(
        self,
        *,
        track_count: int,
        track_height: int,
        track_gap: int,
        ruler_height: int,
        left_margin: int,
        lanes: list[tuple[int, int]] | None = None,
    ) -> None:
        self.track_count = max(0, track_count)
        self.track_height = track_height
        self.track_gap = track_gap
        self.ruler_height = ruler_height
        self.left_margin = left_margin
        if lanes is not None:
            self.lanes = lanes
        self.update()

    def paintEvent(self, event) -> None:  # noqa: D401 - Qt
        palette = _current_palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor(palette.timeline_grid))

        base = palette.timeline_grid
        alt = palette.track_alt_bg
        divider = palette.track_divider
        pitch = self.track_height + self.track_gap
        first_row = _CONTENT_TOP
        lane_left = self.left_margin
        lanes = self.lanes or [
            (first_row + index * pitch, self.track_height)
            for index in range(self.track_count)
        ]

        for index, (top, height) in enumerate(lanes):
            if top > self.height():
                break
            if index % 2 == 1:
                painter.fillRect(
                    lane_left,
                    int(top),
                    self.width() - lane_left,
                    int(height),
                    QColor(alt),
                )
            painter.setPen(QPen(QColor(divider), 1))
            line_y = int(top + height)
            painter.drawLine(lane_left, line_y, self.width(), line_y)
        if self.snap_x is not None:
            painter.setPen(QPen(QColor(palette.snap_line), 1))
            painter.drawLine(int(self.snap_x), 0, int(self.snap_x), self.height())
        if self.playhead_x is not None:
            painter.setPen(QPen(QColor(palette.playhead), 2))
            painter.drawLine(int(self.playhead_x), 0, int(self.playhead_x), self.height())
        painter.end()

    def mousePressEvent(self, event) -> None:
        host = getattr(self, "host", None)
        if host is not None and event.button() == Qt.LeftButton:
            host.begin_marquee(event.position().toPoint())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        host = getattr(self, "host", None)
        if host is not None and host._marquee is not None:
            host.update_marquee(event.position().toPoint())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        host = getattr(self, "host", None)
        if host is not None and host._marquee is not None and event.button() == Qt.LeftButton:
            host.finish_marquee(event.position().toPoint())
            event.accept()
            return
        super().mouseReleaseEvent(event)
