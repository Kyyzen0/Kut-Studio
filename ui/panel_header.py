"""Bandeau de panneau : le même partout (hauteur, titre, icône, filet du bas), avec de la place pour ce que le panneau y ajoute.

Chaque panneau construisait son en-tête à sa façon (40 px ici, un titre en micro-capitales grises là, un filet recodé en dur ailleurs).
``PanelHeader`` donne le même squelette à la visionneuse, au mixeur et à ce qui s'y ajoutera : une hauteur (``Sizes.panel_header``),
un titre au rôle « panel-title » qui ressort, une icône discrète, et à droite les contrôles propres au panneau. Les couleurs viennent
de la feuille de style globale (``QFrame#panelHeader``) : l'en-tête suit le thème sans style local.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QWidget

from ui.design_system import Iconography, Sizes, Spacing
from ui.icons import IconLabel, IconName
from ui.theme import set_role


class PanelHeader(QFrame):
    """Icône + titre à gauche, ``add_trailing`` à droite ; ``title_label`` est un ``QLabel`` ordinaire (``setText`` le re-traduit)."""

    def __init__(self, title: str = "", *, icon: IconName | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("panelHeader")
        self.setFixedHeight(Sizes.panel_header)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(Spacing.md, 0, Spacing.sm, 0)
        self._layout.setSpacing(Spacing.sm)

        self.icon_label: IconLabel | None = None
        if icon is not None:
            self.icon_label = IconLabel(icon, size=Iconography.md)
            self.icon_label.set_color("muted_strong")            # un jeton, lu à la peinture : suit le thème
            self._layout.addWidget(self.icon_label)

        self.title_label = QLabel(title)
        set_role(self.title_label, "panel-title")
        self._layout.addWidget(self.title_label)
        self._layout.addStretch(1)

    def add_trailing(self, widget: QWidget) -> None:
        """Ajoute ``widget`` à droite du titre, centré verticalement dans le bandeau."""
        self._layout.addWidget(widget, 0, Qt.AlignVCenter)


__all__ = ["PanelHeader"]
