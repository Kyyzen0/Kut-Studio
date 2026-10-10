"""Surcouche interactive du viewer : repères et manipulation directe.

Tout ce que dessine cet item est **visuel** : il n'est jamais rendu dans
un export (la composition vient de :mod:`core.mograph_raster` et du graphe
FFmpeg). Il affiche :

- zones de sécurité (titre / action), grille, croix centrale, guides ;
- la boîte englobante du calque sélectionné : poignées d'échelle (coins :
  échelle uniforme ; côtés : échelle X ou Y), poignée de rotation, point
  d'ancrage, déplacement direct ;
- les lignes de magnétisme pendant un glisser (bords et centre du cadre,
  guides, autres calques) ;
- la fenêtre du nœud d'étalonnage éditée (page Couleur) : son contour, sa
  douceur en pointillé, poignées de taille et de rotation, sommets d'une
  forme libre ; elle remplace alors les poignées du clip.

Les valeurs calculées sont émises **en direct** (``transform_dragged``),
puis une seule fois à la fin du geste (``transform_released``) : la
fenêtre n'enregistre qu'une entrée d'historique par glisser.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QGraphicsObject

from core.canvas_guides import (
    GuideOrientation,
    platform_content_rect,
    platform_zone_rects,
    safe_area_rects,
    snap_box,
)
from core.mograph_scene import Matrix, box_corners, mat_apply, mat_invert, map_box
from ui.i18n import translate
from ui.overlay_paint import halo_stroke
from ui.theme import OVERLAY, overlay_qcolor

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


@dataclass
class WindowGeometry:
    """La fenêtre d'un nœud d'étalonnage éditée dans le viewer.

    ``values`` : la fenêtre à la tête de lecture (animation et tracking compris), ce que le viewer montre ; ``base`` :
    ses valeurs saisies, ce qu'un glisser modifie (comme les poignées d'un clip suivi : partir des valeurs rendues
    ajouterait le mouvement suivi une seconde fois). Repère : celui du calque du clip (``box``, pixels de la
    séquence), placé dans le cadre par ``world``.
    """

    node_id: str
    window_id: str
    world: Matrix
    box: tuple[float, float]
    shape: str
    values: dict
    base: dict
    points: tuple = ()
    editable: bool = True


ELLIPSE_STEPS = 48


class ViewerOverlay(QGraphicsObject):
    transform_dragged = Signal(str, dict)
    transform_released = Signal(str, str)
    layer_clicked = Signal(str)
    guide_moved = Signal(str, float)
    guide_released = Signal(str)
    compare_moved = Signal(float)           # trait avant / après déplacé (part de la largeur)
    color_picked = Signal(float, float, bool)  # pipette : point du cadre (pixels), Maj (élargir)
    window_dragged = Signal(str, str, dict)    # fenêtre d'un nœud : (nœud, fenêtre, valeurs saisies)
    window_released = Signal(str, str)

    def __init__(self) -> None:
        super().__init__()
        self.setZValue(10)
        self.setAcceptedMouseButtons(Qt.LeftButton)
        self.setAcceptHoverEvents(True)
        self.canvas_rect = QRectF(0, 0, 1, 1)
        self.canvas_size = (1920.0, 1080.0)
        self.show_safe_areas = False
        self.platform_zones = ""            # plateforme dont les zones masquées sont montrées ("" : aucune)
        self.show_grid = False
        self.show_center = False
        self.show_guides = True
        self.snapping = True
        self.guides: list = []
        self.compare_split: float | None = None   # trait avant / après (part de la largeur), None : aucun
        self.pick_mode = False                    # pipette : un clic dans le cadre prend la couleur
        self.selection: SelectionGeometry | None = None
        self.window: WindowGeometry | None = None
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

    def set_window(self, window: WindowGeometry | None) -> None:
        """La fenêtre éditée (``None`` : aucune) ; pendant un glisser, la géométrie à jour est gardée pour l'affichage."""
        self.prepareGeometryChange()
        self.window = window
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

    def set_pick_mode(self, active: bool) -> None:
        """Pipette : le prochain clic dans le cadre donne un point (:attr:`color_picked`) au lieu de sélectionner."""
        self.prepareGeometryChange()
        self.pick_mode = bool(active)
        self.setCursor(Qt.CrossCursor if self.pick_mode else Qt.ArrowCursor)
        self.update()

    def set_compare_split(self, split: float | None) -> None:
        """Le trait de la comparaison avant / après (``None`` : aucun)."""
        self.compare_split = None if split is None else min(1.0, max(0.0, float(split)))
        self.update()

    def _compare_x(self) -> float | None:
        if self.compare_split is None:
            return None
        return self.canvas_rect.x() + self.compare_split * self.canvas_rect.width()

    def set_platform_zones(self, platform: str) -> None:
        """Montre les zones masquées par l'interface de ``platform`` (``""`` : aucune)."""
        self.platform_zones = str(platform or "")
        self.update()

    def _paint_platform_zones(self, painter: QPainter) -> None:
        """Zones couvertes par l'interface (voilées), puis le cadre libre en pointillés."""
        width, height = self.canvas_size
        painter.save()
        painter.setPen(Qt.NoPen)
        painter.setBrush(overlay_qcolor(OVERLAY.platform_zone, 70))
        for _kind, (x, y, w, h) in platform_zone_rects(width, height, self.platform_zones):
            top_left = self.to_scene(x, y)
            painter.drawRect(QRectF(top_left.x(), top_left.y(), w * self.scale, h * self.scale))
        painter.restore()
        x, y, w, h = platform_content_rect(self.platform_zones)
        top_left = self.to_scene(x * width, y * height)
        free = QRectF(top_left.x(), top_left.y(), w * width * self.scale, h * height * self.scale)
        halo_stroke(painter, overlay_qcolor(OVERLAY.platform_zone, 220), 1.0, lambda: painter.drawRect(free),
                    style=Qt.DashLine)

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
        if (x := self._compare_x()) is not None:
            path.addRect(QRectF(x - 5, self.canvas_rect.top(), 10, self.canvas_rect.height()))
        if self.pick_mode:
            path.addRect(self.canvas_rect)
        if self.window is not None and self.window.editable:
            path.addPolygon(self._window_outline(self.window))
            for point in self._window_handles(self.window).values():
                path.addEllipse(point, HANDLE_RADIUS + 3, HANDLE_RADIUS + 3)
        return path

    def _guide_rect(self, guide) -> QRectF:
        rect = self.canvas_rect
        if guide.orientation is GuideOrientation.VERTICAL:
            x = rect.x() + guide.position * rect.width()
            return QRectF(x, rect.top(), 0.5, rect.height())
        y = rect.y() + guide.position * rect.height()
        return QRectF(rect.left(), y, rect.width(), 0.5)

    # -- fenêtre d'un nœud ---------------------------------------------------------------------------

    @staticmethod
    def _window_frame(window: WindowGeometry, values: dict | None = None):
        """``(centre, demi-largeur, demi-hauteur, angle)`` de la fenêtre dans le repère du calque (comme
        :func:`core.mograph_raster.mask_path`)."""
        v = values or window.values
        w, h = window.box
        expansion = v.get("expansion", 0.0)
        half_w = max(0.0, (v.get("width", 0.5) + expansion) * w) / 2.0
        half_h = max(0.0, (v.get("height", 0.5) + expansion) * h) / 2.0
        return (v.get("position_x", 0.5) * w, v.get("position_y", 0.5) * h), half_w, half_h, \
            math.radians(v.get("rotation", 0.0))

    def _window_point(self, window: WindowGeometry, u: float, v: float) -> QPointF:
        """Point ``(u, v)`` de la boîte de la fenêtre (-0,5..0,5) dans la scène."""
        (cx, cy), half_w, half_h, angle = self._window_frame(window)
        x, y = u * 2.0 * half_w, v * 2.0 * half_h
        cos, sin = math.cos(angle), math.sin(angle)
        return self.to_scene(*mat_apply(window.world, cx + x * cos - y * sin, cy + x * sin + y * cos))

    def _window_outline(self, window: WindowGeometry, grow: float = 0.0) -> QPolygonF:
        """Contour de la fenêtre ; ``grow`` : agrandi de cette part (la douceur, en pointillé)."""
        k = 1.0 + grow
        if window.shape == "ellipse":
            points = [(0.5 * k * math.cos(2 * math.pi * i / ELLIPSE_STEPS), 0.5 * k * math.sin(2 * math.pi * i / ELLIPSE_STEPS))
                      for i in range(ELLIPSE_STEPS)]
        elif window.shape == "polygon" and len(window.points) >= 3:
            points = [(x * k, y * k) for x, y in window.points]
        else:
            points = [(-0.5 * k, -0.5 * k), (0.5 * k, -0.5 * k), (0.5 * k, 0.5 * k), (-0.5 * k, 0.5 * k)]
        return QPolygonF([self._window_point(window, u, v) for u, v in points])

    def _window_handles(self, window: WindowGeometry) -> dict[str, QPointF]:
        handles = {name: self._window_point(window, u, v) for name, (u, v) in {
            "w_tl": (-0.5, -0.5), "w_tr": (0.5, -0.5), "w_br": (0.5, 0.5), "w_bl": (-0.5, 0.5),
            "w_t": (0.0, -0.5), "w_r": (0.5, 0.0), "w_b": (0.0, 0.5), "w_l": (-0.5, 0.0),
        }.items()}
        top, centre = self._window_point(window, 0.0, -0.5), self._window_point(window, 0.0, 0.0)
        dx, dy = top.x() - centre.x(), top.y() - centre.y()
        length = math.hypot(dx, dy) or 1.0
        handles["w_rotate"] = QPointF(top.x() + dx / length * ROTATE_DISTANCE, top.y() + dy / length * ROTATE_DISTANCE)
        if window.shape == "polygon":                # les sommets ; les côtés tomberaient souvent sur eux
            for name in ("w_t", "w_r", "w_b", "w_l"):
                del handles[name]
            for index, (u, v) in enumerate(window.points):
                handles[f"w_p{index}"] = self._window_point(window, u, v)
        return handles

    def _paint_window(self, painter: QPainter, window: WindowGeometry) -> None:
        accent = overlay_qcolor(OVERLAY.selection if window.editable else OVERLAY.selection_locked)
        outline = self._window_outline(window)
        halo_stroke(painter, accent, 1.4, lambda: painter.drawPolygon(outline))
        feather = window.values.get("feather", 0.0)
        if feather > 0.0:
            _centre, half_w, half_h, _angle = self._window_frame(window)
            reach = feather * min(window.box) / max(1e-6, min(half_w, half_h) * 2.0)
            soft = self._window_outline(window, grow=reach)
            halo_stroke(painter, overlay_qcolor(OVERLAY.selection, 150), 1.0, lambda: painter.drawPolygon(soft),
                        style=Qt.DashLine)
        if not window.editable:
            return
        handles = self._window_handles(window)
        painter.setBrush(QBrush(overlay_qcolor(OVERLAY.handle_fill)))
        for name, point in handles.items():
            if name == "w_rotate":
                top = self._window_point(window, 0.0, -0.5)
                halo_stroke(painter, accent, 1.0, lambda point=point, top=top: painter.drawLine(top, point))
            radius = HANDLE_RADIUS - 1 if name.startswith("w_p") else HANDLE_RADIUS
            painter.setPen(QPen(accent, 1.0))
            if name.startswith("w_p") or name == "w_rotate":
                painter.drawEllipse(point, radius, radius)
            else:
                painter.drawRect(QRectF(point.x() - radius, point.y() - radius, 2 * radius, 2 * radius))
        painter.setBrush(Qt.NoBrush)

    def _window_hit(self, point: QPointF) -> str | None:
        window = self.window
        if window is None or not window.editable:
            return None
        handles = self._window_handles(window)
        names = [name for name in handles if name.startswith("w_p")] + \
            ["w_rotate", "w_tl", "w_tr", "w_br", "w_bl", "w_t", "w_r", "w_b", "w_l"]
        for name in names:
            handle = handles.get(name)
            if handle is not None and math.hypot(point.x() - handle.x(), point.y() - handle.y()) <= HANDLE_RADIUS + 3:
                return name
        if self._window_outline(window).containsPoint(point, Qt.OddEvenFill):
            return "w_move"
        return None

    def _window_values(self, drag: dict, current: tuple[float, float], modifiers) -> dict:
        """Valeurs **saisies** de la fenêtre après le geste : le changement mesuré sur la fenêtre montrée (déplacement,
        rapport de taille, angle) reporté sur la saisie ; un sommet de forme libre, lui, est pris tel quel."""
        window: WindowGeometry = drag["window"]
        mode = drag["mode"]
        try:
            inverse = mat_invert(window.world)
        except ValueError:
            return {}
        lx, ly = mat_apply(inverse, *current)
        sx, sy = mat_apply(inverse, *drag["start"])
        (cx, cy), half_w, half_h, angle = self._window_frame(window)
        width, height = window.box
        shown, base = window.values, window.base
        if mode == "w_move":
            dx, dy = lx - sx, ly - sy
            if modifiers & Qt.ShiftModifier:
                if abs(dx) > abs(dy):
                    dy = 0.0
                else:
                    dx = 0.0
            return {"position_x": base["position_x"] + dx / width, "position_y": base["position_y"] + dy / height}
        if mode == "w_rotate":
            turned = math.degrees(math.atan2(ly - cy, lx - cx) - math.atan2(sy - cy, sx - cx))
            rotation = shown.get("rotation", 0.0) + turned
            if modifiers & Qt.ShiftModifier:
                rotation = round(rotation / 15.0) * 15.0
            return {"rotation": base["rotation"] + rotation - shown.get("rotation", 0.0)}
        cos, sin = math.cos(-angle), math.sin(-angle)
        qx = (lx - cx) * cos - (ly - cy) * sin
        qy = (lx - cx) * sin + (ly - cy) * cos
        if mode.startswith("w_p"):
            index = int(mode[3:])
            points = list(window.points)
            if index >= len(points) or half_w <= 0.0 or half_h <= 0.0:
                return {}
            points[index] = (qx / (2.0 * half_w), qy / (2.0 * half_h))
            return {"points": tuple(points)}
        expansion = shown.get("expansion", 0.0)
        new_w, new_h = shown.get("width", 0.5), shown.get("height", 0.5)
        if mode in ("w_tl", "w_tr", "w_br", "w_bl", "w_l", "w_r"):
            new_w = 2.0 * abs(qx) / width - expansion
        if mode in ("w_tl", "w_tr", "w_br", "w_bl", "w_t", "w_b"):
            new_h = 2.0 * abs(qy) / height - expansion
        if mode in ("w_tl", "w_tr", "w_br", "w_bl") and modifiers & Qt.ShiftModifier and shown.get("width", 0) > 0:
            ratio = max(new_w / shown["width"], new_h / max(1e-6, shown.get("height", 0.5)))
            new_w, new_h = shown["width"] * ratio, shown.get("height", 0.5) * ratio
        result = {}
        for name, value in (("width", new_w), ("height", new_h)):
            if abs(value - shown.get(name, 0.5)) > 1e-9:
                scale = base[name] / shown[name] if shown.get(name, 0.0) > 1e-6 else 1.0
                result[name] = max(0.0, value * scale)
        return result

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
        painter.setBrush(Qt.NoBrush)
        if self.show_grid:
            def grid() -> None:
                for i in (1, 2):
                    x = rect.x() + rect.width() * i / 3
                    y = rect.y() + rect.height() * i / 3
                    painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
                    painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))

            halo_stroke(painter, overlay_qcolor(OVERLAY.grid, 70), 1.0, grid)
        if self.show_safe_areas:
            areas = safe_area_rects(self.canvas_size[0], self.canvas_size[1])
            for name, color in (("action", OVERLAY.safe_action), ("title", OVERLAY.safe_title)):
                x, y, w, h = areas[name]
                top_left = self.to_scene(x, y)
                zone = QRectF(top_left.x(), top_left.y(), w * self.scale, h * self.scale)
                halo_stroke(painter, overlay_qcolor(color, 190), 1.0, lambda zone=zone: painter.drawRect(zone), style=Qt.DashLine)
        if self.platform_zones:
            self._paint_platform_zones(painter)
        if (x := self._compare_x()) is not None:
            def divider(x=x) -> None:
                painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))

            halo_stroke(painter, overlay_qcolor(OVERLAY.centre, 230), 1.5, divider)
            painter.setBrush(QBrush(overlay_qcolor(OVERLAY.handle_fill)))
            halo_stroke(painter, overlay_qcolor(OVERLAY.centre, 230), 1.5,
                        lambda: painter.drawEllipse(QPointF(x, rect.center().y()), HANDLE_RADIUS + 2, HANDLE_RADIUS + 2))
            painter.setBrush(Qt.NoBrush)
        if self.show_center or self.show_safe_areas:
            centre = rect.center()

            def cross() -> None:
                painter.drawLine(QPointF(centre.x() - 10, centre.y()), QPointF(centre.x() + 10, centre.y()))
                painter.drawLine(QPointF(centre.x(), centre.y() - 10), QPointF(centre.x(), centre.y() + 10))

            halo_stroke(painter, overlay_qcolor(OVERLAY.centre, 200), 1.0, cross)
        if self.show_guides:
            for guide in self.guides:
                r = self._guide_rect(guide)
                vertical = guide.orientation is GuideOrientation.VERTICAL

                def guide_line(r=r, vertical=vertical) -> None:
                    if vertical:
                        painter.drawLine(QPointF(r.x(), r.top()), QPointF(r.x(), r.bottom()))
                    else:
                        painter.drawLine(QPointF(r.left(), r.y()), QPointF(r.right(), r.y()))

                halo_stroke(painter, overlay_qcolor(OVERLAY.guide, 110 if guide.locked else 220), 1.0, guide_line)
        for line in self._snap_lines:
            vertical = line.orientation is GuideOrientation.VERTICAL

            def snap_line(line=line, vertical=vertical) -> None:
                if vertical:
                    x = rect.x() + line.position * self.scale
                    painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
                else:
                    y = rect.y() + line.position * self.scale
                    painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))

            halo_stroke(painter, overlay_qcolor(OVERLAY.snap), 1.0, snap_line, style=Qt.DashLine)
        if self.window is not None:
            self._paint_window(painter, self.window)
        selection = self.selection
        if selection is None:
            return
        accent = overlay_qcolor(OVERLAY.selection if selection.editable else OVERLAY.selection_locked)
        box = self._box_polygon(selection)
        halo_stroke(painter, accent, 1.2, lambda: painter.drawPolygon(box))
        if not selection.editable:
            return
        handles = self._handle_points(selection)
        fill = QBrush(overlay_qcolor(OVERLAY.handle_fill))
        for name, point in handles.items():
            if name == "anchor":
                continue
            if name == "rotate":
                halo_stroke(painter, accent, 1.0, lambda point=point: painter.drawLine(handles["t"], point))
                shape = lambda point=point: painter.drawEllipse(point, HANDLE_RADIUS, HANDLE_RADIUS)  # noqa: E731
            else:
                square = QRectF(point.x() - HANDLE_RADIUS, point.y() - HANDLE_RADIUS, 2 * HANDLE_RADIUS, 2 * HANDLE_RADIUS)
                shape = lambda square=square: painter.drawRect(square)  # noqa: E731
            # Une poignée : liseré sombre autour, remplissage sombre, trait de la couleur de la sélection : visible sur toute image.
            painter.setBrush(Qt.NoBrush)
            halo_stroke(painter, accent, 1.0, shape)
            painter.setBrush(fill)
            painter.setPen(QPen(accent, 1.0))
            shape()
            painter.setBrush(Qt.NoBrush)
        anchor = handles.get("anchor")
        if anchor is not None:
            def anchor_mark() -> None:
                painter.drawEllipse(anchor, 4, 4)
                painter.drawLine(QPointF(anchor.x() - 8, anchor.y()), QPointF(anchor.x() + 8, anchor.y()))
                painter.drawLine(QPointF(anchor.x(), anchor.y() - 8), QPointF(anchor.x(), anchor.y() + 8))

            halo_stroke(painter, overlay_qcolor(OVERLAY.anchor), 1.5, anchor_mark)

    # -- interactions ------------------------------------------------------------------------

    def _hit(self, point: QPointF) -> tuple[str, str] | None:
        if self.pick_mode and self.canvas_rect.contains(point):
            return "pick", ""
        x = self._compare_x()
        if x is not None and abs(point.x() - x) <= 5 and self.canvas_rect.top() <= point.y() <= self.canvas_rect.bottom():
            return "compare", ""
        if (window := self._window_hit(point)) is not None:
            return window, self.window.window_id if self.window is not None else ""
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
                "l": Qt.SizeHorCursor, "r": Qt.SizeHorCursor, "select": Qt.PointingHandCursor, "pick": Qt.CrossCursor,
                "w_move": Qt.SizeAllCursor, "w_rotate": Qt.CrossCursor,
            }.get(hit[0], Qt.SizeAllCursor if hit[0].startswith("w_") else Qt.SplitHCursor)
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
        if mode == "pick":
            canvas_x, canvas_y = self.to_canvas(event.pos())
            extend = bool(event.modifiers() & Qt.ShiftModifier)
            self.color_picked.emit(float(canvas_x), float(canvas_y), extend)
            event.accept()
            return
        if mode == "compare":
            self._drag = {"mode": "compare", "clip_id": "", "moved": False}
            event.accept()
            return
        if mode.startswith("w_") and self.window is not None:
            self._drag = {"mode": mode, "clip_id": "", "window": self.window, "start": self.to_canvas(event.pos()),
                          "moved": False}
            event.accept()
            return
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
        if drag["mode"] == "compare":
            drag["moved"] = True
            self.compare_moved.emit(min(0.98, max(0.02, current[0] / max(1.0, self.canvas_size[0]))))
            return
        if drag["mode"].startswith("w_"):
            values = self._window_values(drag, current, event.modifiers())
            if values:
                drag["moved"] = True
                window = drag["window"]
                self.window_dragged.emit(window.node_id, window.window_id, values)
            return
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
        if drag["mode"] == "compare":
            return
        if drag["mode"].startswith("w_"):
            if drag["moved"]:
                self.window_released.emit(drag["window"].node_id, drag["window"].window_id)
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


__all__ = ["SelectionGeometry", "ViewerOverlay", "WindowGeometry"]
