"""Scène motion graphics : ancrage, parentage, groupes, cycles, placement historique."""

from __future__ import annotations

import math

import pytest

from core.graphics import GraphicOverlay, GraphicType, LayerLayout, add_graphic_clip, update_graphic
from core.mograph_layers import scene_for_project
from core.mograph_scene import (
    GraphicsScene,
    local_matrix,
    mat_apply,
    mat_decompose,
    mat_invert,
    mat_mul,
    mat_rotate,
    mat_scale,
    mat_skew,
    mat_translate,
    transform_from_world,
)
from core.project_model import Clip, MediaAsset, Project, Track
from core.render_plan import GraphicLayer, build_render_plan
from core.visual_effects import ClipTransform, TransformKeyframe, evaluate_transform

CANVAS = (1000.0, 500.0)


def _values(**changes):
    return evaluate_transform(ClipTransform(**changes), (), 0.0)


def _project() -> Project:
    return Project(name="Scène", width=1000, height=500, fps=25.0)


def test_matrix_algebra_round_trips():
    m = mat_mul(mat_translate(10, 20), mat_mul(mat_rotate(30), mat_mul(mat_skew(10), mat_scale(2, -3))))
    tx, ty, rotation, sx, sy, skew = mat_decompose(m)
    assert (tx, ty) == pytest.approx((10, 20))
    assert rotation == pytest.approx(30) and skew == pytest.approx(10)
    assert (sx, sy) == pytest.approx((2, -3))
    point = mat_apply(m, 3.0, 4.0)
    assert mat_apply(mat_invert(m), *point) == pytest.approx((3.0, 4.0))


def test_anchor_is_the_pivot_of_rotation_and_scale():
    box = (200.0, 100.0)
    for anchor in ((0.5, 0.5), (0.0, 0.0), (1.0, 0.25)):
        local_anchor = (anchor[0] * box[0], anchor[1] * box[1])
        positions = set()
        for rotation, scale in ((0, 1.0), (45, 1.0), (90, 2.0), (-30, 0.5)):
            values = _values(anchor_x=anchor[0], anchor_y=anchor[1], rotation=rotation, scale=scale,
                             position_x=0.1, position_y=-0.2)
            m = local_matrix(values, box, CANVAS, CANVAS)
            positions.add(tuple(round(c, 6) for c in mat_apply(m, *local_anchor)))
        # Le point d'ancrage ne bouge pas quand on tourne ou échelonne…
        assert len(positions) == 1
        # … et il se trouve à la position (0 = centre du cadre).
        assert positions.pop() == pytest.approx((500 + 100, 250 - 100))


def test_skew_flip_and_axis_scale_compose():
    values = _values(scale=2.0, scale_x=0.5, scale_y=1.5, flip_h=True, skew=20.0)
    m = local_matrix(values, (100.0, 100.0), CANVAS, CANVAS)
    _tx, _ty, _rotation, sx, sy, skew = mat_decompose(m)
    assert abs(sx) == pytest.approx(1.0) and abs(sy) == pytest.approx(3.0)
    assert sx * sy < 0  # un miroir inverse l'orientation
    assert abs(skew) == pytest.approx(20.0)


def test_legacy_layout_reproduces_the_historic_top_left_placement():
    values = _values(position_x=0.1, position_y=0.2, rotation=30, scale=0.5)
    m = local_matrix(values, (200.0, 100.0), CANVAS, CANVAS, layout=LayerLayout.LEGACY)
    diagonal = math.hypot(100.0, 50.0)
    # Le centre de l'image tournée (carré de côté hypot) tombe à coin + diagonale / 2.
    assert mat_apply(m, 100.0, 50.0) == pytest.approx((100 + diagonal / 2, 100 + diagonal / 2))


def test_parenting_combines_position_rotation_scale_and_anchor():
    project = _project()
    parent = add_graphic_clip(project, "null", timeline_start=0, duration=4)
    child = add_graphic_clip(project, "shape", timeline_start=0, duration=4)
    parent.transform = ClipTransform(position_x=0.2, rotation=90, scale=2.0)
    parent.transform_keyframes = [TransformKeyframe("rotation", 0.0, 0.0), TransformKeyframe("rotation", 2.0, 90.0)]
    update_graphic(child, "parent_id", parent.id)
    scene = scene_for_project(project)
    child_world = scene.world_matrix(child.id, 2.0)
    expected = mat_mul(scene.world_matrix(parent.id, 2.0), local_matrix(
        scene.evaluate(child.id, 2.0).transform, scene.evaluate(child.id, 2.0).box,
        scene.evaluate(parent.id, 2.0).box, CANVAS,
    ))
    assert child_world == pytest.approx(expected)
    # Le parent tourne de 90° : l'enfant aussi.
    assert mat_decompose(child_world)[2] == pytest.approx(90.0)
    assert mat_decompose(scene.world_matrix(child.id, 0.0))[2] == pytest.approx(0.0)


def test_null_controller_passes_its_opacity_but_visible_parents_do_not():
    project = _project()
    controller = add_graphic_clip(project, "null", timeline_start=0)
    shape = add_graphic_clip(project, "shape", timeline_start=0)
    text = add_graphic_clip(project, "text", timeline_start=0)
    controller.transform = ClipTransform(opacity=0.5)
    shape.transform = ClipTransform(opacity=0.4)
    update_graphic(shape, "parent_id", controller.id)
    update_graphic(text, "parent_id", shape.id)
    scene = scene_for_project(project)
    assert scene.evaluate(shape.id, 0).opacity == pytest.approx(0.2)
    assert scene.evaluate(text.id, 0).opacity == pytest.approx(1.0)


def test_group_is_an_extra_parent_transform():
    project = _project()
    group = add_graphic_clip(project, "group", timeline_start=0)
    member = add_graphic_clip(project, "text", timeline_start=0)
    update_graphic(member, "group_id", group.id)
    group.transform = ClipTransform(position_x=0.1)
    scene = scene_for_project(project)
    assert scene.parent_of(member.id) == group.id
    assert scene.members(group.id) == [member.id]
    assert member.id not in scene.top_level() and group.id in scene.top_level()
    centre = mat_apply(scene.world_matrix(member.id, 0), 450, 90)  # centre de la boîte 900×180
    assert centre == pytest.approx((600.0, 250.0))


def test_corrupted_cycles_are_broken_never_recursive():
    project = _project()
    a = add_graphic_clip(project, "shape", timeline_start=0)
    b = add_graphic_clip(project, "shape", timeline_start=0)
    c = add_graphic_clip(project, "shape", timeline_start=0)
    update_graphic(a, "parent_id", b.id)
    update_graphic(b, "parent_id", c.id)
    update_graphic(c, "parent_id", a.id)  # A → B → C → A (fichier corrompu)
    scene = scene_for_project(project)
    assert {scene.parent_of(x.id) for x in (a, b, c)} == {""}
    scene.evaluate(a.id, 0.0)  # termine


def test_group_cannot_contain_itself():
    g1 = GraphicLayer("g1", "G1", 0, 0, 1, GraphicOverlay(type=GraphicType.GROUP, group_id="g2"))
    g2 = GraphicLayer("g2", "G1", 0, 0, 1, GraphicOverlay(type=GraphicType.GROUP, group_id="g1"))
    scene = GraphicsScene((g1, g2), 100, 100)
    assert scene.group_of("g1") == "" and scene.group_of("g2") == ""
    assert set(scene.top_level()) == {"g1", "g2"}


def test_transform_from_world_keeps_the_layer_in_place():
    box = (300.0, 120.0)
    current = ClipTransform(anchor_x=0.2, anchor_y=0.7, rotation=15, position_x=0.3)
    world = local_matrix(evaluate_transform(current, (), 0), box, CANVAS, CANVAS)
    parent_world = mat_mul(mat_translate(400, 100), mat_mul(mat_rotate(-40), mat_scale(1.5, 1.5)))
    rebased = transform_from_world(world, parent_world, box, (100.0, 100.0), CANVAS, current)
    rebuilt = mat_mul(parent_world, local_matrix(evaluate_transform(rebased, (), 0), box, (100.0, 100.0), CANVAS))
    assert rebuilt == pytest.approx(world, abs=1e-6)


def test_video_clip_can_be_a_parent_and_windowed_plans_keep_rig_parents():
    project = _project()
    project.media_assets.append(MediaAsset("a", "/tmp/v.mp4", "v", 10, 1000, 500, 25, "video"))
    video = Clip("v", "a", "V1", 0, 0, 10, transform=ClipTransform(position_x=0.25, scale=0.5))
    project.tracks.append(Track("V1", "V1", "video", clips=[video]))
    early = add_graphic_clip(project, "null", timeline_start=0, duration=1)
    late = add_graphic_clip(project, "text", timeline_start=5, duration=2)
    update_graphic(late, "parent_id", early.id)
    update_graphic(early, "parent_id", "v")
    plan = build_render_plan(project, window=(5.0, 5.5))
    roles = {layer.clip_id: layer.role for layer in plan.graphics_layers}
    assert roles == {late.id: "draw", early.id: "rig", "v": "rig"}
    window_scene = GraphicsScene(plan.graphics_layers, 1000, 500)
    full_scene = scene_for_project(project)
    assert window_scene.world_matrix(late.id, 5.2) == pytest.approx(full_scene.world_matrix(late.id, 5.2))
