"""La source StoreKit (thread de travail) avec un FAUX pont : publication, mises à jour sondées, échecs, arrêt (ADR-0013, étape 13)."""

from __future__ import annotations

import threading
import time

import pytest

from core.entitlements import EntitlementKind as K
from core.entitlements import storekit_bridge as sb
from core.entitlements.storekit_source import REASON_BRIDGE_UNAVAILABLE, StoreKitEntitlementSource
from core.entitlements.storekit_verifier import StoreKitVerifier
from tests.entitlements_support import DAY, T0, Clock, make_service
from tests.storekit_fakes import (
    PRODUCT_LIFETIME, PRODUCT_MONTHLY, FakeBridge, app_transaction, entitlements, failure, item, loader_for, missing_loader,
)


def wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


@pytest.fixture
def make_source(tmp_path):
    created = []

    def build(loader, clock=None, **options):
        clock = clock or Clock(T0)
        service, _store, _c = make_service(tmp_path, clock=clock)
        verifier = StoreKitVerifier(tmp_path / "key", clock)
        # Même vérificateur pour l'enveloppe et la lecture : celui de la production.
        service._store.verifier = verifier
        changes = []
        options.setdefault("error_backoff", 0.01)
        source = StoreKitEntitlementSource(service, verifier, loader, on_change=lambda: changes.append(1), **options)
        created.append(source)
        return source, service, changes

    yield build
    for source in created:
        source.stop()


def test_start_reads_storekit_and_publishes_the_trial(make_source):
    bridge = FakeBridge(app=app_transaction(T0 - 2 * DAY, T0))
    source, service, changes = make_source(loader_for(bridge))
    assert service.state().kind is K.UNKNOWN                      # avant la première lecture : lecture seule
    source.start()
    assert wait_for(lambda: service.state().kind is K.TRIAL)
    assert service.state().days_left == 12 and changes
    assert (sb.OP_START_UPDATES, "", 15000) in bridge.calls       # l'écoute des mises à jour est démarrée


def test_reads_happen_on_the_worker_thread_only(make_source):
    threads = set()
    bridge = FakeBridge(app=app_transaction(T0 - DAY, T0))
    bridge.on_call = lambda op: threads.add(threading.current_thread().name)
    source, service, _c = make_source(loader_for(bridge))
    source.start()
    assert wait_for(lambda: service.state().kind is K.TRIAL)
    assert threads == {"kut-storekit"} and threading.current_thread().name not in threads


def test_a_purchase_seen_through_the_updates_is_reread_and_notified(make_source):
    bridge = FakeBridge(app=app_transaction(T0 - DAY, T0))
    source, service, changes = make_source(loader_for(bridge))
    source.start()
    assert wait_for(lambda: service.state().kind is K.TRIAL)
    seen = len(changes)
    bridge.results[sb.OP_ENTITLEMENTS] = entitlements(item(PRODUCT_LIFETIME, signed=T0 + 60))
    bridge.push({"verified": True, "transaction": {"productID": PRODUCT_LIFETIME}})
    assert wait_for(lambda: service.state().kind is K.LIFETIME)
    assert wait_for(lambda: len(changes) > seen)


def test_a_refund_seen_through_the_updates_goes_back_to_the_trial_or_expired(make_source):
    bridge = FakeBridge(ent=entitlements(item(PRODUCT_MONTHLY, signed=T0, expires=T0 + 20 * DAY)), app=app_transaction(T0 - 30 * DAY, T0))
    source, service, _c = make_source(loader_for(bridge))
    source.start()
    assert wait_for(lambda: service.state().kind is K.SUBSCRIBER)
    bridge.results[sb.OP_ENTITLEMENTS] = entitlements(item(PRODUCT_MONTHLY, signed=T0 + 60, expires=T0 + 20 * DAY, revoked=T0 + 60))
    bridge.push({"verified": True})
    assert wait_for(lambda: service.state().kind is K.EXPIRED)
    assert service.can_edit() is False


def test_an_unverified_update_changes_nothing(make_source):
    bridge = FakeBridge(app=app_transaction(T0 - DAY, T0))
    source, service, _c = make_source(loader_for(bridge))
    source.start()
    assert wait_for(lambda: service.state().kind is K.TRIAL)
    reads = sum(1 for call in bridge.calls if call[0] == sb.OP_ENTITLEMENTS)
    bridge.push({"verified": False, "productID": "x"})
    assert wait_for(lambda: bridge.polls >= 3)
    assert sum(1 for call in bridge.calls if call[0] == sb.OP_ENTITLEMENTS) == reads


def test_missing_or_invalid_bridge_is_unknown_never_unlocked(make_source):
    source, service, _c = make_source(missing_loader)
    source.start()
    assert wait_for(lambda: not source.running)
    assert source.last_reason == REASON_BRIDGE_UNAVAILABLE
    assert service.state().kind is K.UNKNOWN and service.can_edit() is False and service.can_export() is False and service.can_open()


@pytest.mark.parametrize("status", [sb.TIMEOUT, sb.STOREKIT_ERROR, sb.SHUTDOWN])
def test_storekit_failure_or_timeout_is_unknown_without_a_record(make_source, status):
    bridge = FakeBridge(ent=failure(status))
    source, service, _c = make_source(loader_for(bridge))
    source.start()
    assert wait_for(lambda: any(call[0] == sb.OP_ENTITLEMENTS for call in bridge.calls))
    assert wait_for(lambda: source.last_reason == "storekit_unavailable")
    assert service.state().kind is K.UNKNOWN and service.can_edit() is False


def test_failure_after_a_good_read_keeps_the_last_verified_state(make_source):
    bridge = FakeBridge(ent=entitlements(item(PRODUCT_MONTHLY, signed=T0, expires=T0 + 20 * DAY)))
    source, service, _c = make_source(loader_for(bridge))
    source.start()
    assert wait_for(lambda: service.state().kind is K.SUBSCRIBER)
    bridge.results[sb.OP_ENTITLEMENTS] = failure(sb.TIMEOUT, "timeout")
    bridge.push({"verified": True})
    assert wait_for(lambda: source.last_reason == "storekit_unavailable")
    assert service.state().kind is K.SUBSCRIBER                    # la règle de repli : le dernier état vérifié reste lu


def test_restore_runs_sync_then_rereads(make_source):
    bridge = FakeBridge(app=app_transaction(T0 - DAY, T0))
    source, service, _c = make_source(loader_for(bridge))
    source.start()
    assert wait_for(lambda: service.state().kind is K.TRIAL)
    bridge.results[sb.OP_ENTITLEMENTS] = entitlements(item(PRODUCT_LIFETIME, signed=T0 + 5))
    source.request_restore()
    assert wait_for(lambda: service.state().kind is K.LIFETIME)
    assert any(call[0] == sb.OP_SYNC for call in bridge.calls)


def test_periodic_refresh_without_any_event(make_source):
    now = [0.0]
    bridge = FakeBridge(app=app_transaction(T0 - DAY, T0))
    source, service, _c = make_source(loader_for(bridge), monotonic=lambda: now[0], refresh_interval=100.0)
    source.start()
    assert wait_for(lambda: service.state().kind is K.TRIAL)
    reads = lambda: sum(1 for call in bridge.calls if call[0] == sb.OP_ENTITLEMENTS)  # noqa: E731
    before = reads()
    now[0] = 101.0
    assert wait_for(lambda: reads() > before)


def test_an_error_status_on_the_poll_does_not_spin(make_source):
    bridge = FakeBridge(app=app_transaction(T0 - DAY, T0))
    polls = []
    bridge.poll_update = lambda timeout_ms=0: (polls.append(1), failure(sb.INTERNAL, "internal"))[1]     # type: ignore[method-assign]
    source, _service, _c = make_source(loader_for(bridge), error_backoff=0.05)
    source.start()
    time.sleep(0.3)
    source.stop()
    assert 1 <= len(polls) < 15                                    # ~6 avec le repli de 50 ms ; des milliers sans


def test_a_stop_requested_while_the_bridge_loads_still_shuts_it_down(make_source):
    bridge = FakeBridge(app=app_transaction(T0 - DAY, T0))
    gate = threading.Event()

    def slow_loader():
        gate.wait(5)
        return bridge

    source, service, _c = make_source(slow_loader)
    source.start()
    source.stop(timeout=0.05)                                      # le thread attend encore le chargement
    gate.set()
    assert wait_for(lambda: bridge.shutdown_count >= 1)            # arrêté dès qu'il est chargé, sans lecture
    assert wait_for(lambda: not source.running)
    assert bridge.calls == [] and service.state().kind is K.UNKNOWN


def test_an_unexpected_exception_ends_the_reader_without_unlocking(make_source):
    def boom(op):
        raise RuntimeError("secret-utilisateur@example.com")
    bridge = FakeBridge(app=app_transaction(T0 - DAY, T0))
    bridge.on_call = boom
    source, service, _c = make_source(loader_for(bridge))
    source.start()
    assert wait_for(lambda: not source.running)
    assert service.state().kind is K.UNKNOWN and service.can_edit() is False


def test_stop_is_idempotent_wakes_the_poller_and_shuts_the_bridge_down(make_source):
    bridge = FakeBridge(app=app_transaction(T0 - DAY, T0))
    source, service, _c = make_source(loader_for(bridge), poll_timeout_ms=60_000)
    source.start()
    assert wait_for(lambda: bridge.polls >= 1)
    started = time.monotonic()
    source.stop()
    source.stop()
    assert time.monotonic() - started < 2.0 and not source.running
    assert bridge.shutdown_count >= 1 and bridge._closed.is_set()


def test_stop_before_start_and_start_after_stop_do_nothing(make_source):
    bridge = FakeBridge()
    source, _service, _c = make_source(loader_for(bridge))
    source.stop()
    source.start()
    assert not source.running and bridge.calls == []


def test_start_twice_runs_one_thread(make_source):
    bridge = FakeBridge(app=app_transaction(T0 - DAY, T0))
    source, _service, _c = make_source(loader_for(bridge))
    source.start()
    first = source._thread
    source.start()
    assert source._thread is first


def test_a_failing_subscriber_does_not_stop_the_reader(tmp_path):
    clock = Clock(T0)
    service, _s, _c = make_service(tmp_path, clock=clock)
    verifier = StoreKitVerifier(tmp_path / "key", clock)
    service._store.verifier = verifier
    bridge = FakeBridge(app=app_transaction(T0 - DAY, T0))

    def bad():
        raise RuntimeError("abonné défectueux")

    source = StoreKitEntitlementSource(service, verifier, loader_for(bridge), on_change=bad)
    source.start()
    try:
        assert wait_for(lambda: service.state().kind is K.TRIAL)
        assert wait_for(lambda: bridge.polls >= 1) and source.running
    finally:
        source.stop()
