"""Interface motion graphics : panneau Calques, viewer interactif, inspecteur, parcours débutant."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPointF, QRectF, Qt

from core.canvas_guides import GuideOrientation, safe_area_rects, snap_box
from core.compositing import Compositing, Mask, MaskMode, mask_property_id
from core.mograph_layers import layer_clips, layer_tree
from core.project_model import Project


@pytest.fixture
def window(qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from ui.main_window import MainWindow

    monkeypatch.setattr("ui.main_window.QMessageBox.information", lambda *_a, **_k: None)
    monkeypatch.setattr("ui.main_window.QMessageBox.question", lambda *_a, **_k: QMessageBox.Yes)
    win = MainWindow()
    qtbot.addWidget(win)
    win.timeline_timer.stop()
    monkeypatch.setattr(win.preview_panel, "preview_at", lambda *_a, **_k: None)
    win.project = Project(name="UI", width=1280, height=720, fps=25.0)
    win._timeline_index = None
    win._reload_timeline_preserving_selection()
    win.history.reset(win.project)
    win.resize(1400, 900)
    win.show()
    return win


def _layers(win):
    return [clip for _i, _t, clip in layer_clips(win.project)]


def test_beginner_flow_add_move_style_animate_and_shadow(window):
    # 1. Ajouter un texte.
    window.add_layer_at_playhead("text", "")
    (text,) = _layers(window)
    assert window.properties_panel.graphics_group.isEnabled()
    assert window.preview_panel.overlay.selection.clip_id == text.id
    # 2. Le déplacer directement dans le viewer (un seul geste = une entrée d'historique).
    entries = len(window.history)
    for step in range(1, 6):
        window.preview_panel.overlay.transform_dragged.emit(text.id, {"position_x": 0.02 * step})
    window.preview_panel.overlay.transform_released.emit(text.id, "Déplacer le calque")
    assert text.transform.position_x == pytest.approx(0.1)
    assert len(window.history) == entries + 1
    # 3. Taille et couleur depuis l'inspecteur.
    editor = window.properties_panel.graphics_group
    editor.font_size_spin.setValue(90)
    editor.fill_field.changed.emit("#FFCC00")
    window._finalize_graphic_history()
    assert text.graphic.font_size == 90 and text.graphic.fill_color == "#FFCC00"
    # 4. Une petite animation : image-clé d'opacité au début, puis à 1 s.
    window.on_transform_keyframe_added(text.id, "opacity", 0.0, 0.0)
    window.on_transform_keyframe_added(text.id, "opacity", 1.0, 1.0)
    assert len([kf for kf in text.transform_keyframes if kf.property_name == "opacity"]) == 2
    # 5. Une ombre douce.
    editor.shadow_blur_spin.setValue(6.0)
    window._finalize_graphic_history()
    assert text.graphic.shadow_blur == pytest.approx(6.0)
    window.history.undo()  # chaque étape est annulable


def test_layers_panel_lists_hierarchy_and_edits_with_undo(window):
    window.add_layer_at_playhead("shape", "ellipse")
    window.add_layer_at_playhead("text", "")
    shape, text = _layers(window)
    panel = window.layers_panel
    assert panel.tree.topLevelItemCount() == 2
    window.group_selected_layers([shape.id, text.id])
    group_id = text.graphic.group_id
    assert group_id and [n.depth for n in layer_tree(window.project)] == [0, 1, 1]
    assert panel.tree.topLevelItem(0).childCount() == 2
    entries = len(window.history)
    panel.visibility_toggled.emit(text.id, False)
    assert text.graphic.visible is False and len(window.history) == entries + 1
    panel.rename_requested.emit(shape.id, "Pastille")
    assert shape.label == "Pastille"
    panel.enter_group(group_id)
    assert panel.tree.topLevelItemCount() == 2 and panel.exit_group_button.isVisibleTo(panel)
    window._ungroup_layer(group_id)
    assert all(not clip.graphic.group_id for clip in _layers(window))


def test_parent_from_inspector_refuses_cycles(window):
    window.add_layer_at_playhead("null", "")
    window.add_layer_at_playhead("shape", "rectangle")
    controller, shape = _layers(window)
    window._on_layer_parent(shape.id, controller.id)
    assert shape.graphic.parent_id == controller.id
    window._on_layer_parent(controller.id, shape.id)  # cycle : refusé, rien ne change
    assert controller.graphic.parent_id == ""
    assert shape.id not in {cid for cid, _n in window._parent_choices_for(controller.id)}


def test_compositing_editor_drives_masks_and_blend(window):
    window.add_layer_at_playhead("shape", "rectangle")
    (shape,) = _layers(window)
    editor = window.properties_panel.compositing_group
    editor.add_mask.click()
    assert len(shape.compositing.masks) == 1
    editor.mask_mode.setCurrentIndex(editor.mask_mode.findData("subtract"))
    editor.mask_spins["feather"].setValue(20.0)
    editor.blend_mode.setCurrentIndex(editor.blend_mode.findData("multiply"))
    mask = shape.compositing.masks[0]
    assert mask.mode is MaskMode.SUBTRACT and mask.feather == pytest.approx(0.2)
    assert shape.compositing.blend_mode.value == "multiply"
    # La propriété animable du masque apparaît dans l'éditeur de courbes.
    assert mask_property_id(mask.id, "feather") in window._animation_properties(shape)
    editor.remove_mask.click()
    assert shape.compositing.masks == ()


def test_advanced_transform_inspector_sets_values_and_keyframes(window):
    window.add_layer_at_playhead("shape", "rectangle")
    (shape,) = _layers(window)
    advanced = window.properties_panel.advanced_transform
    advanced.spins["anchor_x"].setValue(0.0)
    advanced.checks["flip_v"].setChecked(True)
    assert shape.transform.anchor_x == 0.0 and shape.transform.flip_v is True
    advanced.keyframe_toggled.emit("skew")
    assert [kf.property_name for kf in shape.transform_keyframes] == ["skew"]
    window.history.undo()
    assert not window.history.current_project().active_sequence.tracks[0].clips[0].transform_keyframes


def test_guides_are_saved_but_never_reach_the_render_graph(window, tmp_path):
    from core.filter_graph import fingerprint_plan
    from core.render_plan import build_render_plan

    window.add_layer_at_playhead("text", "")
    before = fingerprint_plan(build_render_plan(window.project), width=1280, height=720, fps=25)
    window.add_viewer_guide("horizontal")
    window._set_viewer_flag("show_safe_areas", True)
    assert window.project.active_sequence.guides[0].orientation is GuideOrientation.HORIZONTAL
    assert fingerprint_plan(build_render_plan(window.project), width=1280, height=720, fps=25) == before
    window.clear_viewer_guides()
    assert window.project.active_sequence.guides == []


def test_overlay_drag_math_snaps_to_center_and_scales_about_the_anchor():
    from ui.viewer_overlay import SelectionGeometry, ViewerOverlay

    overlay = ViewerOverlay()
    overlay.set_canvas(QRectF(0, 0, 640, 360), (1280.0, 720.0))
    world = (1.0, 0.0, 0.0, 1.0, 100.0, 100.0)  # boîte 200×100 en (100, 100)
    overlay.selection = SelectionGeometry("c", world, (1, 0, 0, 1, 0, 0), (200.0, 100.0),
                                          {"position_x": 0.0, "position_y": 0.0, "scale": 1.0,
                                           "anchor_x": 0.5, "anchor_y": 0.5, "scale_x": 1.0})
    drag = {"mode": "move", "clip_id": "c", "start": (200.0, 150.0), "world": world,
            "parent_world": (1, 0, 0, 1, 0, 0), "box": (200.0, 100.0),
            "values": dict(overlay.selection.values)}
    # Déplacer le centre (200, 150) près du centre du cadre (640, 360) : aimantation exacte.
    values = overlay._drag_values(drag, (636.0, 357.0), Qt.NoModifier)
    assert values["position_x"] * 1280 == pytest.approx(440.0)
    assert values["position_y"] * 720 == pytest.approx(210.0)
    drag["mode"] = "br"
    drag["start"] = (300.0, 200.0)  # coin bas-droit ; l'ancrage (centre) est en (200, 150)
    values = overlay._drag_values(drag, (400.0, 250.0), Qt.NoModifier)  # distance ×2 depuis l'ancrage
    assert values["scale"] == pytest.approx(2.0)
    drag["mode"] = "r"
    values = overlay._drag_values(drag, (400.0, 150.0), Qt.NoModifier)  # côté : échelle X seule
    assert values == {"scale_x": pytest.approx(2.0)}


def test_snapping_and_safe_areas_are_pure_and_visual():
    dx, dy, lines = snap_box((95, 4, 195, 54), 1920, 1080, threshold=8, layer_boxes=[(200, 0, 300, 50)])
    assert (dx, dy) == pytest.approx((5.0, -4.0))  # bord d'un autre calque, bord du cadre
    assert {line.source for line in lines} == {"layer", "frame"}
    areas = safe_area_rects(1080, 1920)
    assert areas["title"][0] > areas["action"][0]  # vertical : marges plus larges pour le titre


def test_viewer_hides_live_layers_when_the_composited_segment_is_shown(window):
    window.add_layer_at_playhead("text", "")
    window._refresh_viewer_graphics()
    assert window.preview_panel.mograph_item.isVisible()
    window._viewer_composited = True
    window._refresh_viewer_graphics()
    assert not window.preview_panel.mograph_item.isVisible()


def test_presets_menu_applies_a_lower_third(window):
    presets = window.layers_panel._presets
    lower_third = next(p for p in presets if p.name == "Lower third")
    window.layers_panel.preset_apply_requested.emit(lower_third)
    assert len(_layers(window)) == 4
    assert window.history.undo_label == "Preset « Lower third »"


def test_overlay_point_mapping_is_consistent():
    from ui.viewer_overlay import ViewerOverlay

    overlay = ViewerOverlay()
    overlay.set_canvas(QRectF(10, 20, 640, 360), (1920.0, 1080.0))
    point = overlay.to_scene(960, 540)
    assert (point.x(), point.y()) == pytest.approx((330, 200))
    assert overlay.to_canvas(QPointF(330, 200)) == pytest.approx((960, 540))


def test_compositing_editor_alone_keeps_mask_ids(qtbot):
    from ui.compositing_editor import CompositingEditor

    editor = CompositingEditor("")
    qtbot.addWidget(editor)
    values = []
    editor.value_changed.connect(values.append)
    mask = Mask()
    editor.set_value(Compositing(masks=(mask,)))
    editor.inverted.setChecked(True)
    assert values[-1].masks[0].id == mask.id and values[-1].masks[0].inverted
