"""Banc du picking (sélection par rayon) et du rendu de la fenêtre 3D (ADR-0010).

Usage ::

    python -m tools.perf.kut3d_pick_bench [--spheres 7] [--size 640x360] [--repeat 5]

Mesure, sur une scène de ``--spheres`` sphères 256 x 128 (65 280 triangles chacune, 7 -> 456 960 triangles) :

1. ``assemble`` (sommets en espace monde, que le rendu fait déjà) ;
2. un rayon contre TOUS les triangles (Möller-Trumbore vectorisé numpy) ;
3. le même rayon avec un tri préalable par boîte englobante de chaque instance (ce que fera la fenêtre : la boîte de chaque
   dessin se calcule une fois par changement de géométrie) ;
4. le rendu numpy de référence à ``--size`` ;
5. le rendu QRhi (GPU) à ``--size``, s'il est utilisable ici (sinon la ligne est omise).

Les temps absolus dépendent de la machine : on compare des RATIOS (picking / rendu). Rien ici n'est un seuil de test.
"""

from __future__ import annotations

import argparse
import statistics
import time
from typing import Any, Callable

import numpy as np

import kut3d
from kut3d.render.assemble import assemble


def _median_ms(fn: Callable[[], Any], repeat: int) -> float:
    fn()  # chauffe (allocations, caches)
    samples = []
    for _ in range(repeat):
        start = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - start) * 1000.0)
    return statistics.median(samples)


def _scene(spheres: int) -> kut3d.Scene:
    items = [
        kut3d.primitive_instance(
            kut3d.Sphere(0.45, 256, 128),
            position=((i - (spheres - 1) / 2) * 1.0, 0.0, 0.0),
            name=f"s{i}",
        )
        for i in range(spheres)
    ]
    camera = kut3d.Camera(position=(0.0, 1.5, 7.0), target=(0.0, 0.0, 0.0))
    lights = [
        kut3d.AmbientLight((1, 1, 1), 0.3),
        kut3d.DirectionalLight((-0.4, -0.6, -0.7), (1, 1, 1), 2.5),
    ]
    return kut3d.Scene(
        instances=items, lights=lights, camera=camera, background=(0.1, 0.1, 0.12, 1.0)
    )


def ray_hits(
    origin: np.ndarray, direction: np.ndarray, pos: np.ndarray, tri: np.ndarray
) -> tuple[int, float] | None:
    """(indice du triangle le plus proche, distance) ou None : Möller-Trumbore, double face, vectorisé."""
    a, b, c = pos[tri[:, 0]], pos[tri[:, 1]], pos[tri[:, 2]]
    e1, e2 = b - a, c - a
    p = np.cross(direction, e2)
    det = np.einsum("ij,ij->i", e1, p)
    ok = np.abs(det) > 1e-12
    inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
    s = origin - a
    u = np.einsum("ij,ij->i", s, p) * inv
    q = np.cross(s, e1)
    v = (q @ direction) * inv
    t = np.einsum("ij,ij->i", e2, q) * inv
    hit = ok & (u >= 0) & (v >= 0) & (u + v <= 1) & (t > 1e-9)
    if not hit.any():
        return None
    ts = np.where(hit, t, np.inf)
    best = int(np.argmin(ts))
    return best, float(ts[best])


def _boxes(
    pos: np.ndarray, tri: np.ndarray, tri_draw: np.ndarray, draws: int
) -> list[tuple[np.ndarray, np.ndarray, np.ndarray]]:
    out = []
    for d in range(draws):
        sel = np.nonzero(tri_draw == d)[0]
        verts = pos[np.unique(tri[sel])]
        out.append((sel, verts.min(axis=0), verts.max(axis=0)))
    return out


def _slab(
    origin: np.ndarray, direction: np.ndarray, low: np.ndarray, high: np.ndarray
) -> bool:
    with np.errstate(divide="ignore", invalid="ignore"):
        t1, t2 = (low - origin) / direction, (high - origin) / direction
    tmin = np.nanmax(np.minimum(t1, t2))
    tmax = np.nanmin(np.maximum(t1, t2))
    return bool(tmax >= max(tmin, 0.0))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spheres", type=int, default=7)
    parser.add_argument("--size", default="640x360")
    parser.add_argument("--repeat", type=int, default=5)
    args = parser.parse_args()
    width, height = (int(x) for x in args.size.split("x"))
    scene = _scene(args.spheres)
    geo = assemble(scene, 0.0)
    print(f"triangles : {geo.triangle_count}  dessins : {len(geo.draw_mat)}")
    origin = np.array([0.0, 1.5, 7.0])
    direction = np.array([0.0, -1.5, -7.0])
    direction /= np.linalg.norm(direction)
    boxes = _boxes(geo.pos, geo.tri, geo.tri_draw, len(geo.draw_mat))

    def brute() -> None:
        ray_hits(origin, direction, geo.pos, geo.tri)

    def culled() -> None:
        best = None
        for sel, low, high in boxes:
            if _slab(origin, direction, low, high):
                hit = ray_hits(origin, direction, geo.pos, geo.tri[sel])
                if hit is not None and (best is None or hit[1] < best):
                    best = hit[1]

    t_assemble = _median_ms(lambda: assemble(scene, 0.0), args.repeat)
    t_brute = _median_ms(brute, args.repeat)
    t_culled = _median_ms(culled, args.repeat)
    t_numpy = _median_ms(
        lambda: kut3d.render_frame(scene, 0.0, (width, height), backend="numpy"),
        max(1, args.repeat // 2),
    )
    print(f"assemble                  {t_assemble:9.1f} ms")
    print(
        f"rayon, tous les triangles {t_brute:9.1f} ms   (= {t_brute / t_numpy:5.2f} x le rendu numpy)"
    )
    print(
        f"rayon, boîtes puis détail {t_culled:9.1f} ms   (= {t_culled / t_numpy:5.3f} x le rendu numpy)"
    )
    print(f"rendu numpy {width}x{height}     {t_numpy:9.1f} ms")
    if kut3d.backend_available("qrhi"):
        try:
            t_gpu = _median_ms(
                lambda: kut3d.render_frame(scene, 0.0, (width, height), backend="qrhi"),
                args.repeat,
            )
            print(
                f"rendu QRhi {width}x{height}      {t_gpu:9.1f} ms   (numpy / QRhi = {t_numpy / t_gpu:5.1f})"
            )
        except kut3d.RenderError as exc:
            print(f"rendu QRhi indisponible ici : {str(exc)[:100]}")
    else:
        print("rendu QRhi indisponible ici (sonde GPU négative)")


if __name__ == "__main__":
    main()
