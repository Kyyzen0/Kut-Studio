"""Grain procédural GPU (ADR-0001) : paramètres et référence numpy du shader ``grain``.

Le moniteur peut remplir une texture de travail avec la passe ``grain`` au lieu de
rastériser un PNG plein cadre à chaque image de grain (:mod:`core.light_layers`).
"""

from __future__ import annotations

import math

import numpy as np

from .light_layers import GRAIN_FRAMES, GRAIN_RATE


def grain_index(local_time: float) -> int:
    return int(math.floor(max(0.0, local_time) * GRAIN_RATE + 1e-6)) % GRAIN_FRAMES


def grain_cell_counts(width: float, height: float, density: float, device_scale: float = 1.0) -> tuple[int, int]:
    """Nombre de cellules (comme ``_grain`` : ``light_density * 1,5`` / échelle appareil)."""
    grain = max(1.0, float(density) * 1.5) / max(1e-3, float(device_scale))
    cols = max(2, int(math.ceil(width / grain)))
    rows = max(2, int(math.ceil(height / grain)))
    return cols, rows


def grain_uniforms(seed: int, index: int, cols: int, rows: int) -> tuple[float, float, float, float]:
    """``misc`` du shader ``grain`` : graine, index, cellules x, cellules y."""
    return (float(int(seed)), float(int(index) % GRAIN_FRAMES), float(cols), float(rows))


def reference_grain(width: int, height: int, seed: int, index: int, cols: int, rows: int) -> np.ndarray:
    """Référence numpy du shader (hash différent ; utile pour tests de forme, pas bit-exact)."""
    ys = (np.arange(height) * rows / height).astype(np.int32)
    xs = (np.arange(width) * cols / width).astype(np.int32)
    cell_y = ys[:, None]
    cell_x = xs[None, :]
    # Même esprit que le hash GLSL (déterministe, pas identique au GPU).
    n = np.sin((cell_x + seed * 17.0) * 12.9898 + (cell_y + index * 31.0) * 78.233) * 43758.5453
    n = n - np.floor(n)
    g = 0.25 + 0.5 * n
    return np.stack([g, g, g], axis=-1)


__all__ = ["grain_cell_counts", "grain_index", "grain_uniforms", "reference_grain"]
