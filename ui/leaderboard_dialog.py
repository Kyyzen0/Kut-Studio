"""Éditeur de classement : un tableau rang / nom / valeur / couleur, et la durée à l'écran.

Le tableau édité remplace les calques du classement (:func:`core.leaderboard.update_leaderboard`) ; les couleurs
proposées sont celles de la palette « Night ».
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.leaderboard import MAX_ROWS, LeaderboardRow
from core.project_templates import NIGHT_PALETTE
from ui.design_system import DIALOG_MARGINS, Spacing
from ui.i18n import translate
from ui.keyboard_navigation import set_single_default
from ui.theme import label_style

ROW_COLORS = ("blue", "white", "red", "steel", "blue_deep")
"""Couleurs d'accent proposées (clés de :data:`core.project_templates.NIGHT_PALETTE`)."""
_COLUMNS = ("rank", "name", "value", "color")


class LeaderboardDialog(QDialog):
    """« Classement… » : les lignes du tableau et sa durée."""

    def __init__(self, rows: tuple[LeaderboardRow, ...], duration: float, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(translate("leaderboard.dialog.title"))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(*DIALOG_MARGINS)
        layout.setSpacing(Spacing.md)
        intro = QLabel(translate("leaderboard.dialog.intro"))
        intro.setWordWrap(True)
        intro.setStyleSheet(label_style(12, "muted", 500))
        layout.addWidget(intro)
        self.table = QTableWidget(0, len(_COLUMNS), objectName="leaderboardTable")
        self.table.setHorizontalHeaderLabels([translate(f"leaderboard.column.{key}") for key in _COLUMNS])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        for row in rows:
            self._append(row)
        layout.addWidget(self.table, 1)
        buttons_row = QHBoxLayout()
        self.add_button = QPushButton(translate("leaderboard.dialog.add_row"))
        self.add_button.clicked.connect(self._add_row)
        self.remove_button = QPushButton(translate("leaderboard.dialog.remove_row"))
        self.remove_button.clicked.connect(self._remove_row)
        buttons_row.addWidget(self.add_button)
        buttons_row.addWidget(self.remove_button)
        buttons_row.addStretch(1)
        layout.addLayout(buttons_row)
        form = QFormLayout()
        self.duration_spin = QDoubleSpinBox()
        self.duration_spin.setRange(1.0, 60.0)
        self.duration_spin.setSingleStep(0.5)
        self.duration_spin.setDecimals(1)
        self.duration_spin.setSuffix(translate("leaderboard.dialog.seconds_suffix"))
        self.duration_spin.setValue(float(duration))
        form.addRow(translate("leaderboard.dialog.duration"), self.duration_spin)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText(translate("leaderboard.dialog.apply"))
        buttons.button(QDialogButtonBox.Cancel).setText(translate("social.dialog.cancel"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        set_single_default(self, buttons.button(QDialogButtonBox.Ok))
        self._sync_buttons()

    def _append(self, row: LeaderboardRow) -> None:
        index = self.table.rowCount()
        self.table.insertRow(index)
        for column, value in enumerate((row.rank, row.name, row.value)):
            self.table.setItem(index, column, QTableWidgetItem(value))
        combo = QComboBox()
        for key in ROW_COLORS:
            combo.addItem(translate(f"leaderboard.color.{key}"), NIGHT_PALETTE[key])
        combo.setCurrentIndex(max(0, combo.findData(row.color.upper())))
        self.table.setCellWidget(index, 3, combo)

    def _add_row(self) -> None:
        rank = str(self.table.rowCount() + 1)
        self._append(LeaderboardRow(rank, translate("template.text.row_name") + f" {rank}", "0",
                                    NIGHT_PALETTE[ROW_COLORS[min(int(rank) - 1, len(ROW_COLORS) - 1)]]))
        self._sync_buttons()

    def _remove_row(self) -> None:
        if self.table.rowCount() > 1:
            self.table.removeRow(self.table.currentRow() if self.table.currentRow() >= 0 else self.table.rowCount() - 1)
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        self.add_button.setEnabled(self.table.rowCount() < MAX_ROWS)
        self.remove_button.setEnabled(self.table.rowCount() > 1)

    def rows(self) -> tuple[LeaderboardRow, ...]:
        result = []
        for index in range(self.table.rowCount()):
            cells = [(self.table.item(index, column).text() if self.table.item(index, column) else "").strip()
                     for column in range(3)]
            combo = self.table.cellWidget(index, 3)
            result.append(LeaderboardRow(*cells, str(combo.currentData()) if combo is not None else NIGHT_PALETTE["blue"]))
        return tuple(row for row in result if row.rank or row.name or row.value)

    def duration(self) -> float:
        return float(self.duration_spin.value())


__all__ = ["LeaderboardDialog", "ROW_COLORS"]
