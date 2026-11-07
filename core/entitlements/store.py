"""L'état de droits persisté : un enregistrement vérifié, dans une enveloppe signée, écrite atomiquement.

Un :class:`VerifiedEntitlement` est ce que le pont (étape 13) a **vérifié auprès d'Apple** (``verified_at`` doit venir de la date
signée de la transaction, jamais de l'horloge locale) ; ``last_seen`` est la plus grande date que l'application a vue, ce qui
permet de refuser une horloge reculée. Le fichier est borné en taille avant toute lecture ; une enveloppe mal formée, tronquée ou
dont la signature ne correspond pas se lit comme ``UNKNOWN`` (jamais comme un essai neuf).
"""

from __future__ import annotations

import base64
import binascii
import json
import math
from dataclasses import dataclass
from pathlib import Path

from ..atomic_io import atomic_write_json
from .model import (
    REASON_BAD_SIGNATURE, REASON_MISSING, REASON_UNREADABLE, EntitlementKind,
)
from .verifier import EntitlementVerifier

ENVELOPE_VERSION = 1
MAX_FILE_BYTES = 16_384
"""Au-delà, le fichier n'est pas lu : un enregistrement légitime en fait quelques centaines d'octets."""

_STORED_KINDS = (EntitlementKind.TRIAL, EntitlementKind.SUBSCRIBER, EntitlementKind.LIFETIME, EntitlementKind.EXPIRED)


@dataclass(frozen=True)
class VerifiedEntitlement:
    """Ce que le pont a vérifié. ``UNKNOWN`` n'est jamais un état vérifié : il ne se stocke pas.

    Attributes:
        kind: ``TRIAL`` (rien d'acheté, l'essai a commencé à ``trial_started_at``), ``SUBSCRIBER`` (jusqu'à ``expires_at``),
            ``LIFETIME`` ou ``EXPIRED``.
        verified_at: date (secondes depuis l'époque) de la vérification, **issue de la transaction signée**.
        trial_started_at: début de l'essai, issu d'Apple (``AppTransaction.originalPurchaseDate``), requis pour ``TRIAL``.
        expires_at: échéance de l'abonnement, requise pour ``SUBSCRIBER``.
        last_seen: plus grande date vue par l'application (par défaut ``verified_at``).
    """

    kind: EntitlementKind
    verified_at: float
    trial_started_at: float | None = None
    expires_at: float | None = None
    last_seen: float | None = None

    def __post_init__(self) -> None:
        if self.kind not in _STORED_KINDS:
            raise ValueError(f"Un état {self.kind.value!r} ne se stocke pas.")
        for name in ("verified_at", "trial_started_at", "expires_at", "last_seen"):
            value = getattr(self, name)
            if value is not None and not (isinstance(value, (int, float)) and math.isfinite(value)):
                raise ValueError(f"{name} doit être une date finie.")
        if self.kind is EntitlementKind.TRIAL and self.trial_started_at is None:
            raise ValueError("Un essai exige son début (trial_started_at).")
        if self.kind is EntitlementKind.SUBSCRIBER and self.expires_at is None:
            raise ValueError("Un abonnement exige son échéance (expires_at).")

    @property
    def watermark(self) -> float:
        return self.verified_at if self.last_seen is None else self.last_seen

    def with_last_seen(self, last_seen: float) -> VerifiedEntitlement:
        return VerifiedEntitlement(self.kind, self.verified_at, self.trial_started_at, self.expires_at, float(last_seen))


def _payload_bytes(record: VerifiedEntitlement) -> bytes:
    data = {
        "kind": record.kind.value, "verified_at": record.verified_at, "trial_started_at": record.trial_started_at,
        "expires_at": record.expires_at, "last_seen": record.watermark,
    }
    return json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def encode_envelope(record: VerifiedEntitlement, verifier: EntitlementVerifier) -> dict[str, object]:
    """L'enveloppe signée de ``record``."""
    payload = _payload_bytes(record)
    return {
        "v": ENVELOPE_VERSION,
        "payload": base64.b64encode(payload).decode("ascii"),
        "signature": base64.b64encode(verifier.sign(payload)).decode("ascii"),
    }


@dataclass(frozen=True)
class LoadResult:
    """Un enregistrement vérifié, ou la raison pour laquelle il n'y en a pas (``REASON_*``)."""

    record: VerifiedEntitlement | None
    reason: str = ""


def decode_envelope(data: object, verifier: EntitlementVerifier) -> LoadResult:
    """Lit une enveloppe : signature d'abord, contenu ensuite. Ne lève pas."""
    if not isinstance(data, dict) or data.get("v") != ENVELOPE_VERSION:
        return LoadResult(None, REASON_UNREADABLE)
    try:
        payload = base64.b64decode(str(data["payload"]), validate=True)
        signature = base64.b64decode(str(data["signature"]), validate=True)
    except (KeyError, binascii.Error, ValueError):
        return LoadResult(None, REASON_UNREADABLE)
    if len(payload) > MAX_FILE_BYTES or not verifier.verify(payload, signature):
        return LoadResult(None, REASON_BAD_SIGNATURE)
    try:
        body = json.loads(payload.decode("utf-8"))
        kind = EntitlementKind(body["kind"])
        record = VerifiedEntitlement(
            kind, float(body["verified_at"]),
            None if body.get("trial_started_at") is None else float(body["trial_started_at"]),
            None if body.get("expires_at") is None else float(body["expires_at"]),
            float(body["last_seen"]),
        )
    except (KeyError, TypeError, ValueError, UnicodeDecodeError):
        return LoadResult(None, REASON_UNREADABLE)           # signé mais incompréhensible : un pont d'une autre version
    return LoadResult(record)


class EntitlementStore:
    """Le fichier d'état dans le conteneur de l'application (son chemin est donné par l'appelant)."""

    def __init__(self, path: str | Path, verifier: EntitlementVerifier) -> None:
        self.path = Path(path)
        self.verifier = verifier

    def load(self) -> LoadResult:
        """L'enregistrement vérifié, ou la raison de son absence. Ne lève pas pour un fichier abîmé."""
        try:
            size = self.path.stat().st_size
        except FileNotFoundError:
            return LoadResult(None, REASON_MISSING)
        except OSError:
            return LoadResult(None, REASON_UNREADABLE)
        if size > MAX_FILE_BYTES:
            return LoadResult(None, REASON_UNREADABLE)
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):                         # UnicodeDecodeError est une ValueError
            return LoadResult(None, REASON_UNREADABLE)
        return decode_envelope(data, self.verifier)

    def save(self, record: VerifiedEntitlement) -> None:
        """Écrit ``record`` (atomiquement). ``OSError`` si le conteneur n'est pas inscriptible."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.path, encode_envelope(record, self.verifier))


__all__ = [
    "ENVELOPE_VERSION", "MAX_FILE_BYTES", "EntitlementStore", "LoadResult", "VerifiedEntitlement", "decode_envelope",
    "encode_envelope",
]
