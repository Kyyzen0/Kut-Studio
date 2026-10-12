"""Textures graphiques par contenu (ADR-0001, étape 2) : clés, cache, placement par matrice."""

from __future__ import annotations

import numpy as np
import pytest

from core.blend_modes import BlendMode
from core.gpu_composite import CompositeFrame, CompositeLayer, VideoSource, reference_frame
from core.gpu_effects import program_for
from core.graphics import add_graphic_clip, update_graphic
from core.mograph_raster import (
    MographRenderer,
    clear_content_texture_cache,
    scene_for_plan,
)
from core.mograph_scene import mat_mul, mat_translate
from core.project_model import Project
from core.render_plan import build_render_plan

W, H = 160, 288


@pytest.fixture(autouse=True)
def _clear_content_cache():
    clear_content_texture_cache()
    yield
    clear_content_texture_cache()


def _solid_project(*, x: float = 0.0, y: float = 0.0, rotation: float = 0.0) -> Project:
    from core.visual_effects import ClipTransform

    project = Project(name="content", width=W, height=H, fps=25.0)
    clip = add_graphic_clip(project, "solid", timeline_start=0.0, duration=1.0)
    for name, value in (("fill_color", "#E07020"), ("width", 40), ("height", 24)):
        update_graphic(clip, name, value)
    clip.transform = ClipTransform(position_x=x, position_y=y, rotation=rotation)
    return project


def test_content_key_ignores_pose_changes(qapp):
    project = _solid_project(x=0.0)
    clip = next(
        c for track in project.active_sequence.tracks for c in track.clips
        if getattr(c, "graphic", None) is not None
    )
    scene = scene_for_plan(build_render_plan(project))
    renderer = MographRenderer(scene, W, H, fps=25.0, quality="draft")
    key_a = renderer.content_key(clip.id, 0.2)
    pose_a = scene.evaluate(clip.id, 0.2).pose_key()
    from core.visual_effects import ClipTransform

    clip.transform = ClipTransform(position_x=0.25)
    scene = scene_for_plan(build_render_plan(project))
    renderer = MographRenderer(scene, W, H, fps=25.0, quality="draft")
    key_b = renderer.content_key(clip.id, 0.2)
    pose_b = scene.evaluate(clip.id, 0.2).pose_key()
    assert key_a == key_b
    assert pose_a != pose_b


def test_content_texture_is_smaller_than_the_frame_and_cached(qapp):
    project = _solid_project()
    scene = scene_for_plan(build_render_plan(project))
    renderer = MographRenderer(scene, W, H, fps=25.0, quality="draft")
    clip_id = next(iter(scene.top_level()))
    assert renderer.content_texture_eligible(clip_id, 0.2)
    first = renderer.content_texture(clip_id, 0.2)
    second = renderer.content_texture(clip_id, 0.2)
    assert first is not None and second is not None
    image, margin, evaluated = first
    assert image.width() < W and image.height() < H
    assert margin >= 0.0
    assert second[0] is image  # même objet : cache LRU


def test_content_texture_placed_by_matrix_matches_full_frame_raster(qapp):
    project = _solid_project(x=0.12, y=-0.08, rotation=18.0)
    plan = build_render_plan(project)
    scene = scene_for_plan(plan)
    renderer = MographRenderer(scene, W, H, fps=25.0, quality="draft")
    clip_id = next(iter(scene.top_level()))
    packed = renderer.content_texture(clip_id, 0.3)
    assert packed is not None
    image, margin, evaluated = packed
    from PySide6.QtGui import QImage

    image = image.convertToFormat(QImage.Format_RGBA8888)
    matrix = mat_mul(evaluated.world, mat_translate(-margin, -margin))
    fit = (0.0, 0.0, float(image.width()), float(image.height()))
    rgba = np.frombuffer(image.constBits(), np.uint8).reshape(image.height(), image.bytesPerLine())
    rgba = rgba[:, : image.width() * 4].reshape(image.height(), image.width(), 4).astype(np.float64) / 255.0
    frame = CompositeFrame(
        W, H, 1.0,
        (CompositeLayer(
            "g", matrix, fit, opacity=float(evaluated.opacity), program=program_for(()),
            matte="m", matte_in_fit=True,
        ),),
        (VideoSource("g", "rgba", image.width(), image.height()),),
    )
    placed = reference_frame(frame, {"g": rgba[..., :3]}, {"m": rgba[..., 3]})
    full = renderer.render([clip_id], 0.3)
    full = full.convertToFormat(QImage.Format_RGBA8888)
    full_rgba = np.frombuffer(full.constBits(), np.uint8).reshape(full.height(), full.bytesPerLine())
    full_rgba = full_rgba[:, : full.width() * 4].reshape(full.height(), full.width(), 4).astype(np.float64) / 255.0
    # Le rastériseur plein cadre compose sur fond transparent ; la référence GPU est RVB.
    expected = full_rgba[..., :3] * full_rgba[..., 3:4]
    error = np.abs(placed - expected).mean() * 255
    assert error <= 2.5, error


def test_preview_panel_sends_content_layers_with_matrix(qtbot):
    from PySide6.QtGui import QColor, QImage

    from ui.preview_panel import PreviewPanel

    class _FakeView:
        def __init__(self):
            self.frames, self.forgotten, self.composite = {}, [], None

        def set_video_frame(self, source, frame):
            self.frames[source] = frame

        def forget_source(self, source):
            self.forgotten.append(source)

        def set_composite(self, frame, mattes):
            self.composite = (frame, mattes)

    panel = PreviewPanel(lambda: None, lambda: None, lambda _d: None, lambda: None, lambda: None)
    qtbot.addWidget(panel)
    view = _FakeView()
    panel.gpu_view = view
    image = QImage(48, 32, QImage.Format_RGBA8888)
    image.fill(QColor(255, 120, 40, 200))
    matrix = (1.0, 0.0, 0.0, 1.0, 40.0, 60.0)
    fit = (0.0, 0.0, 48.0, 32.0)
    panel.set_blend_layers([("c1", image, BlendMode.NORMAL, matrix, 0.8, fit)])
    frame, mattes = view.composite
    layer = frame.layers[-1]
    assert layer.source == "graphics0"
    assert layer.matrix == matrix
    assert layer.fit == fit
    assert layer.opacity == pytest.approx(0.8)
    assert layer.matte_in_fit is True
    assert layer.matte in mattes
    panel.gpu_view = None
