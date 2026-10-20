"""Triangulation de polygones à trous et décalage de contours : sans police, sans Qt (formes écrites à la main)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from kut3d.triangulate import (
    classify,
    clean_ring,
    cross_properly,
    inset_ring,
    signed_area,
    triangles_area,
    triangulate,
    vertex_normals,
)


def _circle(radius: float, n: int, cx: float = 0.0, cy: float = 0.0, ccw: bool = True) -> np.ndarray:
    t = 2 * math.pi * np.arange(n) / n
    pts = np.stack([cx + radius * np.cos(t), cy + radius * np.sin(t)], axis=1)
    return pts if ccw else pts[::-1].copy()


def _box(x0: float, y0: float, x1: float, y1: float, ccw: bool = True) -> np.ndarray:
    pts = np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], dtype=float)
    return pts if ccw else pts[::-1].copy()


def _check(outer: np.ndarray, holes: list[np.ndarray]) -> np.ndarray:
    pts = np.vstack([outer, *holes])
    tris = triangulate(outer, holes)
    expected = signed_area(outer) + sum(signed_area(h) for h in holes)
    assert triangles_area(pts, tris) == pytest.approx(expected, rel=1e-9)
    p = pts[tris]
    areas = 0.5 * ((p[:, 1, 0] - p[:, 0, 0]) * (p[:, 2, 1] - p[:, 0, 1]) - (p[:, 1, 1] - p[:, 0, 1]) * (p[:, 2, 0] - p[:, 0, 0]))
    assert (areas > 0).all()  # tous en sens direct, aucun dégénéré
    assert len(tris) == len(pts) + 2 * len(holes) - 2  # un polygone à h trous : n + 2h - 2 triangles
    return tris


def _centroid_in(ring: np.ndarray, pts: np.ndarray, tris: np.ndarray) -> int:
    from kut3d.triangulate import _contains

    c = pts[tris].mean(axis=1)
    return sum(_contains(ring, x, y) for x, y in c)


def test_square_with_a_square_hole() -> None:
    outer, hole = _box(0, 0, 4, 4), _box(1, 1, 3, 3, ccw=False)
    tris = _check(outer, [hole])
    assert _centroid_in(hole, np.vstack([outer, hole]), tris) == 0  # aucun triangle dans le trou


def test_a_ring_like_an_o() -> None:
    outer, hole = _circle(1.0, 40), _circle(0.55, 28, ccw=False)
    tris = _check(outer, [hole])
    assert _centroid_in(hole, np.vstack([outer, hole]), tris) == 0


def test_two_holes_like_a_b() -> None:
    outer = _box(0, 0, 4, 8)
    holes = [_box(1, 1, 3, 3, ccw=False), _box(1, 5, 3, 7, ccw=False)]
    tris = _check(outer, holes)
    pts = np.vstack([outer, *holes])
    assert all(_centroid_in(h, pts, tris) == 0 for h in holes)


def test_a_concave_polygon_without_hole() -> None:
    c_shape = np.array([[0, 0], [4, 0], [4, 1], [1, 1], [1, 3], [4, 3], [4, 4], [0, 4]], dtype=float)
    _check(c_shape, [])


def test_a_hole_close_to_the_border_and_a_triangle_hole() -> None:
    outer = _box(0, 0, 10, 10)
    holes = [_box(0.05, 4, 0.5, 6, ccw=False), _circle(1.0, 3, cx=6, cy=5, ccw=False)]
    _check(outer, holes)


def test_a_hole_in_a_concave_outline_is_bridged_inside_the_matter() -> None:
    c_shape = np.array([[0, 0], [8, 0], [8, 2], [2, 2], [2, 6], [8, 6], [8, 8], [0, 8]], dtype=float)
    hole = _box(0.5, 3, 1.5, 5, ccw=False)
    tris = _check(c_shape, [hole])
    assert _centroid_in(hole, np.vstack([c_shape, hole]), tris) == 0


def test_the_triangulation_is_deterministic() -> None:
    outer, hole = _circle(1.0, 33), _circle(0.4, 17, ccw=False)
    assert np.array_equal(triangulate(outer, [hole]), triangulate(outer, [hole]))


def test_classify_nests_by_parity_and_orients_the_rings() -> None:
    big = _box(0, 0, 10, 10, ccw=False)  # mal orienté exprès
    hole = _box(2, 2, 8, 8)  # idem
    island = _box(4, 4, 6, 6, ccw=False)  # îlot dans le trou : un nouvel extérieur
    shapes = classify([big, hole, island])
    assert len(shapes) == 2
    (outer, holes), (inner, inner_holes) = shapes
    assert signed_area(outer) > 0 and len(holes) == 1 and signed_area(holes[0]) < 0
    assert signed_area(inner) > 0 and inner_holes == []


def test_clean_ring_drops_duplicates_collinear_points_and_flat_contours() -> None:
    ring = np.array([[0, 0], [1, 0], [2, 0], [2, 0], [2, 2], [0, 2]], dtype=float)
    cleaned = clean_ring(ring)
    assert cleaned is not None and len(cleaned) == 4
    assert clean_ring(np.array([[0, 0], [1, 0], [2, 0]], dtype=float)) is None
    assert clean_ring(np.array([[0, 0], [1, 1]], dtype=float)) is None


def test_inset_of_a_square_is_the_smaller_square() -> None:
    inner = inset_ring(_box(0, 0, 2, 2), 0.25)
    assert signed_area(inner) == pytest.approx(1.5**2)
    assert inner.min() == pytest.approx(0.25) and inner.max() == pytest.approx(1.75)


def test_inset_of_a_hole_grows_it() -> None:
    hole = _box(1, 1, 3, 3, ccw=False)
    grown = inset_ring(hole, 0.5)
    assert abs(signed_area(grown)) == pytest.approx(9.0) and signed_area(grown) < 0


def test_inset_never_flips_a_tight_curve() -> None:
    tiny = _circle(0.05, 12)
    assert signed_area(inset_ring(tiny, 0.2)) > 0  # déplacement borné : le petit cercle ne se retourne pas
    assert not cross_properly([inset_ring(tiny, 0.2)], [inset_ring(tiny, 0.2)])


def test_cross_properly_detects_a_real_crossing_only() -> None:
    a, b = _box(0, 0, 2, 2), _box(1, 1, 3, 3)
    assert cross_properly([a], [b])
    assert not cross_properly([a], [_box(5, 5, 6, 6)])
    assert not cross_properly([a], [a])  # arêtes confondues : pas de croisement franc


def test_vertex_normals_are_smooth_on_curves_and_sharp_on_corners() -> None:
    start, end = vertex_normals(_circle(1.0, 64))
    assert np.allclose(np.hypot(*start.T), 1.0) and np.allclose(np.hypot(*end.T), 1.0)
    assert np.all(np.sum(start * end, axis=1) > 0.99)  # presque identiques le long d'une courbe lissée
    s, e = vertex_normals(_box(0, 0, 1, 1))
    assert np.allclose(s, [[0, -1], [1, 0], [0, 1], [-1, 0]]) and np.allclose(e, s)  # franches : normale de l'arête
