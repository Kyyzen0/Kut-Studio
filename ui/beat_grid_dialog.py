"""Boîte « Grille rythmique… » : tempo, calage, temps par mesure ; tap tempo et détection depuis la musique."""

from __future__ import annotations

import time
from collections.abc import Callable

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core.beat_grid import MAX_BEATS_PER_BAR, MAX_BPM, MIN_BPM, BeatGrid, tap_tempo
from ui.design_system import DIALOG_MARGINS, Spacing
from ui.i18n import translate
from ui.keyboard_navigation import set_single_default
from ui.theme import label_style

REMOVE = 2
"""Code de retour : l'utilisateur a supprimé la grille (``QDialog.Accepted`` = appliquer)."""


class BeatGridDialog(QDialog):
    """Réglage de la grille rythmique de la séquence ouverte."""

    def __init__(
        self,
        grid: BeatGrid | None,
        *,
        playhead: float = 0.0,
        detect: Callable[[], object] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(translate("beat.dialog.title"))
        self._playhead = float(playhead)
        self._detect = detect
        self._taps: list[float] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(*DIALOG_MARGINS)
        layout.setSpacing(Spacing.md)
        form = QFormLayout()
        form.setSpacing(Spacing.sm)
        self.bpm_spin = QDoubleSpinBox()
        self.bpm_spin.setRange(MIN_BPM, MAX_BPM)
        self.bpm_spin.setDecimals(2)
        self.bpm_spin.setSuffix(translate("beat.dialog.bpm_suffix"))
        self.bpm_spin.setValue(grid.bpm if grid is not None else 120.0)
        tap = QPushButton(translate("beat.dialog.tap"))
        tap.setToolTip(translate("beat.dialog.tap_tooltip"))
        tap.setAutoDefault(False)
        tap.clicked.connect(self._on_tap)
        self.tap_button = tap
        row = QHBoxLayout()
        row.addWidget(self.bpm_spin, 1)
        row.addWidget(tap)
        form.addRow(translate("beat.dialog.bpm"), row)
        self.offset_spin = QDoubleSpinBox()
        self.offset_spin.setRange(-60.0, 36000.0)
        self.offset_spin.setDecimals(3)
        self.offset_spin.setSingleStep(0.01)
        self.offset_spin.setSuffix(translate("beat.dialog.seconds_suffix"))
        self.offset_spin.setValue(grid.offset if grid is not None else 0.0)
        at_playhead = QPushButton(translate("beat.dialog.at_playhead"))
        at_playhead.setAutoDefault(False)
        at_playhead.clicked.connect(lambda: self.offset_spin.setValue(self._playhead))
        offset_row = QHBoxLayout()
        offset_row.addWidget(self.offset_spin, 1)
        offset_row.addWidget(at_playhead)
        form.addRow(translate("beat.dialog.offset"), offset_row)
        self.bar_spin = QSpinBox()
        self.bar_spin.setRange(1, MAX_BEATS_PER_BAR)
        self.bar_spin.setValue(grid.beats_per_bar if grid is not None else 4)
        form.addRow(translate("beat.dialog.beats_per_bar"), self.bar_spin)
        layout.addLayout(form)
        self.detect_button = QPushButton(translate("beat.dialog.detect"))
        self.detect_button.setAutoDefault(False)
        self.detect_button.setEnabled(detect is not None)
        self.detect_button.setToolTip(translate("beat.dialog.detect_tooltip" if detect else "beat.dialog.no_music"))
        self.detect_button.clicked.connect(self._on_detect)
        layout.addWidget(self.detect_button)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setStyleSheet(label_style(11, "muted", 500))
        layout.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText(translate("social.sequence.apply"))
        buttons.button(QDialogButtonBox.Cancel).setText(translate("social.dialog.cancel"))
        self.remove_button = buttons.addButton(translate("beat.dialog.remove"), QDialogButtonBox.DestructiveRole)
        self.remove_button.setEnabled(grid is not None)
        self.remove_button.clicked.connect(lambda: self.done(REMOVE))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        set_single_default(self, buttons.button(QDialogButtonBox.Ok))

    def _on_tap(self) -> None:
        self._taps.append(time.monotonic())
        bpm = tap_tempo(self._taps)
        if bpm is not None:
            self.bpm_spin.setValue(bpm)
            self.status.setText(translate("beat.dialog.tapped", bpm=f"{bpm:g}"))

    def _on_detect(self) -> None:
        if self._detect is None:
            return
        result = self._detect()
        if isinstance(result, BeatGrid):
            self.bpm_spin.setValue(result.bpm)
            self.offset_spin.setValue(result.offset)
            self.status.setText(translate("beat.dialog.detected", bpm=f"{result.bpm:g}"))
        else:
            self.status.setText(str(result or translate("beat.dialog.detect_failed")))

    def grid(self) -> BeatGrid:
        return BeatGrid(self.bpm_spin.value(), self.offset_spin.value(), self.bar_spin.value())


__all__ = ["REMOVE", "BeatGridDialog"]
