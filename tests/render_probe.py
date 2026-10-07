"""Rendus réels pour les tests : média de test, image d'un plan par le graphe de l'export.

Les tests de rendu de la vidéo sociale (effets, recadrage, calques de lumière) comparent des **pixels rendus par le vrai
FFmpeg** : comparer des chaînes de filtres a laissé passer des graphes que FFmpeg refusait. Le graphe est celui de
l'export (``ExportEngine._build_filter_complex``), que l'aperçu fidèle réutilise tel quel.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

HAS_FFMPEG = shutil.which("ffmpeg") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="FFmpeg absent : rendu réel impossible")


def lavfi_video(path: Path, source: str, *, size: tuple[int, int], fps: int = 25, seconds: float = 2.0) -> Path:
    """Vidéo H.264 (yuv444p, sans perte visible) produite par une source ``lavfi`` (``color=…``, ``testsrc2``…)."""
    width, height = size
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
         f"{source}:s={width}x{height}:r={fps}:d={seconds}", "-c:v", "libx264", "-qp", "0", "-pix_fmt", "yuv444p",
         str(path)],
        check=True, timeout=120,
    )
    return path


def render_frame(plan, width: int, height: int, t: float, *, fps: int = 25, quality: str = "export") -> np.ndarray:
    """Image RGB (``uint8``, ``height × width × 3``) du plan à ``t`` s, rendue par le graphe de l'export."""
    from core.export_engine import ExportEngine

    graph, video, audio, inputs = ExportEngine._build_filter_complex(plan, width, height, fps, None, quality=quality)
    graph += f";[{video}]trim=start={t},setpts=PTS-STARTPTS,format=rgb24[probe];[{audio}]anullsink"
    command = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    for path in inputs:
        command += ["-i", path]
    command += ["-filter_complex", graph, "-map", "[probe]", "-frames:v", "1", "-fps_mode", "passthrough", "-f", "rawvideo", "-"]
    completed = subprocess.run(command, capture_output=True, timeout=120)
    assert completed.returncode == 0, completed.stderr.decode("utf-8", "replace")
    return np.frombuffer(completed.stdout, dtype=np.uint8).reshape(height, width, 3)


def downscale(image: np.ndarray, factor: int) -> np.ndarray:
    """Réduction par moyenne de blocs ``factor × factor`` (équivalent d'un ``scale`` en ``area``)."""
    h, w = image.shape[0] // factor * factor, image.shape[1] // factor * factor
    blocks = image[:h, :w].astype(np.float64).reshape(h // factor, factor, w // factor, factor, -1)
    return blocks.mean(axis=(1, 3))
