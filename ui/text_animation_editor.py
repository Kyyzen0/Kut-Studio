"""Section « Animation du texte » de l'éditeur de calques : apparition, mots en couleur, temps des mots, presets.

Tout passe par les champs du calque (``word_reveal``, ``highlight_color``, ``highlight_words``, ``word_times``) ou par
un preset (:mod:`core.text_animations`) : la section n'émet que des demandes, la fenêtre les applique avec historique.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QWidget

from core.graphics import WORD_REVEALS, GraphicOverlay
from core.text_animations import TEXT_ANIMATIONS
from core.text_runs import word_count
from ui import i18n
from ui.design_system import Spacing
from ui.theme import label_style


def parse_word_numbers(text: str) -> tuple[int, ...]:
    """« 2, 4 5 » (numéros à partir de 1) → indices ``(1, 3, 4)`` ; ce qui n'est pas un nombre est ignoré."""
    numbers = []
    for item in text.replace(";", ",").replace(" ", ",").split(","):
        item = item.strip()
        if item.isdigit() and int(item) >= 1:
            numbers.append(int(item) - 1)
    return tuple(sorted(set(numbers)))


def word_numbers_text(indices: tuple[int, ...]) -> str:
    return ", ".join(str(index + 1) for index in indices)


class TextAnimationSection(QWidget):
    """Contenu de la section (le cadre repliable est fourni par l'éditeur de calques)."""

    field_changed = Signal(str, object)
    animation_requested = Signal(str)
    voice_sync_requested = Signal()

    def __init__(self, form, parent=None) -> None:
        super().__init__(parent)
        self._updating = False
        self.mode_combo = QComboBox(objectName="text_reveal_mode")
        for mode in WORD_REVEALS:
            self.mode_combo.addItem(i18n.translate(f"text_animation.mode.{mode}"), mode)
        self.mode_combo.currentIndexChanged.connect(lambda: self._emit("word_reveal", self.mode_combo.currentData()))
        form.addRow(i18n.translate("text_animation.mode"), self.mode_combo)
        self.words_edit = QLineEdit(objectName="text_highlight_words")
        self.words_edit.setPlaceholderText(i18n.translate("text_animation.words_placeholder"))
        self.words_edit.setToolTip(i18n.translate("text_animation.words_tooltip"))
        self.words_edit.editingFinished.connect(
            lambda: self._emit("highlight_words", parse_word_numbers(self.words_edit.text()))
        )
        form.addRow(i18n.translate("text_animation.words"), self.words_edit)
        self.timing_label = QLabel()
        self.timing_label.setWordWrap(True)
        self.timing_label.setStyleSheet(label_style(11, "muted", 500))
        form.addRow(i18n.translate("text_animation.timing"), self.timing_label)
        buttons = QWidget()
        row = QHBoxLayout(buttons)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(Spacing.xs)
        self.sync_button = QPushButton(i18n.translate("text_animation.sync_voice"))
        self.sync_button.setToolTip(i18n.translate("text_animation.sync_voice_tooltip"))
        self.sync_button.clicked.connect(self.voice_sync_requested.emit)
        self.clear_button = QPushButton(i18n.translate("text_animation.clear_times"))
        self.clear_button.clicked.connect(lambda: self._emit("word_times", ()))
        row.addWidget(self.sync_button)
        row.addWidget(self.clear_button)
        form.addRow(buttons)
        self.preset_combo = QComboBox(objectName="text_animation_preset")
        self.preset_combo.addItem(i18n.translate("text_animation.choose"), "")
        for preset in TEXT_ANIMATIONS:
            self.preset_combo.addItem(i18n.translate(f"text_animation.preset.{preset}"), preset)
        self.preset_combo.activated.connect(self._on_preset)
        form.addRow(i18n.translate("text_animation.presets"), self.preset_combo)

    def _emit(self, name: str, value) -> None:
        if not self._updating:
            self.field_changed.emit(name, value)

    def _on_preset(self, _index: int) -> None:
        preset = self.preset_combo.currentData()
        self.preset_combo.setCurrentIndex(0)
        if preset:
            self.animation_requested.emit(str(preset))

    def set_graphic(self, graphic: GraphicOverlay) -> None:
        self._updating = True
        try:
            self.mode_combo.setCurrentIndex(max(0, self.mode_combo.findData(graphic.word_reveal)))
            if not self.words_edit.hasFocus():
                self.words_edit.setText(word_numbers_text(graphic.highlight_words))
            timed = len(graphic.word_times)
            self.timing_label.setText(
                i18n.translate("text_animation.timed", timed=timed, words=word_count(graphic.text)) if timed
                else i18n.translate("text_animation.untimed")
            )
            self.clear_button.setEnabled(bool(timed))
        finally:
            self._updating = False


__all__ = ["TextAnimationSection", "parse_word_numbers", "word_numbers_text"]
