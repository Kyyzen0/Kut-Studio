"""Éditeur des propriétés d'un calque graphique (inspecteur).

Les réglages courants restent visibles (texte, taille, couleur) ; les
autres sont rangés dans des sections repliables (« Forme », « Texte »,
« Ombre et fond », « Calque ») qui n'apparaissent que pour les types
concernés : un débutant voit peu de choses, un utilisateur avancé ouvre ce
dont il a besoin.

Le formulaire n'émet que des changements **utilisateur**
(``field_changed``) ; le parent passe par ``parent_changed`` car la fenêtre
doit le changer sans faire sauter le calque à l'écran.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFontComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.graphics import GraphicOverlay, GraphicType, ShapeKind
from ui import i18n
from ui.design_system import Spacing
from ui.theme import label_style

TYPE_NAMES = {
    GraphicType.TEXT: "Titre",
    GraphicType.RECTANGLE: "Rectangle",
    GraphicType.SOLID: "Aplat",
    GraphicType.IMAGE: "Image",
    GraphicType.SHAPE: "Forme",
    GraphicType.GROUP: "Groupe",
    GraphicType.ADJUSTMENT: "Calque d'effets",
    GraphicType.NULL: "Contrôleur",
}


class _Section(QWidget):
    """Section repliable (fermée par défaut : interface légère)."""

    def __init__(self, title: str, *, expanded: bool = False) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Spacing.xs)
        self.toggle = QToolButton()
        self.toggle.setText(title)
        self.toggle.setCheckable(True)
        self.toggle.setChecked(expanded)
        self.toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.toggle.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        self.toggle.setStyleSheet("QToolButton { border: none; font-weight: 700; }")
        self.toggle.toggled.connect(self._on_toggled)
        layout.addWidget(self.toggle)
        self.body = QWidget()
        self.form = QFormLayout(self.body)
        self.form.setContentsMargins(Spacing.sm, 0, 0, 0)
        self.form.setSpacing(Spacing.xs)
        self.body.setVisible(expanded)
        layout.addWidget(self.body)

    def _on_toggled(self, checked: bool) -> None:
        self.toggle.setArrowType(Qt.DownArrow if checked else Qt.RightArrow)
        self.body.setVisible(checked)


class _ColorField(QWidget):
    """Pastille + code hexadécimal (``#RRGGBB`` ou ``#RRGGBBAA``)."""

    changed = Signal(str)

    def __init__(self, default: str) -> None:
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Spacing.xs)
        self.swatch = QPushButton()
        self.swatch.setFixedSize(22, 22)
        self.swatch.setToolTip(i18n.translate("graphics.color.pick"))
        self.swatch.setAccessibleName(i18n.translate("graphics.color.pick"))
        self.swatch.clicked.connect(self._pick)
        self.edit = QLineEdit(default)
        self.edit.setMaxLength(9)
        self.edit.editingFinished.connect(lambda: self.changed.emit(self.edit.text()))
        layout.addWidget(self.swatch)
        layout.addWidget(self.edit, 1)
        self.set_value(default)

    def set_value(self, value: str) -> None:
        self.edit.setText(value)
        text = value if len(value) == 7 else value[:7]
        self.swatch.setStyleSheet(f"background: {text}; border: 1px solid #888; border-radius: 4px;")

    def _pick(self) -> None:
        current = self.edit.text()
        alpha = int(current[7:9], 16) if len(current) == 9 else 255
        color = QColorDialog.getColor(
            QColor(current[:7]), self, "Couleur", QColorDialog.ShowAlphaChannel
        )
        if not color.isValid():
            return
        text = color.name().upper()
        alpha = color.alpha() if color.alpha() != 255 or alpha == 255 else alpha
        if alpha != 255:
            text += f"{alpha:02X}"
        self.set_value(text)
        self.changed.emit(text)


class GraphicsEditor(QGroupBox):
    """Formulaire autonome qui n'émet que des changements utilisateur."""

    field_changed = Signal(str, object)
    parent_changed = Signal(str)

    def __init__(self, group_style: str = "", parent=None) -> None:
        super().__init__("Calque graphique", parent)
        self.setObjectName("graphicsGroup")
        self.setStyleSheet(group_style)
        self._updating = False
        self._graphic: GraphicOverlay | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(Spacing.md, Spacing.md, Spacing.md, Spacing.md)
        root.setSpacing(Spacing.xs)
        form = QFormLayout()
        form.setSpacing(Spacing.xs)
        root.addLayout(form)

        self.type_label = QLabel("--")
        self.type_label.setStyleSheet(label_style(12, "text", 700))
        form.addRow("Type", self.type_label)

        self.text_edit = QTextEdit()
        self.text_edit.setFixedHeight(64)
        self.text_edit.setPlaceholderText("Texte du titre")
        self.text_edit.textChanged.connect(lambda: self._emit("text", self.text_edit.toPlainText()))
        self.text_label = QLabel("Texte")
        form.addRow(self.text_label, self.text_edit)

        self.source_label = QLabel("--")
        self.source_label.setWordWrap(True)
        self.source_label.setStyleSheet(label_style(10, "muted", 500))
        self.source_row_label = QLabel("Source")
        form.addRow(self.source_row_label, self.source_label)

        self.width_spin = self._spin(2, 8192, "width")
        self.height_spin = self._spin(2, 8192, "height")
        self.size_label = QLabel("Taille")
        form.addRow(self.size_label, self._pair(self.width_spin, "×", self.height_spin))

        self.fill_field = _ColorField("#FFFFFF")
        self.fill_field.changed.connect(lambda value: self._emit("fill_color", value))
        self.fill_label = QLabel("Couleur")
        form.addRow(self.fill_label, self.fill_field)

        # --- Forme ---------------------------------------------------------------------------
        self.shape_section = _Section("Forme", expanded=True)
        self.shape_combo = QComboBox(objectName="shape_kind")
        for kind, label in (
            (ShapeKind.RECTANGLE, "Rectangle"), (ShapeKind.ROUNDED_RECTANGLE, "Rectangle arrondi"),
            (ShapeKind.ELLIPSE, "Ellipse / cercle"), (ShapeKind.LINE, "Ligne"), (ShapeKind.POLYGON, "Polygone"),
        ):
            self.shape_combo.addItem(label, kind.value)
        self.shape_combo.currentIndexChanged.connect(
            lambda: self._emit("shape", self.shape_combo.currentData())
        )
        self.shape_section.form.addRow("Forme", self.shape_combo)
        self.corner_spin = self._double(0, 4096, 1, "corner_radius")
        self.shape_section.form.addRow("Rayon d'angle", self.corner_spin)
        self.sides_spin = self._spin(3, 64, "polygon_sides")
        self.shape_section.form.addRow("Côtés", self.sides_spin)
        self.fill_check = QCheckBox("Remplissage")
        self.fill_check.toggled.connect(lambda checked: self._emit("fill_enabled", checked))
        self.shape_section.form.addRow(self.fill_check)
        root.addWidget(self.shape_section)

        # --- Contour (formes et textes) ----------------------------------------------------
        self.stroke_section = _Section("Contour")
        self.stroke_field = _ColorField("#000000")
        self.stroke_field.changed.connect(lambda value: self._emit("stroke_color", value))
        self.stroke_section.form.addRow("Couleur", self.stroke_field)
        self.stroke_width_spin = self._spin(0, 256, "stroke_width")
        self.stroke_section.form.addRow("Épaisseur", self.stroke_width_spin)
        root.addWidget(self.stroke_section)

        # --- Texte ---------------------------------------------------------------------------
        self.text_section = _Section("Texte", expanded=True)
        self.font_combo = QFontComboBox()
        self.font_combo.currentFontChanged.connect(lambda font: self._emit("font_family", font.family()))
        self.text_section.form.addRow("Police", self.font_combo)
        self.font_size_spin = self._spin(6, 512, "font_size")
        self.bold_check = QCheckBox("Gras")
        self.bold_check.toggled.connect(lambda checked: self._emit("bold", checked))
        self.italic_check = QCheckBox("Italique")
        self.italic_check.toggled.connect(lambda checked: self._emit("italic", checked))
        size_row = QWidget()
        size_layout = QHBoxLayout(size_row)
        size_layout.setContentsMargins(0, 0, 0, 0)
        size_layout.addWidget(self.font_size_spin)
        size_layout.addWidget(self.bold_check)
        size_layout.addWidget(self.italic_check)
        self.text_section.form.addRow("Corps", size_row)
        self.align_h = QComboBox(objectName="text_align_h")
        for value, label in (("left", "Gauche"), ("center", "Centre"), ("right", "Droite")):
            self.align_h.addItem(label, value)
        self.align_h.currentIndexChanged.connect(lambda: self._emit("align_h", self.align_h.currentData()))
        self.align_v = QComboBox(objectName="text_align_v")
        for value, label in (("top", "Haut"), ("center", "Milieu"), ("bottom", "Bas")):
            self.align_v.addItem(label, value)
        self.align_v.currentIndexChanged.connect(lambda: self._emit("align_v", self.align_v.currentData()))
        self.text_section.form.addRow("Alignement", self._pair(self.align_h, "", self.align_v))
        self.tracking_spin = self._double(-100, 500, 0.5, "tracking")
        self.text_section.form.addRow("Approche", self.tracking_spin)
        self.line_spacing_spin = self._double(0.3, 5.0, 0.05, "line_spacing")
        self.text_section.form.addRow("Interligne", self.line_spacing_spin)
        self.box_check = QCheckBox("Texte en boîte (retour à la ligne)")
        self.box_check.toggled.connect(lambda checked: self._emit("box_text", checked))
        self.text_section.form.addRow(self.box_check)
        self.autosize_check = QCheckBox("Taille automatique")
        self.autosize_check.toggled.connect(lambda checked: self._emit("autosize", checked))
        self.text_section.form.addRow(self.autosize_check)
        root.addWidget(self.text_section)

        # --- Ombre et fond (texte) ------------------------------------------------------------
        self.style_section = _Section("Ombre et fond")
        self.shadow_field = _ColorField("#000000AA")
        self.shadow_field.changed.connect(lambda value: self._emit("shadow_color", value))
        self.style_section.form.addRow("Ombre", self.shadow_field)
        self.shadow_x_spin = self._spin(-256, 256, "shadow_offset_x")
        self.shadow_y_spin = self._spin(-256, 256, "shadow_offset_y")
        self.style_section.form.addRow(
            "Décalage", self._pair(self.shadow_x_spin, "Y", self.shadow_y_spin, first_label="X")
        )
        self.shadow_blur_spin = self._double(0, 200, 0.5, "shadow_blur")
        self.style_section.form.addRow("Flou de l'ombre", self.shadow_blur_spin)
        self.background_check = QCheckBox("Fond derrière le texte")
        self.background_check.toggled.connect(lambda checked: self._emit("background_enabled", checked))
        self.style_section.form.addRow(self.background_check)
        self.background_field = _ColorField("#000000AA")
        self.background_field.changed.connect(lambda value: self._emit("background_color", value))
        self.style_section.form.addRow("Couleur du fond", self.background_field)
        self.padding_spin = self._spin(0, 1024, "background_padding")
        self.radius_spin = self._double(0, 1024, 1, "background_radius")
        self.style_section.form.addRow("Marge / rayon", self._pair(self.padding_spin, "", self.radius_spin))
        root.addWidget(self.style_section)

        # --- Calque -----------------------------------------------------------------------------
        self.layer_section = _Section("Calque")
        self.parent_combo = QComboBox(objectName="layer_parent")
        self.parent_combo.currentIndexChanged.connect(self._on_parent_changed)
        self.layer_section.form.addRow("Parent", self.parent_combo)
        self.motion_blur_check = QCheckBox("Flou de mouvement")
        self.motion_blur_check.setToolTip(
            "Flou des déplacements, rotations et échelles. Désactivable pour toute la séquence "
            "(menu Calques) sur les machines modestes."
        )
        self.motion_blur_check.toggled.connect(lambda checked: self._emit("motion_blur", checked))
        self.layer_section.form.addRow(self.motion_blur_check)
        root.addWidget(self.layer_section)

        # Compatibilité : anciens accès par nom de champ.
        self.color_edits: dict[str, QLineEdit] = {
            "fill_color": self.fill_field.edit,
            "stroke_color": self.stroke_field.edit,
            "shadow_color": self.shadow_field.edit,
        }
        self.font_family_edit = self.font_combo
        self.setEnabled(False)

    # -- widgets ------------------------------------------------------------------------------

    def _spin(self, minimum: int, maximum: int, field_name: str) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.valueChanged.connect(lambda value, name=field_name: self._emit(name, int(value)))
        return spin

    def _double(self, minimum: float, maximum: float, step: float, field_name: str) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setSingleStep(step)
        spin.setDecimals(2)
        spin.valueChanged.connect(lambda value, name=field_name: self._emit(name, float(value)))
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
        if separator:
            layout.addWidget(QLabel(separator))
        layout.addWidget(second)
        return container

    def _emit(self, field_name: str, value: object) -> None:
        if not self._updating:
            self.field_changed.emit(field_name, value)

    def _on_parent_changed(self) -> None:
        if not self._updating:
            self.parent_changed.emit(str(self.parent_combo.currentData() or ""))

    # -- chargement ---------------------------------------------------------------------------

    def set_parent_choices(self, choices: list[tuple[str, str]], current: str) -> None:
        """Parents possibles (sans cycle) du calque affiché."""
        self._updating = True
        try:
            self.parent_combo.clear()
            self.parent_combo.addItem("Aucun", "")
            for clip_id, name in choices:
                self.parent_combo.addItem(name, clip_id)
            index = self.parent_combo.findData(current)
            self.parent_combo.setCurrentIndex(max(0, index))
        finally:
            self._updating = False

    def set_graphic(self, graphic: object) -> None:
        """Charge un calque sans produire de faux événements d'édition."""
        self._updating = True
        try:
            if not isinstance(graphic, GraphicOverlay):
                self._graphic = None
                self.setEnabled(False)
                self.type_label.setText("--")
                self.text_edit.clear()
                self.text_edit.show()
                self.source_label.setText("--")
                return
            self._graphic = graphic
            kind = graphic.type
            name = TYPE_NAMES.get(kind, kind.value)
            self.type_label.setText(name)
            is_text = kind == GraphicType.TEXT
            is_shape = kind in (GraphicType.SHAPE, GraphicType.RECTANGLE)
            sized = kind in (GraphicType.TEXT, GraphicType.SHAPE, GraphicType.RECTANGLE,
                             GraphicType.SOLID, GraphicType.IMAGE)
            coloured = kind in (GraphicType.TEXT, GraphicType.SHAPE, GraphicType.RECTANGLE, GraphicType.SOLID)
            if self.text_edit.toPlainText() != graphic.text:
                self.text_edit.setPlainText(graphic.text)
            for widget in (self.text_edit, self.text_label):
                widget.setVisible(is_text)
            for widget in (self.source_label, self.source_row_label):
                widget.setVisible(kind == GraphicType.IMAGE)
            self.source_label.setText(graphic.source_path or "Généré")
            for widget in (self.width_spin, self.height_spin, self.size_label):
                widget.setVisible(sized)
            self.width_spin.parentWidget().setVisible(sized)
            self.width_spin.setValue(graphic.width)
            self.height_spin.setValue(graphic.height)
            self.fill_field.setVisible(coloured)
            self.fill_label.setVisible(coloured)
            self.fill_field.set_value(graphic.fill_color)
            self.shape_section.setVisible(kind == GraphicType.SHAPE)
            self.shape_combo.setCurrentIndex(max(0, self.shape_combo.findData(graphic.shape.value)))
            self.corner_spin.setValue(graphic.corner_radius)
            self.sides_spin.setValue(graphic.polygon_sides)
            self.fill_check.setChecked(graphic.fill_enabled)
            self.stroke_section.setVisible(is_shape or is_text)
            self.stroke_field.set_value(graphic.stroke_color)
            self.stroke_width_spin.setValue(graphic.stroke_width)
            self.text_section.setVisible(is_text)
            self.style_section.setVisible(is_text)
            # Famille résolue (mémorisée) : une police absente ne relance pas
            # la reconstruction des alias de Qt à chaque sélection.
            from core.mograph_raster import resolve_family

            self.font_combo.setCurrentFont(QFont(resolve_family(graphic.font_family)))
            self.font_size_spin.setValue(graphic.font_size)
            self.bold_check.setChecked(graphic.bold)
            self.italic_check.setChecked(graphic.italic)
            self.align_h.setCurrentIndex(max(0, self.align_h.findData(graphic.align_h)))
            self.align_v.setCurrentIndex(max(0, self.align_v.findData(graphic.align_v)))
            self.tracking_spin.setValue(graphic.tracking)
            self.line_spacing_spin.setValue(graphic.line_spacing)
            self.box_check.setChecked(graphic.box_text)
            self.autosize_check.setChecked(graphic.autosize)
            self.shadow_field.set_value(graphic.shadow_color)
            self.shadow_x_spin.setValue(graphic.shadow_offset_x)
            self.shadow_y_spin.setValue(graphic.shadow_offset_y)
            self.shadow_blur_spin.setValue(graphic.shadow_blur)
            self.background_check.setChecked(graphic.background_enabled)
            self.background_field.set_value(graphic.background_color)
            self.padding_spin.setValue(graphic.background_padding)
            self.radius_spin.setValue(graphic.background_radius)
            self.motion_blur_check.setChecked(graphic.motion_blur)
            self.motion_blur_check.setVisible(not graphic.is_container)
            index = self.parent_combo.findData(graphic.parent_id)
            if index >= 0:
                self.parent_combo.setCurrentIndex(index)
        finally:
            self._updating = False


__all__ = ["GraphicsEditor"]
