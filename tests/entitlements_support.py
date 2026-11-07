"""Aides communes des tests de droits (ADR-0013, étape 12) : horloge injectée, service signé, bascule « Store »."""

from __future__ import annotations

from pathlib import Path

import pytest

from core import entitlements
from core.entitlements import (
    EntitlementKind, EntitlementPolicy, EntitlementService, EntitlementStore, HmacVerifier, VerifiedEntitlement,
)

DAY = 86_400.0
T0 = 1_800_000_000.0
KEY = b"cle-de-test-pas-un-secret"


class Clock:
    """Horloge injectée : ``clock()`` rend ``now`` ; les tests la font avancer ou reculer."""

    def __init__(self, now: float = T0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def make_service(tmp_path: Path, clock: Clock | None = None, policy: EntitlementPolicy | None = None, enforced: bool = True):
    clock = clock or Clock()
    store = EntitlementStore(tmp_path / "entitlements.json", HmacVerifier(KEY))
    service = EntitlementService(store, clock, policy or EntitlementPolicy(), enforced=lambda: enforced)
    return service, store, clock


def trial(started: float = T0, verified: float | None = None) -> VerifiedEntitlement:
    return VerifiedEntitlement(EntitlementKind.TRIAL, started if verified is None else verified, trial_started_at=started)


def subscriber(expires: float, verified: float = T0) -> VerifiedEntitlement:
    return VerifiedEntitlement(EntitlementKind.SUBSCRIBER, verified, expires_at=expires)


def lifetime(verified: float = T0) -> VerifiedEntitlement:
    return VerifiedEntitlement(EntitlementKind.LIFETIME, verified)


def expired(verified: float = T0) -> VerifiedEntitlement:
    return VerifiedEntitlement(EntitlementKind.EXPIRED, verified)


@pytest.fixture
def store_flavor(monkeypatch):
    """Le build « Store » : le contrôle est exigé. Retire tout service installé en sortie."""
    entitlements.set_enforced_provider(lambda: True)
    yield
    entitlements.set_enforced_provider(None)
    entitlements.install_service(None)


@pytest.fixture
def install(tmp_path_factory, store_flavor):
    """``install(record)`` : installe un service de droits sur ``record`` (horloge injectée) et le rend avec son horloge."""
    def _install(record: VerifiedEntitlement | None, clock: Clock | None = None, policy: EntitlementPolicy | None = None):
        service, store, clk = make_service(tmp_path_factory.mktemp("state"), clock, policy)
        if record is not None:
            service.publish(record)
        entitlements.install_service(service)
        return service, clk
    return _install
