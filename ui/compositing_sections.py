"""Sous-blocs de l'éditeur de compositing : fusion, recadrage, pile de masques, clé chroma.

``CompositingEditor`` (``ui/compositing_editor.py``) garde la logique (valeur, signaux, édition) ; chaque sous-bloc ne
fait que construire ses contrôles dans un widget enfant identifié (``SECTION_*``, nom d'objet identique), pour pouvoir
être masqué ou affiché seul sans toucher aux autres ni à la valeur.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.blend_modes import BLEND_MODES, blend_label_key
from core.compositing import MIN_CHROMA_TOLERANCE, MaskMode, MaskShape
from core.usage_level import UsageLevel
from ui import i18n
from ui.adaptive_layout import FlowLayout
from ui.design_system import Spacing
from ui.properties_widgets.crop_editor import CropEditor
from ui.properties_widgets.level_gate import LevelGate, gated

SECTION_BLEND = "compositing_section_blend"
SECTION_CROP = "compositing_section_crop"
SECTION_MASKS = "compositing_section_masks"
SECTION_CHROMA = "compositing_section_chroma"
#: Dans l'ordre d'affichage.
SECTION_IDS = (SECTION_BLEND, SECTION_CROP, SECTION_MASKS, SECTION_CHROMA)

# (valeur, clé i18n du libellé) : traduit à l'affichage, jamais à l'import.
MASK_SHAPES = (
    (MaskShape.RECTANGLE, "mograph.shape.rectangle"),
    (MaskShape.ELLIPSE, "mograph.compositing.shape_ellipse"),
    (MaskShape.POLYGON, "mograph.shape.polygon"),
    (MaskShape.PATH, "mask.shape.path"),
)
MASK_MODES = (
    (MaskMode.ADD, "common.add"),
    (MaskMode.SUBTRACT, "mograph.compositing.mode_subtract"),
    (MaskMode.INTERSECT, "mograph.compositing.mode_intersect"),
)

MASK_FIELDS = (
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


class _Section(QWidget):
    """Sous-bloc sans marge ni titre : l'espacement vertical est celui du groupe parent."""

    section_id = ""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName(self.section_id)
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(Spacing.xs)


class BlendSection(_Section):
    section_id = SECTION_BLEND

    def __init__(self) -> None:
        super().__init__()
        form = self.blend_form = QFormLayout()     # lu par la page Composition pour masquer l'étiquette « Fusion »
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.body.addLayout(form)
        self.blend_mode = QComboBox(objectName="blend_mode")
        for mode in BLEND_MODES:
            self.blend_mode.addItem(i18n.translate(blend_label_key(mode)), mode.value)
        form.addRow(i18n.translate("mograph.compositing.blend"), self.blend_mode)


class CropSection(_Section):
    """Recadrage : un masque rectangle généré (core/clip_crop.py), visible dans la pile de masques."""

    section_id = SECTION_CROP

    def __init__(self) -> None:
        super().__init__()
        self.crop_editor = CropEditor()
        self.body.addWidget(self.crop_editor)


class MaskSection(_Section):
    section_id = SECTION_MASKS

    def __init__(self) -> None:
        super().__init__()
        root = self.body
        add_row = FlowLayout(spacing=Spacing.xs)  # trois contrôles : à la ligne plutôt qu'au-delà de l'inspecteur
        self.mask_shape = QComboBox(objectName="mask_shape")
        for shape, label in MASK_SHAPES:
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
        self.edit_in_monitor = QPushButton(i18n.translate("mask.edit.toggle"), objectName="mask_edit_in_monitor")
        self.edit_in_monitor.setCheckable(True)
        self.edit_in_monitor.setToolTip(i18n.translate("mask.edit.toggle_tip"))
        # Niveau d'usage : l'édition dans le moniteur, l'expansion et la rotation sont avancées (conteneurs créés ici, à la construction).
        self.level_gates: list[LevelGate] = []
        advanced = UsageLevel.ADVANCED
        self.level_gates.append(gated(self.edit_in_monitor, advanced))
        mask_form.addRow(self.level_gates[-1])
        self.path_hint = QLabel(i18n.translate("mask.path.hint"), objectName="mask_path_hint")    # courbe : les gestes du moniteur
        self.path_hint.setWordWrap(True)
        self.level_gates.append(gated(self.path_hint, advanced))
        mask_form.addRow(self.level_gates[-1])
        self.mask_mode = QComboBox(objectName="mask_mode")
        for mode, label in MASK_MODES:
            self.mask_mode.addItem(i18n.translate(label), mode.value)
        mask_form.addRow(i18n.translate("mograph.compositing.operation"), self.mask_mode)
        self.inverted = QCheckBox(i18n.translate("mograph.compositing.invert"), objectName="mask_inverted")
        mask_form.addRow(self.inverted)
        self.mask_spins: dict[str, QDoubleSpinBox] = {}
        for name, label, low, high, step, _factor in MASK_FIELDS:
            spin = QDoubleSpinBox(objectName=f"mask_{name}")
            spin.setRange(low, high)
            spin.setSingleStep(step)
            spin.setDecimals(1)
            spin.setSuffix(" °" if name == "rotation" else " %")
            if name in ("rotation", "expansion"):
                self.level_gates.append(gated(spin, advanced, mask_form))
                mask_form.addRow(i18n.translate(label), self.level_gates[-1])
            else:
                mask_form.addRow(i18n.translate(label), spin)
            self.mask_spins[name] = spin
        root.addWidget(self.mask_form_host)


class ChromaSection(_Section):
    """Clé chroma (vidéo) : ``key_host`` reste le conteneur que ``set_chroma_visible`` masque selon le clip."""

    section_id = SECTION_CHROMA

    def __init__(self) -> None:
        super().__init__()
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
        self.spins: dict[str, QDoubleSpinBox] = {}
        for key, label, lo, hi, value in (
            ("tolerance", i18n.translate("mograph.compositing.tolerance"), MIN_CHROMA_TOLERANCE, 1, .1), ("softness", i18n.translate("mograph.compositing.softness"), 0, 1, .05),
            ("spill_suppression", i18n.translate("mograph.compositing.spill"), 0, 1, 0),
        ):
            spin = QDoubleSpinBox(objectName=f"chroma_{key}")
            spin.setRange(lo, hi)
            spin.setSingleStep(.01)
            spin.setValue(value)
            key_form.addRow(label, spin)
            self.spins[key] = spin
        self.body.addWidget(self.key_host)


__all__ = [
    "SECTION_BLEND", "SECTION_CHROMA", "SECTION_CROP", "SECTION_IDS", "SECTION_MASKS",
    "BlendSection", "ChromaSection", "CropSection", "MaskSection",
    "MASK_FIELDS", "MASK_MODES", "MASK_SHAPES",
]
