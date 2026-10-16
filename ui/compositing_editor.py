"""Éditeur de compositing de l'inspecteur : fusion, masques, incrustation.

- Mode de fusion : la liste centralisée (:mod:`core.blend_modes`).
- Masques : une pile ; chaque masque a sa forme (rectangle, ellipse,
  polygone, courbe de Bézier), son opération (Ajouter / Soustraire / Intersection), son
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
from PySide6.QtWidgets import QGroupBox, QListWidgetItem, QVBoxLayout, QWidget

from core.clip_crop import CROP_MASK_ID, crop_of_compositing, is_custom_crop, keep_crop_last, with_crop
from core.compositing import ChromaKey, Compositing, Mask, MaskMode, MaskShape
from ui import i18n
from ui.compositing_sections import (
    MASK_FIELDS,
    MASK_MODES,
    MASK_SHAPES,
    BlendSection,
    ChromaSection,
    CropSection,
    MaskSection,
)
from ui.design_system import Spacing


class CompositingEditor(QGroupBox):
    value_changed = Signal(object)
    mask_edit_changed = Signal(str)     # masque dont le moniteur montre les poignées (« » : aucun, poignées du clip)

    def __init__(self, group_style: str, parent=None):
        super().__init__(i18n.translate("inspector.tab.compositing"), parent)
        self.setObjectName("compositing_group")
        self.setStyleSheet(group_style)
        self._value = Compositing()
        self._updating = False
        self._crop_animated = False
        self._crop_editable = True
        self._edit_id = ""
        root = QVBoxLayout(self)
        root.setContentsMargins(Spacing.md, Spacing.md, Spacing.md, Spacing.md)
        root.setSpacing(Spacing.xs)
        self.blend_section = BlendSection()
        self.crop_section = CropSection()
        self.mask_section = MaskSection()
        self.chroma_section = ChromaSection()
        for section in self.sections():
            root.addWidget(section)
        # Les sous-blocs construisent les contrôles ; l'éditeur les expose sous leurs noms historiques.
        self.blend_mode = self.blend_section.blend_mode
        self.crop_editor = self.crop_section.crop_editor
        self.crop_editor.crop_changed.connect(self._crop_edited)
        self.mask_shape = self.mask_section.mask_shape
        self.add_mask = self.mask_section.add_mask
        self.remove_mask = self.mask_section.remove_mask
        self.mask_list = self.mask_section.mask_list
        self.mask_form_host = self.mask_section.mask_form_host
        self.edit_in_monitor = self.mask_section.edit_in_monitor
        self.path_hint = self.mask_section.path_hint
        self.mask_mode = self.mask_section.mask_mode
        self.inverted = self.mask_section.inverted
        self.mask_spins = self.mask_section.mask_spins
        self.key_host = self.chroma_section.key_host
        self.key_enabled = self.chroma_section.key_enabled
        self.key_color = self.chroma_section.key_color
        self.spins = self.chroma_section.spins

        self.blend_mode.currentIndexChanged.connect(self._emit)
        self.add_mask.clicked.connect(self._append_mask)
        self.remove_mask.clicked.connect(self._remove_mask)
        self.mask_list.currentRowChanged.connect(self._row_changed)
        self.edit_in_monitor.toggled.connect(self._edit_toggled)
        self.mask_mode.currentIndexChanged.connect(self._mask_edited)
        self.inverted.toggled.connect(self._mask_edited)
        for spin in self.mask_spins.values():
            spin.valueChanged.connect(self._mask_edited)
        self.key_enabled.toggled.connect(self._emit)
        self.key_color.currentTextChanged.connect(self._emit)
        for spin in self.spins.values():
            spin.valueChanged.connect(self._emit)
        self._refresh_mask_widgets()

    def sections(self) -> tuple[QWidget, ...]:
        """Les sous-blocs, dans l'ordre d'affichage (fusion, recadrage, pile de masques, clé chroma)."""
        return (self.blend_section, self.crop_section, self.mask_section, self.chroma_section)

    # -- chargement -----------------------------------------------------------------------------

    def set_chroma_visible(self, visible: bool) -> None:
        self.key_host.setVisible(bool(visible))

    def set_editable(self, editable: bool = True) -> None:
        """Piste ou calque verrouillé, clip non visuel : la pile reste lisible mais rien ne se modifie."""
        self.setEnabled(bool(editable))

    def set_value(self, value, crop_animated: bool = False, crop_editable: bool = True):
        self._crop_animated = bool(crop_animated)
        self._crop_editable = bool(crop_editable)
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
        self._sync_crop_editor()
        self._refresh_mask_widgets()
        gone = bool(self._edit_id) and self._value.mask_by_id(self._edit_id) is None
        self._set_edit("" if gone else self._edit_id)     # un autre clip, ou le masque n'existe plus : retour aux poignées du clip

    # -- poignées dans le moniteur ----------------------------------------------------------------

    def edited_mask_id(self) -> str:
        """Identifiant du masque dont le moniteur montre les poignées (``""`` : aucun)."""
        return self._edit_id

    def _set_edit(self, mask_id: str) -> None:
        if mask_id != self._edit_id:
            self._edit_id = mask_id
            self.mask_edit_changed.emit(mask_id)
        self.edit_in_monitor.blockSignals(True)
        self.edit_in_monitor.setChecked(bool(self._edit_id) and self._current_mask_id() == self._edit_id)
        self.edit_in_monitor.blockSignals(False)

    def reset_edit(self) -> None:
        """Sort du mode « poignées dans le moniteur » sans signal (la fenêtre principale l'a déjà quitté)."""
        self._edit_id = ""
        self.edit_in_monitor.blockSignals(True)
        self.edit_in_monitor.setChecked(False)
        self.edit_in_monitor.blockSignals(False)

    def _current_mask_id(self) -> str:
        mask = self._current_mask()
        return mask.id if mask is not None else ""

    def _row_changed(self, row: int) -> None:
        self._load_mask(row)
        if not self._updating:
            self._set_edit(self._current_mask_id())     # choisir un masque dans la pile : ses poignées dans le moniteur

    def _edit_toggled(self, checked: bool) -> None:
        self._set_edit(self._current_mask_id() if checked else "")

    def _sync_crop_editor(self) -> None:
        crop = crop_of_compositing(self._value)
        self.crop_editor.set_crop(crop, animated=self._crop_animated and crop is not None, custom=is_custom_crop(self._value),
                                  locked=not self._crop_editable)

    def _mask_label(self, mask: Mask, index: int) -> str:
        name = i18n.translate("crop.mask_name") if mask.id == CROP_MASK_ID else mask.name
        label = f"{name or i18n.translate('mograph.compositing.mask_name', index=index)} · {i18n.translate(dict(MASK_SHAPES)[mask.shape])} · {i18n.translate(dict(MASK_MODES)[mask.mode])}"
        return label + (i18n.translate("mograph.compositing.inverted_suffix") if mask.inverted else "")

    def _fill_mask_list(self, row: int) -> None:
        self.mask_list.blockSignals(True)
        self.mask_list.clear()
        for index, mask in enumerate(self._value.masks, start=1):
            QListWidgetItem(self._mask_label(mask, index), self.mask_list)
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
                for name, _label, _low, _high, _step, factor in MASK_FIELDS:
                    self.mask_spins[name].setValue(getattr(mask, name) * factor)
        finally:
            self._updating = previous
        self._refresh_mask_widgets()

    def _refresh_mask_widgets(self) -> None:
        current = self._current_mask()
        has_mask = current is not None
        self.path_hint.setVisible(has_mask and current.shape is MaskShape.PATH)
        self.mask_form_host.setVisible(has_mask)
        self.remove_mask.setEnabled(has_mask)

    # -- édition --------------------------------------------------------------------------------

    def _append_mask(self):
        mask = Mask(shape=MaskShape(self.mask_shape.currentData()))
        # Le recadrage reste en dernier : un masque « add » placé après lui s'y ajouterait au lieu d'être restreint.
        self._value = replace(self._value, masks=keep_crop_last(self._value.masks + (mask,)))
        self._updating = True
        try:
            self._fill_mask_list(self._value.masks.index(mask))
        finally:
            self._updating = False
        self._refresh_mask_widgets()
        self._emit()
        self._set_edit(mask.id)                 # le masque qu'on vient d'ajouter se règle dans le moniteur

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
        self._sync_crop_editor()
        self._refresh_mask_widgets()
        self._emit()
        if self._edit_id and self._value.mask_by_id(self._edit_id) is None:
            self._set_edit("")

    def _crop_edited(self, crop) -> None:
        if self._updating:
            return
        self._value = with_crop(self._value, crop)
        self._updating = True
        try:
            self._fill_mask_list(self.mask_list.currentRow())
        finally:
            self._updating = False
        self._refresh_mask_widgets()
        self._emit()
        if self._edit_id and self._value.mask_by_id(self._edit_id) is None:
            self._set_edit("")

    def _mask_edited(self, *_):
        if self._updating:
            return
        mask = self._current_mask()
        if mask is None:
            return
        values = {
            name: self.mask_spins[name].value() / factor
            for name, _label, _low, _high, _step, factor in MASK_FIELDS
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
            item.setText(self._mask_label(updated, row + 1))
        if updated.id == CROP_MASK_ID:
            self._sync_crop_editor()
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
