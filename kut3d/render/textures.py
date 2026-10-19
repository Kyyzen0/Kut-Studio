"""Conversions de couleur sRGB / linéaire et échantillonnage bilinéaire des textures (wrap `repeat`).

Les textures `base_color` et `emissive` sont lues en sRGB et converties en linéaire AVANT le filtrage ; les autres
(normale, métal/rugosité, occlusion) sont lues telles quelles (linéaires). Pas de mipmaps : le sur-échantillonnage
(`samples`) atténue le crénelage des textures lointaines.
"""

from __future__ import annotations

import numpy as np

from kut3d.mathutil import Array

_SRGB_LUT: Array = np.where(
    (c := np.arange(256, dtype=np.float64) / 255.0) <= 0.04045,
    c / 12.92,
    ((c + 0.055) / 1.055) ** 2.4,
).astype(np.float32)
_SRGB_LUT.setflags(write=False)


def srgb_to_linear(x: Array) -> Array:
    """sRGB [0, 1] -> linéaire (float32)."""
    v = np.clip(np.asarray(x, dtype=np.float32), 0.0, 1.0)
    return np.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4).astype(
        np.float32
    )


def linear_to_srgb(x: Array) -> Array:
    """Linéaire [0, 1] -> sRGB (float32)."""
    v = np.clip(np.asarray(x, dtype=np.float32), 0.0, 1.0)
    return np.where(
        v <= 0.0031308, v * 12.92, 1.055 * np.power(v, 1.0 / 2.4) - 0.055
    ).astype(np.float32)


def sample_bilinear(image: Array, uv: Array, srgb: bool) -> Array:
    """Échantillonne `image` (H, W, 4 uint8) en `uv` (N, 2) : (N, 4) float32 dans [0, 1], linéaire.

    Origine en haut à gauche, V vers le bas (convention glTF) ; centres de texels à (i + 0.5) ; wrap `repeat`.
    Si `srgb`, les canaux RGB sont convertis en linéaire avant le filtrage (l'alpha reste linéaire).
    """
    h, w = image.shape[:2]
    uvv = np.nan_to_num(
        np.asarray(uv, dtype=np.float64), nan=0.0, posinf=0.0, neginf=0.0
    )
    uvv = uvv - np.floor(uvv)  # wrap repeat dans [0, 1[ avant tout calcul d'indice
    x = uvv[:, 0] * w - 0.5
    y = uvv[:, 1] * h - 0.5
    x0 = np.floor(x)
    y0 = np.floor(y)
    fx = (x - x0).astype(np.float32)[:, None]
    fy = (y - y0).astype(np.float32)[:, None]
    ix0 = x0.astype(np.int64) % w
    iy0 = y0.astype(np.int64) % h
    ix1 = (ix0 + 1) % w
    iy1 = (iy0 + 1) % h

    def fetch(iy: Array, ix: Array) -> Array:
        t = image[iy, ix]
        out = np.empty(t.shape, dtype=np.float32)
        if srgb:
            out[:, :3] = _SRGB_LUT[t[:, :3]]
        else:
            out[:, :3] = t[:, :3] * np.float32(1.0 / 255.0)
        out[:, 3] = t[:, 3] * np.float32(1.0 / 255.0)
        return out

    top = fetch(iy0, ix0) * (1 - fx) + fetch(iy0, ix1) * fx
    bottom = fetch(iy1, ix0) * (1 - fx) + fetch(iy1, ix1) * fx
    return np.asarray(top * (1 - fy) + bottom * fy, dtype=np.float32)
