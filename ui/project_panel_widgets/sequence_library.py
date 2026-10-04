"""Bibliothèque des séquences du projet (page « Séquences » du panneau Médias).

Volontairement légère : la liste des séquences et six actions — créer,
ouvrir, insérer dans la séquence active, renommer, dupliquer, supprimer.
Une séquence se glisse aussi sur une piste de la timeline pour devenir un
clip imbriqué ; un double-clic l'ouvre.

Le widget n'applique rien lui-même : il émet des signaux que la fenêtre
principale traite (historique, validation des cycles, confirmation).
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QMimeData, Qt, Signal
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ui.design_system import Spacing
from ui.i18n import translate
from ui.icons import IconButton, IconName, make_icon
from ui.theme import COLORS, label_style

SEQUENCE_MIME = "application/x-kut-studio-sequence-id"


@dataclass(frozen=True)
class SequenceEntry:
    """Ligne affichée pour une séquence."""

    id: str
    name: str
    width: int
    height: int
    fps: float
    duration: float
    usage_count: int = 0
    active: bool = False
    issue: str = ""


def _format_duration(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    return f"{total // 60:02d}:{total % 60:02d}"


class _SequenceList(QListWidget):
    """Liste dont les éléments se glissent vers la timeline."""

    def startDrag(self, supported_actions) -> None:  # noqa: N802 (API Qt)
        item = self.currentItem()
        if item is None:
            return
        mime = QMimeData()
        mime.setData(SEQUENCE_MIME, str(item.data(Qt.UserRole)).encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.CopyAction, Qt.CopyAction)


class SequenceLibraryView(QWidget):
    """Liste des séquences et actions de gestion."""

    create_requested = Signal()
    open_requested = Signal(str)
    insert_requested = Signal(str)
    rename_requested = Signal(str)
    duplicate_requested = Signal(str)
    delete_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("sequenceLibrary")
        self._entries: list[SequenceEntry] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(Spacing.md, Spacing.sm, Spacing.md, Spacing.sm)
        layout.setSpacing(Spacing.sm)

        self.title = QLabel(translate("sequence.library.title"))
        self.title.setStyleSheet(label_style(11, "muted", 800))
        layout.addWidget(self.title)

        self.list = _SequenceList(self)
        self.list.setObjectName("sequenceList")
        self.list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list.setDragEnabled(True)
        self.list.setDragDropMode(QAbstractItemView.DragOnly)
        self.list.setStyleSheet(
            f"QListWidget {{ background: {COLORS['panel']}; border: 1px solid {COLORS['border']}; "
            f"border-radius: 6px; }}"
        )
        self.list.itemDoubleClicked.connect(
            lambda item: self.open_requested.emit(str(item.data(Qt.UserRole)))
        )
        self.list.currentItemChanged.connect(lambda *_: self._sync_buttons())
        layout.addWidget(self.list, 1)

        self.hint = QLabel(translate("sequence.library.hint"))
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet(label_style(10, "muted", 500))
        layout.addWidget(self.hint)

        row = QHBoxLayout()
        row.setSpacing(Spacing.xs)
        self.new_button = IconButton(icon=IconName.PLUS, tooltip=translate("sequence.action.new"))
        self.open_button = IconButton(icon=IconName.OPEN, tooltip=translate("sequence.action.open"))
        self.insert_button = IconButton(icon=IconName.IMPORT, tooltip=translate("sequence.action.insert"))
        self.rename_button = IconButton(icon=IconName.EDIT, tooltip=translate("sequence.action.rename"))
        self.duplicate_button = IconButton(icon=IconName.DUPLICATE, tooltip=translate("sequence.action.duplicate"))
        self.delete_button = IconButton(icon=IconName.TRASH, tooltip=translate("sequence.action.delete"))
        self.new_button.clicked.connect(self.create_requested.emit)
        self.open_button.clicked.connect(lambda: self._emit_for_current(self.open_requested))
        self.insert_button.clicked.connect(lambda: self._emit_for_current(self.insert_requested))
        self.rename_button.clicked.connect(lambda: self._emit_for_current(self.rename_requested))
        self.duplicate_button.clicked.connect(lambda: self._emit_for_current(self.duplicate_requested))
        self.delete_button.clicked.connect(lambda: self._emit_for_current(self.delete_requested))
        for button in (
            self.new_button, self.open_button, self.insert_button,
            self.rename_button, self.duplicate_button, self.delete_button,
        ):
            row.addWidget(button)
        row.addStretch(1)
        layout.addLayout(row)
        self._sync_buttons()

    # ------------------------------------------------------------------

    def set_sequences(self, entries: list[SequenceEntry]) -> None:
        """Remplace la liste ; conserve la sélection si la séquence existe encore."""
        selected = self.current_sequence_id()
        self._entries = list(entries)
        self.list.blockSignals(True)
        self.list.clear()
        current_row = -1
        for row, entry in enumerate(self._entries):
            details = f"{entry.width}×{entry.height} · {entry.fps:g} i/s · {_format_duration(entry.duration)}"
            if entry.usage_count:
                # Ligne à part : le panneau est étroit, rien ne doit être tronqué.
                details += "\n" + translate("sequence.library.usage", count=entry.usage_count)
            item = QListWidgetItem(f"{entry.name}\n{details}")
            if entry.issue:
                item.setIcon(make_icon(IconName.WARNING))          # un problème prime sur « active » : il demande une action
            elif entry.active:
                item.setIcon(make_icon(IconName.CHECK))
            item.setData(Qt.UserRole, entry.id)
            tooltip = translate("sequence.library.drag_hint")
            if entry.issue:
                tooltip = f"{entry.issue}\n{tooltip}"
            item.setToolTip(tooltip)
            self.list.addItem(item)
            if entry.id == selected or (current_row < 0 and selected is None and entry.active):
                current_row = row
        if current_row >= 0:
            self.list.setCurrentRow(current_row)
        self.list.blockSignals(False)
        self._sync_buttons()

    def current_sequence_id(self) -> str | None:
        item = self.list.currentItem()
        return str(item.data(Qt.UserRole)) if item is not None else None

    def select_sequence(self, sequence_id: str) -> None:
        for row in range(self.list.count()):
            if self.list.item(row).data(Qt.UserRole) == sequence_id:
                self.list.setCurrentRow(row)
                return

    def entries(self) -> list[SequenceEntry]:
        return list(self._entries)

    def _emit_for_current(self, signal) -> None:
        sequence_id = self.current_sequence_id()
        if sequence_id:
            signal.emit(sequence_id)

    def _sync_buttons(self) -> None:
        has_selection = self.current_sequence_id() is not None
        for button in (
            self.open_button, self.insert_button, self.rename_button,
            self.duplicate_button, self.delete_button,
        ):
            button.setEnabled(has_selection)
        self.delete_button.setEnabled(has_selection and len(self._entries) > 1)

    def retranslate(self) -> None:
        self.title.setText(translate("sequence.library.title"))
        self.hint.setText(translate("sequence.library.hint"))
        for button, key in (
            (self.new_button, "sequence.action.new"),
            (self.open_button, "sequence.action.open"),
            (self.insert_button, "sequence.action.insert"),
            (self.rename_button, "sequence.action.rename"),
            (self.duplicate_button, "sequence.action.duplicate"),
            (self.delete_button, "sequence.action.delete"),
        ):
            button.setToolTip(translate(key))
        self.set_sequences(self._entries)


__all__ = ["SEQUENCE_MIME", "SequenceEntry", "SequenceLibraryView"]
