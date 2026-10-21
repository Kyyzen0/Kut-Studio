"""Scènes synthétiques à vérité terrain exacte pour le flux optique.

Chaque scène est une fonction analytique du temps continu ``tau`` (``tau`` entier = une image) : on peut donc rendre l'image
« vraie » à n'importe quelle position fractionnaire (``tau = i + t``) et la comparer à ce que l'interpolation fabrique.
Les textures sont définies **dans le repère de l'objet** (elles suivent l'objet) ; les bords sont anticrénelés par
suréchantillonnage 3×3, de sorte qu'une position à 0,1 px près est visible dans l'image.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

Path = Callable[[float], tuple[float, float, float, float]]
"""Temps -> (x, y, angle en radians, échelle) du centre de l'objet."""


def _object_texture(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    return 0.78 + 0.12 * np.sin(0.9 * x + 0.35 * y) * np.cos(0.55 * y - 0.25 * x) + 0.08 * np.sin(0.21 * x - 0.43 * y + 1.0)


def _background_texture(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    return 0.30 + 0.07 * np.sin(0.31 * x + 0.17 * y) + 0.06 * np.cos(0.23 * y - 0.11 * x + 0.4) + 0.04 * np.sin(0.7 * x * 0.3 + 0.9 * y * 0.2)


@dataclass
class Body:
    """Un carré texturé (côté ``size`` px) qui suit ``path``."""

    size: float
    path: Path
    texture: Callable[[np.ndarray, np.ndarray], np.ndarray] = _object_texture
    depth: int = 0


@dataclass
class Scene:
    width: int
    height: int
    bodies: list[Body] = field(default_factory=list)
    background: Callable[[np.ndarray, np.ndarray], np.ndarray] = _background_texture

    def render(self, tau: float, samples: int = 3) -> np.ndarray:
        """Luminance (0…1, ``float32``) à l'instant ``tau``, avec anticrénelage ``samples × samples``."""
        offsets = (np.arange(samples) + 0.5) / samples - 0.5
        total = np.zeros((self.height, self.width), dtype=np.float64)
        ys, xs = np.mgrid[0:self.height, 0:self.width].astype(np.float64)
        for oy in offsets:
            for ox in offsets:
                total += self._render_at(xs + ox, ys + oy, tau)
        return (total / (samples * samples)).astype(np.float32)

    def _render_at(self, xs: np.ndarray, ys: np.ndarray, tau: float) -> np.ndarray:
        image = self.background(xs, ys)
        for body in sorted(self.bodies, key=lambda item: item.depth):
            cx, cy, angle, scale = body.path(tau)
            dx, dy = xs - cx, ys - cy
            cos, sin = np.cos(-angle), np.sin(-angle)
            local_x = (cos * dx - sin * dy) / scale
            local_y = (sin * dx + cos * dy) / scale
            half = body.size / 2.0
            inside = (np.abs(local_x) <= half) & (np.abs(local_y) <= half)
            image = np.where(inside, body.texture(local_x, local_y), image)
        return image

    def centroid(self, frame: np.ndarray, threshold: float = 0.55) -> tuple[float, float]:
        """Barycentre (px) des pixels plus clairs que ``threshold`` (le corps, plus clair que le fond), pondéré par l'excédent."""
        weights = np.clip(frame - threshold, 0.0, None).astype(np.float64)
        ys, xs = np.mgrid[0:frame.shape[0], 0:frame.shape[1]]
        total = weights.sum()
        return float((weights * xs).sum() / total), float((weights * ys).sum() / total)


def linear(x0: float, y0: float, vx: float, vy: float, angle: float = 0.0, spin: float = 0.0, scale: float = 1.0,
           zoom: float = 0.0) -> Path:
    """Trajectoire à vitesse, rotation et zoom constants."""
    return lambda tau: (x0 + vx * tau, y0 + vy * tau, angle + spin * tau, scale + zoom * tau)


def accelerated(x0: float, y0: float, v0: float, acceleration: float) -> Path:
    """Mouvement horizontal uniformément accéléré (``x = x0 + v0·τ + a·τ²/2``)."""
    return lambda tau: (x0 + v0 * tau + 0.5 * acceleration * tau * tau, y0, 0.0, 1.0)


def rgb(luma: np.ndarray) -> np.ndarray:
    """Une luminance en image ``(h, w, 3)`` (le mélange et la synthèse travaillent sur des images couleur)."""
    return np.repeat(luma[:, :, None], 3, axis=2).astype(np.float32)
