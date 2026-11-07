"""La source d'état StoreKit : un thread de travail Python qui lit le pont et publie dans le service (ADR-0013, étape 13).

* **Rien sur le thread Qt.** Le chargement du pont, les lectures (:meth:`StoreKitVerifier.fetch`), la restauration et le sondage de
  ``Transaction.updates`` se font dans un thread de travail Python (``daemon`` : il ne retient jamais la sortie). L'interface lit
  seulement :meth:`EntitlementService.state`, qui n'appelle jamais StoreKit.
* **Pas de rappel natif.** Les mises à jour (achat, renouvellement, remboursement, autre appareil) se **sondent**
  (``poll_update``, délai court) ; chaque événement vérifié déclenche une relecture complète. ``on_change`` est appelé depuis ce
  thread Python (jamais depuis un thread natif) : l'interface y branche un signal Qt.
* **Échec = rien de publié.** Pont absent ou invalide, délai, erreur, réponse abîmée : l'état reste celui que le service tire du
  dernier enregistrement signé (règle de ``docs/entitlements.md``), sinon ``UNKNOWN``. Jamais un déblocage.
* **Arrêt.** :meth:`stop` est idempotent : il arrête le pont (réveille le sondage) puis attend le thread.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from .model import EntitlementState
from .service import EntitlementService
from .storekit_bridge import (
    OK, OP_START_UPDATES, OP_SYNC, POLL_TIMEOUT, SHUTDOWN, StoreBridgeError, StoreKitBridge,
)
from .storekit_verifier import REASON_OK, StoreKitVerifier

LOGGER = logging.getLogger("kut_studio.entitlements")

REFRESH_INTERVAL_S = 6 * 3600.0
"""Relecture périodique en plus des événements (un abonnement échoit sans événement)."""
POLL_TIMEOUT_MS = 5_000
RESTORE_TIMEOUT_MS = 60_000
ERROR_BACKOFF_S = 1.0
RETRY_INITIAL_S = 2.0
RETRY_MAX_S = 300.0
"""Après une lecture ratée : nouvel essai après 2 s, 4 s, 8 s… plafonné à 5 min ; remis à zéro par un succès."""
REASON_BRIDGE_UNAVAILABLE = "bridge_unavailable"


class StoreKitEntitlementSource:
    """Un service de droits, un vérificateur, un chargeur de pont, une horloge monotone injectable."""

    def __init__(
        self,
        service: EntitlementService,
        verifier: StoreKitVerifier,
        bridge_loader: Callable[[], StoreKitBridge],
        *,
        on_change: Callable[[], None] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        refresh_interval: float = REFRESH_INTERVAL_S,
        poll_timeout_ms: int = POLL_TIMEOUT_MS,
        error_backoff: float = ERROR_BACKOFF_S,
        retry_initial: float = RETRY_INITIAL_S,
        retry_max: float = RETRY_MAX_S,
    ) -> None:
        self._service = service
        self._verifier = verifier
        self._loader = bridge_loader
        self._on_change = on_change
        self._monotonic = monotonic
        self._refresh_interval = refresh_interval
        self._poll_timeout_ms = poll_timeout_ms
        self._error_backoff = error_backoff
        self._retry_initial = retry_initial
        self._retry_max = retry_max
        self._retry_delay = 0.0
        self._next_attempt = 0.0
        self.retry_delays: list[float] = []
        """Délais successifs programmés après un échec (diagnostic, tests)."""
        self._stop = threading.Event()
        self._refresh_requested = threading.Event()
        self._restore_requested = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._bridge: StoreKitBridge | None = None
        self._last_state: EntitlementState | None = None
        self.last_reason = ""
        """Raison de la dernière lecture (``REASON_*``) : diagnostic, jamais de donnée de l'utilisateur."""

    # -- cycle de vie -------------------------------------------------------------------------------------------------------

    def start(self) -> None:
        """Démarre le thread de travail (idempotent ; sans effet après :meth:`stop`)."""
        with self._lock:
            if self._thread is not None or self._stop.is_set():
                return
            self._thread = threading.Thread(target=self._run, name="kut-storekit", daemon=True)
            self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Arrête le pont (réveille le sondage) puis attend le thread. Idempotent."""
        self._stop.set()
        with self._lock:
            bridge, thread = self._bridge, self._thread
        if bridge is not None:
            bridge.shutdown()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)

    @property
    def running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def request_refresh(self) -> None:
        """Demande une relecture (le thread la fait au prochain tour du sondage ; sans effet si arrêté)."""
        self._refresh_requested.set()

    def request_restore(self) -> None:
        """Demande « Restaurer les achats » (``AppStore.sync()``) puis une relecture ; en arrière-plan."""
        self._restore_requested.set()

    # -- le thread ----------------------------------------------------------------------------------------------------------

    def _run(self) -> None:
        bridge: StoreKitBridge | None = None
        try:
            try:
                bridge = self._loader()
            except StoreBridgeError:
                self.last_reason = REASON_BRIDGE_UNAVAILABLE       # état inconnu : jamais un déblocage
                LOGGER.warning("Droits : pont StoreKit indisponible")
                return
            with self._lock:
                self._bridge = bridge
            if self._stop.is_set():
                return
            self._refresh()
            if not self._stop.is_set():
                bridge.call(OP_START_UPDATES)
            self._loop(bridge)
        except Exception as error:                                  # noqa: BLE001 - un thread de travail ne meurt jamais en silence
            LOGGER.warning("Droits : lecteur StoreKit arrêté (%s)", type(error).__name__)
        finally:
            if bridge is not None and self._stop.is_set():
                bridge.shutdown()

    def _loop(self, bridge: StoreKitBridge) -> None:
        while not self._stop.is_set():
            if self._restore_requested.is_set():
                self._restore_requested.clear()
                bridge.call(OP_SYNC, "", RESTORE_TIMEOUT_MS)
                self._refresh_requested.set()
            if self._refresh_requested.is_set() or self._monotonic() >= self._next_attempt:
                self._refresh_requested.clear()
                self._refresh()
            if self._stop.is_set():
                break
            wait_ms = int((self._next_attempt - self._monotonic()) * 1000) + 1     # ne pas dormir au-delà du prochain essai
            result = bridge.poll_update(max(1, min(self._poll_timeout_ms, wait_ms)))
            if result.status == SHUTDOWN:
                break
            if result.status == POLL_TIMEOUT:
                continue
            if result.status != OK:
                self._stop.wait(self._error_backoff)                # pas de boucle serrée sur une erreur durable
                continue
            if result.payload.get("verified") is True:              # une transaction non vérifiée ne change rien
                self._refresh_requested.set()

    def _refresh(self) -> None:
        bridge = self._bridge
        if bridge is None:
            return
        outcome = self._verifier.fetch(bridge)
        self.last_reason = outcome.reason
        if outcome.record is not None and not self._stop.is_set():
            try:
                self._service.publish(outcome.record)
            except OSError as error:                                # conteneur non inscriptible : l'état précédent reste
                LOGGER.warning("Droits : état non écrit (%s)", type(error).__name__)
                self.last_reason = "not_persisted"
        elif outcome.reason != REASON_OK:
            LOGGER.info("Droits : lecture StoreKit sans résultat (%s)", outcome.reason)
        self._schedule_next(self.last_reason == REASON_OK)
        self._notify_if_changed()

    def _schedule_next(self, success: bool) -> None:
        """Succès : prochaine relecture dans ``refresh_interval``. Échec : repli croissant plafonné, indépendant de cet intervalle."""
        if success:
            self._retry_delay = 0.0
            self._next_attempt = self._monotonic() + self._refresh_interval
            return
        self._retry_delay = self._retry_initial if self._retry_delay <= 0 else min(self._retry_delay * 2, self._retry_max)
        self.retry_delays.append(self._retry_delay)
        self._next_attempt = self._monotonic() + self._retry_delay

    def _notify_if_changed(self) -> None:
        state = self._service.state()
        if state != self._last_state:
            self._last_state = state
            if self._on_change is not None:
                try:
                    self._on_change()
                except Exception as error:                          # noqa: BLE001 - un abonné défectueux n'arrête pas le lecteur
                    LOGGER.warning("Droits : abonné au changement en erreur (%s)", type(error).__name__)


__all__ = [
    "ERROR_BACKOFF_S", "POLL_TIMEOUT_MS", "RETRY_INITIAL_S", "RETRY_MAX_S", "REASON_BRIDGE_UNAVAILABLE", "REFRESH_INTERVAL_S", "RESTORE_TIMEOUT_MS",
    "StoreKitEntitlementSource",
]
