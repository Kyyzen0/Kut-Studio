"""Cadrage « remplir » et pan : recadrer une vidéo 16:9 dans un cadre vertical, à l'identique partout.

L'export agrandit le média à la taille qui couvre le cadre (``scale``), puis prend la fenêtre de pan en temps du clip
(``crop``) ; le moniteur GPU (``fit_box``) et le moniteur CPU placent la même fenêtre, au pixel près.
"""

from __future__ import annotations

import json
import subprocess

import numpy as np
import pytest

from core.animation import InterpolationType
from core.project_io import load_project, save_project
from core.project_model import Clip, MediaAsset, Project, Track
from core.render_plan import build_render_plan
from core.tracking_motion import cover_size, fit_box, pan_offset
from core.visual_effects import ClipTransform, TransformKeyframe
from render_probe import needs_ffmpeg, render_frame

SEQ_W, SEQ_H = 180, 320          # cadre vertical
SRC_W, SRC_H = 320, 180          # média 16:9


def _bands(tmp_path):
    """Média 16:9 en trois bandes verticales : rouge | vert | bleu."""
    path = tmp_path / "bands.mp4"
    third = SRC_W // 3
    graph = (f"color=c=red:s={SRC_W}x{SRC_H}:r=25:d=2[a];color=c=lime:s={third + 2}x{SRC_H}:r=25:d=2[g];"
             f"color=c=blue:s={third}x{SRC_H}:r=25:d=2[b];[a][g]overlay={third}:0[ag];[ag][b]overlay={SRC_W - third}:0")
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-filter_complex", graph, "-c:v", "libx264",
                    "-qp", "0", "-pix_fmt", "yuv444p", str(path)], check=True, timeout=60)
    return path


def _project(tmp_path, transform: ClipTransform, keyframes=()) -> Project:
    asset = MediaAsset(id="a", path=str(_bands(tmp_path)), name="bands", duration=2.0, width=SRC_W, height=SRC_H,
                       fps=25.0, media_type="video")
    clip = Clip(id="c", asset_id="a", track_id="V1", timeline_start=0.0, source_in=0.0, source_out=2.0)
    clip.transform = transform
    clip.transform_keyframes = list(keyframes)
    return Project(name="9:16", width=SEQ_W, height=SEQ_H, fps=25.0, media_assets=[asset],
                   tracks=[Track(id="V1", name="V1", type="video", clips=[clip])])


def _dominant(image: np.ndarray) -> str:
    """Couleur dominante de l'image (rouge, vert, bleu ou noir)."""
    mean = image.reshape(-1, 3).mean(axis=0)
    if mean.max() < 40:
        return "black"
    return ("red", "green", "blue")[int(np.argmax(mean))]


# --- Géométrie --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("media", "canvas"), [((1920, 1080), (1080, 1920)), ((1080, 1920), (1920, 1080)),
                                               ((1280, 720), (1080, 1350)), ((640, 480), (1080, 1080)),
                                               ((1919, 1079), (1080, 1920))])
@needs_ffmpeg
def test_cover_size_is_the_size_ffmpeg_scales_to(tmp_path, media, canvas):
    out = tmp_path / "x.mp4"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"color=s={media[0]}x{media[1]}:d=0.04", "-vf",
                    f"scale={canvas[0]}:{canvas[1]}:force_original_aspect_ratio=increase", "-c:v", "png",
                    "-frames:v", "1", "-f", "image2", str(out.with_suffix(".png"))], check=True, timeout=60)
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(out.with_suffix(".png"))],
                           check=True, capture_output=True, text=True).stdout
    stream = json.loads(probe)["streams"][0]
    assert cover_size(*media, *canvas) == (stream["width"], stream["height"])


def test_the_pan_window_goes_from_edge_to_edge():
    assert pan_offset(100, -1.0) == 0 and pan_offset(100, 0.0) == 50 and pan_offset(100, 1.0) == 100
    box = fit_box(SRC_W, SRC_H, SEQ_W, SEQ_H, fill=True, pan_x=1.0)
    iw, ih = cover_size(SRC_W, SRC_H, SEQ_W, SEQ_H)
    assert (box.width, box.height) == (iw, ih) and box.offset_x == -(iw - SEQ_W) and box.offset_y == 0.0


def test_fill_is_a_clip_setting_not_a_curve():
    with pytest.raises(ValueError):
        TransformKeyframe("fill", 0.0, 1.0)
    assert ClipTransform(fill=1).fill is True


def test_framing_round_trips_through_the_kut_file_and_is_absent_by_default(tmp_path):
    project = _project(tmp_path, ClipTransform(fill=True, pan_x=-0.4),
                       [TransformKeyframe("pan_x", 0.0, -0.4), TransformKeyframe("pan_x", 1.0, 0.6)])
    path = tmp_path / "p.kut"
    save_project(project, path)
    clip = load_project(path).tracks[0].clips[0]
    assert clip.transform.fill is True and clip.transform.pan_x == pytest.approx(-0.4)
    assert [kf.value for kf in clip.transform_keyframes if kf.property_name == "pan_x"] == [-0.4, 0.6]
    plain = _project(tmp_path, ClipTransform())
    save_project(plain, path)
    raw = json.loads(path.read_text(encoding="utf-8"))["project"]
    transform = raw["sequences"][0]["tracks"][0]["clips"][0]["transform"]
    assert not {"fill", "pan_x", "pan_y"} & set(transform)            # un clip simple garde son ancien JSON


# --- Rendu réel -------------------------------------------------------------------------------------------


@needs_ffmpeg
@pytest.mark.parametrize(("transform", "expected"), [
    (ClipTransform(), "black"),                                 # cadrage habituel : bandes noires en haut et en bas
    (ClipTransform(fill=True), "green"),                        # remplir : la bande centrale
    (ClipTransform(fill=True, pan_x=-1.0), "red"),
    (ClipTransform(fill=True, pan_x=1.0), "blue"),
])
def test_fill_shows_the_pan_window_of_the_media(tmp_path, transform, expected):
    image = render_frame(build_render_plan(_project(tmp_path, transform)), SEQ_W, SEQ_H, 0.5)
    if expected == "black":
        assert _dominant(image[:40]) == "black" and _dominant(image[-40:]) == "black"
    else:
        assert _dominant(image) == expected


@needs_ffmpeg
def test_an_animated_pan_sweeps_the_media_in_clip_time(tmp_path):
    keyframes = [TransformKeyframe("pan_x", 0.0, -1.0, InterpolationType.LINEAR), TransformKeyframe("pan_x", 1.8, 1.0)]
    plan = build_render_plan(_project(tmp_path, ClipTransform(fill=True), keyframes))
    seen = [_dominant(render_frame(plan, SEQ_W, SEQ_H, t)) for t in (0.0, 0.9, 1.8)]
    assert seen == ["red", "green", "blue"]


@needs_ffmpeg
def test_the_gpu_reference_places_the_same_window_as_the_export(tmp_path):
    """Moniteur GPU (référence numpy) contre image de l'export : même fenêtre de pan, à un pixel près."""
    from core.gpu_composite import CompositeFrame, CompositeLayer, VideoSource, affine_mul, reference_frame
    from core.gpu_effects import program_for
    from core.tracking_motion import video_layer_matrix
    from tests.gpu_harness import reference_codes, smooth_pattern

    codes = smooth_pattern(SRC_W, SRC_H)
    source = tmp_path / "src.mkv"
    raw = codes[..., 0].tobytes() + codes[::2, ::2, 1].tobytes() + codes[::2, ::2, 2].tobytes()
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "yuv420p", "-s", f"{SRC_W}x{SRC_H}",
                    "-r", "10", "-i", "-", "-c:v", "ffv1", str(source)], input=raw * 10, check=True)
    values = ClipTransform(fill=True, pan_x=0.35)
    project = Project(name="fill", width=SEQ_W, height=SEQ_H, fps=10)
    project.media_assets.append(MediaAsset("a", str(source), "src", 1.0, SRC_W, SRC_H, 10, "video"))
    clip = Clip("v", "a", "V1", 0.0, 0.0, 1.0)
    clip.transform = values
    project.tracks.insert(0, Track("V1", "V1", "video", clips=[clip]))
    exported = render_frame(build_render_plan(project), SEQ_W, SEQ_H, 0.2, fps=10) / 255.0
    box = fit_box(SRC_W, SRC_H, SEQ_W, SEQ_H, fill=True, pan_x=values.pan_x)

    def render(shift):
        matrix = affine_mul((1, 0, 0, 1, *shift), tuple(video_layer_matrix(values, SEQ_W, SEQ_H)))
        layer = CompositeLayer("v", matrix, box.rect, 1.0, program=program_for(()))
        frame = CompositeFrame(SEQ_W, SEQ_H, 1.0, (layer,), (VideoSource("v", "yuv420p", SRC_W, SRC_H),))
        return reference_frame(frame, {"v": reference_codes(codes)})

    interior = (slice(20, -20), slice(20, -20))
    error, dx, dy = min(
        (float(np.abs(render((dx, dy)) - exported)[interior].mean() * 255), dx, dy)
        for dx in (-1.0, -0.5, 0.0, 0.5, 1.0) for dy in (-1.0, -0.5, 0.0, 0.5, 1.0)
    )
    assert abs(dx) <= 1.0 and abs(dy) <= 1.0
    assert error <= 4.0, (error, dx, dy)
