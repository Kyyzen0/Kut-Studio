"""Rasteriseur logiciel numpy (référence déterministe) : barycentrique, z-buffer, nearest, lambert."""

from __future__ import annotations

import numpy as np

import cube_math as cm
from texture import checker_texture


def render_cube(
    width: int, height: int, angle_deg: float, cull_backfaces: bool = True
) -> np.ndarray:
    """Rend le cube texturé et retourne un tableau (H, W, 4) uint8.

    ``cull_backfaces=False`` désactive l'élimination des faces arrière : seul le z-buffer cache alors l'arrière.
    """
    pos, uv, nrm, idx = cm.cube_geometry()
    tex = checker_texture()
    th, tw = tex.shape[:2]
    model = cm.model_matrix(angle_deg)
    mvp = (cm.perspective_matrix(width / height) @ cm.view_matrix() @ model).astype(
        np.float64
    )
    world_n = nrm @ model[:3, :3].T
    light = np.array(cm.LIGHT_DIR, np.float64)
    light /= np.linalg.norm(light)
    lam = np.clip(world_n @ light, 0, 1)
    shade = np.minimum(
        cm.AMBIENT + (1 - cm.AMBIENT) * lam, 1.0
    )  # par sommet = par face (normale constante)

    homo = (
        np.concatenate([pos, np.ones((len(pos), 1), np.float32)], axis=1).astype(
            np.float64
        )
        @ mvp.T
    )
    w = homo[:, 3]
    ndc = homo[:, :3] / w[:, None]
    sx = (ndc[:, 0] * 0.5 + 0.5) * width
    sy = (1 - (ndc[:, 1] * 0.5 + 0.5)) * height  # Y image vers le bas

    img = np.empty((height, width, 4), np.uint8)
    img[...] = cm.BACKGROUND
    zbuf = np.full((height, width), np.inf)

    for t in range(0, len(idx), 3):
        i0, i1, i2 = idx[t : t + 3]
        if min(w[i0], w[i1], w[i2]) <= 0:
            continue  # derrière la caméra (cube entièrement devant, jamais atteint ici)
        x0, y0, x1, y1, x2, y2 = sx[i0], sy[i0], sx[i1], sy[i1], sx[i2], sy[i2]
        area = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
        # Y image inversé : un triangle anti-horaire vu de face a une aire négative ici ; positive = face arrière.
        if area == 0 or (cull_backfaces and area > 0):
            continue  # élimination des faces arrière (et des triangles dégénérés)
        xmin = max(int(np.floor(min(x0, x1, x2))), 0)
        xmax = min(int(np.ceil(max(x0, x1, x2))), width - 1)
        ymin = max(int(np.floor(min(y0, y1, y2))), 0)
        ymax = min(int(np.ceil(max(y0, y1, y2))), height - 1)
        if xmin > xmax or ymin > ymax:
            continue
        px, py = np.meshgrid(
            np.arange(xmin, xmax + 1) + 0.5, np.arange(ymin, ymax + 1) + 0.5
        )
        b0 = ((x1 - px) * (y2 - py) - (x2 - px) * (y1 - py)) / area
        b1 = ((x2 - px) * (y0 - py) - (x0 - px) * (y2 - py)) / area
        b2 = 1 - b0 - b1
        inside = (b0 >= 0) & (b1 >= 0) & (b2 >= 0)
        if not inside.any():
            continue
        z = b0 * ndc[i0, 2] + b1 * ndc[i1, 2] + b2 * ndc[i2, 2]
        region = zbuf[ymin : ymax + 1, xmin : xmax + 1]
        vis = inside & (z < region) & (z >= -1) & (z <= 1)
        if not vis.any():
            continue
        # Interpolation perspective-correcte des UV via 1/w.
        iw0, iw1, iw2 = 1 / w[i0], 1 / w[i1], 1 / w[i2]
        denom = b0 * iw0 + b1 * iw1 + b2 * iw2
        u = (b0 * iw0 * uv[i0, 0] + b1 * iw1 * uv[i1, 0] + b2 * iw2 * uv[i2, 0]) / denom
        v = (b0 * iw0 * uv[i0, 1] + b1 * iw1 * uv[i1, 1] + b2 * iw2 * uv[i2, 1]) / denom
        tx = np.clip((u * tw).astype(np.int64), 0, tw - 1)
        ty = np.clip((v * th).astype(np.int64), 0, th - 1)
        color = tex[ty, tx].astype(np.float64)
        color[..., :3] *= shade[i0]
        out = region.copy()
        out[vis] = z[vis]
        zbuf[ymin : ymax + 1, xmin : xmax + 1] = out
        sub = img[ymin : ymax + 1, xmin : xmax + 1]
        sub[vis] = np.round(color[vis]).astype(np.uint8)
    return img


class NumpyRenderer:
    """Même interface que les voies GPU : ``render(angle_deg)``."""

    backend = "numpy (CPU)"

    def __init__(self, width: int, height: int) -> None:
        self.width, self.height = width, height

    def render(self, angle_deg: float) -> np.ndarray:
        return render_cube(self.width, self.height, angle_deg)
