"""Titre et rangée de six boutons « Aligner sur le cadre » du groupe Mouvement de l'inspecteur.

Le widget n'agit pas : il émet ``align_requested(bord)`` (valeur de :class:`core.frame_alignment.FrameEdge`)
et la fenêtre principale déplace le clip sélectionné. Les icônes sont dessinées (pas de glyphes Unicode, que
toutes les polices n'ont pas) ; chaque bouton porte une infobulle et un nom accessible traduits.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QToolButton, QVBoxLayout, QWidget

from core.frame_alignment import FrameEdge
from ui.i18n import translate
from ui.theme import label_style

_ICON = 16


def _icon_for(edge: FrameEdge, color: QColor) -> QIcon:
    """Un trait de cadre (le bord visé) et un rectangle de calque posé contre lui."""
    pixmap = QPixmap(_ICON, _ICON)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    pen = QPen(color)
    pen.setWidthF(1.4)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    s = float(_ICON)
    if edge is FrameEdge.LEFT:
        painter.drawLine(2, 1, 2, int(s) - 2)
        painter.drawRect(QRectF(4, 4, 8, 8))
    elif edge is FrameEdge.RIGHT:
        painter.drawLine(int(s) - 3, 1, int(s) - 3, int(s) - 2)
        painter.drawRect(QRectF(3, 4, 8, 8))
    elif edge is FrameEdge.TOP:
        painter.drawLine(1, 2, int(s) - 2, 2)
        painter.drawRect(QRectF(4, 4, 8, 8))
    elif edge is FrameEdge.BOTTOM:
        painter.drawLine(1, int(s) - 3, int(s) - 2, int(s) - 3)
        painter.drawRect(QRectF(4, 3, 8, 8))
    elif edge is FrameEdge.CENTER_H:
        painter.drawLine(int(s) // 2, 1, int(s) // 2, int(s) - 2)
        painter.drawRect(QRectF(3, 4, 10, 8))
    else:  # MIDDLE_V
        painter.drawLine(1, int(s) // 2, int(s) - 2, int(s) // 2)
        painter.drawRect(QRectF(4, 3, 8, 10))
    painter.end()
    return QIcon(pixmap)


class FrameAlignRow(QWidget):
    """Six boutons : gauche, centre H, droite, haut, milieu V, bas."""

    align_requested = Signal(str)

    EDGES = (
        FrameEdge.LEFT, FrameEdge.CENTER_H, FrameEdge.RIGHT,
        FrameEdge.TOP, FrameEdge.MIDDLE_V, FrameEdge.BOTTOM,
    )

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("frame_align_row")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(2)
        self.title = QLabel()
        self.title.setStyleSheet(label_style(11, "muted", 600))
        outer.addWidget(self.title)
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        outer.addLayout(layout)
        self.buttons: dict[FrameEdge, QToolButton] = {}
        color = self.palette().buttonText().color()
        for edge in self.EDGES:
            button = QToolButton()
            button.setObjectName("iconOnly")
            button.setIcon(_icon_for(edge, color))
            button.setIconSize(QSize(_ICON, _ICON))
            button.setFixedSize(26, 24)
            button.setAutoRaise(True)
            button.clicked.connect(lambda _checked=False, e=edge: self.align_requested.emit(e.value))
            self.buttons[edge] = button
            layout.addWidget(button)
        layout.addStretch(1)
        self.retranslate()

    def retranslate(self) -> None:
        self.title.setText(translate("mograph.align.group"))
        for edge, button in self.buttons.items():
            text = translate(f"shortcuts.command.layer_align_{edge.value}")
            button.setToolTip(text)
            button.setAccessibleName(text)


__all__ = ["FrameAlignRow"]
