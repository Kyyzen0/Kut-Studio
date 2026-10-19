"""Ombrage PBR métal / rugosité (Cook-Torrance) en espace linéaire.

Modèle : diffus de Lambert pondéré par (1 - F), spéculaire GGX (D) avec visibilité de Smith corrélée en hauteur
(V, qui contient déjà le 1/(4 N.L N.V)) et Fresnel de Schlick (F). F0 = 0,04 pour un diélectrique, la couleur de
base pour un métal. Unités : une lumière d'intensité 1 donne 1,0 sur une surface blanche, mate, éclairée de face
(l'éclairement est π x intensité). La lumière d'ambiance éclaire la couleur de base (métal ou non), modulée par
l'occlusion. L'occlusion n'atténue que l'ambiance, comme glTF. Tonemapping : exposition 1,0 puis écrêtage.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np

from kut3d.mathutil import Array
from kut3d.render.assemble import Geometry
from kut3d.render.materials import (
    MIN_ROUGHNESS,
    MODE_BLEND,
    MODE_OPAQUE,
    PreparedMaterial,
)
from kut3d.render.textures import sample_bilinear
from kut3d.scene import AmbientLight, DirectionalLight, Light, PointLight


def _unit(v: Array) -> Array:
    n = np.linalg.norm(v, axis=1, keepdims=True)
    return np.asarray(v / np.where(n > 1e-20, n, 1.0))


def _mix(attr: Array, idx: Array, b: Array) -> Array:
    return np.asarray(
        attr[idx[:, 0]] * b[:, :1]
        + attr[idx[:, 1]] * b[:, 1:2]
        + attr[idx[:, 2]] * b[:, 2:3]
    )


class Shader:
    """Calcule la couleur linéaire (écrêtée dans [0, 1]) de pixels décrits par (triangle, barycentriques)."""

    def __init__(self, geo: Geometry, lights: Sequence[Light], eye: Array) -> None:
        self.geo = geo
        self.eye = np.asarray(eye, dtype=np.float64)
        self.ambient = np.zeros(3)
        self.directional: list[DirectionalLight] = []
        self.points: list[PointLight] = []
        for light in lights:
            if isinstance(light, AmbientLight):
                self.ambient += np.asarray(light.color) * light.intensity
            elif isinstance(light, DirectionalLight):
                self.directional.append(light)
            elif isinstance(light, PointLight):
                self.points.append(light)

    # -- groupes de pixels par matériau ------------------------------------------------------------------------

    def _groups(self, tri: Array) -> list[tuple[PreparedMaterial, Array]]:
        geo = self.geo
        mat = geo.draw_mat[geo.tri_draw[tri]]
        first = int(mat[0]) if mat.size else 0
        if mat.size == 0 or (mat == first).all():
            return [(geo.materials[first], np.arange(mat.size))] if mat.size else []
        order = np.argsort(mat, kind="stable")
        cuts = np.flatnonzero(np.diff(mat[order])) + 1
        return [
            (geo.materials[int(mat[chunk[0]])], chunk)
            for chunk in np.split(order, cuts)
        ]

    def shade(self, tri: Array, b1: Array, b2: Array) -> tuple[Array, Array]:
        """(rgb linéaire (n, 3) float32, alpha (n,) float32) de n pixels."""
        n = tri.shape[0]
        rgb = np.zeros((n, 3), dtype=np.float32)
        alpha = np.ones(n, dtype=np.float32)
        for mat, sel in self._groups(tri):
            c, a = self._shade_group(mat, tri[sel], b1[sel], b2[sel])
            rgb[sel] = c
            alpha[sel] = a
        return rgb, alpha

    def mask_alpha(self, tri_id: int, ob: Array) -> Array:
        """Alpha de couleur de base (n,) des pixels d'un triangle, pour le test `mask` (barycentriques `ob`)."""
        n = ob.shape[0]
        tri = np.full(n, tri_id, dtype=np.int64)
        mat = self.geo.materials[int(self.geo.draw_mat[self.geo.tri_draw[tri_id]])]
        idx = self.geo.tri[tri]
        b = ob
        _, alpha = self._base(mat, idx, b)
        return np.asarray(alpha >= mat.cutoff)

    # -- couleur de base ------------------------------------------------------------------------------------------

    def _base(self, mat: PreparedMaterial, idx: Array, b: Array) -> tuple[Array, Array]:
        geo = self.geo
        col = _mix(geo.col.astype(np.float64), idx, b)
        base = col * mat.base.astype(np.float64)
        if mat.base_tex is not None:
            uv = _mix(geo.uv, idx, b)
            base = base * sample_bilinear(mat.base_tex, uv, True)
        return base[:, :3], base[:, 3]

    # -- ombrage d'un groupe --------------------------------------------------------------------------------------

    def _shade_group(
        self, mat: PreparedMaterial, tri: Array, b1: Array, b2: Array
    ) -> tuple[Array, Array]:
        geo = self.geo
        idx = geo.tri[tri]
        b = np.stack([1.0 - b1 - b2, b1, b2], axis=1).astype(np.float64)
        p0, p1, p2 = geo.pos[idx[:, 0]], geo.pos[idx[:, 1]], geo.pos[idx[:, 2]]
        pos = b[:, :1] * p0 + b[:, 1:2] * p1 + b[:, 2:3] * p2
        ng = _unit(np.cross(p1 - p0, p2 - p0))
        nrm = _unit(_mix(geo.nrm, idx, b))
        nrm = np.where(np.linalg.norm(nrm, axis=1, keepdims=True) > 0.5, nrm, ng)
        view = _unit(self.eye - pos)
        nrm = np.where((np.sum(ng * view, axis=1) < 0)[:, None], -nrm, nrm)
        base, alpha = self._base(mat, idx, b)
        uv = _mix(geo.uv, idx, b)
        if mat.normal_tex is not None:
            nrm = self._perturb(mat.normal_tex, uv, nrm, idx, b)
        metallic = np.full(base.shape[0], mat.metallic)
        rough = np.full(base.shape[0], mat.roughness)
        if mat.mr_tex is not None:
            mr = sample_bilinear(mat.mr_tex, uv, False)
            rough = rough * mr[:, 1]
            metallic = metallic * mr[:, 2]
        rough = np.clip(rough, MIN_ROUGHNESS, 1.0)
        emissive = np.broadcast_to(mat.emissive.astype(np.float64), base.shape).copy()
        if mat.emissive_tex is not None:
            emissive = emissive * sample_bilinear(mat.emissive_tex, uv, True)[:, :3]
        occlusion = np.ones(base.shape[0])
        if mat.occlusion_tex is not None:
            occlusion = sample_bilinear(mat.occlusion_tex, uv, False)[:, 0]
        lit = self._lighting(base, metallic, rough, occlusion, pos, nrm, view)
        rgb = np.clip(lit + emissive, 0.0, 1.0).astype(np.float32)
        if mat.mode == MODE_OPAQUE:
            a = np.ones_like(alpha)
        elif mat.mode == MODE_BLEND:
            a = np.clip(alpha, 0.0, 1.0)
        else:
            a = np.ones_like(alpha)  # mask : le test a déjà eu lieu au rasteriseur
        return rgb, a.astype(np.float32)

    def _perturb(
        self, tex: Array, uv: Array, nrm: Array, idx: Array, b: Array
    ) -> Array:
        geo = self.geo
        ts = sample_bilinear(tex, uv, False)[:, :3].astype(np.float64) * 2.0 - 1.0
        ts = _unit(ts)
        tan = _mix(geo.tan, idx, b)
        tan = tan - nrm * np.sum(nrm * tan, axis=1, keepdims=True)
        tan = _unit(tan)
        sign = np.sign(_mix(geo.tan_sign[:, None], idx, b)[:, 0])
        bit = np.cross(nrm, tan) * np.where(sign == 0, 1.0, sign)[:, None]
        out = _unit(tan * ts[:, :1] + bit * ts[:, 1:2] + nrm * ts[:, 2:3])
        return np.where(np.linalg.norm(out, axis=1, keepdims=True) > 0.5, out, nrm)

    def _lighting(
        self, base: Array, metallic: Array, rough: Array, occlusion: Array,
        pos: Array, nrm: Array, view: Array,
    ) -> Array:  # fmt: skip
        m = metallic[:, None]
        f0 = 0.04 * (1.0 - m) + base * m
        diffuse = base * (1.0 - m)
        a = (rough * rough)[:, None]
        a2 = a * a
        nv = np.clip(np.sum(nrm * view, axis=1, keepdims=True), 1e-4, 1.0)
        out = self.ambient[None, :] * base * occlusion[:, None]
        lights: list[
            tuple[Array, Array]
        ] = []  # (direction vers la lumière, rayonnement RGB)
        for d in self.directional:
            toward = np.broadcast_to(-np.asarray(d.direction), pos.shape)
            lights.append((toward, np.asarray(d.color) * d.intensity))
        for p in self.points:
            vec = np.asarray(p.position) - pos
            d2 = np.maximum(np.sum(vec * vec, axis=1, keepdims=True), 1e-8)
            atten = 1.0 / d2
            if p.range is not None:
                ratio = d2 / (p.range * p.range)
                atten = atten * np.clip(1.0 - ratio * ratio, 0.0, 1.0) ** 2
            lights.append(
                (vec / np.sqrt(d2), np.asarray(p.color) * p.intensity * atten)
            )
        for toward, radiance in lights:
            nl = np.clip(np.sum(nrm * toward, axis=1, keepdims=True), 0.0, 1.0)
            h = _unit(toward + view)
            nh = np.clip(np.sum(nrm * h, axis=1, keepdims=True), 0.0, 1.0)
            vh = np.clip(np.sum(view * h, axis=1, keepdims=True), 0.0, 1.0)
            fres = f0 + (1.0 - f0) * (1.0 - vh) ** 5
            denom = nh * nh * (a2 - 1.0) + 1.0
            dist = a2 / (math.pi * denom * denom)
            vis = 0.5 / np.maximum(
                nl * np.sqrt(nv * nv * (1.0 - a2) + a2)
                + nv * np.sqrt(nl * nl * (1.0 - a2) + a2),
                1e-8,
            )
            spec = fres * dist * vis
            out = (
                out
                + (spec + (1.0 - fres) * diffuse / math.pi) * nl * math.pi * radiance
            )
        return out
