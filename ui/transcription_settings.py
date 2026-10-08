"""Groupe « Transcription (whisper.cpp) » des préférences : le programme et le modèle, découverts ou choisis.

Un champ vide veut dire « découverte automatique » (:func:`core.transcription.find_whisper`,
:func:`core.transcription.find_whisper_model`) ; la note sous les champs dit ce qui sera réellement utilisé, ou comment
installer ce qui manque. Chaque changement est émis aussitôt, comme les autres préférences.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFileDialog, QGridLayout, QGroupBox, QLabel, QLineEdit, QPushButton

from core.transcription import find_whisper, find_whisper_model, model_directory
from ui.i18n import translate
from ui.design_system import TextRoles
from ui.theme import set_role


class TranscriptionSettingsGroup(QGroupBox):
    """Programme ``whisper-cli`` et modèle ``ggml-*.bin`` de la transcription locale."""

    whisper_path_changed = Signal(str)
    whisper_model_changed = Signal(str)

    def __init__(self, whisper_path: str = "", whisper_model: str = "", parent=None) -> None:
        super().__init__(parent)
        self._values = {"tool": str(whisper_path or ""), "model": str(whisper_model or "")}
        grid = QGridLayout(self)
        grid.setContentsMargins(14, 12, 14, 12)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        self.labels: dict[str, QLabel] = {}
        self.fields: dict[str, QLineEdit] = {}
        self.choose_buttons: dict[str, QPushButton] = {}
        self.auto_buttons: dict[str, QPushButton] = {}
        for row, name in enumerate(("tool", "model")):
            label = QLabel()
            field = QLineEdit(self._values[name])
            field.editingFinished.connect(lambda n=name: self._set(n, self.fields[n].text()))
            choose = QPushButton()
            choose.clicked.connect(lambda _checked=False, n=name: self._choose(n))
            auto = QPushButton()
            auto.clicked.connect(lambda _checked=False, n=name: self._set(n, ""))
            for column, widget in enumerate((label, field, choose, auto)):
                grid.addWidget(widget, row, column)
            self.labels[name], self.fields[name] = label, field
            self.choose_buttons[name], self.auto_buttons[name] = choose, auto
        grid.setColumnStretch(1, 1)
        self.status = QLabel()
        self.status.setWordWrap(True)
        set_role(self.status, TextRoles.label_secondary)
        grid.addWidget(self.status, 2, 0, 1, 4)
        self.retranslate()

    def _choose(self, name: str) -> None:
        title = translate(f"transcription.prefs.choose_{name}")
        pattern = translate("transcription.prefs.model_filter") if name == "model" else ""
        path, _filter = QFileDialog.getOpenFileName(self, title, self._values[name], pattern)
        if path:
            self._set(name, path)

    def _set(self, name: str, value: str) -> None:
        value = value.strip()
        self.fields[name].setText(value)
        if value == self._values[name]:
            return
        self._values[name] = value
        (self.whisper_path_changed if name == "tool" else self.whisper_model_changed).emit(value)
        self._refresh_status()

    def _refresh_status(self) -> None:
        tool = find_whisper(self._values["tool"])
        model = find_whisper_model(self._values["model"])
        lines = [
            translate("transcription.prefs.tool_found", path=tool) if tool
            else translate("transcription.prefs.tool_missing"),
            translate("transcription.prefs.model_found", path=model) if model
            else translate("transcription.prefs.model_missing", folder=str(model_directory())),
        ]
        self.status.setText("\n".join(lines))

    def retranslate(self) -> None:
        self.setTitle(translate("transcription.prefs.title"))
        for name in ("tool", "model"):
            self.labels[name].setText(translate(f"transcription.prefs.{name}"))
            self.fields[name].setPlaceholderText(translate("transcription.prefs.automatic"))
            self.choose_buttons[name].setText(translate("transcription.prefs.choose"))
            self.auto_buttons[name].setText(translate("transcription.prefs.auto"))
        self._refresh_status()


__all__ = ["TranscriptionSettingsGroup"]
