"""Génération déterministe de la géométrie des formes de base (fonctions pures, numpy seul).

`primitive_arrays(spec)` retourne `(positions, normals, uvs, indices)` : float32 (V, 3), float32 (V, 3) unitaires, float32 (V, 2),
uint32 (T, 3). Les comptes viennent des formules de :mod:`kut3d.primitives` (vérifiées par les tests contre ce qui est généré) ;
le plafond de triangles est déjà appliqué par la construction de la description, AVANT tout appel d'ici.
Triangles en sens inverse des aiguilles d'une montre vu de l'extérieur ; UV à la glTF (V vers le bas).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np

from kut3d.mathutil import Array
from kut3d.primitives import Cone, Cube, Cylinder, Plane, PrimitiveSpec, Sphere, Torus

Arrays = tuple[Array, Array, Array, Array]

# (normale, axe u de la texture, axe v « vers le haut de l'image »), avec u x v = normale (sens direct vu de dehors).
_CUBE_FACES = (
    ((0, 0, 1), (1, 0, 0), (0, 1, 0)),
    ((0, 0, -1), (-1, 0, 0), (0, 1, 0)),
    ((1, 0, 0), (0, 0, -1), (0, 1, 0)),
    ((-1, 0, 0), (0, 0, 1), (0, 1, 0)),
    ((0, 1, 0), (1, 0, 0), (0, 0, -1)),
    ((0, -1, 0), (1, 0, 0), (0, 0, 1)),
)
_CORNERS = ((-1, -1), (1, -1), (1, 1), (-1, 1))


def _finish(pos: Array, nrm: Array, uv: Array, tri: Array) -> Arrays:
    length = np.linalg.norm(nrm, axis=1, keepdims=True)
    nrm = nrm / np.where(length > 0, length, 1.0)
    return (
        np.ascontiguousarray(pos, dtype=np.float32),
        np.ascontiguousarray(nrm, dtype=np.float32),
        np.ascontiguousarray(uv, dtype=np.float32),
        np.ascontiguousarray(tri, dtype=np.uint32),
    )


def _grid_quads(cols: int, rows: int) -> tuple[Array, Array, Array, Array]:
    """Coins (a, b, c, d) de chaque case d'une grille de sommets (rows + 1) x (cols + 1), rangée par rangée :
    a = (rangée r, colonne j), b = colonne suivante, c = rangée suivante, d = les deux."""
    r, j = np.meshgrid(np.arange(rows), np.arange(cols), indexing="ij")
    a = (r * (cols + 1) + j).reshape(-1)
    return a, a + 1, a + (cols + 1), a + (cols + 2)


def cube(spec: Cube) -> Arrays:
    half = np.array([spec.width, spec.height, spec.depth], dtype=np.float64) / 2.0
    pos, nrm, uv, tri = [], [], [], []
    for face, (n, u, v) in enumerate(_CUBE_FACES):
        for su, sv in _CORNERS:
            pos.append((np.array(n) + su * np.array(u) + sv * np.array(v)) * half)
            nrm.append(n)
            uv.append(((su + 1) / 2, 1.0 - (sv + 1) / 2))
        first = 4 * face
        tri += [(first, first + 1, first + 2), (first, first + 2, first + 3)]
    return _finish(np.array(pos), np.array(nrm, dtype=np.float64), np.array(uv), np.array(tri))


def sphere(spec: Sphere) -> Arrays:
    s, rings = spec.segments, spec.rings
    phi = np.pi * np.arange(rings + 1) / rings
    theta = 2.0 * np.pi * np.arange(s + 1) / s
    sp, cp = np.sin(phi)[:, None], np.cos(phi)[:, None]
    unit = np.stack(
        [sp * np.cos(theta)[None, :], np.broadcast_to(cp, (rings + 1, s + 1)), sp * np.sin(theta)[None, :]], axis=2
    ).reshape(-1, 3)
    # Aux pôles, sin(pi) vaut ~1e-16 : les sommets sont posés exactement sur l'axe.
    unit[: s + 1, 0] = unit[: s + 1, 2] = 0.0
    unit[-(s + 1) :, 0] = unit[-(s + 1) :, 2] = 0.0
    uv = np.stack(
        [np.broadcast_to(np.arange(s + 1) / s, (rings + 1, s + 1)), np.broadcast_to((np.arange(rings + 1) / rings)[:, None], (rings + 1, s + 1))],
        axis=2,
    ).reshape(-1, 2)
    a, b, c, d = _grid_quads(s, rings)
    band = np.repeat(np.arange(rings), s)
    first = np.stack([a, b, c], axis=1)[band != 0]  # bande du haut : a et b sont au même point (pôle)
    second = np.stack([b, d, c], axis=1)[band != rings - 1]  # bande du bas : c et d sont au même point
    tri = np.concatenate([first, second])
    return _finish(unit * spec.radius, unit, uv, tri)


def plane(spec: Plane) -> Arrays:
    w, d = spec.width_segments, spec.depth_segments
    x = (np.arange(w + 1) / w - 0.5) * spec.width
    z = (np.arange(d + 1) / d - 0.5) * spec.depth
    zz, xx = np.meshgrid(z, x, indexing="ij")
    pos = np.stack([xx, np.zeros_like(xx), zz], axis=2).reshape(-1, 3)
    nrm = np.tile([0.0, 1.0, 0.0], (pos.shape[0], 1))
    uu, vv = np.meshgrid(np.arange(w + 1) / w, np.arange(d + 1) / d, indexing="xy")
    uv = np.stack([uu, vv], axis=2).reshape(-1, 2)
    a, b, c, dd = _grid_quads(w, d)
    tri = np.concatenate([np.stack([a, c, b], axis=1), np.stack([b, c, dd], axis=1)])
    return _finish(pos, nrm, uv, tri)


def _ring(segments: int) -> tuple[Array, Array]:
    theta = 2.0 * np.pi * np.arange(segments + 1) / segments
    return np.cos(theta), np.sin(theta)


def _cap(cx: Array, sz: Array, y: float, radius: float, up: bool, first: int) -> tuple[Array, Array, Array, Array]:
    """Bouchon plat : un sommet central puis l'anneau (couture dédoublée) ; sens des triangles selon la face (haut / bas)."""
    n = cx.shape[0]
    pos = np.vstack([[0.0, y, 0.0], np.stack([cx * radius, np.full(n, y), sz * radius], axis=1)])
    nrm = np.tile([0.0, 1.0 if up else -1.0, 0.0], (n + 1, 1))
    flip = 1.0 if up else -1.0  # vu de dessous, le côté droit de l'image est -X
    uv = np.vstack([[0.5, 0.5], np.stack([0.5 + 0.5 * flip * cx, 0.5 + 0.5 * sz], axis=1)])
    ring = first + 1 + np.arange(n - 1)
    centre = np.full(n - 1, first)
    tri = np.stack([centre, ring + 1, ring], axis=1) if up else np.stack([centre, ring, ring + 1], axis=1)
    return pos, nrm, uv, tri


def cylinder(spec: Cylinder) -> Arrays:
    s = spec.segments
    cx, sz = _ring(s)
    half = spec.height / 2.0
    top = np.stack([cx * spec.radius, np.full(s + 1, half), sz * spec.radius], axis=1)
    bottom = top.copy()
    bottom[:, 1] = -half
    side_n = np.stack([cx, np.zeros(s + 1), sz], axis=1)
    u = np.arange(s + 1) / s
    pos = [np.vstack([top, bottom])]
    nrm = [np.vstack([side_n, side_n])]
    uv = [np.vstack([np.stack([u, np.zeros(s + 1)], axis=1), np.stack([u, np.ones(s + 1)], axis=1)])]
    a = np.arange(s)
    c = a + (s + 1)
    tri = [np.concatenate([np.stack([a, a + 1, c], axis=1), np.stack([a + 1, c + 1, c], axis=1)])]
    offset = 2 * (s + 1)
    if spec.caps:
        for up, y in ((True, half), (False, -half)):
            p, n_, t, f = _cap(cx, sz, y, spec.radius, up, offset)
            pos.append(p)
            nrm.append(n_)
            uv.append(t)
            tri.append(f)
            offset += p.shape[0]
    return _finish(np.vstack(pos), np.vstack(nrm), np.vstack(uv), np.vstack(tri))


def cone(spec: Cone) -> Arrays:
    s = spec.segments
    cx, sz = _ring(s)
    half = spec.height / 2.0
    base = np.stack([cx * spec.radius, np.full(s + 1, -half), sz * spec.radius], axis=1)
    slant = np.stack([cx * spec.height, np.full(s + 1, spec.radius), sz * spec.height], axis=1)
    mid = 2.0 * np.pi * (np.arange(s) + 0.5) / s  # le sommet de chaque segment prend la normale du milieu du segment
    apex_n = np.stack([np.cos(mid) * spec.height, np.full(s, spec.radius), np.sin(mid) * spec.height], axis=1)
    apex = np.tile([0.0, half, 0.0], (s, 1))
    u = np.arange(s + 1) / s
    pos = [np.vstack([base, apex])]
    nrm = [np.vstack([slant, apex_n])]
    uv = [
        np.vstack(
            [np.stack([u, np.ones(s + 1)], axis=1), np.stack([(np.arange(s) + 0.5) / s, np.zeros(s)], axis=1)]
        )
    ]
    j = np.arange(s)
    tri = [np.stack([(s + 1) + j, j + 1, j], axis=1)]
    offset = (s + 1) + s
    if spec.cap:
        p, n_, t, f = _cap(cx, sz, -half, spec.radius, False, offset)
        pos.append(p)
        nrm.append(n_)
        uv.append(t)
        tri.append(f)
    return _finish(np.vstack(pos), np.vstack(nrm), np.vstack(uv), np.vstack(tri))


def torus(spec: Torus) -> Arrays:
    s, t = spec.segments, spec.tube_segments
    theta = 2.0 * np.pi * np.arange(s + 1) / s
    psi = 2.0 * np.pi * np.arange(t + 1) / t
    ct, st = np.cos(theta)[None, :], np.sin(theta)[None, :]
    cp, sp = np.cos(psi)[:, None], np.sin(psi)[:, None]
    shape = (t + 1, s + 1)
    ring = spec.major_radius + spec.minor_radius * cp
    pos = np.stack(
        [ring * ct, np.broadcast_to(spec.minor_radius * sp, shape), ring * st], axis=2
    ).reshape(-1, 3)
    nrm = np.stack([cp * ct, np.broadcast_to(sp, shape), cp * st], axis=2).reshape(-1, 3)
    uu = np.broadcast_to(np.arange(s + 1) / s, shape)
    vv = np.broadcast_to((1.0 - np.arange(t + 1) / t)[:, None], shape)
    uv = np.stack([uu, vv], axis=2).reshape(-1, 2)
    a, b, c, d = _grid_quads(s, t)
    tri = np.concatenate([np.stack([a, c, b], axis=1), np.stack([b, c, d], axis=1)])
    return _finish(pos, nrm, uv, tri)


_GENERATORS: dict[type[PrimitiveSpec], Callable[[Any], Arrays]] = {
    Cube: cube,
    Sphere: sphere,
    Plane: plane,
    Cylinder: cylinder,
    Cone: cone,
    Torus: torus,
}


def primitive_arrays(spec: PrimitiveSpec) -> Arrays:
    """Géométrie de la forme : `(positions, normals, uvs, indices)`. `spec` doit être une forme de `kut3d.primitives`."""
    generator = _GENERATORS.get(type(spec))
    if generator is None:
        raise ValueError(f"forme inconnue : {type(spec).__name__}.")
    return generator(spec)
