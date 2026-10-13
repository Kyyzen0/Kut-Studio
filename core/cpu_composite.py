"""Compositeur CPU allégé (ADR-0001, niveau « CPU » / pas de GPU utilisable).

Exécute le même plan que le moniteur GPU (:func:`core.gpu_composite.reference_frame`)
dans un thread de travail, à résolution réduite (typiquement 1/4). Les segments
FFmpeg fidèles restent disponibles en fond ; ce module sert l'aperçu temps réel
quand Auto choisit le niveau CPU.
"""

from __future__ import annotations

import logging
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass

import numpy as np

from .gpu_composite import CompositeFrame, reference_frame

LOGGER = logging.getLogger(__name__)

_POOL: ThreadPoolExecutor | None = None


def _pool() -> ThreadPoolExecutor:
    global _POOL
    if _POOL is None:
        _POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="kut-cpu-comp")
    return _POOL


@dataclass(frozen=True)
class CpuFrame:
    """Résultat d'une composition CPU : RVBA uint8, taille du tampon (pas du cadre)."""

    width: int
    height: int
    rgba: bytes
    render_scale: float


def compose_frame(
    frame: CompositeFrame,
    sources_rgb: dict[str, np.ndarray],
    mattes: dict[str, np.ndarray] | None = None,
    luts: dict[str, np.ndarray] | None = None,
    *,
    render_scale: float | None = None,
) -> CpuFrame:
    """Compose ``frame`` sur CPU. ``render_scale`` défaut : celui du plan (souvent 0,25).

    La géométrie (matrices, ``fit``) reste en pixels logiques de la séquence ;
    la réduction passe uniquement par ``render_scale`` (comme le moniteur GPU).
    """
    scale = float(frame.render_scale if render_scale is None else render_scale)
    scale = max(0.05, min(1.0, scale))
    working = CompositeFrame(
        frame.canvas_width, frame.canvas_height, scale,
        frame.layers, frame.sources, frame.background, frame.adjustments,
    )
    width, height = working.render_size
    # Mattes à la résolution de rendu ; les sources restent à leur taille native
    # (:func:`reference_frame` les échantillonne via ``fit``).
    scaled_mattes: dict[str, np.ndarray] = {}
    for key, matte in (mattes or {}).items():
        if matte.shape[0] == height and matte.shape[1] == width:
            scaled_mattes[key] = matte
            continue
        ys = (np.arange(height) * matte.shape[0] / height).astype(np.int32)
        xs = (np.arange(width) * matte.shape[1] / width).astype(np.int32)
        scaled_mattes[key] = matte[ys][:, xs]
    rgb = reference_frame(working, sources_rgb, scaled_mattes, luts)
    alpha = np.ones((height, width, 1), dtype=np.float64)
    rgba = (np.clip(np.concatenate([rgb, alpha], axis=-1), 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)
    return CpuFrame(width, height, rgba.tobytes(), scale)


def compose_frame_async(
    frame: CompositeFrame,
    sources_rgb: dict[str, np.ndarray],
    mattes: dict[str, np.ndarray] | None = None,
    luts: dict[str, np.ndarray] | None = None,
    *,
    render_scale: float | None = None,
) -> Future:
    """Comme :func:`compose_frame`, dans le fil de travail du compositeur CPU."""
    return _pool().submit(
        compose_frame, frame, sources_rgb, mattes, luts, render_scale=render_scale,
    )


__all__ = ["CpuFrame", "compose_frame", "compose_frame_async"]
