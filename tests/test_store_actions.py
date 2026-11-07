"""Produits, achat et restauration avec un FAUX pont (ADR-0013, étape 15) : un achat ne débloque rien sans relecture vérifiée."""

from __future__ import annotations

import threading
import time

import pytest

from core.entitlements import EntitlementKind as K
from core.entitlements import EntitlementState, storekit_bridge as sb
from core.entitlements.paywall import (
    BANNER_LAST_DAYS, TRIGGER_EXPORT, TRIGGER_MANUAL, TRIGGER_REFUSED, TRIGGER_STARTUP, banner_days, may_show_paywall,
)
from core.entitlements.storekit_actions import (
    MAX_PRICE_CHARS, ActionKind, ActionStatus, offers, parse_products,
)
from core.entitlements.storekit_source import StoreKitEntitlementSource
from core.entitlements.storekit_verifier import ProductIds, StoreKitVerifier
from tests.entitlements_support import DAY, T0, Clock, make_service
from tests.storekit_fakes import (
    PRODUCT_LIFETIME, PRODUCT_MONTHLY, PRODUCT_YEARLY, FakeBridge, app_transaction, entitlements, failure, item, loader_for,
    missing_loader, products, purchase,
)

EXPIRED_TRIAL = app_transaction(T0 - 30 * DAY, T0)           # essai commencé il y a 30 jours : fini
PAID = entitlements(item(PRODUCT_MONTHLY, signed=T0, expires=T0 + 30 * DAY))


def wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


@pytest.fixture
def rig(tmp_path):
    created = []

    def build(bridge=None, loader=None, wait_state=K.EXPIRED):
        bridge = bridge or FakeBridge(app=EXPIRED_TRIAL)
        clock = Clock(T0)
        service, _store, _c = make_service(tmp_path, clock=clock)
        verifier = StoreKitVerifier(tmp_path / "key", clock)
        service._store.verifier = verifier
        results = []
        source = StoreKitEntitlementSource(service, verifier, loader or loader_for(bridge), on_action=results.append, error_backoff=0.01)
        created.append(source)
        source.start()
        if wait_state is not None:
            assert wait_for(lambda: service.state().kind is wait_state)
        return source, service, bridge, results

    yield build
    for source in created:
        source.stop()


def finish(source):
    for thread in list(source.actions.threads):
        thread.join(5)


# -- catalogue et prix --------------------------------------------------------------------------------------------------------


def test_the_catalogue_is_one_constant_with_the_proposed_identifiers():
    assert [o.product_id for o in offers()] == ["studio.monthly", "studio.yearly", "studio.lifetime"]
    assert [o.recurring for o in offers()] == [True, True, False]
    custom = ProductIds(monthly="m", yearly="y", lifetime="l")
    assert [o.product_id for o in offers(custom)] == ["m", "y", "l"] and custom.subscriptions == {"m", "y"}


def test_prices_come_from_storekit_only_and_are_cleaned():
    payload = products({PRODUCT_MONTHLY: "6,99 €", PRODUCT_YEARLY: "49,99 €", "intrus": "0 €", PRODUCT_LIFETIME: "x" * 500}).payload
    found = parse_products(payload)
    assert set(found) == {PRODUCT_MONTHLY, PRODUCT_YEARLY, PRODUCT_LIFETIME}                # le produit inconnu est ignoré
    assert len(found[PRODUCT_LIFETIME].display_price) == MAX_PRICE_CHARS
    messy = {"products": [{"id": PRODUCT_MONTHLY, "displayPrice": "5\x00\x1b €"}, {"id": PRODUCT_YEARLY, "displayPrice": 12}, "x", None]}
    assert parse_products(messy)[PRODUCT_MONTHLY].display_price == "5 €" and PRODUCT_YEARLY not in parse_products(messy)
    assert parse_products({"products": "non"}) == {} and parse_products({}) == {}


def test_load_products_runs_on_the_action_thread_and_returns_localized_prices(rig):
    source, _service, bridge, results = rig()
    bridge.results[sb.OP_PRODUCTS] = products({PRODUCT_MONTHLY: "6,99 €", PRODUCT_LIFETIME: "99,99 €"})
    names = set()
    bridge.on_call = lambda op: names.add(threading.current_thread().name) if op == sb.OP_PRODUCTS else None
    assert source.actions.request_products()
    assert wait_for(lambda: any(r.kind is ActionKind.PRODUCTS for r in results))
    result = next(r for r in results if r.kind is ActionKind.PRODUCTS)
    assert result.status is ActionStatus.DONE and result.products[PRODUCT_MONTHLY].display_price == "6,99 €"
    assert names == {"kut-storekit-action"}
    assert (sb.OP_PRODUCTS, "studio.monthly,studio.yearly,studio.lifetime", 20000) in bridge.calls


def test_no_products_means_unavailable_never_a_made_up_price(rig):
    source, _service, bridge, results = rig()
    bridge.results[sb.OP_PRODUCTS] = failure(sb.STOREKIT_ERROR)
    source.actions.request_products()
    assert wait_for(lambda: results)
    assert results[-1].status is ActionStatus.UNAVAILABLE and results[-1].products == {}


# -- achat : un signal, jamais un déblocage -----------------------------------------------------------------------------------


def test_a_verified_purchase_unlocks_only_through_the_reread_state(rig):
    source, service, bridge, results = rig()
    assert not service.can_edit()
    names = set()

    def on_call(op):
        names.add(threading.current_thread().name)
        if op == sb.OP_PURCHASE:
            bridge.results[sb.OP_ENTITLEMENTS] = PAID                  # StoreKit a enregistré l'achat

    bridge.on_call = on_call
    bridge.results[sb.OP_PURCHASE] = purchase("success", PRODUCT_MONTHLY)
    source.actions.request_purchase(PRODUCT_MONTHLY)
    assert wait_for(lambda: any(r.kind is ActionKind.PURCHASE for r in results))
    result = next(r for r in results if r.kind is ActionKind.PURCHASE)
    assert result.status is ActionStatus.DONE and result.product_id == PRODUCT_MONTHLY
    assert service.state().kind is K.SUBSCRIBER and service.can_edit() and service.can_export()
    assert (sb.OP_PURCHASE, PRODUCT_MONTHLY, 120000) in bridge.calls
    ops = [c[0] for c in bridge.calls]
    assert ops.index(sb.OP_PURCHASE) < len(ops) - 1 and sb.OP_ENTITLEMENTS in ops[ops.index(sb.OP_PURCHASE):]      # relecture APRÈS l'achat
    assert "kut-storekit-action" in names and threading.current_thread().name not in names


def test_a_success_the_reread_cannot_confirm_unlocks_nothing(rig):
    """Le pont dit « succès » mais la relecture ne trouve aucun droit vérifié : l'état ne change pas (c'est la preuve par mutation)."""
    source, service, bridge, results = rig()
    bridge.results[sb.OP_PURCHASE] = purchase("success", PRODUCT_MONTHLY)          # entitlements reste vide
    source.actions.request_purchase(PRODUCT_MONTHLY)
    assert wait_for(lambda: any(r.kind is ActionKind.PURCHASE for r in results))
    assert results[-1].status is ActionStatus.NOT_CONFIRMED
    assert service.state().kind is K.EXPIRED and not service.can_edit() and not service.can_export()


def test_an_unverified_or_mismatched_success_is_not_reread_and_not_trusted(rig):
    source, service, bridge, results = rig()
    for index, reply in enumerate((purchase("success", PRODUCT_MONTHLY, verified=False), purchase("success", PRODUCT_YEARLY),
                                   purchase("unverified", PRODUCT_MONTHLY))):
        bridge.results[sb.OP_PURCHASE] = reply
        before = sum(1 for c in bridge.calls if c[0] == sb.OP_ENTITLEMENTS)
        bridge.results[sb.OP_ENTITLEMENTS] = PAID                          # même si un droit existait : on n'y touche pas
        source.actions.request_purchase(PRODUCT_MONTHLY)
        assert wait_for(lambda n=index + 1: sum(r.kind is ActionKind.PURCHASE for r in results) >= n)
        assert results[-1].status is ActionStatus.NOT_CONFIRMED
        assert sum(1 for c in bridge.calls if c[0] == sb.OP_ENTITLEMENTS) == before          # aucune relecture déclenchée par un succès douteux
        assert service.state().kind is K.EXPIRED and not service.can_edit()
        bridge.results[sb.OP_ENTITLEMENTS] = entitlements()


@pytest.mark.parametrize(("reply", "expected"), [
    (purchase("cancelled"), ActionStatus.CANCELLED),
    (purchase("pending"), ActionStatus.PENDING),
    (purchase("autre"), ActionStatus.FAILED),
    (failure(sb.STOREKIT_ERROR), ActionStatus.FAILED),
    (failure(sb.BAD_ARGUMENT, "unknown_op"), ActionStatus.FAILED),
    (failure(sb.TIMEOUT, "timeout"), ActionStatus.PENDING),
    (failure(sb.SHUTDOWN, "shutdown"), ActionStatus.UNAVAILABLE),
])
def test_every_other_purchase_outcome_leaves_the_state_unchanged(rig, reply, expected):
    source, service, bridge, results = rig()
    bridge.results[sb.OP_PURCHASE] = reply
    source.actions.request_purchase(PRODUCT_MONTHLY)
    assert wait_for(lambda: any(r.kind is ActionKind.PURCHASE for r in results))
    assert results[-1].status is expected
    assert service.state().kind is K.EXPIRED and not service.can_edit()


def test_an_unknown_product_never_reaches_the_bridge(rig):
    source, _service, bridge, results = rig()
    source.actions.request_purchase("com.evil.pro")
    source.actions.request_purchase("studio.monthly,studio.yearly")
    assert wait_for(lambda: sum(r.kind is ActionKind.PURCHASE for r in results) == 2)
    assert all(r.status is ActionStatus.FAILED for r in results) and not [c for c in bridge.calls if c[0] == sb.OP_PURCHASE]


def test_without_a_bridge_every_action_is_unavailable(rig):
    source, service, _bridge, results = rig(loader=missing_loader, wait_state=None)
    assert wait_for(lambda: not source.running)
    source.actions.request_purchase(PRODUCT_MONTHLY)
    assert wait_for(lambda: len(results) == 1)
    source.actions.request_restore()
    assert wait_for(lambda: len(results) == 2)
    assert [r.status for r in results] == [ActionStatus.UNAVAILABLE] * 2 and not service.can_edit()


def test_one_action_at_a_time(rig):
    source, _service, bridge, results = rig()
    gate = threading.Event()
    bridge.on_call = lambda op: gate.wait(5) if op == sb.OP_PURCHASE else None
    bridge.results[sb.OP_PURCHASE] = purchase("cancelled")
    assert source.actions.request_purchase(PRODUCT_MONTHLY)
    assert wait_for(lambda: any(c[0] == sb.OP_PURCHASE for c in bridge.calls))
    assert not source.actions.request_purchase(PRODUCT_YEARLY) and not source.actions.request_restore()
    busy = [r for r in results if r.status is ActionStatus.BUSY]
    assert len(busy) == 2 and sum(1 for c in bridge.calls if c[0] == sb.OP_PURCHASE) == 1
    source.actions.request_products()                          # le chargement des prix ne gêne jamais un achat
    gate.set()
    finish(source)
    assert wait_for(lambda: any(r.status is ActionStatus.CANCELLED for r in results))


# -- restauration -------------------------------------------------------------------------------------------------------------


def test_restore_syncs_then_rereads_and_reports_honestly(rig):
    source, service, bridge, results = rig()
    source.actions.request_restore()
    assert wait_for(lambda: any(r.kind is ActionKind.RESTORE for r in results))
    assert results[-1].status is ActionStatus.NOTHING_TO_RESTORE and not service.can_edit()
    assert any(c[0] == sb.OP_SYNC for c in bridge.calls)
    bridge.results[sb.OP_ENTITLEMENTS] = entitlements(item(PRODUCT_LIFETIME, signed=T0))
    source.actions.request_restore()
    assert wait_for(lambda: sum(r.kind is ActionKind.RESTORE for r in results) == 2)
    assert results[-1].status is ActionStatus.DONE and service.state().kind is K.LIFETIME and service.can_edit()


def test_restore_failure_changes_nothing(rig):
    source, service, bridge, results = rig()
    bridge.results[sb.OP_SYNC] = failure(sb.STOREKIT_ERROR)
    source.actions.request_restore()
    assert wait_for(lambda: any(r.kind is ActionKind.RESTORE for r in results))
    assert results[-1].status is ActionStatus.FAILED and service.state().kind is K.EXPIRED


# -- le mur : quand il peut s'ouvrir ------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("state", "expected"), [
    (EntitlementState(K.EXPIRED, reason="trial_ended"), True),
    (EntitlementState(K.TRIAL, days_left=2), False),
    (EntitlementState(K.TRIAL, days_left=14), False),
    (EntitlementState(K.SUBSCRIBER, expires_at=1.0), False),
    (EntitlementState(K.LIFETIME), False),
    (EntitlementState(K.UNKNOWN, reason="missing"), False),
])
def test_the_wall_is_only_offered_after_the_trial_and_never_mid_edit(state, expected):
    for trigger in (TRIGGER_STARTUP, TRIGGER_EXPORT, TRIGGER_REFUSED):
        assert may_show_paywall(state, trigger) is expected
        assert may_show_paywall(state, trigger, mid_edit=True) is False
    assert may_show_paywall(state, TRIGGER_MANUAL, mid_edit=True) is True            # l'utilisateur l'ouvre lui-même
    assert may_show_paywall(state, "inconnu") is False


def test_the_banner_shows_the_last_three_trial_days_only():
    assert [banner_days(EntitlementState(K.TRIAL, days_left=d)) for d in (14, 4, 3, 2, 1)] == [None, None, 3, 2, 1]
    assert BANNER_LAST_DAYS == 3
    for state in (EntitlementState(K.EXPIRED), EntitlementState(K.UNKNOWN), EntitlementState(K.LIFETIME), EntitlementState(K.TRIAL)):
        assert banner_days(state) is None
