"""ADR-0006 lot 1c : alignement du calque sélectionné sur le cadre et déplacement au clavier."""

from __future__ import annotations

import math

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent

from core.frame_alignment import FrameEdge, align_to_frame, screen_delta_to_position_delta
from core.mograph_layers import layer_clips
from core.mograph_scene import map_box
from core.project_model import Project

CANVAS = (1280.0, 720.0)
IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


# ---------------------------------------------------------------- fonction pure


@pytest.mark.parametrize(
    ("edge", "expected"),
    [
        (FrameEdge.LEFT, (-100.0, 0.0)),
        (FrameEdge.RIGHT, (1280.0 - 300.0, 0.0)),
        (FrameEdge.CENTER_H, (640.0 - 200.0, 0.0)),
        (FrameEdge.TOP, (0.0, -50.0)),
        (FrameEdge.BOTTOM, (0.0, 720.0 - 250.0)),
        (FrameEdge.MIDDLE_V, (0.0, 360.0 - 150.0)),
    ],
)
def test_align_to_frame_six_edges(edge, expected):
    assert align_to_frame((100.0, 50.0, 300.0, 250.0), CANVAS, edge) == pytest.approx(expected)
    assert align_to_frame((100.0, 50.0, 300.0, 250.0), CANVAS, edge.value) == pytest.approx(expected)


def test_align_to_frame_only_moves_one_axis():
    for edge in (FrameEdge.LEFT, FrameEdge.CENTER_H, FrameEdge.RIGHT):
        assert align_to_frame((10, 20, 30, 40), CANVAS, edge)[1] == 0.0
    for edge in (FrameEdge.TOP, FrameEdge.MIDDLE_V, FrameEdge.BOTTOM):
        assert align_to_frame((10, 20, 30, 40), CANVAS, edge)[0] == 0.0


def test_align_to_frame_layer_larger_than_frame():
    box = (-200.0, -100.0, 1600.0, 900.0)            # 1800 x 1000 dans un cadre 1280 x 720
    assert align_to_frame(box, CANVAS, "left") == pytest.approx((200.0, 0.0))
    assert align_to_frame(box, CANVAS, "right") == pytest.approx((1280.0 - 1600.0, 0.0))
    assert align_to_frame(box, CANVAS, "center_h") == pytest.approx((640.0 - 700.0, 0.0))
    assert align_to_frame(box, CANVAS, "bottom") == pytest.approx((0.0, 720.0 - 900.0))


def test_align_to_frame_uses_the_bounding_box_of_a_rotated_layer():
    angle = math.radians(45)
    c, s = math.cos(angle), math.sin(angle)
    world = (c, s, -s, c, 600.0, 300.0)               # carré 100 x 100 tourné de 45°, origine en (600, 300)
    bounds = map_box(world, 100.0, 100.0)
    dx, _dy = align_to_frame(bounds, CANVAS, "left")
    moved = map_box((c, s, -s, c, 600.0 + dx, 300.0), 100.0, 100.0)
    assert moved[0] == pytest.approx(0.0, abs=1e-9)
    assert moved[2] - moved[0] == pytest.approx(100.0 * math.sqrt(2.0))


def test_align_to_frame_rejects_unknown_edge():
    with pytest.raises(ValueError):
        align_to_frame((0, 0, 1, 1), CANVAS, "diagonal")


def test_screen_delta_identity_parent_is_a_fraction_of_the_frame():
    assert screen_delta_to_position_delta(IDENTITY, 128.0, -72.0, CANVAS) == pytest.approx((0.1, -0.1))


def test_screen_delta_through_a_scaled_and_rotated_parent():
    angle = math.radians(90)
    c, s = math.cos(angle), math.sin(angle)
    parent = (2 * c, 2 * s, -2 * s, 2 * c, 40.0, 10.0)   # échelle 2, rotation 90°
    px, py = screen_delta_to_position_delta(parent, 100.0, 0.0, CANVAS)
    # Appliquer la matrice (partie linéaire) au déplacement local doit redonner le déplacement écran.
    lx, ly = px * CANVAS[0], py * CANVAS[1]
    a, b, cc, d = parent[:4]
    assert (a * lx + cc * ly, b * lx + d * ly) == pytest.approx((100.0, 0.0), abs=1e-9)


def test_screen_delta_singular_parent_is_refused():
    assert screen_delta_to_position_delta((0.0, 0.0, 0.0, 0.0, 5.0, 5.0), 10.0, 10.0, CANVAS) is None
    assert screen_delta_to_position_delta((1.0, 2.0, 2.0, 4.0, 0.0, 0.0), 10.0, 10.0, CANVAS) is None


# ---------------------------------------------------------------- fenêtre principale


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
    win.project = Project(name="Align", width=1280, height=720, fps=25.0)
    win._timeline_index = None
    win._reload_timeline_preserving_selection()
    win.history.reset(win.project)
    win.resize(1400, 900)
    win.show()
    return win


def _layers(win):
    return [clip for _i, _t, clip in layer_clips(win.project)]


def _bounds(win, clip_id):
    win._refresh_viewer_overlay(float(win.playhead_seconds))
    selection = win.preview_panel.overlay.selection
    assert selection is not None and selection.clip_id == clip_id
    return map_box(selection.world, *selection.box)


def _shape(win):
    win.add_layer_at_playhead("shape", "rectangle")
    (layer,) = _layers(win)
    return layer


def test_align_left_touches_the_frame_edge_in_one_history_step(window):
    layer = _shape(window)
    window._apply_property_values(layer.id, {"position_x": 0.2, "position_y": -0.1})
    window._commit_layer_edit("préparation", select=layer.id)
    assert _bounds(window, layer.id)[0] > 1.0
    entries = len(window.history)
    scale, rotation = layer.transform.scale, layer.transform.rotation
    y_before = layer.transform.position_y

    assert window.align_selected_layer_to_frame("left") is True

    assert _bounds(window, layer.id)[0] == pytest.approx(0.0, abs=1e-6)
    assert layer.transform.position_y == pytest.approx(y_before)
    assert (layer.transform.scale, layer.transform.rotation) == (scale, rotation)
    assert len(window.history) == entries + 1
    window.undo_last()
    (restored,) = _layers(window)
    assert restored.transform.position_x == pytest.approx(0.2)
    assert len(window.history) == entries


def test_every_edge_aligns_the_bounding_box(window):
    layer = _shape(window)
    canvas_w, canvas_h = float(window.project.width), float(window.project.height)
    for edge in FrameEdge:
        assert window.align_selected_layer_to_frame(edge) is True
        x0, y0, x1, y1 = _bounds(window, layer.id)
        expected = {
            FrameEdge.LEFT: x0, FrameEdge.RIGHT: x1 - canvas_w, FrameEdge.TOP: y0, FrameEdge.BOTTOM: y1 - canvas_h,
            FrameEdge.CENTER_H: (x0 + x1) / 2 - canvas_w / 2, FrameEdge.MIDDLE_V: (y0 + y1) / 2 - canvas_h / 2,
        }[edge]
        assert expected == pytest.approx(0.0, abs=1e-6), edge


def test_align_an_animated_layer_writes_a_keyframe_at_the_playhead(window):
    layer = _shape(window)
    window.on_transform_keyframe_added(layer.id, "position_x", 0.0, 0.0)
    window.on_transform_keyframe_added(layer.id, "position_x", 1.0, 0.1)
    window.playhead_seconds = 0.48            # sur une image (25 i/s)
    window._refresh_viewer_overlay(0.48)
    before = len([k for k in layer.transform_keyframes if k.property_name == "position_x"])
    assert window.align_selected_layer_to_frame("right") is True
    keys = [k for k in layer.transform_keyframes if k.property_name == "position_x"]
    assert len(keys) == before + 1
    assert any(abs(k.time_seconds - 0.48) < 1e-6 for k in keys)
    assert _bounds(window, layer.id)[2] == pytest.approx(float(window.project.width), abs=1e-6)


def test_align_refuses_a_locked_layer_without_changes(window):
    layer = _shape(window)
    window._on_layer_lock(layer.id, True)
    window._select_layer(layer.id)
    entries = len(window.history)
    x_before = layer.transform.position_x
    assert window.align_selected_layer_to_frame("left") is False
    assert layer.transform.position_x == x_before
    assert len(window.history) == entries


def test_align_without_selection_does_nothing(window):
    entries = len(window.history)
    assert window.align_selected_layer_to_frame("left") is False
    assert len(window.history) == entries


def test_align_buttons_menu_and_commands_are_wired(window):
    layer = _shape(window)
    row = window.properties_panel.frame_align
    assert len(row.buttons) == 6
    for edge, button in row.buttons.items():
        assert button.toolTip() and button.accessibleName() == button.toolTip()
        assert window.shortcuts.action(f"layer_align_{edge.value}") is not None
    window._apply_property_values(layer.id, {"position_x": 0.15})
    row.buttons[FrameEdge.LEFT].click()
    assert _bounds(window, layer.id)[0] == pytest.approx(0.0, abs=1e-6)
    window.shortcuts.trigger("layer_align_right")
    assert _bounds(window, layer.id)[2] == pytest.approx(float(window.project.width), abs=1e-6)
    assert window.shortcuts.missing_handlers() == []


# ---------------------------------------------------------------- clavier


def _key(window, key, modifiers=Qt.NoModifier):
    event = QKeyEvent(QEvent.KeyPress, key, modifiers)
    window.preview_panel.graphics_view.setFocus()
    from PySide6.QtWidgets import QApplication

    QApplication.sendEvent(window.preview_panel.graphics_view, event)
    return event


def test_arrow_keys_nudge_by_one_pixel_and_shift_by_ten(window):
    layer = _shape(window)
    start = _bounds(window, layer.id)
    event = _key(window, Qt.Key_Right)
    assert event.isAccepted()
    after = _bounds(window, layer.id)
    assert after[0] - start[0] == pytest.approx(1.0, abs=1e-6)
    _key(window, Qt.Key_Down, Qt.ShiftModifier)
    final = _bounds(window, layer.id)
    assert final[1] - after[1] == pytest.approx(10.0, abs=1e-6)
    assert final[0] == pytest.approx(after[0])
    _key(window, Qt.Key_Left)
    _key(window, Qt.Key_Up)
    back = _bounds(window, layer.id)
    assert (back[0], back[1]) == pytest.approx((start[0], start[1] + 10.0 - 1.0), abs=1e-6)


def test_rapid_nudges_merge_into_one_history_step(window):
    _shape(window)
    entries = len(window.history)
    for _ in range(5):
        _key(window, Qt.Key_Right)
    assert len(window.history) == entries + 1
    window.history.undo()
    assert len(window.history) == entries


def test_arrow_with_nothing_to_move_is_left_alone(window):
    entries = len(window.history)
    event = _key(window, Qt.Key_Right)
    assert not event.isAccepted() or len(window.history) == entries
    assert len(window.history) == entries


def test_ctrl_arrow_is_not_captured(window):
    layer = _shape(window)
    before = _bounds(window, layer.id)
    _key(window, Qt.Key_Right, Qt.ControlModifier)
    assert _bounds(window, layer.id) == pytest.approx(before)



def test_align_works_on_a_video_clip_and_leaves_scale_alone(window):
    from core.project_model import Clip, MediaAsset, Track

    asset = MediaAsset(id="a1", path="/nonexistent/clip.mp4", name="clip", media_type="video", duration=5.0, width=640, height=360, fps=25.0)
    track = Track(id="v1", name="V1", type="video")
    clip = Clip(id="c1", asset_id="a1", track_id="v1", timeline_start=0.0, source_in=0.0, source_out=5.0)
    clip.transform = clip.transform.with_property("scale", 0.5)
    track.clips.append(clip)
    window.project.media_assets.append(asset)
    window.project.tracks.append(track)
    window._reload_timeline_preserving_selection("c1")
    window._select_layer("c1")
    window._refresh_viewer_overlay(0.0)
    assert window.preview_panel.overlay.selection is not None
    assert window.align_selected_layer_to_frame("left") is True
    assert _bounds(window, "c1")[0] == pytest.approx(0.0, abs=1e-6)
    assert clip.transform.scale == pytest.approx(0.5)
    assert window.align_selected_layer_to_frame("bottom") is True
    assert _bounds(window, "c1")[3] == pytest.approx(float(window.project.height), abs=1e-6)
