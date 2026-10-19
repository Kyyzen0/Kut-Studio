"""Moteur de référence « numpy » : rasteriseur logiciel déterministe, sans Qt ni fenêtre.

Chaîne : assemblage des sommets (os puis formes, voir `assemble`) -> projection -> rasterisation dans un tampon de
visibilité (z-buffer) -> ombrage par pixel visible (une fois, sans surdessin) -> surfaces `blend` triées du plus
loin au plus proche (par triangle, centre en profondeur de vue) et composées par-dessus -> fond -> réduction du
sur-échantillonnage (moyenne en linéaire, couleurs prémultipliées) -> sRGB.

Limites connues : pas d'ombres portées, pas de flou de mouvement, pas d'éclairage par image, pas de mipmaps ; le
tri des triangles `blend` est approximatif pour des surfaces transparentes qui s'interpénètrent ; une surface
`blend` n'écrit pas la profondeur de `Frame.depth`.
"""

from __future__ import annotations

import numpy as np

from kut3d.errors import RenderError
from kut3d.mathutil import Array
from kut3d.render.assemble import Geometry, assemble
from kut3d.render.frame import Frame
from kut3d.render.limits import MAX_BLEND_TRIANGLES
from kut3d.render.materials import MODE_BLEND, MODE_MASK
from kut3d.render.raster import (
    Rasterizer,
    VisBuffer,
    build_raster_triangles,
    raster_opaque,
)
from kut3d.render.shading import Shader
from kut3d.render.textures import linear_to_srgb, srgb_to_linear
from kut3d.scene import Scene

SHADE_CHUNK = 1 << 18  # pixels ombrés d'un coup


def _tri_modes(geo: Geometry) -> tuple[Array, Array]:
    """(mode alpha, double face) de chaque triangle du maillage."""
    mats = geo.materials
    mode = np.array([m.mode for m in mats], dtype=np.int8)
    double = np.array([m.double_sided for m in mats], dtype=bool)
    per_draw = (
        geo.draw_mat[geo.tri_draw] if geo.triangle_count else np.zeros(0, np.int32)
    )
    if not mats:
        return np.zeros(0, np.int8), np.zeros(0, bool)
    return mode[per_draw], double[per_draw]


def _shade_visible(
    shader: Shader, vis: VisBuffer, acc_rgb: Array, acc_a: Array
) -> None:
    height, width = vis.tri.shape
    rows = max(1, SHADE_CHUNK // width)
    for ya in range(0, height, rows):
        yb = min(height, ya + rows)
        band = vis.tri[ya:yb]
        ys, xs = np.nonzero(band >= 0)
        if ys.size == 0:
            continue
        rgb, _ = shader.shade(
            band[ys, xs].astype(np.int64), vis.b1[ya:yb][ys, xs], vis.b2[ya:yb][ys, xs]
        )
        acc_rgb[ya:yb][ys, xs] = rgb
        acc_a[ya:yb][ys, xs] = 1.0


def _blend(
    shader: Shader, rz: Rasterizer, order: Array, vis: VisBuffer, far: float,
    acc_rgb: Array, acc_a: Array,
) -> None:  # fmt: skip
    for r in order.tolist():
        src = rz.source(r)
        for cov in rz.cover(r):
            w, ob = rz.interpolate(r, cov)
            ys, xs = np.nonzero(cov.ins)
            ys, xs = ys + cov.y0, xs + cov.x0
            ok = (w < vis.depth[ys, xs]) & (w <= far)
            if not ok.any():
                continue
            ys, xs, ob = ys[ok], xs[ok], ob[ok]
            tri = np.full(ys.size, src, dtype=np.int64)
            rgb, alpha = shader.shade(tri, ob[:, 1], ob[:, 2])
            a = alpha[:, None]
            acc_rgb[ys, xs] = rgb * a + acc_rgb[ys, xs] * (1.0 - a)
            acc_a[ys, xs] = alpha + acc_a[ys, xs] * (1.0 - alpha)


def _reduce(
    acc_rgb: Array, acc_a: Array, depth: Array, k: int
) -> tuple[Array, Array, Array]:
    if k == 1:
        return acc_rgb, acc_a, depth
    h, w = acc_a.shape[0] // k, acc_a.shape[1] // k
    rgb = acc_rgb.reshape(h, k, w, k, 3).mean(axis=(1, 3), dtype=np.float32)
    a = acc_a.reshape(h, k, w, k).mean(axis=(1, 3), dtype=np.float32)
    d = depth.reshape(h, k, w, k).min(axis=(1, 3))
    return rgb, a, d


def render_numpy(scene: Scene, t: float, size: tuple[int, int], samples: int) -> Frame:
    """Moteur « numpy » : voir `kut3d.render.render_frame`. Les arguments sont déjà validés."""
    width, height = size
    sw, sh = width * samples, height * samples
    geo = assemble(scene, t)
    cam = scene.camera
    mode, double = _tri_modes(geo)
    if int((mode == MODE_BLEND).sum()) > MAX_BLEND_TRIANGLES:
        raise RenderError(
            f"trop de triangles en alpha-blending (maximum {MAX_BLEND_TRIANGLES})."
        )
    acc_rgb = np.zeros((sh, sw, 3), dtype=np.float32)  # couleur linéaire PRÉMULTIPLIÉE
    acc_a = np.zeros((sh, sw), dtype=np.float32)
    vis = VisBuffer(sh, sw)
    if geo.triangle_count:
        proj = cam.projection_matrix(width / height) @ cam.view_matrix()
        homo = np.concatenate([geo.pos, np.ones((geo.pos.shape[0], 1))], axis=1)
        clip = homo @ proj.T
        rt = build_raster_triangles(clip, geo.tri, double, sw, sh)
        shader = Shader(geo, scene.lights, np.asarray(cam.position))
        if rt.count:
            rz = Rasterizer(rt)
            kinds = mode[rt.src]
            opaque = np.flatnonzero(kinds != MODE_BLEND)
            raster_opaque(
                rz, opaque, vis, float(cam.far), kinds == MODE_MASK, shader.mask_alpha
            )
            _shade_visible(shader, vis, acc_rgb, acc_a)
            blended = np.flatnonzero(kinds == MODE_BLEND)
            if blended.size:
                mean_w = (1.0 / rt.invw[blended]).mean(axis=1)
                far_first = blended[np.argsort(-mean_w, kind="stable")]
                _blend(shader, rz, far_first, vis, float(cam.far), acc_rgb, acc_a)
    rgb, a, depth = _reduce(acc_rgb, acc_a, vis.depth, samples)
    return _finish(rgb, a, depth, scene.background, size)


def _finish(
    rgb: Array, a: Array, depth: Array, background: Array | None, size: tuple[int, int]
) -> Frame:
    if background is not None:
        bg = np.asarray(background, dtype=np.float32)
        bg_rgb = srgb_to_linear(bg[:3]) * bg[3]
        rgb = rgb + bg_rgb[None, None, :] * (1.0 - a)[..., None]
        a = a + bg[3] * (1.0 - a)
    safe = np.where(a > 0, a, 1.0)[..., None]
    straight = np.where(a[..., None] > 0, rgb / safe, 0.0)
    color = np.empty(a.shape + (4,), dtype=np.uint8)
    color[..., :3] = np.rint(linear_to_srgb(straight) * 255.0)
    color[..., 3] = np.rint(np.clip(a, 0.0, 1.0) * 255.0)
    return Frame(
        color=color,
        alpha=np.clip(a, 0.0, 1.0).astype(np.float32),
        depth=np.asarray(depth, dtype=np.float32),
        size=size,
    )
