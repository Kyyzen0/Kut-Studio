"""Conversions de couleur sRGB / linéaire et échantillonnage des textures (bilinéaire ou plus proche, wrap `repeat` / `clamp` / `mirror`).

Les textures `base_color` et `emissive` sont lues en sRGB et converties en linéaire AVANT le filtrage ; les autres
(normale, métal/rugosité, occlusion) sont lues telles quelles (linéaires). Pas de mipmaps : le sur-échantillonnage
(`samples`) atténue le crénelage des textures lointaines.
"""

from __future__ import annotations

import numpy as np

from kut3d.mathutil import Array
from kut3d.model import DEFAULT_SAMPLER, SamplerKey

_SRGB_LUT: Array = np.where(
    (c := np.arange(256, dtype=np.float64) / 255.0) <= 0.04045,
    c / 12.92,
    ((c + 0.055) / 1.055) ** 2.4,
).astype(np.float32)
_SRGB_LUT.setflags(write=False)
_UV_LIMIT = 1.0e6


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


def _wrap_index(i: Array, size: int, mode: str) -> Array:
    """Indice de texel entier ramené dans [0, size[ selon le mode d'adressage (comme le fait le matériel)."""
    if mode == "clamp":
        return np.clip(i, 0, size - 1)
    if mode == "mirror":
        k = i % (2 * size)
        return np.where(k >= size, 2 * size - 1 - k, k)
    return i % size


def sample_bilinear(
    image: Array, uv: Array, srgb: bool, sampler: SamplerKey | None = None
) -> Array:
    """Échantillonne `image` (H, W, 4 uint8) en `uv` (N, 2) : (N, 4) float32 dans [0, 1], linéaire.

    Origine en haut à gauche, V vers le bas (convention glTF) ; centres de texels à (i + 0.5). `sampler`
    (`TextureRef.sampler` : wrap_s, wrap_t, mag_filter, min_filter) ; absent : répétition + bilinéaire. Le moteur
    numpy n'estime pas le niveau de détail : il utilise le filtre d'agrandissement (`mag_filter`) ; le moteur GPU
    applique `min_filter` aux surfaces réduites. Si `srgb`, les canaux RGB sont convertis en linéaire avant le
    filtrage (l'alpha reste linéaire).
    """
    wrap_s, wrap_t, mag, _min = DEFAULT_SAMPLER if sampler is None else sampler
    h, w = image.shape[:2]
    uvv = np.nan_to_num(
        np.asarray(uv, dtype=np.float64), nan=0.0, posinf=0.0, neginf=0.0
    )
    uvv = np.clip(
        uvv, -_UV_LIMIT, _UV_LIMIT
    )  # évite le débordement des indices entiers

    def fetch(iy: Array, ix: Array) -> Array:
        t = image[_wrap_index(iy, h, wrap_t), _wrap_index(ix, w, wrap_s)]
        out = np.empty(t.shape, dtype=np.float32)
        if srgb:
            out[:, :3] = _SRGB_LUT[t[:, :3]]
        else:
            out[:, :3] = t[:, :3] * np.float32(1.0 / 255.0)
        out[:, 3] = t[:, 3] * np.float32(1.0 / 255.0)
        return out

    if mag == "nearest":
        return fetch(
            np.floor(uvv[:, 1] * h).astype(np.int64),
            np.floor(uvv[:, 0] * w).astype(np.int64),
        )
    x = uvv[:, 0] * w - 0.5
    y = uvv[:, 1] * h - 0.5
    x0 = np.floor(x)
    y0 = np.floor(y)
    fx = (x - x0).astype(np.float32)[:, None]
    fy = (y - y0).astype(np.float32)[:, None]
    ix0 = x0.astype(np.int64)
    iy0 = y0.astype(np.int64)
    ix1 = ix0 + 1
    iy1 = iy0 + 1
    top = fetch(iy0, ix0) * (1 - fx) + fetch(iy0, ix1) * fx
    bottom = fetch(iy1, ix0) * (1 - fx) + fetch(iy1, ix1) * fx
    return np.asarray(top * (1 - fy) + bottom * fy, dtype=np.float32)
