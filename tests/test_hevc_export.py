"""Export H.265 (HEVC) : formats, presets, arguments de l'encodeur, et un vrai fichier relu avec ffprobe."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from test_export_integration import (
    _build_overlap_project,
    _generate_color_clip,
    _probe_duration,
    _require_ffmpeg,
    _wait_for_export,
)

from core.export_engine import ExportEngine, ExportFormat, ExportPreset, ExportRequest
from core.render_plan import build_render_plan
from core.render_presets import export_format_for, get_preset
from core.video_encoders import resolve_video_encoder


def test_hevc_is_an_mp4_format_under_both_names_and_not_a_mov_one():
    assert export_format_for("mp4", "hevc") is ExportFormat.MP4_HEVC
    assert export_format_for("mp4", "h265") is ExportFormat.MP4_HEVC
    with pytest.raises(ValueError):
        export_format_for("mov", "hevc")


def test_hevc_is_part_of_the_end_to_end_hardware_validation():
    # Un format absent de cette liste n'est jamais exporté par la chaîne réelle lors de la validation matérielle.
    from core.hardware_validation import VALIDATED_FORMATS

    assert ExportFormat.MP4_HEVC in VALIDATED_FORMATS


def test_hevc_presets_are_mp4_hevc_and_say_so_in_their_summary():
    for preset_id in ("h265_1080p", "h265_4k"):
        spec = get_preset(preset_id)
        assert spec is not None
        assert (spec.container, spec.video_codec) == ("mp4", "hevc")
        assert "H.265" in spec.summary()


def test_cpu_hevc_uses_libx265_with_the_preset_quality_plus_four_and_hvc1_tag():
    # Le CRF du preset (20) donne un CRF HEVC de 24 : le même rendu que H.264 à 20, à débit plus faible.
    choice = resolve_video_encoder(
        "hevc", speed_preset="medium", quality=20, hardware="cpu", width=1920, height=1080, fps=30.0,
    )
    assert choice.encoder == "libx265"
    assert choice.args[:2] == ("-c:v", "libx265")
    assert choice.args[choice.args.index("-crf") + 1] == "24"
    assert choice.args[choice.args.index("-tag:v") + 1] == "hvc1"


def _encoders(ffmpeg: str) -> str:
    return subprocess.run([ffmpeg, "-hide_banner", "-encoders"], capture_output=True, text=True, check=True).stdout


def _probe_video_stream(ffprobe: str, path: Path) -> dict[str, str]:
    result = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_name,codec_tag_string",
         "-of", "default=noprint_wrappers=1", str(path)],
        check=True, capture_output=True, text=True,
    )
    return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)


def test_real_hevc_export_writes_hevc_tagged_hvc1_of_the_right_duration(tmp_path):
    ffmpeg, ffprobe = _require_ffmpeg()
    if " libx265 " not in _encoders(ffmpeg):
        pytest.skip("FFmpeg de cette machine n'a pas libx265")
    red, blue = tmp_path / "red.mp4", tmp_path / "blue.mp4"
    _generate_color_clip(ffmpeg, red, color="red", duration=2.0)
    _generate_color_clip(ffmpeg, blue, color="blue", duration=4.0)
    plan = build_render_plan(_build_overlap_project(red, blue))
    output = tmp_path / "hevc.mp4"
    request = ExportRequest(
        render_plan=plan, output_path=str(output), format=ExportFormat.MP4_HEVC,
        preset=ExportPreset(name="T", resolution=(160, 90), crf=28, audio_bitrate="96k"),
        fps=15, hardware="cpu",
    )
    engine = ExportEngine()
    finished, failed = _wait_for_export(engine, timeout_ms=60000, start=lambda: engine.start(request))

    assert failed == [] and finished, "l'export HEVC doit aboutir"
    stream = _probe_video_stream(ffprobe, output)
    assert stream["codec_name"] == "hevc"
    assert stream["codec_tag_string"] == "hvc1"  # sans cette étiquette, QuickTime et Safari n'ouvrent pas le MP4
    assert abs(_probe_duration(ffprobe, output) - 4.0) < 0.5
