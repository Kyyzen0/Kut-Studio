"""Module de droits (ADR-0013, étape 12) : états, essai à J / J+13 / J+14, horloge reculée, état illisible, signature invalide.

Sans Qt, sans réseau : l'horloge est injectée, le fichier d'état est signé (HMAC de test).
"""

from __future__ import annotations

import json

import pytest

from core import entitlements
from core.entitlements import (
    EntitlementKind as K, EntitlementPolicy, EntitlementService, EntitlementState, EntitlementStore, EntitlementVerifier,
    HmacVerifier, VerifiedEntitlement, can_edit, can_export, can_open,
)
from tests.entitlements_support import DAY, KEY, T0, expired, lifetime, make_service, subscriber, trial


def state(kind, **fields):
    return EntitlementState(kind, **fields)


# -- la table des états -------------------------------------------------------------------------------------------------------

ALL_STATES = {
    "essai": state(K.TRIAL, days_left=7), "abonne": state(K.SUBSCRIBER, expires_at=T0 + DAY), "achat": state(K.LIFETIME),
    "expire": state(K.EXPIRED), "inconnu": state(K.UNKNOWN, reason="missing"),
}
# (modifier, exporter) quand le contrôle est exigé ; ouvrir est toujours permis.
EXPECTED = {"essai": True, "abonne": True, "achat": True, "expire": False, "inconnu": False}


@pytest.mark.parametrize("name", list(ALL_STATES))
@pytest.mark.parametrize("is_enforced", [True, False])
def test_table_states_by_enforcement(name, is_enforced):
    s = ALL_STATES[name]
    assert can_open(s) is True                                   # ouvrir / visualiser : TOUJOURS, quel que soit l'état
    expected = EXPECTED[name] if is_enforced else True           # hors Store : toujours vrai, aucun comportement ne change
    assert can_edit(s, is_enforced=is_enforced) is expected
    assert can_export(s, is_enforced=is_enforced) is expected


def test_unknown_policy_is_an_explicit_parameter():
    unknown = ALL_STATES["inconnu"]
    assert can_edit(unknown, is_enforced=True) is False          # défaut documenté : lecture seule
    lenient = EntitlementPolicy(unknown_allows_edit=True)
    assert can_edit(unknown, lenient, is_enforced=True) is True
    assert can_export(unknown, lenient, is_enforced=True) is True


def test_default_enforced_is_off_without_build_flavor(monkeypatch):
    # ``core.build_flavor`` n'existe pas encore (étape 4, autre PR) : import tolérant, faux.
    import sys
    monkeypatch.setitem(sys.modules, "core.build_flavor", None)  # un import qui échoue
    assert entitlements.default_enforced() is False
    assert entitlements.enforced() is False


def test_default_enforced_reads_the_store_flag(monkeypatch):
    import sys
    import types
    fake = types.ModuleType("core.build_flavor")
    fake.STORE = True
    monkeypatch.setitem(sys.modules, "core.build_flavor", fake)
    monkeypatch.setattr("core.build_flavor", fake, raising=False)
    assert entitlements.default_enforced() is True
    fake.STORE = False
    assert entitlements.default_enforced() is False


# -- l'essai ------------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("elapsed, days_left, kind", [
    (0 * DAY, 14, K.TRIAL),                       # jour J : il reste les 14 jours
    (1 * DAY, 13, K.TRIAL),
    (13 * DAY, 1, K.TRIAL),                       # J+13 : dernier jour
    (13 * DAY + 23 * 3600, 1, K.TRIAL),           # J+13 à 23 h : toujours le dernier jour
    (14 * DAY - 1, 1, K.TRIAL),                   # une seconde avant la fin
    (14 * DAY, None, K.EXPIRED),                  # J+14 : fini (fin exclue)
    (40 * DAY, None, K.EXPIRED),
])
def test_trial_days(tmp_path, elapsed, days_left, kind):
    service, _store, clock = make_service(tmp_path)
    service.publish(trial(started=T0))
    clock.now = T0 + elapsed
    result = service.state()
    assert result.kind is kind
    assert result.days_left == days_left
    if kind is K.EXPIRED:
        assert result.reason == "trial_ended"
        assert service.can_edit() is False and service.can_export() is False and service.can_open() is True
    else:
        assert service.can_edit() is True and service.can_export() is True


def test_trial_length_is_a_policy_parameter(tmp_path):
    service, _s, clock = make_service(tmp_path, policy=EntitlementPolicy(trial_days=3))
    service.publish(trial(T0))
    assert service.state().days_left == 3
    clock.now = T0 + 3 * DAY
    assert service.state().kind is K.EXPIRED


def test_trial_started_in_the_future_is_never_longer_than_the_trial(tmp_path):
    service, _s, clock = make_service(tmp_path)
    service.publish(trial(started=T0 + 100 * DAY, verified=T0))      # début annoncé dans le futur : pas de rallonge
    assert service.state().days_left == 14


# -- horloge reculée ----------------------------------------------------------------------------------------------------------


def test_clock_rolled_back_does_not_extend_the_trial(tmp_path):
    service, _s, clock = make_service(tmp_path)
    service.publish(trial(T0))
    clock.now = T0 + 13 * DAY
    assert service.state().days_left == 1
    clock.now = T0 + 2 * DAY                                         # l'utilisateur recule la date
    back = service.state()
    assert back.days_left == 1 and back.clock_rollback is True       # aucune rallonge, et on le dit
    clock.now = T0 + 14 * DAY
    assert service.state().kind is K.EXPIRED


def test_clock_rollback_survives_a_restart(tmp_path):
    service, store, clock = make_service(tmp_path)
    service.publish(trial(T0))
    clock.now = T0 + 13 * DAY
    service.state()                                                  # la plus grande date vue est écrite (signée)
    clock.now = T0 + 1 * DAY                                         # on recule, puis on relance l'application
    relaunched = EntitlementService(store, clock, enforced=lambda: True)
    result = relaunched.state()
    assert result.clock_rollback is True and result.days_left == 1


def test_clock_rollback_does_not_extend_a_subscription(tmp_path):
    service, _s, clock = make_service(tmp_path)
    service.publish(subscriber(expires=T0 + 10 * DAY))
    clock.now = T0 + 30 * DAY                                        # échu depuis longtemps, délai de grâce passé
    assert service.state().kind is K.UNKNOWN
    clock.now = T0 + 5 * DAY                                         # reculé : l'abonnement ne revit pas
    result = service.state()
    assert result.kind is K.UNKNOWN and result.clock_rollback is True


def test_small_clock_jitter_is_not_a_rollback(tmp_path):
    service, _s, clock = make_service(tmp_path)
    service.publish(trial(T0))
    clock.now = T0 + 10.0
    service.state()
    clock.now = T0 + 5.0                                             # 5 s de retard : dans la tolérance
    assert service.state().clock_rollback is False


def test_a_trusted_fresh_verification_resets_a_poisoned_watermark(tmp_path):
    service, _s, clock = make_service(tmp_path)
    service.publish(trial(T0))
    clock.now = T0 + 400 * DAY                                       # horloge poussée par erreur très loin
    assert service.state().kind is K.EXPIRED
    clock.now = T0 + DAY                                             # remise à l'heure
    service.publish(lifetime(verified=T0 + DAY), fresh=True)         # temps frais et de confiance : seul cas qui guérit
    healed = service.state()
    assert healed.kind is K.LIFETIME and healed.clock_rollback is False


# -- état illisible / signature invalide / absent ------------------------------------------------------------------------------


def test_missing_state_is_unknown_never_a_fresh_trial(tmp_path):
    service, _s, _c = make_service(tmp_path)
    result = service.state()
    assert result.kind is K.UNKNOWN and result.reason == "missing"
    assert service.can_edit() is False and service.can_export() is False and service.can_open() is True


@pytest.mark.parametrize("content", ["", "pas du json", "[]", "{}", '{"v": 1}', '{"v": 2, "payload": "", "signature": ""}',
                                      '{"v": 1, "payload": "!!!", "signature": "!!!"}', "\x00\x01\x02", "x" * 40_000],
                         ids=["vide", "pas-json", "liste", "objet-vide", "sans-payload", "version-2", "base64-faux", "octets", "enorme"])
def test_unreadable_state_is_unknown(tmp_path, content):
    service, store, _c = make_service(tmp_path)
    store.path.write_text(content, encoding="utf-8")
    result = service.state()
    assert result.kind is K.UNKNOWN and result.reason in ("unreadable", "bad_signature")
    assert service.can_edit() is False


def test_binary_garbage_is_unknown(tmp_path):
    service, store, _c = make_service(tmp_path)
    store.path.write_bytes(b"\xff\xfe\x00\x80garbage")
    assert service.state().kind is K.UNKNOWN


def test_hand_edited_state_has_an_invalid_signature(tmp_path):
    service, store, _c = make_service(tmp_path)
    service.publish(expired())
    envelope = json.loads(store.path.read_text(encoding="utf-8"))
    import base64
    payload = json.loads(base64.b64decode(envelope["payload"]))
    payload["kind"] = "lifetime"                                     # l'utilisateur « s'offre » l'achat unique
    envelope["payload"] = base64.b64encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).decode()
    store.path.write_text(json.dumps(envelope), encoding="utf-8")
    service.refresh()
    result = service.state()
    assert result.kind is K.UNKNOWN and result.reason == "bad_signature"
    assert service.can_edit() is False


def test_state_signed_with_another_key_is_refused(tmp_path):
    service, store, _c = make_service(tmp_path)
    other = EntitlementStore(store.path, HmacVerifier(b"autre-cle"))
    other.save(lifetime())
    assert service.state().reason == "bad_signature"


def test_oversized_state_file_is_not_read(tmp_path):
    service, store, _c = make_service(tmp_path)
    store.path.write_text(" " * 20_000, encoding="utf-8")
    assert service.state().reason == "unreadable"


def test_signed_but_incoherent_record_is_unreadable(tmp_path):
    from core.entitlements.store import encode_envelope
    service, store, _c = make_service(tmp_path)
    envelope = encode_envelope(lifetime(), store.verifier)
    import base64
    payload = json.loads(base64.b64decode(envelope["payload"]))
    payload["kind"] = "platinum"
    raw = json.dumps(payload).encode()
    envelope["payload"] = base64.b64encode(raw).decode()
    envelope["signature"] = base64.b64encode(store.verifier.sign(raw)).decode()
    store.path.write_text(json.dumps(envelope), encoding="utf-8")
    assert service.state().reason == "unreadable"


def test_unknown_is_not_storable():
    with pytest.raises(ValueError):
        VerifiedEntitlement(K.UNKNOWN, T0)
    with pytest.raises(ValueError):
        VerifiedEntitlement(K.TRIAL, T0)                             # un essai sans début
    with pytest.raises(ValueError):
        VerifiedEntitlement(K.SUBSCRIBER, T0)                        # un abonnement sans échéance
    with pytest.raises(ValueError):
        VerifiedEntitlement(K.TRIAL, float("nan"), trial_started_at=T0)


# -- abonnement, délai de grâce, achat unique ---------------------------------------------------------------------------------


def test_subscription_and_grace(tmp_path):
    service, _s, clock = make_service(tmp_path)
    service.publish(subscriber(expires=T0 + 30 * DAY))
    clock.now = T0 + 29 * DAY
    assert (service.state().kind, service.state().in_grace) == (K.SUBSCRIBER, False)
    clock.now = T0 + 33 * DAY                                        # échu, renouvellement non confirmé, dans la grâce
    grace = service.state()
    assert grace.kind is K.SUBSCRIBER and grace.in_grace is True and service.can_edit() is True
    clock.now = T0 + 38 * DAY                                        # grâce de 7 jours passée sans confirmation
    stale = service.state()
    assert stale.kind is K.UNKNOWN and stale.reason == "stale"       # on ne sait pas : pas « expiré »
    assert service.can_edit() is False and service.can_export() is False and service.can_open() is True


def test_grace_is_a_parameter_not_a_constant(tmp_path):
    service, _s, clock = make_service(tmp_path, policy=EntitlementPolicy(grace_seconds=0.0))
    service.publish(subscriber(expires=T0 + DAY))
    clock.now = T0 + DAY + 1
    assert service.state().reason == "stale"


def test_renewal_published_by_the_bridge_ends_the_grace(tmp_path):
    service, _s, clock = make_service(tmp_path)
    service.publish(subscriber(expires=T0 + DAY))
    clock.now = T0 + 5 * DAY
    assert service.state().in_grace is True
    service.publish(subscriber(expires=T0 + 35 * DAY, verified=clock.now))
    renewed = service.state()
    assert renewed.kind is K.SUBSCRIBER and renewed.in_grace is False


def test_lifetime_never_expires(tmp_path):
    service, _s, clock = make_service(tmp_path)
    service.publish(lifetime())
    clock.now = T0 + 5000 * DAY
    assert service.state().kind is K.LIFETIME and service.can_edit() is True


def test_bridge_verified_expired_stays_expired(tmp_path):
    service, _s, _c = make_service(tmp_path)
    service.publish(expired())
    assert service.state().kind is K.EXPIRED


# -- hors Store ---------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("record", [None, expired(), trial(T0 - 90 * DAY)])
def test_not_enforced_always_allows(tmp_path, record):
    service, _s, _c = make_service(tmp_path, enforced=False)
    if record is not None:
        service.publish(record)
    assert service.can_edit() is True and service.can_export() is True and service.can_open() is True


def test_the_signing_interface_is_abstract():
    with pytest.raises(TypeError):
        EntitlementVerifier()                                        # type: ignore[abstract]
    verifier = HmacVerifier(KEY)
    signature = verifier.sign(b"x")
    assert verifier.verify(b"x", signature) and not verifier.verify(b"y", signature) and not verifier.verify(b"x", b"")
    with pytest.raises(ValueError):
        HmacVerifier(b"")


def test_the_watermark_is_written_at_most_once_per_interval(tmp_path):
    service, store, clock = make_service(tmp_path)
    service.publish(trial(T0))
    before = store.path.read_text(encoding="utf-8")
    clock.now = T0 + 60.0
    service.state()
    assert store.path.read_text(encoding="utf-8") == before          # moins d'une heure : pas de réécriture
    clock.now = T0 + 2 * 3600.0
    service.state()
    assert store.path.read_text(encoding="utf-8") != before


def test_republishing_a_cached_transaction_never_lowers_the_watermark(tmp_path):
    service, _s, clock = make_service(tmp_path)
    service.publish(trial(T0))
    clock.now = T0 + 20 * DAY
    assert service.state().kind is K.EXPIRED
    clock.now = T0 + DAY                                             # l'utilisateur recule l'horloge
    service.publish(trial(T0))                                       # même réponse en cache (ancien signedDate)
    state = service.state()
    assert state.kind is K.EXPIRED and state.clock_rollback is True and service.can_edit() is False


def test_republishing_after_a_restart_keeps_the_persisted_watermark(tmp_path):
    service, store, clock = make_service(tmp_path)
    service.publish(trial(T0))
    clock.now = T0 + 20 * DAY
    assert service.state().kind is K.EXPIRED                         # filigrane écrit (signé)
    clock.now = T0 + DAY
    relaunched = EntitlementService(store, clock, enforced=lambda: True)    # session neuve : seul le fichier se souvient
    relaunched.publish(trial(T0))                                    # le pont rend la même transaction en cache
    state = relaunched.state()
    assert state.kind is K.EXPIRED and state.clock_rollback is True
