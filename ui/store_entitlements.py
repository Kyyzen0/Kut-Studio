"""Branchement Qt du pont StoreKit (ADR-0013, étape 13) : installe le service de droits et notifie l'interface par un signal.

Utilisé **seulement** dans la construction Store (``core.build_flavor.is_store()``, voir ``EntitlementsMixin``). Hors Store ce
module n'est même pas importé.

* Le service de droits est installé **avant** toute lecture : tant que le pont n'a rien publié (ou s'il est absent), l'état est
  ``UNKNOWN`` (lecture seule), jamais un déblocage.
* Aucun appel StoreKit sur le thread Qt : le thread de travail de :class:`StoreKitEntitlementSource` lit le pont ; il appelle
  ``on_change`` depuis un thread **Python**, qui émet :attr:`state_changed` (liaison en file d'attente vers le thread Qt).
* L'arrêt du pont se fait à la sortie de l'application (``aboutToQuit``).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from core.entitlements import EntitlementService, EntitlementState, EntitlementStore, install_service
from core.entitlements.storekit_bridge import StoreBridgeError, StoreKitBridge, bundled_dylib_path
from core.entitlements.storekit_actions import ActionKind, ActionResult, ProductInfo
from core.entitlements.storekit_source import StoreKitEntitlementSource
from core.entitlements.storekit_verifier import StoreKitVerifier
from core.platform_paths import user_config_dir

STATE_FILE = "entitlements.json"
KEY_FILE = "entitlements.key"


def bundled_bridge_loader() -> StoreKitBridge:
    """Charge la bibliothèque du bundle (jamais par variable d'environnement) ; ``StoreBridgeError`` si absente ou invalide."""
    path = bundled_dylib_path()
    if path is None:
        raise StoreBridgeError("bridge outside a bundle")  # i18n-ignore: message technique, jamais affiché
    return StoreKitBridge.load(path)


class StoreEntitlementsController(QObject):
    """Le service de droits du build Store et sa source StoreKit. ``state_changed`` : l'état a changé (thread Qt)."""

    state_changed = Signal()
    action_finished = Signal(object)
    """Résultat d'un chargement de produits, d'un achat ou d'une restauration (:class:`ActionResult`), côté thread Qt."""

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        directory: Path | None = None,
        clock: Callable[[], float] = time.time,
        bridge_loader: Callable[[], StoreKitBridge] = bundled_bridge_loader,
        **source_options,
    ) -> None:
        super().__init__(parent)
        self.products: dict[str, ProductInfo] = {}
        """Prix localisés lus de StoreKit (vide tant que le chargement n'a pas abouti : l'interface dit « prix indisponible »)."""
        self.products_requested = False
        base = Path(directory) if directory is not None else user_config_dir()
        self.verifier = StoreKitVerifier(base / KEY_FILE, clock)
        self.service = EntitlementService(EntitlementStore(base / STATE_FILE, self.verifier), clock)
        install_service(self.service)
        self.source = StoreKitEntitlementSource(
            self.service, self.verifier, bridge_loader, on_change=self.state_changed.emit, on_action=self._on_action,
            **source_options,
        )
        self.action_finished.connect(self._remember_products)             # relié en premier : les écrans voient les prix déjà à jour

    def state(self) -> EntitlementState:
        return self.service.state()

    # -- produits, achat, restauration (étape 15) : jamais sur le thread Qt, résultat par ``action_finished`` ------------------

    def _on_action(self, result: ActionResult) -> None:
        try:
            self.action_finished.emit(result)                               # appelé depuis un fil Python : liaison en file d'attente
        except RuntimeError:                                                # contrôleur déjà détruit (sortie de l'application)
            pass

    def _remember_products(self, result: ActionResult) -> None:
        if result.kind is ActionKind.PRODUCTS and result.products:
            self.products = dict(result.products)

    def load_products(self) -> None:
        """Demande les prix localisés (une fois par session tant que la réponse est vide)."""
        if self.products:
            return
        self.products_requested = True
        self.source.actions.request_products()

    def buy(self, product_id: str) -> bool:
        """Demande l'achat d'un produit du catalogue. Ne débloque rien : l'état ne change que par relecture vérifiée."""
        return self.source.actions.request_purchase(product_id)

    def restore(self) -> bool:
        return self.source.actions.request_restore()

    def start(self) -> None:
        """Démarre la lecture en arrière-plan et prévoit l'arrêt à la sortie de l'application."""
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.stop)
        self.source.start()

    def stop(self) -> None:
        self.source.stop()


__all__ = ["KEY_FILE", "STATE_FILE", "StoreEntitlementsController", "bundled_bridge_loader"]
