"""Accrochage à l'échelle et à la rotation dans le moniteur (ADR-0006, étape 1, lot 1b)."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QRectF, Qt

from core.canvas_guides import (
    Guide,
    GuideOrientation,
    snap_rotation,
    snap_scale_factor,
)

BOX = (100.0, 100.0, 300.0, 200.0)  # boîte 200×100, ancrage au centre
ANCHOR = (200.0, 150.0)


# -- fonctions pures ---------------------------------------------------------------------------


def test_scale_snaps_an_edge_onto_the_frame_edge():
    factor, lines = snap_scale_factor(BOX, ANCHOR, 10.75, 1280, 720, threshold=8)
    assert factor == pytest.approx(
        (1280 - 200) / 100
    )  # le bord droit tombe sur x = 1280
    assert [(line.orientation, line.position, line.source) for line in lines] == [
        (GuideOrientation.VERTICAL, 1280.0, "frame")
    ]


def test_scale_snaps_onto_another_layer_and_a_guide():
    factor, lines = snap_scale_factor(
        BOX,
        ANCHOR,
        4.97,
        1280,
        720,
        threshold=8,
        axes="x",
        layer_boxes=[(700, 0, 800, 50)],
    )
    assert factor == pytest.approx(
        5.0
    )  # bord droit (levier 100) sur le bord gauche du calque, x = 700
    assert (lines[0].source, lines[0].position) == ("layer", 700.0)
    guide = Guide(GuideOrientation.HORIZONTAL, 0.4)  # y = 288
    factor, lines = snap_scale_factor(
        BOX, ANCHOR, 2.73, 1280, 720, threshold=8, axes="y", guides=[guide]
    )
    assert factor == pytest.approx((288 - 150) / 50)
    assert (
        lines[0].orientation is GuideOrientation.HORIZONTAL
        and lines[0].source == "guide"
    )


def test_a_layer_already_flush_with_the_frame_is_not_glued_to_its_original_size():
    """Un fond / une vidéo plein cadre a ses bords sur ceux du cadre : l'échelle ne doit pas y rester collée (k' = 1)."""
    flush = (0.0, 0.0, 1280.0, 720.0)
    center = (640.0, 360.0)
    for factor in (1.004, 0.996, 1.01):
        assert snap_scale_factor(flush, center, factor, 1280, 720, threshold=8) == (
            factor,
            [],
        )


def test_scale_beyond_the_threshold_is_untouched():
    assert snap_scale_factor(BOX, ANCHOR, 1.3, 1280, 720, threshold=2) == (1.3, [])


def test_scale_never_returns_a_non_positive_factor():
    # Bord gauche (levier -100) : la seule cible sous le seuil est à droite de l'ancrage (k' < 0), refusée.
    box = (100.0, 100.0, 300.0, 200.0)
    assert (
        snap_scale_factor(
            box,
            ANCHOR,
            0.05,
            1280,
            720,
            threshold=4,
            axes="x",
            layer_boxes=[(205, 0, 205, 0)],
        )[0]
        > 0
    )
    factor, lines = snap_scale_factor(
        (100.0, 0.0, 200.0, 10.0),
        (200.0, 5.0),
        0.02,
        1280,
        720,
        threshold=4,
        axes="x",
        layer_boxes=[(204, 0, 204, 0)],
    )
    assert (factor, lines) == (0.02, [])


def test_scale_ignores_edges_on_the_anchor():
    assert snap_scale_factor(
        (0.0, 0.0, 100.0, 100.0),
        (0.0, 0.0),
        1.0,
        1280,
        720,
        threshold=8,
        axes="x",
        layer_boxes=[],
    ) == (1.0, [])
    # Seul le bord droit (levier 100) reste : 1.0 -> 1280 est loin, 0 est l'ancrage lui-même.
    factor, _ = snap_scale_factor(
        (0.0, 0.0, 100.0, 100.0), (0.0, 0.0), 12.78, 1280, 720, threshold=8, axes="x"
    )
    assert factor == pytest.approx(12.8)


def test_scale_axes_restrict_the_edges_considered():
    # À 10.75 le bord droit s'accroche en x ; limité à y, rien ne se passe.
    assert (
        snap_scale_factor(BOX, ANCHOR, 10.75, 1280, 720, threshold=8, axes="y")[0]
        == 10.75
    )
    assert snap_scale_factor(BOX, ANCHOR, 10.75, 1280, 720, threshold=8, axes="x")[
        0
    ] == pytest.approx(10.8)


def test_rotation_snaps_to_multiples_of_45():
    assert snap_rotation(44.0, 3.0) == (45.0, True)
    assert snap_rotation(2.0, 3.0) == (0.0, True)
    assert snap_rotation(50.0, 3.0) == (50.0, False)
    assert snap_rotation(-44.0, 3.0) == (-45.0, True)
    assert snap_rotation(-91.0, 3.0) == (-90.0, True)
    assert snap_rotation(-20.0, 3.0) == (-20.0, False)


# -- ViewerOverlay._drag_values ----------------------------------------------------------------


def _overlay():
    from ui.viewer_overlay import SelectionGeometry, ViewerOverlay

    overlay = ViewerOverlay()
    overlay.set_canvas(QRectF(0, 0, 640, 360), (1280.0, 720.0))
    world = (1.0, 0.0, 0.0, 1.0, 100.0, 100.0)
    values = {
        "position_x": 0.0,
        "position_y": 0.0,
        "scale": 1.0,
        "anchor_x": 0.5,
        "anchor_y": 0.5,
        "scale_x": 1.0,
        "scale_y": 1.0,
        "rotation": 0.0,
    }
    overlay.selection = SelectionGeometry(
        "c", world, (1, 0, 0, 1, 0, 0), (200.0, 100.0), values
    )
    drag = {
        "mode": "br",
        "clip_id": "c",
        "start": (300.0, 200.0),
        "world": world,
        "parent_world": (1, 0, 0, 1, 0, 0),
        "box": (200.0, 100.0),
        "values": dict(values),
    }
    return overlay, drag


def _with_vertical_guide(overlay):
    overlay.guides = [Guide(GuideOrientation.VERTICAL, 0.5)]  # x = 640
    overlay.show_guides = True


def test_corner_drag_snaps_the_box_edge_onto_a_guide():
    overlay, drag = _overlay()
    _with_vertical_guide(overlay)
    current = (200.0 + 4.37 * 100.0, 150.0 + 4.37 * 50.0)
    values = overlay._drag_values(drag, current, Qt.NoModifier)
    assert values["scale"] == pytest.approx(
        4.4
    )  # bord droit exactement sur le guide x = 640
    assert 200.0 + values["scale"] * 100.0 == pytest.approx(640.0)
    assert len(overlay._snap_lines) == 1


def test_a_snap_the_model_would_clamp_is_dropped_and_draws_no_line():
    """Revue de #144 : le bord droit tomberait sur x = 1280 à l'échelle 10,8, hors de la borne (10) : pas d'accrochage, pas de ligne."""
    overlay, drag = _overlay()
    values = overlay._drag_values(
        drag, (200.0 + 10.75 * 100.0, 150.0 + 10.75 * 50.0), Qt.NoModifier
    )
    assert values["scale"] == pytest.approx(10.75)
    assert overlay._snap_lines == []
    assert not overlay._reachable("scale", 0.01) and overlay._reachable("scale", 2.0)


@pytest.mark.parametrize("disable", ["flag", "alt"])
def test_scale_snap_is_off_with_the_switch_or_alt(disable):
    overlay, drag = _overlay()
    modifiers = Qt.NoModifier
    if disable == "flag":
        overlay.snapping = False
    else:
        modifiers = Qt.AltModifier
    values = overlay._drag_values(drag, (200.0 + 1075.0, 150.0 + 537.5), modifiers)
    assert values["scale"] == pytest.approx(10.75)
    assert overlay._snap_lines == []


def test_side_drag_snaps_only_without_rotation():
    overlay, drag = _overlay()
    drag["mode"] = "r"
    drag["start"] = (300.0, 150.0)
    _with_vertical_guide(overlay)
    values = overlay._drag_values(drag, (200.0 + 437.0, 150.0), Qt.NoModifier)
    assert values["scale_x"] == pytest.approx(4.4)
    rotated = (0.9, 0.1, -0.1, 0.9, 100.0, 100.0)  # b, c non nuls
    drag["world"] = rotated
    values = overlay._drag_values(drag, (200.0 + 437.0, 150.0), Qt.NoModifier)
    assert "scale_x" in values and overlay._snap_lines == []


def test_rotation_snaps_near_90_but_shift_keeps_15_degree_steps():
    import math

    overlay, drag = _overlay()
    drag["mode"] = "rotate"
    drag["start"] = (300.0, 150.0)  # angle 0 autour de l'ancrage (200, 150)
    near_90 = (
        200.0 + 100.0 * math.cos(math.radians(88)),
        150.0 + 100.0 * math.sin(math.radians(88)),
    )
    assert overlay._drag_values(drag, near_90, Qt.NoModifier)[
        "rotation"
    ] == pytest.approx(90.0)
    overlay.snapping = False
    assert overlay._drag_values(drag, near_90, Qt.NoModifier)[
        "rotation"
    ] == pytest.approx(88.0)
    overlay.snapping = True
    assert overlay._drag_values(drag, near_90, Qt.AltModifier)[
        "rotation"
    ] == pytest.approx(88.0)
    assert overlay._drag_values(drag, near_90, Qt.ShiftModifier)[
        "rotation"
    ] == pytest.approx(90.0)
    at_40 = (
        200.0 + 100.0 * math.cos(math.radians(40)),
        150.0 + 100.0 * math.sin(math.radians(40)),
    )
    assert overlay._drag_values(drag, at_40, Qt.ShiftModifier)[
        "rotation"
    ] == pytest.approx(45.0)
    at_30 = (
        200.0 + 100.0 * math.cos(math.radians(30)),
        150.0 + 100.0 * math.sin(math.radians(30)),
    )
    assert overlay._drag_values(drag, at_30, Qt.NoModifier)[
        "rotation"
    ] == pytest.approx(30.0)
