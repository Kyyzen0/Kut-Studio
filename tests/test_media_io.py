"""Entrées / sorties média (ADR-0001) : sonde, décodage CLI, garde d'import PyAV."""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

from core import media_io

ROOT = Path(__file__).resolve().parents[1]
HAS_FFMPEG = bool(
    __import__("shutil").which("ffmpeg") and __import__("shutil").which("ffprobe")
)
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="FFmpeg absent")


@needs_ffmpeg
def test_decode_preview_frame_via_cli(tmp_path):
    source = tmp_path / "clip.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=64x36:r=10:d=1",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        check=True,
    )
    info = media_io.probe(source)
    assert info.width == 64 and info.height == 36
    assert info.duration > 0.5
    frame = media_io.decode_preview_frame(source, 0.2)
    assert frame.width == 64 and frame.height == 36
    assert len(frame.rgba) == 64 * 36 * 4
    with media_io.open_media(source) as handle:
        assert handle.backend in ("pyav", "ffmpeg-cli")
        again = handle.decode_at(0.0)
        assert len(again.rgba) == 64 * 36 * 4


def test_parse_probe_csv_uses_container_duration_when_stream_is_na():
    """Matroska et WebM : ffprobe écrit ``N/A`` pour la durée du flux, puis la durée du conteneur."""
    width, height, fps, duration = media_io._parse_probe_csv(
        "32,18,10/1,N/A\n0.400000\n"
    )
    assert (width, height) == (32, 18)
    assert fps == 10
    assert duration == pytest.approx(0.4)


def test_parse_probe_csv_keeps_stream_duration_for_mp4():
    width, height, fps, duration = media_io._parse_probe_csv(
        "64,36,10/1,1.000000\n1.000000\n"
    )
    assert (width, height, fps) == (64, 36, 10)
    assert duration == pytest.approx(1.0)


@needs_ffmpeg
def test_probe_matroska_when_stream_duration_is_na(tmp_path):
    """Un MKV réel : la sonde ne lève plus, et une image se décode (piste underlay du moniteur GPU)."""
    source = tmp_path / "clip.mkv"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=32x18:r=10:d=0.4",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        check=True,
    )
    info = media_io.probe(source)
    assert info.width == 32 and info.height == 18
    assert info.duration == pytest.approx(0.4, abs=0.05)
    frame = media_io.decode_preview_frame(source, 0.1)
    assert frame.width == 32 and frame.height == 18
    assert len(frame.rgba) == 32 * 18 * 4


@needs_ffmpeg
def test_probe_webm_when_stream_duration_is_na(tmp_path):
    """Un WebM réel (même forme ffprobe N/A) : sonde + décodage underlay."""
    source = tmp_path / "clip.webm"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=32x18:r=10:d=0.4",
            "-c:v",
            "libvpx",
            "-pix_fmt",
            "yuv420p",
            "-b:v",
            "200k",
            str(source),
        ],
        check=True,
    )
    info = media_io.probe(source)
    assert info.width == 32 and info.height == 18
    assert info.duration == pytest.approx(0.4, abs=0.1)
    frame = media_io.decode_preview_frame(source, 0.1)
    assert frame.width == 32 and frame.height == 18
    assert len(frame.rgba) == 32 * 18 * 4


def test_parse_probe_csv_rejects_empty_and_audio_only():
    with pytest.raises(OSError, match="flux vidéo"):
        media_io._parse_probe_csv("")
    with pytest.raises(OSError, match="flux vidéo"):
        media_io._parse_probe_csv("0.400000\n")


def test_prefer_proxy_falls_back_to_original(tmp_path):
    original = tmp_path / "a.mp4"
    original.write_bytes(b"x")
    assert media_io.prefer_proxy(str(original), None) == str(original)
    assert media_io.prefer_proxy(str(original), str(tmp_path / "missing.mp4")) == str(
        original
    )
    proxy = tmp_path / "a_proxy.mp4"
    proxy.write_bytes(b"y")
    assert media_io.prefer_proxy(str(original), str(proxy)) == str(proxy)


def test_pyav_import_stays_inside_media_io():
    """Seul ``core/media_io.py`` peut importer ``av`` (contrat ADR-0001)."""
    offenders = []
    for folder in ("core", "ui", "tools"):
        for path in sorted((ROOT / folder).rglob("*.py")):
            relative = path.relative_to(ROOT).as_posix()
            if relative == "core/media_io.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name == "av" or alias.name.startswith("av."):
                            offenders.append(relative)
                if (
                    isinstance(node, ast.ImportFrom)
                    and node.module
                    and (node.module == "av" or node.module.startswith("av."))
                ):
                    offenders.append(relative)
    assert offenders == [], "import av hors de core/media_io.py : " + ", ".join(
        sorted(set(offenders))
    )
