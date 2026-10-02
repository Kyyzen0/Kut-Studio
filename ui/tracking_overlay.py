"""Surcouche du viewer pour le tracking : points, zones, trajectoires, corrections.

Purement visuelle (jamais rendue dans un export). La fenêtre fournit, à
chaque image, la matrice ``média → cadre`` du clip suivi (placement,
transform et stabilisation compris) et la liste des trackers ; la
surcouche convertit les gestes en pixels **du média** :

- glisser le point (ou l'intérieur de la zone cible) : correction manuelle ;
- glisser un coin de la zone cible / de recherche : redimensionnement
  (symétrique autour du point) ;
- clic : sélection ; double-clic sur l'image : nouveau tracker à cet endroit.

Comme pour les calques, les valeurs sont émises en direct puis une seule
fois au relâchement (``drag_finished``) : une entrée d'historique par geste.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QGraphicsObject

from core.mograph_scene import IDENTITY, Matrix, mat_apply, mat_invert
from core.tracking_model import SampleStatus

HANDLE = 5.0
POINT_RADIUS = 7.0
MAX_PATH_POINTS = 400
"""Trajectoire décimée : au plus ce nombre de sommets, quelle que soit la durée."""

UNCERTAIN_COLOR = QColor(255, 170, 40)
LOST_COLOR = QColor(255, 70, 70)


@dataclass
class OverlayTracker:
    """Ce que la surcouche doit savoir d'un tracker à l'image courante."""

    id: str
    name: str
    color: str
    selected: bool = False
    point: tuple[float, float] | None = None
    """Position dans le **média** à l'image courante (``None`` : aucune donnée)."""
    status: SampleStatus = SampleStatus.EMPTY
    pattern: tuple[float, float] = (48.0, 48.0)
    search: tuple[float, float] = (144.0, 144.0)
    path: list[tuple[float, float, int]] = field(default_factory=list)
    """Trajectoire (pixels du média, état) déjà décimée."""
    show_path: bool = True


def decimate(points: list, limit: int = MAX_PATH_POINTS) -> list:
    """Garde au plus ``limit`` points régulièrement répartis (extrémités comprises)."""
    if len(points) <= limit:
        return list(points)
    step = (len(points) - 1) / (limit - 1)
    return [points[int(round(i * step))] for i in range(limit)]


class TrackingOverlay(QGraphicsObject):
    tracker_clicked = Signal(str)
    point_dragged = Signal(str, float, float)
    zone_dragged = Signal(str, str, float, float)
    drag_finished = Signal(str, str)
    add_requested = Signal(float, float)

    def __init__(self) -> None:
        super().__init__()
        self.setZValue(12)
        self.setAcceptedMouseButtons(Qt.LeftButton)
        self.setAcceptHoverEvents(True)
        self.canvas_rect = QRectF(0, 0, 1, 1)
        self.canvas_size = (1920.0, 1080.0)
        self.active = False
        self.show_paths = True
        self.mapping: Matrix = IDENTITY
        self.trackers: list[OverlayTracker] = []
        self._drag: dict | None = None
        self.setVisible(False)

    # -- état --------------------------------------------------------------------------------

    def set_canvas(self, rect: QRectF, size: tuple[float, float]) -> None:
        self.prepareGeometryChange()
        self.canvas_rect = QRectF(rect)
        self.canvas_size = (float(size[0]), float(size[1]))
        self.update()

    def set_state(self, *, active: bool, mapping: Matrix, trackers: list[OverlayTracker], show_paths: bool) -> None:
        """Nouvel état (ignoré pendant un glisser pour ne pas perturber le geste)."""
        if self._drag is not None:
            self.trackers = trackers if active else []
            self.mapping = mapping
            self.update()
            return
        self.prepareGeometryChange()
        self.active = bool(active)
        self.mapping = mapping
        self.trackers = list(trackers) if active else []
        self.show_paths = bool(show_paths)
        self.setVisible(self.active)
        self.update()

    # -- repères -----------------------------------------------------------------------------

    @property
    def scale(self) -> float:
        return self.canvas_rect.width() / max(1.0, self.canvas_size[0])

    def to_scene(self, x: float, y: float) -> QPointF:
        """Pixels du média → scène."""
        cx, cy = mat_apply(self.mapping, x, y)
        k = self.scale
        return QPointF(self.canvas_rect.x() + cx * k, self.canvas_rect.y() + cy * k)

    def to_media(self, point: QPointF) -> tuple[float, float] | None:
        k = self.scale
        cx = (point.x() - self.canvas_rect.x()) / k
        cy = (point.y() - self.canvas_rect.y()) / k
        try:
            inverse = mat_invert(self.mapping)
        except ValueError:
            return None
        return mat_apply(inverse, cx, cy)

    def _zone(self, tracker: OverlayTracker, size: tuple[float, float]) -> QPolygonF:
        x, y = tracker.point
        hw, hh = size[0] / 2.0, size[1] / 2.0
        return QPolygonF([self.to_scene(x + dx, y + dy) for dx, dy in ((-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh))])

    def boundingRect(self) -> QRectF:  # noqa: N802 (API Qt)
        return self.canvas_rect.adjusted(-2000, -2000, 2000, 2000)

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        if not self.active:
            return path
        path.addRect(self.canvas_rect)
        for tracker in self.trackers:
            if tracker.point is not None:
                path.addPolygon(self._zone(tracker, tracker.search))
        return path

    # -- dessin ------------------------------------------------------------------------------

    def paint(self, painter: QPainter, _option, _widget=None) -> None:
        if not self.active:
            return
        painter.setRenderHint(QPainter.Antialiasing, True)
        for tracker in self.trackers:
            color = QColor(tracker.color)
            if self.show_paths and tracker.show_path and tracker.path:
                self._paint_path(painter, tracker, color)
        for tracker in self.trackers:
            self._paint_tracker(painter, tracker, QColor(tracker.color))

    def _paint_path(self, painter: QPainter, tracker: OverlayTracker, color: QColor) -> None:
        faded = QColor(color)
        faded.setAlpha(150)
        painter.setPen(QPen(faded, 1.2))
        painter.setBrush(Qt.NoBrush)
        polygon = QPolygonF([self.to_scene(x, y) for x, y, _status in tracker.path])
        painter.drawPolyline(polygon)
        dot = 1.8
        for (x, y, status), point in zip(tracker.path, polygon):
            if status == SampleStatus.UNCERTAIN:
                painter.setBrush(UNCERTAIN_COLOR)
            elif status == SampleStatus.LOST:
                painter.setBrush(LOST_COLOR)
            elif status == SampleStatus.MANUAL:
                painter.setBrush(QColor(255, 255, 255))
            else:
                continue
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(point, dot + 0.8, dot + 0.8)
            painter.setPen(QPen(faded, 1.2))
            painter.setBrush(Qt.NoBrush)

    def _paint_tracker(self, painter: QPainter, tracker: OverlayTracker, color: QColor) -> None:
        if tracker.point is None:
            return
        status = tracker.status
        point_color = {SampleStatus.UNCERTAIN: UNCERTAIN_COLOR, SampleStatus.LOST: LOST_COLOR}.get(status, color)
        if status == SampleStatus.EMPTY:
            point_color = QColor(180, 180, 180)
        width = 1.6 if tracker.selected else 1.0
        search_pen = QPen(color, width, Qt.DashLine)
        painter.setPen(search_pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawPolygon(self._zone(tracker, tracker.search))
        painter.setPen(QPen(point_color, width))
        painter.drawPolygon(self._zone(tracker, tracker.pattern))
        centre = self.to_scene(*tracker.point)
        if status == SampleStatus.LOST:
            painter.setPen(QPen(LOST_COLOR, 2.0))
            r = POINT_RADIUS
            painter.drawLine(QPointF(centre.x() - r, centre.y() - r), QPointF(centre.x() + r, centre.y() + r))
            painter.drawLine(QPointF(centre.x() - r, centre.y() + r), QPointF(centre.x() + r, centre.y() - r))
        else:
            painter.setPen(QPen(point_color, 1.5))
            painter.drawEllipse(centre, 3.0, 3.0)
            painter.drawLine(QPointF(centre.x() - POINT_RADIUS, centre.y()), QPointF(centre.x() - 4, centre.y()))
            painter.drawLine(QPointF(centre.x() + 4, centre.y()), QPointF(centre.x() + POINT_RADIUS, centre.y()))
            painter.drawLine(QPointF(centre.x(), centre.y() - POINT_RADIUS), QPointF(centre.x(), centre.y() - 4))
            painter.drawLine(QPointF(centre.x(), centre.y() + 4), QPointF(centre.x(), centre.y() + POINT_RADIUS))
        if tracker.selected:
            painter.setBrush(QBrush(QColor(20, 24, 28)))
            for polygon, pen in (
                (self._zone(tracker, tracker.pattern), QPen(point_color, 1.0)),
                (self._zone(tracker, tracker.search), QPen(color, 1.0)),
            ):
                painter.setPen(pen)
                for corner in polygon:
                    painter.drawRect(QRectF(corner.x() - HANDLE / 2, corner.y() - HANDLE / 2, HANDLE, HANDLE))
        font = QFont(painter.font())
        font.setPointSizeF(8.0)
        painter.setFont(font)
        painter.setPen(QPen(color))
        label_anchor = self._zone(tracker, tracker.pattern)[0]
        painter.drawText(QPointF(label_anchor.x(), label_anchor.y() - 4), tracker.name)

    # -- interactions ------------------------------------------------------------------------

    def _hit(self, point: QPointF) -> tuple[str, OverlayTracker] | None:
        ordered = sorted(self.trackers, key=lambda t: not t.selected)
        for tracker in ordered:
            if tracker.point is None:
                continue
            if tracker.selected:
                for kind, size in (("pattern", tracker.pattern), ("search", tracker.search)):
                    for corner in self._zone(tracker, size):
                        if math.hypot(point.x() - corner.x(), point.y() - corner.y()) <= HANDLE + 3:
                            return kind, tracker
            centre = self.to_scene(*tracker.point)
            if math.hypot(point.x() - centre.x(), point.y() - centre.y()) <= POINT_RADIUS + 3:
                return "point", tracker
            if self._zone(tracker, tracker.pattern).containsPoint(point, Qt.OddEvenFill):
                return "point", tracker
        return None

    def hoverMoveEvent(self, event) -> None:  # noqa: N802
        hit = self._hit(event.pos())
        if hit is None:
            self.setCursor(Qt.CrossCursor)
        else:
            self.setCursor(Qt.SizeAllCursor if hit[0] == "point" else Qt.SizeFDiagCursor)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if not self.active:
            event.ignore()
            return
        hit = self._hit(event.pos())
        if hit is None:
            event.accept()
            return
        kind, tracker = hit
        self.tracker_clicked.emit(tracker.id)
        media = self.to_media(event.pos())
        if media is None or tracker.point is None:
            event.accept()
            return
        self._drag = {
            "kind": kind, "id": tracker.id, "start": media, "origin": tracker.point,
            "size": tracker.pattern if kind == "pattern" else tracker.search, "moved": False,
        }
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        drag = self._drag
        if drag is None:
            return
        media = self.to_media(event.pos())
        if media is None:
            return
        ox, oy = drag["origin"]
        if drag["kind"] == "point":
            dx, dy = media[0] - drag["start"][0], media[1] - drag["start"][1]
            self.point_dragged.emit(drag["id"], ox + dx, oy + dy)
        else:
            width = max(8.0, 2.0 * abs(media[0] - ox))
            height = max(8.0, 2.0 * abs(media[1] - oy))
            if event.modifiers() & Qt.ShiftModifier:
                width = height = max(width, height)
            self.zone_dragged.emit(drag["id"], drag["kind"], width, height)
        drag["moved"] = True

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        drag = self._drag
        self._drag = None
        if drag is not None and drag["moved"]:
            self.drag_finished.emit(drag["id"], drag["kind"])

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if not self.active or self._hit(event.pos()) is not None:
            return
        if not self.canvas_rect.contains(event.pos()):
            return
        media = self.to_media(event.pos())
        if media is not None:
            self.add_requested.emit(media[0], media[1])
        event.accept()


__all__ = ["MAX_PATH_POINTS", "OverlayTracker", "TrackingOverlay", "decimate"]
