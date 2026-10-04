"""Contrôle de l'application construite pour le flux optique : il passe, et il détecte vraiment une panne."""

from __future__ import annotations

import builtins

import core.optical_flow as optical_flow


def test_the_self_check_passes_and_is_fast():
    import time

    started = time.perf_counter()
    assert optical_flow.self_check() is None
    assert time.perf_counter() - started < 5.0                            # un contrôle de démarrage, pas un banc


def test_a_missing_numpy_is_reported_not_raised(monkeypatch):
    real_import = builtins.__import__

    def refuse(name, *args, **kwargs):
        if name == "numpy" or name.startswith("numpy."):
            raise ImportError("module absent de l'empaquetage")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse)
    problem = optical_flow.self_check()
    assert problem is not None and "indisponible" in problem


def test_a_broken_estimator_is_caught(monkeypatch):
    import core.flow_numpy as flow_numpy

    def broken(a, b, params, cancel=None):
        zero = flow_numpy.FlowField(a * 0, a * 0, a * 0 + 1)
        return zero, zero

    monkeypatch.setattr(flow_numpy, "estimate_pair", broken)
    problem = optical_flow.self_check()
    assert problem is not None and "faussé" in problem


def test_the_smoke_test_runs_the_check():
    import pathlib

    source = (pathlib.Path(__file__).resolve().parent.parent / "main.py").read_text(encoding="utf-8")
    assert "optical_flow import self_check" in source and "flux optique indisponible" in source
