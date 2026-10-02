"""Barre de navigation des séquences, au-dessus de la timeline.

Affiche en permanence **quelle séquence est éditée** :

    ‹  ›  ↑   Master › Scene 01 › Intro        [Séquences ▾]

- ``‹`` / ``›`` : séquence précédente / suivante (historique de navigation) ;
- ``↑`` : retour à la séquence parente ;
- fil d'Ariane cliquable : chaque niveau rouvre la séquence correspondante ;
  le dernier (séquence active) est en gras ;
- menu « Séquences » : ouvrir directement n'importe quelle séquence.

Le widget n'a aucune logique métier : il émet des signaux, la fenêtre
principale décide (voir ``ui/main_window_mixins/sequences.py``).
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMenu, QToolButton, QWidget

from ui.i18n import translate
from ui.icons import IconButton, IconName
from ui.theme import label_style


class SequenceNavigationBar(QWidget):
    """Fil d'Ariane + précédent / suivant / parent + menu des séquences."""

    open_requested = Signal(str)
    back_requested = Signal()
    forward_requested = Signal()
    parent_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("sequenceNavigationBar")
        self._crumbs: list[tuple[str, str]] = []
        self._sequences: list[tuple[str, str]] = []
        self._crumb_buttons: list[QToolButton] = []
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 2, 8, 2)
        layout.setSpacing(4)

        self.back_button = self._nav_button("‹", "sequence.nav.back")
        self.back_button.clicked.connect(self.back_requested.emit)
        self.forward_button = self._nav_button("›", "sequence.nav.forward")
        self.forward_button.clicked.connect(self.forward_requested.emit)
        self.parent_button = IconButton(
            icon=IconName.ARROW_UP, tooltip=translate("sequence.nav.parent"), size=22
        )
        self.parent_button.clicked.connect(self.parent_requested.emit)
        for button in (self.back_button, self.forward_button, self.parent_button):
            layout.addWidget(button)

        self.caption = QLabel(translate("sequence.nav.caption"))
        self.caption.setStyleSheet(label_style(9, "muted", 800))
        layout.addSpacing(6)
        layout.addWidget(self.caption)

        self._crumb_host = QWidget()
        self._crumb_layout = QHBoxLayout(self._crumb_host)
        self._crumb_layout.setContentsMargins(0, 0, 0, 0)
        self._crumb_layout.setSpacing(2)
        layout.addWidget(self._crumb_host)
        layout.addStretch(1)

        self.sequences_button = QToolButton()
        self.sequences_button.setObjectName("sequencesMenuButton")
        self.sequences_button.setText(translate("sequence.nav.menu"))
        self.sequences_button.setPopupMode(QToolButton.InstantPopup)
        self.sequences_button.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.sequences_menu = QMenu(self.sequences_button)
        self.sequences_button.setMenu(self.sequences_menu)
        layout.addWidget(self.sequences_button)
        self.set_state([], [], can_back=False, can_forward=False, can_parent=False)

    def _nav_button(self, glyph: str, tooltip_key: str) -> QToolButton:
        button = QToolButton()
        button.setText(glyph)
        button.setToolTip(translate(tooltip_key))
        button.setFixedSize(22, 22)
        button.setCursor(Qt.PointingHandCursor)
        button.setFocusPolicy(Qt.NoFocus)
        return button

    # ------------------------------------------------------------------

    def set_state(
        self,
        crumbs: list[tuple[str, str]],
        sequences: list[tuple[str, str]],
        *,
        can_back: bool,
        can_forward: bool,
        can_parent: bool,
    ) -> None:
        """Met à jour le fil d'Ariane, le menu et l'état des boutons."""
        self._crumbs = list(crumbs)
        self._sequences = list(sequences)
        self.back_button.setEnabled(can_back)
        self.forward_button.setEnabled(can_forward)
        self.parent_button.setEnabled(can_parent)
        while self._crumb_layout.count():
            item = self._crumb_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # Masqué tout de suite : ``deleteLater`` seul laissait l'ancien
                # niveau peint sous le nouveau jusqu'au prochain tour de boucle.
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        self._crumb_buttons = []
        for index, (sequence_id, name) in enumerate(self._crumbs):
            if index:
                separator = QLabel("›")
                separator.setStyleSheet(label_style(12, "muted", 600))
                self._crumb_layout.addWidget(separator)
            button = QToolButton()
            button.setText(name)
            button.setAutoRaise(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setFocusPolicy(Qt.NoFocus)
            is_active = index == len(self._crumbs) - 1
            button.setProperty("activeSequence", is_active)
            weight = 800 if is_active else 500
            button.setStyleSheet(f"QToolButton {{ {label_style(12, 'text' if is_active else 'muted', weight)} }}")
            button.setToolTip(
                translate("sequence.nav.active") if is_active else translate("sequence.nav.open_level")
            )
            button.clicked.connect(lambda _checked=False, sid=sequence_id: self.open_requested.emit(sid))
            self._crumb_layout.addWidget(button)
            self._crumb_buttons.append(button)
        self.sequences_menu.clear()
        active = self._crumbs[-1][0] if self._crumbs else ""
        for sequence_id, name in self._sequences:
            action = self.sequences_menu.addAction(name)
            action.setCheckable(True)
            action.setChecked(sequence_id == active)
            action.triggered.connect(lambda _checked=False, sid=sequence_id: self.open_requested.emit(sid))

    @property
    def breadcrumb_names(self) -> list[str]:
        return [name for _sid, name in self._crumbs]

    @property
    def active_name(self) -> str:
        return self._crumbs[-1][1] if self._crumbs else ""

    def retranslate(self) -> None:
        self.back_button.setToolTip(translate("sequence.nav.back"))
        self.forward_button.setToolTip(translate("sequence.nav.forward"))
        self.parent_button.setToolTip(translate("sequence.nav.parent"))
        self.caption.setText(translate("sequence.nav.caption"))
        self.sequences_button.setText(translate("sequence.nav.menu"))


__all__ = ["SequenceNavigationBar"]
