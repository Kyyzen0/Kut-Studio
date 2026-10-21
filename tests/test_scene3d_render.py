"""Rendu d'un clip « scène 3D » dans le moteur des calques (lot B2b) : temps de la scène, image, clés, plan, empreinte.

Sans FFmpeg : le dessin (``MographRenderer``), les flux d'images (``compose_graphics``) et le plan. Les exports réels sont dans
``tests/test_scene3d_export.py``.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

import kut3d
from core import mograph_ffmpeg, mograph_stream, scene3d
from core.export_engine import (
    ExportFormat,
    ExportPreset,
    ExportRequest,
    build_export_command,
)
from core.filter_graph import fingerprint_plan
from core.graphics import (
    GraphicOverlay,
    GraphicType,
    add_graphic_clip,
    graphic_from_dict,
    graphic_to_dict,
    update_graphic,
)
from core.mograph_raster import MographRenderer, scene_for_plan
from core.project_io import save_project
from core.render_plan import build_render_plan
from core.scene3d_card import CARD_TEXT
from core.timeline_operations import cut_clip, trim_clip_left
from core.visual_effects import TransformKeyframe
from tests.scene3d_refs import PROJECTS, REFERENCE_FINGERPRINTS, fingerprints
from tests.scene3d_render_samples import (
    FPS,
    SIZE,
    write_animated_scene,
    write_static_scene,
)
from tests.scene3d_samples import base_project

W, H = SIZE


@pytest.fixture(autouse=True)
def _fresh_caches(qapp):
    scene3d.clear_signature_cache()
    yield
    scene3d.clear_signature_cache()


def _project(tmp_path, scene: Path, **kwargs):
    project = base_project()
    project.fps = FPS
    clip = add_graphic_clip(
        project,
        GraphicType.SCENE3D,
        timeline_start=kwargs.get("start", 0.0),
        duration=kwargs.get("duration", 2.0),
        source_path=str(scene),
        clip_id="s3d",
    )
    return project, clip


def _renderer(project):
    plan = build_render_plan(project)
    return plan, MographRenderer(scene_for_plan(plan), W, H, fps=FPS)


def _graphic(scene: Path, **changes) -> GraphicOverlay:
    return GraphicOverlay(
        type=GraphicType.SCENE3D, source_path=str(scene), width=W, height=H, **changes
    )


def _alpha(image) -> np.ndarray:
    return np.array(
        [
            [image.pixelColor(x, y).alpha() for x in range(image.width())]
            for y in range(image.height())
        ]
    )


# --- Temps de la scène ----------------------------------------------------------------------------------------------------


def test_a_static_scene_is_always_at_time_zero_and_an_animated_one_follows_the_clip(
    tmp_path,
):
    static = write_static_scene(tmp_path)
    assert scene3d.is_animated(static) is False
    assert scene3d.scene_time_at(static, 3.0, 1.25) == 0.0
    folder = tmp_path / "anim"
    folder.mkdir()
    animated = write_animated_scene(folder)
    assert scene3d.is_animated(animated) is True
    assert scene3d.scene_time_at(animated, 0.0, 0.5) == 0.5
    assert scene3d.scene_time_at(animated, 2.0, 0.3333333) == pytest.approx(2.3333)
    assert (
        scene3d.scene_time_at(tmp_path / "absent.kut3d", 1.0, 1.0) == 2.0
    )  # illisible : pas présumée fixe


def test_cutting_and_trimming_an_animated_clip_keep_the_scene_time(tmp_path):
    scene = write_animated_scene(tmp_path)
    project, clip = _project(tmp_path, scene, start=1.0, duration=2.0)
    left, right = cut_clip(project, clip.id, 1.5)
    assert (right.source_in, right.timeline_start) == (pytest.approx(0.5), 1.5)
    graphics = scene_for_plan(build_render_plan(project))
    assert graphics.evaluated_graphic(left.id, 1.25).scene_time == pytest.approx(0.25)
    # La seconde moitié reprend exactement là où la première s'arrête.
    assert graphics.evaluated_graphic(left.id, 1.4999).scene_time == pytest.approx(
        0.4999, abs=1e-3
    )
    assert graphics.evaluated_graphic(right.id, 1.5).scene_time == pytest.approx(0.5)
    assert graphics.evaluated_graphic(right.id, 2.0).scene_time == pytest.approx(1.0)
    trim_clip_left(project, right.id, 2.0)
    graphics = scene_for_plan(build_render_plan(project))
    assert graphics.evaluated_graphic(right.id, 2.0).scene_time == pytest.approx(1.0)
    assert graphics.evaluated_graphic(right.id, 2.5).scene_time == pytest.approx(1.5)


def test_the_plan_layer_carries_the_clip_entry_point(tmp_path):
    scene = write_animated_scene(tmp_path)
    project, clip = _project(tmp_path, scene)
    clip.source_in = 0.75
    layer = build_render_plan(project).graphics_layers[0]
    assert layer.source_in == 0.75


def test_scene_time_is_a_runtime_field_that_never_reaches_a_project(tmp_path):
    scene = write_animated_scene(tmp_path)
    project, clip = _project(tmp_path, scene)
    graphic = clip.graphic
    assert "scene_time" not in graphic_to_dict(graphic)
    assert "scene_time=" not in repr(
        graphic
    )  # l'empreinte des plans repose sur ce repr
    with_time = replace(graphic, scene_time=0.7)
    assert with_time.scene_time == 0.7 and "scene_time" not in graphic_to_dict(
        with_time
    )
    assert (
        graphic_from_dict({**graphic_to_dict(graphic), "scene_time": 5.0}).scene_time
        == 0.0
    )
    with pytest.raises(ValueError, match="inconnue"):
        update_graphic(clip, "scene_time", 1.0)
    saved = tmp_path / "p.kut"
    save_project(project, saved)
    assert '"scene_time"' not in saved.read_text(encoding="utf-8")
    assert (
        replace(graphic, scene_time=-3.0).scene_time == 0.0
        and replace(graphic, scene_time=1e9).scene_time == 86400.0
    )


# --- Image ------------------------------------------------------------------------------------------------------------------


def test_the_scene_image_has_the_box_size_a_transparent_background_and_is_reproducible(
    tmp_path,
):
    scene = write_static_scene(tmp_path)
    image = scene3d.scene_image(_graphic(scene))
    assert (image.width(), image.height()) == SIZE and image.hasAlphaChannel()
    alpha = _alpha(image)
    assert (
        alpha[0, 0] == 0 and alpha[H // 2, W // 2] == 255
    )  # coin vide, centre sur le cube
    again = scene3d.clear_signature_cache() or scene3d.scene_image(_graphic(scene))
    assert bytes(image.constBits()) == bytes(
        again.constBits()
    )  # même scène, même image au bit près


def test_the_image_is_the_straight_alpha_frame_of_the_numpy_engine(tmp_path):
    scene = write_static_scene(tmp_path)
    frame = kut3d.render_frame(
        kut3d.load_scene(scene, allow_outside=False),
        t=0.0,
        size=SIZE,
        backend="numpy",
        samples=1,
    )
    image = scene3d.scene_image(_graphic(scene))
    pixel = image.pixelColor(
        W // 2, H // 2
    )  # pixelColor rend la couleur non prémultipliée
    assert (pixel.red(), pixel.green(), pixel.blue(), pixel.alpha()) == pytest.approx(
        tuple(frame.color[H // 2, W // 2]), abs=1
    )


def test_an_animated_scene_changes_with_its_time(tmp_path):
    scene = write_animated_scene(tmp_path)
    first = scene3d.scene_image(_graphic(scene, scene_time=0.0))
    later = scene3d.scene_image(_graphic(scene, scene_time=0.5))
    assert bytes(first.constBits()) != bytes(later.constBits())


def test_the_interactive_mode_renders_smaller_and_an_unknown_mode_is_refused(tmp_path):
    scene = write_static_scene(tmp_path)
    big = replace(_graphic(scene), width=1920, height=1080)
    assert scene3d.render_size((1920, 1080)) == (1920, 1080)
    assert scene3d.render_size((1920, 1080), "interactive") == (480, 270)
    assert scene3d.scene_image(big, (96, 54), "interactive").width() == 96
    with pytest.raises(ValueError, match="Mode"):
        scene3d.scene_image(_graphic(scene), mode="turbo")


@pytest.mark.parametrize(
    "box", [(8192, 8192), (20000, 20000), (8192, 2), (2, 8192), (3, 3), (0.2, 0.2)]
)
def test_the_render_size_respects_the_engine_limits(box):
    from kut3d.render.limits import MAX_IMAGE_SIDE, MAX_SAMPLED_PIXELS

    width, height = scene3d.render_size(box)
    assert 1 <= width <= MAX_IMAGE_SIDE and 1 <= height <= MAX_IMAGE_SIDE
    assert width * height * scene3d.RENDER_SAMPLES**2 <= MAX_SAMPLED_PIXELS
    kut3d.render.frame.check_request(
        (width, height), scene3d.RENDER_SAMPLES, 0.0
    )  # le moteur l'accepte


def test_the_loaded_scene_cache_is_bounded_and_revalidated(tmp_path):
    scene = write_static_scene(tmp_path)
    first, _animated = scene3d.load_scene(scene)
    assert scene3d.load_scene(scene)[0] is first  # resservie tant que rien ne change
    (tmp_path / "cube.obj").write_text(
        (tmp_path / "cube.obj").read_text() + "\n# modifié\n", encoding="utf-8"
    )
    assert scene3d.load_scene(scene)[0] is not first  # un fichier a changé : relue
    for index in range(scene3d._SCENES_LIMIT + 3):
        folder = tmp_path / f"s{index}"
        folder.mkdir()
        scene3d.load_scene(write_static_scene(folder))
    assert len(scene3d._SCENES) <= scene3d._SCENES_LIMIT


def test_an_unreadable_scene_raises_scene3d_error_and_draws_nothing(tmp_path, caplog):
    gone = tmp_path / "gone.kut3d"
    with pytest.raises(scene3d.Scene3DError, match="introuvable"):
        scene3d.scene_image(_graphic(gone))
    broken = tmp_path / "broken.kut3d"
    broken.write_text("{ pas du json", encoding="utf-8")
    with pytest.raises(scene3d.Scene3DError):
        scene3d.scene_image(_graphic(broken))
    from PySide6.QtGui import QImage, QPainter

    canvas = QImage(W, H, QImage.Format.Format_ARGB32_Premultiplied)
    canvas.fill(0)
    painter = QPainter(canvas)
    with caplog.at_level("WARNING", logger="kut_studio.scene3d"):
        scene3d.draw_scene(painter, _graphic(gone), W, H)  # ne lève jamais
    painter.end()
    assert _alpha(canvas).max() == 0 and "non rendue" in caplog.text


def test_an_undecodable_texture_draws_nothing_on_screen_instead_of_raising(
    tmp_path, caplog
):
    # À l'écran seulement : l'export, lui, refuse (tests/test_scene3d_strict_export.py).
    scene = write_static_scene(tmp_path, textured=True)
    (tmp_path / "tex.png").write_bytes(b"\x89PNG\r\n\x1a\nfake-image-bytes")
    from PySide6.QtGui import QImage, QPainter

    canvas = QImage(W, H, QImage.Format.Format_ARGB32_Premultiplied)
    canvas.fill(0)
    painter = QPainter(canvas)
    scene3d.draw_scene(painter, _graphic(scene), W, H)
    painter.end()
    assert _alpha(canvas).max() == 0


# --- Dessin dans le moteur des calques ---------------------------------------------------------------------------------


def test_the_layer_renderer_draws_the_cube_inside_the_box(tmp_path):
    project, clip = _project(tmp_path, write_static_scene(tmp_path))
    _plan, renderer = _renderer(project)
    alpha = _alpha(renderer.render((clip.id,), 0.5))
    assert (
        alpha[H // 2, W // 2] == 255 and alpha[0, 0] == 0 and alpha[H - 1, W - 1] == 0
    )
    assert 0 < (alpha > 0).sum() < alpha.size


def test_opacity_position_and_visibility_apply_to_a_scene_like_to_an_image_layer(
    tmp_path,
):
    project, clip = _project(tmp_path, write_static_scene(tmp_path))
    clip.transform_keyframes = [
        TransformKeyframe(property_name="opacity", time_seconds=0.0, value=1.0),
        TransformKeyframe(property_name="opacity", time_seconds=2.0, value=0.0),
        TransformKeyframe(property_name="position_x", time_seconds=0.0, value=0.0),
        TransformKeyframe(property_name="position_x", time_seconds=2.0, value=0.25),
    ]
    _plan, renderer = _renderer(project)
    start = _alpha(renderer.render((clip.id,), 0.0))
    middle = _alpha(renderer.render((clip.id,), 1.0))
    columns = lambda a: np.nonzero(a.max(axis=0))[0]  # noqa: E731
    assert abs(float(middle.max()) - 128) <= 3  # opacité animée : 1 -> 0, ici 0,5
    assert columns(middle).mean() - columns(start).mean() == pytest.approx(
        0.125 * W, abs=1.5
    )  # position animée
    assert (
        _alpha(renderer.render((clip.id,), 2.0)).max() == 0
    )  # entièrement transparent
    clip.graphic = replace(clip.graphic, visible=False)
    _plan, renderer = _renderer(project)
    assert _alpha(renderer.render((clip.id,), 0.5)).max() == 0  # calque masqué


def test_a_rotated_scene_layer_is_drawn_rotated(tmp_path):
    project, clip = _project(tmp_path, write_static_scene(tmp_path))
    flat = _alpha(_renderer(project)[1].render((clip.id,), 0.5))
    clip.transform = replace(clip.transform, rotation=45.0)
    turned = _alpha(_renderer(project)[1].render((clip.id,), 0.5))
    assert (turned > 0).sum() != (
        flat > 0
    ).sum()  # le cube tourné couvre une autre surface


# --- Clés d'image et déduplication ---------------------------------------------------------------------------------------------


def _count_renders(monkeypatch):
    calls: list[tuple] = []
    original = kut3d.render_frame

    def spy(scene, t=0.0, size=(640, 360), **kwargs):
        calls.append(
            (
                round(float(t), 4),
                tuple(size),
                kwargs.get("backend"),
                kwargs.get("samples"),
            )
        )
        return original(scene, t, size, **kwargs)

    monkeypatch.setattr(kut3d, "render_frame", spy)
    return calls


def _compose(plan, tmp_path, monkeypatch, *, duration=2.0):
    cache = tmp_path / "cache"
    cache.mkdir(exist_ok=True)
    monkeypatch.setattr(mograph_stream, "cache_directory", lambda: cache)
    parts: list[str] = []
    inputs: list[str] = []
    mograph_ffmpeg.compose_graphics(
        parts,
        plan,
        W,
        H,
        FPS,
        "base",
        lambda path: inputs.append(path) or len(inputs),
        duration=duration,
    )
    return parts, inputs, cache


def test_a_static_scene_is_rendered_once_whatever_the_number_of_frames(
    tmp_path, monkeypatch
):
    project, _clip = _project(tmp_path, write_static_scene(tmp_path), duration=2.0)
    calls = _count_renders(monkeypatch)
    _parts, inputs, cache = _compose(build_render_plan(project), tmp_path, monkeypatch)
    assert calls == [(0.0, SIZE, "numpy", 1)]  # 20 images, un seul rendu 3D
    assert len(list(cache.glob("f-*.png"))) == 1 and len(inputs) == 1


def test_a_static_scene_with_an_animated_transform_still_renders_the_3d_once(
    tmp_path, monkeypatch
):
    project, clip = _project(tmp_path, write_static_scene(tmp_path), duration=2.0)
    clip.transform_keyframes = [
        TransformKeyframe(property_name="position_x", time_seconds=0.0, value=0.0),
        TransformKeyframe(property_name="position_x", time_seconds=2.0, value=0.2),
    ]
    calls = _count_renders(monkeypatch)
    _parts, _inputs, cache = _compose(build_render_plan(project), tmp_path, monkeypatch)
    assert len(calls) == 1  # l'encre est posée par matrice
    assert len(list(cache.glob("f-*.png"))) == 20  # une image de sortie par pose


def test_an_animated_scene_is_rendered_once_per_distinct_instant(tmp_path, monkeypatch):
    project, _clip = _project(tmp_path, write_animated_scene(tmp_path), duration=1.0)
    calls = _count_renders(monkeypatch)
    _parts, _inputs, cache = _compose(
        build_render_plan(project), tmp_path, monkeypatch, duration=1.0
    )
    assert sorted(t for t, *_ in calls) == [
        round(index / FPS, 4) for index in range(10)
    ]
    assert all(call[1:] == (SIZE, "numpy", 1) for call in calls)
    assert len(list(cache.glob("f-*.png"))) == 10


def test_the_cache_is_reused_by_a_second_composition(tmp_path, monkeypatch):
    project, _clip = _project(tmp_path, write_animated_scene(tmp_path), duration=1.0)
    plan = build_render_plan(project)
    first = _count_renders(monkeypatch)
    _compose(plan, tmp_path, monkeypatch, duration=1.0)
    scene3d.clear_signature_cache()
    first.clear()
    _compose(plan, tmp_path, monkeypatch, duration=1.0)
    assert first == []  # les PNG nommés par empreinte existent


def test_the_content_texture_of_a_scene_is_used_only_when_the_scene_is_static(tmp_path):
    static_project, static_clip = _project(tmp_path, write_static_scene(tmp_path))
    assert (
        _renderer(static_project)[1].content_texture_eligible(static_clip.id, 0.5)
        is True
    )
    folder = tmp_path / "anim"
    folder.mkdir()
    animated_project, animated_clip = _project(tmp_path, write_animated_scene(folder))
    assert (
        _renderer(animated_project)[1].content_texture_eligible(animated_clip.id, 0.5)
        is False
    )


def test_frame_keys_hold_time_signature_size_engine_samples_and_version(
    tmp_path, monkeypatch
):
    project, clip = _project(tmp_path, write_animated_scene(tmp_path))
    _plan, renderer = _renderer(project)
    key = renderer.frame_key((clip.id,), 0.5)
    identity = scene3d.render_identity(
        renderer.scene.evaluate(clip.id, 0.5).graphic, (W, H)
    )
    assert identity == (
        0.5,
        scene3d.scene_signature(clip.graphic.source_path),
        SIZE,
        "numpy",
        1,
        scene3d.SCENE3D_RASTER_VERSION,
    )
    assert identity in key[3]
    assert (
        renderer.frame_key((clip.id,), 0.5) == key
        and renderer.frame_key((clip.id,), 0.6) != key
    )
    monkeypatch.setattr(
        scene3d, "SCENE3D_RASTER_VERSION", scene3d.SCENE3D_RASTER_VERSION + 1
    )
    assert (
        renderer.frame_key((clip.id,), 0.5) != key
    )  # un rendu changé renomme les images
    assert renderer.content_key(clip.id, 0.5) != MographRenderer(
        renderer.scene, W, H, fps=FPS
    ).content_key(clip.id, 0.4)


@pytest.mark.parametrize("target", ["cube.obj", "cube.mtl", "tex.png"])
def test_changing_any_file_of_the_scene_changes_its_signature_keys_and_fingerprint(
    tmp_path, target
):
    scene = write_static_scene(tmp_path, textured=True)
    project, clip = _project(tmp_path, scene)
    _plan, renderer = _renderer(project)
    key = renderer.frame_key((clip.id,), 0.5)
    content = renderer.content_key(clip.id, 0.5)
    fingerprint = fingerprint_plan(
        build_render_plan(project), width=W, height=H, fps=FPS
    )
    file = tmp_path / target
    if target == "tex.png":
        file.write_bytes(scene3d_png((255, 0, 0, 255)))
    else:
        file.write_text(
            file.read_text(encoding="utf-8") + "\n# changé\n", encoding="utf-8"
        )
    assert renderer.frame_key((clip.id,), 0.5) != key
    assert renderer.content_key(clip.id, 0.5) != content
    assert (
        fingerprint_plan(build_render_plan(project), width=W, height=H, fps=FPS)
        != fingerprint
    )


def scene3d_png(color):
    from tests.scene3d_render_samples import png_bytes

    return png_bytes(color)


# --- Empreinte du plan ----------------------------------------------------------------------------------------------------------


def test_the_fingerprint_of_a_plan_without_3d_is_the_one_computed_before_the_lot():
    assert fingerprints() == REFERENCE_FINGERPRINTS
    assert set(PROJECTS) == set(REFERENCE_FINGERPRINTS)


def test_a_3d_layer_adds_its_entry_point_version_and_engine_to_the_fingerprint(
    tmp_path,
):
    project, clip = _project(tmp_path, write_animated_scene(tmp_path))
    first = fingerprint_plan(build_render_plan(project), width=W, height=H, fps=FPS)
    clip.source_in = 0.5
    assert (
        fingerprint_plan(build_render_plan(project), width=W, height=H, fps=FPS)
        != first
    )


def test_the_fingerprint_follows_the_raster_version_of_3d_layers_only(
    tmp_path, monkeypatch
):
    project, _clip = _project(tmp_path, write_static_scene(tmp_path))
    plan = build_render_plan(project)
    before = fingerprint_plan(plan, width=W, height=H, fps=FPS)
    monkeypatch.setattr(
        scene3d, "SCENE3D_RASTER_VERSION", scene3d.SCENE3D_RASTER_VERSION + 1
    )
    assert fingerprint_plan(plan, width=W, height=H, fps=FPS) != before
    assert fingerprints() == REFERENCE_FINGERPRINTS


# --- Fichier manquant ou illisible ---------------------------------------------------------------------------------------------------


def _request(plan, tmp_path):
    return ExportRequest(
        render_plan=plan,
        output_path=str(tmp_path / "out.mp4"),
        format=ExportFormat.MP4_H264,
        preset=ExportPreset(name="t", resolution=SIZE, crf=30, audio_bitrate="64k"),
        fps=int(FPS),
    )


def _assert_card_on_screen_and_refusal_on_export(project, clip, tmp_path):
    plan = build_render_plan(project)  # ne lève pas
    layer = next(item for item in plan.graphics_layers if item.clip_id == clip.id)
    assert layer.graphic.type is GraphicType.TEXT and layer.graphic.text == CARD_TEXT
    assert (layer.graphic.width, layer.graphic.height) == (
        clip.graphic.width,
        clip.graphic.height,
    )
    assert plan.warnings and Path(clip.graphic.source_path).name in plan.warnings[0]
    assert plan.missing_media and "scène 3D" in plan.missing_media[0]
    _plan, renderer = _renderer(project)
    assert (
        _alpha(renderer.render((clip.id,), 0.5)).max() > 0
    )  # la carte se voit à l'écran
    with pytest.raises(ValueError, match="Média introuvable"):
        build_export_command(_request(plan, tmp_path))  # refus à l'export


def test_a_deleted_scene_file_shows_a_card_and_refuses_the_export(tmp_path):
    scene = write_static_scene(tmp_path)
    project, clip = _project(tmp_path, scene)
    assert build_render_plan(project).missing_media == ()
    scene.unlink()
    _assert_card_on_screen_and_refusal_on_export(project, clip, tmp_path)


def test_an_unreadable_scene_file_gives_a_warning_and_the_same_refusal(tmp_path):
    scene = write_static_scene(tmp_path)
    project, clip = _project(tmp_path, scene)
    scene.write_text("{ ceci n'est pas du json", encoding="utf-8")
    _assert_card_on_screen_and_refusal_on_export(project, clip, tmp_path)


def test_a_missing_model_behaves_like_a_missing_scene_and_recovers_when_it_returns(
    tmp_path,
):
    scene = write_static_scene(tmp_path)
    project, clip = _project(tmp_path, scene)
    model = (tmp_path / "cube.obj").read_bytes()
    (tmp_path / "cube.obj").unlink()
    _assert_card_on_screen_and_refusal_on_export(project, clip, tmp_path)
    (tmp_path / "cube.obj").write_bytes(model)
    plan = build_render_plan(project)
    assert plan.missing_media == () and plan.warnings == ()
    assert (
        next(
            layer for layer in plan.graphics_layers if layer.clip_id == clip.id
        ).graphic.type
        is GraphicType.SCENE3D
    )


def test_a_missing_scene_inside_a_nested_sequence_is_reported_too(tmp_path):
    from core.sequences import create_sequence_from_selection

    scene = write_static_scene(tmp_path)
    project, clip = _project(tmp_path, scene, start=0.0, duration=3.0)
    create_sequence_from_selection(project, [clip.id], "Imbriquée")
    scene.unlink()
    plan = build_render_plan(project)
    assert plan.missing_media and plan.warnings


def test_the_plan_of_a_hidden_track_ignores_a_missing_scene(tmp_path):
    scene = write_static_scene(tmp_path)
    project, clip = _project(tmp_path, scene)
    next(t for t in project.tracks if t.id == clip.track_id).visible = False
    scene.unlink()
    plan = build_render_plan(project)
    assert plan.missing_media == () and plan.warnings == ()


def test_problem_describes_each_failure_without_raising(tmp_path):
    scene = write_static_scene(tmp_path)
    assert scene3d.problem(scene) is None
    assert "introuvable" in scene3d.problem(tmp_path / "nope.kut3d")
    assert (
        scene3d.problem(tmp_path / "x.txt") is not None
        and scene3d.problem("") is not None
    )
    (tmp_path / "cube.obj").unlink()
    assert scene3d.problem(scene)
    assert len(scene3d.problem(scene)) <= 200
