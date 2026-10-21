"""Régulateur d'aperçu : échelle liée au banc, énergie, rendu à la demande."""

from __future__ import annotations

from core.power_state import THERMAL_SERIOUS, PowerState
from core.prefetch import PrefetchPlanner, PrefetchRequest
from core.runtime_profile import MachineResources, resolve_profile
from core.runtime_regulator import (
    OnDemandGate,
    bump_divisor,
    compute_policy,
    limit_prefetch_requests,
    profile_base_steps,
)
from core.studio_runtime import StudioRuntime


def test_profile_base_steps_follow_the_bench_ladder():
    assert profile_base_steps("high") == 0
    assert profile_base_steps("balanced") == 1
    assert profile_base_steps("low") == 2


def test_high_profile_on_ac_keeps_full_prefetch_and_full_neighborhood():
    profile = resolve_profile("high", MachineResources(logical_cpus=8, memory_bytes=32 * 1024**3))
    policy = compute_policy(profile, power=PowerState(on_battery=False, thermal="nominal", source="test"))
    assert policy.steps == 0
    assert policy.neighborhood_half is False
    assert policy.neighborhood_scale == 1.0
    assert policy.prefetch_enabled is True
    assert policy.prefetch_max_extra == 3
    assert policy.preview_divisor == 1


def test_balanced_enables_neighborhood_half():
    profile = resolve_profile("balanced", MachineResources(logical_cpus=4, memory_bytes=8 * 1024**3))
    policy = compute_policy(profile, power=PowerState(on_battery=False, source="test"))
    assert policy.steps >= 1
    assert policy.neighborhood_half is True
    assert policy.neighborhood_scale == 0.5
    assert policy.prefetch_max_extra == 3


def test_battery_suspends_prefetch_and_bumps_auto_divisor():
    profile = resolve_profile("high", MachineResources(logical_cpus=8, memory_bytes=32 * 1024**3))
    policy = compute_policy(
        profile,
        power=PowerState(on_battery=True, thermal=THERMAL_SERIOUS, source="test"),
        preview_divisor=profile.preview_divisor,
    )
    assert policy.prefetch_enabled is False
    assert policy.prefetch_max_extra == 0
    assert policy.preview_divisor == bump_divisor(1, 1) == 2
    assert policy.steps >= 1


def test_energy_saver_without_battery_tightens_prefetch_radius():
    profile = resolve_profile("low", MachineResources(logical_cpus=2, memory_bytes=4 * 1024**3))
    on_battery = compute_policy(
        profile,
        power=PowerState(on_battery=True, source="test"),
        preview_divisor=profile.preview_divisor,
    )
    assert on_battery.prefetch_enabled is False
    plugged = compute_policy(profile, power=PowerState(on_battery=False, source="test"))
    assert plugged.steps == 2
    assert plugged.prefetch_max_extra == 3
    # low (2) + économie (+1) → steps 3 → un seul voisin
    hot = compute_policy(
        profile,
        power=PowerState(on_battery=False, low_power_mode=True, source="test"),
        preview_divisor=4,
    )
    assert hot.steps == 3
    assert hot.prefetch_enabled is True
    assert hot.prefetch_max_extra == 1
    assert hot.preview_divisor == 8


def test_limit_prefetch_keeps_current_then_extras():
    requests = [
        PrefetchRequest(5, 0, "current"),
        PrefetchRequest(6, 10, "ahead"),
        PrefetchRequest(4, 20, "behind"),
        PrefetchRequest(7, 30, "ahead"),
    ]
    assert [r.index for r in limit_prefetch_requests(requests, max_extra=0)] == [5]
    assert [r.index for r in limit_prefetch_requests(requests, max_extra=1)] == [5, 6]
    assert [r.index for r in limit_prefetch_requests(requests, max_extra=3)] == [5, 6, 4, 7]


def test_ondemand_gate_skips_identical_fingerprint():
    gate = OnDemandGate()
    assert gate.should_run(("a", 1)) is True
    assert gate.should_run(("a", 1)) is False
    assert gate.should_run(("a", 2)) is True
    gate.invalidate()
    assert gate.should_run(("a", 2)) is True


def test_studio_runtime_applies_injected_power_to_auto_baseline(monkeypatch):
    monkeypatch.setenv("KUT_STUDIO_MACHINE_BENCH", "off")
    monkeypatch.delenv("KUT_STUDIO_POWER", raising=False)  # l'état injecté ne doit pas être écrasé par le forçage
    runtime = StudioRuntime(
        profile="auto",
        preview_quality="auto",
        resources=MachineResources(logical_cpus=8, memory_bytes=32 * 1024**3),
    )
    # Sans banc mesuré : heuristique → high sur 8 cœurs / 32 Go.
    assert runtime.resolved_profile().name == "high"
    runtime.refresh_power_state(PowerState(on_battery=True, source="test"))
    assert runtime.prefetch_allowed() is False
    assert runtime.preview.baseline == 2
    assert runtime.neighborhood_scale() == 0.5


def test_preview_divisor_refreshes_power_before_returning(monkeypatch):
    """Le diviseur publié suit le cran énergie dès le premier accès."""
    monkeypatch.setenv("KUT_STUDIO_MACHINE_BENCH", "off")
    monkeypatch.setenv("KUT_STUDIO_POWER", "battery")
    runtime = StudioRuntime(
        profile="auto",
        preview_quality="auto",
        resources=MachineResources(logical_cpus=8, memory_bytes=32 * 1024**3),
    )
    # Avant tout neighborhood_scale : le diviseur doit déjà être abaissé.
    assert runtime.preview_divisor() == 2
    assert runtime.neighborhood_scale() == 0.5


def test_unplugging_restores_energy_baseline_not_stuck_half(monkeypatch):
    monkeypatch.setenv("KUT_STUDIO_MACHINE_BENCH", "off")
    runtime = StudioRuntime(
        profile="auto",
        preview_quality="auto",
        resources=MachineResources(logical_cpus=8, memory_bytes=32 * 1024**3),
    )
    runtime.refresh_power_state(PowerState(on_battery=True, source="test"))
    assert runtime.preview.baseline == 2
    assert runtime.preview.divisor == 2
    runtime.refresh_power_state(PowerState(on_battery=False, thermal="nominal", source="test"))
    assert runtime.preview.baseline == 1
    assert runtime.preview.divisor == 1


def test_adaptive_overload_survives_unplug(monkeypatch):
    monkeypatch.setenv("KUT_STUDIO_MACHINE_BENCH", "off")
    runtime = StudioRuntime(
        profile="auto",
        preview_quality="auto",
        resources=MachineResources(logical_cpus=8, memory_bytes=32 * 1024**3),
    )
    runtime.refresh_power_state(PowerState(on_battery=True, source="test"))
    # Surcharge adaptative sous la base batterie (2 → 4).
    runtime.preview.divisor = 4
    runtime.preview.adaptive.divisor = 4
    runtime.refresh_power_state(PowerState(on_battery=False, thermal="nominal", source="test"))
    assert runtime.preview.baseline == 1
    assert runtime.preview.divisor == 4


def test_explicit_quality_is_not_lowered_by_battery(monkeypatch):
    monkeypatch.delenv("KUT_STUDIO_POWER", raising=False)
    runtime = StudioRuntime(
        profile="high",
        preview_quality="full",
        resources=MachineResources(logical_cpus=8, memory_bytes=32 * 1024**3),
    )
    runtime.refresh_power_state(PowerState(on_battery=True, source="test"))
    assert runtime.preview.baseline == 1
    assert runtime.preview_divisor() == 1
    assert runtime.prefetch_allowed() is False


def test_prefetch_planner_still_plans_neighbors_for_limit_helper():
    planner = PrefetchPlanner(segment_seconds=2.0)
    # À t=0,5 s le « behind » sort de la grille → courant + 2 ahead.
    planned = planner.plan(0.5, duration=20.0, velocity=0.0)
    assert len(planned) == 3
    assert len(limit_prefetch_requests(planned, max_extra=0)) == 1
    assert len(limit_prefetch_requests(planned, max_extra=1)) == 2


# --- Relevé d'énergie hors du thread d'interface ----------------------------------------------------------------------


def _wait_until(predicate, timeout=3.0):
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


def _unforced_runtime(monkeypatch):
    monkeypatch.setenv("KUT_STUDIO_MACHINE_BENCH", "off")
    monkeypatch.delenv("KUT_STUDIO_POWER", raising=False)
    return StudioRuntime(
        profile="auto", preview_quality="auto",
        resources=MachineResources(logical_cpus=8, memory_bytes=32 * 1024**3),
    )


def test_the_power_reading_never_blocks_the_caller_and_runs_on_a_worker_thread(monkeypatch):
    """``preview_divisor()`` est appelé par le thread d'interface à chaque déplacement de la tête : un ``pmset`` lent
    (3 × 2 s au plus) ne doit jamais le bloquer. Le relevé tourne sur un thread de travail, un seul à la fois."""
    import threading

    runtime = _unforced_runtime(monkeypatch)
    release = threading.Event()
    calls: list[str] = []

    def slow_detect():
        calls.append(threading.current_thread().name)
        release.wait(5.0)
        return PowerState(on_battery=True, source="test")

    monkeypatch.setattr("core.studio_runtime.detect_power_state", slow_detect)
    before = runtime.preview_divisor()
    for _ in range(5):
        assert runtime.preview_divisor() == before, "tant que le relevé n'a pas abouti, l'état connu ne change pas"
    assert _wait_until(lambda: calls), "le relevé a démarré"
    assert calls == ["kut-power-probe"], "un seul relevé à la fois, jamais sur le thread appelant"
    assert threading.current_thread().name != calls[0]

    release.set()
    assert _wait_until(lambda: not runtime._power_probe_running)
    runtime.preview_divisor()                              # le résultat est appliqué par l'appelant
    assert runtime.power_state().on_battery is True
    assert runtime.prefetch_allowed() is False and runtime.preview_divisor() > before


def test_a_fresh_reading_is_kept_for_the_ttl_then_read_again(monkeypatch):
    runtime = _unforced_runtime(monkeypatch)
    readings: list[int] = []

    def detect():
        readings.append(1)
        return PowerState(on_battery=False, thermal="nominal", source="test")

    monkeypatch.setattr("core.studio_runtime.detect_power_state", detect)
    runtime.preview_divisor()
    assert _wait_until(lambda: len(readings) == 1 and not runtime._power_probe_running)
    for _ in range(10):
        runtime.preview_divisor()
    assert len(readings) == 1, "dans les 15 s, pas de nouvelle lecture"
    runtime._power_checked_at -= 60.0                      # le temps passe
    runtime.preview_divisor()
    assert _wait_until(lambda: len(readings) == 2)


def test_an_unreadable_power_state_degrades_to_unavailable_without_raising(monkeypatch):
    runtime = _unforced_runtime(monkeypatch)

    def broken():
        raise OSError("pmset a disparu")

    monkeypatch.setattr("core.studio_runtime.detect_power_state", broken)
    runtime.preview_divisor()
    assert _wait_until(lambda: not runtime._power_probe_running and runtime._power_probe_result is not None)
    runtime.preview_divisor()
    assert runtime.power_state().source == "unavailable"
    assert runtime.prefetch_allowed() is True, "sans information, pas de cran batterie"


def test_a_forced_power_state_is_applied_at_once_without_any_thread(monkeypatch):
    runtime = _unforced_runtime(monkeypatch)
    monkeypatch.setenv("KUT_STUDIO_POWER", "battery")

    def never(*_args, **_kwargs):
        raise AssertionError("un forçage ne lance aucune lecture réelle")

    monkeypatch.setattr("core.studio_runtime.detect_power_state", never)
    runtime.preview_divisor()
    assert runtime.power_state().on_battery is True and runtime._power_probe_running is False
    monkeypatch.setenv("KUT_STUDIO_POWER", "ac")
    runtime.preview_divisor()
    assert runtime.power_state().on_battery is False, "le forçage suit l'environnement"
