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


def test_explicit_quality_is_not_lowered_by_battery():
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
