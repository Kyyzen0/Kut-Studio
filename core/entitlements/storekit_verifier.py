"""Le vérificateur StoreKit : produit l'état de droits à partir du pont, et signe l'enveloppe persistée (ADR-0013, étape 13).

Deux rôles, une classe (:class:`StoreKitVerifier`, implémentation réelle de :class:`EntitlementVerifier`) :

1. **Produire l'état** (:meth:`StoreKitVerifier.fetch`). À partir des réponses du pont (appels BLOQUANTS : thread de travail,
   jamais le thread Qt) :

   * ``Transaction.currentEntitlements`` : un achat unique donne ``LIFETIME`` ; un abonnement actif donne ``SUBSCRIBER`` (échéance
     du droit le plus long) ; une transaction révoquée (remboursement), mise à niveau ou d'un produit inconnu est ignorée ;
   * sinon ``AppTransaction.originalPurchaseDate`` date l'essai : ``TRIAL`` (le service calcule les jours restants, ou ``EXPIRED``
     une fois l'essai fini). Cette date vient d'Apple : une réinstallation ou un changement d'horloge ne remet rien à zéro.

   Chaque date d'un état vient de la **transaction signée** (``signedDate``), jamais de l'horloge locale. Un résultat non
   vérifié par StoreKit (JWS) n'est jamais rendu par le pont ; s'il n'y a que cela, on ne publie rien.

2. **Signer l'enveloppe** (:meth:`sign` / :meth:`verify`) : HMAC-SHA256 avec une clé aléatoire propre à l'installation, gardée dans le
   conteneur de l'application (0600). **Limite assumée** : qui lit le conteneur lit la clé ; la confiance repose sur la
   revérification auprès d'Apple à chaque lancement et sur le bac à sable du Store (ADR, § Intégrité), pas sur ce secret. Le
   trousseau (clé non extractible) est un durcissement possible, non fait (étape 7 / 15).

**Échec ou délai de StoreKit** : :meth:`fetch` rend ``record=None`` (rien n'est publié). La règle décidée (``docs/entitlements.md``)
s'applique alors par le service : le dernier état vérifié et signé reste lu (un abonnement échu reste ``SUBSCRIBER`` pendant le délai
de grâce, puis ``UNKNOWN(stale)``) ; sans dernier état lisible, ``UNKNOWN`` (lecture seule), jamais un essai neuf.

**Dates peu fiables.** Sous Xcode, ``AppTransaction.originalPurchaseDate`` vaut 1970 et ``environment`` = ``Xcode`` (mesuré, spike du
2026-10-29) ; les vraies valeurs n'existent qu'en TestFlight (``Sandbox``) et en production. L'essai n'est donc daté que pour
``Production`` / ``Sandbox`` avec une date postérieure à 2007 et non postérieure à la date signée ; sinon ``record=None`` avec la
raison ``untrusted_trial_date`` (état inconnu : jamais un essai neuf ni un déblocage).
"""

from __future__ import annotations

import hmac
import logging
import os
import secrets
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

from .model import EntitlementKind
from .storekit_bridge import DEFAULT_CALL_TIMEOUT_MS, OP_APP_TRANSACTION, OP_ENTITLEMENTS, BridgeResult, StoreKitBridge
from .store import VerifiedEntitlement
from .verifier import EntitlementVerifier

LOGGER = logging.getLogger("kut_studio.entitlements")

KEY_BYTES = 32
MAX_ITEMS = 200
"""Au plus 200 droits lus (un compte légitime en a quelques-uns)."""
MAX_DATE_CHARS = 64
MIN_TRUSTED_DATE = datetime(2007, 1, 1, tzinfo=timezone.utc).timestamp()
"""Avant l'App Store (2008) : une date de ce genre (1970 sous Xcode) ne date pas un essai."""
TRUSTED_ENVIRONMENTS = frozenset({"production", "sandbox"})
DAY = 86_400.0

# Raisons d'un échec de lecture (``FetchOutcome.reason``). Jamais de donnée de l'utilisateur.
REASON_OK = "ok"
REASON_UNAVAILABLE = "storekit_unavailable"
REASON_INCOMPLETE = "incomplete_payload"
REASON_UNVERIFIED = "unverified_transaction"
REASON_UNTRUSTED_TRIAL_DATE = "untrusted_trial_date"


@dataclass(frozen=True)
class ProductIds:
    """Identifiants des produits. **Provisoires** : ceux du fichier ``.storekit`` du spike, à fixer à l'étape 14 (App Store Connect)."""

    subscriptions: frozenset[str] = field(default_factory=lambda: frozenset({"com.kutstudio.app.monthly", "com.kutstudio.app.yearly"}))
    lifetime: str = "com.kutstudio.app.lifetime"


DEFAULT_PRODUCTS = ProductIds()


@dataclass(frozen=True)
class FetchOutcome:
    """Résultat d'une lecture : l'état vérifié à publier, ou ``None`` et la raison (``REASON_*``)."""

    record: VerifiedEntitlement | None
    reason: str = REASON_OK


class _Incomplete(Exception):
    """Une réponse du pont est abîmée : rien n'est publié (sans texte : aucune donnée recopiée)."""


def parse_date(value: object) -> float | None:
    """Secondes depuis l'époque d'une date ISO 8601 **avec fuseau** (ce que rend le pont), ou ``None``. Ne lève pas."""
    if not isinstance(value, str) or not value or len(value) > MAX_DATE_CHARS:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    try:
        stamp = parsed.timestamp()
    except (OverflowError, OSError, ValueError):
        return None
    return stamp if stamp == stamp and abs(stamp) < 1e11 else None


class StoreKitVerifier(EntitlementVerifier):
    """Voir le docstring du module. ``key_path`` : fichier de la clé de signature (dans le conteneur) ; ``clock`` injectée."""

    def __init__(
        self,
        key_path: str | Path | None,
        clock: Callable[[], float],
        products: ProductIds = DEFAULT_PRODUCTS,
        *,
        call_timeout_ms: int = DEFAULT_CALL_TIMEOUT_MS,
    ) -> None:
        self._clock = clock
        self.products = products
        self.call_timeout_ms = call_timeout_ms
        self._key = _load_or_create_key(Path(key_path) if key_path is not None else None)

    # -- signature de l'enveloppe -------------------------------------------------------------------------------------------

    def sign(self, payload: bytes) -> bytes:
        return hmac.new(self._key, payload, sha256).digest()

    def verify(self, payload: bytes, signature: bytes) -> bool:
        try:
            return hmac.compare_digest(self.sign(payload), bytes(signature))
        except (TypeError, ValueError):
            return False

    # -- lecture de StoreKit ------------------------------------------------------------------------------------------------

    def fetch(self, bridge: StoreKitBridge) -> FetchOutcome:
        """Lit les droits (BLOQUANT : thread de travail). Ne lève pas ; ``record=None`` si rien ne peut être publié."""
        try:
            return self._fetch(bridge)
        except _Incomplete:
            return FetchOutcome(None, REASON_INCOMPLETE)

    def _fetch(self, bridge: StoreKitBridge) -> FetchOutcome:
        entitlements = bridge.call(OP_ENTITLEMENTS, "", self.call_timeout_ms)
        if not entitlements.ok:
            return FetchOutcome(None, REASON_UNAVAILABLE)
        items = entitlements.payload.get("entitlements")
        if not isinstance(items, list):
            raise _Incomplete
        rejected = entitlements.payload.get("rejected", 0)
        best = self._best_entitlement(items[:MAX_ITEMS])
        if best is not None:
            return FetchOutcome(best)
        if isinstance(rejected, int) and not isinstance(rejected, bool) and rejected > 0:
            return FetchOutcome(None, REASON_UNVERIFIED)           # des droits non vérifiés ne débloquent rien, ni ne rendent un essai
        return self._trial(bridge.call(OP_APP_TRANSACTION, "", self.call_timeout_ms))

    def _best_entitlement(self, items: list) -> VerifiedEntitlement | None:
        now = float(self._clock())
        lifetime: VerifiedEntitlement | None = None
        subscription: VerifiedEntitlement | None = None
        for item in items:
            if not isinstance(item, dict):
                continue
            product = item.get("productID")
            is_lifetime = product == self.products.lifetime
            if not is_lifetime and product not in self.products.subscriptions:
                continue                                           # produit inconnu : ne débloque rien
            if item.get("revocationDate") is not None or item.get("isUpgraded") is True:
                continue                                           # remboursé ou remplacé par une mise à niveau
            signed = parse_date(item.get("signedDate"))
            if signed is None:
                raise _Incomplete
            if is_lifetime:
                lifetime = VerifiedEntitlement(EntitlementKind.LIFETIME, signed)
                continue
            expires = parse_date(item.get("expirationDate"))
            if expires is None:
                raise _Incomplete
            if expires <= now:
                continue                                           # échu : n'est plus un droit
            if subscription is None or expires > (subscription.expires_at or 0.0):
                subscription = VerifiedEntitlement(EntitlementKind.SUBSCRIBER, signed, expires_at=expires)
        return lifetime or subscription

    def _trial(self, result: BridgeResult) -> FetchOutcome:
        if not result.ok:
            return FetchOutcome(None, REASON_UNAVAILABLE)
        payload = result.payload
        environment = payload.get("environment")
        started = parse_date(payload.get("originalPurchaseDate"))
        signed = parse_date(payload.get("signedDate"))
        if payload.get("verified") is not True or signed is None:
            raise _Incomplete
        if (
            not isinstance(environment, str) or environment.lower() not in TRUSTED_ENVIRONMENTS or started is None
            or started < MIN_TRUSTED_DATE or signed < MIN_TRUSTED_DATE or started > signed + DAY
        ):
            return FetchOutcome(None, REASON_UNTRUSTED_TRIAL_DATE)
        return FetchOutcome(VerifiedEntitlement(EntitlementKind.TRIAL, signed, trial_started_at=started))


def _load_or_create_key(path: Path | None) -> bytes:
    """La clé du conteneur, ou une neuve (écrite en 0600). Si le conteneur n'est pas inscriptible : clé de la session seulement."""
    if path is not None:
        try:
            data = path.read_bytes()
            if len(data) == KEY_BYTES:
                return data
        except OSError:
            pass
    key = secrets.token_bytes(KEY_BYTES)
    if path is not None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temp = tempfile.mkstemp(dir=path.parent, prefix=".key-")
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(key)
                os.chmod(temp, 0o600)
                os.replace(temp, path)
            except OSError:
                try:
                    os.unlink(temp)
                except OSError:
                    pass
                raise
        except OSError as error:
            LOGGER.warning("Droits : clé de signature non écrite (%s)", type(error).__name__)
    return key


__all__ = [
    "DEFAULT_PRODUCTS", "FetchOutcome", "MIN_TRUSTED_DATE", "ProductIds", "REASON_INCOMPLETE", "REASON_OK", "REASON_UNAVAILABLE",
    "REASON_UNTRUSTED_TRIAL_DATE", "REASON_UNVERIFIED", "StoreKitVerifier", "parse_date",
]
