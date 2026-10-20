"""Triangulation de polygones plans à trous (découpage en oreilles) et décalage de contours, sans dépendance hors numpy.

Conventions : Y vers le haut ; la matière est TOUJOURS à gauche d'un contour parcouru dans son sens (extérieur en sens
inverse des aiguilles d'une montre, trou dans le sens des aiguilles). Les triangles produits sont en sens direct (normale +Z).

Les trous sont reliés au contour extérieur par un pont (segment qui ne coupe aucun bord et qui part vers l'intérieur de la
matière aux deux bouts), puis le polygone unique obtenu est découpé en oreilles. Les sommets du pont sont dupliqués dans la
liste de découpe mais les triangles indexent toujours les points d'origine : le pont est donc une arête intérieure parcourue
deux fois en sens inverse, et le maillage reste fermé. Aucune tolérance cachée : un polygone qu'on ne sait pas découper lève
une `ValueError` (le fabricant du maillage la remonte), jamais un résultat approximatif.
"""

from __future__ import annotations

import math

import numpy as np

from kut3d.mathutil import Array

_EPS = 1e-12
_CREASE_COS = math.cos(math.radians(35.0))


def signed_area(ring: Array) -> float:
    x, y = ring[:, 0], ring[:, 1]
    return float(0.5 * np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y))


def clean_ring(ring: Array, eps: float = 1e-9) -> Array | None:
    """Sans sommets doublons ni sommets alignés dans le prolongement ; None si le contour dégénère (< 3 points, aire nulle)."""
    pts = [(float(p[0]), float(p[1])) for p in ring]
    changed = True
    while changed and len(pts) >= 3:
        changed = False
        i = 0
        while i < len(pts) and len(pts) >= 3:
            a, b, c = pts[i - 1], pts[i], pts[(i + 1) % len(pts)]
            d1 = (b[0] - a[0], b[1] - a[1])
            d2 = (c[0] - b[0], c[1] - b[1])
            l1, l2 = math.hypot(*d1), math.hypot(*d2)
            straight = (
                l1 > eps
                and l2 > eps
                and abs(d1[0] * d2[1] - d1[1] * d2[0]) <= 1e-9 * l1 * l2
                and d1[0] * d2[0] + d1[1] * d2[1] > 0
            )
            if l1 <= eps or straight:
                del pts[i]
                changed = True
            else:
                i += 1
    if len(pts) < 3:
        return None
    out = np.array(pts, dtype=np.float64)
    return out if abs(signed_area(out)) > eps * eps else None


def _contains(ring: Array, x: float, y: float) -> bool:
    ax, ay = ring[:, 0], ring[:, 1]
    bx, by = np.roll(ax, -1), np.roll(ay, -1)
    straddle = (ay > y) != (by > y)
    with np.errstate(divide="ignore", invalid="ignore"):
        cross_x = ax + (y - ay) * (bx - ax) / (by - ay)
    return bool(np.count_nonzero(straddle & (cross_x > x)) % 2)


def within(inner: Array, outer: Array) -> bool:
    """Vrai si le contour `inner` (qui ne coupe pas `outer`) est dans `outer` : vote des sommets, car deux contours qui se
    touchent en un point (cédille, queue du « Q ») ont des sommets SUR le bord de l'autre, où le test est ambigu."""
    votes = sum(_contains(outer, x, y) for x, y in inner)
    return 2 * votes > len(inner)


def classify(rings: list[Array]) -> list[tuple[Array, list[Array]]]:
    """(extérieur, [trous]) pour des contours propres qui ne se coupent pas : profondeur de nidification paire = extérieur,
    impaire = trou de son plus proche contour englobant. Extérieurs orientés en sens direct, trous en sens inverse."""
    count = len(rings)
    inside = [
        [j for j in range(count) if j != i and within(rings[i], rings[j])]
        for i in range(count)
    ]
    areas = [abs(signed_area(r)) for r in rings]
    shapes: dict[int, tuple[Array, list[Array]]] = {}
    for i, ring in enumerate(rings):
        if len(inside[i]) % 2 == 0:
            shapes[i] = (_oriented(ring, True), [])
    for i, ring in enumerate(rings):
        if len(inside[i]) % 2 == 1:
            parents = [j for j in inside[i] if len(inside[j]) == len(inside[i]) - 1]
            parent = min(parents, key=lambda j: areas[j])
            shapes[parent][1].append(_oriented(ring, False))
    return [shapes[i] for i in sorted(shapes)]


def _oriented(ring: Array, counter_clockwise: bool) -> Array:
    return ring if (signed_area(ring) > 0) == counter_clockwise else ring[::-1].copy()


def _orient(ax: float, ay: float, bx: float, by: float, px: Array, py: Array) -> Array:
    return (bx - ax) * (py - ay) - (by - ay) * (px - ax)


def _in_wedge(b: Array, a: Array, c: Array, q: Array) -> bool:
    """Vrai si la direction b->q est strictement dans l'angle intérieur (matière à gauche) du sommet b entre b->c et b->a."""
    first = math.atan2(c[1] - b[1], c[0] - b[0])
    full = 2.0 * math.pi
    span = (math.atan2(a[1] - b[1], a[0] - b[0]) - first) % full
    toward = (math.atan2(q[1] - b[1], q[0] - b[0]) - first) % full
    return 0.0 < toward < span


def _find_bridge(
    pts: Array, merged: list[int], hole: list[int], edges: list[tuple[int, int]]
) -> int:
    h = hole[0]
    hp = pts[h]
    order = sorted(
        range(len(merged)),
        key=lambda p: (float(np.sum((pts[merged[p]] - hp) ** 2)), p),
    )
    starts = pts[[e[0] for e in edges]]
    ends = pts[[e[1] for e in edges]]
    for p in order:
        v = merged[p]
        vp = pts[v]
        if np.array_equal(vp, hp):
            continue
        shares = (
            np.all(starts == hp, 1)
            | np.all(ends == hp, 1)
            | np.all(starts == vp, 1)
            | np.all(ends == vp, 1)
        )
        o1 = _orient(hp[0], hp[1], vp[0], vp[1], starts[:, 0], starts[:, 1])
        o2 = _orient(hp[0], hp[1], vp[0], vp[1], ends[:, 0], ends[:, 1])
        o3 = (ends[:, 0] - starts[:, 0]) * (hp[1] - starts[:, 1]) - (
            ends[:, 1] - starts[:, 1]
        ) * (hp[0] - starts[:, 0])
        o4 = (ends[:, 0] - starts[:, 0]) * (vp[1] - starts[:, 1]) - (
            ends[:, 1] - starts[:, 1]
        ) * (vp[0] - starts[:, 0])
        if np.any(~shares & (o1 * o2 <= 0) & (o3 * o4 <= 0)):
            continue
        if not _in_wedge(
            vp, pts[merged[p - 1]], pts[merged[(p + 1) % len(merged)]], hp
        ):
            continue
        if not _in_wedge(hp, pts[hole[-1]], pts[hole[1]], vp):
            continue
        return p
    raise ValueError("contour impossible à relier à son trou (polygone non simple).")


def _clip_ears(pts: Array, ring: list[int]) -> list[tuple[int, int, int]]:
    n = len(ring)
    xs, ys = pts[ring, 0], pts[ring, 1]
    prev = [(i - 1) % n for i in range(n)]
    nxt = [(i + 1) % n for i in range(n)]
    alive = np.ones(n, dtype=bool)
    out: list[tuple[int, int, int]] = []

    def convexity(i: int) -> float:
        a, c = prev[i], nxt[i]
        return float(
            (xs[i] - xs[a]) * (ys[c] - ys[i]) - (ys[i] - ys[a]) * (xs[c] - xs[i])
        )

    def is_ear(i: int) -> bool:
        a, c = prev[i], nxt[i]
        if convexity(i) <= 0.0:
            return False
        inside = alive.copy()
        inside &= _orient(xs[a], ys[a], xs[i], ys[i], xs, ys) >= 0
        inside &= _orient(xs[i], ys[i], xs[c], ys[c], xs, ys) >= 0
        inside &= _orient(xs[c], ys[c], xs[a], ys[a], xs, ys) >= 0
        for k in (a, i, c):
            inside &= ~((xs == xs[k]) & (ys == ys[k]))
        return not bool(inside.any())

    def clip(i: int) -> int:
        a, c = prev[i], nxt[i]
        out.append((ring[a], ring[i], ring[c]))
        alive[i] = False
        nxt[a], prev[c] = c, a
        return c

    remaining, cur, misses = n, 0, 0
    while remaining > 3:
        if is_ear(cur):
            cur = clip(cur)
            remaining -= 1
            misses = 0
            continue
        cur = nxt[cur]
        misses += 1
        if (
            misses > remaining
        ):  # aucune oreille franche : on coupe la plus convexe (maillage fermé, aire vérifiée par l'appelant)
            live = [i for i in range(n) if alive[i]]
            cur = clip(max(live, key=convexity))
            remaining -= 1
            misses = 0
    a, c = prev[cur], nxt[cur]
    out.append((ring[a], ring[cur], ring[c]))
    return out


def triangulate(outer: Array, holes: list[Array]) -> Array:
    """Triangles (m, 3) d'indices dans `np.vstack([outer, *holes])` : `outer` en sens direct, chaque trou en sens inverse."""
    pts = np.vstack([outer, *holes]).astype(np.float64)
    merged = list(range(len(outer)))
    pending: list[list[int]] = []
    first = len(outer)
    for ring in holes:
        idx = list(range(first, first + len(ring)))
        first += len(ring)
        start = min(range(len(idx)), key=lambda k: (pts[idx[k], 0], pts[idx[k], 1]))
        pending.append(idx[start:] + idx[:start])
    pending.sort(key=lambda r: (pts[r[0], 0], pts[r[0], 1]))
    for k, hole in enumerate(pending):
        edges = [(merged[i], merged[(i + 1) % len(merged)]) for i in range(len(merged))]
        for other in pending[k:]:
            edges += [
                (other[i], other[(i + 1) % len(other)]) for i in range(len(other))
            ]
        p = _find_bridge(pts, merged, hole, edges)
        merged = merged[: p + 1] + hole + [hole[0], merged[p]] + merged[p + 1 :]
    return np.array(_clip_ears(pts, merged), dtype=np.int64).reshape(-1, 3)


def triangles_area(pts: Array, tris: Array) -> float:
    a, b, c = pts[tris[:, 0]], pts[tris[:, 1]], pts[tris[:, 2]]
    return float(
        np.sum(
            0.5
            * (
                (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1])
                - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0])
            )
        )
    )


def edge_normals(ring: Array) -> Array:
    """Normale unitaire sortante de la matière (à droite du sens de parcours) de chaque arête i -> i+1."""
    d = np.roll(ring, -1, axis=0) - ring
    length = np.hypot(d[:, 0], d[:, 1])[:, None]
    return np.stack([d[:, 1], -d[:, 0]], axis=1) / np.where(length > 0, length, 1.0)


def vertex_normals(ring: Array) -> tuple[Array, Array]:
    """(normale en début d'arête, normale en fin d'arête) : lissées entre arêtes voisines si l'angle est faible
    (courbes), celles de l'arête elle-même aux angles vifs."""
    en = edge_normals(ring)
    before = np.roll(en, 1, axis=0)  # arête i-1 : voisine du début de l'arête i
    smooth = np.sum(before * en, axis=1) >= _CREASE_COS
    mean = before + en
    length = np.hypot(mean[:, 0], mean[:, 1])[:, None]
    mean = mean / np.where(length > _EPS, length, 1.0)
    start = np.where(smooth[:, None], mean, en)
    end = np.roll(
        start, -1, axis=0
    )  # la normale de fin de l'arête i = celle de début de l'arête i+1, SI lissée
    end = np.where(np.roll(smooth, -1)[:, None], end, en)
    return start, end


def inset_ring(ring: Array, distance: float) -> Array:
    """Le contour déplacé de `distance` vers la matière (à gauche), en onglet (longueur de l'onglet limitée à 2 fois).
    Aux sommets convexes, le déplacement est borné pour que les deux arêtes voisines restent de longueur positive (un
    arrondi serré ne se retourne pas en queue d'aronde) : le décalage y est localement plus petit que `distance`."""
    before = ring - np.roll(ring, 1, axis=0)
    after = np.roll(ring, -1, axis=0) - ring
    len1 = np.maximum(np.hypot(before[:, 0], before[:, 1]), _EPS)
    len2 = np.maximum(np.hypot(after[:, 0], after[:, 1]), _EPS)
    n1 = np.stack([-before[:, 1], before[:, 0]], axis=1) / len1[:, None]
    n2 = np.stack([-after[:, 1], after[:, 0]], axis=1) / len2[:, None]
    dot = np.sum(n1 * n2, axis=1)
    miter = (n1 + n2) / np.maximum(1.0 + dot, 1e-6)[:, None]
    length = np.hypot(miter[:, 0], miter[:, 1])[:, None]
    miter = miter * np.minimum(1.0, 2.0 / np.maximum(length, _EPS))
    turn = (
        n1[:, 0] * n2[:, 1] - n1[:, 1] * n2[:, 0]
    )  # > 0 : virage vers la matière (sommet convexe)
    tan_half = np.maximum(turn, 0.0) / np.maximum(1.0 + dot, 1e-6)
    room = 0.45 * np.minimum(len1, len2) / np.maximum(tan_half, _EPS)
    return ring + miter * np.minimum(distance, room)[:, None]


def _edges(rings: list[Array]) -> tuple[Array, Array]:
    return (
        np.vstack(rings),
        np.vstack([np.roll(r, -1, axis=0) for r in rings]),
    )


def cross_properly(first: list[Array], second: list[Array]) -> bool:
    """Vrai si une arête de `first` coupe franchement (en son intérieur) une arête de `second`."""
    a, b = _edges(first)
    c, d = _edges(second)
    ax, ay, bx, by = a[:, None, 0], a[:, None, 1], b[:, None, 0], b[:, None, 1]
    cx, cy, dx, dy = c[None, :, 0], c[None, :, 1], d[None, :, 0], d[None, :, 1]
    o1 = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
    o2 = (bx - ax) * (dy - ay) - (by - ay) * (dx - ax)
    o3 = (dx - cx) * (ay - cy) - (dy - cy) * (ax - cx)
    o4 = (dx - cx) * (by - cy) - (dy - cy) * (bx - cx)
    return bool(np.any((o1 * o2 < 0) & (o3 * o4 < 0)))
