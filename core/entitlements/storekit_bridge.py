"""Le pont StoreKit 2 vu de Python : contrat ``ctypes`` de ``libkutstore.dylib`` (ADR-0013, étape 13).

La bibliothèque est écrite en Swift (``packaging/store-spike/storekit/KutStoreBridge.swift``, interface C ``kutstore.h``).
Règles, toutes testées :

* **Aucun rappel.** Python appelle des fonctions **bloquantes** ; ``ctypes`` relâche le GIL pendant l'appel. Ces appels se font
  depuis un thread de travail Python, **jamais** depuis le thread Qt (un appel dure ~0,4 s hors Xcode). Les mises à jour de
  transactions se **sondent** (:meth:`StoreKitBridge.poll_update`) ; rien ne rappelle Python depuis un thread natif.
* **Chemin fixe.** La bibliothèque se cherche dans le bundle (``Contents/Frameworks``), jamais par variable d'environnement,
  ``PATH`` ni ``DYLD_*`` (:func:`bundled_dylib_path`). Absente, invalide ou d'une autre version d'ABI : :class:`StoreBridgeError`,
  que l'appelant traduit par un état **inconnu**, jamais par un déblocage.
* **ABI.** ``kutstore_abi_version()`` est lu avant toute autre fonction ; une autre valeur que :data:`ABI_VERSION` refuse la
  bibliothèque (aucune autre fonction n'est liée).
* **Bornes.** Argument limité à :data:`MAX_ARG_BYTES` avant de franchir la frontière, délai borné, réponse lue avec une
  taille maximale (:data:`MAX_RESPONSE_BYTES`) ; l'opération doit être connue.
* **Mémoire.** La chaîne rendue par le pont est copiée puis libérée **une fois** (``kutstore_free``), dans un ``finally``.
* **Pas de donnée utilisateur dans les erreurs.** Les messages sont fixes ; une réponse en erreur est réduite à un code court
  (``{"error": "<mot>"}``) : le détail natif (qui peut citer un identifiant) n'est ni gardé ni journalisé.
* **Arrêt.** :meth:`StoreKitBridge.shutdown` est idempotent et réveille le sondage en attente. L'état d'arrêt du pont est global
  au processus : on n'arrête qu'à la sortie de l'application.
"""

from __future__ import annotations

import ctypes
import json
import re
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

ABI_VERSION = 1
LIBRARY_NAME = "libkutstore.dylib"

# kutstore_status (kutstore.h)
OK, TIMEOUT, BAD_ARGUMENT, STOREKIT_ERROR, UNVERIFIED, SHUTDOWN, INTERNAL, POLL_TIMEOUT = range(8)
# kutstore_op (kutstore.h)
OP_ENTITLEMENTS, OP_APP_TRANSACTION, OP_SYNC, OP_PRODUCTS, OP_START_UPDATES = 1, 2, 3, 4, 5
KNOWN_OPS = frozenset({OP_ENTITLEMENTS, OP_APP_TRANSACTION, OP_SYNC, OP_PRODUCTS, OP_START_UPDATES})

MAX_ARG_BYTES = 8192
MAX_RESPONSE_BYTES = 1 << 20
"""Une réponse légitime fait quelques centaines d'octets par droit ; au-delà d'un mégaoctet elle est jetée."""
MIN_TIMEOUT_MS, MAX_TIMEOUT_MS = 1, 120_000
DEFAULT_CALL_TIMEOUT_MS = 15_000

_ERROR_WORD = re.compile(r"[a-z_]{1,40}")


class StoreBridgeError(RuntimeError):
    """Pont absent, invalide, d'une autre ABI ou plateforme sans StoreKit. Le message est fixe (aucune donnée de l'utilisateur)."""


@dataclass(frozen=True)
class BridgeResult:
    """``status`` (``kutstore_status``) et ``payload`` (JSON décodé). Hors ``OK``, ``payload`` ne porte que ``{"error": "<mot>"}``."""

    status: int
    payload: dict

    @property
    def ok(self) -> bool:
        return self.status == OK


def bundled_dylib_path(executable: str | None = None, *, frozen: bool | None = None) -> Path | None:
    """Où la bibliothèque doit se trouver dans le bundle : ``<App>.app/Contents/Frameworks/libkutstore.dylib``.

    ``None`` hors d'un bundle figé (sources, CI) : le pont n'est alors pas chargé et l'état reste inconnu. Aucune variable
    d'environnement n'est lue.
    """
    if sys.platform != "darwin":
        return None
    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    if not is_frozen:
        return None
    exe = Path(sys.executable if executable is None else executable)
    return exe.resolve().parent.parent / "Frameworks" / LIBRARY_NAME


def _bounded_text(libc: ctypes.CDLL, pointer: int) -> bytes | None:
    """Les octets de la chaîne C à ``pointer``, ou ``None`` si elle dépasse :data:`MAX_RESPONSE_BYTES`."""
    length = int(libc.strnlen(ctypes.c_void_p(pointer), MAX_RESPONSE_BYTES + 1))
    if length > MAX_RESPONSE_BYTES:
        return None
    return ctypes.string_at(pointer, length)


class StoreKitBridge:
    """``libkutstore.dylib`` chargée et vérifiée. Se construit par :meth:`load`."""

    def __init__(self, library: ctypes.CDLL, libc: ctypes.CDLL) -> None:
        libc.strnlen.restype = ctypes.c_size_t
        libc.strnlen.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        self._lib = library
        self._libc = libc
        self._lock = threading.Lock()
        self._closed = False
        self.abi_version = ABI_VERSION

    @classmethod
    def load(cls, path: str | Path) -> StoreKitBridge:
        """Charge ``path`` et vérifie l'ABI avant tout autre appel. ``StoreBridgeError`` à la moindre anomalie."""
        if sys.platform != "darwin":
            raise StoreBridgeError("StoreKit n'existe que sous macOS")
        target = Path(path)
        if not target.is_file():
            raise StoreBridgeError("bibliothèque du pont absente")
        try:
            lib = ctypes.CDLL(str(target))
            libc = ctypes.CDLL(None)
        except OSError as error:                                   # le détail du système peut citer un chemin : non recopié
            raise StoreBridgeError("bibliothèque du pont non chargeable") from error
        try:
            lib.kutstore_abi_version.restype = ctypes.c_int32
            lib.kutstore_abi_version.argtypes = []
            version = int(lib.kutstore_abi_version())
        except AttributeError as error:
            raise StoreBridgeError("bibliothèque sans version d'interface") from error
        if version != ABI_VERSION:
            raise StoreBridgeError("version d'interface du pont différente de celle attendue")
        try:
            lib.kutstore_call.restype = ctypes.c_int32
            lib.kutstore_call.argtypes = [ctypes.c_int32, ctypes.c_char_p, ctypes.c_int32, ctypes.POINTER(ctypes.c_void_p)]
            lib.kutstore_poll_update.restype = ctypes.c_int32
            lib.kutstore_poll_update.argtypes = [ctypes.c_int32, ctypes.POINTER(ctypes.c_void_p)]
            lib.kutstore_free.restype = None
            lib.kutstore_free.argtypes = [ctypes.c_void_p]
            lib.kutstore_shutdown.restype = None
            lib.kutstore_shutdown.argtypes = []
        except AttributeError as error:
            raise StoreBridgeError("bibliothèque incomplète") from error
        return cls(lib, libc)

    # -- lecture de la réponse ----------------------------------------------------------------------------------------------

    def _take(self, pointer: ctypes.c_void_p, status: int) -> BridgeResult:
        """Copie puis libère la chaîne du pont (le pont alloue, nous libérons une fois)."""
        address = pointer.value
        if not address:
            return BridgeResult(status, {})
        try:
            raw = _bounded_text(self._libc, address)
        finally:
            self._lib.kutstore_free(pointer)
        if raw is None:
            return BridgeResult(status if status != OK else INTERNAL, {"error": "response_too_large"})
        try:
            data = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            return BridgeResult(status if status != OK else INTERNAL, {"error": "invalid_json"})
        if not isinstance(data, dict):
            return BridgeResult(status if status != OK else INTERNAL, {"error": "invalid_json"})
        if status != OK:
            word = data.get("error")
            safe = word if isinstance(word, str) and _ERROR_WORD.fullmatch(word) else "error"
            return BridgeResult(status, {"error": safe})
        return BridgeResult(status, data)

    # -- appels -------------------------------------------------------------------------------------------------------------

    def call(self, op: int, arg: str = "", timeout_ms: int = DEFAULT_CALL_TIMEOUT_MS) -> BridgeResult:
        """Appel BLOQUANT : à faire depuis un thread de travail. Ne lève pas pour une erreur StoreKit (voir ``status``)."""
        if op not in KNOWN_OPS or not isinstance(arg, str):
            return BridgeResult(BAD_ARGUMENT, {"error": "bad_argument"})
        data = arg.encode("utf-8", "replace")
        if len(data) > MAX_ARG_BYTES:                              # refusé avant la frontière ; l'entrée n'est pas recopiée
            return BridgeResult(BAD_ARGUMENT, {"error": "bad_argument"})
        timeout = max(MIN_TIMEOUT_MS, min(int(timeout_ms), MAX_TIMEOUT_MS))
        out = ctypes.c_void_p()
        status = int(self._lib.kutstore_call(op, data, timeout, ctypes.byref(out)))
        return self._take(out, status)

    def poll_update(self, timeout_ms: int = 1000) -> BridgeResult:
        """Attend un événement de ``Transaction.updates`` (BLOQUANT, thread de travail). ``POLL_TIMEOUT`` : rien dans le délai."""
        timeout = max(MIN_TIMEOUT_MS, min(int(timeout_ms), MAX_TIMEOUT_MS))
        out = ctypes.c_void_p()
        status = int(self._lib.kutstore_poll_update(timeout, ctypes.byref(out)))
        return self._take(out, status)

    def shutdown(self) -> None:
        """Annule les tâches et réveille le sondage en attente. Idempotent, sûr depuis n'importe quel thread."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._lib.kutstore_shutdown()


__all__ = [
    "ABI_VERSION", "BAD_ARGUMENT", "BridgeResult", "DEFAULT_CALL_TIMEOUT_MS", "INTERNAL", "KNOWN_OPS", "LIBRARY_NAME",
    "MAX_ARG_BYTES", "MAX_RESPONSE_BYTES", "OK", "OP_APP_TRANSACTION", "OP_ENTITLEMENTS", "OP_PRODUCTS", "OP_START_UPDATES",
    "OP_SYNC", "POLL_TIMEOUT", "SHUTDOWN", "STOREKIT_ERROR", "StoreBridgeError", "StoreKitBridge", "TIMEOUT", "UNVERIFIED",
    "bundled_dylib_path",
]
