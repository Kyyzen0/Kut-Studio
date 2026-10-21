"""Vidéos synthétiques pour les tests de tracking (reproductibles en CI).

Les images sont calculées par numpy puis encodées sans perte (``ffv1``) :
positions, rotations et zooms sont connus exactement à chaque image.
"""

from __future__ import annotations

import math
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

HAS_FFMPEG = shutil.which("ffmpeg") is not None


def blobs(np, width: int, height: int, points, *, sigma: float = 5.0, background: float = 0.0):
    """Image en niveaux de gris avec des taches gaussiennes texturées aux ``points``."""
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float64)
    image = np.full((height, width), background, dtype=np.float64)
    for x, y in points:
        image += 200.0 * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * sigma ** 2))
        image += 120.0 * np.exp(-((xx - x - sigma * 1.6) ** 2 + (yy - y + sigma) ** 2) / (2 * (sigma / 2) ** 2))
    return np.clip(image, 0, 255).astype(np.uint8)


def write_video(path: Path, frames: list, fps: float) -> Path:
    """Encode des images ``uint8`` (gris) sans perte."""
    height, width = frames[0].shape
    command = [
        "ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{width}x{height}",
        "-r", str(fps), "-i", "pipe:0", "-c:v", "ffv1", "-pix_fmt", "gray", str(path),
    ]
    process = subprocess.run(command, input=b"".join(f.tobytes() for f in frames), capture_output=True)
    if process.returncode != 0:
        raise RuntimeError(process.stderr.decode("utf-8", "replace"))
    return path


def moving_points_video(
    path: Path, *, width: int, height: int, fps: float, count: int,
    points_at: Callable[[int], list[tuple[float, float]]], sigma: float = 5.0,
) -> Path:
    import numpy as np

    frames = [blobs(np, width, height, points_at(i), sigma=sigma) for i in range(count)]
    return write_video(path, frames, fps)


def rigid_points(center, radius: float, angle_deg: float, scale: float, offsets):
    """Points ``offsets`` tournés de ``angle`` et mis à l'échelle autour de ``center``."""
    cx, cy = center
    a = math.radians(angle_deg)
    cos, sin = math.cos(a), math.sin(a)
    return [(cx + scale * (ox * cos - oy * sin), cy + scale * (ox * sin + oy * cos)) for ox, oy in offsets]
