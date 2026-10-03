"""Colonne centrale : visionneuse et scopes dans un splitter vertical.

À 720 px de hauteur de fenêtre, toute la rangée du haut ne dispose que de ~400 px. Avec les scopes affichés, ils
gardaient leurs 200 px et la visionneuse n'avait plus que ~100 px d'image (143 × 85 px mesurés) : les scopes doivent
**céder en premier**. La visionneuse a donc une hauteur minimale utile, et les scopes redescendent vers leur plancher
quand la colonne manque de place. Quand elle en a (900 px de fenêtre), ils gardent leur confort habituel : la
disposition n'y change pas.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QSplitter, QWidget

from ui.design_system import Sizes


class ViewerHostSplitter(QSplitter):
    """Visionneuse (haut) et scopes (bas) : la visionneuse garde une hauteur utile, les scopes cèdent d'abord."""

    def __init__(self, viewer: QWidget, scopes: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(Qt.Vertical, parent)
        self._scopes = scopes
        self.setObjectName("viewer_with_scopes")
        self.setChildrenCollapsible(False)
        self.setHandleWidth(6)
        viewer.setMinimumHeight(Sizes.monitor_min_height)
        self.addWidget(viewer)
        self.addWidget(scopes)
        self.setStretchFactor(0, 3)
        self.setStretchFactor(1, 2)

    def scopes_minimum_height(self) -> int:
        """Hauteur minimale des scopes pour la hauteur actuelle de la colonne."""
        room = self.height() - self.handleWidth() - Sizes.monitor_min_height
        return max(Sizes.scopes_min_height, min(Sizes.scopes_comfort_height, room))

    def resizeEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().resizeEvent(event)
        wanted = self.scopes_minimum_height()
        if self._scopes.minimumHeight() != wanted:
            self._scopes.setMinimumHeight(wanted)


__all__ = ["ViewerHostSplitter"]
