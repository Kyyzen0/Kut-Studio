"""Moniteur : de VRAIS clics Qt (QTest) sur la surcouche, formes qui se recouvrent.

Le défaut corrigé : ``ViewerOverlay.shape()`` empilait ses formes dans un ``QPainterPath`` en ``OddEvenFill`` ; là où un
nombre PAIR de formes se recouvrent (deux vidéos plein cadre, un calque sur un autre, le disque d'une poignée sur la boîte de
sa sélection) le point était « hors forme » et Qt ne livrait pas le clic. Les tests précédents appelaient ``_hit`` ou
émettaient ``layer_clicked`` à la main : ils ne passaient jamais par la livraison des événements de Qt.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QPainterPath, QPolygonF
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QGraphicsScene, QGraphicsView

from core.compositing import Mask, MaskShape
from core.mask_handles import geometry_of
from core.mograph_scene import box_corners

IDENT = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
FRAME = (1280.0, 720.0)
V1, V2 = ("v1", IDENT, FRAME), ("v2", IDENT, FRAME)
T1 = (
    "t1",
    (1.0, 0.0, 0.0, 1.0, 200.0, 100.0),
    (400.0, 300.0),
)  # canvas 200..600 x 100..400
T2 = (
    "t2",
    (1.0, 0.0, 0.0, 1.0, 1000.0, 500.0),
    (400.0, 300.0),
)  # canvas 1000..1400 x 500..800 (déborde du cadre)
T3 = (
    "t3",
    (1.0, 0.0, 0.0, 1.0, 400.0, 200.0),
    (400.0, 300.0),
)  # canvas 400..800 x 200..500
BOXES = [V1, V2, T1, T3, T2]


@pytest.fixture
def stage(qtbot):
    """Une vraie ``QGraphicsView`` hors écran portant une surcouche (cadre 640x360 dans une scène 800x500)."""
    from ui.viewer_overlay import ViewerOverlay

    scene = QGraphicsScene(0, 0, 800, 500)
    overlay = ViewerOverlay()
    overlay.set_canvas(QRectF(0, 0, 640, 360), (1280.0, 720.0))
    scene.addItem(overlay)
    view = QGraphicsView(scene)
    view._scene_owner = (
        scene  # la vue ne possède pas la scène : sans référence, Python la détruit
    )
    view.setFixedSize(804, 504)
    view.setAlignment(
        Qt.AlignLeft | Qt.AlignTop
    )  # coordonnées de la vue = coordonnées de la scène
    view.setFrameShape(QGraphicsView.NoFrame)
    view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    qtbot.addWidget(view)
    view.show()
    qtbot.waitExposed(view)
    return view, overlay


def _selection(box_entry):
    from ui.viewer_overlay import SelectionGeometry

    clip_id, world, box = box_entry
    return SelectionGeometry(
        clip_id,
        world,
        IDENT,
        box,
        {
            "position_x": 0.0,
            "position_y": 0.0,
            "scale": 1.0,
            "anchor_x": 0.5,
            "anchor_y": 0.5,
            "scale_x": 1.0,
        },
    )


def _click(view, scene_point: QPointF) -> None:
    QTest.mouseClick(
        view.viewport(),
        Qt.LeftButton,
        Qt.NoModifier,
        QPoint(round(scene_point.x()), round(scene_point.y())),
    )


def _picks(view, overlay):
    got: list[str] = []
    overlay.layer_clicked.connect(got.append)
    return got


@pytest.mark.parametrize(
    "canvas_point, expected, count",
    [
        ((40.0, 40.0), "v2", 2),  # coin : seulement les deux vidéos plein cadre
        ((500.0, 300.0), "t3", 4),  # centre : v1, v2, t1 et t3 se recouvrent
        ((1340.0, 760.0), "t2", 1),  # hors cadre, sous un seul calque
        ((2.0, 200.0), "v2", 2),  # bord gauche du cadre
    ],
)
def test_a_real_click_reaches_the_overlay_and_picks_the_top_box(
    stage, canvas_point, expected, count
):
    view, overlay = stage
    overlay.set_layer_boxes(BOXES)
    point = overlay.to_scene(*canvas_point)
    covering = [
        cid
        for cid, world, box in BOXES
        if QPolygonF(
            [overlay.to_scene(*p) for p in box_corners(world, *box)]
        ).containsPoint(point, Qt.WindingFill)
    ]
    assert len(covering) == count
    assert overlay in view.items(
        QPoint(round(point.x()), round(point.y()))
    )  # Qt livre bien le clic à la surcouche
    got = _picks(view, overlay)
    _click(view, point)
    assert got == [expected]


def test_every_overlap_parity_is_inside_the_shape_in_both_orientations(stage):
    """1, 2, 3 et 4 boîtes superposées, dans les deux sens (calque retourné : échelle négative)."""
    _view, overlay = stage
    flipped = (
        -1.0,
        0.0,
        0.0,
        1.0,
        1280.0,
        0.0,
    )  # miroir horizontal : polygone de sens inverse
    for stack in range(1, 5):
        for world in (IDENT, flipped):
            overlay.set_layer_boxes(
                [(f"b{i}", world if i % 2 == 0 else IDENT, FRAME) for i in range(stack)]
            )
            assert overlay.shape().contains(overlay.to_scene(640.0, 360.0)), (
                stack,
                world,
            )
    overlay.set_layer_boxes([("a", flipped, FRAME), ("b", IDENT, FRAME)])
    assert overlay.shape().contains(overlay.to_scene(100.0, 100.0))


def test_a_real_click_on_a_flipped_layer_over_a_normal_one(stage):
    view, overlay = stage
    flipped = (-1.0, 0.0, 0.0, 1.0, 1280.0, 0.0)
    overlay.set_layer_boxes([("a", IDENT, FRAME), ("b", flipped, FRAME)])
    got = _picks(view, overlay)
    _click(view, overlay.to_scene(300.0, 200.0))
    assert got == ["b"]


# -- poignées de la sélection ---------------------------------------------------------------------


def _toward(point: QPointF, target: QPointF, distance: float) -> QPointF:
    dx, dy = target.x() - point.x(), target.y() - point.y()
    length = (dx * dx + dy * dy) ** 0.5 or 1.0
    return QPointF(
        point.x() + dx / length * distance, point.y() + dy / length * distance
    )


def test_inner_half_of_a_selection_handle_is_clickable_and_a_real_drag_changes_the_value(
    stage,
):
    view, overlay = stage
    overlay.set_layer_boxes([V1, V2, T1, T3])
    overlay.selection = _selection(T1)
    handle = overlay._handle_points(overlay.selection)["br"]
    centre = overlay.to_scene(400.0, 250.0)
    inner = _toward(
        handle, centre, 4.0
    )  # moitié INTÉRIEURE du disque (sur la boîte, sous T3 et les vidéos)
    assert overlay.shape().contains(inner)
    assert overlay._hit(inner) == ("br", "t1")
    dragged: list = []
    overlay.transform_dragged.connect(
        lambda clip_id, values: dragged.append((clip_id, values))
    )
    p = QPoint(round(inner.x()), round(inner.y()))
    QTest.mousePress(view.viewport(), Qt.LeftButton, Qt.NoModifier, p)
    QTest.mouseMove(view.viewport(), p + QPoint(30, 20))
    QTest.mouseRelease(
        view.viewport(), Qt.LeftButton, Qt.NoModifier, p + QPoint(30, 20)
    )
    assert dragged and dragged[-1][0] == "t1" and dragged[-1][1]


def test_a_real_drag_inside_the_selection_moves_it(stage):
    view, overlay = stage
    overlay.set_layer_boxes([V1, V2, T1])
    overlay.selection = _selection(T1)
    dragged: list = []
    overlay.transform_dragged.connect(
        lambda clip_id, values: dragged.append((clip_id, values))
    )
    start = overlay.to_scene(300.0, 150.0)
    p = QPoint(round(start.x()), round(start.y()))
    QTest.mousePress(view.viewport(), Qt.LeftButton, Qt.NoModifier, p)
    QTest.mouseMove(view.viewport(), p + QPoint(25, 10))
    QTest.mouseRelease(
        view.viewport(), Qt.LeftButton, Qt.NoModifier, p + QPoint(25, 10)
    )
    assert dragged and dragged[-1][0] == "t1"
    assert dragged[-1][1]["position_x"] > 0.0


# -- poignées de masque (rectangle) et de courbe --------------------------------------------------


WAVY_NODES = ((-0.4, -0.3), (0.1, -0.5), (0.45, 0.0), (0.2, 0.45), (-0.35, 0.3))


@pytest.mark.parametrize(
    "mask, handle_name",
    [
        (Mask(id="m1"), "m_br"),
        (Mask(id="m1", shape=MaskShape.PATH, points=WAVY_NODES), "m_p1"),
    ],
)
def test_mask_handles_are_clickable_through_the_layer_boxes_above_them(
    stage, mask, handle_name
):
    view, overlay = stage
    overlay.set_layer_boxes([V1, V2])
    overlay.set_mask_edit(geometry_of("v1", mask, IDENT, FRAME, editable=True))
    handle = overlay.masks.handles()[handle_name]
    inner = _toward(handle, overlay.to_scene(640.0, 360.0), 3.0)
    assert overlay.shape().contains(inner)
    assert overlay._hit(inner)[0] == handle_name
    dragged: list = []
    released: list = []
    overlay.mask_dragged.connect(lambda *args: dragged.append(args))
    overlay.mask_released.connect(lambda *args: released.append(args))
    p = QPoint(round(inner.x()), round(inner.y()))
    QTest.mousePress(view.viewport(), Qt.LeftButton, Qt.NoModifier, p)
    QTest.mouseMove(view.viewport(), p + QPoint(-20, -15))
    QTest.mouseRelease(
        view.viewport(), Qt.LeftButton, Qt.NoModifier, p + QPoint(-20, -15)
    )
    assert dragged and released


def test_tracking_overlay_zones_are_not_holes_in_its_clickable_area():
    """Même défaut (``OddEvenFill``) : la zone de recherche d'un tracker, posée sur le cadre, était un trou."""
    from ui.tracking_overlay import OverlayTracker, TrackingOverlay

    overlay = TrackingOverlay()
    overlay.set_canvas(QRectF(0, 0, 640, 360), (1280.0, 720.0))
    overlay.set_state(
        active=True,
        mapping=IDENT,
        show_paths=True,
        trackers=[
            OverlayTracker("a", "A", "#ff0000", point=(640.0, 360.0)),
            OverlayTracker("b", "B", "#00ff00", point=(660.0, 370.0)),
        ],
    )
    assert overlay.shape().contains(
        overlay.to_scene(580.0, 330.0)
    )  # cadre + zone A seulement : 2 formes
    assert overlay.shape().contains(
        overlay.to_scene(640.0, 360.0)
    )  # cadre + zones A et B : 3 formes
    assert overlay.shape().contains(overlay.to_scene(20.0, 20.0))  # le cadre seul


# -- polygones qui se croisent, concaves, dégénérés (revue de #152) -------------------------------------


def _poly(*coords) -> QPolygonF:
    return QPolygonF([QPointF(*c) for c in coords])


BOARD = _poly((-50, -50), (150, -50), (150, 150), (-50, 150))
BOWTIE = (
    (0, 0),
    (100, 100),
    (100, 0),
    (0, 100),
)  # lobes à gauche et à droite, centre en (50, 50)
LOBES = ((10, 50), (90, 50))
OUTSIDE_LOBES = ((50, 10), (50, 90))


def _accumulate(*polygons) -> QPainterPath:
    from ui.hit_shape import add_polygon, new_hit_path

    path = new_hit_path()
    for polygon in polygons:
        add_polygon(path, polygon)
    return path


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("board_first", [False, True])
def test_a_bow_tie_over_a_layer_box_keeps_both_lobes_clickable(reverse, board_first):
    points = BOWTIE[::-1] if reverse else BOWTIE
    polygons = (BOARD, _poly(*points)) if board_first else (_poly(*points), BOARD)
    path = _accumulate(*polygons)
    assert all(
        path.contains(QPointF(*p)) for p in LOBES + OUTSIDE_LOBES
    )  # la boîte couvre tout, lobes compris
    alone = _accumulate(_poly(*points))
    assert [alone.contains(QPointF(*p)) for p in LOBES + OUTSIDE_LOBES] == [
        True,
        True,
        False,
        False,
    ]


def test_a_star_keeps_its_odd_even_hole_but_the_box_under_it_still_counts():
    import math

    star = _poly(
        *[
            (
                100 + 80 * math.cos(math.radians(-90 + 144 * k)),
                100 + 80 * math.sin(math.radians(-90 + 144 * k)),
            )
            for k in range(5)
        ]
    )
    frame = _poly((0, 0), (200, 0), (200, 200), (0, 200))
    assert not _accumulate(star).contains(
        QPointF(100, 100)
    )  # le rendu pair-impair laisse le centre vide
    assert _accumulate(star).contains(QPointF(100, 30))
    for polygons in ((star, frame), (frame, star)):
        assert _accumulate(*polygons).contains(QPointF(100, 100))


@pytest.mark.parametrize(
    "coords, inside, outside",
    [
        (
            ((0, 0), (100, 0), (100, 100), (50, 40), (0, 100)),
            (10, 10),
            (50, 90),
        ),  # concave simple
        (((0, 0), (100, 0), (100, 100), (0, 100)), (50, 50), (150, 50)),  # convexe
        (
            ((0, 0), (100, 0), (100, 0), (100, 100), (0, 100), (0, 100)),
            (50, 50),
            (150, 50),
        ),  # sommets répétés
    ],
)
def test_concave_convex_and_repeated_vertex_polygons_stay_exact_under_a_box(
    coords, inside, outside
):
    alone = _accumulate(_poly(*coords))
    assert alone.contains(QPointF(*inside)) and not alone.contains(QPointF(*outside))
    for polygons in ((_poly(*coords), BOARD), (BOARD, _poly(*coords))):
        assert _accumulate(*polygons).contains(QPointF(*inside))


def test_degenerate_polygons_add_nothing_and_do_not_fail():
    for coords in (
        ((0, 0), (0, 0), (0, 0)),
        ((0, 0), (50, 50), (100, 100)),
        ((0, 0), (10, 10)),
    ):
        _accumulate(_poly(*coords), BOARD)  # aucune exception
        assert _accumulate(_poly(*coords), BOARD).contains(
            QPointF(5, 5)
        )  # la boîte voisine reste intacte
    assert _accumulate(_poly((0, 0), (0, 0), (0, 0))).isEmpty()


def test_real_clicks_reach_both_lobes_of_a_bow_tie_polygon_mask_over_a_full_frame_video(
    stage,
):
    from core import mask_handles as mh

    view, overlay = stage
    overlay.set_layer_boxes(
        [V1]
    )  # une seule boîte : c'est là qu'un lobe de sens inverse s'annulait
    mask = Mask(
        id="m1",
        shape=MaskShape.POLYGON,
        points=((-0.4, -0.4), (0.4, 0.4), (0.4, -0.4), (-0.4, 0.4)),
    )
    geometry = geometry_of("v1", mask, IDENT, FRAME, editable=True)
    overlay.set_mask_edit(geometry)
    dragged: list = []
    overlay.mask_dragged.connect(lambda *args: dragged.append(args))
    for u in (-0.3, 0.3):  # lobe gauche puis lobe droit
        scene = overlay.to_scene(*mh.canvas_point(geometry, u, 0.0))
        assert overlay.masks.hit(scene) == "m_move"
        assert overlay in view.items(QPoint(round(scene.x()), round(scene.y())))
        p = QPoint(round(scene.x()), round(scene.y()))
        count = len(dragged)
        QTest.mousePress(view.viewport(), Qt.LeftButton, Qt.NoModifier, p)
        QTest.mouseMove(view.viewport(), p + QPoint(12, 8))
        QTest.mouseRelease(
            view.viewport(), Qt.LeftButton, Qt.NoModifier, p + QPoint(12, 8)
        )
        assert len(dragged) > count
