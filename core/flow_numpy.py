"""Flux optique sur le processeur, en NumPy seul : estimation pyramidale et synthèse d'une image intermédiaire.

C'est le premier backend de :mod:`core.optical_flow` : il tourne partout (aucun GPU, aucune dépendance en plus de NumPy, rien à
empaqueter) et son résultat est déterministe. Il n'est pas le plus rapide qu'on puisse écrire ; c'est le plus fiable à livrer,
et l'interface de backend laisse la place à Metal, CUDA ou Vulkan sans toucher à ce qui l'appelle.

**Estimation** — Lucas-Kanade itératif, du plus grossier au plus fin. À chaque niveau, l'image d'arrivée est déformée par le
flux courant, les gradients donnent un système 2×2 par pixel (somme sur une fenêtre), régularisé (Tikhonov) pour que les zones
sans texture ne divergent pas, puis le flux est lissé en **moyenne pondérée par la texture** : un aplat reçoit le mouvement
des bords qui l'entourent, un bord garde le sien. Le flux d'une paire est estimé dans les deux sens ; l'accord des deux, et
l'erreur qui reste après déformation, donnent la confiance de chaque pixel (:mod:`core.flow_field`).

**Synthèse** — projection avant (*splatting*) : chaque pixel de ``A`` est posé à ``x + t·F(A→B)``, chaque pixel de ``B`` à
``x + (1−t)·F(B→A)``, pondérés par leur confiance puis moyennés, ``A`` pesant ``1−t`` et ``B`` pesant ``t``. Contrairement à une
déformation arrière, un objet qui se déplace arrive à sa bonne place sur ses bords ; une zone qu'aucune des deux images ne
recouvre (désocclusion) retombe sur le mélange simple. Rien n'est supposé sur la scène : ce qui n'est pas fiable est mélangé,
jamais « deviné ».
"""

from __future__ import annotations

import math
from collections.abc import Callable
from functools import lru_cache
from typing import TYPE_CHECKING

import numpy as np

from .flow_field import FlowField
from .optical_flow import FlowCancelled

if TYPE_CHECKING:
    import numpy.typing as npt

    from .optical_flow import FlowParams

    Plane = npt.NDArray[np.float32]
    Frame = npt.NDArray[np.float32]

CancelCheck = Callable[[], bool]

_MIN_LEVEL_SIZE = 16
_MAX_STEP = 1.5
"""Correction maximale (px) d'une itération : un système mal conditionné ne doit pas faire sauter le flux."""
_TEXTURE_FLOOR = 1e-4
"""Valeur propre minimale (images en 0…1) à partir de laquelle un pixel apporte une information de mouvement."""
_PHOTOMETRIC_SIGMA = 0.08
_CONSISTENCY_SIGMA = 1.5
"""Écarts (luminance 0…1 ; pixels d'analyse) qui ramènent la confiance à ``1/e``."""
_PRIORITY_PIXELS = 16.0
"""Au-delà de ce déplacement (px), un pixel ne gagne plus de priorité sur ses voisins (le premier plan bouge plus vite)."""
_COVERAGE_REFERENCE = 0.25
"""Poids cumulé en dessous duquel un pixel de sortie est mélangé au résultat simple, au prorata."""


def _check(cancel: CancelCheck | None) -> None:
    if cancel is not None and cancel():
        raise FlowCancelled


@lru_cache(maxsize=16)
def _grid(height: int, width: int) -> tuple[Plane, Plane]:
    ys, xs = np.mgrid[0:height, 0:width]
    return xs.astype(np.float32), ys.astype(np.float32)


def halve(plane: Plane) -> Plane:
    """Plan de moitié de côté : moyenne 2×2 (le dernier rang d'une dimension impaire est dupliqué)."""
    if plane.shape[0] % 2:
        plane = np.concatenate((plane, plane[-1:]), axis=0)
    if plane.shape[1] % 2:
        plane = np.concatenate((plane, plane[:, -1:]), axis=1)
    return (0.25 * (plane[0::2, 0::2] + plane[1::2, 0::2] + plane[0::2, 1::2] + plane[1::2, 1::2])).astype(np.float32)


def pyramid(plane: Plane, levels: int) -> list[Plane]:
    """``plane`` puis ses versions réduites de moitié (au plus ``levels`` niveaux, jamais sous 16 px de côté)."""
    result = [plane]
    while len(result) < levels and min(result[-1].shape) >= 2 * _MIN_LEVEL_SIZE:
        result.append(halve(result[-1]))
    return result


def box_mean(plane: Plane, radius: int) -> Plane:
    """Moyenne sur une fenêtre ``(2r+1)²`` (bords prolongés), par sommes cumulées : coût indépendant du rayon."""
    if radius <= 0:
        return plane
    height, width = plane.shape
    size = 2 * radius + 1
    padded = np.pad(plane, radius, mode="edge")
    rows = np.cumsum(padded, axis=0, dtype=np.float64)
    rows = np.concatenate((np.zeros((1, rows.shape[1])), rows), axis=0)
    band = rows[size:] - rows[:-size]
    cols = np.cumsum(band, axis=1)
    cols = np.concatenate((np.zeros((height, 1)), cols), axis=1)
    return ((cols[:, size:] - cols[:, :-size]) / (size * size)).astype(np.float32)


def warp(image: Frame, u: Plane, v: Plane) -> Frame:
    """``image`` lue en ``(x + u, y + v)`` : échantillonnage bilinéaire, bords prolongés. Plan ``(h, w)`` ou image ``(h, w, c)``."""
    height, width = u.shape
    grid_x, grid_y = _grid(height, width)
    xs = np.clip(grid_x + u, 0.0, width - 1.0)
    ys = np.clip(grid_y + v, 0.0, height - 1.0)
    x0 = xs.astype(np.intp)
    y0 = ys.astype(np.intp)
    x1 = np.minimum(x0 + 1, width - 1)
    y1 = np.minimum(y0 + 1, height - 1)
    fx = xs - x0
    fy = ys - y0
    flat = image.reshape(height * width, -1)
    top_left = flat[(y0 * width + x0).ravel()]
    top_right = flat[(y0 * width + x1).ravel()]
    bottom_left = flat[(y1 * width + x0).ravel()]
    bottom_right = flat[(y1 * width + x1).ravel()]
    fx = fx.reshape(-1, 1)
    fy = fy.reshape(-1, 1)
    mixed = (top_left * (1.0 - fx) + top_right * fx) * (1.0 - fy) + (bottom_left * (1.0 - fx) + bottom_right * fx) * fy
    return mixed.reshape(image.shape).astype(np.float32, copy=False)


def _gradients(plane: Plane) -> tuple[Plane, Plane]:
    gx = np.empty_like(plane)
    gy = np.empty_like(plane)
    gx[:, 1:-1] = 0.5 * (plane[:, 2:] - plane[:, :-2])
    gx[:, 0] = plane[:, 1] - plane[:, 0]
    gx[:, -1] = plane[:, -1] - plane[:, -2]
    gy[1:-1] = 0.5 * (plane[2:] - plane[:-2])
    gy[0] = plane[1] - plane[0]
    gy[-1] = plane[-1] - plane[-2]
    return gx, gy


def _refine(a: Plane, b: Plane, u: Plane, v: Plane, params: FlowParams, cancel: CancelCheck | None) -> tuple[Plane, Plane]:
    """Itérations de Lucas-Kanade d'un niveau : corrige ``(u, v)`` pour que ``b`` déformée ressemble à ``a``."""
    for _ in range(params.iterations):
        _check(cancel)
        warped = warp(b, u, v)
        ix, iy = _gradients(0.5 * (a + warped))
        it = warped - a
        sxx = box_mean(ix * ix, params.window)
        sxy = box_mean(ix * iy, params.window)
        syy = box_mean(iy * iy, params.window)
        sxt = box_mean(ix * it, params.window)
        syt = box_mean(iy * it, params.window)
        lam = params.regularization * (sxx + syy) + 1e-7
        a11, a22 = sxx + lam, syy + lam
        det = a11 * a22 - sxy * sxy
        du = np.clip(-(a22 * sxt - sxy * syt) / det, -_MAX_STEP, _MAX_STEP)
        dv = np.clip(-(a11 * syt - sxy * sxt) / det, -_MAX_STEP, _MAX_STEP)
        trace = sxx + syy
        smallest = 0.5 * (trace - np.sqrt((sxx - syy) ** 2 + 4.0 * sxy * sxy))
        trust = (smallest / (smallest + _TEXTURE_FLOOR)).astype(np.float32)
        u = _blend_by_trust(u + du, trust, params.smoothing)
        v = _blend_by_trust(v + dv, trust, params.smoothing)
    return u, v


def _blend_by_trust(plane: Plane, trust: Plane, radius: int) -> Plane:
    """Moyenne pondérée par la texture : un aplat reprend le mouvement de ses bords, un bord garde le sien."""
    weighted = box_mean(plane * trust, radius)
    weights = box_mean(trust, radius)
    return ((weighted + 1e-3 * plane) / (weights + 1e-3)).astype(np.float32)


def _upsample_flow(u: Plane, v: Plane, shape: tuple[int, int]) -> tuple[Plane, Plane]:
    """Flux du niveau grossier ramené au niveau fin (taille ``shape``), vecteurs doublés."""
    height, width = shape
    ru = np.repeat(np.repeat(u, 2, axis=0), 2, axis=1)[:height, :width] * 2.0
    rv = np.repeat(np.repeat(v, 2, axis=0), 2, axis=1)[:height, :width] * 2.0
    if ru.shape != (height, width):                       # dimensions impaires : on complète par le dernier rang
        ru = np.pad(ru, ((0, height - ru.shape[0]), (0, width - ru.shape[1])), mode="edge")
        rv = np.pad(rv, ((0, height - rv.shape[0]), (0, width - rv.shape[1])), mode="edge")
    return ru.astype(np.float32), rv.astype(np.float32)


def estimate(a: Plane, b: Plane, params: FlowParams, cancel: CancelCheck | None = None) -> tuple[Plane, Plane]:
    """Flux ``(u, v)`` de ``a`` vers ``b`` (luminance 0…1, même taille), du niveau grossier au niveau fin."""
    pyramid_a = pyramid(a, params.levels)
    pyramid_b = pyramid(b, params.levels)
    u = np.zeros_like(pyramid_a[-1])
    v = np.zeros_like(pyramid_a[-1])
    for level in range(len(pyramid_a) - 1, -1, -1):
        if level < len(pyramid_a) - 1:
            u, v = _upsample_flow(u, v, pyramid_a[level].shape)
        u, v = _refine(pyramid_a[level], pyramid_b[level], u, v, params, cancel)
    return u, v


def confidence(a: Plane, b: Plane, forward: tuple[Plane, Plane], backward: tuple[Plane, Plane]) -> Plane:
    """Confiance (0…1) du flux ``forward`` : erreur photométrique après déformation × accord avec le flux ``backward``."""
    u, v = forward
    error = np.abs(a - warp(b, u, v))
    back_u = warp(backward[0], u, v)
    back_v = warp(backward[1], u, v)
    residual = np.hypot(u + back_u, v + back_v)
    photometric = np.exp(-((error / _PHOTOMETRIC_SIGMA) ** 2))
    agreement = np.exp(-((residual / _CONSISTENCY_SIGMA) ** 2))
    return box_mean((photometric * agreement).astype(np.float32), 1)


def estimate_pair(a: Plane, b: Plane, params: FlowParams, cancel: CancelCheck | None = None) -> tuple[FlowField, FlowField]:
    """Les deux flux d'une paire d'images (``a → b`` et ``b → a``), chacun avec sa confiance."""
    forward = estimate(a, b, params, cancel)
    backward = estimate(b, a, params, cancel)
    return (
        FlowField(forward[0], forward[1], confidence(a, b, forward, backward)),
        FlowField(backward[0], backward[1], confidence(b, a, backward, forward)),
    )


# ---------------------------------------------------------------------------
# Synthèse
# ---------------------------------------------------------------------------


def _splat(source: Frame, du: Plane, dv: Plane, weight: Plane) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.float64]]:
    """Pose chaque pixel de ``source`` à ``(x + du, y + dv)`` (quatre voisins, poids bilinéaires × ``weight``)."""
    height, width, channels = source.shape
    count = height * width
    grid_x, grid_y = _grid(height, width)
    xs = grid_x + du
    ys = grid_y + dv
    x0f = np.floor(xs)
    y0f = np.floor(ys)
    fx = (xs - x0f).ravel()
    fy = (ys - y0f).ravel()
    x0 = x0f.astype(np.intp).ravel()
    y0 = y0f.astype(np.intp).ravel()
    flat_weight = weight.ravel()
    flat = source.reshape(count, channels)
    accumulated = np.zeros((count, channels), dtype=np.float64)
    total = np.zeros(count, dtype=np.float64)
    for step_x, step_y, share in (
        (0, 0, (1.0 - fx) * (1.0 - fy)), (1, 0, fx * (1.0 - fy)), (0, 1, (1.0 - fx) * fy), (1, 1, fx * fy),
    ):
        xi = x0 + step_x
        yi = y0 + step_y
        inside = (xi >= 0) & (xi < width) & (yi >= 0) & (yi < height)
        index = (yi * width + xi)[inside]
        mass = (share * flat_weight)[inside]
        total += np.bincount(index, weights=mass, minlength=count)
        values = flat[inside]
        for channel in range(channels):
            accumulated[:, channel] += np.bincount(index, weights=mass * values[:, channel], minlength=count)
    return accumulated, total


def synthesize(
    a: Frame, b: Frame, forward: FlowField, backward: FlowField, t: float
) -> tuple[Frame, float, float]:
    """L'image au point ``t`` (0…1) entre ``a`` et ``b`` : ``(image, confiance moyenne, part non fiable)``.

    ``a`` et ``b`` sont ``(h, w, c)`` en flottants 0…1 (alpha prémultiplié s'il y en a un). Les flux sont déjà à la grille
    des images (:meth:`core.optical_flow.PairAnalysis.fields` les y ramène, vecteurs mis à l'échelle).
    """
    height, width = a.shape[:2]
    if (forward.width, forward.height) != (width, height) or (backward.width, backward.height) != (width, height):
        raise ValueError("les flux doivent être à la grille des images (PairAnalysis.fields)")

    def priority(field: FlowField) -> Plane:
        speed = np.hypot(field.u, field.v)
        return (field.confidence * (1.0 + np.minimum(speed, _PRIORITY_PIXELS) / (_PRIORITY_PIXELS / 4.0))).astype(np.float32)

    mass_a, weight_a = _splat(a, forward.u * t, forward.v * t, priority(forward))
    mass_b, weight_b = _splat(b, backward.u * (1.0 - t), backward.v * (1.0 - t), priority(backward))
    numerator = (1.0 - t) * mass_a + t * mass_b
    denominator = (1.0 - t) * weight_a + t * weight_b
    plain = (1.0 - t) * a + t * b
    warped = (numerator / np.maximum(denominator, 1e-9)[:, None]).reshape(a.shape)
    coverage = np.clip(denominator / _COVERAGE_REFERENCE, 0.0, 1.0).reshape(height, width, 1)
    result = (coverage * warped + (1.0 - coverage) * plain).astype(np.float32)
    reliability = 0.5 * (forward.mean_confidence + backward.mean_confidence)
    return result, float(reliability * float(coverage.mean())), float((coverage < 0.5).mean())


def blend(a: Frame, b: Frame, t: float) -> Frame:
    """``a·(1 − t) + b·t`` : le mélange image à image, sans mouvement."""
    return ((1.0 - t) * a + t * b).astype(np.float32)


def scene_change(a: Plane, b: Plane) -> float:
    """Distance de variation totale (0…1) entre les histogrammes de luminance de deux images : ≈ 1 pour deux plans sans rapport."""
    hist_a = np.histogram(a, bins=32, range=(0.0, 1.0))[0] / a.size
    hist_b = np.histogram(b, bins=32, range=(0.0, 1.0))[0] / b.size
    return float(0.5 * np.abs(hist_a - hist_b).sum())


def mean_difference(a: Plane, b: Plane) -> float:
    """Écart moyen absolu de luminance (0…1)."""
    return float(np.abs(a - b).mean()) if a.shape == b.shape else math.inf


def correlation(a: Plane, b: Plane) -> float:
    """Corrélation normalisée (−1…1) de deux images, insensible à un changement global de luminosité ou de contraste.

    ≈ 1 pour la même scène même si un flash l'éclaire ; ≈ 0 pour deux plans sans rapport : c'est ce qui distingue un flash
    d'une coupure quand les histogrammes diffèrent dans les deux cas.
    """
    flat_a = a.astype(np.float64).ravel() - float(a.mean())
    flat_b = b.astype(np.float64).ravel() - float(b.mean())
    norm = math.sqrt(float((flat_a * flat_a).sum()) * float((flat_b * flat_b).sum()))
    return float((flat_a * flat_b).sum() / norm) if norm > 1e-12 else 1.0


__all__ = [
    "FlowCancelled",
    "blend",
    "correlation",
    "estimate",
    "estimate_pair",
    "mean_difference",
    "scene_change",
    "synthesize",
]
