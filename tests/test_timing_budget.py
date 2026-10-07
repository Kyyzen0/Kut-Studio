"""Budgets de temps partagés des tests de performance (``tests/timing_budget.py``)."""

from __future__ import annotations

import sys

import pytest

import timing_budget


@pytest.mark.parametrize(
    ("ci", "covered", "expected"),
    [(False, False, 0.25), (True, False, 0.6), (False, True, 1.25), (True, True, 3.0)],
)
def test_the_budget_is_nominal_unless_on_ci_or_under_coverage(
    monkeypatch: pytest.MonkeyPatch, ci: bool, covered: bool, expected: float
) -> None:
    monkeypatch.setattr(timing_budget, "on_ci", lambda: ci)
    monkeypatch.setattr(timing_budget, "under_coverage", lambda: covered)
    assert timing_budget.budget(0.25) == pytest.approx(expected)
    assert timing_budget.budget(0.5, ci_factor=3.0, coverage_factor=1.0) == pytest.approx(1.5 if ci else 0.5)


def test_ci_is_detected_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CI", "true")
    assert timing_budget.on_ci()
    monkeypatch.delenv("CI")
    assert not timing_budget.on_ci()


def test_coverage_is_detected_only_when_it_measures_this_process() -> None:
    coverage = sys.modules.get("coverage")
    measuring = coverage is not None and coverage.Coverage.current() is not None
    assert timing_budget.under_coverage() is measuring


def test_coverage_imported_but_idle_does_not_count(monkeypatch: pytest.MonkeyPatch) -> None:
    class IdleCoverage:
        @staticmethod
        def current() -> None:
            return None

    monkeypatch.setitem(sys.modules, "coverage", type(sys)("coverage"))
    monkeypatch.setattr(sys.modules["coverage"], "Coverage", IdleCoverage, raising=False)
    assert not timing_budget.under_coverage()


def test_best_cpu_seconds_keeps_the_fastest_run() -> None:
    calls: list[int] = []
    elapsed = timing_budget.best_cpu_seconds(lambda: calls.append(sum(range(10_000))), repeats=4)
    assert len(calls) == 4
    assert 0 <= elapsed < 1
