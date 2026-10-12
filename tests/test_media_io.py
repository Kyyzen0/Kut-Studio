"""Entrées / sorties média (ADR-0001) : sonde, décodage CLI, garde d'import PyAV."""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

from core import media_io

ROOT = Path(__file__).resolve().parents[1]
HAS_FFMPEG = bool(__import__("shutil").which("ffmpeg") and __import__("shutil").which("ffprobe"))
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="FFmpeg absent")


@needs_ffmpeg
def test_decode_preview_frame_via_cli(tmp_path):
    source = tmp_path / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=64x36:r=10:d=1",
         "-pix_fmt", "yuv420p", str(source)],
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


def test_prefer_proxy_falls_back_to_original(tmp_path):
    original = tmp_path / "a.mp4"
    original.write_bytes(b"x")
    assert media_io.prefer_proxy(str(original), None) == str(original)
    assert media_io.prefer_proxy(str(original), str(tmp_path / "missing.mp4")) == str(original)
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
                if isinstance(node, ast.ImportFrom) and node.module and (
                    node.module == "av" or node.module.startswith("av.")
                ):
                    offenders.append(relative)
    assert offenders == [], "import av hors de core/media_io.py : " + ", ".join(sorted(set(offenders)))
