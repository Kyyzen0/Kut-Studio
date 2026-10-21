"""Pages qui défilent au lieu de se chevaucher quand la colonne manque de place.

Un panneau de bibliothèque (Transitions, Effets, Titres...) a une hauteur minimale naturelle (champ de recherche, puces,
liste, boutons). Quand la colonne est plus basse (portable 13 pouces : ~400 px pour toute la bibliothèque), un widget
dont le layout est plus grand que lui-même **déborde sur ses voisins** : la bannière chevauchait la liste des préréglages.
Faire céder la fenêtre (minimum plus haut) la rendrait trop grande pour l'écran ; la page défile donc.

:class:`ScrollGuard` enveloppe une page à sa construction (jamais après coup : un widget déjà rangé dans un layout ne
change pas de parent, voir la mémoire « pyside-widget-reparent-segfault »). :class:`GuardedStack` est un
``QStackedWidget`` dont ``addWidget`` enveloppe chaque page, et dont ``currentWidget`` / ``widget`` / ``indexOf`` parlent
encore de la page d'origine : le reste du code ne voit pas la différence.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QScrollArea, QSizePolicy, QStackedWidget, QWidget


class ScrollGuard(QScrollArea):
    """Zone défilante transparente qui donne à sa page au moins sa taille minimale naturelle, puis défile."""

    def __init__(self, page: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._page = page
        self.setObjectName("scrollGuard")
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.NoFrame)
        self.setFocusPolicy(Qt.NoFocus)           # conteneur : Tab visite le contenu de la page
        self.setStyleSheet("QScrollArea#scrollGuard { background: transparent; border: none; }")
        self.viewport().setAutoFillBackground(False)
        # La page réclamait « Ignored » en hauteur pour masquer son minimum : ici, son minimum est honnête et c'est
        # la zone défilante qui le respecte.
        page.setMinimumHeight(0)
        page.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.setWidget(page)
        page.setAutoFillBackground(False)         # ``setWidget`` l'active : le contenu peindrait le fond clair du système

    @property
    def page(self) -> QWidget:
        return self._page

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - API Qt
        return QSize(0, 0)                        # la colonne reste réductible : la page défile

    def sizeHint(self) -> QSize:  # noqa: N802 - API Qt
        # La taille souhaitée reste celle de la page : le gestionnaire de panneaux s'en sert pour partager la hauteur
        # (``balance_vertical_split``) ; sans cela la colonne se contenterait de la taille d'un ``QScrollArea`` vide.
        return self._page.sizeHint()


class GuardedStack(QStackedWidget):
    """``QStackedWidget`` dont chaque page est enveloppée dans un :class:`ScrollGuard` à l'ajout."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._guards: dict[QWidget, ScrollGuard] = {}

    def addWidget(self, page: QWidget) -> int:  # noqa: N802 - API Qt
        guard = ScrollGuard(page)
        self._guards[page] = guard
        return super().addWidget(guard)

    def _unwrap(self, widget: QWidget | None) -> QWidget | None:
        return widget.page if isinstance(widget, ScrollGuard) else widget

    def currentWidget(self) -> QWidget | None:  # noqa: N802 - API Qt
        return self._unwrap(super().currentWidget())

    def widget(self, index: int) -> QWidget | None:
        return self._unwrap(super().widget(index))

    def indexOf(self, widget: QWidget) -> int:  # noqa: N802 - API Qt
        return super().indexOf(self._guards.get(widget, widget))

    def setCurrentWidget(self, widget: QWidget) -> None:  # noqa: N802 - API Qt
        super().setCurrentWidget(self._guards.get(widget, widget))


__all__ = ["GuardedStack", "ScrollGuard"]
