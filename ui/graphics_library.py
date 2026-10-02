"""Section Graphiques du panneau Médias : ajouter des calques, voir la pile.

En haut, les gestes du débutant : un clic ajoute un titre, une forme ou un
aplat à la tête de lecture (il apparaît dans le viewer, où on le déplace
directement). En dessous, le panneau Calques (:mod:`ui.layers_panel`) pour
la hiérarchie : ordre, groupes, parentage, visibilité, verrou, presets.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QGridLayout, QLabel, QVBoxLayout, QWidget

from ui.design_system import Sizes, Spacing
from ui.icons import IconButton, IconName
from ui.layers_panel import LayersPanel
from ui.theme import label_style


class GraphicsLibraryView(QWidget):
    create_requested = Signal(str)
    import_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(Spacing.sm, Spacing.sm, Spacing.sm, Spacing.sm)
        layout.setSpacing(Spacing.sm)

        title = QLabel("MOTION GRAPHICS")
        title.setStyleSheet(label_style(11, "muted_strong", 800))
        layout.addWidget(title)
        intro = QLabel(
            "Ajoutez un élément à la tête de lecture, puis déplacez-le directement "
            "dans le viewer. Chaque élément est un clip animable."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet(label_style(11, "muted", 500))
        layout.addWidget(intro)

        entries = (
            ("Titre", "Texte éditable", "text", IconName.TEXT),
            ("Forme", "Rectangle coloré", "rectangle", IconName.COLOR),
            ("Aplat", "Fond plein cadre", "solid", IconName.FILM),
        )
        grid = QGridLayout()
        grid.setSpacing(Spacing.xs)
        self.create_buttons: dict[str, IconButton] = {}
        for index, (name, description, kind, icon) in enumerate(entries):
            button = IconButton(
                icon=icon, text=name, tooltip=f"Ajouter : {name} — {description}",
                square=False, size=Sizes.icon_button,
            )
            button.clicked.connect(lambda _checked=False, value=kind: self.create_requested.emit(value))
            self.create_buttons[kind] = button
            grid.addWidget(button, index // 2, index % 2)
        self.import_image_button = IconButton(
            icon=IconName.IMPORT,
            text="Image…",
            tooltip="Créer un calque graphique depuis une image",
            square=False,
            size=Sizes.icon_button,
        )
        self.import_image_button.clicked.connect(self.import_requested.emit)
        grid.addWidget(self.import_image_button, 1, 1)
        layout.addLayout(grid)

        self.layers_panel = LayersPanel(self)
        self.layers_panel.import_image_requested.connect(self.import_requested.emit)
        layout.addWidget(self.layers_panel, 1)


__all__ = ["GraphicsLibraryView"]
