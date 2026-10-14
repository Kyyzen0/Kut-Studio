"""ADR-0006 lot 3a : les poignées du masque choisi dans la pile, dans le moniteur (surcouche et fenêtre principale)."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPointF, QRectF, Qt

from core import keyframe_editing
from core.animation_targets import get_target
from core.compositing import Compositing, Mask, MaskMode, MaskShape, mask_property_id
from core.graphics import GraphicType, add_graphic_clip
from core.mask_handles import geometry_of
from core.mograph_layers import set_layer_locked
from core.project_model import Clip, MediaAsset, Project, Track
from ui.i18n import translate

IDENT = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
FRAME_BOX = (1280.0, 720.0)


class _Event:
    """Événement souris minimal pour appeler directement les gestionnaires de la surcouche."""

    def __init__(self, point: QPointF, modifiers=Qt.NoModifier):
        self._point, self._modifiers, self.accepted = point, modifiers, None

    def pos(self):
        return self._point

    def modifiers(self):
        return self._modifiers

    def accept(self):
        self.accepted = True

    def ignore(self):
        self.accepted = False


# -- surcouche faite à la main --------------------------------------------------------------------


def _overlay(mask: Mask | None = None, *, editable=True, selection=None, boxes=()):
    from ui.viewer_overlay import ViewerOverlay

    overlay = ViewerOverlay()
    overlay.set_canvas(QRectF(0, 0, 640, 360), (1280.0, 720.0))
    overlay.set_layer_boxes(boxes)
    overlay.selection = selection
    mask = mask or Mask(id="m1")
    overlay.set_mask_edit(geometry_of("v", mask, IDENT, FRAME_BOX, editable=editable))
    return overlay


def _drag(overlay, start: QPointF, end: QPointF, modifiers=Qt.NoModifier):
    events = {"drag": [], "release": []}
    overlay.mask_dragged.connect(lambda *args: events["drag"].append(args))
    overlay.mask_released.connect(lambda *args: events["release"].append(args))
    overlay.mousePressEvent(_Event(start, modifiers))
    overlay.mouseMoveEvent(_Event(end, modifiers))
    overlay.mouseReleaseEvent(_Event(end, modifiers))
    return events


def test_hit_gives_the_mask_handles_then_its_inside():
    overlay = _overlay()
    handle = overlay.masks.handles()["m_br"]
    assert overlay._hit(handle) == ("m_br", "m1")
    assert overlay._hit(overlay.to_scene(640.0, 360.0)) == ("m_move", "m1")
    assert overlay._hit(overlay.masks.handles()["m_rotate"]) == ("m_rotate", "m1")
    assert overlay._hit(overlay.masks.handles()["m_feather"]) == ("m_feather", "m1")


def test_mask_handles_beat_the_clip_and_the_layers_around():
    from ui.viewer_overlay import SelectionGeometry

    clip = ("v", IDENT, FRAME_BOX)
    above = ("t", IDENT, FRAME_BOX)
    selection = SelectionGeometry("v", IDENT, IDENT, FRAME_BOX, {"anchor_x": 0.5, "anchor_y": 0.5})
    overlay = _overlay(selection=selection, boxes=[clip, above])
    centre = overlay.to_scene(640.0, 360.0)
    assert overlay._hit(centre) == ("m_move", "m1")                       # pas « select » du calque au-dessus, ni « move » du clip
    assert overlay._hit(overlay.masks.handles()["m_tl"]) == ("m_tl", "m1")
    assert overlay._hit(overlay.to_scene(20.0, 20.0)) == ("select", "t")   # hors du masque : le comportement habituel


def test_pick_mode_and_compare_keep_priority_over_the_mask():
    overlay = _overlay()
    overlay.set_pick_mode(True)
    assert overlay._hit(overlay.to_scene(640.0, 360.0)) == ("pick", "")
    overlay.set_pick_mode(False)
    overlay.set_compare_split(0.5)
    assert overlay._hit(overlay.to_scene(640.0, 360.0)) == ("compare", "")


def test_a_non_editable_mask_has_no_handles_and_no_inside():
    overlay = _overlay(editable=False, boxes=[("v", IDENT, FRAME_BOX)])
    assert overlay.masks.handles() == {}
    assert overlay._hit(overlay.to_scene(640.0, 360.0)) == ("select", "v")
    assert overlay.shape().contains(overlay.to_scene(10.0, 10.0))       # le calque reste cliquable
    events = _drag(overlay, overlay.to_scene(640.0, 360.0), overlay.to_scene(700.0, 360.0))
    assert events["drag"] == [] and events["release"] == []


def test_polygon_vertices_come_first_and_have_no_side_handles():
    overlay = _overlay(Mask(id="m1", shape=MaskShape.POLYGON))
    names = set(overlay.masks.handles())
    assert {"m_p0", "m_p4", "m_tl", "m_rotate", "m_feather"} <= names and not {"m_t", "m_r", "m_b", "m_l"} & names
    vertex = overlay.masks.handles()["m_p1"]
    assert overlay._hit(vertex) == ("m_p1", "m1")


def test_shape_includes_the_mask_for_hit_testing():
    overlay = _overlay()
    assert overlay.shape().contains(overlay.to_scene(640.0, 360.0))
    assert overlay.shape().contains(overlay.masks.handles()["m_rotate"])


def test_cursors_follow_the_handles():
    overlay = _overlay()
    assert overlay.masks.cursor("m_move") == Qt.SizeAllCursor
    assert overlay.masks.cursor("m_tl") == Qt.SizeFDiagCursor and overlay.masks.cursor("m_tr") == Qt.SizeBDiagCursor
    assert overlay.masks.cursor("m_r") == Qt.SizeHorCursor and overlay.masks.cursor("m_t") == Qt.SizeVerCursor
    assert overlay.masks.cursor("m_rotate") == Qt.CrossCursor
    overlay.hoverMoveEvent(_Event(overlay.masks.handles()["m_br"]))
    assert overlay.cursor().shape() == Qt.SizeFDiagCursor


def test_dragging_the_inside_emits_live_values_then_one_release():
    overlay = _overlay()
    events = _drag(overlay, overlay.to_scene(640.0, 360.0), overlay.to_scene(704.0, 360.0))
    assert len(events["drag"]) == 1 and len(events["release"]) == 1
    clip_id, mask_id, values = events["drag"][0]
    assert (clip_id, mask_id) == ("v", "m1")
    assert values["position_x"] == pytest.approx(0.5 + 64.0 / 1280.0)
    assert events["release"][0] == ("v", "m1")


def test_a_drag_far_beyond_the_bounds_emits_the_pinned_value():
    overlay = _overlay()
    start = overlay.to_scene(640.0, 360.0)
    events = _drag(overlay, start, overlay.to_scene(640.0 + 1280.0 * 4.0, 360.0))      # position_x voudrait 4,5
    assert events["drag"][0][2]["position_x"] == 3.0 and len(events["release"]) == 1


def test_dragging_a_polygon_vertex_emits_the_points():
    overlay = _overlay(Mask(id="m1", shape=MaskShape.POLYGON))
    vertex = overlay.masks.handles()["m_p0"]
    events = _drag(overlay, vertex, QPointF(vertex.x() + 20.0, vertex.y() + 10.0))
    (_clip, _mask, values), = events["drag"]
    assert set(values) == {"points"} and len(values["points"]) == 5
    assert values["points"][0] != (0.0, -0.5)


def test_a_drag_keeps_its_start_geometry_when_the_model_sends_a_new_one():
    overlay = _overlay()
    handle = overlay.masks.handles()["m_br"]
    overlay.mousePressEvent(_Event(handle))
    overlay.set_mask_edit(geometry_of("v", Mask(id="m1", width=0.9), IDENT, FRAME_BOX))     # retour du modèle pendant le geste
    seen = []
    overlay.mask_dragged.connect(lambda *args: seen.append(args[2]))
    overlay.mouseMoveEvent(_Event(QPointF(handle.x() + 32.0, handle.y())))
    assert seen[0]["width"] == pytest.approx(0.5 + 2 * 64.0 / 1280.0)                       # calculé depuis la largeur de l'appui, 0,5


def test_painting_every_shape_and_mode_does_not_raise():
    from PySide6.QtGui import QImage, QPainter

    image = QImage(640, 360, QImage.Format_ARGB32)
    for shape in MaskShape:
        for mode in MaskMode:
            for editable in (True, False):
                overlay = _overlay(Mask(id="m1", shape=shape, mode=mode, inverted=mode is MaskMode.SUBTRACT, feather=0.05),
                                   editable=editable)
                painter = QPainter(image)
                overlay.paint(painter, None)
                painter.end()


# -- fenêtre principale ---------------------------------------------------------------------------


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
    win.project = Project(name="Mask", width=1280, height=720, fps=25.0)
    win.project.media_assets.append(MediaAsset("asset", "/tmp/clip.mp4", "clip", 6, 1280, 720, 25.0, "video"))
    masks = (Mask(id="m1", width=0.5, height=0.5), Mask(id="m2", shape=MaskShape.POLYGON, width=0.4, height=0.4, mode=MaskMode.SUBTRACT))
    clip = Clip("v", "asset", "V1", 0.0, 0.0, 6.0, compositing=Compositing(masks=masks))
    win.project.tracks.append(Track("V1", "V1", "video", clips=[clip]))
    win._timeline_index = None
    win._reload_timeline_preserving_selection()
    win.history.reset(win.project)
    win.resize(1400, 900)
    win.show()
    return win


def _clip(win, clip_id="v"):
    return next(c for t in win.project.tracks for c in t.clips if c.id == clip_id)


def _mask(win, mask_id="m1", clip_id="v"):
    return _clip(win, clip_id).compositing.mask_by_id(mask_id)


def _select_clip(win, clip_id="v"):
    win.preview_panel.overlay.layer_clicked.emit(clip_id)
    assert win._primary_selected_id() == clip_id
    win._refresh_viewer_graphics()                  # le cadre du moniteur prend la taille du projet (échelle de la surcouche)


def _show_mask(win, row=0):
    group = win.properties_panel.compositing_group
    if group.mask_list.currentRow() == row:
        group.edit_in_monitor.setChecked(True)
    else:
        group.mask_list.setCurrentRow(row)


def _press_drag_release(win, start_canvas, end_canvas):
    overlay = win.preview_panel.overlay
    start, end = overlay.to_scene(*start_canvas), overlay.to_scene(*end_canvas)
    overlay.mousePressEvent(_Event(start))
    overlay.mouseMoveEvent(_Event(end))
    overlay.mouseReleaseEvent(_Event(end))


def test_without_a_selected_mask_the_clip_keeps_its_handles(window):
    window.playhead_seconds = 1.0
    _select_clip(window)
    overlay = window.preview_panel.overlay
    assert overlay.masks.geometry is None
    assert overlay.selection is not None and overlay.selection.clip_id == "v"


def test_choosing_a_mask_in_the_stack_replaces_the_clip_handles(window):
    window.playhead_seconds = 1.0
    _select_clip(window)
    _show_mask(window, 1)
    overlay = window.preview_panel.overlay
    assert overlay.selection is None
    geometry = overlay.masks.geometry
    assert (geometry.clip_id, geometry.mask_id, geometry.shape, geometry.mode) == ("v", "m2", "polygon", "subtract")
    assert geometry.editable and "m_p0" in overlay.masks.handles()
    _show_mask(window, 0)
    assert overlay.masks.geometry.mask_id == "m1"


def test_unchecking_the_toggle_gives_the_clip_handles_back(window):
    window.playhead_seconds = 1.0
    _select_clip(window)
    _show_mask(window, 0)
    overlay = window.preview_panel.overlay
    assert overlay.masks.geometry is not None
    window.properties_panel.compositing_group.edit_in_monitor.setChecked(False)
    assert overlay.masks.geometry is None and overlay.selection is not None


def test_selecting_another_clip_or_nothing_leaves_mask_mode(window):
    window.playhead_seconds = 1.0
    _select_clip(window)
    _show_mask(window, 0)
    window.properties_panel.set_clip(None)
    assert window._mask_edit_id == ""
    assert window.preview_panel.overlay.masks.geometry is None


def test_a_removed_mask_leaves_mask_mode(window):
    window.playhead_seconds = 1.0
    _select_clip(window)
    _show_mask(window, 0)
    window.properties_panel.compositing_group.remove_mask.click()
    assert window._mask_edit_id == "" and window.preview_panel.overlay.masks.geometry is None


def test_dragging_the_mask_writes_it_in_one_undoable_history_step(window):
    window.playhead_seconds = 1.0
    _select_clip(window)
    _show_mask(window, 0)
    entries = len(window.history)
    before = _mask(window)
    centre = (640.0, 360.0)
    _press_drag_release(window, centre, (640.0 + 128.0, 360.0 + 72.0))
    mask = _mask(window)
    assert mask.position_x == pytest.approx(0.6) and mask.position_y == pytest.approx(0.6)
    assert (mask.width, mask.height, mask.rotation, mask.feather) == (before.width, before.height, 0.0, 0.0)
    assert len(window.history) == entries + 1
    assert window.history.entries()[-1].label == translate("history.mask.drag")
    window.undo_last()
    assert _mask(window).position_x == pytest.approx(0.5) and _mask(window).position_y == pytest.approx(0.5)
    assert len(window.history) == entries


def test_resizing_rotating_and_feathering_through_the_monitor(window):
    window.playhead_seconds = 1.0
    _select_clip(window)
    _show_mask(window, 0)
    overlay = window.preview_panel.overlay
    k = overlay.scale                                                          # pixels de scène par pixel du cadre
    corner = overlay.masks.handles()["m_br"]
    overlay.mousePressEvent(_Event(corner))
    overlay.mouseMoveEvent(_Event(QPointF(corner.x() + 128.0 * k, corner.y() + 72.0 * k)))
    overlay.mouseReleaseEvent(_Event(corner))
    assert _mask(window).width == pytest.approx(0.5 + 2 * 128.0 / 1280.0)
    assert _mask(window).height == pytest.approx(0.5 + 2 * 72.0 / 720.0)
    k = overlay.scale                                                          # le cadre a pu être recalculé après le geste
    feather = overlay.masks.handles()["m_feather"]
    overlay.mousePressEvent(_Event(feather))
    overlay.mouseMoveEvent(_Event(QPointF(feather.x() + 72.0 * k, feather.y())))
    overlay.mouseReleaseEvent(_Event(feather))
    assert _mask(window).feather == pytest.approx(72.0 / 720.0)             # 72 px du calque / min(1280, 720)
    rotate = overlay.masks.handles()["m_rotate"]
    centre = overlay.to_scene(640.0, 360.0)
    overlay.mousePressEvent(_Event(rotate))
    overlay.mouseMoveEvent(_Event(QPointF(centre.x() + 100.0, centre.y())))      # du haut vers la droite : +90°
    overlay.mouseReleaseEvent(_Event(centre))
    assert _mask(window).rotation == pytest.approx(90.0)


def test_an_animated_property_gets_a_keyframe_at_the_playhead_and_the_others_stay_static(window):
    window.playhead_seconds = 0.0
    pid = mask_property_id("m1", "position_x")
    keyframe_editing.add_keyframe(window.project, "v", pid, 0.0, 0.5)
    window.playhead_seconds = 2.0
    _select_clip(window)
    _show_mask(window, 0)
    _press_drag_release(window, (640.0, 360.0), (640.0 + 128.0, 360.0 + 72.0))
    clip = _clip(window)
    times = sorted(k.time_seconds for k in get_target(pid).get_keyframes(clip))
    assert times == [0.0, 2.0]
    assert keyframe_editing.value_at(clip, pid, 2.0) == pytest.approx(0.6)
    assert _mask(window).position_x == pytest.approx(0.5)                       # la valeur statique d'une propriété animée ne bouge pas
    assert _mask(window).position_y == pytest.approx(0.6)                       # une propriété non animée : valeur statique


def test_polygon_vertices_are_written_into_the_mask(window):
    window.playhead_seconds = 1.0
    _select_clip(window)
    _show_mask(window, 1)
    overlay = window.preview_panel.overlay
    vertex = overlay.masks.handles()["m_p0"]
    before = _mask(window, "m2").points
    entries = len(window.history)
    overlay.mousePressEvent(_Event(vertex))
    overlay.mouseMoveEvent(_Event(QPointF(vertex.x() + 20.0, vertex.y() + 10.0)))
    overlay.mouseReleaseEvent(_Event(vertex))
    after = _mask(window, "m2").points
    assert after[0] != before[0] and after[1:] == before[1:]
    assert len(window.history) == entries + 1
    window.undo_last()
    assert _mask(window, "m2").points == before


def test_a_locked_track_shows_the_mask_without_handles_and_refuses_writes(window):
    window.playhead_seconds = 1.0
    _select_clip(window)
    _show_mask(window, 0)
    window.project.tracks[0].locked = True
    window._refresh_viewer_overlay(1.0)
    overlay = window.preview_panel.overlay
    assert overlay.masks.geometry is not None and not overlay.masks.geometry.editable and overlay.masks.handles() == {}
    entries = len(window.history)
    overlay.mask_dragged.emit("v", "m1", {"position_x": 0.9})                  # un signal qui atteindrait le modèle
    overlay.mask_released.emit("v", "m1")
    assert _mask(window).position_x == pytest.approx(0.5) and len(window.history) == entries


def test_a_locked_graphic_layer_refuses_through_the_layer_rules(window):
    window.project.tracks.clear()
    track = Track("G1", "G1", "graphics")
    window.project.tracks.append(track)
    clip = add_graphic_clip(window.project, GraphicType.TEXT, timeline_start=0.0, duration=4.0, track=track, clip_id="g")
    clip.compositing = Compositing(masks=(Mask(id="m1"),))
    window._timeline_index = None
    window._reload_timeline_preserving_selection()
    window.history.reset(window.project)
    window.playhead_seconds = 1.0
    _select_clip(window, "g")
    _show_mask(window, 0)
    overlay = window.preview_panel.overlay
    assert overlay.masks.geometry is not None and overlay.masks.geometry.editable
    set_layer_locked(window.project, "g", True)
    window._refresh_viewer_overlay(1.0)
    assert not overlay.masks.geometry.editable and overlay.masks.handles() == {}
    entries = len(window.history)
    overlay.mask_dragged.emit("g", "m1", {"position_x": 0.9})
    assert _mask(window, "m1", "g").position_x == pytest.approx(0.5) and len(window.history) == entries


def test_outside_the_clip_duration_the_clip_handles_come_back_and_writes_are_refused(window):
    window.playhead_seconds = 1.0
    _select_clip(window)
    _show_mask(window, 0)
    window.playhead_seconds = 7.0                                              # la durée du clip est 6 s
    window._refresh_viewer_overlay(7.0)
    overlay = window.preview_panel.overlay
    assert overlay.masks.geometry is None
    overlay.mask_dragged.emit("v", "m1", {"position_x": 0.9})
    assert _mask(window).position_x == pytest.approx(0.5)


def test_a_hidden_track_or_a_disabled_clip_shows_the_mask_without_active_handles(window):
    window.playhead_seconds = 1.0
    _select_clip(window)
    _show_mask(window, 0)
    _clip(window).enabled = False
    window._refresh_viewer_overlay(1.0)
    assert window.preview_panel.overlay.masks.geometry is not None
    assert not window.preview_panel.overlay.masks.geometry.editable


def test_a_value_beyond_the_model_bounds_is_never_written_or_clamped(window):
    window.playhead_seconds = 1.0
    _select_clip(window)
    _show_mask(window, 0)
    overlay = window.preview_panel.overlay
    entries = len(window.history)
    overlay.mask_dragged.emit("v", "m1", {"feather": 1.4})
    overlay.mask_dragged.emit("v", "m1", {"points": ((9.0, 0.0), (0.0, 1.0), (1.0, 1.0))})
    overlay.mask_released.emit("v", "m1")
    assert _mask(window).feather == 0.0 and len(window.history) == entries


def test_the_mask_overlay_follows_the_playhead_animation(window):
    pid = mask_property_id("m1", "position_x")
    window.playhead_seconds = 0.0
    keyframe_editing.add_keyframe(window.project, "v", pid, 0.0, 0.2)
    keyframe_editing.add_keyframe(window.project, "v", pid, 4.0, 0.6)
    _select_clip(window)
    _show_mask(window, 0)
    window.playhead_seconds = 2.0
    window._refresh_viewer_overlay(2.0)
    assert window.preview_panel.overlay.masks.geometry.values["position_x"] == pytest.approx(0.4)


def test_tracking_and_window_modes_keep_priority(window, monkeypatch):
    window.playhead_seconds = 1.0
    _select_clip(window)
    _show_mask(window, 0)
    monkeypatch.setattr(window, "_tracking_mode_active", lambda: True, raising=False)
    window._refresh_viewer_overlay(1.0)
    assert window.preview_panel.overlay.masks.geometry is None


# -- masque suivi (revue de #147) ------------------------------------------------------------------


def _fake_tracking(window, monkeypatch, shift=0.1):
    """Un lien de suivi simulé : le rendu décale la position du masque de ``shift`` par rapport à la saisie."""
    import core.tracking_bindings as tb
    from core.animation import Keyframe
    from core.compositing import evaluate_mask_at
    from core.animation_targets import animation_curves
    from core.tracking_model import ClipTracking

    _clip(window).tracking = ClipTracking()                       # non nul : la scène demande l'état effectif

    def effective(clip, _context):
        pid = mask_property_id("m1", "position_x")
        curves = animation_curves(clip, prefix="mask.m1.")
        mask = clip.compositing.mask_by_id("m1")
        raw = [k for k in clip.animation if k.property_name != pid]
        derived = [Keyframe(pid, t, evaluate_mask_at(mask, curves, t).position_x + shift) for t in (0.0, clip.duration)]
        return tb.EffectiveState(tuple(clip.transform_keyframes), tuple(raw + derived), clip.compositing, frozenset({pid}))

    monkeypatch.setattr(tb, "effective_clip_state", effective)


def test_a_tracked_mask_is_drawn_where_it_is_rendered_but_dragged_from_its_entered_value(window, monkeypatch):
    _fake_tracking(window, monkeypatch)
    window.playhead_seconds = 1.0
    _select_clip(window)
    _show_mask(window, 0)
    overlay = window.preview_panel.overlay
    geometry = overlay.masks.geometry
    assert geometry.values["position_x"] == pytest.approx(0.6) and geometry.entered["position_x"] == pytest.approx(0.5)
    _press_drag_release(window, (640.0 + 128.0, 360.0), (640.0 + 128.0 + 64.0, 360.0))      # 64 px du cadre
    assert _mask(window).position_x == pytest.approx(0.5 + 64.0 / 1280.0)             # la saisie bouge de d, sans saut de 0,1
    window._refresh_viewer_overlay(1.0)                                              # seconde évaluation, même tête de lecture
    shown = window.preview_panel.overlay.masks.geometry.values["position_x"]
    assert shown == pytest.approx(0.6 + 64.0 / 1280.0)                               # déplacé de d, suivi appliqué une seule fois


# -- doublon d'un clip (revue de #147) -------------------------------------------------------------


def test_selecting_a_duplicate_leaves_mask_mode_and_going_back_does_not_restore_it(window):
    import copy

    window.playhead_seconds = 1.0
    _select_clip(window)
    _show_mask(window, 0)
    track = window.project.tracks[0]
    twin = copy.deepcopy(_clip(window))
    twin.id = "v2"
    track.clips.append(twin)
    window._timeline_index = None
    window._reload_timeline_preserving_selection()
    _select_clip(window, "v2")
    overlay = window.preview_panel.overlay
    assert overlay.masks.geometry is None and window._mask_edit_id == ""      # mêmes identifiants de masque, autre clip
    assert overlay.selection is not None and overlay.selection.clip_id == "v2"
    assert not window.properties_panel.compositing_group.edit_in_monitor.isChecked()
    _select_clip(window, "v")
    assert overlay.masks.geometry is None                                      # le mode n'est pas rétabli tout seul
    assert overlay.selection is not None and overlay.selection.clip_id == "v"


def test_clearing_the_crop_while_editing_it_in_the_monitor_leaves_mask_mode(qtbot):
    """Le recadrage (#146) est un masque : vider ses champs le supprime et doit sortir du mode masque (fusion #147)."""
    from core.clip_crop import CROP_MASK_ID, Crop, with_crop
    from core.compositing import Compositing
    from ui.compositing_editor import CompositingEditor

    editor = CompositingEditor("")
    qtbot.addWidget(editor)
    editor.set_value(with_crop(Compositing(), Crop(left=0.1)))
    seen = []
    editor.mask_edit_changed.connect(seen.append)
    editor._set_edit(CROP_MASK_ID)
    assert seen == [CROP_MASK_ID]
    editor._crop_edited(None)
    assert editor._edit_id == "" and seen[-1] == ""
