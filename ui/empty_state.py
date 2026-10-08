"""État vide : une icône, un titre court, un texte court et, si l'écran en a une, une action principale.

Un état vide dit à l'utilisateur **ce qui manque et quoi faire**, sans illustration lourde. ``EmptyState`` remplace les libellés
nus (« Aucun export dans la file… ») par la même présentation partout : l'icône en discret, le titre qui ressort, le texte d'aide
qui s'efface, l'action (bouton normal) en dessous.

C'est un remplaçant direct d'un ``QLabel`` pour le code existant : ``setText`` / ``text`` / ``setWordWrap`` fonctionnent, et un
texte à plusieurs lignes se lit « premier ligne = titre, le reste = aide » (``"Aucun clip\\nDéplacez la tête de lecture."``).
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from ui.design_system import Iconography, ButtonVariant, Spacing
from ui.icons import IconLabel, IconName
from ui.theme import set_role, set_variant

MAX_TEXT_WIDTH = 360
"""Largeur maximale du texte d'aide (px) : une ligne plus longue se lit mal et fait déborder une colonne étroite."""


class _HelperLabel(QLabel):
    """Texte d'aide qui annonce sa largeur voulue (``MAX_TEXT_WIDTH``) plutôt que l'idéal étroit d'un ``QLabel`` à retour à la ligne.

    Centré dans une colonne plus large, un ``QLabel`` ordinaire se contentait d'environ 170 px : le texte tenait sur deux lignes
    et la seconde était rognée par une mise en page serrée. Rien n'est imposé en minimum : une colonne étroite réduit toujours
    le texte (il retourne alors à la ligne), seule la préférence change."""

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt
        if not (self.wordWrap() and self.text()):
            return super().sizeHint()
        return QSize(MAX_TEXT_WIDTH, self.heightForWidth(MAX_TEXT_WIDTH))


class EmptyState(QWidget):
    """Icône + titre + texte + action facultative, centrés. ``action_triggered`` est émis au clic sur l'action."""

    action_triggered = Signal()

    def __init__(self, text: str = "", *, icon: IconName | None = None, action: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("emptyState")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(Spacing.lg, Spacing.lg, Spacing.lg, Spacing.lg)
        layout.setSpacing(Spacing.sm)
        layout.setAlignment(Qt.AlignCenter)

        self.icon_label: IconLabel | None = None
        if icon is not None:
            self.icon_label = IconLabel(icon, size=Iconography.xxl)
            self.icon_label.set_color("muted")                      # un jeton, lu à la peinture : suit le thème
            layout.addWidget(self.icon_label, 0, Qt.AlignHCenter)

        self.title_label = QLabel()
        self.title_label.setAlignment(Qt.AlignCenter)
        self.title_label.setWordWrap(True)
        set_role(self.title_label, "panel-title")
        layout.addWidget(self.title_label)

        self.text_label = _HelperLabel()
        self.text_label.setAlignment(Qt.AlignCenter)
        self.text_label.setWordWrap(True)
        self.text_label.setMaximumWidth(MAX_TEXT_WIDTH)
        set_role(self.text_label, "helper")
        layout.addWidget(self.text_label, 0, Qt.AlignHCenter)

        self._layout = layout
        self.action_button: QPushButton | None = None             # créé à la première action : pas de bouton vide sinon

        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self.setText(text)
        self.set_action(action)

    # -- contenu ------------------------------------------------------------------------------------------

    def setText(self, text: str) -> None:  # noqa: N802 - API d'un QLabel, pour le code existant
        """Deux lignes ou plus : la première est le titre, le reste est le texte d'aide. Une seule : texte d'aide seul."""
        title, separator, body = text.partition("\n")
        if not separator:
            title, body = "", text
        self.title_label.setText(title)
        self.title_label.setVisible(bool(title))
        self.text_label.setText(body)
        self.text_label.setVisible(bool(body))
        self._reserve_text_height()

    def _reserve_text_height(self) -> None:
        """Réserve la hauteur que le texte d'aide occupe à la largeur dont il disposera (jamais la largeur : voir ``_HelperLabel``)."""
        label = self.text_label
        body = label.text()
        width = min(MAX_TEXT_WIDTH, max(1, self.width() - 2 * Spacing.lg))
        wanted = label.fontMetrics().boundingRect(0, 0, width, 0, int(Qt.TextWordWrap), body).height() if body else 0
        if wanted != label.minimumHeight():
            label.setMinimumHeight(wanted)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt
        super().resizeEvent(event)
        self._reserve_text_height()

    def text(self) -> str:
        title, body = self.title_label.text(), self.text_label.text()
        return f"{title}\n{body}" if title and body else (title or body)

    def set_title_and_text(self, title: str, text: str) -> None:
        self.setText(f"{title}\n{text}" if title else text)

    def set_action(self, label: str) -> None:
        """Texte de l'action principale ; vide : pas d'action."""
        if self.action_button is None:
            if not label:
                return
            button = QPushButton()
            button.setCursor(Qt.PointingHandCursor)
            button.setFocusPolicy(Qt.TabFocus)                    # atteint avec Tab, jamais au clic : il volerait les touches de lecture
            set_variant(button, ButtonVariant.SECONDARY)
            button.clicked.connect(self.action_triggered)
            self._layout.addWidget(button, 0, Qt.AlignHCenter)
            self.action_button = button
        self.action_button.setText(label)
        self.action_button.setVisible(bool(label))

    def setWordWrap(self, enabled: bool) -> None:  # noqa: N802 - API d'un QLabel (le retour à la ligne est toujours actif ici)
        self.title_label.setWordWrap(True)
        self.text_label.setWordWrap(True)

__all__ = ["EmptyState"]
