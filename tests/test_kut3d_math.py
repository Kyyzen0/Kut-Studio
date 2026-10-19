"""Matrices, quaternions et géométrie vectorisée de Kut-3D."""

from __future__ import annotations

import numpy as np
import pytest

from kut3d.geometry import fan_triangulate_fixed, fan_triangulate_ragged, smooth_normals
from kut3d.mathutil import (
    matrix_to_quat,
    matrix_to_trs,
    normalize_quaternion,
    quat_to_matrix,
    rotation_x,
    trs_to_matrix,
)


@pytest.mark.parametrize(
    "axis", [(1, 0, 0), (0, 1, 0), (0, 0, 1), (1, 1, 1), (-1, 2, 0.5)]
)
@pytest.mark.parametrize("degrees", [0, 30, 90, 179, 180, 270, -45])
def test_quaternion_matrix_round_trip(
    axis: tuple[float, float, float], degrees: float
) -> None:
    a = np.asarray(axis, dtype=float)
    a /= np.linalg.norm(a)
    half = np.radians(degrees) / 2
    q = (*(a * np.sin(half)), np.cos(half))
    back = matrix_to_quat(quat_to_matrix(q))
    # q et -q sont la même rotation
    assert np.allclose(quat_to_matrix(back), quat_to_matrix(q), atol=1e-9)


def test_trs_round_trip_including_negative_scale() -> None:
    q = normalize_quaternion((0.3, -0.5, 0.2, 0.8))
    for scale in ((1, 2, 3), (-1, 2, 3), (0.5, 0.5, 0.5)):
        m = trs_to_matrix((1, 2, 3), q, scale)
        t, r, s = matrix_to_trs(m)
        assert np.allclose(trs_to_matrix(t, r, s), m, atol=1e-9)
        assert np.allclose(t, (1, 2, 3))


def test_degenerate_quaternion_becomes_identity() -> None:
    assert normalize_quaternion((0, 0, 0, 0)) == (0.0, 0.0, 0.0, 1.0)
    assert normalize_quaternion((float("nan"), 0, 0, 1)) == (0.0, 0.0, 0.0, 1.0)


def test_rotation_x_is_right_handed() -> None:
    # +90° autour de X : Y -> Z
    assert np.allclose(rotation_x(90)[:3, :3] @ [0, 1, 0], [0, 0, 1], atol=1e-12)
    # -90° autour de X : Z -> Y (conversion Z haut -> Y haut)
    assert np.allclose(rotation_x(-90)[:3, :3] @ [0, 0, 1], [0, 1, 0], atol=1e-12)


def test_smooth_normals_edge_cases() -> None:
    pos = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [5, 5, 5]], dtype=np.float32)
    assert np.allclose(
        smooth_normals(pos, np.zeros((0, 3), dtype=np.uint32)), [0, 1, 0]
    )
    n = smooth_normals(pos, np.array([[0, 1, 2]], dtype=np.uint32))
    assert np.allclose(n[:3], [0, 0, 1]) and np.allclose(
        n[3], [0, 1, 0]
    )  # sommet isolé : (0, 1, 0)
    flat = np.array(
        [[0, 0, 0], [1, 0, 0], [2, 0, 0]], dtype=np.float32
    )  # triangle dégénéré
    assert np.allclose(
        smooth_normals(flat, np.array([[0, 1, 2]], dtype=np.uint32)), [0, 1, 0]
    )


def test_fan_triangulation() -> None:
    quads = np.array([[0, 1, 2, 3], [4, 5, 6, 7]])
    assert fan_triangulate_fixed(quads).tolist() == [
        [0, 1, 2],
        [0, 2, 3],
        [4, 5, 6],
        [4, 6, 7],
    ]
    corners, face_of = fan_triangulate_ragged([0, 4, 7], [4, 3, 2])
    assert corners.tolist() == [[0, 1, 2], [0, 2, 3], [4, 5, 6]]
    assert face_of.tolist() == [0, 0, 1]
