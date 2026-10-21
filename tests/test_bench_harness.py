"""Le banc de mesures fonctionne (petit scénario) et sait comparer deux rapports."""

from __future__ import annotations

import json

from tools.perf import bench
from tools.perf.synthetic import KINDS, build_project


def test_synthetic_projects_are_deterministic_and_have_the_requested_size():
    for kind in KINDS:
        first, second = build_project(250, kind), build_project(250, kind)
        clips = [c for t in first.tracks for c in t.clips]
        assert len(clips) == 250
        assert [c.id for c in clips] == [c.id for t in second.tracks for c in t.clips]
        assert [c.timeline_start for c in clips] == [c.timeline_start for t in second.tracks for c in t.clips]
    assert len(build_project(90, "short_1t").tracks) == 1 and len(build_project(90, "short_8t").tracks) == 8
    long_clips = [c.duration for t in build_project(80, "long_8t").tracks for c in t.clips]
    assert min(long_clips) >= 300.0


def test_unknown_scenarios_are_rejected():
    import pytest

    with pytest.raises(ValueError):
        build_project(10, "n'importe quoi")


def test_a_tiny_benchmark_run_produces_every_section(tmp_path):
    report = bench.run(sizes=(60,), kinds=("short_1t",), ui_max=60)
    assert report["meta"]["sizes"] == [60]
    core = report["core"]["short_1t:60"]
    for metric in ("load_project_ms", "index_build_ms", "active_at_ms", "snap_ms", "render_plan_ms", "py_peak_mb"):
        assert core[metric] >= 0
    timeline = report["timeline"]["short_1t:60"]
    for metric in ("scroll_step_ms", "zoom_step_ms", "playhead_step_ms", "layout_refresh_ms", "snap_position_ms"):
        assert timeline[metric] >= 0
    assert report["window"]["short_1t:60"]["seek_paused_ms"] >= 0
    assert "memory_get_hot_us" in report["caches"] and "segment_store_200_ms" in report["caches"]
    json.dumps(report)                                                       # sérialisable


def test_compare_prints_the_gain_factor():
    before = {"core": {"a:1": {"snap_ms": 50.0}}, "timeline": {}, "window": {}, "caches": {}, "thumbnails": {}}
    after = {"core": {"a:1": {"snap_ms": 0.5}}, "timeline": {}, "window": {}, "caches": {}, "thumbnails": {}}
    table = bench.compare(before, after)
    assert "snap_ms" in table and "×100.0" in table
