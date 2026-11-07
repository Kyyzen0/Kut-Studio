"""Contrat Python (ctypes) du pont StoreKit 2 : MODULE JETABLE du spike ADR-0013 étape 13, pas du produit.

À appeler depuis un thread de travail, JAMAIS depuis le thread Qt principal : les appels bloquent (le GIL est relâché).
Le version d'ABI est vérifiée AVANT tout autre appel ; un décalage lève ``StoreBridgeError``.
"""
from __future__ import annotations

import ctypes
import json
import sys
from pathlib import Path

ABI_VERSION = 1
OK, TIMEOUT, BAD_ARGUMENT, STOREKIT_ERROR, UNVERIFIED, SHUTDOWN, INTERNAL, POLL_TIMEOUT = range(8)
OP_ENTITLEMENTS, OP_APP_TRANSACTION, OP_SYNC, OP_PRODUCTS, OP_START_UPDATES = 1, 2, 3, 4, 5
MAX_ARG_BYTES = 8192


class StoreBridgeError(RuntimeError):
    """Pont absent, ABI différente ou plateforme sans StoreKit."""


class StoreBridge:
    def __init__(self, path: str | Path):
        if sys.platform != "darwin":
            raise StoreBridgeError("StoreKit n'existe que sous macOS")
        try:
            lib = ctypes.CDLL(str(path))
        except OSError as error:
            raise StoreBridgeError(f"chargement impossible : {str(error)[:200]}") from error
        try:
            lib.kutstore_abi_version.restype = ctypes.c_int32
            lib.kutstore_abi_version.argtypes = []
            version = int(lib.kutstore_abi_version())
        except AttributeError as error:
            raise StoreBridgeError("symbole kutstore_abi_version absent") from error
        if version != ABI_VERSION:
            raise StoreBridgeError(f"ABI {version} != {ABI_VERSION} attendue : pont non utilisé")
        lib.kutstore_call.restype = ctypes.c_int32
        lib.kutstore_call.argtypes = [ctypes.c_int32, ctypes.c_char_p, ctypes.c_int32, ctypes.POINTER(ctypes.c_void_p)]
        lib.kutstore_poll_update.restype = ctypes.c_int32
        lib.kutstore_poll_update.argtypes = [ctypes.c_int32, ctypes.POINTER(ctypes.c_void_p)]
        lib.kutstore_free.restype = None
        lib.kutstore_free.argtypes = [ctypes.c_void_p]
        lib.kutstore_shutdown.restype = None
        lib.kutstore_shutdown.argtypes = []
        self._lib = lib
        self.abi_version = version

    def _take(self, pointer: ctypes.c_void_p) -> dict:
        """Copie puis libère la chaîne du pont (propriété : le pont alloue, nous libérons une fois)."""
        if not pointer.value:
            return {}
        try:
            return json.loads(ctypes.string_at(pointer.value).decode("utf-8", "replace"))
        except ValueError:
            return {"error": "json_invalide"}
        finally:
            self._lib.kutstore_free(pointer)

    def call(self, op: int, arg: str = "", timeout_ms: int = 15000) -> tuple[int, dict]:
        data = arg.encode("utf-8")
        if len(data) > MAX_ARG_BYTES:
            return BAD_ARGUMENT, {"error": "argument trop long"}
        out = ctypes.c_void_p()
        status = int(self._lib.kutstore_call(op, data, timeout_ms, ctypes.byref(out)))
        return status, self._take(out)

    def poll_update(self, timeout_ms: int = 1000) -> tuple[int, dict]:
        out = ctypes.c_void_p()
        status = int(self._lib.kutstore_poll_update(timeout_ms, ctypes.byref(out)))
        return status, self._take(out)

    def shutdown(self) -> None:
        self._lib.kutstore_shutdown()
