"""Compositeur CPU allégé (ADR-0001)."""

from __future__ import annotations

import numpy as np

from core.cpu_composite import compose_frame, compose_frame_async
from core.gpu_composite import CompositeFrame, CompositeLayer, VideoSource
from core.gpu_effects import program_for


def test_compose_frame_at_quarter_resolution():
    rgb = np.zeros((80, 120, 3), dtype=np.float64)
    rgb[:, :, 0] = 0.8
    frame = CompositeFrame(
        120, 80, 0.25,
        (CompositeLayer("main", (1, 0, 0, 1, 0, 0), (0, 0, 120, 80), program=program_for(())),),
        (VideoSource("main", "rgba", 120, 80),),
    )
    result = compose_frame(frame, {"main": rgb}, render_scale=0.25)
    assert result.width == 30 and result.height == 20
    assert len(result.rgba) == 30 * 20 * 4
    assert result.render_scale == 0.25


def test_compose_frame_async_returns_same_bytes():
    rgb = np.full((40, 40, 3), 0.5, dtype=np.float64)
    frame = CompositeFrame(
        40, 40, 1.0,
        (CompositeLayer("main", (1, 0, 0, 1, 0, 0), (0, 0, 40, 40), program=program_for(())),),
        (VideoSource("main", "rgba", 40, 40),),
    )
    future = compose_frame_async(frame, {"main": rgb}, render_scale=0.5)
    result = future.result(timeout=10)
    assert result.width == 20 and result.height == 20


def test_compose_frame_keeps_logical_translation_at_quarter_scale():
    """Une translation en pixels séquence reste proportionnelle après scale < 1."""
    rgb = np.zeros((40, 40, 3), dtype=np.float64)
    rgb[:, :, 1] = 1.0  # vert plein
    # Décale le calque de 20 px à droite sur un cadre 80×40.
    layer = CompositeLayer(
        "main", (1, 0, 0, 1, 20, 0), (0, 0, 40, 40), program=program_for(()),
    )
    frame = CompositeFrame(
        80, 40, 0.25, (layer,), (VideoSource("main", "rgba", 40, 40),),
    )
    result = compose_frame(frame, {"main": rgb}, render_scale=0.25)
    assert result.width == 20 and result.height == 10
    rgba = np.frombuffer(result.rgba, dtype=np.uint8).reshape(10, 20, 4)
    # À scale 0.25, +20 px logique → +5 px rendu : la colonne 5 doit voir du vert.
    assert rgba[5, 5, 1] > 200
    # La zone gauche (avant la translation) reste le fond noir.
    assert rgba[5, 1, 1] < 40
