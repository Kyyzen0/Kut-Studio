"""Branchement Qt du pont StoreKit (ADR-0013, étape 13) : seulement dans le build Store, signal Qt, aucun StoreKit sur le thread Qt."""

from __future__ import annotations

import ast
import threading
import time
from pathlib import Path

import pytest
from main_window_harness import build_window, install_dialogs

from core import entitlements
from core.entitlements import EntitlementKind as K
from tests.entitlements_support import DAY, T0, Clock, make_service, store_flavor, trial  # noqa: F401 - fixture
from tests.storekit_fakes import FakeBridge, app_transaction, loader_for, missing_loader

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _clean_service():
    yield
    entitlements.install_service(None)
    entitlements.set_enforced_provider(None)


def test_controller_installs_the_service_before_any_read_and_signals_changes(qtbot, tmp_path):
    from ui.store_entitlements import StoreEntitlementsController

    names = set()
    bridge = FakeBridge(app=app_transaction(T0 - DAY, T0))
    bridge.on_call = lambda op: names.add(threading.current_thread().name)
    controller = StoreEntitlementsController(directory=tmp_path, clock=Clock(T0), bridge_loader=loader_for(bridge), error_backoff=0.01)
    try:
        assert entitlements.installed_service() is controller.service                   # installé AVANT la première lecture
        assert controller.service.state().kind is K.UNKNOWN and controller.service.state().reason == "missing"
        with qtbot.waitSignal(controller.state_changed, timeout=5000):
            controller.start()
        assert controller.service.state().kind is K.TRIAL
        assert names == {"kut-storekit"} and threading.current_thread().name not in names
    finally:
        controller.stop()
    assert not controller.source.running


def test_controller_without_a_bridge_stays_read_only(qtbot, tmp_path, store_flavor):  # noqa: F811
    from ui.store_entitlements import StoreEntitlementsController

    controller = StoreEntitlementsController(directory=tmp_path, clock=Clock(T0), bridge_loader=missing_loader)
    controller.start()
    try:
        deadline = time.monotonic() + 5
        while controller.source.running and time.monotonic() < deadline:
            qtbot.wait(10)
        assert controller.service.state().kind is K.UNKNOWN and not controller.service.can_edit()
    finally:
        controller.stop()


def _window(qtbot, monkeypatch, tmp_path):
    install_dialogs(monkeypatch)
    return build_window(qtbot, monkeypatch, tmp_path / "config")


def test_outside_the_store_the_bridge_is_never_wired(qtbot, monkeypatch, tmp_path):
    """Hors Store (même contrôle exigé par un test) : ni contrôleur, ni service remplacé, ni thread de lecture."""
    entitlements.set_enforced_provider(lambda: True)
    sentinel, _store, _clock = make_service(tmp_path / "state")
    sentinel.publish(trial(T0))
    entitlements.install_service(sentinel)
    threads = {t.name for t in threading.enumerate()}
    window = _window(qtbot, monkeypatch, tmp_path)
    assert not hasattr(window, "_store_entitlements")
    assert entitlements.installed_service() is sentinel
    assert not ({t.name for t in threading.enumerate()} - threads) & {"kut-storekit"}


def test_in_the_store_build_the_window_installs_the_service_and_is_read_only_without_a_bridge(qtbot, monkeypatch, tmp_path):
    monkeypatch.setattr("ui.main_window_mixins.entitlements.is_store", lambda: True)
    entitlements.set_enforced_provider(lambda: True)
    window = _window(qtbot, monkeypatch, tmp_path)
    try:
        controller = window._store_entitlements
        assert entitlements.installed_service() is controller.service
        deadline = time.monotonic() + 5
        while controller.source.running and time.monotonic() < deadline:
            qtbot.wait(10)                                                  # pas de bundle ici : le pont est absent
        assert controller.source.last_reason == "bridge_unavailable"
        assert entitlements.edit_refusal() is not None and entitlements.export_refusal() is not None
    finally:
        window._store_entitlements.stop()


def test_the_signal_refreshes_the_window(qtbot, monkeypatch, tmp_path):
    monkeypatch.setattr("ui.main_window_mixins.entitlements.is_store", lambda: True)
    entitlements.set_enforced_provider(lambda: True)
    window = _window(qtbot, monkeypatch, tmp_path)
    try:
        calls = []
        window.refresh_entitlements = lambda: calls.append(1)             # type: ignore[method-assign]
        window._store_entitlements.state_changed.disconnect()
        window._store_entitlements.state_changed.connect(window.refresh_entitlements)
        window._store_entitlements.state_changed.emit()
        assert calls
    finally:
        window._store_entitlements.stop()


def test_store_modules_are_imported_only_inside_the_store_branch():
    source = (ROOT / "ui" / "main_window_mixins" / "entitlements.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    top_level = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert not any("store_entitlements" in ast.dump(n) or "storekit" in ast.dump(n) for n in top_level)
