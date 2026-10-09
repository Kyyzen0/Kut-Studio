"""Plan vidéo fixe : composé sans rééchantillonnage, ni filtre qui ne change aucun pixel.

``rotate`` posait chaque plan, même jamais tourné, au centre d'un cadre ``hypot(iw,ih)`` : 2 203 px pour du 1080p, un
côté impair, donc un demi-pixel de décalage sur chaque axe et une interpolation bilinéaire qui moyennait 2×2 pixels
(netteté −12 % mesurée sur un montage 1080×1920), sur un cadre 2,3 fois plus grand à traiter à chaque image.
"""

from __future__ import annotations

import subprocess

import numpy as np
from render_probe import lavfi_video, needs_ffmpeg, render_frame

from core.export_engine import ExportEngine
from core.project_model import Clip, MediaAsset, Project, Track
from core.render_plan import build_render_plan
from core.visual_effects import ClipTransform, TransformKeyframe

W, H = 72, 128                 # comme 1080×1920 ou 1920×1080 : cadre de ``rotate`` de côté impair (147 px)


def _project(media: str = "/media/clip.mp4") -> Project:
    project = Project("p", width=W, height=H, fps=25.0,
                      media_assets=[MediaAsset("v", media, "v", 2.0, W, H, 25.0, "video")],
                      tracks=[Track("V1", "V1", "video", clips=[Clip("c", "v", "V1", 0.0, 0.0, 2.0)])])
    return project


def _graph(project: Project) -> str:
    return ExportEngine._build_filter_complex(build_render_plan(project), W, H, 25, None)[0]


def test_a_clip_that_never_turns_has_no_rotate_and_an_opaque_one_no_opacity_filter():
    project = _project()
    graph = _graph(project)
    assert "rotate=" not in graph and "colorchannelmixer" not in graph
    clip = project.tracks[0].clips[0]
    clip.transform = ClipTransform(rotation=15.0, opacity=0.5)
    graph = _graph(project)
    assert "rotate=a='15.0*" in graph and "colorchannelmixer=aa=0.5" in graph
    clip.transform = ClipTransform()
    clip.transform_keyframes = [TransformKeyframe("rotation", 0.0, 0.0), TransformKeyframe("rotation", 1.0, 30.0)]
    assert "rotate=a=" in _graph(project)                     # une rotation animée garde son filtre
    clip.transform_keyframes = [TransformKeyframe("scale", 0.0, 1.0), TransformKeyframe("scale", 1.0, 1.2)]
    assert "rotate=a=" in _graph(project)                     # une échelle animée garde son cadre fixe


@needs_ffmpeg
def test_a_still_clip_comes_out_with_the_pixels_of_its_media(tmp_path):
    media = lavfi_video(tmp_path / "mire.mp4", "testsrc2=alpha=255", size=(W, H), fps=25, seconds=1.0)
    rendered = render_frame(build_render_plan(_project(str(media))), W, H, 0.4).astype(int)
    completed = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(media), "-vf", "trim=start=0.4,setpts=PTS-STARTPTS", "-frames:v", "1",
         "-pix_fmt", "rgb24", "-f", "rawvideo", "-"], capture_output=True, check=True, timeout=60)
    source = np.frombuffer(completed.stdout, np.uint8).reshape(H, W, 3).astype(int)
    # Le demi-pixel de ``rotate`` donnait des écarts de plus de 100 niveaux sur les arêtes de la mire.
    assert np.abs(rendered - source).max() <= 2
