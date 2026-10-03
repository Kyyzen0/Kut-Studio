"""Éditeur de style de texte pour le panneau inspecteur (tâche 24).

Le :class:`TextStyleEditor` est un widget composite qui couvre tous les
réglages visuels d'un :class:`~core.text_style.TextStyle` :

- contenu (texte affiché) ;
- police et opacité ;
- couleur du texte, contour, ombre ;
- alignement 3×3 ;
- position fine (offset horizontal / vertical) ;
- fond optionnel et marges ;
- bouton de réinitialisation.

Il publie les changements à travers les signaux :pyattr:`content_changed`
et :pyattr:`style_changed`. Le modèle parent distingue les deux car la
frappe du contenu et le réglage d'un style suivent des historiques
Undo/Redo séparés.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ui.adaptive_layout import allow_shrinking, make_shrinkable
from ui.i18n import translate
from core.text_style import (
    DEFAULT_TEXT_STYLE,
    TextAlignment,
    TextStyle,
    alignment_rows,
    default_text_style,
)


# ---------------------------------------------------------------------------
# Tuile d'alignement
# ---------------------------------------------------------------------------


class AlignmentTile(QToolButton):
    """Bouton radio d'une cellule de la grille d'alignement 3×3."""

    def __init__(
        self,
        alignment: TextAlignment,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.alignment = alignment
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setFixedSize(24, 24)
        self.setToolTip(alignment.value.replace("_", " ").capitalize())
        self.setAccessibleName(translate(f"a11y.align.{alignment.value}"))
        self._apply_style(active=False)

    def set_active(self, active: bool) -> None:
        self._apply_style(active=active)

    def _apply_style(self, *, active: bool) -> None:
        if active:
            self.setStyleSheet(
                "QToolButton { background: #36E6C3; border: 1px solid #1FAE93;"
                " border-radius: 4px; }"
            )
        else:
            self.setStyleSheet(
                "QToolButton { background: #2A2A2A; border: 1px solid #3F3F3F;"
                " border-radius: 4px; }"
                "QToolButton:hover { background: #3A3A3A; }"
            )


# ---------------------------------------------------------------------------
# Bouton couleur
# ---------------------------------------------------------------------------


def _color_chip_icon(hex_color: str, size: int = 14) -> QIcon:
    """Génère une icône ronde de la couleur ``hex_color``."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QColor(hex_color))
    painter.setPen(QColor("#3F3F3F"))
    painter.drawEllipse(1, 1, size - 2, size - 2)
    painter.end()
    return QIcon(pixmap)


# ---------------------------------------------------------------------------
# Widget principal
# ---------------------------------------------------------------------------


class TextStyleEditor(QWidget):
    """Éditeur complet d'un :class:`TextStyle` (tâche 24)."""

    content_changed = Signal(str)
    style_changed = Signal(object)  # TextStyle
    reset_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._style = default_text_style()
        self._suppress_signals = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Spacing.sm)

        # ----- Contenu ------------------------------------------------
        content_label = QLabel(translate("common.text"))
        content_label.setStyleSheet(_label_style(11, "muted", 600))
        layout.addWidget(content_label)
        self.content_editor = QTextEdit()
        self.content_editor.setPlaceholderText(translate("mograph.textstyle.placeholder"))
        self.content_editor.setFixedHeight(60)
        self.content_editor.setStyleSheet(
            "QTextEdit { background: #1F1F1F; color: #FAFAFA;"
            " border: 1px solid #3F3F3F; border-radius: 6px; padding: 6px; }"
        )
        self.content_editor.textChanged.connect(self._on_content_changed)
        layout.addWidget(self.content_editor)

        # ----- Typographie -------------------------------------------
        typo_label = QLabel(translate("mograph.textstyle.typography"))
        typo_label.setStyleSheet(_label_style(11, "muted", 600))
        layout.addWidget(typo_label)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(6)
        form.setLabelAlignment(Qt.AlignLeft)
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)

        self.font_family_combo = make_shrinkable(QComboBox(), 8)  # le plus long nom de police ne fixe plus la largeur
        self._populate_fonts()
        self.font_family_combo.currentTextChanged.connect(
            self._emit_style_changed
        )
        form.addRow(_row_label(translate("mograph.graphics.font")), self.font_family_combo)

        self.font_size_spin = _build_spin(
            6, 240, 1, default=32, decimals=0
        )
        self.font_size_spin.valueChanged.connect(self._emit_style_changed)
        form.addRow(_row_label(translate("common.size")), self.font_size_spin)

        self.opacity_spin = _build_spin(
            0, 1, 0.05, default=1.0, suffix="", decimals=2
        )
        self.opacity_spin.valueChanged.connect(self._emit_style_changed)
        form.addRow(_row_label(translate("field.opacity")), self.opacity_spin)

        layout.addLayout(form)

        # ----- Couleurs ----------------------------------------------
        colors_label = QLabel(translate("mograph.textstyle.colors"))
        colors_label.setStyleSheet(_label_style(11, "muted", 600))
        layout.addWidget(colors_label)

        self.text_color_btn = _ColorButton("#ffffff")
        self.text_color_btn.clicked.connect(
            lambda: self._pick_color("color", self.text_color_btn)
        )
        self.outline_color_btn = _ColorButton("#000000")
        self.outline_color_btn.clicked.connect(
            lambda: self._pick_color("outline_color", self.outline_color_btn)
        )
        self.shadow_color_btn = _ColorButton("#000000")
        self.shadow_color_btn.clicked.connect(
            lambda: self._pick_color("shadow_color", self.shadow_color_btn)
        )
        self.background_color_btn = _ColorButton("", allow_none=True)
        self.background_color_btn.clicked.connect(
            lambda: self._pick_color("background_color", self.background_color_btn)
        )

        colors_grid = QGridLayout()
        colors_grid.setContentsMargins(0, 0, 0, 0)
        colors_grid.setSpacing(4)
        colors_grid.addWidget(_row_label(translate("common.text")), 0, 0)
        colors_grid.addWidget(self.text_color_btn, 0, 1)
        colors_grid.addWidget(_row_label(translate("mograph.graphics.stroke")), 1, 0)
        colors_grid.addWidget(self.outline_color_btn, 1, 1)
        colors_grid.addWidget(_row_label(translate("mograph.graphics.shadow")), 2, 0)
        colors_grid.addWidget(self.shadow_color_btn, 2, 1)
        colors_grid.addWidget(_row_label(translate("field.background")), 3, 0)
        colors_grid.addWidget(self.background_color_btn, 3, 1)
        layout.addLayout(colors_grid)

        # Contour / ombre / fond opacité
        effect_form = QFormLayout()
        effect_form.setContentsMargins(0, 0, 0, 0)
        effect_form.setSpacing(6)
        effect_form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.outline_width_spin = _build_spin(
            0, 20, 0.5, default=2.0, suffix=" px", decimals=1
        )
        self.outline_width_spin.valueChanged.connect(self._emit_style_changed)
        effect_form.addRow(
            _row_label(translate("mograph.textstyle.stroke_width")), self.outline_width_spin
        )
        self.shadow_offset_spin = _build_spin(
            0, 20, 0.5, default=1.5, suffix=" px", decimals=1
        )
        self.shadow_offset_spin.valueChanged.connect(self._emit_style_changed)
        effect_form.addRow(
            _row_label(translate("mograph.textstyle.shadow_offset")), self.shadow_offset_spin
        )
        self.background_opacity_spin = _build_spin(
            0, 1, 0.05, default=0.0, suffix="", decimals=2
        )
        self.background_opacity_spin.valueChanged.connect(
            self._emit_style_changed
        )
        effect_form.addRow(
            _row_label(translate("mograph.textstyle.background_opacity")), self.background_opacity_spin
        )
        layout.addLayout(effect_form)

        # ----- Alignement 3×3 ---------------------------------------
        align_label = QLabel(translate("mograph.graphics.alignment"))
        align_label.setStyleSheet(_label_style(11, "muted", 600))
        layout.addWidget(align_label)
        self._tiles: dict[TextAlignment, AlignmentTile] = {}
        align_grid = QGridLayout()
        align_grid.setContentsMargins(0, 0, 0, 0)
        align_grid.setSpacing(4)
        for row_index, row in enumerate(alignment_rows()):
            for col_index, alignment in enumerate(row):
                tile = AlignmentTile(alignment)
                tile.clicked.connect(
                    lambda _checked=False, a=alignment: self._select_alignment(a)
                )
                self._tiles[alignment] = tile
                align_grid.addWidget(tile, row_index, col_index)
        layout.addLayout(align_grid)

        # ----- Position fine + marges --------------------------------
        pos_form = QFormLayout()
        pos_form.setContentsMargins(0, 0, 0, 0)
        pos_form.setSpacing(6)
        pos_form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.position_x_spin = _build_spin(
            -100, 100, 1, default=0.0, suffix=" %", decimals=1
        )
        self.position_x_spin.valueChanged.connect(self._emit_style_changed)
        pos_form.addRow(_row_label(translate("mograph.textstyle.offset_x")), self.position_x_spin)
        self.position_y_spin = _build_spin(
            -100, 100, 1, default=0.0, suffix=" %", decimals=1
        )
        self.position_y_spin.valueChanged.connect(self._emit_style_changed)
        pos_form.addRow(_row_label(translate("mograph.textstyle.offset_y")), self.position_y_spin)
        self.margin_x_spin = _build_spin(
            0, 200, 4, default=32.0, suffix=" px", decimals=0
        )
        self.margin_x_spin.valueChanged.connect(self._emit_style_changed)
        pos_form.addRow(_row_label(translate("mograph.textstyle.margin_side")), self.margin_x_spin)
        self.margin_y_spin = _build_spin(
            0, 200, 4, default=48.0, suffix=" px", decimals=0
        )
        self.margin_y_spin.valueChanged.connect(self._emit_style_changed)
        pos_form.addRow(_row_label(translate("mograph.textstyle.margin_vertical")), self.margin_y_spin)
        layout.addLayout(pos_form)

        # Padding fond
        padding_form = QFormLayout()
        padding_form.setContentsMargins(0, 0, 0, 0)
        padding_form.setSpacing(6)
        padding_form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.padding_x_spin = _build_spin(
            0, 80, 2, default=12.0, suffix=" px", decimals=0
        )
        self.padding_x_spin.valueChanged.connect(self._emit_style_changed)
        padding_form.addRow(_row_label(translate("mograph.textstyle.padding_horizontal")), self.padding_x_spin)
        self.padding_y_spin = _build_spin(
            0, 80, 2, default=6.0, suffix=" px", decimals=0
        )
        self.padding_y_spin.valueChanged.connect(self._emit_style_changed)
        padding_form.addRow(_row_label(translate("mograph.textstyle.padding_vertical")), self.padding_y_spin)
        layout.addLayout(padding_form)

        # ----- Bouton réinitialisation ---------------------------------
        self.reset_button = QPushButton(translate("mograph.textstyle.reset"))
        self.reset_button.setStyleSheet(
            "QPushButton { padding: 6px 10px; border-radius: 4px;"
            " background: #2A2A2A; color: #FAFAFA;"
            " border: 1px solid #3F3F3F; }"
            "QPushButton:hover { background: #3A3A3A; }"
        )
        self.reset_button.clicked.connect(self._on_reset_clicked)
        allow_shrinking(self.reset_button, 120)  # dernier recours : libellé coupé plutôt que contenu hors de l'inspecteur
        layout.addWidget(self.reset_button)

        layout.addStretch(1)

    # ----- API publique -------------------------------------------------

    def set_state(self, content: str, style: TextStyle | None) -> None:
        """Synchronise l'éditeur avec le contenu et le style courants."""
        target = style or default_text_style()
        self._suppress_signals = True
        try:
            if self.content_editor.toPlainText() != content:
                self.content_editor.setPlainText(content)
            self._apply_style_to_widgets(target)
        finally:
            self._suppress_signals = False
        self._style = target

    def style(self) -> TextStyle:
        return self._style

    def content(self) -> str:
        return self.content_editor.toPlainText()

    # ----- Façades de compatibilité (l'éditeur était un QTextEdit) -----

    def toPlainText(self) -> str:  # noqa: N802 - API Qt historique
        """Facade de compatibilité : l'éditeur était un ``QTextEdit``."""
        return self.content_editor.toPlainText()

    def setPlainText(self, text: str) -> None:  # noqa: N802 - API Qt historique
        """Facade de compatibilité : l'éditeur était un ``QTextEdit``."""
        self.content_editor.setPlainText(text)

    def textChanged(self, *args):  # noqa: N802 - API Qt historique
        """Signal historique ``textChanged`` (émis à chaque frappe)."""
        return self.content_editor.textChanged

    def clear(self) -> None:
        self.content_editor.clear()

    # ----- Internes ------------------------------------------------------

    def _populate_fonts(self) -> None:
        for family in _system_font_families():
            self.font_family_combo.addItem(family)
        self.font_family_combo.setCurrentText("Arial")

    def _apply_style_to_widgets(self, style: TextStyle) -> None:
        self.font_family_combo.setCurrentText(style.font_family)
        self.font_size_spin.setValue(style.font_size)
        self.opacity_spin.setValue(style.opacity)
        self.text_color_btn.set_color(style.color)
        self.outline_color_btn.set_color(style.outline_color)
        self.outline_width_spin.setValue(style.outline_width)
        self.shadow_color_btn.set_color(style.shadow_color)
        self.shadow_offset_spin.setValue(style.shadow_offset)
        self.background_color_btn.set_color(style.background_color or "")
        self.background_opacity_spin.setValue(style.background_opacity)
        self.padding_x_spin.setValue(style.padding_x)
        self.padding_y_spin.setValue(style.padding_y)
        self.position_x_spin.setValue(style.position_x)
        self.position_y_spin.setValue(style.position_y)
        self.margin_x_spin.setValue(style.margin_x)
        self.margin_y_spin.setValue(style.margin_y)
        for alignment, tile in self._tiles.items():
            tile.set_active(alignment is style.alignment)

    def _collect_style(self) -> TextStyle:
        return TextStyle(
            font_family=self.font_family_combo.currentText() or "Arial",
            font_size=float(self.font_size_spin.value()),
            color=self.text_color_btn.color or "#ffffff",
            opacity=float(self.opacity_spin.value()),
            outline_color=self.outline_color_btn.color or "#000000",
            outline_width=float(self.outline_width_spin.value()),
            shadow_color=self.shadow_color_btn.color or "#000000",
            shadow_offset=float(self.shadow_offset_spin.value()),
            background_color=(
                self.background_color_btn.color
                if self.background_color_btn.color
                else None
            ),
            background_opacity=float(self.background_opacity_spin.value()),
            padding_x=float(self.padding_x_spin.value()),
            padding_y=float(self.padding_y_spin.value()),
            alignment=self._active_alignment(),
            position_x=float(self.position_x_spin.value()),
            position_y=float(self.position_y_spin.value()),
            margin_x=float(self.margin_x_spin.value()),
            margin_y=float(self.margin_y_spin.value()),
        )

    def _active_alignment(self) -> TextAlignment:
        for alignment, tile in self._tiles.items():
            if tile.isChecked():
                return alignment
        return self._style.alignment

    def _select_alignment(self, alignment: TextAlignment) -> None:
        for current, tile in self._tiles.items():
            tile.set_active(current is alignment)
            tile.setChecked(current is alignment)
        self._emit_style_changed()

    def _pick_color(self, field: str, button: "_ColorButton") -> None:
        initial = button.color or "#ffffff"
        chosen = QColorDialog.getColor(
            QColor(initial),
            self,
            translate("mograph.textstyle.pick_color", name=field.replace('_', ' ')),
            QColorDialog.ShowAlphaChannel,
        )
        if not chosen.isValid():
            return
        hex_color = chosen.name().lower()
        button.set_color(hex_color)
        self._emit_style_changed()

    def _on_content_changed(self) -> None:
        if self._suppress_signals:
            return
        self.content_changed.emit(self.content_editor.toPlainText())

    def _emit_style_changed(self, *_) -> None:
        if self._suppress_signals:
            return
        self._style = self._collect_style()
        self.style_changed.emit(self._style)

    def _on_reset_clicked(self) -> None:
        self._suppress_signals = True
        try:
            self._apply_style_to_widgets(DEFAULT_TEXT_STYLE)
        finally:
            self._suppress_signals = False
        self._style = DEFAULT_TEXT_STYLE
        self.reset_requested.emit()
        self.style_changed.emit(self._style)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _ColorButton(QPushButton):
    """Bouton « couleur » avec pastille et libellé hex."""

    def __init__(
        self,
        hex_color: str,
        *,
        allow_none: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._allow_none = allow_none
        self.color = hex_color
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(22)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMinimumWidth(60)  # le code hexadécimal est coupé plutôt que de pousser la grille hors de l'inspecteur
        self._refresh_label()

    def set_color(self, hex_color: str) -> None:
        self.color = hex_color
        self._refresh_label()

    def _refresh_label(self) -> None:
        if self._allow_none and not self.color:
            self.setText(translate("common.none"))
            self.setIcon(QIcon())
        else:
            self.setText(self.color.upper() if self.color else "—")
            self.setIcon(_color_chip_icon(self.color or "#ffffff"))
        self.setStyleSheet(
            "QPushButton { text-align: left; padding: 4px 8px;"
            " background: #1F1F1F; color: #FAFAFA;"
            " border: 1px solid #3F3F3F; border-radius: 4px; }"
            "QPushButton:hover { background: #2A2A2A; }"
        )


def _build_spin(
    minimum: float,
    maximum: float,
    step: float,
    *,
    default: float,
    suffix: str = "",
    decimals: int = 1,
) -> QDoubleSpinBox:
    """Construit un spinbox préconfiguré."""
    spin = QDoubleSpinBox()
    spin.setRange(float(minimum), float(maximum))
    spin.setSingleStep(float(step))
    spin.setValue(float(default))
    spin.setDecimals(int(decimals))
    if suffix:
        spin.setSuffix(suffix)
    spin.setFixedHeight(22)
    return spin


def _label_style(size: int, role: str = "muted", weight: int = 500) -> str:
    return f"color: #B0B0B0; font-size: {size}px; font-weight: {weight};"


def _row_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet(_label_style(11, "muted", 600))
    label.setMinimumWidth(110)
    return label


def _system_font_families() -> list[str]:
    """Liste de polices système communes (ordre déterministe)."""
    return [
        "Arial",
        "Helvetica",
        "Georgia",
        "Times New Roman",
        "Courier New",
        "Verdana",
        "Trebuchet MS",
        "Impact",
        "Comic Sans MS",
        "Palatino",
        "Garamond",
    ]


# Imports différés : évite les cycles et garde un typage strict.
from ui.design_system import Spacing  # noqa: E402  (import local)
from ui.i18n import translate