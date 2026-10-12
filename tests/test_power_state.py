"""État énergie / chauffe : parse, politique, détection injectable."""

from __future__ import annotations

from core.power_state import (
    THERMAL_CRITICAL,
    THERMAL_NOMINAL,
    THERMAL_SERIOUS,
    PowerState,
    parse_power_override,
    power_requires_step_down,
    prefetch_suspended,
)


def test_override_battery_and_thermal():
    state = parse_power_override("battery,thermal=serious")
    assert state is not None
    assert state.on_battery is True
    assert state.thermal == THERMAL_SERIOUS
    assert power_requires_step_down(state)
    assert prefetch_suspended(state)


def test_override_ac_does_not_step_down_alone():
    state = parse_power_override("ac,thermal=nominal")
    assert state is not None
    assert state.on_battery is False
    assert state.thermal == THERMAL_NOMINAL
    assert not power_requires_step_down(state)
    assert not prefetch_suspended(state)


def test_low_power_or_critical_thermal_steps_down():
    assert power_requires_step_down(PowerState(low_power_mode=True, thermal=THERMAL_NOMINAL))
    assert power_requires_step_down(PowerState(thermal=THERMAL_CRITICAL))
    assert not power_requires_step_down(PowerState(source="unavailable"))


def test_override_off_is_ignored():
    assert parse_power_override("off") is None
    assert parse_power_override("") is None
