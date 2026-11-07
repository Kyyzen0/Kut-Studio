"""Groupe « Mode nuit » des préférences : bascule, intensité, horaire automatique.

Chaque changement est émis aussitôt (un seul signal portant les cinq valeurs). Le curseur n'émet qu'au **relâchement** (ou à
chaque cran au clavier) : glisser ne repeint pas l'interface à chaque pixel, la palette est appliquée en un seul passage.
"""

from __future__ import annotations

from PySide6.QtCore import QTime, Qt, Signal
from PySide6.QtWidgets import QCheckBox, QGridLayout, QGroupBox, QLabel, QSlider, QTimeEdit

from core.night_mode import DEFAULT_END, DEFAULT_START, INTENSITY_RANGE, NightModeState, parse_time
from ui.design_system import TextRoles
from ui.i18n import translate
from ui.theme import set_role


def _qtime(text: str, fallback: str) -> QTime:
    minutes = parse_time(text)
    if minutes is None:
        minutes = parse_time(fallback) or 0
    return QTime(minutes // 60, minutes % 60)


class NightModeGroup(QGroupBox):
    """Réglages du mode nuit de l'interface (jamais les images du montage)."""

    changed = Signal(bool, int, bool, str, str)         # activé, intensité, automatique, début, fin

    def __init__(self, state: NightModeState, parent=None) -> None:
        super().__init__(parent)
        grid = QGridLayout(self)
        grid.setContentsMargins(14, 12, 14, 12)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        self.enabled_box = QCheckBox()
        self.intensity_label = QLabel()
        self.intensity_slider = QSlider(Qt.Horizontal)
        self.intensity_slider.setRange(*INTENSITY_RANGE)
        self.intensity_slider.setSingleStep(5)
        self.intensity_slider.setPageStep(10)
        self.intensity_value = QLabel()
        self.auto_box = QCheckBox()
        self.from_label = QLabel()
        self.start_edit = QTimeEdit()
        self.to_label = QLabel()
        self.end_edit = QTimeEdit()
        for edit in (self.start_edit, self.end_edit):
            edit.setDisplayFormat("HH:mm")
        self.note = QLabel()
        self.note.setWordWrap(True)
        set_role(self.note, TextRoles.label_secondary)
        grid.addWidget(self.enabled_box, 0, 0, 1, 4)
        grid.addWidget(self.intensity_label, 1, 0)
        grid.addWidget(self.intensity_slider, 1, 1, 1, 2)
        grid.addWidget(self.intensity_value, 1, 3)
        grid.addWidget(self.auto_box, 2, 0, 1, 4)
        grid.addWidget(self.from_label, 3, 0)
        grid.addWidget(self.start_edit, 3, 1)
        grid.addWidget(self.to_label, 3, 2)
        grid.addWidget(self.end_edit, 3, 3)
        grid.addWidget(self.note, 4, 0, 1, 4)
        grid.setColumnStretch(1, 1)
        self.sync(state)
        self.enabled_box.toggled.connect(lambda _on: self._emit())
        self.auto_box.toggled.connect(lambda _on: self._on_auto())
        self.intensity_slider.valueChanged.connect(self._on_slider)
        self.intensity_slider.sliderReleased.connect(self._emit)
        self.start_edit.editingFinished.connect(self._emit)
        self.end_edit.editingFinished.connect(self._emit)
        self.retranslate()

    def values(self) -> tuple[bool, int, bool, str, str]:
        return (
            self.enabled_box.isChecked(), self.intensity_slider.value(), self.auto_box.isChecked(),
            self.start_edit.time().toString("HH:mm"), self.end_edit.time().toString("HH:mm"),
        )

    def sync(self, state: NightModeState) -> None:
        """Affiche ``state`` sans rien émettre."""
        widgets = (self.enabled_box, self.intensity_slider, self.auto_box, self.start_edit, self.end_edit)
        for widget in widgets:
            widget.blockSignals(True)
        self.enabled_box.setChecked(state.enabled)
        self.intensity_slider.setValue(state.intensity)
        self.auto_box.setChecked(state.auto)
        self.start_edit.setTime(_qtime(state.start, DEFAULT_START))
        self.end_edit.setTime(_qtime(state.end, DEFAULT_END))
        for widget in widgets:
            widget.blockSignals(False)
        self._refresh()

    def _refresh(self) -> None:
        self.intensity_value.setText(translate("night.prefs.value", value=self.intensity_slider.value()))
        auto = self.auto_box.isChecked()
        for widget in (self.from_label, self.start_edit, self.to_label, self.end_edit):
            widget.setEnabled(auto)

    def _on_auto(self) -> None:
        self._refresh()
        self._emit()

    def _on_slider(self, _value: int) -> None:
        self._refresh()
        if not self.intensity_slider.isSliderDown():          # clavier, molette ou clic : un cran = un passage
            self._emit()

    def _emit(self) -> None:
        self._refresh()
        self.changed.emit(*self.values())

    def retranslate(self) -> None:
        self.setTitle(translate("night.prefs.title"))
        self.enabled_box.setText(translate("night.prefs.enabled"))
        self.intensity_label.setText(translate("night.prefs.intensity"))
        self.auto_box.setText(translate("night.prefs.auto"))
        self.from_label.setText(translate("night.prefs.from"))
        self.to_label.setText(translate("night.prefs.to"))
        self.note.setText(translate("night.prefs.note"))
        self._refresh()


__all__ = ["NightModeGroup"]
