"""Bibliothèque légère des générateurs de calques graphiques."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ui.design_system import Sizes, Spacing
from ui.icons import IconButton, IconName
from ui.theme import COLORS, label_style


class GraphicsLibraryView(QWidget):
    create_requested = Signal(str)
    import_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(Spacing.sm, Spacing.sm, Spacing.sm, Spacing.sm)
        layout.setSpacing(Spacing.sm)

        title = QLabel("CALQUES GRAPHIQUES")
        title.setStyleSheet(label_style(11, "muted_strong", 800))
        layout.addWidget(title)
        intro = QLabel(
            "Ajoutez un titre, une forme, un aplat ou une image à la tête "
            "de lecture. Chaque élément devient un clip animable sur G1."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet(label_style(11, "muted", 500))
        layout.addWidget(intro)

        entries = (
            ("Titre", "Texte éditable avec contour et ombre", "text", IconName.TEXT),
            ("Rectangle", "Forme colorée redimensionnable", "rectangle", IconName.COLOR),
            ("Aplat", "Fond de couleur plein cadre", "solid", IconName.FILM),
        )
        self.create_buttons: dict[str, IconButton] = {}
        for name, description, kind, icon in entries:
            card = QFrame()
            card.setObjectName("graphicCard")
            card.setStyleSheet(
                f"QFrame#graphicCard {{ background: {COLORS['panel_alt']};"
                f" border: 1px solid {COLORS['border']}; border-radius: 8px; }}"
            )
            row = QHBoxLayout(card)
            row.setContentsMargins(Spacing.sm, Spacing.sm, Spacing.sm, Spacing.sm)
            labels = QVBoxLayout()
            heading = QLabel(name)
            heading.setStyleSheet(label_style(12, "text", 700))
            detail = QLabel(description)
            detail.setWordWrap(True)
            detail.setStyleSheet(label_style(10, "muted", 500))
            labels.addWidget(heading)
            labels.addWidget(detail)
            row.addLayout(labels, 1)
            button = IconButton(
                icon=icon,
                text="Ajouter",
                tooltip=f"Ajouter : {name}",
                square=False,
                size=Sizes.icon_button,
            )
            button.clicked.connect(
                lambda _checked=False, value=kind: self.create_requested.emit(value)
            )
            self.create_buttons[kind] = button
            row.addWidget(button)
            layout.addWidget(card)

        self.import_image_button = IconButton(
            icon=IconName.IMPORT,
            text="Importer une image…",
            tooltip="Créer un calque graphique depuis une image",
            square=False,
            size=Sizes.icon_button,
        )
        self.import_image_button.clicked.connect(self.import_requested.emit)
        layout.addWidget(self.import_image_button)
        layout.addStretch(1)


__all__ = ["GraphicsLibraryView"]
