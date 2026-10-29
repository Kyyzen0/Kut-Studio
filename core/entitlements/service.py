"""Le service de droits : l'état courant, calculé à partir de l'enregistrement signé et d'une horloge **injectée**.

Ce que le service garantit :

* l'état vient **toujours** d'un enregistrement dont la signature est valide ; sinon ``UNKNOWN`` (jamais un essai neuf) ;
* l'horloge est injectée (``clock``) : aucun ``time.time()`` direct ;
* **horloge reculée** : le calcul utilise ``max(horloge, plus grande date vue)``. Reculer la date ne rallonge ni l'essai ni
  l'abonnement ; l'état porte ``clock_rollback=True`` pour qu'on le dise à l'utilisateur. La plus grande date vue est écrite
  (signée) au plus une fois par ``persist_interval``. Une nouvelle vérification du pont (:meth:`publish`) refait foi : sa
  ``verified_at`` vient d'Apple et remplace la plus grande date vue, ce qui guérit une horloge poussée trop loin par erreur ;
* l'essai : ``trial_started_at`` (donné par Apple) + ``trial_days`` ; au jour J il reste ``trial_days`` jours, à J+14 il en
  reste 1, à J+15 l'essai est fini (fin exclue) ;
* un abonnement échu mais dans le délai de grâce reste ``SUBSCRIBER`` (``in_grace``) ; au-delà, ``UNKNOWN(stale)`` : on ne sait
  pas s'il a été renouvelé, on ne dit pas « expiré ».
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from threading import RLock

from .model import (
    DAY_SECONDS, DEFAULT_POLICY, REASON_STALE, REASON_UNREADABLE, EntitlementKind, EntitlementPolicy, EntitlementState, can_edit,
    can_export, can_open, enforced,
)
from .store import EntitlementStore, LoadResult, VerifiedEntitlement

LOGGER = logging.getLogger("kut_studio.entitlements")


class EntitlementService:
    """Un magasin signé, une horloge, une politique : l'état et ce qu'il permet.

    ``enforced`` (défaut ``None`` : :func:`core.entitlements.model.enforced`) : source injectable de « le contrôle est-il exigé ? ».
    """

    def __init__(
        self,
        store: EntitlementStore,
        clock: Callable[[], float],
        policy: EntitlementPolicy = DEFAULT_POLICY,
        *,
        enforced: Callable[[], bool] | None = None,
    ) -> None:
        self._store = store
        self._clock = clock
        self.policy = policy
        self._enforced = enforced
        self._lock = RLock()
        self._loaded: LoadResult | None = None
        self._session_max = float("-inf")

    # -- exigé ou non -------------------------------------------------------------------------------------------------------

    def is_enforced(self) -> bool:
        return bool(self._enforced()) if self._enforced is not None else enforced()

    # -- l'état -------------------------------------------------------------------------------------------------------------

    def refresh(self) -> None:
        """Relit le fichier (le prochain :meth:`state` repart de lui)."""
        with self._lock:
            self._loaded = None

    def state(self) -> EntitlementState:
        """L'état à cet instant."""
        with self._lock:
            loaded = self._loaded if self._loaded is not None else self._store.load()
            self._loaded = loaded
            record = loaded.record
            if record is None:
                return EntitlementState(EntitlementKind.UNKNOWN, reason=loaded.reason)
            now = float(self._clock())
            watermark = max(record.watermark, self._session_max)
            rollback = now + self.policy.clock_tolerance < watermark
            effective = max(now, watermark)
            self._session_max = max(self._session_max, effective)
            if effective - record.watermark >= self.policy.persist_interval:
                self._persist_watermark(record, effective)
            return self._compute(record, effective, rollback)

    def _compute(self, record: VerifiedEntitlement, now: float, rollback: bool) -> EntitlementState:
        policy = self.policy
        kind = record.kind
        if kind is EntitlementKind.LIFETIME:
            return EntitlementState(kind, clock_rollback=rollback)
        if kind is EntitlementKind.EXPIRED:
            return EntitlementState(kind, reason="expired", clock_rollback=rollback)
        if kind is EntitlementKind.TRIAL:
            started = record.trial_started_at
            if started is None:                                   # exclu par VerifiedEntitlement ; jamais un essai neuf
                return EntitlementState(EntitlementKind.UNKNOWN, reason=REASON_UNREADABLE, clock_rollback=rollback)
            remaining = started + policy.trial_days * DAY_SECONDS - now
            if remaining <= 0:
                return EntitlementState(EntitlementKind.EXPIRED, reason="trial_ended", clock_rollback=rollback)
            days = max(1, min(policy.trial_days, math.ceil(remaining / DAY_SECONDS)))
            return EntitlementState(kind, days_left=days, clock_rollback=rollback)
        expires = record.expires_at
        if expires is None:                                       # idem
            return EntitlementState(EntitlementKind.UNKNOWN, reason=REASON_UNREADABLE, clock_rollback=rollback)
        if now <= expires:
            return EntitlementState(kind, expires_at=expires, clock_rollback=rollback)
        if now <= expires + policy.grace_seconds:
            return EntitlementState(kind, expires_at=expires, in_grace=True, clock_rollback=rollback)
        return EntitlementState(EntitlementKind.UNKNOWN, expires_at=expires, reason=REASON_STALE, clock_rollback=rollback)

    def _persist_watermark(self, record: VerifiedEntitlement, effective: float) -> None:
        updated = record.with_last_seen(effective)
        try:
            self._store.save(updated)
        except OSError as error:
            LOGGER.warning("Droits : date vue non écrite (%s)", error)
            return                                              # le calcul reste correct pour cette session (``_session_max``)
        self._loaded = LoadResult(updated)

    # -- ce que le pont publie ----------------------------------------------------------------------------------------------

    def publish(self, verified: VerifiedEntitlement) -> None:
        """Enregistre un état **vérifié par le pont** (étape 13). Sa ``verified_at`` remplace la plus grande date vue."""
        with self._lock:
            record = verified.with_last_seen(verified.verified_at)
            self._store.save(record)
            self._loaded = LoadResult(record)
            self._session_max = float("-inf")

    # -- ce que l'état permet -----------------------------------------------------------------------------------------------

    def can_open(self) -> bool:
        return can_open(self.state())

    def can_edit(self) -> bool:
        return can_edit(self.state(), self.policy, is_enforced=self.is_enforced())

    def can_export(self) -> bool:
        return can_export(self.state(), self.policy, is_enforced=self.is_enforced())


__all__ = ["EntitlementService"]
