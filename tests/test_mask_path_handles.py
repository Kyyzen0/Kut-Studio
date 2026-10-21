"""ADR-0006 lot 3b : poignées d'un masque courbe dans le moniteur (calcul pur, surcouche, fenêtre principale, inspecteur)."""

from __future__ import annotations

import math

import pytest
from PySide6.QtCore import QEvent, QPointF, QRectF, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QGraphicsSceneHoverEvent

from core import keyframe_editing
from core import mask_handles as mh
from core import mask_path
from core import mask_path_handles as ph
from core.compositing import Compositing, Mask, MaskShape, mask_property_id
from core.graphics import GraphicType, add_graphic_clip
from core.mask_handles import geometry_of
from core.mograph_layers import set_layer_locked
from core.project_model import Clip, MediaAsset, Project, Track
from ui.i18n import translate

IDENT = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
FRAME_BOX = (1280.0, 720.0)
K = mask_path.KAPPA
WAVY_NODES = ((-0.4, -0.3), (0.1, -0.5), (0.45, 0.0), (0.2, 0.45), (-0.35, 0.3))
WAVY_HANDLES = (
    (-0.1, 0.1, 0.1, -0.1),
    (-0.15, 0.0, 0.2, 0.05),
    (0.0, -0.2, 0.0, 0.2),
    (0.15, 0.1, -0.2, -0.05),
    (0.0, 0.1, 0.0, -0.1),
)


def _path(**kwargs) -> Mask:
    kwargs.setdefault("id", "m1")
    return Mask(shape=MaskShape.PATH, **kwargs)


def _geo(
    mask: Mask | None = None, world=IDENT, box=FRAME_BOX, **kwargs
) -> mh.MaskGeometry:
    return geometry_of("clip", mask or _path(), world, box, **kwargs)


def _unit_to_canvas(geometry, u, v):
    return mh.canvas_point(geometry, u, v)


# -- calcul pur : positions ---------------------------------------------------------------------------


def test_a_curve_has_corner_node_and_tangent_handles_but_no_side_handles():
    geometry = _geo()
    units = mh.handle_units(geometry)
    assert {"tl", "tr", "br", "bl"} <= set(units) and not {"t", "r", "b", "l"} & set(
        units
    )
    assert {f"p{i}" for i in range(4)} <= set(units)
    assert units["ho0"] == pytest.approx((K / 2, -0.5)) and units[
        "hi0"
    ] == pytest.approx((-K / 2, -0.5))
    angular = _geo(_path(points=((0.0, -0.5), (0.5, 0.5), (-0.5, 0.5))))
    assert not [
        name for name in mh.handle_units(angular) if name.startswith(("hi", "ho"))
    ]  # nœuds anguleux : rien à tirer


def test_the_outline_follows_the_curve_and_the_box():
    geometry = _geo(_path(width=0.4, height=0.2))
    outline = mh.outline_units(geometry)
    assert len(outline) == 4 * 16 and outline[0] == pytest.approx((0.0, -0.5))
    assert all(
        math.hypot(u, v) == pytest.approx(0.5, abs=2e-3) for u, v in outline
    )  # un cercle de rayon 0,5
    assert mh.outline_units(geometry, (1.0, 1.0))[0] == pytest.approx((0.0, -1.0))


# -- découpe de De Casteljau -----------------------------------------------------------------------------


def _distance_to(points, handles, samples) -> float:
    return max(
        mask_path.nearest_on_outline(
            points, handles, lambda u, v: (u, v), s, steps=200
        )[2]
        for s in samples
    )


@pytest.mark.parametrize("index", [0, 1, 4])  # 4 : le segment de fermeture
@pytest.mark.parametrize("t", [0.2, 0.5, 0.83])
def test_adding_a_node_keeps_the_shape(index, t):
    points, handles = WAVY_NODES, WAVY_HANDLES
    new_points, new_handles = mask_path.split_segment(points, handles, index, t)
    assert len(new_points) == len(points) + 1 and len(new_handles) == len(handles) + 1
    before, after = (
        mask_path.sample_outline(points, handles, 60),
        mask_path.sample_outline(new_points, new_handles, 60),
    )
    assert (
        _distance_to(new_points, new_handles, before) < 1e-3
    )  # chaque point d'avant est encore sur la courbe
    assert (
        _distance_to(points, handles, after) < 1e-3
    )  # et réciproquement : rien de nouveau
    p0, p1, p2, p3 = mask_path.segment(points, handles, index)
    assert new_points[index + 1] == pytest.approx(
        mask_path.bezier_point(p0, p1, p2, p3, t)
    )  # le nouveau nœud est sur la courbe


def test_splitting_is_refused_at_the_cap_outside_the_segment_and_beyond_the_bounds():
    ring = tuple(
        (
            0.4 * math.cos(2 * math.pi * i / mask_path.MAX_NODES),
            0.4 * math.sin(2 * math.pi * i / mask_path.MAX_NODES),
        )
        for i in range(mask_path.MAX_NODES)
    )
    zero = ((0.0,) * 4,) * mask_path.MAX_NODES
    assert mask_path.split_segment(ring, zero, 0, 0.5) is None
    assert mask_path.split_segment(WAVY_NODES, WAVY_HANDLES, 0, 0.0) is None
    assert mask_path.split_segment(WAVY_NODES, WAVY_HANDLES, 0, 1.0) is None
    far = ((2.0, 0.0), (2.0, 2.0), (-2.0, 2.0))
    wide = ((0.0, 0.0, 2.0, 0.0), (2.0, 0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 0.0))
    assert (
        mask_path.split_segment(far, wide, 0, 0.5) is None
    )  # le nœud tomberait à x = 3,5 : on ne l'écrête pas


# -- calcul pur : glisser ------------------------------------------------------------------------------------


def test_dragging_a_node_moves_only_that_node():
    geometry = _geo()  # boîte 640 × 360 centrée en (640, 360)
    start = _unit_to_canvas(geometry, 0.0, -0.5)
    edit = mh.drag(geometry, "p0", start, (start[0] + 64.0, start[1] + 36.0))
    assert edit.values == {} and edit.handles is None
    assert edit.points[0] == pytest.approx((0.1, -0.4)) and edit.points[1:] == tuple(
        geometry.points[1:]
    )


def test_dragging_a_tangent_mirrors_the_opposite_one_unless_alt():
    geometry = _geo()
    start = _unit_to_canvas(geometry, K / 2, -0.5)  # la tangente sortante du nœud 0
    target = (start[0] + 64.0, start[1] + 36.0)  # +0,1 en u, +0,1 en v
    mirrored = mh.drag(geometry, "ho0", start, target)
    assert mirrored.points is None
    assert mirrored.handles[0] == pytest.approx(
        (-(K / 2 + 0.1), -0.1, K / 2 + 0.1, 0.1)
    )
    assert mirrored.handles[1:] == tuple(geometry.handles[1:])
    broken = mh.drag(geometry, "ho0", start, target, alt=True)
    assert broken.handles[0] == pytest.approx(
        (-K / 2, 0.0, K / 2 + 0.1, 0.1)
    )  # l'entrante ne bouge pas
    inside = _unit_to_canvas(geometry, -K / 2, -0.5)
    into = mh.drag(geometry, "hi0", inside, (inside[0] - 64.0, inside[1]))
    assert into.handles[0][0] < -K / 2 and into.handles[0][2] == pytest.approx(
        -into.handles[0][0]
    )


def test_the_drag_is_pinned_to_the_model_bounds_and_stays_symmetric():
    geometry = _geo()
    start = _unit_to_canvas(geometry, 0.0, -0.5)
    far = mh.drag(geometry, "p0", start, (start[0] + 100000.0, start[1] - 100000.0))
    assert far.points[0] == (mask_path.POINT_LIMIT, -mask_path.POINT_LIMIT)
    tangent = _unit_to_canvas(geometry, K / 2, -0.5)
    huge = mh.drag(geometry, "ho0", tangent, (tangent[0] + 100000.0, tangent[1]))
    assert huge.handles[0] == (
        -mask_path.HANDLE_LIMIT,
        0.0,
        mask_path.HANDLE_LIMIT,
        0.0,
    )
    assert not mh.out_of_bounds({}, far.points, huge.handles)


def test_nothing_changes_when_the_pointer_has_not_moved_or_the_index_is_unknown():
    geometry = _geo()
    start = _unit_to_canvas(geometry, 0.0, -0.5)
    assert mh.drag(geometry, "p0", start, start) is None
    assert mh.drag(geometry, "p9", start, (start[0] + 5, start[1])) is None
    assert (
        mh.drag(geometry, "ho2", start, (start[0] + 5, start[1])) is not None
    )  # ho2 existe
    assert mh.drag(geometry, "hi9", start, (start[0] + 5, start[1])) is None


def test_a_rotated_scaled_mask_still_drops_the_node_under_the_pointer():
    mask = _path(rotation=30.0, width=0.3, height=0.6, position_x=0.4)
    world = (0.5, 0.0, 0.0, 0.5, 100.0, 40.0)
    geometry = _geo(mask, world=world)
    start = _unit_to_canvas(geometry, 0.0, -0.5)
    target = _unit_to_canvas(geometry, 0.2, -0.3)
    edit = mh.drag(geometry, "p0", start, target)
    assert edit.points[0] == pytest.approx((0.2, -0.3), abs=1e-6)


def test_a_tracked_mask_is_dragged_from_the_pointer_not_from_the_tracking_offset():
    rendered = _path(position_x=0.6, width=0.5)  # position rendue (suivi compris)
    entered = _path(position_x=0.5, width=0.5)  # position saisie
    geometry = _geo(rendered, entered=entered)
    assert geometry.values["position_x"] == pytest.approx(0.6) and geometry.entered[
        "position_x"
    ] == pytest.approx(0.5)
    start = _unit_to_canvas(geometry, 0.5, 0.0)
    edit = mh.drag(geometry, "p1", start, (start[0] + 32.0, start[1]))
    assert (
        edit.values == {}
    )  # aucune propriété animable n'est écrite : rien à compter deux fois
    assert edit.points[1] == pytest.approx(
        (0.5 + 32.0 / 640.0, 0.0)
    )  # le nœud est dans la boîte : le décalage du suivi n'y entre pas


# -- calcul pur : actions de structure --------------------------------------------------------------------


def test_node_actions_add_remove_and_toggle():
    geometry = _geo()
    on_contour = _unit_to_canvas(geometry, 0.3536, 0.3536)
    added = ph.node_action(geometry, "add", point=on_contour, tolerance=6.0)
    assert len(added.points) == 5 and added.points[2] == pytest.approx(
        (0.3536, 0.3536), abs=0.01
    )
    assert (
        ph.node_action(geometry, "add", point=(0.0, 0.0), tolerance=6.0) is None
    )  # loin du contour
    assert ph.node_action(geometry, "add", point=None) is None
    removed = ph.node_action(geometry, "remove", 1)
    assert (
        removed.points == (geometry.points[0], *geometry.points[2:])
        and len(removed.handles) == 3
    )
    assert (
        ph.node_action(
            _geo(_path(points=removed.points, handles=removed.handles)), "remove", 0
        )
        is None
    )  # 3 nœuds : refus
    smooth = _geo(_path(points=WAVY_NODES, handles=((0.0,) * 4,) * 5))
    toggled = ph.node_action(smooth, "toggle", 2)
    assert not mask_path.is_angular(toggled.handles[2]) and toggled.handles[2][
        :2
    ] == pytest.approx((-toggled.handles[2][2], -toggled.handles[2][3]))
    back = ph.node_action(
        _geo(_path(points=toggled.points, handles=toggled.handles)), "toggle", 2
    )
    assert back.handles[2] == (0.0, 0.0, 0.0, 0.0)
    assert (
        ph.node_action(geometry, "toggle", 9) is None
        and ph.node_action(geometry, "remove", None) is None
    )
    assert ph.node_action(_geo(Mask(id="r")), "remove", 0) is None  # pas une courbe
    assert ph.node_action(geometry, "explode", 0) is None


def test_the_nodes_guard_refuses_what_the_model_would_not_take():
    mask = _path()
    assert ph.nodes_refusal(mask, mask.points, mask.handles) is None
    assert ph.nodes_refusal(mask, mask.points[:3], None) == "length"
    assert ph.nodes_refusal(mask, mask.points[:2], mask.handles[:2]) == "count"
    assert (
        ph.nodes_refusal(mask, ((9.0, 0.0),) + tuple(mask.points[1:]), None) == "bounds"
    )
    assert ph.nodes_refusal(Mask(id="rect"), None, mask.handles) == "shape"
    assert (
        ph.nodes_refusal(
            Mask(shape=MaskShape.POLYGON, id="poly"),
            ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0)),
            None,
        )
        is None
    )


# -- surcouche faite à la main -------------------------------------------------------------------------------


class _Event:
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


def _overlay(mask: Mask | None = None, *, editable=True):
    from ui.viewer_overlay import ViewerOverlay

    overlay = ViewerOverlay()
    overlay.set_canvas(QRectF(0, 0, 640, 360), (1280.0, 720.0))
    overlay.set_mask_edit(
        geometry_of("v", mask or _path(), IDENT, FRAME_BOX, editable=editable)
    )
    return overlay


def _record(overlay):
    events = {"drag": [], "release": []}
    overlay.mask_dragged.connect(lambda *args: events["drag"].append(args))
    overlay.mask_released.connect(lambda *args: events["release"].append(args))
    return events


def _drag(overlay, start, end, modifiers=Qt.NoModifier):
    events = _record(overlay)
    overlay.mousePressEvent(_Event(start, modifiers))
    overlay.mouseMoveEvent(_Event(end, modifiers))
    overlay.mouseReleaseEvent(_Event(end, modifiers))
    return events


def test_hit_gives_nodes_before_tangents_and_tangents_before_the_corners():
    overlay = _overlay()
    handles = overlay.masks.handles()
    assert {"m_p0", "m_p3", "m_hi0", "m_ho3", "m_tl", "m_rotate", "m_feather"} <= set(
        handles
    )
    assert overlay._hit(handles["m_p1"]) == ("m_p1", "m1")
    assert overlay._hit(handles["m_ho0"]) == ("m_ho0", "m1")
    assert overlay._hit(handles["m_hi2"]) == ("m_hi2", "m1")
    assert overlay._hit(overlay.to_scene(640.0, 360.0)) == ("m_move", "m1")


def test_dragging_a_node_or_a_tangent_emits_nodes_then_one_release():
    overlay = _overlay()
    node = overlay.masks.handles()["m_p0"]
    events = _drag(overlay, node, QPointF(node.x() + 20.0, node.y() + 10.0))
    ((clip, mask, values),) = events["drag"]
    assert (
        (clip, mask) == ("v", "m1")
        and set(values) == {"points"}
        and len(values["points"]) == 4
    )
    assert len(events["release"]) == 1
    overlay = _overlay()
    tangent = overlay.masks.handles()["m_ho0"]
    events = _drag(overlay, tangent, QPointF(tangent.x() + 20.0, tangent.y() + 10.0))
    values = events["drag"][0][2]
    assert set(values) == {"handles"}
    h = values["handles"][0]
    assert h[0] == pytest.approx(-h[2]) and h[1] == pytest.approx(-h[3])  # symétrique
    overlay = _overlay()
    tangent = overlay.masks.handles()["m_ho0"]
    events = _drag(
        overlay,
        tangent,
        QPointF(tangent.x() + 20.0, tangent.y() + 10.0),
        Qt.AltModifier,
    )
    h = events["drag"][0][2]["handles"][0]
    assert (
        h[0] == pytest.approx(-K / 2) and h[1] == pytest.approx(0.0) and h[3] > 0.0
    )  # Alt : l'entrante reste


def test_alt_click_removes_a_node_and_is_refused_under_three_nodes():
    overlay = _overlay()
    node = overlay.masks.handles()["m_p2"]
    events = _record(overlay)
    event = _Event(node, Qt.AltModifier)
    overlay.mousePressEvent(event)
    overlay.mouseReleaseEvent(event)
    assert event.accepted and len(events["drag"]) == 1 and len(events["release"]) == 1
    values = events["drag"][0][2]
    assert len(values["points"]) == 3 and len(values["handles"]) == 3
    assert overlay._drag is None  # pas de glisser engagé
    triangle = _path(points=((0.0, -0.5), (0.5, 0.5), (-0.5, 0.5)))
    overlay = _overlay(triangle)
    events = _record(overlay)
    event = _Event(overlay.masks.handles()["m_p0"], Qt.AltModifier)
    overlay.mousePressEvent(event)
    assert events["drag"] == [] and events["release"] == [] and event.accepted


def test_alt_click_on_a_polygon_vertex_does_not_remove_it():
    overlay = _overlay(Mask(id="m1", shape=MaskShape.POLYGON))
    events = _record(overlay)
    overlay.mousePressEvent(_Event(overlay.masks.handles()["m_p0"], Qt.AltModifier))
    assert (
        events["drag"] == [] and overlay._drag is not None
    )  # un glisser ordinaire, pas une suppression


def test_double_click_on_the_outline_adds_a_node_and_on_a_node_toggles_it():
    overlay = _overlay()
    events = _record(overlay)
    contour = overlay.to_scene(
        640.0 + 320.0 * math.cos(math.pi / 4), 360.0 + 180.0 * math.sin(math.pi / 4)
    )
    event = _Event(contour)
    overlay.mouseDoubleClickEvent(event)
    ((_c, _m, values),) = events["drag"]
    assert (
        event.accepted
        and len(values["points"]) == 5
        and len(values["handles"]) == 5
        and len(events["release"]) == 1
    )
    assert values["points"][2] == pytest.approx((0.3536, 0.3536), abs=0.01)
    overlay = _overlay()
    events = _record(overlay)
    overlay.mouseDoubleClickEvent(_Event(overlay.masks.handles()["m_p1"]))
    assert events["drag"][0][2]["handles"][1] == (
        0.0,
        0.0,
        0.0,
        0.0,
    )  # lisse → anguleux
    overlay = _overlay(_path(points=mask_path.DEFAULT_NODES))
    events = _record(overlay)
    overlay.mouseDoubleClickEvent(_Event(overlay.masks.handles()["m_p1"]))
    assert events["drag"][0][2]["handles"][1] != (
        0.0,
        0.0,
        0.0,
        0.0,
    )  # anguleux → lisse


def test_double_click_elsewhere_or_on_a_locked_mask_does_nothing():
    overlay = _overlay()
    events = _record(overlay)
    overlay.mouseDoubleClickEvent(
        _Event(overlay.to_scene(640.0, 360.0))
    )  # au cœur de la forme, loin du contour
    overlay.mouseDoubleClickEvent(
        _Event(overlay.masks.handles()["m_br"])
    )  # un coin : le geste habituel
    assert events["drag"] == []
    locked = _overlay(editable=False)
    events = _record(locked)
    locked.mouseDoubleClickEvent(
        _Event(
            locked.to_scene(
                640.0 + 320.0 * math.cos(0.7), 360.0 + 180.0 * math.sin(0.7)
            )
        )
    )
    assert events["drag"] == [] and locked.masks.handles() == {}
    other = _overlay(
        Mask(id="m1", shape=MaskShape.POLYGON)
    )  # une autre forme : pas d'ajout de sommet
    events = _record(other)
    other.mouseDoubleClickEvent(_Event(other.to_scene(640.0, 180.0)))
    assert events["drag"] == []


def test_the_hovered_node_follows_the_pointer_and_is_forgotten_when_the_structure_changes():
    overlay = _overlay()
    overlay.hoverMoveEvent(_Event(overlay.masks.handles()["m_p2"]))
    assert overlay.masks.hovered_node() == 2
    overlay.hoverMoveEvent(_Event(overlay.to_scene(640.0, 360.0)))
    assert overlay.masks.hovered_node() is None
    overlay.hoverMoveEvent(_Event(overlay.masks.handles()["m_p2"]))
    assert (
        overlay.masks.remove_hovered_node() is True
        and overlay.masks.hovered_node() is None
    )
    assert overlay.masks.remove_hovered_node() is None  # plus de nœud survolé
    overlay.hoverMoveEvent(_Event(overlay.masks.handles()["m_p2"]))
    overlay.hoverLeaveEvent(QGraphicsSceneHoverEvent())
    assert overlay.masks.hovered_node() is None


# -- fenêtre principale ---------------------------------------------------------------------------------------


@pytest.fixture
def window(qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from ui.main_window import MainWindow

    monkeypatch.setattr(
        "ui.main_window.QMessageBox.information", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        "ui.main_window.QMessageBox.question", lambda *_a, **_k: QMessageBox.Yes
    )
    win = MainWindow()
    qtbot.addWidget(win)
    win.timeline_timer.stop()
    monkeypatch.setattr(win.preview_panel, "preview_at", lambda *_a, **_k: None)
    win.project = Project(name="Path", width=1280, height=720, fps=25.0)
    win.project.media_assets.append(
        MediaAsset("asset", "/tmp/clip.mp4", "clip", 6, 1280, 720, 25.0, "video")
    )
    masks = (_path(id="m1"), Mask(id="m2", shape=MaskShape.POLYGON), Mask(id="m3"))
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


def _show(win, clip_id="v", row=0):
    win.playhead_seconds = 1.0
    win.preview_panel.overlay.layer_clicked.emit(clip_id)
    win._refresh_viewer_graphics()
    group = win.properties_panel.compositing_group
    if group.mask_list.currentRow() == row:
        group.edit_in_monitor.setChecked(True)
    else:
        group.mask_list.setCurrentRow(row)
    return win.preview_panel.overlay


def _press_drag_release(overlay, start, end, modifiers=Qt.NoModifier):
    overlay.mousePressEvent(_Event(start, modifiers))
    overlay.mouseMoveEvent(_Event(end, modifiers))
    overlay.mouseReleaseEvent(_Event(end, modifiers))


def test_dragging_a_node_writes_it_in_one_undoable_step_without_touching_the_rest(
    window,
):
    overlay = _show(window)
    before, entries = _mask(window), len(window.history)
    node = overlay.masks.handles()["m_p0"]
    _press_drag_release(overlay, node, QPointF(node.x() + 20.0, node.y() + 10.0))
    after = _mask(window)
    assert after.points[0] != before.points[0] and after.points[1:] == before.points[1:]
    assert after.handles == before.handles and after.position_x == before.position_x
    assert len(window.history) == entries + 1
    assert window.history.entries()[-1].label == translate("history.mask.drag")
    window.undo_last()
    assert _mask(window).points == before.points and len(window.history) == entries


def test_dragging_a_tangent_writes_symmetric_handles_and_alt_breaks_the_symmetry(
    window,
):
    overlay = _show(window)
    tangent = overlay.masks.handles()["m_ho0"]
    _press_drag_release(
        overlay, tangent, QPointF(tangent.x() + 20.0, tangent.y() + 20.0)
    )
    h = _mask(window).handles[0]
    assert (
        h[0] == pytest.approx(-h[2])
        and h[1] == pytest.approx(-h[3])
        and h != mask_path.DEFAULT_TANGENTS[0]
    )
    window.undo_last()
    assert _mask(window).handles == mask_path.DEFAULT_TANGENTS
    overlay = _show(window)
    tangent = overlay.masks.handles()["m_ho0"]
    _press_drag_release(
        overlay,
        tangent,
        QPointF(tangent.x() + 20.0, tangent.y() + 20.0),
        Qt.AltModifier,
    )
    assert _mask(window).handles[0][:2] == pytest.approx(
        mask_path.DEFAULT_TANGENTS[0][:2]
    )


def test_adding_removing_and_smoothing_nodes_are_one_history_step_each(window):
    overlay = _show(window)
    entries = len(window.history)
    contour = overlay.to_scene(
        640.0 + 320.0 * math.cos(math.pi / 4), 360.0 + 180.0 * math.sin(math.pi / 4)
    )
    overlay.mouseDoubleClickEvent(_Event(contour))
    assert (
        len(_mask(window).points) == 5
        and len(_mask(window).handles) == 5
        and len(window.history) == entries + 1
    )
    window.undo_last()
    assert len(_mask(window).points) == 4 and len(window.history) == entries
    overlay = _show(window)
    overlay.mousePressEvent(_Event(overlay.masks.handles()["m_p3"], Qt.AltModifier))
    assert len(_mask(window).points) == 3 and len(window.history) == entries + 1
    window.undo_last()
    overlay = _show(window)
    overlay.mouseDoubleClickEvent(_Event(overlay.masks.handles()["m_p0"]))
    assert (
        _mask(window).handles[0] == (0.0, 0.0, 0.0, 0.0)
        and len(window.history) == entries + 1
    )


def test_delete_removes_the_hovered_node_and_never_the_clip(window, qtbot):
    overlay = _show(window)
    view = window.preview_panel.graphics_view
    overlay.hoverMoveEvent(_Event(overlay.masks.handles()["m_p1"]))
    override = QKeyEvent(QEvent.ShortcutOverride, Qt.Key_Delete, Qt.NoModifier)
    assert (
        window._mask_path_keys.eventFilter(view, override) and override.isAccepted()
    )  # la touche est réclamée au raccourci « supprimer le clip »
    press = QKeyEvent(QEvent.KeyPress, Qt.Key_Delete, Qt.NoModifier)
    assert window._mask_path_keys.eventFilter(view, press)
    assert len(_mask(window).points) == 3 and _clip(window) is not None
    window.undo_last()
    overlay = _show(window)
    overlay.hoverMoveEvent(
        _Event(overlay.to_scene(640.0, 360.0))
    )  # aucun nœud survolé : la touche garde son rôle
    for kind in (QEvent.ShortcutOverride, QEvent.KeyPress):
        assert not window._mask_path_keys.eventFilter(
            view, QKeyEvent(kind, Qt.Key_Delete, Qt.NoModifier)
        )
    assert len(_mask(window).points) == 4
    overlay.hoverMoveEvent(_Event(overlay.masks.handles()["m_p1"]))
    ripple = QKeyEvent(
        QEvent.KeyPress, Qt.Key_Backspace, Qt.ControlModifier
    )  # Ctrl+Retour arrière : « supprimer en refermant »
    assert (
        not window._mask_path_keys.eventFilter(view, ripple)
        and len(_mask(window).points) == 4
    )


def test_delete_under_three_nodes_is_refused_with_a_message_and_still_keeps_the_clip(
    window,
):
    overlay = _show(window)
    view = window.preview_panel.graphics_view
    window._on_mask_dragged(
        "v",
        "m1",
        {"points": _mask(window).points[:3], "handles": _mask(window).handles[:3]},
    )
    window._on_mask_released("v", "m1")
    window._refresh_viewer_overlay(1.0)
    overlay.hoverMoveEvent(_Event(overlay.masks.handles()["m_p0"]))
    entries = len(window.history)
    press = QKeyEvent(QEvent.KeyPress, Qt.Key_Backspace, Qt.NoModifier)
    assert window._mask_path_keys.eventFilter(view, press) and press.isAccepted()
    assert len(_mask(window).points) == 3 and len(window.history) == entries
    assert window.statusBar().currentMessage() == translate("mask.path.min_nodes")


def test_the_guards_refuse_nodes_the_model_would_not_accept(window):
    _show(window)
    entries, before = len(window.history), _mask(window)
    points, handles = before.points, before.handles
    for payload in (
        {"points": points[:2], "handles": handles[:2]},  # moins de 3 nœuds
        {"points": points[:3]},  # longueurs différentes
        {"points": ((9.0, 0.0),) + points[1:]},  # ancre hors bornes
        {"handles": ((9.0, 0.0, 0.0, 0.0),) + handles[1:]},  # tangente hors bornes
        {
            "points": ((0.0, 0.0),) * (mask_path.MAX_NODES + 1),
            "handles": ((0.0,) * 4,) * (mask_path.MAX_NODES + 1),
        },
        {"handles": ((float("nan"), 0.0, 0.0, 0.0),) + handles[1:]},
    ):
        window._on_mask_dragged("v", "m1", payload)
    window._on_mask_dragged(
        "v", "m2", {"handles": handles}
    )  # des tangentes pour un polygone
    window._on_mask_dragged("v", "m3", {"handles": handles})  # ni pour un rectangle
    window._on_mask_released("v", "m1")
    assert (
        _mask(window) == before
        and _mask(window, "m2").handles == ()
        and _mask(window, "m3").handles == ()
    )
    assert len(window.history) == entries and not window._viewer_dragging


def test_a_locked_track_or_layer_refuses_every_node_edit(window):
    overlay = _show(window)
    window.project.tracks[0].locked = True
    window._refresh_viewer_overlay(1.0)
    assert (
        overlay.masks.geometry is not None
        and not overlay.masks.geometry.editable
        and overlay.masks.handles() == {}
    )
    before, entries = _mask(window), len(window.history)
    overlay.mask_dragged.emit(
        "v",
        "m1",
        {"points": before.points[:3] + ((0.1, 0.1),), "handles": before.handles},
    )
    overlay.mask_dragged.emit("v", "m1", {"handles": before.handles[::-1]})
    overlay.mask_released.emit("v", "m1")
    overlay.mouseDoubleClickEvent(
        _Event(
            overlay.to_scene(
                640.0 + 320.0 * math.cos(0.7), 360.0 + 180.0 * math.sin(0.7)
            )
        )
    )
    overlay.hoverMoveEvent(_Event(overlay.to_scene(640.0, 180.0)))
    assert overlay.masks.hovered_node() is None
    press = QKeyEvent(QEvent.KeyPress, Qt.Key_Delete, Qt.NoModifier)
    assert not window._mask_path_keys.eventFilter(
        window.preview_panel.graphics_view, press
    )
    assert _mask(window) == before and len(window.history) == entries


def test_a_locked_graphic_layer_refuses_through_the_layer_rules(window):
    window.project.tracks.clear()
    track = Track("G1", "G1", "graphics")
    window.project.tracks.append(track)
    clip = add_graphic_clip(
        window.project,
        GraphicType.TEXT,
        timeline_start=0.0,
        duration=4.0,
        track=track,
        clip_id="g",
    )
    clip.compositing = Compositing(masks=(_path(id="m1"),))
    window._timeline_index = None
    window._reload_timeline_preserving_selection()
    window.history.reset(window.project)
    overlay = _show(window, "g")
    assert overlay.masks.geometry.editable
    set_layer_locked(window.project, "g", True)
    window._refresh_viewer_overlay(1.0)
    before, entries = _mask(window, "m1", "g"), len(window.history)
    overlay.mask_dragged.emit(
        "g", "m1", {"points": before.points, "handles": before.handles[::-1]}
    )
    assert _mask(window, "m1", "g") == before and len(window.history) == entries


def test_node_edits_do_not_animate_but_the_shape_properties_still_do(window):
    pid = mask_property_id("m1", "position_x")
    window.playhead_seconds = 0.0
    keyframe_editing.add_keyframe(window.project, "v", pid, 0.0, 0.5)
    overlay = _show(window)
    keyframes = len(_clip(window).animation)
    node = overlay.masks.handles()["m_p0"]
    _press_drag_release(overlay, node, QPointF(node.x() + 20.0, node.y() + 10.0))
    assert (
        len(_clip(window).animation) == keyframes
        and _mask(window).points[0] != mask_path.DEFAULT_NODES[0]
    )
    inside = overlay.to_scene(640.0, 360.0)
    _press_drag_release(overlay, inside, QPointF(inside.x() + 20.0, inside.y()))
    assert (
        len(_clip(window).animation) == keyframes + 1
    )  # position_x animée : une image clé de plus


def test_a_nested_clip_or_a_hidden_track_gets_no_node_edit(window):
    overlay = _show(window)
    _clip(window).enabled = False
    window._refresh_viewer_overlay(1.0)
    assert not overlay.masks.geometry.editable
    before = _mask(window)
    overlay.mask_dragged.emit(
        "v", "m1", {"points": before.points, "handles": before.handles[::-1]}
    )
    assert _mask(window) == before
    _clip(window).enabled = True
    _clip(window).sequence_id = "other"
    assert mh.mask_edit_refusal(window.project, "v", 1.0, "m1") == "nested"


# -- inspecteur ---------------------------------------------------------------------------------------------------


def test_the_inspector_offers_the_curve_and_adds_it_with_default_nodes(qtbot):
    from ui.compositing_editor import CompositingEditor

    editor = CompositingEditor("")
    qtbot.addWidget(editor)
    editor.show()
    editor.set_value(Compositing())
    assert editor.mask_shape.findData("path") >= 0
    seen = []
    editor.value_changed.connect(seen.append)
    editor.mask_shape.setCurrentIndex(editor.mask_shape.findData("path"))
    editor.add_mask.click()
    (mask,) = seen[-1].masks
    assert (
        mask.shape is MaskShape.PATH
        and mask.points == mask_path.DEFAULT_NODES
        and mask.handles == mask_path.DEFAULT_TANGENTS
    )
    assert editor.path_hint.isVisible()
    editor.mask_shape.setCurrentIndex(editor.mask_shape.findData("ellipse"))
    editor.add_mask.click()
    assert editor.mask_list.currentRow() == 1 and not editor.path_hint.isVisible()
    editor.mask_spins["feather"].setValue(
        10.0
    )  # un réglage de l'inspecteur garde les nœuds
    editor.mask_list.setCurrentRow(0)
    editor.mask_spins["feather"].setValue(10.0)
    assert seen[-1].masks[0].handles == mask_path.DEFAULT_TANGENTS and seen[-1].masks[
        0
    ].feather == pytest.approx(0.1)


def test_a_real_delete_key_press_removes_the_node_and_not_the_selected_clip(
    window, qtbot
):
    overlay = _show(window)
    view = window.preview_panel.graphics_view
    view.setFocus()
    window.activateWindow()
    overlay.hoverMoveEvent(_Event(overlay.masks.handles()["m_p1"]))
    qtbot.keyClick(view, Qt.Key_Delete)
    assert _clip(window) is not None and len(_mask(window).points) == 3
    assert any(c.id == "v" for t in window.project.tracks for c in t.clips)
