"""Texture procédurale : damier RGBA uint8, sans fichier externe."""

from __future__ import annotations

import numpy as np


def checker_texture(size: int = 256, tiles: int = 8) -> np.ndarray:
    """Damier orange / crème de size x size pixels, tiles x tiles cases, tableau (size, size, 4) uint8."""
    cell = size // tiles
    yy, xx = np.mgrid[0:size, 0:size]
    odd = ((xx // cell) + (yy // cell)) % 2 == 1
    tex = np.empty((size, size, 4), np.uint8)
    tex[...] = (235, 120, 40, 255)
    tex[odd] = (245, 235, 210, 255)
    return tex
