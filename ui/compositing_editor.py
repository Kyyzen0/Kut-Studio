"""Éditeur de compositing de l'inspecteur : fusion, masques, incrustation.

- Mode de fusion : la liste centralisée (:mod:`core.blend_modes`).
- Masques : une pile ; chaque masque a sa forme (rectangle, ellipse,
  polygone), son opération (Ajouter / Soustraire / Intersection), son
  inversion, son contour adouci, sa dilatation, son opacité et sa
  position / taille / rotation. Les valeurs animables passent par le
  moteur central (losange dans l'éditeur de courbes, ``mask.<id>.*``).
- Chroma key : réservé aux clips vidéo.

L'éditeur émet la nouvelle valeur complète (``value_changed``) ; la
fenêtre l'applique et regroupe les rafales dans l'historique.
"""
from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.blend_modes import BLEND_MODES, blend_label_key
from core.compositing import ChromaKey, Compositing, Mask, MaskMode, MaskShape
from ui import i18n
from ui.adaptive_layout import FlowLayout
from ui.design_system import Spacing

# (valeur, clé i18n du libellé) : traduit à l'affichage, jamais à l'import.
_SHAPES = (
    (MaskShape.RECTANGLE, "mograph.shape.rectangle"),
    (MaskShape.ELLIPSE, "mograph.compositing.shape_ellipse"),
    (MaskShape.POLYGON, "mograph.shape.polygon"),
)
_MODES = (
    (MaskMode.ADD, "common.add"),
    (MaskMode.SUBTRACT, "mograph.compositing.mode_subtract"),
    (MaskMode.INTERSECT, "mograph.compositing.mode_intersect"),
)

_MASK_FIELDS = (
    # (champ, clé i18n du libellé, min, max, pas, facteur d'affichage)
    ("position_x", "mograph.compositing.position_x", -200.0, 300.0, 1.0, 100.0),
    ("position_y", "mograph.compositing.position_y", -200.0, 300.0, 1.0, 100.0),
    ("width", "graphics.property.width", 0.0, 400.0, 1.0, 100.0),
    ("height", "graphics.property.height", 0.0, 400.0, 1.0, 100.0),
    ("rotation", "mograph.compositing.rotation", -3600.0, 3600.0, 1.0, 1.0),
    ("feather", "mograph.compositing.feather", 0.0, 100.0, 0.5, 100.0),
    ("expansion", "mograph.compositing.expansion", -100.0, 100.0, 0.5, 100.0),
    ("opacity", "field.opacity", 0.0, 100.0, 1.0, 100.0),
)


class CompositingEditor(QGroupBox):
    value_changed = Signal(object)

    def __init__(self, group_style: str, parent=None):
        super().__init__(i18n.translate("inspector.tab.compositing"), parent)
        self.setObjectName("compositing_group")
        self.setStyleSheet(group_style)
        self._value = Compositing()
        self._updating = False
        root = QVBoxLayout(self)
        root.setContentsMargins(Spacing.md, Spacing.md, Spacing.md, Spacing.md)
        root.setSpacing(Spacing.xs)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        root.addLayout(form)

        self.blend_mode = QComboBox(objectName="blend_mode")
        for mode in BLEND_MODES:
            self.blend_mode.addItem(i18n.translate(blend_label_key(mode)), mode.value)
        form.addRow(i18n.translate("mograph.compositing.blend"), self.blend_mode)

        # --- Pile de masques -------------------------------------------------------------------
        add_row = FlowLayout(spacing=Spacing.xs)  # trois contrôles : à la ligne plutôt qu'au-delà de l'inspecteur
        self.mask_shape = QComboBox(objectName="mask_shape")
        for shape, label in _SHAPES:
            self.mask_shape.addItem(i18n.translate(label), shape.value)
        self.add_mask = QPushButton(i18n.translate("mograph.compositing.add_mask"), objectName="add_mask")
        self.remove_mask = QPushButton(i18n.translate("action.delete"), objectName="remove_mask")
        add_row.addWidget(self.mask_shape)
        add_row.addWidget(self.add_mask)
        add_row.addWidget(self.remove_mask)
        root.addLayout(add_row)
        self.mask_list = QListWidget(objectName="mask_list")
        self.mask_list.setMaximumHeight(84)
        root.addWidget(self.mask_list)

        self.mask_form_host = QWidget()
        mask_form = QFormLayout(self.mask_form_host)
        mask_form.setContentsMargins(0, 0, 0, 0)
        mask_form.setSpacing(Spacing.xs)
        mask_form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.mask_mode = QComboBox(objectName="mask_mode")
        for mode, label in _MODES:
            self.mask_mode.addItem(i18n.translate(label), mode.value)
        mask_form.addRow(i18n.translate("mograph.compositing.operation"), self.mask_mode)
        self.inverted = QCheckBox(i18n.translate("mograph.compositing.invert"), objectName="mask_inverted")
        mask_form.addRow(self.inverted)
        self.mask_spins: dict[str, QDoubleSpinBox] = {}
        for name, label, low, high, step, _factor in _MASK_FIELDS:
            spin = QDoubleSpinBox(objectName=f"mask_{name}")
            spin.setRange(low, high)
            spin.setSingleStep(step)
            spin.setDecimals(1)
            spin.setSuffix(" °" if name == "rotation" else " %")
            mask_form.addRow(i18n.translate(label), spin)
            self.mask_spins[name] = spin
        root.addWidget(self.mask_form_host)

        # --- Chroma key (vidéo) ----------------------------------------------------------------
        self.key_host = QWidget()
        key_form = QFormLayout(self.key_host)
        key_form.setContentsMargins(0, 0, 0, 0)
        key_form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.key_enabled = QCheckBox(i18n.translate("mograph.compositing.chroma_key"), objectName="chroma_key_enabled")
        key_form.addRow(self.key_enabled)
        self.key_color = QComboBox(objectName="chroma_key_color")
        self.key_color.setEditable(True)
        self.key_color.addItems(["#00FF00", "#0000FF"])
        key_form.addRow(i18n.translate("group.color"), self.key_color)
        self.spins = {}
        for key, label, lo, hi, value in (
            ("tolerance", i18n.translate("mograph.compositing.tolerance"), 0, 1, .1), ("softness", i18n.translate("mograph.compositing.softness"), 0, 1, .05),
            ("spill_suppression", i18n.translate("mograph.compositing.spill"), 0, 1, 0),
        ):
            spin = QDoubleSpinBox(objectName=f"chroma_{key}")
            spin.setRange(lo, hi)
            spin.setSingleStep(.01)
            spin.setValue(value)
            key_form.addRow(label, spin)
            self.spins[key] = spin
        root.addWidget(self.key_host)

        self.blend_mode.currentIndexChanged.connect(self._emit)
        self.add_mask.clicked.connect(self._append_mask)
        self.remove_mask.clicked.connect(self._remove_mask)
        self.mask_list.currentRowChanged.connect(self._load_mask)
        self.mask_mode.currentIndexChanged.connect(self._mask_edited)
        self.inverted.toggled.connect(self._mask_edited)
        for spin in self.mask_spins.values():
            spin.valueChanged.connect(self._mask_edited)
        self.key_enabled.toggled.connect(self._emit)
        self.key_color.currentTextChanged.connect(self._emit)
        for spin in self.spins.values():
            spin.valueChanged.connect(self._emit)
        self._refresh_mask_widgets()

    # -- chargement -----------------------------------------------------------------------------

    def set_chroma_visible(self, visible: bool) -> None:
        self.key_host.setVisible(bool(visible))

    def set_value(self, value):
        self._updating = True
        try:
            self._value = value if isinstance(value, Compositing) else Compositing()
            self.blend_mode.setCurrentIndex(max(0, self.blend_mode.findData(self._value.blend_mode.value)))
            key = self._value.chroma_key
            self.key_enabled.setChecked(key.enabled)
            self.key_color.setCurrentText(key.color)
            for name, spin in self.spins.items():
                spin.setValue(getattr(key, name))
            row = self.mask_list.currentRow()
            self._fill_mask_list(row)
        finally:
            self._updating = False
        self._refresh_mask_widgets()

    def _fill_mask_list(self, row: int) -> None:
        self.mask_list.blockSignals(True)
        self.mask_list.clear()
        for index, mask in enumerate(self._value.masks, start=1):
            label = f"{mask.name or i18n.translate('mograph.compositing.mask_name', index=index)} · {i18n.translate(dict(_SHAPES)[mask.shape])} · {i18n.translate(dict(_MODES)[mask.mode])}"
            if mask.inverted:
                label += i18n.translate("mograph.compositing.inverted_suffix")
            QListWidgetItem(label, self.mask_list)
        count = len(self._value.masks)
        if count:
            self.mask_list.setCurrentRow(min(max(row, 0), count - 1))
        self.mask_list.blockSignals(False)
        self._load_mask(self.mask_list.currentRow())

    def _current_mask(self) -> Mask | None:
        row = self.mask_list.currentRow()
        if 0 <= row < len(self._value.masks):
            return self._value.masks[row]
        return None

    def _load_mask(self, _row: int) -> None:
        mask = self._current_mask()
        previous = self._updating
        self._updating = True
        try:
            if mask is not None:
                self.mask_mode.setCurrentIndex(max(0, self.mask_mode.findData(mask.mode.value)))
                self.inverted.setChecked(mask.inverted)
                for name, _label, _low, _high, _step, factor in _MASK_FIELDS:
                    self.mask_spins[name].setValue(getattr(mask, name) * factor)
        finally:
            self._updating = previous
        self._refresh_mask_widgets()

    def _refresh_mask_widgets(self) -> None:
        has_mask = self._current_mask() is not None
        self.mask_form_host.setVisible(has_mask)
        self.remove_mask.setEnabled(has_mask)

    # -- édition --------------------------------------------------------------------------------

    def _append_mask(self):
        mask = Mask(shape=MaskShape(self.mask_shape.currentData()))
        self._value = replace(self._value, masks=self._value.masks + (mask,))
        self._updating = True
        try:
            self._fill_mask_list(len(self._value.masks) - 1)
        finally:
            self._updating = False
        self._refresh_mask_widgets()
        self._emit()

    def _remove_mask(self):
        row = self.mask_list.currentRow()
        if not 0 <= row < len(self._value.masks):
            return
        masks = self._value.masks[:row] + self._value.masks[row + 1:]
        self._value = replace(self._value, masks=masks)
        self._updating = True
        try:
            self._fill_mask_list(row - 1)
        finally:
            self._updating = False
        self._refresh_mask_widgets()
        self._emit()

    def _mask_edited(self, *_):
        if self._updating:
            return
        mask = self._current_mask()
        if mask is None:
            return
        values = {
            name: self.mask_spins[name].value() / factor
            for name, _label, _low, _high, _step, factor in _MASK_FIELDS
        }
        updated = replace(
            mask, mode=MaskMode(self.mask_mode.currentData()), inverted=self.inverted.isChecked(),
            id=mask.id, **values,
        )
        row = self.mask_list.currentRow()
        masks = self._value.masks[:row] + (updated,) + self._value.masks[row + 1:]
        self._value = replace(self._value, masks=masks)
        item = self.mask_list.item(row)
        if item is not None:
            label = f"{updated.name or i18n.translate('mograph.compositing.mask_name', index=row + 1)} · {i18n.translate(dict(_SHAPES)[updated.shape])} · {i18n.translate(dict(_MODES)[updated.mode])}"
            item.setText(label + (i18n.translate("mograph.compositing.inverted_suffix") if updated.inverted else ""))
        self._emit()

    def _emit(self, *_):
        if self._updating:
            return
        self._value = Compositing(
            self._value.masks,
            ChromaKey(self.key_enabled.isChecked(), self.key_color.currentText(),
                      **{n: s.value() for n, s in self.spins.items()}),
            self.blend_mode.currentData(),
        )
        self.value_changed.emit(self._value)


__all__ = ["CompositingEditor"]
