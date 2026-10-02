"""Rendus FFmpeg réels des séquences imbriquées : export, aperçu, audio.

Ces tests génèrent de petits médias (``lavfi``), rendent le graphe que
produiraient l'export et l'aperçu, puis lisent des pixels et le niveau
audio de la sortie. Ils vérifient la promesse centrale : une séquence
rendue directement, utilisée dans une autre, ou pré-rendue en segment
d'aperçu donne la même image.
"""

from __future__ import annotations

import re
import shutil
import subprocess

import pytest

from core.export_engine import ExportEngine, _ffmpeg_command_prefix
from core.filter_graph import build_preview_command
from core.preview_segments import segment_plan
from core.project_model import Clip, MediaAsset, Project, Sequence, Track
from core.render_plan import build_render_plan
from core.sequences import insert_sequence_clip
from core.visual_effects import ClipTransform

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg indisponible")

W, H, FPS = 160, 90, 25


def _run(command: list[str]) -> None:
    completed = subprocess.run(command, capture_output=True, text=True, timeout=120)
    assert completed.returncode == 0, completed.stderr[-1500:]


@pytest.fixture
def media(tmp_path):
    red = tmp_path / "red.mp4"
    blue = tmp_path / "blue.mp4"
    _run([
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=red:s={W}x{H}:r={FPS}:d=4",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=4", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(red),
    ])
    _run([
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=blue:s={W}x{H}:r={FPS}:d=4",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(blue),
    ])
    return {
        "red": MediaAsset("red", str(red), "red", 4.0, W, H, float(FPS), "video", True),
        "blue": MediaAsset("blue", str(blue), "blue", 4.0, W, H, float(FPS), "video", False),
    }


def _project(media, *, inner_scale: float = 1.0) -> Project:
    intro = Sequence("intro", "Intro", W, H, float(FPS), tracks=[
        Track("V1", "V1", "video", clips=[
            Clip("r", "red", "V1", 0.0, 0.0, 3.0, transform=ClipTransform(scale=inner_scale))
        ]),
    ])
    main = Sequence("main", "Master", W, H, float(FPS), tracks=[
        Track("V1", "V1", "video"), Track("V2", "V2", "video"),
    ])
    return Project("p", media_assets=list(media.values()), sequences=[main, intro], active_sequence_id="main")


def _render(plan, out_path) -> None:
    graph, video, audio, inputs = ExportEngine._build_filter_complex(plan, W, H, FPS, None)
    command = [*_ffmpeg_command_prefix(), "-y", "-v", "error"]
    for path in inputs:
        command += ["-i", path]
    command += ["-filter_complex", graph, "-map", f"[{video}]", "-map", f"[{audio}]",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(out_path)]
    _run(command)


def _pixel(path, seconds: float, x: int, y: int) -> tuple[int, int, int]:
    data = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{seconds:.3f}", "-i", str(path), "-frames:v", "1",
         "-vf", f"crop=1:1:{x}:{y},format=rgb24", "-f", "rawvideo", "-"],
        capture_output=True, timeout=60,
    ).stdout
    assert len(data) == 3, "aucune image lue"
    return tuple(data)


def _close(a, b, tolerance: int = 12) -> bool:
    return all(abs(x - y) <= tolerance for x, y in zip(a, b))


def _mean_volume(path) -> float:
    completed = subprocess.run(
        ["ffmpeg", "-v", "info", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True, timeout=60,
    )
    match = re.search(r"mean_volume:\s*(-?[\d.]+|-inf) dB", completed.stderr)
    assert match, completed.stderr[-500:]
    return float("-inf") if match.group(1) == "-inf" else float(match.group(1))


def test_direct_and_nested_renders_are_identical(media, tmp_path):
    project = _project(media, inner_scale=0.5)
    insert_sequence_clip(project, "intro", "V2", 0.0)
    direct = tmp_path / "direct.mp4"
    nested = tmp_path / "nested.mp4"
    _render(build_render_plan(project, sequence_id="intro"), direct)
    _render(build_render_plan(project), nested)
    for seconds in (0.5, 2.0):
        for point in ((W // 2, H // 2), (5, 5), (W - 6, H - 6)):
            assert _close(_pixel(direct, seconds, *point), _pixel(nested, seconds, *point)), (seconds, point)


def test_nested_background_is_transparent_and_parent_transform_applies_after(media, tmp_path):
    project = _project(media, inner_scale=0.5)
    project.tracks[0].clips.append(Clip("b", "blue", "V1", 0.0, 0.0, 4.0))
    nested = insert_sequence_clip(project, "intro", "V2", 0.0)
    nested.transform = ClipTransform(position_x=0.25)  # déplacé après composition
    out = tmp_path / "over.mp4"
    _render(build_render_plan(project), out)
    assert _close(_pixel(out, 1.0, 5, 5), (0, 0, 255), 40)  # le parent reste visible
    red_x = W // 2 + W // 4
    assert _close(_pixel(out, 1.0, red_x, H // 2), (255, 0, 0), 40)
    assert _close(_pixel(out, 1.0, W // 2 - W // 4 - 2, H // 2), (0, 0, 255), 40)


def test_multiple_instances_and_three_levels_render(media, tmp_path):
    project = _project(media)
    level2 = Sequence("l2", "L2", W, H, float(FPS), tracks=[Track("V1", "V1", "video")])
    level3 = Sequence("l3", "L3", W, H, float(FPS), tracks=[Track("V1", "V1", "video")])
    project.sequences.extend([level2, level3])
    insert_sequence_clip(project, "intro", "V1", 0.0, parent_sequence_id="l3")
    insert_sequence_clip(project, "l3", "V1", 0.0, parent_sequence_id="l2")
    insert_sequence_clip(project, "l2", "V2", 0.0)
    insert_sequence_clip(project, "intro", "V1", 3.0, source_in=0.0, source_out=1.0)
    out = tmp_path / "deep.mp4"
    plan = build_render_plan(project)
    assert [entry.sequence_id for entry in plan.nested_sequences] == ["intro", "l3", "l2"]
    _render(plan, out)
    assert _close(_pixel(out, 1.0, W // 2, H // 2), (255, 0, 0), 40)
    assert _close(_pixel(out, 3.5, W // 2, H // 2), (255, 0, 0), 40)


def test_nested_audio_is_mixed_and_parent_mute_silences_it(media, tmp_path):
    project = _project(media)
    insert_sequence_clip(project, "intro", "V2", 0.0)
    out = tmp_path / "audio.mp4"
    _render(build_render_plan(project), out)
    assert _mean_volume(out) > -40.0
    project.tracks[1].muted = True
    silent = tmp_path / "silent.mp4"
    _render(build_render_plan(project), silent)
    assert _mean_volume(silent) < -80.0


def test_preview_segment_matches_export(media, tmp_path):
    project = _project(media, inner_scale=0.5)
    project.tracks[0].clips.append(Clip("b", "blue", "V1", 0.0, 0.0, 4.0))
    insert_sequence_clip(project, "intro", "V2", 0.0)
    exported = tmp_path / "export.mp4"
    _render(build_render_plan(project), exported)
    segment = tmp_path / "segment.mp4"
    plan = segment_plan(project, 2.0, 4.0)
    _run(build_preview_command(
        plan, width=W, height=H, fps=FPS, quality="high", start=2.0, duration=1.0,
        output_path=str(segment),
    ))
    for point in ((W // 2, H // 2), (5, 5)):
        assert _close(_pixel(segment, 0.4, *point), _pixel(exported, 2.4, *point), 20), point
