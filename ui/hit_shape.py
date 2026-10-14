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

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QPainterPath, QPolygonF


def new_hit_path() -> QPainterPath:
    path = QPainterPath()
    path.setFillRule(Qt.WindingFill)
    return path


def add_polygon(path: QPainterPath, polygon: QPolygonF) -> None:
    """Ajoute ``polygon`` avec le sens des ``addRect`` / ``addEllipse`` de Qt (aire signée positive, y vers le bas)."""
    points = [polygon.at(i) for i in range(polygon.count())]
    if len(points) > 1 and points[0] == points[-1]:
        points.pop()
    if len(points) < 3:
        return
    area = sum(a.x() * b.y() - b.x() * a.y() for a, b in zip(points, points[1:] + points[:1], strict=True))
    if area < 0:
        points.reverse()
    path.addPolygon(QPolygonF(points))
    path.closeSubpath()


def add_circle(path: QPainterPath, centre: QPointF, radius: float) -> None:
    path.addEllipse(centre, radius, radius)
