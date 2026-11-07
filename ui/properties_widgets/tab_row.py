"""Rangée d'onglets de l'inspecteur : **une seule ligne**, les onglets les moins utilisés sous « Plus ».

Avant, sept boutons retournaient à la ligne (deux rangées de 280 px de large). Sur un portable, la rangée seule mangeait ~70 px du
haut de l'inspecteur. Les quatre onglets usuels (Clip, Couleur, Audio, Effets) restent des boutons ; Graphiques, Compositing et
Suivi sont dans le menu du bouton « Plus » (et un onglet de plus y passe quand la colonne est trop étroite, par exemple avec une
police plus large). Les sept boutons existent toujours (``inspector_tab_buttons``, indices historiques) : seul leur affichage change,
aucun libellé n'est modifié, et le bouton « Plus » s'allume quand l'onglet choisi est l'un de ceux qu'il contient.
"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QEvent, QSize, Qt, Signal
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import QHBoxLayout, QMenu, QPushButton, QSizePolicy, QWidget

from ui.design_system import Sizes, Spacing, Typography
from ui.i18n import translate
from ui.theme import COLORS

#: Onglets toujours en bouton (les premiers, dans l'ordre d'affichage) ; les suivants sont sous « Plus ».
ALWAYS_SHOWN = 4


def tab_stylesheet(name: str) -> str:
    """Feuille d'un onglet de l'inspecteur (texte de ``Typography.micro`` = 12 px, jamais en dessous)."""
    return (
        f"QPushButton#{name} {{ background: transparent;"
        f" color: {COLORS['muted']}; border: 1px solid transparent;"
        f" border-radius: 0; padding: 4px 7px;"
        f" font-weight: 600; font-size: {Typography.micro}px; }}"
        f"QPushButton#{name}:hover {{ color: {COLORS['text']};"
        f" background: transparent; }}"
        f"QPushButton#{name}:checked {{ color: {COLORS['accent']};"
        f" background: transparent; border: none;"
        f" border-bottom: 2px solid {COLORS['accent']}; }}"
        f"QPushButton#{name}:focus {{ border: 1px solid {COLORS['accent']}; }}"
        f"QPushButton#{name}::menu-indicator {{ image: none; width: 0; }}"
    )


class InspectorTabRow(QWidget):
    """Onglets en une ligne + « Plus » ; ``tab_chosen(index)`` : un onglet du menu (indice du bouton dans ``buttons``)."""

    tab_chosen = Signal(int)

    def __init__(self, buttons: Sequence[QPushButton], ordered: Sequence[int], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._buttons = list(buttons)
        self._order = list(ordered)                     # indices des boutons dans l'ordre d'affichage
        self._shown = len(self._order)
        self._fitting = False
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, Spacing.xs)
        layout.setSpacing(Spacing.xs)
        for index in self._order:
            button = self._buttons[index]
            button.setMinimumHeight(Sizes.control_min)
            layout.addWidget(button)
        self.more_button = QPushButton(translate("inspector.tab.more"), self)
        self.more_button.setObjectName("inspectorMoreTabs")
        self.more_button.setCheckable(True)
        self.more_button.setCursor(Qt.PointingHandCursor)
        self.more_button.setFocusPolicy(Qt.NoFocus)
        self.more_button.setMinimumHeight(Sizes.control_min)
        self.more_button.setStyleSheet(tab_stylesheet("inspectorMoreTabs"))
        self.more_button.setToolTip(translate("inspector.tab.more.tip"))
        self.more_button.setAccessibleName(translate("inspector.tab.more"))
        self.more_button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._menu = QMenu(self.more_button)
        self._menu.aboutToShow.connect(self._fill_menu)
        self._menu.aboutToHide.connect(self.sync_checked)     # le clic bascule le bouton : l'état vrai est celui de l'onglet choisi
        self.more_button.setMenu(self._menu)
        layout.addWidget(self.more_button)
        layout.addStretch(1)                             # « Plus » suit le dernier onglet montré
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)    # la rangée suit la largeur de l'inspecteur, jamais l'inverse
        self.fit()

    # -- affichage --------------------------------------------------------------------------------------------------

    def shown_count(self) -> int:
        """Nombre d'onglets montrés en boutons (le reste est sous « Plus »)."""
        return self._shown

    def hidden_tabs(self) -> list[int]:
        """Indices (des boutons) des onglets sous « Plus »."""
        return self._order[self._shown:]

    def _plus_width(self) -> int:
        return self.more_button.sizeHint().width()

    def fit(self) -> None:
        """Choisit combien d'onglets tiennent en bouton à la largeur actuelle (quatre au plus), le reste passe sous « Plus »."""
        if self._fitting:
            return
        self._fitting = True
        try:
            spacing = self.layout().spacing()
            room = self.width() - self._plus_width() - spacing
            shown = 0
            used = 0
            for index in self._order[:ALWAYS_SHOWN]:
                width = self._buttons[index].sizeHint().width() + spacing
                if shown >= 1 and used + width > room and self.width() > 0:
                    break
                used += width
                shown += 1
            self._shown = max(1, shown)
            for position, index in enumerate(self._order):
                self._buttons[index].setVisible(position < self._shown)
            self.sync_checked()
        finally:
            self._fitting = False

    def sync_checked(self) -> None:
        """« Plus » est allumé quand l'onglet choisi est l'un de ceux qu'il contient."""
        hidden = set(self.hidden_tabs())
        self.more_button.setChecked(any(self._buttons[i].isChecked() for i in hidden))

    def retranslate(self) -> None:
        self.more_button.setText(translate("inspector.tab.more"))
        self.more_button.setToolTip(translate("inspector.tab.more.tip"))
        self.more_button.setAccessibleName(translate("inspector.tab.more"))
        self.fit()

    def resizeEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().resizeEvent(event)
        self.fit()

    def event(self, event) -> bool:  # noqa: N802 - API Qt
        handled = super().event(event)
        if event.type() == QEvent.Type.LayoutRequest:
            self.fit()                          # un onglet a changé de largeur idéale (police, langue) : le partage suit, sans minuteur
        return handled

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - API Qt
        first = self._buttons[self._order[0]].sizeHint().width()
        return QSize(first + self._plus_width() + 2 * Spacing.xs, Sizes.control_min + Spacing.xs)

    def sizeHint(self) -> QSize:  # noqa: N802 - API Qt
        return self.minimumSizeHint()

    # -- menu « Plus » -----------------------------------------------------------------------------------------------

    def _fill_menu(self) -> None:
        """Les onglets sous « Plus », à jour (libellés traduits, onglet choisi coché)."""
        self._menu.clear()
        group = QActionGroup(self._menu)
        group.setExclusive(True)
        for index in self.hidden_tabs():
            button = self._buttons[index]
            action = QAction(button.text(), self._menu)
            action.setCheckable(True)
            action.setChecked(button.isChecked())
            action.triggered.connect(lambda _checked=False, i=index: self.tab_chosen.emit(i))
            group.addAction(action)
            self._menu.addAction(action)


__all__ = ["ALWAYS_SHOWN", "InspectorTabRow", "tab_stylesheet"]
