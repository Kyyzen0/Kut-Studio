"""Loudness d'un export : mesure EBU R 128 du mixage et normalisation statique à −14 LUFS (vrai FFmpeg)."""

from __future__ import annotations

import subprocess
from dataclasses import replace

import pytest

from core.export_engine import ExportEngine
from core.loudness import LoudnessMeasure, measure_plan, normalization_gain, parse_ebur128
from core.project_model import Clip, MediaAsset, Project, Track
from core.render_plan import build_render_plan
from core.render_presets import custom_preset, get_preset, with_loudness
from core.render_queue import RenderQueue
from core.render_queue_store import RenderQueueStore
from render_probe import needs_ffmpeg

W, H, FPS, SECONDS = 160, 284, 25, 6.0

SUMMARY = """[Parsed_ebur128_0 @ 0x1] Summary:

  Integrated loudness:
    I:         -21.8 LUFS
    Threshold: -31.8 LUFS

  Loudness range:
    LRA:        20.0 LU
    Threshold: -41.8 LUFS

  True peak:
    Peak:      -18.1 dBFS
"""


def test_the_ebur128_summary_is_read_and_silence_is_left_alone():
    measure = parse_ebur128("noise\n" + SUMMARY)
    assert measure == LoudnessMeasure(-21.8, 20.0, -18.1)
    assert normalization_gain(measure, -14.0) == pytest.approx(7.8)
    assert normalization_gain(LoudnessMeasure(float("-inf"), 0.0, float("-inf"))) is None
    assert get_preset("tiktok").loudness_lufs == -14.0 and get_preset("h264_1080p").loudness_lufs is None


def _project(tmp_path, amplitude: float) -> Project:
    video = tmp_path / "v.mp4"
    music = tmp_path / "m.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c=navy:s={W}x{H}:r={FPS}:d={SECONDS}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)], check=True, timeout=60)
    wave = f"{amplitude}*sin(2*PI*220*t)*(0.6+0.4*sin(2*PI*2*t))"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    f"aevalsrc={wave}|{wave}:sample_rate=48000:duration={SECONDS}", "-c:a", "pcm_s16le", str(music)],
                   check=True, timeout=60)
    assets = [MediaAsset("v", str(video), "v", SECONDS, W, H, float(FPS), "video", False),
              MediaAsset("m", str(music), "m", SECONDS, 0, 0, 0.0, "audio", True)]
    return Project("loud", width=W, height=H, fps=float(FPS), media_assets=assets, tracks=[
        Track("V1", "V1", "video", clips=[Clip("c", "v", "V1", 0.0, 0.0, SECONDS)]),
        Track("A1", "A1", "audio", audio_role="music", clips=[Clip("a", "m", "A1", 0.0, 0.0, SECONDS)]),
    ])


def _measure_file(path) -> LoudnessMeasure:
    completed = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af",
                                "ebur128=peak=true:framelog=quiet", "-f", "null", "-"],
                               capture_output=True, text=True, timeout=120)
    return parse_ebur128(completed.stderr)


@needs_ffmpeg
def test_the_measure_is_the_loudness_of_the_exported_mix(tmp_path):
    plan = build_render_plan(_project(tmp_path, 0.08))
    measured = measure_plan(plan, FPS)
    graph, video, audio, inputs = ExportEngine._build_filter_complex(plan, W, H, FPS, None)
    out = tmp_path / "plain.mkv"
    command = ["ffmpeg", "-y", "-v", "error"]
    for path in inputs:
        command += ["-i", path]
    command += ["-filter_complex", graph, "-map", f"[{video}]", "-map", f"[{audio}]", "-c:v", "libx264", "-c:a",
                "pcm_s16le", str(out)]
    subprocess.run(command, check=True, timeout=120)
    assert measured.integrated == pytest.approx(_measure_file(out).integrated, abs=0.2)
    assert measure_plan(replace(plan, loudness_gain_db=6.0), FPS).integrated == pytest.approx(measured.integrated,
                                                                                              abs=0.05)


@needs_ffmpeg
@pytest.mark.parametrize("amplitude", [0.05, 0.6])
def test_a_normalised_export_lands_on_minus_14_lufs(qtbot, tmp_path, amplitude):
    """Trop bas (−30 LUFS) ou trop fort (proche de 0 dBFS) : l'export sort à −14 LUFS, crête sous −1 dBFS."""
    queue = RenderQueue(ExportEngine(), RenderQueueStore(tmp_path / "queue"))
    try:
        spec = with_loudness(custom_preset(width=W, height=H, fps=FPS, quality=28), -14.0)
        job = queue.enqueue(_project(tmp_path, amplitude), spec, str(tmp_path / "social.mp4"))
        queue.start_job(job.id)
        qtbot.waitUntil(lambda: not queue.is_running, timeout=120000)
        assert job.error_message == "" and job.measured_lufs is not None
        measure = _measure_file(job.output_path)
        assert measure.integrated == pytest.approx(-14.0, abs=0.5), (job.measured_lufs, measure)
        assert measure.true_peak <= -0.5
    finally:
        queue.shutdown()
