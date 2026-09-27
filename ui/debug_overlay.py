"""Overlay de diagnostic, invisible tant qu'on ne l'ouvre pas.

Le panneau ne participe pas aux clics. Il est mis à jour par la
fenêtre principale, au plus deux fois par seconde, et seulement
quand il est affiché. Le menu Fenêtre ou la variable
``KUT_STUDIO_DEBUG=1`` l'allument.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel


class DebugOverlay(QLabel):
    """Petit cartouche monospace, en haut à gauche de la fenêtre."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setTextFormat(Qt.PlainText)
        self.setStyleSheet(
            "QLabel {"
            "background: rgba(8, 12, 16, 210);"
            "color: #b7f7c8;"
            "font: 11px 'Menlo', 'Consolas', monospace;"
            "padding: 8px 10px;"
            "border-radius: 6px;"
            "}"
        )
        self.hide()

    def present(self, lines: list[str]) -> None:
        self.setText("\n".join(lines))
        self.adjustSize()
        self.move(12, 36)
        self.show()
        self.raise_()
