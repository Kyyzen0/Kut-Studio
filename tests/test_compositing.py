import json

from core.compositing import (BlendMode, ChromaKey, Compositing, Mask, MaskKeyframe,
                              MaskShape, build_ffmpeg_filters, evaluate_mask)
from core.project_model import Project
from core.project_io import CURRENT_VERSION, load_project, save_project
from core.project_model import Clip, MediaAsset, Track
from core.render_plan import build_render_plan


def test_mask_animation_and_ffmpeg_filters():
    mask = Mask(shape=MaskShape.ELLIPSE, keyframes=(MaskKeyframe("width", 1, .8),))
    assert evaluate_mask(mask, 2).width == .8
    value = Compositing((mask,), ChromaKey(True, "#00ff00", .2, .1, .4), BlendMode.SCREEN)
    filters = ",".join(build_ffmpeg_filters(value, 1920, 1080))
    assert "chromakey=0x00FF00:0.2:0.1" in filters
    assert "despill=green" in filters and "pow((X-" in filters


def test_compositing_kut_round_trip_and_old_default(tmp_path):
    project = Project("Compo")
    asset = MediaAsset("a", "/tmp/a.mp4", "a", 2, 16, 16, 25, "video")
    comp = Compositing((Mask(inverted=True),), blend_mode=BlendMode.MULTIPLY)
    clip = Clip("c", "a", "V1", 0, 0, 1, compositing=comp)
    project.media_assets.append(asset); project.tracks.append(Track("V1", "V1", "video", clips=[clip]))
    path = tmp_path / "x.kut"; save_project(project, str(path))
    # Le compositing est un ajout rétrocompatible au schéma v12. Éviter une
    # hausse de version protège les lecteurs de la tâche 32 et constitue une
    # régression explicitement couverte par les suites graphics/project_io.
    assert CURRENT_VERSION == 12
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 12
    restored = load_project(str(path)).tracks[0].clips[0]
    assert restored.compositing == comp
    assert build_render_plan(load_project(str(path))).video_layers[0].compositing == comp
