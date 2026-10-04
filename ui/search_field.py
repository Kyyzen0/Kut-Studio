"""Champ de recherche : le même dans toute l'application (icône, hauteur, bouton d'effacement, nom accessible).

Cinq pages de bibliothèque et l'éditeur de raccourcis construisaient chacun leur ``QLineEdit`` (la même hauteur, mais aucune icône qui dise
« recherche »). ``SearchField`` en fait un seul composant : une loupe à gauche, le bouton d'effacement de Qt à droite, une hauteur
(``Sizes.search_field``) et le texte d'aide comme nom accessible, tenu à jour quand la langue change.

La loupe est **peinte**, pas ajoutée par ``addAction`` : une action crée un vrai bouton interne, qu'un lecteur d'écran et la touche Tab
rencontreraient sans nom ni raison d'être. Une loupe décorative n'est pas un contrôle.
"""

from __future__ import annotations

from PySide6.QtCore import QRect
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QLineEdit, QWidget

from ui.design_system import Iconography, Sizes, Spacing
from ui.icons import IconName, make_icon

_ICON_OPACITY = 0.65


class SearchField(QLineEdit):
    """``QLineEdit`` de recherche : loupe + effacement ; ``setPlaceholderText`` renomme aussi le champ pour les lecteurs d'écran."""

    def __init__(self, placeholder: str = "", *, object_name: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        if object_name:
            self.setObjectName(object_name)
        self.setClearButtonEnabled(True)
        self.setFixedHeight(Sizes.search_field)
        self._icon = make_icon(IconName.SEARCH)                    # suit la palette à la peinture
        self.setTextMargins(Spacing.sm + Iconography.sm, 0, 0, 0)  # le texte et l'aide démarrent après la loupe
        self.setPlaceholderText(placeholder)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setOpacity(_ICON_OPACITY)
        size = Iconography.sm
        self._icon.paint(painter, QRect(Spacing.sm, (self.height() - size) // 2, size, size))
        painter.end()

    def setPlaceholderText(self, text: str) -> None:  # noqa: N802 - API Qt
        super().setPlaceholderText(text)
        self.setAccessibleName(text)


__all__ = ["SearchField"]
