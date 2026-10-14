"""Nœuds et tangentes d'un masque courbe dans le moniteur (ADR-0006, lot 3b) : dessin, gestes de structure.

Le contour lui-même est échantillonné par :func:`core.mask_handles.outline_units` : dessin et hit-test de l'intérieur
sont ceux des autres formes. Ici : les nœuds (``m_p<i>`` : rond = lisse, carré = anguleux), leurs tangentes (``m_hi<i>`` /
``m_ho<i>``, reliées au nœud par un trait), et les gestes qui changent la structure — Alt + clic sur un nœud le retire,
double-clic sur un nœud le lisse ou l'angularise, double-clic sur le contour y ajoute un nœud (la forme est conservée).
Ces gestes émettent ``mask_dragged`` puis ``mask_released`` : même chemin d'écriture, mêmes gardes et UNE étape
d'historique, comme un glisser.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QPen

from core import mask_path
from core import mask_path_handles as path_geo
from ui.overlay_paint import halo_stroke
from ui.theme import OVERLAY, overlay_qcolor
from ui.viewer_overlay import HANDLE_RADIUS

CONTOUR_PIXELS = 7.0
"""Distance (pixels écran) au contour en deçà de laquelle un double-clic y ajoute un nœud."""


def node_index(name: str) -> int | None:
    """Indice du nœud d'une poignée ``m_p<i>`` (``None`` : ce n'est pas un nœud)."""
    tail = name[3:]
    return int(tail) if name.startswith("m_p") and tail.isdigit() else None


def is_tangent(name: str) -> bool:
    return name.startswith(("m_hi", "m_ho")) and name[4:].isdigit()


def paint(helper, painter, accent) -> None:
    """Tangentes (trait + point) puis nœuds de la courbe."""
    handles = helper.handles()
    painter.setBrush(overlay_qcolor(OVERLAY.handle_fill))
    for name, point in handles.items():
        if not is_tangent(name):
            continue
        anchor = handles.get(f"m_p{name[4:]}")
        if anchor is not None:
            halo_stroke(painter, accent, 1.0, lambda a=anchor, b=point: painter.drawLine(a, b))
        painter.setPen(QPen(accent, 1.0))
        painter.drawEllipse(point, HANDLE_RADIUS - 2, HANDLE_RADIUS - 2)
    for name, point in handles.items():
        index = node_index(name)
        if index is None:
            continue
        angular = index >= len(helper.geometry.handles) or mask_path.is_angular(tuple(helper.geometry.handles[index]))
        painter.setPen(QPen(accent, 1.0))
        radius = HANDLE_RADIUS - 1
        if angular:
            painter.drawRect(QRectF(point.x() - radius, point.y() - radius, 2 * radius, 2 * radius))
        else:
            painter.drawEllipse(point, radius, radius)


def hovered(helper, point: QPointF) -> int | None:
    """Nœud sous ``point`` (le survol, pour la touche Suppr), sinon ``None``."""
    geometry = helper.geometry
    if geometry is None or geometry.shape != "path" or not geometry.editable:
        return None
    name = helper.hit(point)
    return node_index(name) if name is not None else None


def _emit(helper, edit) -> bool:
    if edit is None:
        return False
    geometry = helper.geometry
    helper._hovered = None                                 # les indices ont pu changer : le survol est à refaire
    helper.overlay.mask_dragged.emit(geometry.clip_id, geometry.mask_id, {"points": edit.points, "handles": edit.handles})
    helper.overlay.mask_released.emit(geometry.clip_id, geometry.mask_id)
    return True


def remove_node(helper, index: int) -> bool:
    """Retire le nœud ``index`` (refusé sous 3 nœuds : ``False``, rien n'est modifié)."""
    return _emit(helper, path_geo.node_action(helper.geometry, "remove", index))


def press(helper, name: str, modifiers) -> bool:
    """Alt + appui sur un nœud : le retire. ``True`` : le geste est consommé (pas de glisser), même si la suppression est refusée."""
    geometry = helper.geometry
    index = node_index(name)
    if index is None or geometry is None or geometry.shape != "path" or not geometry.editable or not modifiers & Qt.AltModifier:
        return False
    remove_node(helper, index)
    return True


def double_click(helper, point: QPointF) -> bool:
    """Double-clic : un nœud se lisse / s'angularise, le contour reçoit un nœud. ``True`` : consommé."""
    geometry = helper.geometry
    if geometry is None or geometry.shape != "path" or not geometry.editable:
        return False
    name = helper.hit(point)
    if name is not None and (index := node_index(name)) is not None:
        _emit(helper, path_geo.node_action(geometry, "toggle", index))
        return True
    if name is not None and name != "m_move":
        return False                                       # une autre poignée : son comportement habituel
    overlay = helper.overlay
    tolerance = CONTOUR_PIXELS / max(overlay.scale, 1e-9)
    # Seule la distance au contour compte : hors de la tolérance, le double-clic ne fait rien.
    return _emit(helper, path_geo.node_action(geometry, "add", point=overlay.to_canvas(point), tolerance=tolerance))


__all__ = ["CONTOUR_PIXELS", "double_click", "hovered", "is_tangent", "node_index", "paint", "press", "remove_node"]
