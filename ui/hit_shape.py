"""Zones cliquables Qt : l'union réelle de formes qui se recouvrent.

``QGraphicsItem.shape()`` décide si un clic est livré à l'élément. Un ``QPainterPath`` rempli en
``OddEvenFill`` (le défaut) compte les recouvrements : là où un nombre PAIR de formes se superposent
(deux vidéos plein cadre, un calque sur un autre, le disque d'une poignée sur la boîte de sa sélection) le
point est « hors forme » et Qt ne livre pas le clic. Ces aides construisent un tracé en ``WindingFill``
dont tous les contours tournent dans le MÊME sens (un calque retourné ou à échelle négative donne un
polygone inversé, dont l'enroulement annulerait celui des autres) : la zone est alors l'union des formes.
Ne sert qu'au test d'atteinte de Qt, pas au dessin.
"""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QPainterPath, QPolygonF


def new_hit_path() -> QPainterPath:
    path = QPainterPath()
    path.setFillRule(Qt.WindingFill)
    return path


def _cross(o: QPointF, a: QPointF, b: QPointF) -> float:
    return (a.x() - o.x()) * (b.y() - o.y()) - (a.y() - o.y()) * (b.x() - o.x())


def _on_segment(a: QPointF, b: QPointF, p: QPointF) -> bool:
    return min(a.x(), b.x()) <= p.x() <= max(a.x(), b.x()) and min(a.y(), b.y()) <= p.y() <= max(a.y(), b.y())


def _segments_meet(a: QPointF, b: QPointF, c: QPointF, d: QPointF) -> bool:
    """Les segments ``ab`` et ``cd`` se croisent ou se touchent (colinéaires qui se chevauchent compris)."""
    d1, d2, d3, d4 = _cross(c, d, a), _cross(c, d, b), _cross(a, b, c), _cross(a, b, d)
    if ((d1 > 0 > d2) or (d1 < 0 < d2)) and ((d3 > 0 > d4) or (d3 < 0 < d4)):
        return True
    return ((d1 == 0 and _on_segment(c, d, a)) or (d2 == 0 and _on_segment(c, d, b))
            or (d3 == 0 and _on_segment(a, b, c)) or (d4 == 0 and _on_segment(a, b, d)))


def _signed_area(points: list[QPointF]) -> float:
    return sum(a.x() * b.y() - b.x() * a.y() for a, b in zip(points, points[1:] + points[:1], strict=True))


def _is_simple_quad(points: list[QPointF]) -> bool:
    """Un triangle est toujours simple ; un quadrilatère l'est si ses côtés opposés ne se rencontrent pas (2 paires)."""
    if len(points) == 3:
        return True
    a, b, c, d = points
    return not (_segments_meet(a, b, c, d) or _segments_meet(b, c, d, a))


def add_polygon(path: QPainterPath, polygon: QPolygonF) -> None:
    """Ajoute la région de ``polygon`` à ``path`` (créé par :func:`new_hit_path`), telle que le rendu la voit (pair-impair).

    Triangles et quadrilatères simples (les boîtes de calque : le cas courant) : le contour, orienté dans le sens des
    ``addRect`` / ``addEllipse`` de Qt (aire signée positive, y vers le bas). Tout autre polygone (masques polygonaux et
    fenêtres d'étalonnage, tirés point par point SANS refuser les croisements : nœud papillon, concave, sommets répétés) a
    des lobes qui peuvent tourner en sens opposés et s'annuleraient contre une autre forme : sa région pair-impair est ramenée
    à des contours sans recouvrement (``simplified``) ; ses lobes sont orientés dans le même sens, ses trous (sens inverse)
    restent des trous du seul polygone.
    """
    points = [polygon.at(i) for i in range(polygon.count())]
    if len(points) > 1 and points[0] == points[-1]:
        points.pop()
    points = [p for i, p in enumerate(points) if i == 0 or p != points[i - 1]]
    if len(points) < 3:
        return
    if len(points) <= 4 and _is_simple_quad(points):
        if _signed_area(points) < 0:
            points.reverse()
        path.addPolygon(QPolygonF(points))
        path.closeSubpath()
        return
    for ring in _odd_even_rings(tuple((p.x(), p.y()) for p in points)):
        path.addPolygon(QPolygonF([QPointF(x, y) for x, y in ring]))
        path.closeSubpath()


@lru_cache(maxsize=64)
def _odd_even_rings(coordinates: tuple[tuple[float, float], ...]) -> tuple[tuple[tuple[float, float], ...], ...]:
    """Contours sans recouvrement de la région pair-impair (mis en cache : ``shape()`` est rappelé à chaque mouvement de souris)."""
    own = QPainterPath()
    own.setFillRule(Qt.OddEvenFill)
    own.addPolygon(QPolygonF([QPointF(x, y) for x, y in coordinates]))
    own.closeSubpath()
    rings = [QPolygonF(r) for r in ([ring.at(i) for i in range(ring.count())] for ring in own.simplified().toSubpathPolygons())
             if len(r) >= 3]
    result = []
    for index, ring in enumerate(rings):
        ring_points = [ring.at(i) for i in range(ring.count())]
        if (_depth(index, rings) % 2 == 0) != (_signed_area(ring_points) > 0):      # lobe : sens positif ; trou : négatif
            ring_points.reverse()
        result.append(tuple((p.x(), p.y()) for p in ring_points))
    return tuple(result)


def _depth(index: int, rings: list[QPolygonF]) -> int:
    """Nombre d'autres contours qui enferment le contour ``index`` (pair : bord d'un lobe ; impair : bord d'un trou)."""
    ring = rings[index]
    a, b = ring.at(0), ring.at(1)
    for k in range(ring.count()):
        a, b = ring.at(k), ring.at((k + 1) % ring.count())
        if a != b:
            break
    dx, dy = b.x() - a.x(), b.y() - a.y()
    length = (dx * dx + dy * dy) ** 0.5 or 1.0
    mid = QPointF((a.x() + b.x()) / 2, (a.y() + b.y()) / 2)
    nudge = max(1e-3, length * 1e-3)
    inside = next((p for p in (QPointF(mid.x() - dy / length * nudge, mid.y() + dx / length * nudge),
                               QPointF(mid.x() + dy / length * nudge, mid.y() - dx / length * nudge))
                   if ring.containsPoint(p, Qt.OddEvenFill)), mid)
    return sum(1 for j, other in enumerate(rings) if j != index and other.containsPoint(inside, Qt.OddEvenFill))


def add_circle(path: QPainterPath, centre: QPointF, radius: float) -> None:
    path.addEllipse(centre, radius, radius)
