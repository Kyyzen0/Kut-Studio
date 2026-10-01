"""Losange d'image-clé de l'inspecteur."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QColor, QPolygonF
from PySide6.QtCore import QPointF, QRectF
from PySide6.QtWidgets import (
    QToolButton,
)

from ui.theme import COLORS


class _DiamondButton(QToolButton):
    """Petit bouton losange pour ajouter / retirer une image-clé.

    État ``checked`` : image-clé présente au playhead courant.
    Clic simple : ajoute ou remplace l'image-clé.
    Maj+clic : retire l'image-clé présente sous la tête de lecture.
    """

    def __init__(self, property_name: str, parent=None):
        super().__init__(parent)
        self.property_name = property_name
        self.setCheckable(True)
        self.setChecked(False)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(22, 22)
        self.setToolTip(
            f"Image-clé « {property_name} » : clic pour ajouter ou remplacer, "
            "Maj+clic pour retirer"
        )

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
        painter.setPen(QColor(COLORS["diamond_outline"]))
        painter.drawPolygon(polygon)
