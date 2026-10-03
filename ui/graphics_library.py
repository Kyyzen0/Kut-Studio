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
from ui import i18n
from ui.i18n import translate
from ui.theme import label_style

_ENTRIES = (  # (type, clé du nom, clé de la description, icône)
    ("text", "text.preset.title.name", "mograph.library.text_desc", IconName.TEXT),
    ("rectangle", "menu.item.add_shape_layer", "mograph.library.rect_desc", IconName.COLOR),
    ("solid", "mograph.library.solid_name", "mograph.library.solid_desc", IconName.FILM),
)


class GraphicsLibraryView(QWidget):
    create_requested = Signal(str)
    import_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(Spacing.sm, Spacing.sm, Spacing.sm, Spacing.sm)
        layout.setSpacing(Spacing.sm)

        self._title = QLabel(translate("mograph.library.title"))
        self._title.setStyleSheet(label_style(11, "muted_strong", 800))
        layout.addWidget(self._title)
        self._intro = QLabel(translate("mograph.library.intro"))
        self._intro.setWordWrap(True)
        self._intro.setStyleSheet(label_style(11, "muted", 500))
        layout.addWidget(self._intro)

        grid = QGridLayout()
        grid.setSpacing(Spacing.xs)
        self.create_buttons: dict[str, IconButton] = {}
        for index, (kind, name_key, description_key, icon) in enumerate(_ENTRIES):
            button = IconButton(
                icon=icon, text=translate(name_key),
                tooltip=translate("mograph.library.add_tooltip", name=translate(name_key),
                                  description=translate(description_key)),
                square=False, size=Sizes.icon_button,
            )
            button.clicked.connect(lambda _checked=False, value=kind: self.create_requested.emit(value))
            self.create_buttons[kind] = button
            grid.addWidget(button, index // 2, index % 2)
        self.import_image_button = IconButton(
            icon=IconName.IMPORT,
            text=translate("mograph.layers.image"),
            tooltip=translate("mograph.library.import_tooltip"),
            square=False,
            size=Sizes.icon_button,
        )
        self.import_image_button.clicked.connect(self.import_requested.emit)
        grid.addWidget(self.import_image_button, 1, 1)
        layout.addLayout(grid)

        self.layers_panel = LayersPanel(self)
        self.layers_panel.import_image_requested.connect(self.import_requested.emit)
        layout.addWidget(self.layers_panel, 1)
        callback = self._on_language_changed
        i18n.subscribe(callback)
        self.destroyed.connect(lambda *_: i18n.unsubscribe(callback))

    def _on_language_changed(self, _code: str) -> None:
        self.retranslate()

    def retranslate(self) -> None:
        """Textes de la section dans la langue courante (le panneau Calques se retraduit lui-même)."""
        self._title.setText(translate("mograph.library.title"))
        self._intro.setText(translate("mograph.library.intro"))
        for kind, name_key, description_key, _icon in _ENTRIES:
            button = self.create_buttons[kind]
            button.setText(translate(name_key))
            button.setToolTip(translate("mograph.library.add_tooltip", name=translate(name_key),
                                        description=translate(description_key)))
        self.import_image_button.setText(translate("mograph.layers.image"))
        self.import_image_button.setToolTip(translate("mograph.library.import_tooltip"))


__all__ = ["GraphicsLibraryView"]
