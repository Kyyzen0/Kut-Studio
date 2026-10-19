"""Outils géométriques vectorisés : normales lisses, triangulation en éventail."""

from __future__ import annotations

import numpy as np

from kut3d.mathutil import Array


def smooth_normals(
    positions: Array, indices: Array, groups: Array | None = None
) -> Array:
    """Normales de sommet lisses (moyenne pondérée par l'aire des faces), float32 (N, 3).

    `groups` (N,) soude les sommets de même groupe (typiquement un même indice de position) : les coutures
    d'UV n'y créent alors pas de rupture d'ombrage. Un sommet sans face valide reçoit (0, 1, 0).
    """
    pos = np.asarray(positions, dtype=np.float64)
    tri = np.asarray(indices, dtype=np.int64)
    n = pos.shape[0]
    if tri.size == 0:
        out = np.zeros((n, 3), dtype=np.float32)
        out[:, 1] = 1.0
        return out
    face = np.cross(pos[tri[:, 1]] - pos[tri[:, 0]], pos[tri[:, 2]] - pos[tri[:, 0]])
    if groups is None:
        key = tri
        size = n
    else:
        g = np.asarray(groups, dtype=np.int64)
        key = g[tri]
        size = int(np.max(g)) + 1
    acc = np.zeros((size, 3), dtype=np.float64)
    for corner in range(3):
        for axis in range(3):
            acc[:, axis] += np.bincount(
                key[:, corner], weights=face[:, axis], minlength=size
            )
    normals = acc if groups is None else acc[np.asarray(groups, dtype=np.int64)]
    length = np.linalg.norm(normals, axis=1)
    bad = length < 1e-20
    normals = normals / np.where(bad, 1.0, length)[:, None]
    normals[bad] = (0.0, 1.0, 0.0)
    return normals.astype(np.float32)


def fan_triangulate_fixed(faces: Array) -> Array:
    """Triangule en éventail des polygones de même taille (F, n) -> (F * (n - 2), 3)."""
    f = np.asarray(faces)
    n = f.shape[1]
    if n == 3:
        return f.reshape(-1, 3)
    k = np.arange(1, n - 1)
    tri = np.stack(
        [np.broadcast_to(f[:, :1], (f.shape[0], n - 2)), f[:, k], f[:, k + 1]], axis=2
    )
    return tri.reshape(-1, 3)


def fan_triangulate_ragged(starts: Array, counts: Array) -> tuple[Array, Array]:
    """Éventail pour des polygones de tailles variables.

    `starts`/`counts` décrivent des polygones dont les coins sont contigus dans un tableau plat.
    Retourne (coins (T, 3) = positions dans ce tableau plat, face d'origine (T,)). Les polygones de moins de
    3 coins ne donnent aucun triangle.
    """
    starts = np.asarray(starts, dtype=np.int64)
    counts = np.asarray(counts, dtype=np.int64)
    ntri = np.maximum(counts - 2, 0)
    total = int(ntri.sum())
    face_of = np.repeat(np.arange(len(counts)), ntri)
    first = np.cumsum(ntri) - ntri
    k = np.arange(total) - np.repeat(first, ntri)
    s = starts[face_of]
    return np.stack([s, s + k + 1, s + k + 2], axis=1), face_of
