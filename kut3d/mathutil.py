"""Matrices et quaternions (numpy pur). Main droite, Y vers le haut ; quaternions (x, y, z, w)."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

import numpy as np
import numpy.typing as npt

Array = npt.NDArray[Any]

IDENTITY_QUATERNION = (0.0, 0.0, 0.0, 1.0)


def normalize_quaternion(
    q: Sequence[float] | Array,
) -> tuple[float, float, float, float]:
    """Quaternion unitaire ; un quaternion nul ou non fini redevient l'identité."""
    x, y, z, w = (float(c) for c in q)
    n = math.sqrt(x * x + y * y + z * z + w * w)
    if not math.isfinite(n) or n < 1e-12:
        return IDENTITY_QUATERNION
    return (x / n, y / n, z / n, w / n)


def quat_to_matrix(q: Sequence[float]) -> Array:
    """Matrice de rotation 3x3 (float64) d'un quaternion (x, y, z, w)."""
    x, y, z, w = normalize_quaternion(q)
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ],
        dtype=np.float64,
    )


def matrix_to_quat(r: Array) -> tuple[float, float, float, float]:
    """Quaternion (x, y, z, w) d'une matrice de rotation 3x3."""
    tr = float(r[0, 0] + r[1, 1] + r[2, 2])
    if tr > 0:
        s = math.sqrt(tr + 1.0) * 2
        q = (
            (r[2, 1] - r[1, 2]) / s,
            (r[0, 2] - r[2, 0]) / s,
            (r[1, 0] - r[0, 1]) / s,
            0.25 * s,
        )
    elif r[0, 0] > r[1, 1] and r[0, 0] > r[2, 2]:
        s = math.sqrt(1.0 + r[0, 0] - r[1, 1] - r[2, 2]) * 2
        q = (
            0.25 * s,
            (r[0, 1] + r[1, 0]) / s,
            (r[0, 2] + r[2, 0]) / s,
            (r[2, 1] - r[1, 2]) / s,
        )
    elif r[1, 1] > r[2, 2]:
        s = math.sqrt(1.0 + r[1, 1] - r[0, 0] - r[2, 2]) * 2
        q = (
            (r[0, 1] + r[1, 0]) / s,
            0.25 * s,
            (r[1, 2] + r[2, 1]) / s,
            (r[0, 2] - r[2, 0]) / s,
        )
    else:
        s = math.sqrt(1.0 + r[2, 2] - r[0, 0] - r[1, 1]) * 2
        q = (
            (r[0, 2] + r[2, 0]) / s,
            (r[1, 2] + r[2, 1]) / s,
            0.25 * s,
            (r[1, 0] - r[0, 1]) / s,
        )
    return normalize_quaternion(q)


def trs_to_matrix(
    translation: Sequence[float], rotation: Sequence[float], scale: Sequence[float]
) -> Array:
    """Matrice 4x4 (float64) T @ R @ S."""
    m = np.eye(4, dtype=np.float64)
    m[:3, :3] = quat_to_matrix(rotation) * np.asarray(scale, dtype=np.float64)[None, :]
    m[:3, 3] = np.asarray(translation, dtype=np.float64)
    return m


def matrix_to_trs(
    m: Array,
) -> tuple[
    tuple[float, float, float],
    tuple[float, float, float, float],
    tuple[float, float, float],
]:
    """Décompose une matrice 4x4 en (translation, quaternion, échelle). Le cisaillement est ignoré."""
    t = (float(m[0, 3]), float(m[1, 3]), float(m[2, 3]))
    lin = np.asarray(m[:3, :3], dtype=np.float64)
    s = np.linalg.norm(lin, axis=0)
    if np.linalg.det(lin) < 0:
        s[0] = -s[0]
    safe = np.where(np.abs(s) < 1e-12, 1.0, s)
    q = matrix_to_quat(lin / safe[None, :])
    return t, q, (float(s[0]), float(s[1]), float(s[2]))


def translation_matrix(offset: Sequence[float] | Array) -> Array:
    m = np.eye(4, dtype=np.float64)
    m[:3, 3] = np.asarray(offset, dtype=np.float64)
    return m


def scale_matrix(factor: float) -> Array:
    m = np.eye(4, dtype=np.float64)
    m[0, 0] = m[1, 1] = m[2, 2] = factor
    return m


def rotation_x(degrees: float) -> Array:
    """Rotation de `degrees` autour de X (main droite, 4x4 float64)."""
    a = math.radians(degrees)
    c, s = math.cos(a), math.sin(a)
    m = np.eye(4, dtype=np.float64)
    m[1, 1], m[1, 2], m[2, 1], m[2, 2] = c, -s, s, c
    return m
