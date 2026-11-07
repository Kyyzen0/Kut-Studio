"""Produits, achat et restauration par le pont StoreKit (ADR-0013, étape 15). Sans Qt.

**Le seul module qui demande un achat** (``OP_PURCHASE``) : un test d'inventaire (AST) fait échouer toute autre référence.

Règles, toutes testées avec un faux pont :

* **Un achat ne débloque rien.** La réponse du pont (« succès ») n'est qu'un signal : le déblocage vient d'une **relecture complète**
  (``refresh`` = :meth:`StoreKitVerifier.fetch` puis ``publish`` dans le service) qui trouve un droit **vérifié**. Un succès non vérifié
  (``verified`` faux, produit différent de celui demandé, relecture sans droit) donne ``NOT_CONFIRMED`` et ne change aucun état.
  Côté Swift, ``Transaction.finish()`` n'est appelé qu'après la vérification JWS (``KutStoreBridge.swift``, opération 6).
* **Hors du fil Qt.** Chaque action tourne dans un fil Python court (``kut-storekit-action``), une à la fois (sinon ``BUSY``) : le pont
  est bloquant (GIL relâché) et un achat attend l'utilisateur. Aucun rappel natif ; le résultat est remis par ``on_result``,
  appelé depuis ce fil Python (l'interface y branche un signal Qt).
* **Produits connus seulement.** Un identifiant hors catalogue n'atteint jamais le pont. Les prix viennent de ``Product.displayPrice``
  (StoreKit, localisés) ; **aucun montant n'est codé en dur** : sans réponse, l'interface dit « prix indisponible ».
* **Réponses bornées** : au plus ``MAX_PRODUCTS`` produits, prix limité à ``MAX_PRICE_CHARS`` caractères sans caractère de contrôle.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum

from .model import EntitlementKind
from .storekit_bridge import (
    OK, OP_PRODUCTS, OP_PURCHASE, OP_SYNC, SHUTDOWN, TIMEOUT, StoreKitBridge,
)
from .storekit_verifier import DEFAULT_PRODUCTS, ProductIds

LOGGER = logging.getLogger("kut_studio.entitlements")

MAX_PRODUCTS = 8
MAX_PRICE_CHARS = 40
PRODUCTS_TIMEOUT_MS = 20_000
PURCHASE_TIMEOUT_MS = 120_000
"""Un achat attend l'utilisateur (feuille Apple, mot de passe) : le délai maximal du pont."""
RESTORE_TIMEOUT_MS = 60_000

OFFER_MONTHLY, OFFER_YEARLY, OFFER_LIFETIME = "monthly", "yearly", "lifetime"
PAID_KINDS = frozenset({EntitlementKind.SUBSCRIBER, EntitlementKind.LIFETIME})


@dataclass(frozen=True)
class Offer:
    """Une offre affichée : ``key`` (clé i18n), ``product_id``, ``recurring`` (abonnement à renouvellement automatique)."""

    key: str
    product_id: str
    recurring: bool


def offers(products: ProductIds = DEFAULT_PRODUCTS) -> tuple[Offer, ...]:
    """Les trois offres, dans l'ordre d'affichage. Les identifiants viennent de :class:`ProductIds` (une seule constante)."""
    return (
        Offer(OFFER_MONTHLY, products.monthly, True),
        Offer(OFFER_YEARLY, products.yearly, True),
        Offer(OFFER_LIFETIME, products.lifetime, False),
    )


class ActionKind(str, Enum):
    PRODUCTS = "products"
    PURCHASE = "purchase"
    RESTORE = "restore"


class ActionStatus(str, Enum):
    DONE = "done"
    """Produits lus ; achat ou restauration **confirmé par une relecture vérifiée** (un droit payant existe)."""
    CANCELLED = "cancelled"
    PENDING = "pending"
    """Achat en attente (contrôle parental, approbation, ou délai dépassé) : rien n'est débloqué ; ``Transaction.updates`` suivra."""
    NOT_CONFIRMED = "not_confirmed"
    """Le pont dit « succès » mais la relecture ne trouve aucun droit vérifié : rien n'est débloqué."""
    NOTHING_TO_RESTORE = "nothing_to_restore"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"
    """Pont absent, arrêté ou produits non chargés."""
    BUSY = "busy"


@dataclass(frozen=True)
class ProductInfo:
    """Ce que StoreKit rend d'un produit : le prix **localisé** (``Product.displayPrice``)."""

    product_id: str
    display_price: str


@dataclass(frozen=True)
class ActionResult:
    kind: ActionKind
    status: ActionStatus
    product_id: str = ""
    products: dict[str, ProductInfo] = field(default_factory=dict)


def _clean_price(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = "".join(ch for ch in value if ch.isprintable()).strip()
    return text[:MAX_PRICE_CHARS] if text else None


def parse_products(payload: dict, products: ProductIds = DEFAULT_PRODUCTS) -> dict[str, ProductInfo]:
    """Les produits connus de la réponse ``OP_PRODUCTS``, avec leur prix localisé. Ne lève pas ; le reste est ignoré."""
    items = payload.get("products")
    if not isinstance(items, list):
        return {}
    known = set(products.ordered)
    found: dict[str, ProductInfo] = {}
    for item in items[:MAX_PRODUCTS]:
        if not isinstance(item, dict):
            continue
        identifier = item.get("id")
        price = _clean_price(item.get("displayPrice"))
        if isinstance(identifier, str) and identifier in known and price is not None:
            found[identifier] = ProductInfo(identifier, price)
    return found


class StoreActions:
    """Produits, achat, restauration. ``bridge`` rend le pont chargé (ou ``None``) ; ``refresh`` relit et publie l'état vérifié ;
    ``paid`` dit si l'état publié est payant ; ``on_result`` reçoit chaque résultat (fil Python)."""

    def __init__(
        self,
        bridge: Callable[[], StoreKitBridge | None],
        refresh: Callable[[], None],
        paid: Callable[[], bool],
        on_result: Callable[[ActionResult], None],
        products: ProductIds = DEFAULT_PRODUCTS,
    ) -> None:
        self._bridge = bridge
        self._refresh = refresh
        self._paid = paid
        self._on_result = on_result
        self.products = products
        self._busy = threading.Lock()
        self._loading = threading.Lock()                            # le chargement des prix ne bloque pas un achat
        self.threads: list[threading.Thread] = []
        """Les fils lancés (tests : les attendre)."""

    # -- demandes (thread Qt : ne bloquent jamais) --------------------------------------------------------------------------

    def request_products(self) -> bool:
        return self._start(ActionKind.PRODUCTS, "", self._load_products)

    def request_purchase(self, product_id: str) -> bool:
        return self._start(ActionKind.PURCHASE, product_id, lambda: self._purchase(product_id))

    def request_restore(self) -> bool:
        return self._start(ActionKind.RESTORE, "", self._restore)

    def _start(self, kind: ActionKind, product_id: str, work: Callable[[], ActionResult]) -> bool:
        lock = self._loading if kind is ActionKind.PRODUCTS else self._busy
        if not lock.acquire(blocking=False):
            if kind is not ActionKind.PRODUCTS:
                self._deliver(ActionResult(kind, ActionStatus.BUSY, product_id))
            return False

        def run() -> None:
            try:
                result = work()
            except Exception as error:                              # noqa: BLE001 - un fil de travail ne meurt jamais en silence
                LOGGER.warning("Droits : action %s en erreur (%s)", kind.value, type(error).__name__)
                result = ActionResult(kind, ActionStatus.FAILED, product_id)
            finally:
                lock.release()
            self._deliver(result)

        thread = threading.Thread(target=run, name="kut-storekit-action", daemon=True)
        self.threads = [t for t in self.threads if t.is_alive()] + [thread]
        thread.start()
        return True

    def _deliver(self, result: ActionResult) -> None:
        try:
            self._on_result(result)
        except Exception as error:                                  # noqa: BLE001 - un abonné défectueux n'arrête rien
            LOGGER.warning("Droits : abonné au résultat en erreur (%s)", type(error).__name__)

    # -- le travail (fil Python) --------------------------------------------------------------------------------------------

    def _load_products(self) -> ActionResult:
        bridge = self._bridge()
        if bridge is None:
            return ActionResult(ActionKind.PRODUCTS, ActionStatus.UNAVAILABLE)
        reply = bridge.call(OP_PRODUCTS, ",".join(self.products.ordered), PRODUCTS_TIMEOUT_MS)
        found = parse_products(reply.payload, self.products) if reply.status == OK else {}
        status = ActionStatus.DONE if found else ActionStatus.UNAVAILABLE
        return ActionResult(ActionKind.PRODUCTS, status, products=found)

    def _purchase(self, product_id: str) -> ActionResult:
        def result(status: ActionStatus) -> ActionResult:
            return ActionResult(ActionKind.PURCHASE, status, product_id)

        if product_id not in self.products.ordered:
            return result(ActionStatus.FAILED)                      # hors catalogue : n'atteint pas le pont
        bridge = self._bridge()
        if bridge is None:
            return result(ActionStatus.UNAVAILABLE)
        reply = bridge.call(OP_PURCHASE, product_id, PURCHASE_TIMEOUT_MS)
        if reply.status == TIMEOUT:
            return result(ActionStatus.PENDING)                     # peut aboutir plus tard ; ``Transaction.updates`` relira
        if reply.status == SHUTDOWN:
            return result(ActionStatus.UNAVAILABLE)
        if reply.status != OK:
            return result(ActionStatus.FAILED)
        outcome = reply.payload.get("outcome")
        if outcome == "cancelled":
            return result(ActionStatus.CANCELLED)
        if outcome == "pending":
            return result(ActionStatus.PENDING)
        if outcome != "success":
            return result(ActionStatus.NOT_CONFIRMED if outcome == "unverified" else ActionStatus.FAILED)
        if reply.payload.get("verified") is not True or reply.payload.get("productID") != product_id:
            return result(ActionStatus.NOT_CONFIRMED)               # un succès non vérifié ne vaut rien
        self._refresh()                                             # relecture complète : seul un droit vérifié débloque
        return result(ActionStatus.DONE if self._paid() else ActionStatus.NOT_CONFIRMED)

    def _restore(self) -> ActionResult:
        bridge = self._bridge()
        if bridge is None:
            return ActionResult(ActionKind.RESTORE, ActionStatus.UNAVAILABLE)
        reply = bridge.call(OP_SYNC, "", RESTORE_TIMEOUT_MS)
        if reply.status == SHUTDOWN:
            return ActionResult(ActionKind.RESTORE, ActionStatus.UNAVAILABLE)
        if reply.status != OK:
            return ActionResult(ActionKind.RESTORE, ActionStatus.FAILED)
        self._refresh()
        return ActionResult(ActionKind.RESTORE, ActionStatus.DONE if self._paid() else ActionStatus.NOTHING_TO_RESTORE)


__all__ = [
    "ActionKind", "ActionResult", "ActionStatus", "MAX_PRICE_CHARS", "MAX_PRODUCTS", "OFFER_LIFETIME", "OFFER_MONTHLY", "OFFER_YEARLY",
    "Offer", "PAID_KINDS", "ProductInfo", "StoreActions", "offers", "parse_products",
]
