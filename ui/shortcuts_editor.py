"""Éditeur des raccourcis clavier (onglet « Raccourcis » des Préférences).

Ce widget ne contient aucune règle : il affiche
:class:`ui.shortcut_manager.ShortcutManager` et lui délègue chaque
modification. Validation, conflits et raccourcis réservés viennent de
:mod:`core.shortcuts` ; l'application est immédiate et la persistance
est faite par le gestionnaire.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QKeySequence
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.shortcuts import (
    MAX_CHORD_STEPS,
    AssignResult,
    AssignStatus,
    Category,
    Command,
    format_sequence,
)
from ui.i18n import translate
from ui.shortcut_manager import ShortcutManager
from ui.theme import label_style
from ui.search_field import SearchField

_COMMAND_ID_ROLE = Qt.UserRole


class ShortcutsEditor(QWidget):
    """Recherche, filtre et modification des raccourcis."""

    def __init__(self, manager: ShortcutManager, parent=None) -> None:
        super().__init__(parent)
        self._manager = manager
        self._items: dict[str, QTreeWidgetItem] = {}
        self._loading = False
        self._pending: tuple[str, int, str] | None = None
        self._build_ui()
        self._populate()
        self.retranslate()
        manager.changed.connect(self.refresh)

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        filters = QHBoxLayout()
        self.search_edit = SearchField()
        self.search_edit.textChanged.connect(self._apply_filter)
        self.category_combo = QComboBox()
        self.category_combo.currentIndexChanged.connect(self._apply_filter)
        filters.addWidget(self.search_edit, 1)
        filters.addWidget(self.category_combo)
        layout.addLayout(filters)

        self.tree = QTreeWidget()
        self.tree.setObjectName("shortcutsTree")
        self.tree.setColumnCount(4)
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QTreeWidget.SingleSelection)
        self.tree.setMinimumHeight(260)
        self.tree.currentItemChanged.connect(self._on_selection_changed)
        layout.addWidget(self.tree, 1)

        self.detail_label = QLabel()
        self.detail_label.setStyleSheet(label_style(13, "text", 600))
        layout.addWidget(self.detail_label)

        self.primary_label = QLabel()
        self.primary_edit = self._make_edit(0)
        self.primary_clear = QPushButton()
        self.primary_clear.clicked.connect(lambda: self._clear_slot(0))
        layout.addLayout(self._row(self.primary_label, self.primary_edit, self.primary_clear))

        self.secondary_label = QLabel()
        self.secondary_edit = self._make_edit(1)
        self.secondary_clear = QPushButton()
        self.secondary_clear.clicked.connect(lambda: self._clear_slot(1))
        layout.addLayout(
            self._row(self.secondary_label, self.secondary_edit, self.secondary_clear)
        )

        status_row = QHBoxLayout()
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        self.reassign_button = QPushButton()
        self.reassign_button.clicked.connect(self._on_reassign)
        self.reassign_button.hide()
        status_row.addWidget(self.status_label, 1)
        status_row.addWidget(self.reassign_button)
        layout.addLayout(status_row)

        buttons = QHBoxLayout()
        self.reset_button = QPushButton()
        self.reset_button.clicked.connect(self._on_reset_command)
        self.reset_all_button = QPushButton()
        self.reset_all_button.clicked.connect(self._on_reset_all)
        buttons.addWidget(self.reset_button)
        buttons.addStretch(1)
        buttons.addWidget(self.reset_all_button)
        layout.addLayout(buttons)

    @staticmethod
    def _row(label: QLabel, edit: QKeySequenceEdit, clear: QPushButton) -> QHBoxLayout:
        row = QHBoxLayout()
        label.setMinimumWidth(150)
        row.addWidget(label)
        row.addWidget(edit, 1)
        row.addWidget(clear)
        return row

    def _make_edit(self, slot: int) -> QKeySequenceEdit:
        edit = QKeySequenceEdit()
        edit.setMaximumSequenceLength(MAX_CHORD_STEPS)
        # ``editingFinished`` : un accord se saisit en plusieurs frappes ;
        # on n'applique qu'une fois la saisie terminée.
        edit.editingFinished.connect(
            lambda s=slot, e=edit: self._on_edited(s, e.keySequence())
        )
        return edit

    def _populate(self) -> None:
        self.tree.clear()
        self._items.clear()
        for command in self._manager.shortcut_map.commands:
            item = QTreeWidgetItem()
            item.setData(0, _COMMAND_ID_ROLE, command.id)
            self.tree.addTopLevelItem(item)
            self._items[command.id] = item
        self.category_combo.blockSignals(True)
        self.category_combo.clear()
        self.category_combo.addItem("", None)
        for category in Category:
            self.category_combo.addItem("", category.value)
        self.category_combo.blockSignals(False)

    # ------------------------------------------------------------------
    # Texte
    # ------------------------------------------------------------------

    @staticmethod
    def command_name(command: Command) -> str:
        return translate(command.name_key)

    @staticmethod
    def category_name(category: Category) -> str:
        return translate(f"shortcuts.category.{category.value}")

    def retranslate(self) -> None:
        """Applique la langue courante (appelé aussi à chaud)."""
        self.search_edit.setPlaceholderText(translate("shortcuts.search"))
        self.category_combo.setItemText(0, translate("shortcuts.all_categories"))
        for index, category in enumerate(Category, start=1):
            self.category_combo.setItemText(index, self.category_name(category))
        self.tree.setHeaderLabels(
            [
                translate("shortcuts.col.command"),
                translate("shortcuts.col.category"),
                translate("shortcuts.col.primary"),
                translate("shortcuts.col.secondary"),
            ]
        )
        self.primary_label.setText(translate("shortcuts.primary"))
        self.secondary_label.setText(translate("shortcuts.secondary"))
        for button in (self.primary_clear, self.secondary_clear):
            button.setText(translate("shortcuts.clear"))
        placeholder = translate("shortcuts.press_keys")
        self.primary_edit.setToolTip(placeholder)
        self.secondary_edit.setToolTip(placeholder)
        self.reset_button.setText(translate("shortcuts.reset_command"))
        self.reset_all_button.setText(translate("shortcuts.reset_all"))
        self.reassign_button.setText(translate("shortcuts.reassign"))
        self.refresh()

    # ------------------------------------------------------------------
    # Affichage
    # ------------------------------------------------------------------

    def refresh(self) -> None:
        """Relit la configuration : tableau, éditeurs et filtre."""
        shortcut_map = self._manager.shortcut_map
        none = translate("shortcuts.none")
        for command in shortcut_map.commands:
            item = self._items[command.id]
            sequences = shortcut_map.sequences(command.id)
            item.setText(0, self.command_name(command))
            item.setText(1, self.category_name(command.category))
            item.setText(2, format_sequence(sequences[0]) if sequences else none)
            item.setText(3, format_sequence(sequences[1]) if len(sequences) > 1 else "")
            font = QFont(item.font(0))
            font.setBold(not shortcut_map.is_default(command.id))
            for column in range(4):
                item.setFont(column, font)
        for column in range(4):
            self.tree.resizeColumnToContents(column)
        self._load_selected()
        self._apply_filter()

    def _selected_id(self) -> str | None:
        item = self.tree.currentItem()
        return item.data(0, _COMMAND_ID_ROLE) if item is not None else None

    def select_command(self, command_id: str) -> None:
        """Sélectionne une commande (aussi utile aux tests)."""
        item = self._items.get(command_id)
        if item is not None:
            if item.isHidden():
                self.search_edit.clear()
                self.category_combo.setCurrentIndex(0)
            self.tree.setCurrentItem(item)

    def _load_selected(self) -> None:
        """Remplit les éditeurs avec la commande sélectionnée."""
        command_id = self._selected_id()
        shortcut_map = self._manager.shortcut_map
        self._loading = True
        try:
            self.primary_edit.clear()
            self.secondary_edit.clear()
            if command_id is None:
                self.detail_label.setText(translate("shortcuts.select_prompt"))
                sequences: tuple[str, ...] = ()
            else:
                self.detail_label.setText(self.command_name(shortcut_map.command(command_id)))
                sequences = shortcut_map.sequences(command_id)
            if sequences:
                self.primary_edit.setKeySequence(QKeySequence(sequences[0]))
            if len(sequences) > 1:
                self.secondary_edit.setKeySequence(QKeySequence(sequences[1]))
            selected = command_id is not None
            self.primary_edit.setEnabled(selected)
            # Le secondaire n'a de sens qu'à côté d'un principal.
            self.secondary_edit.setEnabled(bool(sequences))
            self.primary_clear.setEnabled(bool(sequences))
            self.secondary_clear.setEnabled(len(sequences) > 1)
            self.reset_button.setEnabled(
                selected and not shortcut_map.is_default(command_id)
            )
        finally:
            self._loading = False

    def _apply_filter(self, *_args) -> None:
        needle = self.search_edit.text().strip().lower()
        category = self.category_combo.currentData()
        shortcut_map = self._manager.shortcut_map
        for command in shortcut_map.commands:
            item = self._items[command.id]
            visible = category is None or command.category.value == category
            if visible and needle:
                haystack = " ".join(
                    (
                        self.command_name(command),
                        self.category_name(command.category),
                        command.id,
                        *(format_sequence(s) for s in shortcut_map.sequences(command.id)),
                        *shortcut_map.sequences(command.id),
                    )
                ).lower()
                visible = needle in haystack
            item.setHidden(not visible)

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------

    def _set_status(self, text: str, *, warning: bool = False) -> None:
        self.status_label.setText(text)
        self.status_label.setStyleSheet(label_style(12, "warning" if warning else "muted"))

    def _command_label(self, command_id: str) -> str:
        return self.command_name(self._manager.shortcut_map.command(command_id))

    def _explain(self, result: AssignResult) -> str:
        sequence = format_sequence(result.sequence) if result.sequence else ""
        if result.status is AssignStatus.CONFLICT:
            names = ", ".join(self._command_label(c) for c in result.conflicts)
            return translate("shortcuts.msg.conflict", sequence=sequence, command=names)
        if result.status is AssignStatus.RESERVED:
            return translate(f"shortcuts.msg.reserved.{result.reason}", sequence=sequence)
        if result.status is AssignStatus.DUPLICATE:
            return translate("shortcuts.msg.duplicate", sequence=sequence)
        return translate("shortcuts.msg.invalid")

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _on_selection_changed(self, *_args) -> None:
        self._pending = None
        self.reassign_button.hide()
        self._set_status("")
        self._load_selected()

    def _on_edited(self, slot: int, sequence: QKeySequence) -> None:
        command_id = self._selected_id()
        if self._loading or command_id is None or sequence.isEmpty():
            return
        text = sequence.toString(QKeySequence.PortableText)
        result = self._manager.assign(command_id, slot, text)
        self._pending = None
        self.reassign_button.hide()
        if result.ok:
            self._set_status("")
            return
        if result.status is AssignStatus.CONFLICT and result.sequence:
            self._pending = (command_id, slot, result.sequence)
            self.reassign_button.show()
        self._set_status(self._explain(result), warning=True)
        self._load_selected()  # remet l'éditeur sur la valeur réellement active

    def _on_reassign(self) -> None:
        if self._pending is None:
            return
        command_id, slot, sequence = self._pending
        result = self._manager.assign(command_id, slot, sequence, replace=True)
        self._pending = None
        self.reassign_button.hide()
        if result.ok and result.displaced:
            names = ", ".join(self._command_label(c) for c in result.displaced)
            self._set_status(
                translate(
                    "shortcuts.msg.reassigned",
                    sequence=format_sequence(sequence),
                    command=names,
                )
            )
        elif not result.ok:
            self._set_status(self._explain(result), warning=True)

    def _clear_slot(self, slot: int) -> None:
        command_id = self._selected_id()
        if command_id is None:
            return
        self._pending = None
        self.reassign_button.hide()
        self._manager.assign(command_id, slot, None)
        self._set_status("")

    def _on_reset_command(self) -> None:
        command_id = self._selected_id()
        if command_id is None:
            return
        affected = self._manager.reset(command_id)
        self._pending = None
        self.reassign_button.hide()
        if affected:
            names = ", ".join(self._command_label(c) for c in affected)
            sequences = ", ".join(
                format_sequence(s) for s in self._manager.shortcut_map.sequences(command_id)
            )
            self._set_status(
                translate("shortcuts.msg.reassigned", sequence=sequences, command=names)
            )
        else:
            self._set_status("")

    def _on_reset_all(self) -> None:
        answer = QMessageBox.question(
            self,
            translate("shortcuts.col.primary"),
            translate("shortcuts.reset_all_confirm"),
        )
        if answer == QMessageBox.Yes:
            self._pending = None
            self.reassign_button.hide()
            self._set_status("")
            self._manager.reset_all()
