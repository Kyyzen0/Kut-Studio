"""Aides des tests du texte 3D : maillage soudé (position), arêtes, caractéristique d'Euler, volume."""

from __future__ import annotations

from collections import Counter

import numpy as np

from kut3d.mathutil import Array


def welded(positions: Array, indices: Array) -> Array:
    """Indices de triangles après soudure des sommets confondus (position arrondie au cent-millième)."""
    key = np.round(positions.astype(np.float64) * 1e5).astype(np.int64)
    _, inverse = np.unique(key, axis=0, return_inverse=True)
    return np.asarray(inverse.reshape(-1)[indices])


def directed_edges(tri: Array) -> Counter[tuple[int, int]]:
    edges = np.concatenate([tri[:, [0, 1]], tri[:, [1, 2]], tri[:, [2, 0]]])
    return Counter(map(tuple, edges.tolist()))


def is_manifold(positions: Array, indices: Array) -> bool:
    """Chaque arête orientée est utilisée une seule fois et son opposée aussi : surface fermée, cohérente, sans pincement."""
    edges = directed_edges(welded(positions, indices))
    return all(n == 1 and edges.get((b, a), 0) == 1 for (a, b), n in edges.items())


def is_closed(positions: Array, indices: Array) -> bool:
    """Chaque arête orientée est parcourue autant de fois que son opposée : aucun bord ouvert, sens cohérent. Deux contours
    qui se touchent en un sommet (cédille, queue d'un « Q », trou tangent) donnent des arêtes verticales doublées : fermé,
    mais pas une variété."""
    edges = directed_edges(welded(positions, indices))
    return all(edges.get((b, a), 0) == n for (a, b), n in edges.items())


def euler_characteristic(positions: Array, indices: Array) -> int:
    tri = welded(positions, indices)
    undirected = {tuple(sorted(e)) for e in directed_edges(tri)}
    return int(len(np.unique(tri)) - len(undirected) + len(tri))


def volume(positions: Array, indices: Array) -> float:
    p = positions.astype(np.float64)
    a, b, c = p[indices[:, 0]], p[indices[:, 1]], p[indices[:, 2]]
    return float(np.sum(np.einsum("ij,ij->i", a, np.cross(b, c))) / 6.0)


def degenerate_triangles(positions: Array, indices: Array) -> int:
    tri = welded(positions, indices)
    return int(
        np.sum(
            (tri[:, 0] == tri[:, 1])
            | (tri[:, 1] == tri[:, 2])
            | (tri[:, 0] == tri[:, 2])
        )
    )
