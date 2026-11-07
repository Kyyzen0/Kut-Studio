"""Le vérificateur StoreKit (ADR-0013, étape 13) avec un FAUX pont : chaque état, chaque échec, la règle de repli.

Aucun StoreKit réel, aucune bibliothèque : le pont est un objet Python (``tests/storekit_fakes.py``).
"""

from __future__ import annotations

import os

import pytest

from core.entitlements import EntitlementKind as K
from core.entitlements import storekit_bridge as sb
from core.entitlements.storekit_verifier import (
    REASON_INCOMPLETE, REASON_OK, REASON_UNAVAILABLE, REASON_UNTRUSTED_TRIAL_DATE, REASON_UNVERIFIED, StoreKitVerifier, parse_date,
)
from tests.entitlements_support import DAY, T0, Clock, make_service
from tests.storekit_fakes import (
    PRODUCT_LIFETIME, PRODUCT_MONTHLY, FakeBridge, app_transaction, entitlements, failure, item,
)

NOW = T0 + 30 * DAY


def verifier(tmp_path, clock=None) -> StoreKitVerifier:
    return StoreKitVerifier(tmp_path / "key", clock or Clock(NOW))


# -- chaque état --------------------------------------------------------------------------------------------------------------


def test_trial_dated_by_the_app_transaction(tmp_path):
    outcome = verifier(tmp_path).fetch(FakeBridge(app=app_transaction(started=NOW - 3 * DAY, signed=NOW)))
    record = outcome.record
    assert outcome.reason == REASON_OK and record is not None and record.kind is K.TRIAL
    assert record.trial_started_at == pytest.approx(NOW - 3 * DAY) and record.verified_at == pytest.approx(NOW)
    service, _s, clock = make_service(tmp_path)
    service.publish(record)
    clock.now = NOW
    assert service.state().kind is K.TRIAL and service.state().days_left == 11          # 14 jours − 3 écoulés


def test_expired_trial_is_computed_by_the_service_from_apples_date(tmp_path):
    record = verifier(tmp_path).fetch(FakeBridge(app=app_transaction(started=NOW - 20 * DAY, signed=NOW))).record
    service, _s, clock = make_service(tmp_path)
    service.publish(record)
    clock.now = NOW
    assert service.state().kind is K.EXPIRED and service.can_edit() is False and service.can_open() is True


def test_active_subscription(tmp_path):
    bridge = FakeBridge(ent=entitlements(item(PRODUCT_MONTHLY, signed=NOW, expires=NOW + 20 * DAY)))
    record = verifier(tmp_path).fetch(bridge).record
    assert record is not None and record.kind is K.SUBSCRIBER and record.expires_at == pytest.approx(NOW + 20 * DAY)
    assert (sb.OP_APP_TRANSACTION, "", 15000) not in bridge.calls                         # inutile : un droit suffit


def test_lifetime_purchase_beats_a_subscription(tmp_path):
    bridge = FakeBridge(ent=entitlements(
        item(PRODUCT_MONTHLY, signed=NOW, expires=NOW + 5 * DAY), item(PRODUCT_LIFETIME, signed=NOW)))
    record = verifier(tmp_path).fetch(bridge).record
    assert record is not None and record.kind is K.LIFETIME


def test_longest_subscription_wins(tmp_path):
    bridge = FakeBridge(ent=entitlements(
        item(PRODUCT_MONTHLY, signed=NOW, expires=NOW + 5 * DAY), item("com.kutstudio.app.yearly", signed=NOW, expires=NOW + 300 * DAY)))
    record = verifier(tmp_path).fetch(bridge).record
    assert record is not None and record.expires_at == pytest.approx(NOW + 300 * DAY)


@pytest.mark.parametrize("ignored", [
    item(PRODUCT_MONTHLY, signed=NOW, expires=NOW + 5 * DAY, revoked=NOW - DAY),          # remboursé
    item(PRODUCT_MONTHLY, signed=NOW, expires=NOW + 5 * DAY, upgraded=True),              # remplacé par une mise à niveau
    item("com.exemple.autre", signed=NOW, expires=NOW + 5 * DAY),                         # produit inconnu : ne débloque rien
    item(PRODUCT_MONTHLY, signed=NOW, expires=NOW - DAY),                                 # échu selon l'horloge injectée
    item(PRODUCT_LIFETIME, signed=NOW, revoked=NOW - 1),                                  # achat unique remboursé
])
def test_revoked_upgraded_unknown_or_ended_entitlements_do_not_unlock(tmp_path, ignored):
    bridge = FakeBridge(ent=entitlements(ignored), app=app_transaction(started=NOW - 20 * DAY, signed=NOW))
    record = verifier(tmp_path).fetch(bridge).record
    assert record is not None and record.kind is K.TRIAL                                  # retombe sur l'essai (ici fini côté service)


def test_the_injected_clock_decides_whether_a_subscription_has_ended(tmp_path):
    bridge = FakeBridge(ent=entitlements(item(PRODUCT_MONTHLY, signed=NOW, expires=NOW + 5 * DAY)),
                        app=app_transaction(started=NOW - DAY, signed=NOW))
    assert verifier(tmp_path, Clock(NOW + 6 * DAY)).fetch(bridge).record.kind is K.TRIAL
    assert verifier(tmp_path, Clock(NOW)).fetch(bridge).record.kind is K.SUBSCRIBER


def test_unverified_transactions_alone_never_unlock_nor_start_a_trial(tmp_path):
    bridge = FakeBridge(ent=entitlements(rejected=2), app=app_transaction(started=NOW, signed=NOW))
    outcome = verifier(tmp_path).fetch(bridge)
    assert outcome.record is None and outcome.reason == REASON_UNVERIFIED
    ok = FakeBridge(ent=entitlements(item(PRODUCT_LIFETIME, signed=NOW), rejected=1))
    assert verifier(tmp_path).fetch(ok).record.kind is K.LIFETIME                          # une transaction vérifiée reste valable


# -- dates peu fiables (Xcode) ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("started, signed, environment", [
    (0.0, NOW, "Xcode"),                          # ce que rend Xcode : 1970
    (NOW - DAY, NOW, "Xcode"),                    # environnement Xcode, même avec une date plausible
    (0.0, NOW, "Sandbox"),                        # 1970 en TestFlight : pas une date d'essai
    (NOW - DAY, NOW, "Unknown"),
    (NOW + 5 * DAY, NOW, "Production"),           # installation « après » la signature : incohérent
])
def test_untrusted_trial_dates_are_unknown_never_a_fresh_trial(tmp_path, started, signed, environment):
    outcome = verifier(tmp_path).fetch(FakeBridge(app=app_transaction(started, signed, environment)))
    assert outcome.record is None and outcome.reason == REASON_UNTRUSTED_TRIAL_DATE
    service, _s, clock = make_service(tmp_path)
    assert service.state().kind is K.UNKNOWN and service.can_edit() is False


@pytest.mark.parametrize("environment", ["Production", "Sandbox", "production"])
def test_trusted_environments_date_the_trial(tmp_path, environment):
    assert verifier(tmp_path).fetch(FakeBridge(app=app_transaction(NOW - DAY, NOW, environment))).record is not None


# -- échecs et délais : rien n'est publié -------------------------------------------------------------------------------------


@pytest.mark.parametrize("status", [sb.TIMEOUT, sb.STOREKIT_ERROR, sb.UNVERIFIED, sb.SHUTDOWN, sb.INTERNAL, sb.BAD_ARGUMENT])
def test_a_failing_entitlements_call_publishes_nothing(tmp_path, status):
    outcome = verifier(tmp_path).fetch(FakeBridge(ent=failure(status), app=app_transaction(NOW - DAY, NOW)))
    assert outcome.record is None and outcome.reason == REASON_UNAVAILABLE


@pytest.mark.parametrize("status", [sb.TIMEOUT, sb.STOREKIT_ERROR, sb.UNVERIFIED, sb.SHUTDOWN])
def test_a_failing_app_transaction_publishes_nothing(tmp_path, status):
    outcome = verifier(tmp_path).fetch(FakeBridge(app=failure(status)))
    assert outcome.record is None and outcome.reason == REASON_UNAVAILABLE


@pytest.mark.parametrize("bad", [
    sb.BridgeResult(sb.OK, {}),                                                    # pas de liste
    sb.BridgeResult(sb.OK, {"entitlements": "x"}),
    entitlements(item(PRODUCT_MONTHLY, signed=NOW, expires=None)),                 # abonnement sans échéance
    entitlements({"productID": PRODUCT_LIFETIME, "signedDate": "pas une date"}),   # date illisible
    entitlements({"productID": PRODUCT_LIFETIME}),                                  # sans date signée
])
def test_malformed_entitlements_publish_nothing(tmp_path, bad):
    outcome = verifier(tmp_path).fetch(FakeBridge(ent=bad))
    assert outcome.record is None and outcome.reason == REASON_INCOMPLETE


def test_malformed_app_transaction_publishes_nothing(tmp_path):
    bad = sb.BridgeResult(sb.OK, {"environment": "Sandbox", "originalPurchaseDate": "x"})
    assert verifier(tmp_path).fetch(FakeBridge(app=bad)).reason == REASON_INCOMPLETE


def test_a_huge_list_is_bounded(tmp_path):
    junk = [{"productID": "x"}] * 5000
    bridge = FakeBridge(ent=sb.BridgeResult(sb.OK, {"entitlements": junk + [item(PRODUCT_LIFETIME, signed=NOW)], "rejected": 0}),
                        app=app_transaction(NOW - DAY, NOW))
    assert verifier(tmp_path).fetch(bridge).record.kind is K.TRIAL                  # l'élément au-delà de la borne n'est pas lu


def test_failure_keeps_the_last_verified_state_per_the_documented_rule(tmp_path):
    """docs/entitlements.md : un échec ne publie rien ; abonnement gardé pendant le délai de grâce, puis UNKNOWN(stale)."""
    service, _s, clock = make_service(tmp_path)
    good = FakeBridge(ent=entitlements(item(PRODUCT_MONTHLY, signed=T0, expires=T0 + 10 * DAY)))
    service.publish(verifier(tmp_path, Clock(T0)).fetch(good).record)
    down = FakeBridge(ent=failure(sb.TIMEOUT, "timeout"))
    for elapsed, kind, grace in ((5 * DAY, K.SUBSCRIBER, False), (12 * DAY, K.SUBSCRIBER, True), (20 * DAY, K.UNKNOWN, False)):
        clock.now = T0 + elapsed
        assert verifier(tmp_path, clock).fetch(down).record is None                  # rien à publier
        state = service.state()
        assert state.kind is kind and state.in_grace is grace
    assert service.state().reason == "stale"


def test_without_any_record_a_failure_is_read_only_not_a_fresh_trial(tmp_path):
    service, _s, _c = make_service(tmp_path)
    assert verifier(tmp_path).fetch(FakeBridge(ent=failure(sb.TIMEOUT))).record is None
    assert service.state().kind is K.UNKNOWN and service.can_edit() is False


# -- signature de l'enveloppe -------------------------------------------------------------------------------------------------


def test_signature_key_is_kept_in_the_container_and_checks_tampering(tmp_path):
    first = StoreKitVerifier(tmp_path / "key", Clock())
    signature = first.sign(b"payload")
    assert first.verify(b"payload", signature) and not first.verify(b"payloae", signature) and not first.verify(b"payload", b"x")
    assert StoreKitVerifier(tmp_path / "key", Clock()).verify(b"payload", signature)       # même clé après relance
    if os.name == "posix":
        assert (tmp_path / "key").stat().st_mode & 0o777 == 0o600
    (tmp_path / "key").write_bytes(b"court")                                               # clé abîmée : remplacée, jamais acceptée
    assert not StoreKitVerifier(tmp_path / "key", Clock()).verify(b"payload", signature)


def test_unwritable_container_keeps_a_session_key_without_raising(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    local = StoreKitVerifier(blocker / "sub" / "key", Clock())                              # le « dossier » est un fichier
    assert local.verify(b"a", local.sign(b"a"))
    assert StoreKitVerifier(None, Clock()).verify(b"a", StoreKitVerifier(None, Clock()).sign(b"a")) is False   # deux clés distinctes


def test_the_store_envelope_round_trips_with_the_real_verifier(tmp_path):
    from core.entitlements import EntitlementService, EntitlementStore

    keyed = StoreKitVerifier(tmp_path / "key", Clock(NOW))
    path = tmp_path / "entitlements.json"
    service = EntitlementService(EntitlementStore(path, keyed), Clock(NOW), enforced=lambda: True)
    service.publish(keyed.fetch(FakeBridge(app=app_transaction(NOW - DAY, NOW))).record)
    again = EntitlementService(EntitlementStore(path, StoreKitVerifier(tmp_path / "key", Clock(NOW))), Clock(NOW), enforced=lambda: True)
    assert again.state().kind is K.TRIAL
    forged = EntitlementService(EntitlementStore(path, StoreKitVerifier(tmp_path / "other", Clock(NOW))), Clock(NOW), enforced=lambda: True)
    assert forged.state().kind is K.UNKNOWN and forged.state().reason == "bad_signature"    # autre clé : jamais un essai neuf


@pytest.mark.parametrize("value, expected", [
    ("2026-10-29T10:00:00.123Z", 1793268000.123), ("2026-10-29T10:00:00+00:00", 1793268000.0),
    ("2026-10-29T10:00:00", None), ("", None), (None, None), (12, None), ("x" * 100, None), ("0000-00-00T00:00:00Z", None),
])
def test_parse_date(value, expected):
    result = parse_date(value)
    assert (result is None) if expected is None else result == pytest.approx(expected)
