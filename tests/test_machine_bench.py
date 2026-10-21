"""Banc machine mesuré (ADR-0001 étape 1)."""

from __future__ import annotations

import json
import time
from types import SimpleNamespace

from core.machine_bench import (
    SCHEMA_VERSION,
    BenchResult,
    _score_to_profile,
    bench_enabled,
    ensure_machine_bench,
    load_cached_bench,
    machine_fingerprint,
    measured_profile_name,
    run_machine_bench,
    save_cached_bench,
)
from core.runtime_profile import MachineResources, recommend_profile, resolve_profile
from core.studio_runtime import StudioRuntime
from core.task_queue import CancelToken


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
    monkeypatch.delenv(
        "KUT_STUDIO_MACHINE_BENCH", raising=False
    )  # tests/conftest.py le coupe pour toute la suite
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
    assert (
        measured_profile_name(
            resources,
            directory=tmp_path,
            environment={"KUT_STUDIO_MACHINE_BENCH": "off"},
        )
        is None
    )


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


def test_tied_votes_without_decode_are_balanced():
    """Compose high + raster low sans decode : égalité → balanced (déterministe)."""
    assert (
        _score_to_profile(compose_ms=10.0, decode_fps=None, raster_ms=40.0)
        == "balanced"
    )
    assert (
        _score_to_profile(compose_ms=200.0, decode_fps=None, raster_ms=2.0)
        == "balanced"
    )


def test_bench_enabled_switch():
    assert bench_enabled({}) is True
    assert bench_enabled({"KUT_STUDIO_MACHINE_BENCH": "off"}) is False


def test_schedule_machine_bench_skips_when_off(monkeypatch, tmp_path):
    monkeypatch.setenv("KUT_STUDIO_MACHINE_BENCH", "off")
    monkeypatch.setattr("core.machine_bench.user_cache_dir", lambda: tmp_path)
    runtime = StudioRuntime(
        resources=MachineResources(logical_cpus=4, memory_bytes=8 * 1024**3),
    )
    runtime.worker.stop()
    scheduled: list[str] = []
    runtime.schedule = lambda key, fn, **kw: scheduled.append(key)  # type: ignore[method-assign]
    runtime.schedule_machine_bench(force=False)
    assert scheduled == []


def test_schedule_machine_bench_accepts_token_and_defers_refresh(monkeypatch, tmp_path):
    monkeypatch.delenv("KUT_STUDIO_MACHINE_BENCH", raising=False)
    monkeypatch.setattr("core.machine_bench.user_cache_dir", lambda: tmp_path)

    def fake_run(res, *, decode_runner=None):
        return BenchResult(
            profile="high",
            compose_ms=10.0,
            decode_fps=120.0,
            raster_ms=2.0,
            measured_at=time.time(),
            fingerprint=machine_fingerprint(res),
        )

    monkeypatch.setattr("core.machine_bench.run_machine_bench", fake_run)
    runtime = StudioRuntime(
        profile="auto",
        preview_quality="auto",
        resources=MachineResources(logical_cpus=2, memory_bytes=4 * 1024**3),
    )
    runtime.worker.stop()
    held: list = []

    def capture(key, fn, *, priority=0, session_id=0):
        held.append(fn)
        return CancelToken()

    runtime.schedule = capture  # type: ignore[method-assign]
    runtime.schedule_machine_bench(force=True)
    assert len(held) == 1
    held[0](CancelToken())  # ne doit pas lever TypeError
    assert runtime.machine_bench_refresh_pending() is True
    # Heuristique low (2 cœurs) tant que le rafraîchissement n'est pas appliqué.
    assert runtime.resolved_profile().name == "low"
    assert runtime.apply_pending_machine_bench() is True
    assert runtime.resolved_profile().name == "high"
    assert runtime.apply_pending_machine_bench() is False
