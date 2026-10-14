"""Poignées du masque édité dans le moniteur (dessin, hit-test, glisser) : le calcul est dans :mod:`core.mask_handles`.

Un masque choisi dans la pile de l'éditeur de compositing remplace les poignées du clip : son contour en pointillés
(couleur selon l'opération), l'étendue de son adoucissement, des poignées de taille (coins, côtés ; sommets pour un
polygone), de rotation et d'adoucissement. Même géométrie que les fenêtres d'étalonnage : repère du calque, placé par la
matrice monde. Rien de ceci n'est rendu à l'export.

Noms de poignées (préfixe ``m_``, pour ne jamais être confondus avec ceux du clip ni des fenêtres) : ``m_tl``…, ``m_t``…,
``m_p<i>`` (sommet ou nœud de courbe), ``m_hi<i>`` / ``m_ho<i>`` (tangentes d'une courbe), ``m_rotate``, ``m_feather``,
``m_move`` (l'intérieur de la forme). Les nœuds et tangentes d'une courbe sont dessinés par :mod:`ui.mask_path_overlay`.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QPainter, QPainterPath, QPen, QPolygonF

from core import mask_handles as geo
from core.mograph_scene import box_corners
from ui import mask_path_overlay as path_ui
from ui.overlay_paint import halo_stroke
from ui.theme import OVERLAY, overlay_qcolor
from ui.viewer_overlay import HANDLE_RADIUS, ROTATE_DISTANCE

FEATHER_GAP = 14.0
"""Écart (pixels écran) entre le bord adouci et la poignée d'adoucissement : elle ne se confond pas avec la poignée du côté."""
_HIT = HANDLE_RADIUS + 3
_MODE_COLORS = {"add": OVERLAY.mask_add, "subtract": OVERLAY.mask_subtract, "intersect": OVERLAY.mask_intersect}
_MODE_GLYPHS = {"add": "+", "subtract": "−", "intersect": "∩"}
_CURSORS = {
    "m_move": Qt.SizeAllCursor, "m_rotate": Qt.CrossCursor, "m_feather": Qt.SplitHCursor,
    "m_tl": Qt.SizeFDiagCursor, "m_br": Qt.SizeFDiagCursor, "m_tr": Qt.SizeBDiagCursor, "m_bl": Qt.SizeBDiagCursor,
    "m_t": Qt.SizeVerCursor, "m_b": Qt.SizeVerCursor, "m_l": Qt.SizeHorCursor, "m_r": Qt.SizeHorCursor,
}


class MaskOverlayHelper:
    """Le masque édité d'un :class:`ui.viewer_overlay.ViewerOverlay` (``geometry`` : ``None`` hors du mode masque)."""

    def __init__(self, overlay) -> None:
        self.overlay = overlay
        self.geometry: geo.MaskGeometry | None = None
        self._hovered: int | None = None

    # -- repères écran -----------------------------------------------------------------------------

    def _scene(self, canvas_xy: tuple[float, float]) -> QPointF:
        return self.overlay.to_scene(*canvas_xy)

    def point(self, u: float, v: float) -> QPointF:
        return self._scene(geo.canvas_point(self.geometry, u, v))

    def outline(self, grow: tuple[float, float] = (0.0, 0.0)) -> QPolygonF:
        return QPolygonF([self.point(u, v) for u, v in geo.outline_units(self.geometry, grow)])

    def _box_path(self) -> QPainterPath:
        path = QPainterPath()
        path.addPolygon(QPolygonF([self._scene(p) for p in box_corners(self.geometry.world, *self.geometry.box)]))
        path.closeSubpath()
        return path

    def _affected_path(self) -> QPainterPath:
        """Zone que le masque agit sur : la forme, ou tout le calque sauf la forme si le masque est inversé."""
        shape = QPainterPath()
        shape.addPolygon(self.outline())
        shape.closeSubpath()
        return self._box_path().subtracted(shape) if self.geometry.inverted else shape

    def handles(self) -> dict[str, QPointF]:
        """Poignées actives (vide si le masque n'est pas modifiable)."""
        geometry = self.geometry
        if geometry is None or not geometry.editable:
            return {}
        result = {f"m_{name}": self.point(u, v) for name, (u, v) in geo.handle_units(geometry).items()}
        top, centre = self.point(0.0, -0.5), self.point(0.0, 0.0)
        dx, dy = top.x() - centre.x(), top.y() - centre.y()
        length = math.hypot(dx, dy) or 1.0
        result["m_rotate"] = QPointF(top.x() + dx / length * ROTATE_DISTANCE, top.y() + dy / length * ROTATE_DISTANCE)
        result["m_feather"] = self._feather_handle()
        return result

    def _feather_handle(self) -> QPointF:
        geometry = self.geometry
        _centre, half_w, _half_h, _angle = geo.mask_frame(geometry)
        reach = half_w + geo.feather_distance(geometry)
        edge = self._scene(geo.offset_point(geometry, reach, 0.0))
        beyond = self._scene(geo.offset_point(geometry, reach + 1.0, 0.0))
        dx, dy = beyond.x() - edge.x(), beyond.y() - edge.y()
        length = math.hypot(dx, dy) or 1.0
        return QPointF(edge.x() + dx / length * FEATHER_GAP, edge.y() + dy / length * FEATHER_GAP)

    # -- état ---------------------------------------------------------------------------------------

    def shape_path(self) -> QPainterPath:
        """Zone cliquable ajoutée à celle du moniteur : l'intérieur de la forme et les poignées."""
        path = QPainterPath()
        if self.geometry is None or not self.geometry.editable:
            return path
        path.addPolygon(self.outline())
        for point in self.handles().values():
            path.addEllipse(point, _HIT, _HIT)
        return path

    def hit(self, point: QPointF) -> str | None:
        """Poignée ou intérieur sous ``point`` ; les sommets d'abord, la rotation, l'adoucissement, puis les coins et les côtés."""
        geometry = self.geometry
        if geometry is None or not geometry.editable:
            return None
        handles = self.handles()
        vertices = [name for name in handles if name[2:].startswith("p") and name[3:].isdigit()]
        tangents = [name for name in handles if path_ui.is_tangent(name)]
        order = vertices + tangents + ["m_rotate", "m_feather", "m_tl", "m_tr", "m_br", "m_bl", "m_t", "m_r", "m_b", "m_l"]
        for name in order:
            handle = handles.get(name)
            if handle is not None and math.hypot(point.x() - handle.x(), point.y() - handle.y()) <= _HIT:
                return name
        if self.outline().containsPoint(point, Qt.OddEvenFill):
            return "m_move"
        return None

    # -- courbe : gestes de structure et survol (ui/mask_path_overlay.py) ---------------------------

    def hovered_node(self) -> int | None:
        """Nœud de courbe survolé à la dernière position de la souris (touche Suppr), ``None`` sinon."""
        return self._hovered

    def note_hover(self, point: QPointF | None) -> None:
        self._hovered = None if point is None else path_ui.hovered(self, point)

    def path_press(self, name: str, modifiers) -> bool:
        return path_ui.press(self, name, modifiers)

    def path_double_click(self, point: QPointF) -> bool:
        return path_ui.double_click(self, point)

    def remove_hovered_node(self) -> bool | None:
        """Retire le nœud survolé : ``True`` fait, ``False`` refusé (sous 3 nœuds), ``None`` aucun nœud survolé."""
        index = self._hovered
        if index is None or self.geometry is None or self.geometry.shape != "path" or not self.geometry.editable:
            return None
        return path_ui.remove_node(self, index)

    @staticmethod
    def cursor(name: str):
        return _CURSORS.get(name, Qt.SizeAllCursor)

    # -- dessin -------------------------------------------------------------------------------------

    def paint(self, painter: QPainter) -> None:
        geometry = self.geometry
        if geometry is None:
            return
        color = OVERLAY.selection_locked if not geometry.editable else _MODE_COLORS.get(geometry.mode, OVERLAY.selection)
        accent = overlay_qcolor(color)
        painter.save()
        painter.setPen(Qt.NoPen)
        painter.setBrush(overlay_qcolor(color, 38))
        painter.drawPath(self._affected_path())
        painter.setBrush(Qt.NoBrush)
        outline = self.outline()
        halo_stroke(painter, accent, 1.4, lambda: painter.drawPolygon(outline), style=Qt.DashLine)
        if geometry.values.get("feather", 0.0) > 0.0:
            soft = self.outline(geo.feather_growth(geometry))
            halo_stroke(painter, overlay_qcolor(color, 150), 1.0, lambda: painter.drawPolygon(soft), style=Qt.DotLine)
        corner = self.point(-0.5, -0.5)
        glyph = _MODE_GLYPHS.get(geometry.mode, "")
        for offset, tint in ((1.0, overlay_qcolor(OVERLAY.halo, 200)), (0.0, accent)):
            painter.setPen(QPen(tint))
            painter.drawText(QPointF(corner.x() - 14 + offset, corner.y() - 6 + offset), glyph)
        if geometry.editable:
            self._paint_handles(painter, accent)
        painter.restore()

    def _paint_handles(self, painter: QPainter, accent) -> None:
        handles = self.handles()
        geometry_is_path = self.geometry.shape == "path"
        painter.setBrush(QBrush(overlay_qcolor(OVERLAY.handle_fill)))
        top = self.point(0.0, -0.5)
        edge = self._scene(geo.offset_point(self.geometry, geo.mask_frame(self.geometry)[1], 0.0))
        for name, point in handles.items():
            if geometry_is_path and name[2:3] in ("p", "h"):
                continue                                   # nœuds et tangentes : ui.mask_path_overlay
            if name == "m_rotate":
                halo_stroke(painter, accent, 1.0, lambda point=point: painter.drawLine(top, point))
            elif name == "m_feather":
                halo_stroke(painter, accent, 1.0, lambda point=point: painter.drawLine(edge, point), style=Qt.DotLine)
            round_handle = name in ("m_rotate", "m_feather") or name[2:].startswith("p")
            radius = HANDLE_RADIUS - 1 if name[2:].startswith("p") else HANDLE_RADIUS
            painter.setPen(QPen(accent, 1.0))
            if name == "m_feather":
                diamond = QPolygonF([QPointF(point.x(), point.y() - radius - 1), QPointF(point.x() + radius + 1, point.y()),
                                     QPointF(point.x(), point.y() + radius + 1), QPointF(point.x() - radius - 1, point.y())])
                painter.drawPolygon(diamond)
            elif round_handle:
                painter.drawEllipse(point, radius, radius)
            else:
                painter.drawRect(QRectF(point.x() - radius, point.y() - radius, 2 * radius, 2 * radius))
        if geometry_is_path:
            path_ui.paint(self, painter, accent)
        painter.setBrush(Qt.NoBrush)

    # -- glisser ------------------------------------------------------------------------------------

    def begin(self, name: str, start: tuple[float, float]) -> dict:
        """État d'un glisser : la géométrie **au moment de l'appui** (le modèle la renverra mise à jour pendant le geste)."""
        geometry = self.geometry
        return {"mode": name, "clip_id": "", "mask_geometry": geometry, "start": start, "turn": 0.0, "moved": False}

    @staticmethod
    def step(drag: dict, current: tuple[float, float], modifiers) -> dict:
        """Valeurs à écrire pour ce pas de glisser (vide : rien à changer ou valeur hors bornes, pas refusée en silence)."""
        geometry: geo.MaskGeometry = drag["mask_geometry"]
        edit = geo.drag(geometry, drag["mode"][2:], drag["start"], current, shift=bool(modifiers & Qt.ShiftModifier),
                        previous_turn=drag["turn"], alt=bool(modifiers & Qt.AltModifier))
        if edit is None:
            return {}
        drag["turn"] = edit.turn
        payload = dict(edit.values)
        if edit.points is not None:
            payload["points"] = edit.points
        if edit.handles is not None:
            payload["handles"] = edit.handles
        return payload


__all__ = ["FEATHER_GAP", "MaskOverlayHelper"]
