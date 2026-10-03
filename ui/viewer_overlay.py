"""Surcouche interactive du viewer : repères et manipulation directe.

Tout ce que dessine cet item est **visuel** : il n'est jamais rendu dans
un export (la composition vient de :mod:`core.mograph_raster` et du graphe
FFmpeg). Il affiche :

- zones de sécurité (titre / action), grille, croix centrale, guides ;
- la boîte englobante du calque sélectionné : poignées d'échelle (coins :
  échelle uniforme ; côtés : échelle X ou Y), poignée de rotation, point
  d'ancrage, déplacement direct ;
- les lignes de magnétisme pendant un glisser (bords et centre du cadre,
  guides, autres calques).

Les valeurs calculées sont émises **en direct** (``transform_dragged``),
puis une seule fois à la fin du geste (``transform_released``) : la
fenêtre n'enregistre qu'une entrée d'historique par glisser.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QGraphicsObject

from core.canvas_guides import GuideOrientation, safe_area_rects, snap_box
from core.mograph_scene import Matrix, box_corners, mat_apply, mat_invert, map_box
from ui.i18n import translate

HANDLE_RADIUS = 5.0
ROTATE_DISTANCE = 26.0
SNAP_PIXELS = 8.0


@dataclass
class SelectionGeometry:
    """Ce qu'il faut savoir du calque sélectionné pour le manipuler."""

    clip_id: str
    world: Matrix  # calque → cadre (pixels de la séquence)
    parent_world: Matrix
    box: tuple[float, float]
    values: dict  # valeurs courantes (transform évalué à la tête de lecture)
    editable: bool = True
    anchor_editable: bool = True


class ViewerOverlay(QGraphicsObject):
    transform_dragged = Signal(str, dict)
    transform_released = Signal(str, str)
    layer_clicked = Signal(str)
    guide_moved = Signal(str, float)
    guide_released = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setZValue(10)
        self.setAcceptedMouseButtons(Qt.LeftButton)
        self.setAcceptHoverEvents(True)
        self.canvas_rect = QRectF(0, 0, 1, 1)
        self.canvas_size = (1920.0, 1080.0)
        self.show_safe_areas = False
        self.show_grid = False
        self.show_center = False
        self.show_guides = True
        self.snapping = True
        self.guides: list = []
        self.selection: SelectionGeometry | None = None
        self.layer_boxes: list[tuple[str, Matrix, tuple[float, float]]] = []
        self._snap_lines = []
        self._drag: dict | None = None

    # -- état --------------------------------------------------------------------------------

    def set_canvas(self, rect: QRectF, size: tuple[float, float]) -> None:
        self.prepareGeometryChange()
        self.canvas_rect = QRectF(rect)
        self.canvas_size = (float(size[0]), float(size[1]))
        self.update()

    def set_selection(self, selection: SelectionGeometry | None) -> None:
        if self._drag is not None and selection is not None and self._drag["clip_id"] == selection.clip_id:
            # Pendant un glisser, la fenêtre renvoie la géométrie à jour : on la
            # garde pour l'affichage sans perturber le geste en cours.
            self.selection = selection
            self.update()
            return
        self.selection = selection
        self.update()

    def set_layer_boxes(self, boxes) -> None:
        self.layer_boxes = list(boxes)

    def set_guides(self, guides) -> None:
        self.guides = list(guides)
        self.update()

    def set_display(self, **flags) -> None:
        for name, value in flags.items():
            if hasattr(self, name):
                setattr(self, name, bool(value))
        self.update()

    # -- repères -----------------------------------------------------------------------------

    @property
    def scale(self) -> float:
        return self.canvas_rect.width() / max(1.0, self.canvas_size[0])

    def to_scene(self, x: float, y: float) -> QPointF:
        k = self.scale
        return QPointF(self.canvas_rect.x() + x * k, self.canvas_rect.y() + y * k)

    def to_canvas(self, point: QPointF) -> tuple[float, float]:
        k = self.scale
        return ((point.x() - self.canvas_rect.x()) / k, (point.y() - self.canvas_rect.y()) / k)

    def boundingRect(self) -> QRectF:  # noqa: N802 (API Qt)
        return self.canvas_rect.adjusted(-4000, -4000, 4000, 4000)

    def shape(self) -> QPainterPath:
        """Zone cliquable : seulement les poignées, les calques et les guides."""
        path = QPainterPath()
        if self.selection is not None:
            path.addPolygon(self._box_polygon(self.selection))
            for point in self._handle_points(self.selection).values():
                path.addEllipse(point, HANDLE_RADIUS + 3, HANDLE_RADIUS + 3)
        for _clip_id, world, box in self.layer_boxes:
            path.addPolygon(QPolygonF([self.to_scene(*p) for p in box_corners(world, *box)]))
        if self.show_guides:
            for guide in self.guides:
                path.addRect(self._guide_rect(guide).adjusted(-3, -3, 3, 3))
        return path

    def _guide_rect(self, guide) -> QRectF:
        rect = self.canvas_rect
        if guide.orientation is GuideOrientation.VERTICAL:
            x = rect.x() + guide.position * rect.width()
            return QRectF(x, rect.top(), 0.5, rect.height())
        y = rect.y() + guide.position * rect.height()
        return QRectF(rect.left(), y, rect.width(), 0.5)

    def _box_polygon(self, selection: SelectionGeometry) -> QPolygonF:
        return QPolygonF([self.to_scene(*p) for p in box_corners(selection.world, *selection.box)])

    def _handle_points(self, selection: SelectionGeometry) -> dict[str, QPointF]:
        w, h = selection.box
        world = selection.world
        points = {
            "tl": (0, 0), "tr": (w, 0), "br": (w, h), "bl": (0, h),
            "t": (w / 2, 0), "r": (w, h / 2), "b": (w / 2, h), "l": (0, h / 2),
        }
        result = {name: self.to_scene(*mat_apply(world, *p)) for name, p in points.items()}
        top = result["t"]
        centre = self.to_scene(*mat_apply(world, w / 2, h / 2))
        dx, dy = top.x() - centre.x(), top.y() - centre.y()
        length = math.hypot(dx, dy) or 1.0
        result["rotate"] = QPointF(top.x() + dx / length * ROTATE_DISTANCE, top.y() + dy / length * ROTATE_DISTANCE)
        if selection.anchor_editable:
            ax = selection.values.get("anchor_x", 0.5) * w
            ay = selection.values.get("anchor_y", 0.5) * h
            result["anchor"] = self.to_scene(*mat_apply(world, ax, ay))
        return result

    # -- dessin ------------------------------------------------------------------------------

    def paint(self, painter: QPainter, _option, _widget=None) -> None:
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = self.canvas_rect
        if self.show_grid:
            pen = QPen(QColor(255, 255, 255, 50), 1, Qt.SolidLine)
            painter.setPen(pen)
            for i in (1, 2):
                x = rect.x() + rect.width() * i / 3
                y = rect.y() + rect.height() * i / 3
                painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
                painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
        if self.show_safe_areas:
            areas = safe_area_rects(self.canvas_size[0], self.canvas_size[1])
            for name, color in (("action", QColor(255, 210, 0, 150)), ("title", QColor(0, 220, 255, 150))):
                x, y, w, h = areas[name]
                top_left = self.to_scene(x, y)
                painter.setPen(QPen(color, 1, Qt.DashLine))
                painter.setBrush(Qt.NoBrush)
                painter.drawRect(QRectF(top_left.x(), top_left.y(), w * self.scale, h * self.scale))
        if self.show_center or self.show_safe_areas:
            centre = rect.center()
            painter.setPen(QPen(QColor(255, 255, 255, 140), 1))
            painter.drawLine(QPointF(centre.x() - 10, centre.y()), QPointF(centre.x() + 10, centre.y()))
            painter.drawLine(QPointF(centre.x(), centre.y() - 10), QPointF(centre.x(), centre.y() + 10))
        if self.show_guides:
            for guide in self.guides:
                color = QColor(80, 200, 255, 200) if not guide.locked else QColor(80, 200, 255, 110)
                painter.setPen(QPen(color, 1))
                r = self._guide_rect(guide)
                if guide.orientation is GuideOrientation.VERTICAL:
                    painter.drawLine(QPointF(r.x(), r.top()), QPointF(r.x(), r.bottom()))
                else:
                    painter.drawLine(QPointF(r.left(), r.y()), QPointF(r.right(), r.y()))
        for line in self._snap_lines:
            painter.setPen(QPen(QColor(255, 60, 160), 1, Qt.DashLine))
            if line.orientation is GuideOrientation.VERTICAL:
                x = rect.x() + line.position * self.scale
                painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            else:
                y = rect.y() + line.position * self.scale
                painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
        selection = self.selection
        if selection is None:
            return
        accent = QColor(54, 230, 195) if selection.editable else QColor(160, 160, 160)
        painter.setPen(QPen(accent, 1.2))
        painter.setBrush(Qt.NoBrush)
        painter.drawPolygon(self._box_polygon(selection))
        if not selection.editable:
            return
        handles = self._handle_points(selection)
        painter.setBrush(QBrush(QColor(20, 24, 28)))
        for name, point in handles.items():
            if name == "anchor":
                continue
            if name == "rotate":
                painter.setPen(QPen(accent, 1))
                painter.drawLine(handles["t"], point)
                painter.drawEllipse(point, HANDLE_RADIUS, HANDLE_RADIUS)
            else:
                painter.drawRect(QRectF(point.x() - HANDLE_RADIUS, point.y() - HANDLE_RADIUS,
                                        2 * HANDLE_RADIUS, 2 * HANDLE_RADIUS))
        anchor = handles.get("anchor")
        if anchor is not None:
            painter.setPen(QPen(QColor(255, 200, 40), 1.5))
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(anchor, 4, 4)
            painter.drawLine(QPointF(anchor.x() - 8, anchor.y()), QPointF(anchor.x() + 8, anchor.y()))
            painter.drawLine(QPointF(anchor.x(), anchor.y() - 8), QPointF(anchor.x(), anchor.y() + 8))

    # -- interactions ------------------------------------------------------------------------

    def _hit(self, point: QPointF) -> tuple[str, str] | None:
        selection = self.selection
        if selection is not None and selection.editable:
            handles = self._handle_points(selection)
            order = ["anchor", "rotate", "tl", "tr", "br", "bl", "t", "r", "b", "l"]
            for name in order:
                handle = handles.get(name)
                if handle is not None and math.hypot(point.x() - handle.x(), point.y() - handle.y()) <= HANDLE_RADIUS + 3:
                    return name, selection.clip_id
            if self._box_polygon(selection).containsPoint(point, Qt.OddEvenFill):
                return "move", selection.clip_id
        if self.show_guides:
            for guide in self.guides:
                if not guide.locked and self._guide_rect(guide).adjusted(-3, -3, 3, 3).contains(point):
                    return "guide", guide.id
        for clip_id, world, box in reversed(self.layer_boxes):
            polygon = QPolygonF([self.to_scene(*p) for p in box_corners(world, *box)])
            if polygon.containsPoint(point, Qt.OddEvenFill):
                return "select", clip_id
        return None

    def hoverMoveEvent(self, event) -> None:  # noqa: N802
        hit = self._hit(event.pos())
        cursor = Qt.ArrowCursor
        if hit is not None:
            cursor = {
                "move": Qt.SizeAllCursor, "rotate": Qt.CrossCursor, "anchor": Qt.CrossCursor,
                "tl": Qt.SizeFDiagCursor, "br": Qt.SizeFDiagCursor, "tr": Qt.SizeBDiagCursor,
                "bl": Qt.SizeBDiagCursor, "t": Qt.SizeVerCursor, "b": Qt.SizeVerCursor,
                "l": Qt.SizeHorCursor, "r": Qt.SizeHorCursor, "select": Qt.PointingHandCursor,
            }.get(hit[0], Qt.SplitHCursor)
            if hit[0] == "guide":
                guide = next((g for g in self.guides if g.id == hit[1]), None)
                if guide is not None and guide.orientation is GuideOrientation.HORIZONTAL:
                    cursor = Qt.SplitVCursor
        self.setCursor(cursor)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        hit = self._hit(event.pos())
        if hit is None:
            event.ignore()
            return
        mode, target = hit
        if mode == "select":
            self.layer_clicked.emit(target)
            if self.selection is None or self.selection.clip_id != target or not self.selection.editable:
                event.accept()
                return
            mode = "move"
        if mode == "guide":
            self._drag = {"mode": "guide", "guide_id": target, "clip_id": ""}
            event.accept()
            return
        selection = self.selection
        self._drag = {
            "mode": mode,
            "clip_id": selection.clip_id,
            "start": self.to_canvas(event.pos()),
            "world": selection.world,
            "parent_world": selection.parent_world,
            "box": selection.box,
            "values": dict(selection.values),
            "moved": False,
        }
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        drag = self._drag
        if drag is None:
            return
        current = self.to_canvas(event.pos())
        if drag["mode"] == "guide":
            guide = next((g for g in self.guides if g.id == drag["guide_id"]), None)
            if guide is None:
                return
            if guide.orientation is GuideOrientation.VERTICAL:
                position = current[0] / self.canvas_size[0]
            else:
                position = current[1] / self.canvas_size[1]
            drag["moved"] = True
            self.guide_moved.emit(guide.id, position)
            return
        values = self._drag_values(drag, current, event.modifiers())
        if values:
            drag["moved"] = True
            self.transform_dragged.emit(drag["clip_id"], values)
        self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        drag = self._drag
        self._drag = None
        self._snap_lines = []
        self.update()
        if drag is None:
            return
        if drag["mode"] == "guide":
            if drag["moved"]:
                self.guide_released.emit(drag["guide_id"])
            return
        if drag["moved"]:
            label = {
                "move": translate("mograph.viewer.move"), "rotate": translate("mograph.viewer.rotate"),
                "anchor": translate("mograph.viewer.anchor"),
            }.get(drag["mode"], translate("mograph.viewer.resize"))
            self.transform_released.emit(drag["clip_id"], label)

    # -- calcul des valeurs ------------------------------------------------------------------

    def _drag_values(self, drag: dict, current: tuple[float, float], modifiers) -> dict:
        mode = drag["mode"]
        values = drag["values"]
        w, h = drag["box"]
        world = drag["world"]
        cw, ch = self.canvas_size
        sx0, sy0 = drag["start"]
        dx, dy = current[0] - sx0, current[1] - sy0
        anchor_local = (values.get("anchor_x", 0.5) * w, values.get("anchor_y", 0.5) * h)
        anchor = mat_apply(world, *anchor_local)
        if mode == "move":
            if modifiers & Qt.ShiftModifier:
                if abs(dx) > abs(dy):
                    dy = 0.0
                else:
                    dx = 0.0
            self._snap_lines = []
            if self.snapping and not modifiers & Qt.AltModifier:
                x0, y0, x1, y1 = map_box(world, w, h)
                others = [
                    map_box(m, *b) for cid, m, b in self.layer_boxes if cid != drag["clip_id"]
                ]
                visible_guides = self.guides if self.show_guides else ()
                sdx, sdy, lines = snap_box(
                    (x0 + dx, y0 + dy, x1 + dx, y1 + dy), cw, ch,
                    threshold=SNAP_PIXELS / max(self.scale, 1e-6),
                    guides=visible_guides, layer_boxes=others,
                )
                dx, dy = dx + sdx, dy + sdy
                self._snap_lines = lines
            a, b, c, d, _e, _f = drag["parent_world"]
            det = a * d - b * c
            if abs(det) < 1e-12:
                return {}
            px = (d * dx - c * dy) / det
            py = (-b * dx + a * dy) / det
            return {
                "position_x": values.get("position_x", 0.0) + px / cw,
                "position_y": values.get("position_y", 0.0) + py / ch,
            }
        if mode == "rotate":
            start_angle = math.degrees(math.atan2(sy0 - anchor[1], sx0 - anchor[0]))
            angle = math.degrees(math.atan2(current[1] - anchor[1], current[0] - anchor[0]))
            rotation = values.get("rotation", 0.0) + (angle - start_angle)
            if modifiers & Qt.ShiftModifier:
                rotation = round(rotation / 15.0) * 15.0
            return {"rotation": rotation}
        if mode == "anchor":
            try:
                inverse = mat_invert(world)
            except ValueError:
                return {}
            lx, ly = mat_apply(inverse, *current)
            new_ax, new_ay = lx / max(w, 1e-6), ly / max(h, 1e-6)
            # La position suit l'ancrage : le calque ne bouge pas à l'écran.
            parent_inverse = mat_invert(drag["parent_world"])
            old_parent = mat_apply(parent_inverse, *anchor)
            new_parent = mat_apply(parent_inverse, *current)
            return {
                "anchor_x": new_ax, "anchor_y": new_ay,
                "position_x": values.get("position_x", 0.0) + (new_parent[0] - old_parent[0]) / cw,
                "position_y": values.get("position_y", 0.0) + (new_parent[1] - old_parent[1]) / ch,
            }
        # Échelle : coins = uniforme, côtés = un axe.
        if mode in ("tl", "tr", "br", "bl"):
            start = math.hypot(sx0 - anchor[0], sy0 - anchor[1])
            now = math.hypot(current[0] - anchor[0], current[1] - anchor[1])
            if start < 1e-6:
                return {}
            return {"scale": values.get("scale", 1.0) * now / start}
        try:
            inverse = mat_invert(world)
        except ValueError:
            return {}
        lx0, ly0 = mat_apply(inverse, sx0, sy0)
        lx, ly = mat_apply(inverse, *current)
        if mode in ("l", "r"):
            base = lx0 - anchor_local[0]
            if abs(base) < 1e-6:
                return {}
            return {"scale_x": values.get("scale_x", 1.0) * (lx - anchor_local[0]) / base}
        base = ly0 - anchor_local[1]
        if abs(base) < 1e-6:
            return {}
        return {"scale_y": values.get("scale_y", 1.0) * (ly - anchor_local[1]) / base}


__all__ = ["SelectionGeometry", "ViewerOverlay"]
