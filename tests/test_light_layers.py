"""Lumière : néon des textes et formes, calques de lumière procéduraux (rendus déterministes, additifs)."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from core.blend_modes import BlendMode
from core.graphics import LIGHT_KINDS, GraphicOverlay, GraphicType, add_graphic_clip
from core.mograph_raster import content_margin, draw_content
from core.project_model import Project
from core.render_plan import build_render_plan
from render_probe import needs_ffmpeg, render_frame

W, H = 270, 480


def _image(graphic, size=(W, H), offset=(0.0, 0.0)):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage, QPainter

    image = QImage(size[0], size[1], QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.translate(*offset)
    draw_content(painter, graphic, (float(graphic.width), float(graphic.height)))
    painter.end()
    image = image.convertToFormat(QImage.Format_RGBA8888)
    return np.frombuffer(image.constBits(), np.uint8).reshape(size[1], image.bytesPerLine())[:, : size[0] * 4] \
        .reshape(size[1], size[0], 4).astype(np.int32)


def test_a_neon_glow_spreads_coloured_light_around_a_shape(qapp):
    shape = GraphicOverlay(type=GraphicType.SHAPE, width=W, height=H, fill_color="#FFFFFF")
    small = replace(shape, width=60, height=60)
    plain = _image(small, size=(200, 200), offset=(70.0, 70.0))
    neon = _image(replace(small, glow_radius=12.0, glow_color="#22B8FF", glow_strength=1.5), size=(200, 200),
                  offset=(70.0, 70.0))
    outside = (slice(60, 140), slice(132, 160))                   # à droite du carré (70..130)
    assert plain[outside][..., 3].max() == 0
    halo = neon[outside]
    assert halo[..., 3].max() > 40 and halo[..., 2].mean() > halo[..., 0].mean()   # bleu, autour de la forme
    assert content_margin(replace(small, glow_radius=12.0)) >= 36


@pytest.mark.parametrize("kind", [k for k in LIGHT_KINDS])
def test_every_light_is_deterministic_and_moves_with_time(qapp, kind):
    light = GraphicOverlay(type=GraphicType.LIGHT, width=W, height=H, light_kind=kind, light_seed=7,
                           fill_color="#FFB040", glow_color="#22B8FF")
    first = _image(replace(light, light_time=0.4))
    again = _image(replace(light, light_time=0.4))
    later = _image(replace(light, light_time=0.9))
    assert np.array_equal(first, again)
    assert first[..., 3].max() > 0
    if kind != "flash":
        assert not np.array_equal(first, later)
    assert not np.array_equal(first, _image(replace(light, light_time=0.4, light_seed=8))) or kind == "flash"


def test_a_light_layer_is_additive_and_follows_its_local_time(qapp):
    from core.mograph_raster import scene_for_plan

    project = Project(name="l", width=W, height=H, fps=25.0)
    clip = add_graphic_clip(project, "light", timeline_start=1.0, duration=2.0)
    assert clip.compositing.blend_mode is BlendMode.ADD
    assert (clip.graphic.width, clip.graphic.height) == (W, H)
    scene = scene_for_plan(build_render_plan(project))
    assert scene.evaluate(clip.id, 1.5).graphic.light_time == pytest.approx(0.5)


@needs_ffmpeg
def test_an_exported_light_leak_brightens_the_picture_below(tmp_path):
    from core.graphics import update_graphic

    project = Project(name="l", width=W, height=H, fps=25.0)
    base = add_graphic_clip(project, "solid", timeline_start=0.0, duration=1.0)
    for name, value in (("fill_color", "#202838"), ("width", W), ("height", H)):
        update_graphic(base, name, value)
    leak = add_graphic_clip(project, "light", timeline_start=0.0, duration=1.0)
    update_graphic(leak, "light_kind", "leak")
    plan = build_render_plan(project)
    lit = render_frame(plan, W, H, 0.3)
    leak.enabled = False
    dark = render_frame(build_render_plan(project), W, H, 0.3)
    assert (lit.astype(int) - dark.astype(int)).min() >= -3        # l'addition n'assombrit jamais
    assert lit.mean() > dark.mean() + 8


# --- Grain ----------------------------------------------------------------------------------------------------------


def test_grain_cycles_through_eight_deterministic_frames(qapp):
    from core.light_layers import GRAIN_FRAMES, GRAIN_RATE, grain_time

    assert grain_time(0.0) == grain_time(GRAIN_FRAMES / GRAIN_RATE)
    assert len({grain_time(n / GRAIN_RATE) for n in range(40)}) == GRAIN_FRAMES
    grain = GraphicOverlay(type=GraphicType.LIGHT, width=W, height=H, light_kind="grain", light_seed=4)
    first = _image(replace(grain, light_time=grain_time(0.1)))
    assert np.array_equal(first, _image(replace(grain, light_time=grain_time(0.1))))
    assert not np.array_equal(first, _image(replace(grain, light_time=grain_time(0.15))))
    gray = first[..., 0]
    assert abs(gray.mean() - 128) < 3 and 20 < gray.std() < 50 and first[..., 3].min() == 255


@needs_ffmpeg
def test_an_exported_grain_layer_keeps_the_picture_level_and_adds_texture(tmp_path):
    from core.graphics import graphic_defaults, update_graphic

    project = Project(name="g", width=W, height=H, fps=25.0)
    base = add_graphic_clip(project, "solid", timeline_start=0.0, duration=1.0)
    for name, value in (("fill_color", "#506070"), ("width", W), ("height", H)):
        update_graphic(base, name, value)
    grain = add_graphic_clip(project, "light", timeline_start=0.0, duration=1.0,
                             graphic=replace(graphic_defaults("light", project_width=W, project_height=H),
                                             light_kind="grain"))
    assert grain.compositing.blend_mode is BlendMode.OVERLAY and grain.transform.opacity == pytest.approx(0.35)
    textured = render_frame(build_render_plan(project), W, H, 0.3).astype(float)
    grain.enabled = False
    flat = render_frame(build_render_plan(project), W, H, 0.3).astype(float)
    assert abs(textured.mean() - flat.mean()) < 3.0                 # neutre en moyenne
    assert textured[..., 1].std() > flat[..., 1].std() + 3.0        # mais texturé (écart spatial, canal vert)
