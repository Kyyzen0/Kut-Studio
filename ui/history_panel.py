"""Panneau Historique : les étapes nommées du montage, et un clic pour revenir à l'une d'elles.

La liste vient de :meth:`core.edit_history.ProjectHistory.entries` (lue par ``entries_provider``) : de l'état initial
(en haut) à la dernière étape,
l'état affiché mis en avant, les étapes annulées (encore rétablissables) atténuées, l'état du fichier enregistré
marqué d'un point. Cliquer une étape émet :attr:`HistoryPanel.state_requested` ; la fenêtre principale y saute en une
fois (:meth:`ProjectHistory.go_to`), comme autant d'Annuler ou de Rétablir.

Panneau fermé, rien n'est reconstruit à chaque modification : la liste est relue à l'affichage (:meth:`refresh`).
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QLabel, QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from ui.design_system import Spacing
from ui.i18n import translate
from ui.icons import IconName
from ui.panel_header import PanelHeader
from ui.theme import COLORS, set_role

_INDEX_ROLE = Qt.UserRole + 1


class HistoryPanel(QWidget):
    """Liste des états de l'historique ; :attr:`state_requested` (rang de l'état) au clic."""

    state_requested = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("history_panel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        header = PanelHeader(translate("history.panel.title"), icon=IconName.RESET)
        self.title_label = header.title_label
        layout.addWidget(header)
        self.hint = QLabel(translate("history.panel.hint"))
        self.hint.setWordWrap(True)
        set_role(self.hint, "label-secondary")
        self.hint.setContentsMargins(Spacing.md, Spacing.sm, Spacing.md, Spacing.sm)
        layout.addWidget(self.hint)
        self.list = QListWidget(objectName="history_list")
        self.list.setUniformItemSizes(True)
        self.list.itemClicked.connect(self._on_item_clicked)
        self.list.itemActivated.connect(self._on_item_clicked)
        layout.addWidget(self.list, 1)
        self._entries: list = []
        self.entries_provider: Callable[[], list] | None = None

    def refresh(self) -> None:
        """Relit l'historique (``entries_provider``) si le panneau est affiché et encore vivant."""
        from shiboken6 import isValid

        if self.entries_provider is None or not isValid(self) or not isValid(self.list) or not self.isVisible():
            return
        self.set_entries(self.entries_provider())

    def showEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().showEvent(event)
        self.refresh()

    def set_entries(self, entries) -> None:
        """Affiche ``entries`` (:class:`core.edit_history.HistoryEntry`) ; l'état courant reste visible."""
        self._entries = list(entries)
        self.list.blockSignals(True)
        self.list.clear()
        current_item = None
        for entry in self._entries:
            label = entry.label or translate("history.panel.initial")
            item = QListWidgetItem(f"● {label}" if entry.saved else label)
            item.setData(_INDEX_ROLE, entry.index)
            tooltip = [label]
            if entry.saved:
                tooltip.append(translate("history.panel.saved"))
            if entry.undone:
                tooltip.append(translate("history.panel.undone"))
                font = QFont(item.font())
                font.setItalic(True)
                item.setFont(font)
                item.setForeground(QColor(COLORS["muted"]))     # le jeton du thème, lisible clair comme sombre
            if entry.current:
                font = QFont(item.font())
                font.setBold(True)
                item.setFont(font)
                current_item = item
            item.setToolTip("\n".join(tooltip))
            self.list.addItem(item)
        if current_item is not None:
            self.list.setCurrentItem(current_item)
            self.list.scrollToItem(current_item)
        self.list.blockSignals(False)

    def retranslate(self) -> None:
        self.title_label.setText(translate("history.panel.title"))
        self.hint.setText(translate("history.panel.hint"))
        self.refresh()

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        index = item.data(_INDEX_ROLE)
        if isinstance(index, int) and not any(entry.current and entry.index == index for entry in self._entries):
            self.state_requested.emit(index)
