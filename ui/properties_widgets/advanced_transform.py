"""Section « Transformation avancée » du groupe Mouvement de l'inspecteur.

Repliée par défaut : position, échelle, rotation et opacité suffisent à la
plupart des montages. Ouverte, elle expose le point d'ancrage, l'échelle
X/Y, l'inclinaison (calques graphiques) et les miroirs. Chaque ligne a un
losange : ajouter / retirer une image-clé à la tête de lecture ; une
valeur modifiée sur une propriété animée crée ou met à jour l'image-clé
(même règle que les propriétés de base).
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QGridLayout,
    QLabel,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ui.adaptive_layout import allow_shrinking
from ui.design_system import Spacing
from ui.properties_widgets.diamond_button import KeyframeDiamondButton
from ui.theme import label_style
from ui.i18n import translate

# (propriété, clé i18n du libellé, min, max, pas, facteur d'affichage, suffixe)
NUMERIC_FIELDS = (
    ("anchor_x", "animation.property.anchor_x", -400.0, 500.0, 1.0, 100.0, " %"),
    ("anchor_y", "animation.property.anchor_y", -400.0, 500.0, 1.0, 100.0, " %"),
    ("scale_x", "animation.property.scale_x", 0.0, 1000.0, 1.0, 100.0, " %"),
    ("scale_y", "animation.property.scale_y", 0.0, 1000.0, 1.0, 100.0, " %"),
    ("skew", "animation.property.skew", -85.0, 85.0, 1.0, 1.0, " °"),
)
BOOL_FIELDS = (("flip_h", "animation.property.flip_h"), ("flip_v", "animation.property.flip_v"))


class AdvancedTransformEditor(QWidget):
    value_changed = Signal(str, object)
    keyframe_toggled = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._updating = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, Spacing.xs, 0, 0)
        layout.setSpacing(Spacing.xs)
        self.toggle = QToolButton()
        self.toggle.setText(translate("mograph.advanced.title"))
        self.toggle.setCheckable(True)
        self.toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.toggle.setArrowType(Qt.RightArrow)
        self.toggle.setStyleSheet("QToolButton { border: none; font-weight: 700; }")
        self.toggle.toggled.connect(self._on_toggled)
        allow_shrinking(self.toggle, 120)  # « Transformation avancée » fixait la largeur minimale du groupe Mouvement
        layout.addWidget(self.toggle)
        self.body = QWidget()
        grid = QGridLayout(self.body)
        grid.setContentsMargins(Spacing.sm, 0, 0, 0)
        grid.setSpacing(Spacing.xs)
        self.spins: dict[str, QDoubleSpinBox] = {}
        self.checks: dict[str, QCheckBox] = {}
        self.diamonds: dict[str, QToolButton] = {}
        self._labels: dict[str, QLabel] = {}
        row = 0
        for name, label, low, high, step, factor, suffix in NUMERIC_FIELDS:
            caption = QLabel(translate(label))
            caption.setStyleSheet(label_style(11, "muted", 600))
            spin = QDoubleSpinBox(objectName=f"advanced_{name}")
            spin.setRange(low, high)
            spin.setSingleStep(step)
            spin.setDecimals(1)
            spin.setSuffix(suffix)
            spin.valueChanged.connect(
                lambda value, prop=name, f=factor: self._emit(prop, value / f)
            )
            grid.addWidget(caption, row, 0)
            grid.addWidget(spin, row, 1)
            grid.addWidget(self._diamond(name), row, 2)
            self.spins[name] = spin
            self._labels[name] = caption
            row += 1
        for name, label in BOOL_FIELDS:
            check = QCheckBox(translate(label), objectName=f"advanced_{name}")
            check.toggled.connect(lambda checked, prop=name: self._emit(prop, bool(checked)))
            grid.addWidget(check, row, 0, 1, 2)
            grid.addWidget(self._diamond(name), row, 2)
            self.checks[name] = check
            row += 1
        self._factors = {name: factor for name, _l, _a, _b, _s, factor, _x in NUMERIC_FIELDS}
        self.body.setVisible(False)
        layout.addWidget(self.body)

    def _diamond(self, name: str) -> QToolButton:
        button = KeyframeDiamondButton(name)       # le même losange que dans tout l'inspecteur (plein, contour appuyé, contour discret)
        button.setToolTip(translate("mograph.advanced.keyframe_tooltip"))
        button.clicked.connect(lambda _checked=False, prop=name: self.keyframe_toggled.emit(prop))
        self.diamonds[name] = button
        return button

    def _on_toggled(self, checked: bool) -> None:
        self.toggle.setArrowType(Qt.DownArrow if checked else Qt.RightArrow)
        self.body.setVisible(checked)

    def _emit(self, name: str, value) -> None:
        if not self._updating:
            self.value_changed.emit(name, value)

    def set_skew_available(self, available: bool) -> None:
        for widget in (self.spins["skew"], self._labels["skew"], self.diamonds["skew"]):
            widget.setVisible(bool(available))

    def set_values(self, values: dict, *, animated=(), keyed=()) -> None:
        """Valeurs à la tête de lecture ; ``animated`` / ``keyed`` : losanges."""
        self._updating = True
        try:
            for name, spin in self.spins.items():
                if name in values:
                    spin.setValue(float(values[name]) * self._factors[name])
            for name, check in self.checks.items():
                if name in values:
                    check.setChecked(bool(values[name]))
            for name, diamond in self.diamonds.items():
                diamond.setChecked(name in keyed)
                diamond.set_animated(name in animated)
        finally:
            self._updating = False


__all__ = ["AdvancedTransformEditor"]
