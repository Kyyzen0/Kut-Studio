from pathlib import Path

import pytest
from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QApplication

from core import export_engine
from core.export_engine import ExportEngine, ExportFormat, ExportPreset, ExportRequest


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    """Create the Qt Widgets application required by the export test suite."""
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def engine(monkeypatch):
    """Return an export engine with a mocked ffmpeg executable path."""
    monkeypatch.setattr(export_engine.shutil, "which", lambda _: "/usr/bin/ffmpeg")
    monkeypatch.setattr(export_engine, "_ffmpeg_path", "/usr/bin/ffmpeg")
    return ExportEngine()


def make_request(tmp_path: Path, export_format: ExportFormat) -> ExportRequest:
    """Build a request using one temporary source path."""
    source_path = tmp_path / "source.mp4"
    source_path.touch()
    return ExportRequest(
        clips=[{"id": "clip-1", "start": 0.0, "end": 10.0, "source_path": str(source_path)}],
        output_path=str(tmp_path / "output.mp4"),
        format=export_format,
        preset=ExportPreset("Haute", (1920, 1080), 18, "192k"),
    )


def test_build_command_mp4_h264(engine, tmp_path):
    """Build an MP4 H.264 command with the expected filter and output."""
    request = make_request(tmp_path, ExportFormat.MP4_H264)

    command = engine._build_command(request)

    assert command[command.index("-c:v") + 1] == "libx264"
    assert command[-1] == request.output_path
    assert command[command.index("-i") + 1].endswith("concat.txt")
    assert "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black,fps=30" in command
    engine._cleanup_temporary_directory()


def test_build_command_mov_prores(engine, tmp_path):
    """Build a MOV ProRes command with the requested codec."""
    request = make_request(tmp_path, ExportFormat.MOV_PRORES)

    command = engine._build_command(request)

    assert command[command.index("-c:v") + 1] == "prores_ks"
    assert command[command.index("-profile:v") + 1] == "3"
    engine._cleanup_temporary_directory()


def test_create_concat_file_format(engine, tmp_path):
    """Write source paths using the ffmpeg concat demuxer format."""
    source_path = tmp_path / "folder" / "clip one.mp4"
    source_path.parent.mkdir()
    clips = [
        {"id": "subtitle", "start": 0.0, "end": 2.0, "text": "Bonjour"},
        {"id": "clip", "start": 2.0, "end": 5.0, "source_path": str(source_path)},
    ]

    concat_path = engine._create_concat_file(clips, str(tmp_path))

    assert Path(concat_path).read_text(encoding="utf-8") == f"file '{source_path}'\n"
    engine._cleanup_temporary_directory()


def test_parse_progress_valid(engine):
    """Parse a valid ffmpeg progress line against a ten-second duration."""
    engine._duration_seconds = 10.0

    assert engine._parse_progress("out_time_ms=5000000") == 50


def test_parse_progress_invalid(engine):
    """Return no progress value for unrelated ffmpeg output."""
    engine._duration_seconds = 10.0

    assert engine._parse_progress("random output") is None


def test_missing_source_path_emits_warning(engine, tmp_path):
    """Warn and skip a media clip without source_path."""
    messages = []
    engine.status_changed.connect(messages.append)

    with pytest.raises(ValueError, match="Aucun média vidéo"):
        engine._create_concat_file(
            [{"id": "missing", "start": 0.0, "end": 1.0}],
            str(tmp_path),
        )

    assert "source_path manquant" in messages[0]
    engine._cleanup_temporary_directory()


def test_process_error_does_not_emit_failed_when_cancel_requested(engine):
    """Ignore Qt process errors emitted during a user-triggered cancel."""
    engine._cancel_requested = True
    messages = []
    engine.failed.connect(messages.append)

    engine._process_error(QProcess.Crashed)

    assert messages == []
