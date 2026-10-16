"""ADR-0006 étape 4, lot 3 : ``CompositingEditor`` scindé en sous-blocs, sans effet visible ni changement d'interface."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QWidget

from core.clip_crop import Crop, with_crop
from core.compositing import ChromaKey, Compositing, Mask, MaskShape
from ui.compositing_editor import CompositingEditor
from ui.compositing_sections import (
    SECTION_BLEND, SECTION_CHROMA, SECTION_CROP, SECTION_IDS, SECTION_MASKS,
)

pytestmark = pytest.mark.usefixtures("qapp")

#: Interface publique lue par l'inspecteur, la page Composition et les tests : liste explicite, à ne pas réduire.
PUBLIC_ATTRIBUTES = (
    "crop_editor", "blend_mode", "mask_shape", "add_mask", "remove_mask", "mask_list", "mask_form_host", "edit_in_monitor",
    "path_hint", "mask_mode", "inverted", "mask_spins", "key_host", "key_enabled", "key_color", "spins",
    "value_changed", "mask_edit_changed", "set_chroma_visible", "set_editable", "set_value", "edited_mask_id", "reset_edit",
)


def _editor() -> CompositingEditor:
    editor = CompositingEditor("")
    editor.show()
    return editor


def _record(editor: CompositingEditor) -> list:
    log: list = []
    editor.value_changed.connect(lambda value: log.append(("value", value)))
    editor.mask_edit_changed.connect(lambda mask_id: log.append(("edit", mask_id)))
    return log


def test_public_attributes_are_all_there() -> None:
    editor = CompositingEditor("")
    assert [name for name in PUBLIC_ATTRIBUTES if not hasattr(editor, name)] == []
    assert editor.objectName() == "compositing_group"
    assert editor.title()


def test_group_holds_the_four_sections_in_order() -> None:
    editor = CompositingEditor("")
    layout = editor.layout()
    children = [layout.itemAt(i).widget() for i in range(layout.count())]
    children = [getattr(child, "child", None) or child for child in children]      # fusion, masques et chroma sont dans un conteneur de niveau d'usage
    assert [child.objectName() for child in children] == [SECTION_BLEND, SECTION_CROP, SECTION_MASKS, SECTION_CHROMA]
    assert tuple(child.objectName() for child in children) == SECTION_IDS
    # chaque contrôle est bien dans le sous-bloc attendu (pas rattaché au groupe directement)
    owners = {
        SECTION_BLEND: [editor.blend_mode], SECTION_CROP: [editor.crop_editor],
        SECTION_MASKS: [editor.mask_shape, editor.add_mask, editor.remove_mask, editor.mask_list, editor.mask_form_host],
        SECTION_CHROMA: [editor.key_host, editor.key_enabled, editor.key_color],
    }
    for section, widgets in owners.items():
        parent = editor.findChild(QWidget, section)
        assert all(parent.isAncestorOf(widget) for widget in widgets), section


def test_hiding_one_section_leaves_the_others_and_the_values_alone() -> None:
    editor = _editor()
    mask = Mask(shape=MaskShape.ELLIPSE, feather=0.1)
    value = Compositing((mask,), ChromaKey(True, "#00FF00", tolerance=0.3), "multiply")
    crop = Crop(left=0.1, top=0.2, right=0.0, bottom=0.0)
    editor.set_value(with_crop(value, crop))
    assert editor.crop_editor.current_crop() == crop
    log = _record(editor)
    for hidden in SECTION_IDS:
        section = editor.findChild(QWidget, hidden)
        section.setVisible(False)
        for other in SECTION_IDS:
            assert editor.findChild(QWidget, other).isVisible() == (other != hidden)
        section.setVisible(True)
    assert log == []
    assert editor.mask_list.count() == 2        # le masque et le recadrage
    assert editor.key_enabled.isChecked()
    assert editor.spins["tolerance"].value() == pytest.approx(0.3)
    assert editor.blend_mode.currentData() == "multiply"
    assert editor.crop_editor.current_crop() == crop
    assert editor.mask_spins["feather"].value() == pytest.approx(10.0)


def test_editable_lock_still_disables_every_section() -> None:
    editor = _editor()
    editor.set_editable(False)
    assert not editor.isEnabled()
    for widget in (*(editor.findChild(QWidget, s) for s in SECTION_IDS), editor.blend_mode, editor.crop_editor,
                   editor.add_mask, editor.mask_list, editor.key_enabled, editor.key_host):
        assert not widget.isEnabled()
    editor.set_editable(True)
    assert editor.isEnabled() and all(editor.findChild(QWidget, s).isEnabled() for s in SECTION_IDS)


def test_chroma_visibility_is_independent_of_the_section() -> None:
    editor = _editor()
    editor.set_chroma_visible(False)
    assert not editor.key_host.isVisible()
    assert editor.findChild(QWidget, SECTION_CHROMA).isVisible()
    editor.set_chroma_visible(True)
    assert editor.key_host.isVisible()


def test_blend_gesture_emits_once() -> None:
    editor = _editor()
    log = _record(editor)
    editor.blend_mode.setCurrentIndex(editor.blend_mode.findData("multiply"))
    assert [kind for kind, _ in log] == ["value"]
    assert log[0][1].blend_mode.value == "multiply"


def test_add_and_remove_mask_emissions() -> None:
    editor = _editor()
    log = _record(editor)
    editor.add_mask.click()
    kinds = [kind for kind, _ in log]
    assert kinds == ["value", "edit"]          # valeur d'abord, puis les poignées du masque ajouté dans le moniteur
    assert len(log[0][1].masks) == 1 and log[1][1] == log[0][1].masks[0].id
    log.clear()
    editor.mask_spins["feather"].setValue(5.0)
    assert [kind for kind, _ in log] == ["value"]
    log.clear()
    editor.remove_mask.click()
    assert [kind for kind, _ in log] == ["value", "edit"]
    assert log[0][1].masks == () and log[1][1] == ""


def test_chroma_key_gestures_emit_once_each() -> None:
    editor = _editor()
    log = _record(editor)
    editor.key_enabled.setChecked(True)
    editor.spins["softness"].setValue(0.5)
    editor.key_color.setCurrentText("#0000FF")
    assert [kind for kind, _ in log] == ["value", "value", "value"]
    key = log[-1][1].chroma_key
    assert key.enabled and key.color == "#0000FF" and key.softness == pytest.approx(0.5)


def test_crop_gesture_emits_value_and_set_value_is_silent() -> None:
    editor = _editor()
    log = _record(editor)
    editor.set_value(Compositing())
    assert log == []
    editor.crop_editor.crop_changed.emit(Crop(left=0.1, top=0.1, right=0.1, bottom=0.1))
    assert [kind for kind, _ in log] == ["value"]
    assert len(log[0][1].masks) == 1
