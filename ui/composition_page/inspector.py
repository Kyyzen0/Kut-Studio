"""Inspecteur d'un nœud de composition : les réglages du nœud choisi, un formulaire par type de nœud.

Chaque champ rend un nœud neuf (les nœuds sont immuables) : :attr:`NodeInspector.changed` (le nœud, le nom du champ),
que la fenêtre applique au graphe ; une rafale sur un même champ fait une étape d'historique. Les masques réutilisent
l'éditeur de masques des clips ; les effets, les paramètres déclarés de chaque effet (:mod:`core.effects_model`) ;
l'étalonnage, les réglages primaires de l'inspecteur.
"""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QRegularExpression, Signal
from PySide6.QtGui import QColor, QRegularExpressionValidator
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core.blend_modes import BLEND_MODES, blend_label_key
from core.compositing import CHROMA_KEY_COLORS, ChromaKey, Compositing, is_hex_color
from core.composition import (
    EffectsNode,
    GradeNode,
    GraphicNode,
    KeyNode,
    MaskNode,
    MediaNode,
    MergeNode,
    OutputNode,
    SolidNode,
    TransformNode,
    kind_of,
)
from core.effects_model import EFFECT_PARAMETER_SPECS, EffectType, create_effect
from ui.design_system import Spacing
from ui.i18n import translate
from ui.theme import set_role

_GRADE_FIELDS = (("exposure", -2.0, 2.0, 0.05), ("contrast", -1.0, 1.0, 0.05), ("saturation", 0.0, 2.0, 0.05),
                 ("temperature", -100.0, 100.0, 1.0), ("hue", -180.0, 180.0, 1.0), ("shadows", -1.0, 1.0, 0.05),
                 ("highlights", -1.0, 1.0, 0.05))
_GRADE_LABELS = {"contrast": "effects.param.contrast"}
"""Libellés des réglages d'étalonnage (ceux de l'inspecteur, sauf le contraste, nommé par les effets)."""
_TRANSFORM_FIELDS = (("position_x", -200.0, 200.0, "%", 100.0), ("position_y", -200.0, 200.0, "%", 100.0),
                     ("scale", 0.0, 1000.0, "%", 100.0), ("rotation", -3600.0, 3600.0, "°", 1.0),
                     ("opacity", 0.0, 100.0, "%", 100.0), ("anchor_x", -100.0, 200.0, "%", 100.0),
                     ("anchor_y", -100.0, 200.0, "%", 100.0))


def _spin(low: float, high: float, step: float, suffix: str = "", decimals: int = 2) -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    spin.setRange(low, high)
    spin.setSingleStep(step)
    spin.setDecimals(decimals)
    spin.setSuffix(suffix)
    spin.setKeyboardTracking(False)
    return spin


class NodeInspector(QWidget):
    """Les réglages du nœud choisi ; :attr:`changed` (nœud neuf, nom du champ)."""

    changed = Signal(object, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("composition_node_inspector")
        self._node = None
        self._updating = False
        self._assets: list[tuple[str, str]] = []
        root = QVBoxLayout(self)
        root.setContentsMargins(Spacing.sm, Spacing.sm, Spacing.sm, Spacing.sm)
        root.setSpacing(Spacing.sm)
        self.title = QLabel()
        set_role(self.title, "label-secondary")
        root.addWidget(self.title)
        name_row = QFormLayout()
        self.label_edit = QLineEdit()
        self.label_edit.editingFinished.connect(lambda: self._edit("label", label=self.label_edit.text()))
        self.label_caption = QLabel()
        name_row.addRow(self.label_caption, self.label_edit)
        root.addLayout(name_row)
        self.stack = QStackedWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(self.stack)
        root.addWidget(scroll, 1)
        self.pages: dict[str, QWidget] = {}
        self._fields: dict[str, dict[str, object]] = {}
        for kind, build in (("media", self._build_media), ("graphic", self._build_graphic),
                            ("solid", self._build_solid), ("transform", self._build_transform),
                            ("merge", self._build_merge), ("mask", self._build_mask), ("key", self._build_key),
                            ("effects", self._build_effects), ("grade", self._build_grade),
                            ("output", self._build_output)):
            page = QWidget()
            form = QFormLayout(page)
            form.setContentsMargins(0, 0, 0, 0)
            form.setSpacing(Spacing.xs)
            form.setRowWrapPolicy(QFormLayout.WrapLongRows)
            self._fields[kind] = {}
            build(form, self._fields[kind])
            self.pages[kind] = page
            self.stack.addWidget(page)
        self.retranslate()
        self.set_node(None)

    # -- formulaires --------------------------------------------------------------------------------------------

    def _number(self, form: QFormLayout, fields: dict, name: str, spin: QDoubleSpinBox, key: str,
                scale: float = 1.0) -> None:
        spin.valueChanged.connect(lambda value, n=name, k=scale: self._edit(n, **{n: value / k}))
        fields[name] = (spin, key, scale)
        form.addRow(QLabel(), spin)

    def _check(self, form: QFormLayout, fields: dict, name: str, key: str) -> None:
        box = QCheckBox()
        box.toggled.connect(lambda checked, n=name: self._edit(n, **{n: bool(checked)}))
        fields[name] = (box, key, None)
        form.addRow(box)

    def _build_media(self, form, fields) -> None:
        self.asset_combo = QComboBox()
        self.asset_combo.currentIndexChanged.connect(
            lambda _i: self._edit("asset_id", asset_id=self.asset_combo.currentData() or ""))
        fields["asset_id"] = (self.asset_combo, "comp.field.asset", None)
        form.addRow(QLabel(), self.asset_combo)
        for name in ("start", "source_in", "source_out"):
            self._number(form, fields, name, _spin(0.0, 86400.0, 0.1, " s"), f"comp.field.{name}")
        self._number(form, fields, "gain_db", _spin(-60.0, 24.0, 0.5, " dB", 1), "comp.field.gain")
        self._check(form, fields, "muted", "comp.field.muted")
        self._check(form, fields, "fill", "comp.field.fill")
        for name in ("pan_x", "pan_y"):
            self._number(form, fields, name, _spin(-100.0, 100.0, 5.0, " %", 0), f"comp.field.{name}", 100.0)

    def _build_graphic(self, form, fields) -> None:
        self.text_edit = QLineEdit()
        self.text_edit.editingFinished.connect(self._edit_text)
        fields["text"] = (self.text_edit, "comp.field.text", None)
        form.addRow(QLabel(), self.text_edit)
        self._number(form, fields, "start", _spin(0.0, 86400.0, 0.1, " s"), "comp.field.start")
        self._number(form, fields, "duration", _spin(0.04, 86400.0, 0.1, " s"), "comp.field.duration")

    def _build_solid(self, form, fields) -> None:
        self.color_button = QPushButton()
        self.color_button.setObjectName("chipButton")
        self.color_button.clicked.connect(self._choose_color)
        fields["color"] = (self.color_button, "comp.field.color", None)
        form.addRow(QLabel(), self.color_button)

    def _build_transform(self, form, fields) -> None:
        for name, low, high, suffix, scale in _TRANSFORM_FIELDS:
            spin = _spin(low, high, 1.0, f" {suffix}" if suffix != "°" else suffix, 1)
            spin.valueChanged.connect(lambda value, n=name, k=scale: self._edit_transform(n, value / k))
            fields[name] = (spin, f"comp.field.{name}", scale)
            form.addRow(QLabel(), spin)
        for name in ("flip_h", "flip_v"):
            box = QCheckBox()
            box.toggled.connect(lambda checked, n=name: self._edit_transform(n, bool(checked)))
            fields[name] = (box, f"comp.field.{name}", None)
            form.addRow(box)
        self.animated_hint = QLabel()
        self.animated_hint.setWordWrap(True)
        set_role(self.animated_hint, "label-secondary")
        form.addRow(self.animated_hint)

    def _build_merge(self, form, fields) -> None:
        self.blend_combo = QComboBox()
        for mode in BLEND_MODES:
            self.blend_combo.addItem("", mode.value)
        self.blend_combo.currentIndexChanged.connect(
            lambda _i: self._edit("blend", blend=self.blend_combo.currentData()))
        fields["blend"] = (self.blend_combo, "comp.field.blend", None)
        form.addRow(QLabel(), self.blend_combo)
        self._number(form, fields, "opacity", _spin(0.0, 100.0, 5.0, " %", 0), "comp.field.opacity", 100.0)

    def _build_mask(self, form, fields) -> None:
        from ui.compositing_editor import CompositingEditor

        self.mask_editor = CompositingEditor("")
        self.mask_editor.set_chroma_visible(False)
        self.mask_editor.blend_mode.setVisible(False)
        label = self.mask_editor.layout().itemAt(0).layout().labelForField(self.mask_editor.blend_mode)
        if label is not None:
            label.setVisible(False)
        self.mask_editor.value_changed.connect(lambda value: self._edit("masks", masks=tuple(value.masks)))
        form.addRow(self.mask_editor)

    def _build_key(self, form, fields) -> None:
        self.key_color = QComboBox()
        self.key_color.setEditable(True)
        for color in CHROMA_KEY_COLORS:
            self.key_color.addItem(color)
        self.key_color.setValidator(QRegularExpressionValidator(QRegularExpression(r"#[0-9A-Fa-f]{0,6}"), self))
        self.key_color.currentTextChanged.connect(              # une couleur en cours de frappe n'est pas envoyée
            lambda text: self._edit_key(color=text.upper()) if is_hex_color(text) else None)
        fields["key_color"] = (self.key_color, "comp.field.key_color", None)
        form.addRow(QLabel(), self.key_color)
        for name, key in (("tolerance", "comp.field.tolerance"), ("softness", "comp.field.softness"),
                          ("spill_suppression", "comp.field.spill")):
            spin = _spin(0.0, 100.0, 1.0, " %", 0)
            spin.valueChanged.connect(lambda value, n=name: self._edit_key(**{n: value / 100.0}))
            fields[name] = (spin, key, 100.0)
            form.addRow(QLabel(), spin)

    def _build_effects(self, form, fields) -> None:
        self.effects_list = QListWidget()
        self.effects_list.setMaximumHeight(96)
        self.effects_list.currentRowChanged.connect(lambda _row: self._load_effect())
        form.addRow(self.effects_list)
        row = QHBoxLayout()
        self.effect_kind = QComboBox()
        for kind in EffectType:
            self.effect_kind.addItem("", kind.value)
        self.add_effect = QPushButton()
        self.add_effect.clicked.connect(self._append_effect)
        self.remove_effect = QPushButton()
        self.remove_effect.clicked.connect(self._remove_effect)
        row.addWidget(self.effect_kind, 1)
        row.addWidget(self.add_effect)
        row.addWidget(self.remove_effect)
        form.addRow(row)
        self.effect_enabled = QCheckBox()
        self.effect_enabled.toggled.connect(lambda checked: self._edit_effect(enabled=bool(checked)))
        form.addRow(self.effect_enabled)
        self.effect_params = QWidget()
        self.effect_form = QFormLayout(self.effect_params)
        self.effect_form.setContentsMargins(0, 0, 0, 0)
        form.addRow(self.effect_params)

    def _build_grade(self, form, fields) -> None:
        for name, low, high, step in _GRADE_FIELDS:
            spin = _spin(low, high, step)
            spin.valueChanged.connect(lambda value, n=name: self._edit_grade(n, value))
            fields[name] = (spin, _GRADE_LABELS.get(name, f"inspector.color.{name}"), None)
            form.addRow(QLabel(), spin)

    def _build_output(self, form, fields) -> None:
        self.output_hint = QLabel()
        self.output_hint.setWordWrap(True)
        set_role(self.output_hint, "label-secondary")
        form.addRow(self.output_hint)

    # -- affichage ----------------------------------------------------------------------------------------------

    def set_assets(self, assets: list[tuple[str, str]]) -> None:
        """Les médias que peut montrer un nœud média : ``[(identifiant, nom)]``."""
        self._assets = list(assets)

    def set_node(self, node, *, editable: bool = True) -> None:
        """Affiche les réglages de ``node`` (``None`` : rien), sans émettre."""
        self._node = node
        self.setEnabled(node is not None and editable)
        if node is None:
            self.title.clear()
            self.label_edit.clear()
            return
        kind = kind_of(node)
        self.stack.setCurrentWidget(self.pages[kind])
        self.title.setText(translate(f"comp.node.{kind}"))
        self._updating = True
        try:
            self.label_edit.setVisible(not isinstance(node, OutputNode))
            self.label_caption.setVisible(not isinstance(node, OutputNode))
            self.label_edit.setText(getattr(node, "label", ""))
            self._load(kind, node)
        finally:
            self._updating = False

    def _load(self, kind: str, node) -> None:
        fields = self._fields[kind]
        if isinstance(node, MediaNode):
            self.asset_combo.clear()
            self.asset_combo.addItem(translate("comp.field.no_asset"), "")
            for asset_id, name in self._assets:
                self.asset_combo.addItem(name, asset_id)
            self.asset_combo.setCurrentIndex(max(0, self.asset_combo.findData(node.asset_id)))
        elif isinstance(node, GraphicNode):
            text = getattr(node.graphic, "text", "")
            self.text_edit.setText(text)
            self.text_edit.setEnabled(getattr(getattr(node.graphic, "type", None), "value", "") == "text")
        elif isinstance(node, SolidNode):
            self.color_button.setText(node.color)
            self.color_button.setStyleSheet(f"background: {node.color};")
        elif isinstance(node, TransformNode):
            for name, (widget, _key, scale) in fields.items():
                value = getattr(node.transform, name)
                if isinstance(widget, QCheckBox):
                    widget.setChecked(bool(value))
                else:
                    widget.setValue(float(value) * scale)  # type: ignore[union-attr]
            self.animated_hint.setVisible(bool(node.keyframes))
        elif isinstance(node, MergeNode):
            self.blend_combo.setCurrentIndex(max(0, self.blend_combo.findData(node.blend.value)))
        elif isinstance(node, MaskNode):
            self.mask_editor.set_value(Compositing(masks=node.masks))
        elif isinstance(node, KeyNode):
            self.key_color.setCurrentText(node.key.color)
        elif isinstance(node, EffectsNode):
            row = self.effects_list.currentRow()
            self.effects_list.clear()
            for effect in node.effects:
                QListWidgetItem(translate(f"effects.name.{effect.type.value}"), self.effects_list)
            self.effects_list.setCurrentRow(min(max(row, 0), len(node.effects) - 1) if node.effects else -1)
            self._load_effect()
        elif isinstance(node, GradeNode):
            grade = getattr(node.grade, "correctors", None)
            base = node.grade if grade is None else node.grade.correctors[0].grade
            for name, (widget, _key, _scale) in fields.items():
                widget.setValue(float(getattr(base, name)))  # type: ignore[union-attr]
                widget.setEnabled(grade is None)          # un graphe d'étalonnage se règle sur la page Couleur
        for name, (widget, _key, scale) in fields.items():
            if isinstance(widget, QDoubleSpinBox) and not isinstance(node, (TransformNode, GradeNode)) and \
                    hasattr(node, name) and name not in ("tolerance", "softness", "spill_suppression"):
                widget.setValue(float(getattr(node, name)) * (scale or 1.0))
            elif isinstance(widget, QCheckBox) and hasattr(node, name) and not isinstance(node, TransformNode):
                widget.setChecked(bool(getattr(node, name)))
        if isinstance(node, KeyNode):
            for name in ("tolerance", "softness", "spill_suppression"):
                fields[name][0].setValue(getattr(node.key, name) * 100.0)  # type: ignore[union-attr]

    def _load_effect(self) -> None:
        node = self._node
        while self.effect_form.rowCount():
            self.effect_form.removeRow(0)
        effect = self._current_effect()
        self.remove_effect.setEnabled(effect is not None)
        self.effect_enabled.setVisible(effect is not None)
        if effect is None or not isinstance(node, EffectsNode):
            return
        previous, self._updating = self._updating, True
        try:
            self.effect_enabled.setChecked(effect.enabled)
            for spec in EFFECT_PARAMETER_SPECS.get(effect.type, ()):
                spin = _spin(spec.minimum, spec.maximum, (spec.maximum - spec.minimum) / 100.0 or 0.1)
                spin.setValue(float(effect.params.get(spec.name, spec.default)))
                spin.valueChanged.connect(lambda value, name=spec.name: self._edit_effect(param=(name, value)))
                self.effect_form.addRow(translate(f"effects.param.{spec.name}"), spin)
        finally:
            self._updating = previous

    def retranslate(self) -> None:
        self.label_caption.setText(translate("comp.field.label"))
        for kind, fields in self._fields.items():
            form = self.pages[kind].layout()
            for _name, (widget, key, _scale) in fields.items():
                label = form.labelForField(widget)  # type: ignore[union-attr]
                if isinstance(widget, QCheckBox):
                    widget.setText(translate(key))
                elif isinstance(label, QLabel):
                    label.setText(translate(key))
        for index, mode in enumerate(BLEND_MODES):
            self.blend_combo.setItemText(index, translate(blend_label_key(mode)))
        for index, kind in enumerate(EffectType):
            self.effect_kind.setItemText(index, translate(f"effects.name.{kind.value}"))
        self.add_effect.setText(translate("comp.field.add_effect"))
        self.remove_effect.setText(translate("comp.field.remove_effect"))
        self.effect_enabled.setText(translate("comp.field.effect_enabled"))
        self.animated_hint.setText(translate("comp.field.animated"))
        self.output_hint.setText(translate("comp.output.hint"))
        if self._node is not None:
            self.set_node(self._node, editable=self.isEnabled())

    # -- réglages -----------------------------------------------------------------------------------------------

    def _emit(self, node, field: str) -> None:
        if node == self._node:
            return
        self._node = node
        self.changed.emit(node, field)

    def _edit(self, field: str, **changes) -> None:
        if self._updating or self._node is None:
            return
        try:
            self._emit(replace(self._node, **changes), field)
        except (TypeError, ValueError):
            return

    def _edit_text(self) -> None:
        node = self._node
        if self._updating or not isinstance(node, GraphicNode) or node.graphic is None:
            return
        self._emit(replace(node, graphic=replace(node.graphic, text=self.text_edit.text())), "text")

    def _choose_color(self) -> None:
        node = self._node
        if not isinstance(node, SolidNode):
            return
        color = QColorDialog.getColor(QColor(node.color), self)
        if color.isValid():
            self._emit(replace(node, color=color.name().upper()), "color")
            self.set_node(self._node)

    def _edit_transform(self, name: str, value) -> None:
        node = self._node
        if self._updating or not isinstance(node, TransformNode):
            return
        self._emit(replace(node, transform=replace(node.transform, **{name: value})), name)

    def _edit_key(self, **changes) -> None:
        node = self._node
        if self._updating or not isinstance(node, KeyNode):
            return
        values = {**vars(node.key), **changes}
        self._emit(replace(node, key=ChromaKey(**values)), "key")

    def _edit_grade(self, name: str, value: float) -> None:
        node = self._node
        if self._updating or not isinstance(node, GradeNode) or getattr(node.grade, "correctors", None) is not None:
            return
        self._emit(replace(node, grade=node.grade.with_field(name, value)), name)

    def _current_effect(self):
        node = self._node
        row = self.effects_list.currentRow()
        if not isinstance(node, EffectsNode) or not 0 <= row < len(node.effects):
            return None
        return node.effects[row]

    def _append_effect(self) -> None:
        node = self._node
        if not isinstance(node, EffectsNode):
            return
        effect = create_effect(EffectType(self.effect_kind.currentData()))
        self._emit(replace(node, effects=node.effects + (effect,)), "effects")
        self.set_node(self._node)
        self.effects_list.setCurrentRow(len(node.effects))

    def _remove_effect(self) -> None:
        node = self._node
        row = self.effects_list.currentRow()
        if not isinstance(node, EffectsNode) or not 0 <= row < len(node.effects):
            return
        self._emit(replace(node, effects=node.effects[:row] + node.effects[row + 1:]), "effects")
        self.set_node(self._node)

    def _edit_effect(self, *, enabled: bool | None = None, param: tuple[str, float] | None = None) -> None:
        node = self._node
        effect = self._current_effect()
        if self._updating or not isinstance(node, EffectsNode) or effect is None:
            return
        row = self.effects_list.currentRow()
        if enabled is not None:
            effect = replace(effect, enabled=enabled)
        if param is not None:
            effect = replace(effect, params={**effect.params, param[0]: param[1]})
        effects = node.effects[:row] + (effect,) + node.effects[row + 1:]
        self._emit(replace(node, effects=effects), f"effect{row}")


__all__ = ["NodeInspector"]
