"""Moniteur GPU : les calques graphiques à mode de fusion (lumière en Addition, Écran…) composés par le shader.

Le moniteur GPU affichait les calques par-dessus, fusionnés contre un fond transparent : une lumière en Addition y
apparaissait comme un calque normal. Elle est désormais une source du compositeur GPU, avec son alpha en matte : la
référence numpy (l'oracle des shaders) doit donner l'image de l'export.
"""

from __future__ import annotations

import numpy as np
import pytest

from core.blend_modes import BlendMode
from core.gpu_composite import CompositeFrame, CompositeLayer, VideoSource, reference_frame
from core.gpu_effects import program_for
from core.graphics import add_graphic_clip, update_graphic
from core.project_model import Project
from core.render_plan import build_render_plan
from render_probe import needs_ffmpeg, render_frame

W, H = 160, 288


def _straight_rgba(image) -> np.ndarray:
    from PySide6.QtGui import QImage

    image = image.convertToFormat(QImage.Format_RGBA8888)
    data = np.frombuffer(image.constBits(), np.uint8).reshape(image.height(), image.bytesPerLine())
    return data[:, : image.width() * 4].reshape(image.height(), image.width(), 4) / 255.0


def _compose(background: np.ndarray, light: np.ndarray, mode: BlendMode) -> np.ndarray:
    identity = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    frame = CompositeFrame(
        W, H, 1.0,
        (CompositeLayer("bg", identity, (0.0, 0.0, W, H), program=program_for(())),
         CompositeLayer("light", identity, (0.0, 0.0, W, H), blend=mode, program=program_for(()), matte="m")),
        (VideoSource("bg", "rgba", W, H), VideoSource("light", "rgba", W, H)),
    )
    return reference_frame(frame, {"bg": background, "light": light[..., :3]}, {"m": light[..., 3]})


@needs_ffmpeg
@pytest.mark.parametrize("mode", [BlendMode.ADD, BlendMode.SCREEN])
@pytest.mark.parametrize("kind", ["leak", "anamorphic_flare", "light_trails"])
def test_the_gpu_composes_a_light_layer_like_the_export(qapp, mode, kind):
    from core.mograph_raster import MographRenderer, scene_for_plan

    project = Project(name="g", width=W, height=H, fps=25.0)
    base = add_graphic_clip(project, "solid", timeline_start=0.0, duration=1.0)
    for name, value in (("fill_color", "#1A2438"), ("width", W), ("height", H)):
        update_graphic(base, name, value)
    light = add_graphic_clip(project, "light", timeline_start=0.0, duration=1.0)
    update_graphic(light, "light_kind", kind)
    light.compositing = type(light.compositing)(blend_mode=mode)
    lit = render_frame(build_render_plan(project), W, H, 0.4) / 255.0
    light.enabled = False
    dark = render_frame(build_render_plan(project), W, H, 0.4) / 255.0
    light.enabled = True
    scene = scene_for_plan(build_render_plan(project))
    image = MographRenderer(scene, W, H, fps=25.0, quality="draft").render([light.id], 0.4, blend_modes=False)
    composed = _compose(dark, _straight_rgba(image), mode)
    error = np.abs(composed - lit).mean() * 255
    assert error <= 3.0, error
    assert np.abs(lit - dark).max() * 255 > 30.0                 # la lumière se voit vraiment


class _FakeView:
    def __init__(self):
        self.frames, self.forgotten, self.composite = {}, [], None

    def set_video_frame(self, source, frame):
        self.frames[source] = frame

    def forget_source(self, source):
        self.forgotten.append(source)

    def set_composite(self, frame, mattes):
        self.composite = (frame, mattes)


def test_the_preview_panel_sends_blend_layers_once_and_composes_them(qtbot):
    from PySide6.QtGui import QColor, QImage

    from ui.preview_panel import PreviewPanel

    panel = PreviewPanel(lambda: None, lambda: None, lambda _d: None, lambda: None, lambda: None)
    qtbot.addWidget(panel)
    view = _FakeView()
    panel.gpu_view = view
    image = QImage(W, H, QImage.Format_RGBA8888)
    image.fill(QColor(255, 176, 64, 128))
    panel.set_blend_layers([("k1", image, BlendMode.ADD)])
    panel.set_blend_layers([("k1", image, BlendMode.ADD)])
    assert list(view.frames) == ["graphics0"]
    frame, mattes = view.composite
    layer = frame.layers[-1]
    assert layer.source == "graphics0" and layer.blend is BlendMode.ADD and layer.matte in mattes
    panel.set_blend_layers([])
    assert view.forgotten == ["graphics0"]
    panel.gpu_view = None
