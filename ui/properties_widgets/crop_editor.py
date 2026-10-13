"""Recadrage non destructif de l'inspecteur : quatre bords en pourcentage et un bouton de réinitialisation.

Le widget n'agit pas sur le projet : il émet ``crop_changed(Crop | None)`` (``None`` = plus de recadrage) et
:class:`~ui.compositing_editor.CompositingEditor` le traduit en pile de masques (le recadrage est un masque, voir
:mod:`core.clip_crop`). Il passe donc par le chemin des autres champs de masque : aperçu en direct, puis une seule étape
d'historique pour une rafale de modifications.

Les maxima des champs suivent le modèle : ``gauche + droite`` et ``haut + bas`` laissent au moins
:data:`core.clip_crop.MIN_VISIBLE` du cadre ; aucune valeur n'est ramenée en silence.
"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QDoubleSpinBox, QFormLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from core.clip_crop import MAX_CROP_SIDE, MIN_VISIBLE, Crop, CropError
from ui import i18n
from ui.design_system import Spacing

_SIDES = ("left", "top", "right", "bottom")
_OPPOSITE = {"left": "right", "right": "left", "top": "bottom", "bottom": "top"}
_PERCENT = 100.0


class CropEditor(QWidget):
    crop_changed = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("crop_editor")
        self._updating = False
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(Spacing.xs)
        title = QLabel(i18n.translate("crop.title"), objectName="crop_title")
        root.addWidget(title)
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(Spacing.xs)
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        root.addLayout(form)
        self.spins: dict[str, QDoubleSpinBox] = {}
        for side in _SIDES:
            spin = QDoubleSpinBox(objectName=f"crop_{side}")
            spin.setRange(0.0, MAX_CROP_SIDE * _PERCENT)
            spin.setSingleStep(1.0)
            spin.setDecimals(1)
            spin.setSuffix(" %")
            spin.setToolTip(i18n.translate(f"crop.tooltip.{side}"))
            spin.setAccessibleName(i18n.translate(f"crop.{side}"))
            spin.valueChanged.connect(self._edited)
            form.addRow(i18n.translate(f"crop.{side}"), spin)
            self.spins[side] = spin
        self.hint = QLabel("", objectName="crop_hint")
        self.hint.setWordWrap(True)
        self.hint.setVisible(False)
        root.addWidget(self.hint)
        self.reset_button = QPushButton(i18n.translate("crop.reset"), objectName="crop_reset")
        self.reset_button.setToolTip(i18n.translate("crop.reset_tooltip"))
        self.reset_button.setAccessibleName(i18n.translate("crop.reset"))
        self.reset_button.clicked.connect(self._reset)
        root.addWidget(self.reset_button)
        self.set_crop(None)

    # -- chargement -----------------------------------------------------------------------------

    def set_crop(self, crop: Crop | None, *, animated: bool = False, custom: bool = False) -> None:
        """Affiche la valeur courante. ``animated`` / ``custom`` : le masque existe mais ces champs ne le pilotent pas."""
        self._updating = True
        try:
            for side, spin in self.spins.items():
                spin.setValue(round(getattr(crop, side, 0.0) * _PERCENT, 1))
            self._refresh_limits()
        finally:
            self._updating = False
        editable = not (animated or custom)
        for spin in self.spins.values():
            spin.setEnabled(editable)
        self.hint.setText(i18n.translate("crop.animated" if animated else "crop.custom") if not editable else "")
        self.hint.setVisible(not editable)
        self.reset_button.setEnabled(crop is not None or animated or custom)

    def current_crop(self) -> Crop | None:
        values = {side: round(spin.value() / _PERCENT, 6) for side, spin in self.spins.items()}
        try:
            crop = Crop(**values)
        except CropError:
            return None
        return None if crop.is_empty else crop

    # -- édition --------------------------------------------------------------------------------

    def _refresh_limits(self) -> None:
        """Le maximum d'un bord laisse au moins ``MIN_VISIBLE`` de largeur / hauteur avec le bord opposé."""
        for side, spin in self.spins.items():
            other = self.spins[_OPPOSITE[side]].value()
            spin.setMaximum(max(0.0, min(MAX_CROP_SIDE * _PERCENT, (1.0 - MIN_VISIBLE) * _PERCENT - other)))

    def _edited(self, *_):
        if self._updating:
            return
        self._updating = True
        try:
            self._refresh_limits()
        finally:
            self._updating = False
        crop = self.current_crop()
        self.reset_button.setEnabled(crop is not None)
        self.crop_changed.emit(crop)

    def _reset(self) -> None:
        self.set_crop(None)
        self.crop_changed.emit(None)


__all__ = ["CropEditor"]
