"""Éditeur compact des propriétés intrinsèques d'un calque graphique."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QTextEdit,
    QWidget,
)

from core.graphics import GraphicOverlay, GraphicType
from ui.design_system import Spacing
from ui.theme import label_style


class GraphicsEditor(QGroupBox):
    """Formulaire autonome qui n'émet que des changements utilisateur."""

    field_changed = Signal(str, object)

    def __init__(self, group_style: str = "", parent=None) -> None:
        super().__init__("Calque graphique", parent)
        self.setObjectName("graphicsGroup")
        self.setStyleSheet(group_style)
        self._updating = False

        form = QFormLayout(self)
        form.setContentsMargins(Spacing.md, Spacing.md, Spacing.md, Spacing.md)
        form.setSpacing(Spacing.xs)

        self.type_label = QLabel("--")
        self.type_label.setStyleSheet(label_style(12, "text", 700))
        form.addRow("Type", self.type_label)

        self.text_edit = QTextEdit()
        self.text_edit.setFixedHeight(64)
        self.text_edit.setPlaceholderText("Texte du titre")
        self.text_edit.textChanged.connect(
            lambda: self._emit("text", self.text_edit.toPlainText())
        )
        form.addRow("Texte", self.text_edit)

        self.source_label = QLabel("--")
        self.source_label.setWordWrap(True)
        self.source_label.setStyleSheet(label_style(10, "muted", 500))
        form.addRow("Source", self.source_label)

        self.width_spin = self._spin(2, 8192, "width")
        self.height_spin = self._spin(2, 8192, "height")
        form.addRow("Taille", self._pair(self.width_spin, "×", self.height_spin))

        self.color_edits: dict[str, QLineEdit] = {}
        for field_name, label, default in (
            ("fill_color", "Couleur", "#FFFFFF"),
            ("stroke_color", "Contour", "#000000"),
            ("shadow_color", "Ombre", "#000000AA"),
        ):
            edit = QLineEdit(default)
            edit.setMaxLength(9)
            edit.editingFinished.connect(
                lambda name=field_name, widget=edit: self._emit(name, widget.text())
            )
            self.color_edits[field_name] = edit
            form.addRow(label, edit)

        self.stroke_width_spin = self._spin(0, 64, "stroke_width")
        form.addRow("Épaisseur", self.stroke_width_spin)

        self.font_family_edit = QLineEdit("Sans Serif")
        self.font_family_edit.editingFinished.connect(
            lambda: self._emit("font_family", self.font_family_edit.text())
        )
        form.addRow("Police", self.font_family_edit)
        self.font_size_spin = self._spin(6, 512, "font_size")
        form.addRow("Corps", self.font_size_spin)

        self.shadow_x_spin = self._spin(-256, 256, "shadow_offset_x")
        self.shadow_y_spin = self._spin(-256, 256, "shadow_offset_y")
        form.addRow(
            "Décalage ombre",
            self._pair(self.shadow_x_spin, "Y", self.shadow_y_spin, first_label="X"),
        )
        self.setEnabled(False)

    def _spin(self, minimum: int, maximum: int, field_name: str) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.valueChanged.connect(
            lambda value, name=field_name: self._emit(name, int(value))
        )
        return spin

    @staticmethod
    def _pair(first: QWidget, separator: str, second: QWidget, *, first_label="") -> QWidget:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Spacing.xs)
        if first_label:
            layout.addWidget(QLabel(first_label))
        layout.addWidget(first)
        layout.addWidget(QLabel(separator))
        layout.addWidget(second)
        return container

    def _emit(self, field_name: str, value: object) -> None:
        if not self._updating:
            self.field_changed.emit(field_name, value)

    def set_graphic(self, graphic: object) -> None:
        """Charge un calque sans produire de faux événements d'édition."""
        self._updating = True
        try:
            if not isinstance(graphic, GraphicOverlay):
                self.setEnabled(False)
                self.type_label.setText("--")
                self.text_edit.clear()
                self.text_edit.show()
                self.source_label.setText("--")
                return
            names = {
                GraphicType.TEXT: "Titre",
                GraphicType.RECTANGLE: "Rectangle",
                GraphicType.SOLID: "Aplat",
                GraphicType.IMAGE: "Image",
            }
            self.type_label.setText(names[graphic.type])
            self.text_edit.setPlainText(graphic.text)
            self.text_edit.setVisible(graphic.type == GraphicType.TEXT)
            self.source_label.setText(graphic.source_path or "Généré")
            self.width_spin.setValue(graphic.width)
            self.height_spin.setValue(graphic.height)
            for name, edit in self.color_edits.items():
                edit.setText(str(getattr(graphic, name)))
            self.stroke_width_spin.setValue(graphic.stroke_width)
            self.font_family_edit.setText(graphic.font_family)
            self.font_size_spin.setValue(graphic.font_size)
            self.shadow_x_spin.setValue(graphic.shadow_offset_x)
            self.shadow_y_spin.setValue(graphic.shadow_offset_y)
            is_text = graphic.type == GraphicType.TEXT
            self.font_family_edit.setEnabled(is_text)
            self.font_size_spin.setEnabled(is_text)
        finally:
            self._updating = False


__all__ = ["GraphicsEditor"]
