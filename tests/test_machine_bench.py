"""Banc machine mesuré (ADR-0001 étape 1)."""

from __future__ import annotations

import json
import time
from types import SimpleNamespace

from core.machine_bench import (
    SCHEMA_VERSION,
    BenchResult,
    ensure_machine_bench,
    load_cached_bench,
    machine_fingerprint,
    measured_profile_name,
    run_machine_bench,
    save_cached_bench,
)
from core.runtime_profile import MachineResources, recommend_profile, resolve_profile


def test_fingerprint_changes_with_cpu_count():
    a = MachineResources(logical_cpus=4, memory_bytes=8 * 1024**3)
    b = MachineResources(logical_cpus=8, memory_bytes=8 * 1024**3)
    assert machine_fingerprint(a) != machine_fingerprint(b)


def test_cache_roundtrip_and_invalidation(tmp_path):
    resources = MachineResources(logical_cpus=8, memory_bytes=16 * 1024**3)
    result = BenchResult(
        profile="balanced",
        compose_ms=50.0,
        decode_fps=60.0,
        raster_ms=10.0,
        measured_at=1_700_000_000.0,
        fingerprint=machine_fingerprint(resources),
    )
    save_cached_bench(result, directory=tmp_path)
    loaded = load_cached_bench(resources, directory=tmp_path, now=1_700_000_100.0)
    assert loaded is not None and loaded.profile == "balanced"

    other = MachineResources(logical_cpus=2, memory_bytes=4 * 1024**3)
    assert load_cached_bench(other, directory=tmp_path, now=1_700_000_100.0) is None

    stale = tmp_path / "machine-profile.json"
    payload = json.loads(stale.read_text(encoding="utf-8"))
    payload["schema"] = SCHEMA_VERSION + 1
    stale.write_text(json.dumps(payload), encoding="utf-8")
    assert load_cached_bench(resources, directory=tmp_path, now=1_700_000_100.0) is None


def test_ensure_uses_cache_without_rerunning(tmp_path, monkeypatch):
    resources = MachineResources(logical_cpus=8, memory_bytes=16 * 1024**3)
    calls = {"n": 0}

    def fake_run(res, *, decode_runner=None):
        calls["n"] += 1
        return BenchResult(
            profile="high",
            compose_ms=10.0,
            decode_fps=120.0,
            raster_ms=2.0,
            measured_at=time.time(),
            fingerprint=machine_fingerprint(res),
        )

    monkeypatch.setattr("core.machine_bench.run_machine_bench", fake_run)
    first = ensure_machine_bench(resources, directory=tmp_path)
    second = ensure_machine_bench(resources, directory=tmp_path)
    assert first.profile == "high" and second.profile == "high"
    assert calls["n"] == 1


def test_recommend_profile_prefers_measured():
    resources = MachineResources(logical_cpus=2, memory_bytes=4 * 1024**3)
    assert recommend_profile(resources) == "low"
    assert recommend_profile(resources, measured="high") == "high"


def test_resolve_auto_reads_warm_cache(tmp_path, monkeypatch):
    resources = MachineResources(logical_cpus=2, memory_bytes=4 * 1024**3)
    save_cached_bench(
        BenchResult(
            profile="balanced",
            compose_ms=55.0,
            decode_fps=50.0,
            raster_ms=12.0,
            measured_at=time.time(),
            fingerprint=machine_fingerprint(resources),
        ),
        directory=tmp_path,
    )
    monkeypatch.setattr("core.machine_bench.user_cache_dir", lambda: tmp_path)
    monkeypatch.delenv("KUT_STUDIO_MACHINE_BENCH", raising=False)
    profile = resolve_profile("auto", resources, "light")
    assert profile.name == "balanced"


def test_measured_profile_name_respects_off_switch(tmp_path, monkeypatch):
    resources = MachineResources(logical_cpus=8, memory_bytes=16 * 1024**3)
    save_cached_bench(
        BenchResult(
            profile="high",
            compose_ms=10.0,
            decode_fps=200.0,
            raster_ms=1.0,
            measured_at=time.time(),
            fingerprint=machine_fingerprint(resources),
        ),
        directory=tmp_path,
    )
    assert measured_profile_name(resources, directory=tmp_path) == "high"
    assert measured_profile_name(resources, directory=tmp_path, environment={"KUT_STUDIO_MACHINE_BENCH": "off"}) is None


def test_run_machine_bench_with_fake_decode():
    def runner(command):
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    result = run_machine_bench(
        MachineResources(logical_cpus=4, memory_bytes=8 * 1024**3),
        decode_runner=runner,
    )
    assert result.profile in {"low", "balanced", "high"}
    assert result.compose_ms > 0 and result.raster_ms >= 0
    assert result.decode_fps is not None and result.decode_fps > 0
