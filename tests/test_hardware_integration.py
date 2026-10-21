"""Encodage matériel avec le **vrai** FFmpeg, sans dépendre d'un GPU précis.

Ces tests s'adaptent à la machine : ils vérifient ce que *cette* installation
sait faire (détection réelle, chaque encodeur validé produit un fichier lisible)
et provoquent un vrai échec d'initialisation pour contrôler le repli CPU.
Sans FFmpeg ils sont ignorés ; sans GPU, seuls les chemins CPU sont exercés.
"""

from __future__ import annotations

import json
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
from core.hardware_cache import CapabilityService, set_default_service
from core.hardware_encoding import HARDWARE_BACKENDS, EncoderCapability, HardwareCapabilities, HardwareEncoder
from core.render_plan import build_render_plan
from tools.perf.encode_bench import run_bench


@pytest.fixture
def real_service(qtbot, tmp_path):
    _require_ffmpeg()
    service = CapabilityService(cache_path=tmp_path / "caps.json", environment={})
    set_default_service(service)
    return service


def _codec_name(ffprobe: str, path: Path) -> str:
    result = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_name",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        check=True, capture_output=True, text=True,
    )
    return result.stdout.strip()


def _export(tmp_path, ffmpeg, hardware: str, name: str):
    red, blue = tmp_path / "red.mp4", tmp_path / "blue.mp4"
    if not red.exists():
        _generate_color_clip(ffmpeg, red, color="red", duration=2.0)
        _generate_color_clip(ffmpeg, blue, color="blue", duration=4.0)
    plan = build_render_plan(_build_overlap_project(red, blue))
    output = tmp_path / name
    request = ExportRequest(
        render_plan=plan, output_path=str(output), format=ExportFormat.MP4_H264,
        preset=ExportPreset(name="T", resolution=(160, 90), crf=28, audio_bitrate="96k"),
        fps=15, hardware=hardware,
    )
    engine = ExportEngine()
    fallbacks: list[str] = []
    engine.encoder_fallback.connect(fallbacks.append)
    finished, failed = _wait_for_export(engine, timeout_ms=60000, start=lambda: engine.start(request))
    return engine, output, finished, failed, fallbacks


def test_real_detection_is_cached_and_consistent(real_service, tmp_path):
    caps = real_service.capabilities()
    assert caps.ffmpeg_available and caps.ffmpeg_version and "libx264" in caps.software
    assert all(item.validated is not None for item in caps.encoders)      # tout encodeur listé est validé
    assert json.loads(real_service.cache_path.read_text(encoding="utf-8"))["fingerprint"] == caps.fingerprint
    again = CapabilityService(cache_path=real_service.cache_path, environment={})
    assert again.capabilities() == caps and again.scan_count == 0         # relu depuis le disque
    assert "FFmpeg" in caps.describe()


def test_every_validated_encoder_really_produces_a_readable_file(real_service):
    report = run_bench(seconds=1.0, size="320x180", fps=15)
    assert report["results"][0]["backend"] == "cpu" and report["results"][0]["ok"]
    for item in report["results"]:
        assert item["ok"], item                                           # validé à la détection = utilisable
        assert item["size_bytes"] > 0 and item["fps"] > 0


def test_auto_export_with_real_ffmpeg_is_valid_whatever_the_machine(real_service, tmp_path):
    ffmpeg, ffprobe = _require_ffmpeg()
    engine, output, finished, failed, _ = _export(tmp_path, ffmpeg, "auto", "auto.mp4")
    assert finished and not failed
    assert abs(_probe_duration(ffprobe, output) - 4.0) < 0.5 and _codec_name(ffprobe, output) == "h264"
    expected = real_service.capabilities().auto_backend("h264")
    assert engine.last_encoder_choice.used is expected


def test_each_usable_hardware_backend_exports_a_valid_video(real_service, tmp_path):
    ffmpeg, ffprobe = _require_ffmpeg()
    usable = real_service.capabilities().usable_backends("h264")
    if not usable:
        pytest.skip("aucun encodeur matériel validé sur cette machine")
    for backend in usable:
        engine, output, finished, failed, fallbacks = _export(tmp_path, ffmpeg, backend.value, f"{backend.value}.mp4")
        assert finished and not failed and not fallbacks, (backend, failed)
        assert _codec_name(ffprobe, output) == "h264" and abs(_probe_duration(ffprobe, output) - 4.0) < 0.5
        assert engine.last_encoder_choice.used is backend


def _unusable_backend(real: HardwareCapabilities) -> HardwareEncoder:
    for backend in HARDWARE_BACKENDS:
        if not real.is_usable("h264", backend):
            return backend
    pytest.skip("tous les backends sont utilisables ici : rien à faire échouer")


def _lying_capabilities(real: HardwareCapabilities, backend: HardwareEncoder) -> HardwareCapabilities:
    """Détection qui prétend à tort qu'un backend absent est validé (pilote changé depuis)."""
    from dataclasses import replace

    from core.hardware_encoding import FFMPEG_ENCODER_NAMES

    fake = EncoderCapability("h264", backend, FFMPEG_ENCODER_NAMES[("h264", backend)], True, True)
    return replace(real, encoders=(fake,))


class _FixedService(CapabilityService):
    def __init__(self, capabilities: HardwareCapabilities) -> None:
        super().__init__(environment={})
        self._fixed = capabilities

    def capabilities(self, *, refresh: bool = False) -> HardwareCapabilities:
        return self._fixed

    def cached(self):
        return self._fixed


def test_auto_falls_back_to_the_cpu_when_the_real_hardware_encoder_fails(real_service, tmp_path):
    ffmpeg, ffprobe = _require_ffmpeg()
    real = real_service.capabilities()
    backend = _unusable_backend(real)
    set_default_service(_FixedService(_lying_capabilities(real, backend)))
    engine, output, finished, failed, fallbacks = _export(tmp_path, ffmpeg, "auto", "fallback.mp4")
    assert finished and not failed, failed                                 # le job n'est pas perdu
    assert len(fallbacks) == 1 and "CPU" in fallbacks[0]
    assert engine.last_encoder_choice.used is HardwareEncoder.CPU
    assert engine.last_encoder_choice.fallback_reason == fallbacks[0]
    assert abs(_probe_duration(ffprobe, output) - 4.0) < 0.5 and _codec_name(ffprobe, output) == "h264"


def test_explicit_hardware_encoder_failure_is_reported_with_the_real_error(real_service, tmp_path):
    ffmpeg, _ = _require_ffmpeg()
    real = real_service.capabilities()
    backend = _unusable_backend(real)
    set_default_service(_FixedService(_lying_capabilities(real, backend)))
    engine, output, finished, failed, fallbacks = _export(tmp_path, ffmpeg, backend.value, "explicit.mp4")
    assert failed and not finished and not fallbacks                      # jamais masqué
    assert engine.last_error_kind == "encoder" and not output.exists()
