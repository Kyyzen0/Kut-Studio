"""Maillage d'un texte 3D extrudé : mise en page, faces avant / arrière triangulées, parois latérales, biseau.

`text_arrays(spec)` retourne `(positions, normals, uvs, indices)` comme les autres formes (float32 / uint32), maillage FERMÉ :
chaque arête est partagée par exactement deux triangles de sens opposés (testé), normales géométriques sortantes.

Géométrie : le texte est dans le plan XY, lisible depuis +Z, extrudé de z = -depth/2 à +depth/2. Face avant : normale +Z ;
face arrière : -Z ; parois : normales sortantes de la matière, lissées le long des courbes et franches aux angles vifs
(> 35 degrés). Avec `bevel`, chaque paroi devient trois bandes : chanfrein avant (45 degrés), paroi droite, chanfrein arrière ;
les faces avant et arrière sont alors celles des contours RÉTRÉCIS de `bevel`. UV : projection plane en XY sur l'étendue du
texte (juste sur les faces, approximative sur les parois). Aucun crénage ; voir `kut3d.text_font` pour ce qui dépend de la police.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from kut3d.mathutil import Array
from kut3d.primitives import MAX_PRIMITIVE_TRIANGLES, Text3D
from kut3d.text_font import glyph, metrics
from kut3d.text_rules import is_blank
from kut3d.triangulate import (
    classify,
    cross_properly,
    inset_ring,
    signed_area,
    triangles_area,
    triangulate,
    vertex_normals,
)

Arrays = tuple[Array, Array, Array, Array]
_AREA_TOLERANCE = (
    1e-6  # écart relatif admis entre l'aire du polygone et celle de ses triangles
)


@dataclass(frozen=True)
class _Solid:
    rings: tuple[
        Array, ...
    ]  # contours d'origine (extérieurs en sens direct, trous en sens inverse), em
    front: tuple[
        Array, ...
    ]  # contours des faces avant / arrière (rétrécis si biseau), mêmes indices
    points: Array  # (N, 2) : points des contours `front` mis bout à bout
    triangles: Array  # (M, 3) : indices dans `points`


def _shrunk(rings: list[Array], distance: float, char: str) -> list[Array]:
    inset = [inset_ring(r, distance) for r in rings]
    same_sign = all(
        (signed_area(a) > 0) == (signed_area(b) > 0) and abs(signed_area(b)) > 0
        for a, b in zip(rings, inset, strict=True)
    )
    if not same_sign or cross_properly(inset, inset) or cross_properly(inset, rings):
        raise ValueError(
            f"le biseau est trop grand pour le caractère « {char} » : ses traits sont plus fins que le biseau."
        )
    return inset


@lru_cache(maxsize=256)
def _solid(family: str, char: str, inset: float) -> _Solid:
    """Un caractère prêt à extruder (em), avec contours rétrécis de `inset` em (0 = aucun)."""
    shapes = classify(list(glyph(family, char).rings))
    rings: list[Array] = []
    front: list[Array] = []
    points: list[Array] = []
    triangles: list[Array] = []
    offset = 0
    for outer, holes in shapes:
        group = [outer, *holes]
        shrunk = _shrunk(group, inset, char) if inset > 0.0 else group
        pts = np.vstack(shrunk)
        tris = triangulate(shrunk[0], shrunk[1:])
        expected = signed_area(shrunk[0]) + sum(signed_area(h) for h in shrunk[1:])
        got = triangles_area(pts, tris)
        if not math.isclose(got, expected, rel_tol=_AREA_TOLERANCE, abs_tol=1e-12) or (
            np.any(_triangle_areas(pts, tris) < -1e-12)
        ):
            raise ValueError(
                f"le caractère « {char} » de « {family[:40]} » ne se découpe pas en triangles (contour non simple)."
            )
        rings += group
        front += shrunk
        points.append(pts)
        triangles.append(tris + offset)
        offset += len(pts)
    return _Solid(tuple(rings), tuple(front), np.vstack(points), np.vstack(triangles))


def _triangle_areas(pts: Array, tris: Array) -> Array:
    a, b, c = pts[tris[:, 0]], pts[tris[:, 1]], pts[tris[:, 2]]
    return np.asarray(
        0.5
        * (
            (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1])
            - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0])
        )
    )


def _layout(spec: Text3D) -> list[tuple[str, float, float]]:
    """(caractère, x, y de la ligne de base) en em, pour chaque caractère visible ; centré verticalement sur les capitales."""
    lines = spec.text.split("\n")
    m = metrics(spec.font)
    placed: list[tuple[str, float, float]] = []
    for number, line in enumerate(lines):
        glyphs = [glyph(spec.font, c) for c in line]
        width = sum(g.advance for g in glyphs)
        x = {"left": 0.0, "center": -width / 2.0, "right": -width}[spec.align]
        baseline = (
            -m.cap_height / 2.0 + (len(lines) - 1) * m.pitch / 2.0 - number * m.pitch
        )
        for char, g in zip(line, glyphs, strict=True):
            if not is_blank(char):
                placed.append((char, x, baseline))
            x += g.advance
    return placed


class _Parts:
    def __init__(self) -> None:
        self.pos: list[Array] = []
        self.nrm: list[Array] = []
        self.tri: list[Array] = []
        self.count = 0

    def add(self, pos: Array, nrm: Array, tri: Array) -> None:
        self.pos.append(pos)
        self.nrm.append(nrm)
        self.tri.append(tri + self.count)
        self.count += len(pos)


def _walls(
    parts: _Parts,
    upper: Array,
    lower: Array,
    z_up: float,
    z_low: float,
    tilt: float,
    ring: Array,
    scale: float,
) -> None:
    """Une bande de paroi entre deux contours homologues (`upper` à z_up, `lower` à z_low), 4 sommets par arête.
    `tilt` = composante z de la normale avant normalisation (0 paroi droite, +1 / -1 chanfreins à 45 degrés)."""
    start, end = vertex_normals(ring)
    n = len(ring)
    u0, u1 = upper, np.roll(upper, -1, axis=0)
    l0, l1 = lower, np.roll(lower, -1, axis=0)
    pos = np.empty((n, 4, 3))
    pos[:, 0, :2], pos[:, 1, :2], pos[:, 2, :2], pos[:, 3, :2] = l0, l1, u1, u0
    pos[:, 0, 2] = pos[:, 1, 2] = z_low
    pos[:, 2, 2] = pos[:, 3, 2] = z_up
    pos[:, :, :2] *= scale
    nrm = np.zeros((n, 4, 3))
    for slot, source in ((0, start), (1, end), (2, end), (3, start)):
        nrm[:, slot, :2] = source
    nrm[:, :, 2] = tilt
    norm = np.linalg.norm(nrm, axis=2, keepdims=True)
    nrm = nrm / norm
    base = (np.arange(n) * 4)[:, None]
    tri = np.concatenate(
        [base + [[0, 1, 2]], base + [[0, 2, 3]]]
    )  # (B0, B1, T1) et (B0, T1, T0) : sens direct vu de l'extérieur
    parts.add(pos.reshape(-1, 3), nrm.reshape(-1, 3), tri)


def _glyph_parts(
    parts: _Parts, solid: _Solid, spec: Text3D, dx: float, dy: float
) -> None:
    half = spec.depth / 2.0
    shift = np.array([dx, dy])
    scale = spec.size
    bevel = spec.bevel
    front = (solid.points + shift) * scale
    n = len(front)
    top = np.column_stack([front, np.full(n, half)])
    bottom = np.column_stack([front, np.full(n, -half)])
    up = np.tile([0.0, 0.0, 1.0], (n, 1))
    parts.add(top, up, solid.triangles)
    parts.add(bottom, -up, solid.triangles[:, [0, 2, 1]])
    for ring, shrunk in zip(solid.rings, solid.front, strict=True):
        ring_s, front_s = ring + shift, shrunk + shift
        if bevel > 0.0:
            _walls(parts, front_s, ring_s, half, half - bevel, 1.0, ring, scale)
            _walls(parts, ring_s, ring_s, half - bevel, -half + bevel, 0.0, ring, scale)
            _walls(parts, ring_s, front_s, -half + bevel, -half, -1.0, ring, scale)
        else:
            _walls(parts, ring_s, ring_s, half, -half, 0.0, ring, scale)


def _check(arrays: Arrays) -> Arrays:
    for a in arrays:
        a.setflags(write=False)
    return arrays


@lru_cache(maxsize=16)
def text_arrays(spec: Text3D) -> Arrays:
    """Géométrie du texte : `(positions, normals, uvs, indices)`. Lève `ValueError` (français) si la police ou un caractère est
    inutilisable, si un biseau dépasse l'épaisseur d'un trait ou si le maillage dépasse `MAX_PRIMITIVE_TRIANGLES`."""
    inset = spec.bevel / spec.size
    placed = _layout(spec)
    if not placed:
        raise ValueError("text : aucun caractère visible.")
    parts = _Parts()
    for char, x, baseline in placed:
        _glyph_parts(parts, _solid(spec.font, char, inset), spec, x, baseline)
    pos = np.vstack(parts.pos)
    nrm = np.vstack(parts.nrm)
    tri = np.vstack(parts.tri)
    if len(tri) > MAX_PRIMITIVE_TRIANGLES or len(tri) > spec.triangle_count:
        raise ValueError(
            f"text : {len(tri)} triangles, plus que le maximum de {MAX_PRIMITIVE_TRIANGLES}."
        )
    low, high = pos[:, :2].min(axis=0), pos[:, :2].max(axis=0)
    extent = np.where(high - low > 0, high - low, 1.0)
    uv = np.column_stack(
        [(pos[:, 0] - low[0]) / extent[0], 1.0 - (pos[:, 1] - low[1]) / extent[1]]
    )
    return _check(
        (
            np.ascontiguousarray(pos, dtype=np.float32),
            np.ascontiguousarray(nrm, dtype=np.float32),
            np.ascontiguousarray(uv, dtype=np.float32),
            np.ascontiguousarray(tri, dtype=np.uint32),
        )
    )
