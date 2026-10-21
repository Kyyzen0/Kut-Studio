"""ADR-0006 lot 3a : calcul pur des poignées de masque (positions, glisser, bornes, droits d'édition)."""

from __future__ import annotations

import math
from dataclasses import replace

import pytest

from core import mask_handles as mh
from core.compositing import (
    MASK_PROPERTY_ORDER,
    MASK_PROPERTY_SPECS,
    Compositing,
    Mask,
    MaskShape,
)
from core.graphics import GraphicType, add_graphic_clip
from core.mograph_layers import set_layer_locked
from core.project_model import Clip, Project, Track

IDENT = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
BOX = (1000.0, 500.0)


def _geo(mask: Mask | None = None, world=IDENT, box=BOX, **kwargs) -> mh.MaskGeometry:
    mask = mask or Mask(width=0.4, height=0.2)
    return mh.geometry_of("clip", mask, world, box, **kwargs)


# -- positions ------------------------------------------------------------------------------------


def test_unrotated_rectangle_handles_sit_on_its_edges():
    geometry = _geo(Mask(position_x=0.5, position_y=0.5, width=0.4, height=0.2))
    assert mh.canvas_point(geometry, 0.0, 0.0) == pytest.approx((500.0, 250.0))
    assert mh.canvas_point(geometry, 0.5, 0.0) == pytest.approx(
        (700.0, 250.0)
    )  # côté droit : 0,2 × 1000
    assert mh.canvas_point(geometry, -0.5, -0.5) == pytest.approx(
        (300.0, 200.0)
    )  # coin haut-gauche
    assert set(mh.handle_units(geometry)) == {
        "tl",
        "tr",
        "br",
        "bl",
        "t",
        "r",
        "b",
        "l",
    }


def test_rotation_turns_the_handles_around_the_centre():
    geometry = _geo(Mask(width=0.4, height=0.2, rotation=90.0))
    assert mh.canvas_point(geometry, 0.5, 0.0) == pytest.approx(
        (500.0, 250.0 + 200.0)
    )  # le côté droit pointe vers le bas
    assert mh.canvas_point(geometry, 0.0, -0.5) == pytest.approx((500.0 + 50.0, 250.0))


def test_expansion_grows_the_drawn_shape():
    geometry = _geo(Mask(width=0.4, height=0.2, expansion=0.1))
    assert mh.canvas_point(geometry, 0.5, 0.0) == pytest.approx(
        (500.0 + 250.0, 250.0)
    )  # (0,4 + 0,1) × 1000 / 2


def test_the_world_matrix_places_the_layer_box_in_the_frame():
    world = (0.5, 0.0, 0.0, 0.5, 100.0, 40.0)  # calque réduit de moitié, décalé
    geometry = _geo(Mask(width=0.4, height=0.2), world=world)
    assert mh.canvas_point(geometry, 0.0, 0.0) == pytest.approx(
        (100.0 + 250.0, 40.0 + 125.0)
    )
    assert mh.canvas_point(geometry, 0.5, 0.0) == pytest.approx(
        (100.0 + 350.0, 40.0 + 125.0)
    )


def test_polygon_handles_are_its_vertices_and_corners_but_not_sides():
    mask = Mask(shape=MaskShape.POLYGON, width=0.5, height=0.5)
    geometry = _geo(mask)
    units = mh.handle_units(geometry)
    assert {name for name in units if name.startswith("p")} == {
        f"p{i}" for i in range(len(mask.points))
    }
    assert {"tl", "tr", "br", "bl"} <= set(units) and not {"t", "r", "b", "l"} & set(
        units
    )
    assert units["p0"] == tuple(mask.points[0])


def test_ellipse_outline_is_round_and_polygon_outline_is_its_points():
    ellipse = _geo(Mask(shape=MaskShape.ELLIPSE))
    assert all(
        math.hypot(u, v) == pytest.approx(0.5) for u, v in mh.outline_units(ellipse)
    )
    polygon = _geo(Mask(shape=MaskShape.POLYGON))
    assert mh.outline_units(polygon) == [tuple(p) for p in polygon.points]


def test_feather_distance_and_growth_follow_the_render_radius():
    geometry = _geo(Mask(width=0.4, height=0.2, feather=0.1))
    assert mh.feather_distance(geometry) == pytest.approx(50.0)  # 0,1 × min(1000, 500)
    gx, gy = mh.feather_growth(geometry)
    assert gx == pytest.approx(50.0 / 200.0) and gy == pytest.approx(50.0 / 50.0)
    assert mh.feather_growth(_geo(Mask(width=0.0, height=0.0, feather=0.1))) == (
        0.0,
        0.0,
    )


# -- déplacer -------------------------------------------------------------------------------------


def test_move_shifts_the_centre_by_the_pointer_delta_as_a_fraction_of_the_layer():
    edit = mh.drag(_geo(), "move", (500.0, 250.0), (600.0, 300.0))
    assert edit.values == pytest.approx({"position_x": 0.6, "position_y": 0.6})


def test_move_through_a_scaled_layer_converts_screen_pixels_to_layer_pixels():
    geometry = _geo(
        world=(0.5, 0.0, 0.0, 0.5, 0.0, 0.0)
    )  # le calque est à moitié de taille à l'écran
    edit = mh.drag(
        geometry, "move", (100.0, 100.0), (150.0, 100.0)
    )  # 50 px écran = 100 px calque
    assert edit.values == pytest.approx({"position_x": 0.6})


def test_shift_locks_the_move_to_the_dominant_axis():
    edit = mh.drag(_geo(), "move", (0.0, 0.0), (100.0, 30.0), shift=True)
    assert set(edit.values) == {"position_x"}


def test_a_fast_move_sticks_to_the_position_bound():
    assert (
        mh.drag(_geo(), "move", (0.0, 0.0), (3000.0, 0.0)).values["position_x"] == 3.0
    )  # 0,5 + 3 = 3,5 → 3
    assert (
        mh.drag(_geo(), "move", (0.0, 0.0), (-9000.0, 0.0)).values["position_x"] == -2.0
    )
    assert (
        mh.drag(_geo(Mask(position_x=3.0)), "move", (0.0, 0.0), (500.0, 0.0)) is None
    )  # déjà à la borne : rien


def test_a_drag_that_changes_nothing_returns_none():
    assert mh.drag(_geo(), "move", (10.0, 10.0), (10.0, 10.0)) is None
    assert mh.drag(_geo(), "r", (700.0, 250.0), (700.0, 250.0)) is None


def test_a_singular_world_matrix_gives_nothing():
    assert (
        mh.drag(_geo(world=(0.0, 0.0, 0.0, 0.0, 5.0, 5.0)), "move", (0, 0), (5, 5))
        is None
    )
    assert mh.drag(_geo(box=(0.0, 100.0)), "move", (0, 0), (5, 5)) is None


# -- redimensionner -------------------------------------------------------------------------------


def test_side_handle_resizes_one_axis_around_the_centre():
    edit = mh.drag(_geo(), "r", (700.0, 250.0), (800.0, 250.0))
    assert edit.values == pytest.approx(
        {"width": 0.6}
    )  # demi-largeur 300 → largeur 600 / 1000


def test_dragging_the_left_side_is_symmetric():
    edit = mh.drag(_geo(), "l", (300.0, 250.0), (200.0, 250.0))
    assert edit.values == pytest.approx({"width": 0.6})


def test_corner_handle_resizes_both_axes():
    edit = mh.drag(_geo(), "br", (700.0, 300.0), (800.0, 350.0))
    assert edit.values == pytest.approx({"width": 0.6, "height": 0.4})


def test_shift_on_a_corner_keeps_the_proportions():
    edit = mh.drag(
        _geo(), "br", (700.0, 300.0), (800.0, 300.0), shift=True
    )  # largeur ×1,5, hauteur suivie
    assert edit.values["width"] == pytest.approx(0.6)
    assert edit.values["height"] == pytest.approx(0.3)
    assert edit.values["width"] / edit.values["height"] == pytest.approx(0.4 / 0.2)


def test_resize_accounts_for_the_mask_rotation():
    geometry = _geo(Mask(width=0.4, height=0.2, rotation=90.0))
    handle = mh.canvas_point(geometry, 0.5, 0.0)  # (500, 450) : en dessous du centre
    assert handle == pytest.approx((500.0, 450.0))
    edit = mh.drag(geometry, "r", handle, (500.0, 550.0))
    assert edit.values == pytest.approx(
        {"width": 0.6}
    )  # le côté « droit » suit l'axe tourné
    assert "height" not in edit.values


def test_resize_subtracts_the_expansion_so_the_drawn_size_is_the_pointer_size():
    geometry = _geo(Mask(width=0.4, height=0.2, expansion=0.1))
    edit = mh.drag(
        geometry, "r", (750.0, 250.0), (850.0, 250.0)
    )  # taille dessinée 700 → 0,7 ; expansion 0,1
    assert edit.values == pytest.approx({"width": 0.6})


def test_size_sticks_to_the_bounds_instead_of_being_refused():
    geometry = _geo(Mask(width=0.4, height=0.2, expansion=0.5))
    assert (
        mh.drag(geometry, "r", (700.0, 250.0), (520.0, 250.0)).values["width"] == 0.0
    )  # 0,04 − 0,5 < 0
    assert (
        mh.drag(_geo(), "r", (700.0, 250.0), (500.0 + 2100.0, 250.0)).values["width"]
        == 4.0
    )  # 4,2 → 4


def test_shift_corner_keeps_the_proportions_when_one_dimension_is_pinned():
    geometry = _geo(Mask(width=0.4, height=0.2))  # rapport 2
    edit = mh.drag(
        geometry, "br", (700.0, 300.0), (500.0 + 5000.0, 300.0), shift=True
    )  # largeur voudrait 10 → 4
    assert edit.values["width"] == 4.0
    assert edit.values["height"] == pytest.approx(2.0)  # 4 / 2 : pas de déformation
    assert edit.values["width"] / edit.values["height"] == pytest.approx(2.0)
    small = mh.drag(
        geometry, "br", (700.0, 300.0), (500.0, 250.0), shift=True
    )  # tout s'écrase à 0
    assert small.values == pytest.approx({"width": 0.0, "height": 0.0})


# -- pivoter ----------------------------------------------------------------------------------------


def test_rotate_turns_by_the_angle_swept_around_the_centre():
    edit = mh.drag(_geo(), "rotate", (700.0, 250.0), (500.0, 450.0))  # de 0° à 90°
    assert edit.values == pytest.approx(
        {"rotation": 90.0}
    ) and edit.turn == pytest.approx(90.0)


def test_rotate_shift_snaps_to_fifteen_degrees():
    edit = mh.drag(
        _geo(),
        "rotate",
        (700.0, 250.0),
        (
            500.0 + 200.0 * math.cos(math.radians(38)),
            250.0 + 200.0 * math.sin(math.radians(38)),
        ),
        shift=True,
    )
    assert edit.values["rotation"] == pytest.approx(45.0)


def test_rotation_stays_continuous_past_half_a_turn():
    assert mh.unwrap_turn(-170.0, 170.0) == pytest.approx(190.0)
    assert mh.unwrap_turn(10.0, 350.0) == pytest.approx(370.0)
    geometry = _geo()
    first = mh.drag(
        geometry, "rotate", (700.0, 250.0), (300.0, 249.0)
    )  # ≈ −179,7° (au-dessus)
    second = mh.drag(
        geometry, "rotate", (700.0, 250.0), (300.0, 251.0), previous_turn=first.turn
    )  # franchit 180°
    assert first.turn == pytest.approx(-179.71, abs=0.05)
    assert second.turn == pytest.approx(-180.29, abs=0.05)  # continu, pas +179,7


def test_rotation_beyond_the_model_bound_is_refused_not_pinned():
    geometry = _geo(Mask(rotation=3599.0))
    assert (
        mh.drag(geometry, "rotate", (700.0, 250.0), (500.0, 450.0)) is None
    )  # 3599 + 90 > 3600


# -- adoucissement ----------------------------------------------------------------------------------


def test_feather_grows_when_the_handle_is_pulled_outwards_along_the_mask_axis():
    edit = mh.drag(
        _geo(), "feather", (750.0, 250.0), (800.0, 250.0)
    )  # +50 px / min(1000, 500)
    assert edit.values == pytest.approx({"feather": 0.1})


def test_feather_follows_a_rotated_mask_axis():
    geometry = _geo(Mask(rotation=90.0))
    edit = mh.drag(geometry, "feather", (500.0, 500.0), (500.0, 550.0))
    assert edit.values == pytest.approx({"feather": 0.1})


def test_a_fast_feather_drag_reaches_exactly_zero_and_one():
    geometry = _geo(Mask(feather=0.5))
    assert (
        mh.drag(
            geometry, "feather", (700.0, 250.0), (700.0 + 0.6 * 500.0, 250.0)
        ).values["feather"]
        == 1.0
    )
    assert (
        mh.drag(
            geometry, "feather", (700.0, 250.0), (700.0 - 0.6 * 500.0, 250.0)
        ).values["feather"]
        == 0.0
    )
    assert mh.drag(
        geometry, "feather", (700.0, 250.0), (700.0 + 0.4 * 500.0, 250.0)
    ).values["feather"] == pytest.approx(0.9)


# -- sommets -----------------------------------------------------------------------------------------


def _polygon(**kwargs) -> mh.MaskGeometry:
    return _geo(Mask(shape=MaskShape.POLYGON, width=0.4, height=0.2, **kwargs))


def test_vertex_drag_gives_the_point_in_the_mask_frame():
    geometry = _polygon()
    start = mh.canvas_point(geometry, *geometry.points[0])
    edit = mh.drag(geometry, "p0", start, (500.0 + 100.0, 250.0 - 50.0))
    assert edit.values == {}
    assert edit.points[0] == pytest.approx((100.0 / 400.0, -50.0 / 100.0))
    assert edit.points[1:] == geometry.points[1:]


def test_vertex_drag_on_a_rotated_polygon_uses_the_rotated_frame():
    geometry = _polygon(rotation=90.0)
    target = mh.canvas_point(geometry, 0.25, -0.25)
    edit = mh.drag(geometry, "p2", (0.0, 0.0), target)
    assert edit.points[2] == pytest.approx((0.25, -0.25))


def test_vertex_sticks_to_plus_or_minus_two():
    geometry = _polygon()
    assert (
        mh.drag(geometry, "p0", (0.0, 0.0), (500.0 + 0.4 * 1000.0 * 2.1, 250.0)).points[
            0
        ][0]
        == mh.POINT_LIMIT
    )
    assert (
        mh.drag(geometry, "p0", (0.0, 0.0), (500.0 - 0.4 * 1000.0 * 9.0, 250.0)).points[
            0
        ][0]
        == -mh.POINT_LIMIT
    )
    assert mh.drag(
        geometry, "p0", (0.0, 0.0), (500.0 + 0.4 * 1000.0 * 1.9, 250.0)
    ).points[0][0] == pytest.approx(1.9)


def test_pinned_values_are_what_the_model_would_keep():
    """La valeur émise n'est jamais re-écrêtée par le modèle : poignée et valeur concordent."""
    edit = mh.drag(_geo(), "feather", (700.0, 250.0), (9000.0, 250.0))
    assert Mask(**edit.values).feather == edit.values["feather"]
    assert mh.out_of_bounds(edit.values) == []


def test_vertex_handles_only_exist_on_a_polygon_and_in_range():
    assert mh.drag(_geo(), "p0", (0, 0), (10, 10)) is None
    assert mh.drag(_polygon(), "p99", (0, 0), (10, 10)) is None
    assert (
        mh.drag(_geo(Mask(shape=MaskShape.POLYGON, width=0.0)), "p0", (0, 0), (10, 10))
        is None
    )


def test_bounds_mirror_the_model():
    """Si le modèle change une borne, la garde des poignées doit la suivre."""
    for name in MASK_PROPERTY_ORDER:
        spec = MASK_PROPERTY_SPECS[name]
        assert getattr(Mask(**{name: spec.maximum + 1.0}), name) == spec.maximum
        assert getattr(Mask(**{name: spec.minimum - 1.0}), name) == spec.minimum
    clamped = Mask(
        shape=MaskShape.POLYGON, points=((9.0, -9.0), (0.0, 0.0), (1.0, 1.0))
    ).points[0]
    assert clamped == (mh.POINT_LIMIT, -mh.POINT_LIMIT)


def test_out_of_bounds_names_the_offenders():
    assert mh.out_of_bounds({"feather": 1.2, "width": 0.5}) == ["feather"]
    assert mh.out_of_bounds({"width": float("nan")}) == ["width"]
    assert mh.out_of_bounds({}, ((0.0, 3.0),)) == ["points"]
    assert mh.out_of_bounds({"width": 4.0, "rotation": -3600.0}, ((2.0, -2.0),)) == []


def test_only_the_changed_properties_are_returned():
    edit = mh.drag(_geo(), "r", (700.0, 250.0), (800.0, 250.0))
    assert set(edit.values) == {"width"}


# -- droits d'édition ---------------------------------------------------------------------------------


def _video_project(**track_kwargs) -> tuple[Project, Clip, Track]:
    project = Project(name="Edit", width=1280, height=720, fps=25.0)
    mask = Mask(id="m1")
    clip = Clip(
        "v", "asset", "V1", 0.0, 0.0, 4.0, compositing=Compositing(masks=(mask,))
    )
    track = Track("V1", "V1", "video", clips=[clip], **track_kwargs)
    project.tracks.append(track)
    return project, clip, track


def test_an_editable_video_clip_has_no_refusal():
    project, _clip, _track = _video_project()
    assert mh.mask_edit_refusal(project, "v", 1.0, "m1") is None


@pytest.mark.parametrize(
    ("case", "reason"),
    [
        ("track_locked", "locked"),
        ("hidden_track", "hidden"),
        ("disabled", "hidden"),
        ("nested", "nested"),
        ("before", "outside"),
        ("after", "outside"),
        ("no_mask", "missing"),
        ("no_clip", "missing"),
    ],
)
def test_video_refusals(case, reason):
    project, clip, track = _video_project()
    t, mask_id, clip_id = 1.0, "m1", "v"
    if case == "track_locked":
        track.locked = True
    elif case == "hidden_track":
        track.visible = False
    elif case == "disabled":
        clip.enabled = False
    elif case == "nested":
        clip.sequence_id = "seq"
    elif case == "before":
        clip.timeline_start = 2.0
    elif case == "after":
        t = 4.0
    elif case == "no_mask":
        mask_id = "zz"
    else:
        clip_id = "ghost"
    assert mh.mask_edit_refusal(project, clip_id, t, mask_id) == reason


def test_a_solo_on_another_video_track_hides_this_clip():
    project, _clip, _track = _video_project()
    project.tracks.append(Track("V2", "V2", "video", solo=True))
    assert mh.mask_edit_refusal(project, "v", 1.0, "m1") == "hidden"


def test_a_track_of_the_wrong_kind_is_refused():
    project = Project(name="Audio", width=1280, height=720, fps=25.0)
    project.tracks.append(
        Track("A1", "A1", "audio", clips=[Clip("a", "asset", "A1", 0.0, 0.0, 4.0)])
    )
    assert mh.mask_edit_refusal(project, "a", 1.0) == "kind"


def test_graphic_layer_lock_is_honoured_through_the_layer_rules():
    project = Project(name="Gfx", width=1280, height=720, fps=25.0)
    track = Track("G1", "G1", "graphics")
    project.tracks.append(track)
    clip = add_graphic_clip(
        project,
        GraphicType.TEXT,
        timeline_start=0.0,
        duration=4.0,
        track=track,
        clip_id="g",
    )
    clip.compositing = Compositing(masks=(Mask(id="m1"),))
    assert mh.mask_edit_refusal(project, "g", 1.0, "m1") is None
    set_layer_locked(project, "g", True)
    assert mh.mask_edit_refusal(project, "g", 1.0, "m1") == "locked"
    set_layer_locked(project, "g", False)
    track.locked = True
    assert mh.mask_edit_refusal(project, "g", 1.0, "m1") == "locked"
    track.locked = False
    clip.graphic = replace(clip.graphic, visible=False)
    assert mh.mask_edit_refusal(project, "g", 1.0, "m1") == "hidden"


# -- masque suivi : le glisser part des valeurs saisies (revue de #147) ------------------------------------


def _tracked(shown_dx=0.1, **kwargs) -> mh.MaskGeometry:
    """Masque rendu décalé de ``shown_dx`` par un lien de suivi ; la saisie est ``Mask(...)``."""
    entered = Mask(width=0.4, height=0.2, **kwargs)
    shown = Mask(
        width=0.4, height=0.2, **{**kwargs, "position_x": entered.position_x + shown_dx}
    )
    return mh.geometry_of("clip", shown, IDENT, BOX, entered=entered)


def test_a_move_starts_from_the_entered_value_never_the_tracked_one():
    geometry = _tracked()
    assert geometry.values["position_x"] == pytest.approx(0.6) and geometry.entered[
        "position_x"
    ] == pytest.approx(0.5)
    edit = mh.drag(geometry, "move", (600.0, 250.0), (700.0, 250.0))
    assert edit.values["position_x"] == pytest.approx(
        0.5 + 0.1
    )  # saisie + delta, pas 0,6 + 0,1
    assert "position_y" not in edit.values


def test_handles_are_drawn_where_the_tracked_mask_is_rendered():
    geometry = _tracked()
    assert mh.canvas_point(geometry, 0.0, 0.0) == pytest.approx((600.0, 250.0))


def test_rotation_starts_from_the_entered_value():
    shown = Mask(width=0.4, height=0.2, rotation=30.0)
    geometry = mh.geometry_of(
        "clip", shown, IDENT, BOX, entered=Mask(width=0.4, height=0.2, rotation=10.0)
    )
    edit = mh.drag(
        geometry, "rotate", (700.0, 250.0), (500.0, 450.0)
    )  # +90° sur le rendu
    assert edit.values["rotation"] == pytest.approx(10.0 + 90.0)


def test_feather_and_size_start_from_the_entered_values():
    shown = Mask(
        width=0.8, height=0.4, feather=0.2
    )  # le suivi a doublé la taille (k = 2)
    geometry = mh.geometry_of(
        "clip", shown, IDENT, BOX, entered=Mask(width=0.4, height=0.2, feather=0.2)
    )
    feather = mh.drag(geometry, "feather", (900.0, 250.0), (950.0, 250.0))
    assert feather.values["feather"] == pytest.approx(0.2 + 50.0 / 500.0)
    handle = mh.canvas_point(geometry, 0.5, 0.0)
    resize = mh.drag(
        geometry, "r", handle, (handle[0] + 100.0, handle[1])
    )  # largeur rendue +200 px = saisie +100 px
    assert resize.values["width"] == pytest.approx(0.4 + 100.0 / 1000.0)


def test_the_entered_mask_ignores_the_tracking_derivation():
    from core.animation import Keyframe

    clip = Clip(
        "v",
        "asset",
        "V1",
        0.0,
        0.0,
        4.0,
        compositing=Compositing(masks=(Mask(id="m1", position_x=0.3),)),
    )
    pid = "mask.m1.position_x"
    clip.animation = [Keyframe(pid, 0.0, 0.3)]
    assert mh.entered_mask(clip, "m1", 1.0).position_x == pytest.approx(0.3)
    assert mh.entered_mask(clip, "zz", 1.0) is None
