"""Éditeur compact des masques et de l'incrustation de l'inspecteur."""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox, QPushButton

from core.compositing import BlendMode, ChromaKey, Compositing, Mask, MaskShape


class CompositingEditor(QGroupBox):
    value_changed = Signal(object)

    def __init__(self, group_style: str, parent=None):
        super().__init__("Compositing", parent)
        self.setObjectName("compositing_group")
        self.setStyleSheet(group_style)
        self._value = Compositing(); self._updating = False
        form = QFormLayout(self)
        self.blend_mode = QComboBox(objectName="blend_mode")
        for mode, label in ((BlendMode.NORMAL,"Normal"),(BlendMode.MULTIPLY,"Produit"),(BlendMode.SCREEN,"Écran"),(BlendMode.OVERLAY,"Superposition"),(BlendMode.ADD,"Addition")):
            self.blend_mode.addItem(label, mode.value)
        form.addRow("Mode de fusion", self.blend_mode)
        self.mask_shape = QComboBox(objectName="mask_shape"); self.mask_shape.addItem("Rectangle", "rectangle"); self.mask_shape.addItem("Ellipse", "ellipse")
        self.add_mask = QPushButton("Ajouter un masque", objectName="add_mask")
        form.addRow("Forme", self.mask_shape); form.addRow(self.add_mask)
        self.inverted = QCheckBox("Inverser", objectName="mask_inverted"); form.addRow(self.inverted)
        self.key_enabled = QCheckBox("Chroma Key", objectName="chroma_key_enabled"); form.addRow(self.key_enabled)
        self.key_color = QComboBox(objectName="chroma_key_color"); self.key_color.setEditable(True); self.key_color.addItems(["#00FF00", "#0000FF"]); form.addRow("Couleur", self.key_color)
        self.spins = {}
        for key, label, lo, hi, value in (("tolerance","Tolérance",0,1,.1),("softness","Douceur",0,1,.05),("spill_suppression","Anti-débordement vert",0,1,0)):
            spin=QDoubleSpinBox(objectName=f"chroma_{key}"); spin.setRange(lo,hi); spin.setSingleStep(.01); spin.setValue(value); form.addRow(label,spin); self.spins[key]=spin
        self.blend_mode.currentIndexChanged.connect(self._emit); self.add_mask.clicked.connect(self._append_mask)
        self.inverted.toggled.connect(self._invert); self.key_enabled.toggled.connect(self._emit)
        self.key_color.currentTextChanged.connect(self._emit)
        for spin in self.spins.values(): spin.valueChanged.connect(self._emit)

    def set_value(self, value):
        self._updating=True; self._value=value if isinstance(value,Compositing) else Compositing()
        self.blend_mode.setCurrentIndex(max(0,self.blend_mode.findData(self._value.blend_mode.value)))
        k=self._value.chroma_key; self.key_enabled.setChecked(k.enabled); self.key_color.setCurrentText(k.color)
        for name, spin in self.spins.items(): spin.setValue(getattr(k,name))
        self.inverted.setChecked(bool(self._value.masks and self._value.masks[-1].inverted)); self._updating=False

    def _append_mask(self):
        self._value=Compositing(self._value.masks+(Mask(shape=MaskShape(self.mask_shape.currentData())),),self._value.chroma_key,self._value.blend_mode); self._emit()
    def _invert(self, checked):
        if self._value.masks:
            from dataclasses import replace
            self._value=Compositing(self._value.masks[:-1]+(replace(self._value.masks[-1],inverted=checked),),self._value.chroma_key,self._value.blend_mode)
        self._emit()
    def _emit(self, *_):
        if self._updating:return
        self._value=Compositing(self._value.masks,ChromaKey(self.key_enabled.isChecked(),self.key_color.currentText(),**{n:s.value() for n,s in self.spins.items()}),BlendMode(self.blend_mode.currentData()))
        self.value_changed.emit(self._value)
