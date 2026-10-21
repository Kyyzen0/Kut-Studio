"""Modèle motion graphics : transform avancé, animation générique, sérialisation."""

from __future__ import annotations

import json

import pytest

from core.animation import InterpolationType, Keyframe
from core.animation_targets import get_target, split_animation, targets_for, targets_for_clip
from core.blend_modes import BLEND_MODES, BlendMode, coerce_blend_mode, ffmpeg_blend_mode
from core.canvas_guides import Guide, add_guide
from core.compositing import (
    Compositing,
    Mask,
    MaskKeyframe,
    MaskMode,
    MaskShape,
    evaluate_mask_at,
    mask_property_id,
)
from core.graph_cycles import find_cycles, reachable, would_create_cycle
from core.graphics import GraphicOverlay, GraphicType, LayerLayout, ShapeKind, add_graphic_clip, update_graphic
from core.motion_blur import MotionBlurSettings
from core.project_io import CURRENT_VERSION, load_project, save_project
from core.project_model import Clip, MediaAsset, Project, Track
from core.timeline_operations import cut_clip
from core.visual_effects import (
    ADVANCED_TRANSFORM_PROPERTIES,
    ANIMATABLE_PROPERTIES,
    ClipTransform,
    TransformKeyframe,
    build_ffmpeg_expression,
    evaluate_transform,
)


def _project() -> Project:
    return Project(name="MG", width=640, height=360, fps=30.0)


# --- Graphe de cycles partagé ---------------------------------------------------------------


def test_cycle_helpers_detect_two_and_three_node_loops():
    graph = {"A": {"B"}, "B": {"C"}, "C": set()}
    assert reachable(graph, "A") == {"B", "C"}
    assert would_create_cycle(graph, "C", "A")  # A → B → C → A
    assert would_create_cycle({"A": {"B"}, "B": set()}, "B", "A")  # A → B → A
    assert would_create_cycle(graph, "A", "A")
    assert not would_create_cycle(graph, "A", "C")
    assert find_cycles({"A": {"B"}, "B": {"A"}, "C": {"C"}}) == [("A", "B"), ("C",)]


# --- Transform avancé ---------------------------------------------------------------------


def test_advanced_transform_is_neutral_by_default_and_validated():
    transform = ClipTransform()
    assert not transform.is_advanced
    assert (transform.anchor_x, transform.anchor_y, transform.scale_x, transform.skew) == (0.5, 0.5, 1.0, 0.0)
    flipped = transform.with_property("flip_h", 1.0)
    assert flipped.flip_h is True and flipped.is_advanced
    with pytest.raises(ValueError):
        ClipTransform(skew=120.0)
    with pytest.raises(ValueError):
        TransformKeyframe("anchor_x", 0.0, 9.0)


def test_every_transform_property_is_animatable_and_flip_holds():
    keyframes = [
        TransformKeyframe("anchor_x", 0.0, 0.0), TransformKeyframe("anchor_x", 1.0, 1.0),
        TransformKeyframe("flip_h", 0.0, 0.0), TransformKeyframe("flip_h", 1.0, 1.0),
        TransformKeyframe("scale_x", 0.0, 1.0), TransformKeyframe("scale_x", 2.0, 2.0),
    ]
    half = evaluate_transform(ClipTransform(), keyframes, 0.5)
    assert half.anchor_x == pytest.approx(0.5)
    assert half.flip_h is False  # booléen : maintien jusqu'à l'image-clé suivante
    assert evaluate_transform(ClipTransform(), keyframes, 1.0).flip_h is True
    assert half.scale_x == pytest.approx(1.25)
    # La même courbe en expression FFmpeg (maintien pour le booléen).
    expression = build_ffmpeg_expression("flip_h", 0.0, keyframes, time_var="t")
    assert "clip(" in expression


def test_registry_lists_advanced_properties_skew_only_for_graphics():
    video = [t.id for t in targets_for("video")]
    graphics = [t.id for t in targets_for("graphics")]
    assert video[:5] == list(ANIMATABLE_PROPERTIES)
    assert set(ADVANCED_TRANSFORM_PROPERTIES) - {"skew", "fill"} <= set(video)
    assert "skew" not in video and "skew" in graphics
    # Cadrage d'un média : pan animable sur la vidéo seulement ; « remplir » est un réglage, sans courbe.
    assert {"pan_x", "pan_y"} <= set(video) and not {"pan_x", "pan_y"} & set(graphics)
    assert "fill" not in video and "fill" not in graphics
    assert "graphic.tracking" in graphics and "graphic.tracking" not in video


def test_graphic_properties_apply_only_to_their_layer_type():
    project = _project()
    text = add_graphic_clip(project, "text", timeline_start=0)
    shape = add_graphic_clip(project, "shape", timeline_start=0, shape="rounded_rectangle")
    text_ids = {t.id for t in targets_for_clip(text, "graphics")}
    shape_ids = {t.id for t in targets_for_clip(shape, "graphics")}
    assert "graphic.tracking" in text_ids and "graphic.tracking" not in shape_ids
    assert "graphic.corner_radius" in shape_ids and "graphic.corner_radius" not in text_ids


def test_mask_properties_are_dynamic_targets_per_mask():
    project = _project()
    clip = add_graphic_clip(project, "shape", timeline_start=0)
    mask = Mask(shape=MaskShape.ELLIPSE)
    clip.compositing = Compositing(masks=(mask,))
    feather = mask_property_id(mask.id, "feather")
    assert feather in {t.id for t in targets_for_clip(clip, "graphics")}
    target = get_target(feather)
    target.set_static(clip, 0.25)
    assert clip.compositing.masks[0].feather == pytest.approx(0.25)
    target.set_keyframes(clip, [Keyframe(feather, 0.0, 0.0), Keyframe(feather, 2.0, 0.5)])
    assert target.value_at(clip, 1.0) == pytest.approx(0.25)


# --- Calques ----------------------------------------------------------------------------------


def test_one_layer_model_for_every_kind():
    project = _project()
    for kind in ("text", "shape", "solid", "group", "adjustment", "null"):
        clip = add_graphic_clip(project, kind, timeline_start=0)
        assert isinstance(clip.graphic, GraphicOverlay)
        assert clip.graphic.layout is LayerLayout.ANCHOR
    kinds = {c.graphic.type for c in project.tracks[0].clips}
    assert {GraphicType.GROUP, GraphicType.ADJUSTMENT, GraphicType.NULL} <= kinds
    assert all(c.graphic.is_container for c in project.tracks[0].clips if c.graphic.type in (
        GraphicType.GROUP, GraphicType.ADJUSTMENT, GraphicType.NULL))
    # Pile : chaque nouveau calque arrive au-dessus.
    orders = [c.graphic.z_order for c in sorted(project.tracks[0].clips, key=lambda c: c.graphic.z_order)]
    assert orders == sorted(set(orders))


def test_shape_and_text_fields_are_bounded():
    graphic = GraphicOverlay(type=GraphicType.SHAPE, shape="polygon", polygon_sides=200, corner_radius=-4,
                             tracking=10_000, align_h="diagonal", line_spacing=0)
    assert graphic.shape is ShapeKind.POLYGON and graphic.polygon_sides == 64
    assert graphic.corner_radius == 0 and graphic.tracking == 500 and graphic.align_h == "center"
    assert graphic.line_spacing == pytest.approx(0.3)


# --- Blend modes ------------------------------------------------------------------------------


def test_blend_mode_list_is_central_and_backward_compatible():
    names = {mode.value for mode in BLEND_MODES}
    assert {"normal", "multiply", "screen", "overlay", "darken", "lighten", "addition", "difference"} == names
    assert coerce_blend_mode("add") is BlendMode.ADD
    assert coerce_blend_mode("unknown") is BlendMode.NORMAL
    assert Compositing(blend_mode="addition").blend_mode is BlendMode.ADD  # valeur historique
    # Overlay du W3C = test sur le fond = « hardlight » de FFmpeg (calque en 1er flux).
    assert ffmpeg_blend_mode(BlendMode.OVERLAY) == "hardlight"


# --- Masques ----------------------------------------------------------------------------------


def test_mask_stack_round_trips_with_modes_points_and_animation(tmp_path):
    project = _project()
    clip = add_graphic_clip(project, "shape", timeline_start=0, duration=3)
    polygon = Mask(shape=MaskShape.POLYGON, points=((0, -0.5), (0.5, 0.5), (-0.5, 0.5)), mode=MaskMode.SUBTRACT)
    ellipse = Mask(shape=MaskShape.ELLIPSE, inverted=True, feather=0.2, mode=MaskMode.INTERSECT, name="Vignette")
    clip.compositing = Compositing(masks=(ellipse, polygon), blend_mode=BlendMode.SCREEN)
    pid = mask_property_id(ellipse.id, "expansion")
    clip.animation = [Keyframe(pid, 0.0, 0.0), Keyframe(pid, 2.0, 0.4, InterpolationType.EASE_OUT)]
    path = tmp_path / "masks.kut"
    save_project(project, str(path))
    loaded = load_project(str(path)).tracks[0].clips[0]
    assert loaded.compositing == clip.compositing
    assert [m.id for m in loaded.compositing.masks] == [ellipse.id, polygon.id]
    assert loaded.compositing.masks[1].points == polygon.points
    assert [kf.interpolation for kf in loaded.animation] == [InterpolationType.LINEAR, InterpolationType.EASE_OUT]


def test_legacy_mask_keyframes_migrate_without_changing_the_curve(tmp_path):
    project = _project()
    project.media_assets.append(MediaAsset("a", "/tmp/x.mp4", "x", 4, 16, 16, 30, "video"))
    legacy = Mask(width=0.5, keyframes=(MaskKeyframe("width", 1.0, 0.9), MaskKeyframe("width", 2.0, 0.1)))
    project.tracks.append(Track("V1", "V1", "video", clips=[
        Clip("c", "a", "V1", 0, 0, 3, compositing=Compositing(masks=(legacy,))),
    ]))
    path = tmp_path / "legacy.kut"
    save_project(project, str(path))
    loaded = load_project(str(path)).tracks[0].clips[0]
    mask = loaded.compositing.masks[0]
    assert mask.keyframes == ()  # converties vers le moteur central
    from core.animation_targets import animation_curves

    curves = animation_curves(loaded, prefix="mask.")
    for t, expected in ((0.5, 0.5), (1.0, 0.9), (1.5, 0.5), (2.5, 0.1)):
        assert evaluate_mask_at(mask, curves, t).width == pytest.approx(expected)


# --- Sérialisation / rétrocompatibilité ---------------------------------------------------------


def test_v15_round_trip_keeps_layers_hierarchy_guides_and_motion_blur(tmp_path):
    project = _project()
    controller = add_graphic_clip(project, "null", timeline_start=0)
    text = add_graphic_clip(project, "text", timeline_start=0)
    update_graphic(text, "parent_id", controller.id)
    update_graphic(text, "motion_blur", True)
    text.transform = ClipTransform(anchor_x=0.0, skew=12.0, flip_v=True)
    text.animation = [Keyframe("graphic.tracking", 0.0, 20.0), Keyframe("graphic.tracking", 1.0, 0.0)]
    add_guide(project.active_sequence, "horizontal", 0.25)
    project.active_sequence.motion_blur = MotionBlurSettings(enabled=False, shutter_angle=90, samples=12)
    path = tmp_path / "v15.kut"
    save_project(project, str(path))
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["version"] == CURRENT_VERSION == 17
    loaded = load_project(str(path))
    loaded_text = next(c for c in loaded.tracks[0].clips if c.id == text.id)
    assert loaded_text.graphic.parent_id == controller.id and loaded_text.graphic.motion_blur
    assert loaded_text.transform == text.transform
    assert loaded_text.animation == text.animation
    assert loaded.active_sequence.guides == project.active_sequence.guides
    assert loaded.active_sequence.motion_blur == MotionBlurSettings(False, 90.0, 12)
    assert isinstance(loaded.active_sequence.guides[0], Guide)


def test_plain_clip_json_is_unchanged_by_the_advanced_transform(tmp_path):
    project = _project()
    project.media_assets.append(MediaAsset("a", "/tmp/x.mp4", "x", 4, 16, 16, 30, "video"))
    project.tracks.append(Track("V1", "V1", "video", clips=[Clip("c", "a", "V1", 0, 0, 2)]))
    path = tmp_path / "plain.kut"
    save_project(project, str(path))
    clip = json.loads(path.read_text(encoding="utf-8"))["project"]["sequences"][0]["tracks"][0]["clips"][0]
    assert set(clip["transform"]) == {"position_x", "position_y", "scale", "rotation", "opacity"}


def test_v14_graphic_keeps_its_historic_top_left_placement(tmp_path):
    project = _project()
    clip = add_graphic_clip(project, "text", timeline_start=0)
    path = tmp_path / "old.kut"
    save_project(project, str(path))
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["version"] = 14
    graphic = raw["project"]["sequences"][0]["tracks"][0]["clips"][0]["graphic"]
    for key in list(graphic):
        if key not in {"type", "text", "source_path", "width", "height", "fill_color", "stroke_color",
                       "stroke_width", "shadow_color", "shadow_offset_x", "shadow_offset_y",
                       "font_family", "font_size"}:
            graphic.pop(key)
    raw["project"]["sequences"][0].pop("guides")
    raw["project"]["sequences"][0].pop("motion_blur")
    path.write_text(json.dumps(raw), encoding="utf-8")
    loaded = load_project(str(path))
    old = loaded.tracks[0].clips[0].graphic
    assert old.layout is LayerLayout.LEGACY and old.text == clip.graphic.text
    assert loaded.active_sequence.guides == [] and loaded.active_sequence.motion_blur == MotionBlurSettings()


# --- Découpe / trim de l'animation générique ------------------------------------------------------


def test_split_keeps_generic_animation_values():
    keyframes = [Keyframe("graphic.tracking", 0.0, 0.0, InterpolationType.EASE_IN_OUT),
                 Keyframe("graphic.tracking", 2.0, 40.0)]
    left, right = split_animation(keyframes, 0.5)
    from core.animation import AnimationCurve

    whole = AnimationCurve(keyframes)
    assert AnimationCurve(left).evaluate(0.25) == pytest.approx(whole.evaluate(0.25))
    assert AnimationCurve(right).evaluate(1.0) == pytest.approx(whole.evaluate(1.5))


def test_blade_cut_splits_mask_and_shape_animation():
    project = _project()
    clip = add_graphic_clip(project, "shape", timeline_start=0, duration=4)
    clip.animation = [Keyframe("graphic.width", 0.0, 100.0), Keyframe("graphic.width", 4.0, 500.0)]
    left, right = cut_clip(project, clip.id, 1.0)
    assert get_target("graphic.width").value_at(right, 1.0) == pytest.approx(300.0)
    assert get_target("graphic.width").value_at(left, 1.0) == pytest.approx(200.0)
