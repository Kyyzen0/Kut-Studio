"""Grain procédural GPU (ADR-0001) : paramètres, référence, shader présent."""

from __future__ import annotations

import numpy as np

from core.gpu_backend import shader_dir
from core.gpu_grain import grain_cell_counts, grain_index, grain_uniforms, reference_grain


def test_grain_index_loops_over_eight_frames():
    assert grain_index(0.0) == 0
    assert grain_index(1.0 / 24.0) == 1
    assert grain_index(8.0 / 24.0) == 0


def test_grain_cell_counts_and_uniforms():
    cols, rows = grain_cell_counts(1920, 1080, density=2.0)
    assert cols >= 2 and rows >= 2
    misc = grain_uniforms(seed=7, index=3, cols=cols, rows=rows)
    assert misc == (7.0, 3.0, float(cols), float(rows))


def test_reference_grain_is_centered_grey_noise():
    rgb = reference_grain(64, 48, seed=1, index=0, cols=32, rows=24)
    assert rgb.shape == (48, 64, 3)
    assert 0.2 < float(rgb.mean()) < 0.8
    assert float(np.std(rgb)) > 0.01


def test_grain_shader_artifact_is_versioned():
    path = shader_dir() / "grain.frag.qsb"
    assert path.is_file(), path
    assert path.stat().st_size > 100


def test_plan_frame_emits_grain_pass():
    from core.gpu_composite import CompositeFrame, CompositeLayer, VideoSource, plan_frame
    from core.gpu_effects import program_for

    cols, rows = grain_cell_counts(160, 90, density=2.0)
    frame = CompositeFrame(
        160, 90, 1.0,
        (CompositeLayer("g", (1, 0, 0, 1, 0, 0), (0, 0, 160, 90), program=program_for(())),),
        (VideoSource("g", "grain", cols, rows, colorspace="7", color_range="2"),),
    )
    plan = plan_frame(frame)
    assert any(spec.shader == "grain" for spec in plan.passes)
