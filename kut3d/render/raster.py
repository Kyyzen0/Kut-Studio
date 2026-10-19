"""Rasteriseur logiciel : découpe contre le plan proche, couverture exacte (règle top-left), z-buffer.

Chaîne : sommets en espace de découpe (clip) -> rejet (triangle hors frustum, face arrière, dégénéré) -> découpe
contre le plan proche (Sutherland-Hodgman en coordonnées homogènes) -> coordonnées écran. Chaque sommet porte ses
coordonnées barycentriques d'ORIGINE (celles du triangle du maillage) : un triangle découpé reste donc
interpolable avec les attributs du triangle d'origine. Les barycentriques par pixel sont corrigées de la
perspective (pondérées par 1/w). Les arêtes partagées appartiennent à un seul des deux triangles (top-left), de
sorte qu'un alpha-blending ne double jamais la couverture sur une diagonale.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass

import numpy as np

from kut3d.mathutil import Array
from kut3d.render.limits import TILE_PIXELS


@dataclass(frozen=True, eq=False)
class RasterTris:
    """Triangles prêts à rasteriser, en coordonnées écran (x vers la droite, y vers le bas, pixels)."""

    invw: Array  # (R, 3) 1/w de vue
    ob: Array  # (R, 3, 3) barycentriques d'origine de chaque sommet
    src: Array  # (R,) indice du triangle du maillage
    bbox: Array  # (R, 4) x0, x1, y0, y1 inclus, déjà bornés à l'image
    ax: Array  # (R, 3) sommet de départ de chaque arête (arête i : opposée au sommet i)
    ay: Array
    ea: Array  # (R, 3) coefficients de l'arête, orientés pour que l'intérieur soit positif
    eb: Array
    top_left: Array  # (R, 3) bool : l'arête appartient au triangle quand le pixel est exactement dessus
    area: Array  # (R,) aire écran doublée, positive

    @property
    def count(self) -> int:
        return int(self.src.shape[0])


def _clip_near(c3: Array, d: Array, inside: Array) -> list[tuple[Array, Array]]:
    """Découpe un triangle (3 sommets homogènes) contre z + w >= 0 : 1 ou 2 triangles (sommets, barycentriques)."""
    eye = np.eye(3)
    verts: list[Array] = []
    obs: list[Array] = []
    for a in range(3):
        b = (a + 1) % 3
        if inside[a]:
            verts.append(c3[a])
            obs.append(eye[a])
        if inside[a] != inside[b]:
            s = d[a] / (d[a] - d[b])
            verts.append(c3[a] + s * (c3[b] - c3[a]))
            obs.append(eye[a] + s * (eye[b] - eye[a]))
    fans = [(0, 1, 2)] if len(verts) == 3 else [(0, 1, 2), (0, 2, 3)]
    return [
        (np.array([verts[i] for i in f]), np.array([obs[i] for i in f])) for f in fans
    ]


def build_raster_triangles(
    clip: Array, tri: Array, double_sided: Array, width: int, height: int
) -> RasterTris:
    """Prépare les triangles `tri` (T, 3) dont les sommets sont `clip` (V, 4). `double_sided` (T,) bool."""
    c = clip[tri]
    x, y, z, w = c[..., 0], c[..., 1], c[..., 2], c[..., 3]
    finite = np.isfinite(c).all(axis=(1, 2))
    # Orientation robuste, valable même avec des sommets derrière la caméra : signe du déterminant (x, y, w).
    det = (
        x[:, 0] * (y[:, 1] * w[:, 2] - w[:, 1] * y[:, 2])
        - y[:, 0] * (x[:, 1] * w[:, 2] - w[:, 1] * x[:, 2])
        + w[:, 0] * (x[:, 1] * y[:, 2] - y[:, 1] * x[:, 2])
    )
    keep = finite & ((det > 0) | (double_sided & (det < 0)))
    outside = np.stack(
        [x > w, x < -w, y > w, y < -w, z > w, z < -w], axis=1
    )  # (T, 6, 3)
    keep &= ~outside.all(axis=2).any(axis=1)
    d = z + w
    inside = d >= 0
    nin = inside.sum(axis=1)
    full = np.flatnonzero(keep & (nin == 3))
    partial = np.flatnonzero(keep & (nin > 0) & (nin < 3))
    verts = [c[full]]
    obs = [np.broadcast_to(np.eye(3), (full.size, 3, 3))]
    srcs = [full]
    extra_v: list[Array] = []
    extra_o: list[Array] = []
    extra_s: list[int] = []
    for k in partial.tolist():
        for v, o in _clip_near(c[k], d[k], inside[k]):
            extra_v.append(v)
            extra_o.append(o)
            extra_s.append(k)
    if extra_v:
        verts.append(np.stack(extra_v))
        obs.append(np.stack(extra_o))
        srcs.append(np.asarray(extra_s, dtype=np.int64))
    cv = np.concatenate(verts)
    ob = np.concatenate(obs)
    src = np.concatenate(srcs).astype(np.int64)
    invw = 1.0 / cv[..., 3]
    sx = (cv[..., 0] * invw * 0.5 + 0.5) * width
    sy = (1.0 - (cv[..., 1] * invw * 0.5 + 0.5)) * height
    ok = (
        np.isfinite(sx).all(axis=1)
        & np.isfinite(sy).all(axis=1)
        & (invw > 0).all(axis=1)
    )
    area = (sx[:, 1] - sx[:, 0]) * (sy[:, 2] - sy[:, 0]) - (sx[:, 2] - sx[:, 0]) * (
        sy[:, 1] - sy[:, 0]
    )
    ok &= area != 0
    # Boîte de pixels dont le CENTRE (i + 0.5) peut être couvert, bornée à l'image.
    x0 = np.ceil(sx.min(axis=1) - 0.5).clip(0, width)
    x1 = np.floor(sx.max(axis=1) - 0.5).clip(-1, width - 1)
    y0 = np.ceil(sy.min(axis=1) - 0.5).clip(0, height)
    y1 = np.floor(sy.max(axis=1) - 0.5).clip(-1, height - 1)
    ok &= (x0 <= x1) & (y0 <= y1)
    sel = np.flatnonzero(ok)
    sx, sy, invw, ob, src, area = (
        sx[sel],
        sy[sel],
        invw[sel],
        ob[sel],
        src[sel],
        area[sel],
    )
    bbox = np.stack([x0[sel], x1[sel], y0[sel], y1[sel]], axis=1).astype(np.int64)
    s = np.sign(area)[:, None]
    nxt, prv = (
        [1, 2, 0],
        [2, 0, 1],
    )  # l'arête i (opposée au sommet i) va du sommet nxt[i] au sommet prv[i]
    ax0, ay0, bx, by = sx[:, nxt], sy[:, nxt], sx[:, prv], sy[:, prv]
    # Sens d'orientation (intérieur positif) pour la règle top-left, avant toute permutation d'extrémités.
    oy, ox = s * (by - ay0), s * (bx - ax0)
    top_left = (oy > 0) | ((oy == 0) & (ox < 0))
    # Fonction d'arête CANONIQUE : extrémités triées, signe porté à part. Deux triangles qui partagent une arête
    # calculent alors exactement la valeur opposée (pas de fissure ni de double couverture par arrondi).
    swap = (ax0 > bx) | ((ax0 == bx) & (ay0 > by))
    cax, cay = np.where(swap, bx, ax0), np.where(swap, by, ay0)
    cbx, cby = np.where(swap, ax0, bx), np.where(swap, ay0, by)
    sgn = s * np.where(swap, -1.0, 1.0)
    ea, eb = sgn * (cby - cay), sgn * (cbx - cax)
    ax, ay = cax, cay
    return RasterTris(invw, ob, src, bbox, ax, ay, ea, eb, top_left, np.abs(area))


@dataclass(frozen=True, eq=False)
class Coverage:
    """Pixels couverts par un triangle dans une bande : `ins` (ny, nx) et les trois fonctions d'arête."""

    y0: int
    x0: int
    ins: Array
    e: tuple[Array, Array, Array]


class Rasterizer:
    """Parcours des triangles ; les tableaux sont convertis en listes une fois pour la boucle Python."""

    def __init__(self, rt: RasterTris) -> None:
        self.rt = rt
        self._bbox = rt.bbox.tolist()
        self._ax, self._ay = rt.ax.tolist(), rt.ay.tolist()
        self._ea, self._eb = rt.ea.tolist(), rt.eb.tolist()
        self._tl = rt.top_left.tolist()
        self._invw = rt.invw.tolist()
        self._area = rt.area.tolist()
        self._src = rt.src.tolist()

    def source(self, r: int) -> int:
        return int(self._src[r])

    def cover(self, r: int) -> Iterator[Coverage]:
        x0, x1, y0, y1 = self._bbox[r]
        ax, ay, ea, eb, tl = (
            self._ax[r],
            self._ay[r],
            self._ea[r],
            self._eb[r],
            self._tl[r],
        )
        px = np.arange(x0, x1 + 1) + 0.5
        rows = max(1, TILE_PIXELS // (x1 - x0 + 1))
        for ya in range(y0, y1 + 1, rows):
            yb = min(ya + rows - 1, y1)
            py = (np.arange(ya, yb + 1) + 0.5)[:, None]
            e = [eb[i] * (py - ay[i]) - ea[i] * (px - ax[i]) for i in range(3)]
            low = np.minimum(np.minimum(e[0], e[1]), e[2])
            ins = low > 0
            tied = low == 0
            if tied.any():  # arête exactement sur un centre de pixel : règle top-left
                ok = np.ones_like(ins)
                for i in range(3):
                    ok &= (e[i] > 0) | ((e[i] == 0) & tl[i])
                ins |= tied & ok
            if ins.any():
                yield Coverage(ya, x0, ins, (e[0], e[1], e[2]))

    def interpolate(self, r: int, cov: Coverage) -> tuple[Array, Array]:
        """Profondeur de vue `w` (n,) et barycentriques d'origine corrigées de la perspective (n, 3), pour les
        pixels couverts, dans l'ordre de `np.nonzero(cov.ins)`."""
        e0, e1, e2 = (a[cov.ins] for a in cov.e)
        i0, i1, i2 = self._invw[r]
        q0, q1, q2 = e0 * i0, e1 * i1, e2 * i2
        total = q0 + q1 + q2  # = aire x (1/w du pixel)
        w = self._area[r] / total
        ob = self.rt.ob[r]
        weights = np.stack([q0, q1, q2], axis=1) / total[:, None]
        return w, weights @ ob


class VisBuffer:
    """Tampon de visibilité : profondeur de vue, triangle du maillage, barycentriques d'origine (b1, b2)."""

    def __init__(self, height: int, width: int) -> None:
        self.depth = np.full((height, width), np.inf, dtype=np.float32)
        self.tri = np.full((height, width), -1, dtype=np.int32)
        self.b1 = np.zeros((height, width), dtype=np.float32)
        self.b2 = np.zeros((height, width), dtype=np.float32)


MaskAlpha = Callable[[int, Array], Array]


def raster_opaque(
    rz: Rasterizer,
    order: Array,
    vis: VisBuffer,
    far: float,
    is_mask: Array,
    mask_alpha: MaskAlpha,
) -> None:
    """Rasterise `order` (indices de triangles raster, dans l'ordre) avec test de profondeur. Un triangle `mask`
    ne garde que les pixels dont l'alpha passe le seuil (`mask_alpha(triangle, barycentriques) -> bool`)."""
    for r in order.tolist():
        masked = bool(is_mask[r])
        src = rz.source(r)
        for cov in rz.cover(r):
            w, ob = rz.interpolate(r, cov)
            ny, nx = cov.ins.shape
            sl = (slice(cov.y0, cov.y0 + ny), slice(cov.x0, cov.x0 + nx))
            zreg = vis.depth[sl]
            ok = (w < zreg[cov.ins]) & (w <= far)
            if masked and ok.any():
                ok &= mask_alpha(src, ob).astype(bool)
            if not ok.any():
                continue
            hit = cov.ins.copy()
            hit[cov.ins] = ok
            zreg[hit] = w[ok]
            vis.tri[sl][hit] = src
            vis.b1[sl][hit] = ob[ok, 1]
            vis.b2[sl][hit] = ob[ok, 2]
