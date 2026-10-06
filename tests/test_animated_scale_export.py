"""Une échelle animée qui part petite n'est plus rognée à l'export.

``rotate`` (toujours dans la chaîne d'un clip) fixait sa taille de sortie sur la **première** image : un clip qui
grandissait (zoom d'entrée, pop-in) restait coupé au cadre de sa taille de départ. Mesuré avant la correction : un
carré qui passe de 30 à 100 px restait à 42 px (``hypot(30, 30)``).
"""

from __future__ import annotations

import pytest

from core.animation import InterpolationType
from core.project_model import Clip, MediaAsset, Project, Track
from core.render_plan import build_render_plan
from core.visual_effects import ClipTransform, TransformKeyframe, max_transform_value
from render_probe import lavfi_video, needs_ffmpeg, render_frame


def test_the_peak_of_a_curve_includes_its_overshoot():
    frames = [TransformKeyframe("scale", 0.0, 0.3, InterpolationType.BEZIER, out_slope=15.0),
              TransformKeyframe("scale", 0.2, 1.0, InterpolationType.LINEAR, in_slope=0.0)]
    assert max_transform_value(ClipTransform(), frames, "scale", 1.0) > 1.0
    assert max_transform_value(ClipTransform(scale=0.8), [], "scale", 1.0) == 0.8


@needs_ffmpeg
def test_a_growing_clip_reaches_its_full_size(tmp_path):
    media = lavfi_video(tmp_path / "w.mp4", "color=c=white", size=(64, 64), seconds=1.0)
    asset = MediaAsset(id="a", path=str(media), name="w", duration=1.0, width=64, height=64, fps=25.0,
                       media_type="video")
    clip = Clip(id="c", asset_id="a", track_id="V1", timeline_start=0.0, source_in=0.0, source_out=1.0)
    clip.transform_keyframes = [TransformKeyframe("scale", 0.0, 0.15, InterpolationType.LINEAR),
                                TransformKeyframe("scale", 0.8, 0.5)]
    project = Project(name="p", width=200, height=200, fps=25.0, media_assets=[asset],
                      tracks=[Track(id="V1", name="V1", type="video", clips=[clip])])
    plan = build_render_plan(project)
    widths = [int((render_frame(plan, 200, 200, t)[100, :, 0] > 128).sum()) for t in (0.0, 0.4, 0.9)]
    assert widths[0] == pytest.approx(30, abs=2) and widths[1] == pytest.approx(65, abs=3)
    assert widths[2] == pytest.approx(100, abs=2)
