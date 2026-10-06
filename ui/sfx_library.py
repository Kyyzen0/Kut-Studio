"""Page « SFX » de la section Audio : la bibliothèque de sons synthétisés (core.sfx_synth).

Écouter un son, le poser à la tête de lecture, ou en poser un à chaque cut de la piste vidéo. Les sons sont calculés à
la première utilisation (une fraction de seconde) puis gardés dans la bibliothèque de l'utilisateur.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget

from core.sfx_synth import CATALOG
from ui import i18n
from ui.design_system import Spacing
from ui.theme import label_style

ON_CUTS_DEFAULT = ("whoosh", "whoosh_long", "whoosh_short")
"""Sons qui tournent sur les cuts quand aucun n'est sélectionné (trois whooshes : le montage respire)."""


class SfxLibraryView(QWidget):
    add_requested = Signal(str)               # identifiant du son : à la tête de lecture
    on_cuts_requested = Signal(list)          # identifiants : un son par cut, en boucle

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(Spacing.sm, Spacing.sm, Spacing.sm, Spacing.sm)
        layout.setSpacing(Spacing.sm)
        self.intro = QLabel()
        self.intro.setWordWrap(True)
        self.intro.setStyleSheet(label_style(11, "muted", 500))
        layout.addWidget(self.intro)
        self.list = QListWidget(objectName="sfxList")
        self.list.setSelectionMode(QListWidget.ExtendedSelection)
        for spec, _render in CATALOG:
            item = QListWidgetItem()
            item.setData(Qt.UserRole, spec.id)
            item.setData(Qt.UserRole + 1, spec.category)
            self.list.addItem(item)
        self.list.itemDoubleClicked.connect(lambda item: self.add_requested.emit(str(item.data(Qt.UserRole))))
        layout.addWidget(self.list, 1)
        row = QHBoxLayout()
        row.setSpacing(Spacing.xs)
        self.play_button = QPushButton()
        self.play_button.clicked.connect(self._play)
        self.add_button = QPushButton()
        self.add_button.clicked.connect(self._add)
        self.cuts_button = QPushButton()
        self.cuts_button.clicked.connect(self._on_cuts)
        for button in (self.play_button, self.add_button, self.cuts_button):
            button.setFocusPolicy(Qt.TabFocus)              # un clic ne vole pas le focus de la timeline
            row.addWidget(button)
        layout.addLayout(row)
        self._player: object | None = None
        self.retranslate()

    def retranslate(self) -> None:
        self.intro.setText(i18n.translate("sfx.intro"))
        for index in range(self.list.count()):
            item = self.list.item(index)
            item.setText(i18n.translate(f"sfx.name.{item.data(Qt.UserRole)}"))
            item.setToolTip(i18n.translate(f"sfx.category.{item.data(Qt.UserRole + 1)}"))
        self.play_button.setText(i18n.translate("sfx.play"))
        self.add_button.setText(i18n.translate("sfx.add"))
        self.cuts_button.setText(i18n.translate("sfx.on_cuts"))
        self.cuts_button.setToolTip(i18n.translate("sfx.on_cuts_tooltip"))

    def selected_ids(self) -> list[str]:
        return [str(item.data(Qt.UserRole)) for item in self.list.selectedItems()]

    def _add(self) -> None:
        for sfx_id in self.selected_ids()[:1]:
            self.add_requested.emit(sfx_id)

    def _on_cuts(self) -> None:
        self.on_cuts_requested.emit(self.selected_ids() or list(ON_CUTS_DEFAULT))

    def _play(self) -> None:
        """Écoute du son sélectionné (synthétisé au besoin)."""
        ids = self.selected_ids()
        if not ids:
            return
        from PySide6.QtMultimedia import QSoundEffect

        from core.sfx_synth import ensure_sfx_file

        if self._player is None:
            self._player = QSoundEffect(self)
        self._player.setSource(QUrl.fromLocalFile(str(ensure_sfx_file(ids[0]))))
        self._player.play()


__all__ = ["ON_CUTS_DEFAULT", "SfxLibraryView"]
