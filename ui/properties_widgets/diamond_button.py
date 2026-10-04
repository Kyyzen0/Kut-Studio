"""Losange d'image-clé de l'inspecteur."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QColor, QPen, QPolygonF
from PySide6.QtCore import QPointF, QRectF
from PySide6.QtWidgets import (
    QToolButton,
)

from ui.theme import COLORS
from ui.i18n import translate


class KeyframeDiamondButton(QToolButton):
    """Petit bouton losange pour ajouter / retirer une image-clé.

    État ``checked`` : image-clé présente au playhead courant (losange plein).
    État ``animated`` : la propriété est animée, sans image-clé ici (contour
    accentué). Clic : ajoute l'image-clé (et active l'animation) ou retire
    celle présente sous la tête de lecture. Clic droit : menu d'animation.
    """

    def __init__(self, property_name: str, parent=None):
        super().__init__(parent)
        self.property_name = property_name
        self.setCheckable(True)
        self.setChecked(False)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(22, 22)
        self.animated = False
        self.setToolTip(translate("inspector.keyframe_tooltip", name=property_name))

    def set_animated(self, animated: bool) -> None:
        if animated != self.animated:
            self.animated = animated
            self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(4, 4, self.width() - 8, self.height() - 8)
        center = QPointF(self.width() / 2, self.height() / 2)
        polygon = QPolygonF(
            [
                QPointF(center.x(), rect.top()),
                QPointF(rect.right(), center.y()),
                QPointF(center.x(), rect.bottom()),
                QPointF(rect.left(), center.y()),
            ]
        )
        if self.isChecked():
            painter.setBrush(QColor(COLORS["diamond_filled"]))
        else:
            painter.setBrush(QColor(COLORS["surface"]))
        outline = QColor(COLORS["diamond_filled"] if self.animated else COLORS["diamond_outline"])
        painter.setPen(QPen(outline, 2 if self.animated else 1))
        painter.drawPolygon(polygon)


_DiamondButton = KeyframeDiamondButton
"""Ancien nom, gardé pour les importations existantes."""

__all__ = ["KeyframeDiamondButton"]
