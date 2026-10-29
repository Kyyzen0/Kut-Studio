"""Faux pont StoreKit pour les tests (ADR-0013, étape 13) : aucun StoreKit réel, aucune bibliothèque."""

from __future__ import annotations

import queue
import threading
from datetime import datetime, timezone

from core.entitlements import storekit_bridge as sb
from core.entitlements.storekit_bridge import BridgeResult, StoreBridgeError

PRODUCT_LIFETIME = "com.kutstudio.app.lifetime"
PRODUCT_MONTHLY = "com.kutstudio.app.monthly"


def iso(stamp: float) -> str:
    return datetime.fromtimestamp(stamp, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def item(product: str, *, signed: float, expires: float | None = None, revoked: float | None = None, upgraded: bool = False) -> dict:
    return {
        "productID": product, "signedDate": iso(signed), "expirationDate": None if expires is None else iso(expires),
        "revocationDate": None if revoked is None else iso(revoked), "isUpgraded": upgraded,
    }


def entitlements(*items: dict, rejected: int = 0) -> BridgeResult:
    return BridgeResult(sb.OK, {"entitlements": list(items), "rejected": rejected})


def app_transaction(started: float, signed: float, environment: str = "Sandbox") -> BridgeResult:
    return BridgeResult(sb.OK, {
        "originalPurchaseDate": iso(started), "signedDate": iso(signed), "environment": environment, "verified": True,
    })


def failure(status: int, word: str = "storekit") -> BridgeResult:
    return BridgeResult(status, {"error": word})


class FakeBridge:
    """``call`` rend ce qui est programmé par opération ; ``poll_update`` lit une file (``push``) ; ``shutdown`` réveille le sondage."""

    def __init__(self, ent: BridgeResult | None = None, app: BridgeResult | None = None) -> None:
        self.results: dict[int, BridgeResult] = {
            sb.OP_ENTITLEMENTS: ent or entitlements(), sb.OP_APP_TRANSACTION: app or failure(sb.STOREKIT_ERROR),
            sb.OP_SYNC: BridgeResult(sb.OK, {"synced": True}), sb.OP_START_UPDATES: BridgeResult(sb.OK, {"listening": True}),
        }
        self.calls: list[tuple[int, str, int]] = []
        self.shutdown_count = 0
        self.polls = 0
        self._events: queue.Queue = queue.Queue()
        self._closed = threading.Event()
        self.on_call = None

    def call(self, op: int, arg: str = "", timeout_ms: int = 15000) -> BridgeResult:
        self.calls.append((op, arg, timeout_ms))
        if self.on_call is not None:
            self.on_call(op)
        if self._closed.is_set():
            return failure(sb.SHUTDOWN, "shutdown")
        return self.results[op]

    def push(self, payload: dict) -> None:
        self._events.put(BridgeResult(sb.OK, payload))

    def poll_update(self, timeout_ms: int = 1000) -> BridgeResult:
        self.polls += 1
        if self._closed.is_set():
            return failure(sb.SHUTDOWN, "shutdown")
        try:
            return self._events.get(timeout=min(timeout_ms / 1000.0, 0.05))
        except queue.Empty:
            return failure(sb.SHUTDOWN, "shutdown") if self._closed.is_set() else failure(sb.POLL_TIMEOUT, "poll_timeout")

    def shutdown(self) -> None:
        self.shutdown_count += 1
        self._closed.set()


def loader_for(bridge: FakeBridge):
    return lambda: bridge


def missing_loader():
    raise StoreBridgeError("pont absent")
