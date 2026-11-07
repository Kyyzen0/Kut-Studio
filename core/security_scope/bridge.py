"""Le pont des signets à portée de sécurité vu de Python : contrat ``ctypes`` de ``libkutscope.dylib`` (ADR-0013, étape 5).

Bibliothèque **séparée** de ``libkutstore`` (celle-ci garde son ABI 1) : Swift dans
``packaging/store-spike/scope/KutScopeBridge.swift``, interface C ``kutscope.h``. Mêmes règles que ``storekit_bridge`` :

* **Chemin fixe** : ``Contents/Frameworks`` du bundle, jamais une variable d'environnement, ``PATH`` ni ``DYLD_*``.
* **ABI lue d'abord** ; une autre valeur refuse la bibliothèque (aucune autre fonction n'est liée).
* **Bornes** : chemin et signet limités avant de franchir la frontière ; réponse lue avec une taille maximale.
* **Mémoire** : la chaîne rendue par le pont est copiée puis libérée une fois, dans un ``finally``.
* **Pas de chemin dans les erreurs** : :class:`ScopeBridgeError` porte un message fixe, jamais l'entrée ni le détail du système.
* **Pas d'accès sans pont** : bibliothèque absente ou invalide => :class:`ScopeBridgeError`, que l'appelant traduit par « aucun
  accès » (jamais par un contournement).
"""

from __future__ import annotations

import ctypes
import json
import sys
from dataclasses import dataclass
from pathlib import Path

ABI_VERSION = 1
LIBRARY_NAME = "libkutscope.dylib"

# kutscope_status (kutscope.h)
OK, BAD_ARGUMENT, DENIED, UNRESOLVABLE, INTERNAL = range(5)

MAX_PATH_BYTES = 4096
MAX_BOOKMARK_CHARS = 65536
MAX_RESPONSE_BYTES = 1 << 17


class ScopeBridgeError(RuntimeError):
    """Pont absent, invalide ou d'une autre ABI. Le message est fixe (aucune donnée de l'utilisateur)."""


@dataclass(frozen=True)
class OpenedScope:
    """Un accès ouvert : ``handle`` (à fermer une fois), chemin résolu, périmé ou non, signet de remplacement éventuel."""

    handle: int
    path: str
    stale: bool
    refreshed: str | None


def bundled_dylib_path(executable: str | None = None, *, frozen: bool | None = None) -> Path | None:
    """``<App>.app/Contents/Frameworks/libkutscope.dylib`` ; ``None`` hors macOS et hors bundle figé (aucune variable lue)."""
    if sys.platform != "darwin":
        return None
    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    if not is_frozen:
        return None
    exe = Path(sys.executable if executable is None else executable)
    return exe.resolve().parent.parent / "Frameworks" / LIBRARY_NAME


class ScopeBridge:
    """``libkutscope.dylib`` chargée et vérifiée. Se construit par :meth:`load`. Les erreurs de signet rendent ``None``."""

    def __init__(self, library: ctypes.CDLL, libc: ctypes.CDLL) -> None:
        libc.strnlen.restype = ctypes.c_size_t
        libc.strnlen.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        self._lib = library
        self._libc = libc
        self.abi_version = ABI_VERSION

    @classmethod
    def load(cls, path: str | Path) -> ScopeBridge:
        """Charge ``path`` et vérifie l'ABI avant tout autre appel. ``ScopeBridgeError`` à la moindre anomalie."""
        if sys.platform != "darwin":
            raise ScopeBridgeError("les signets de sécurité n'existent que sous macOS")
        target = Path(path)
        if not target.is_file():
            raise ScopeBridgeError("bibliothèque des signets absente")
        try:
            lib = ctypes.CDLL(str(target))
            libc = ctypes.CDLL(None)
        except OSError as error:                                   # le détail du système peut citer un chemin : non recopié
            raise ScopeBridgeError("bibliothèque des signets non chargeable") from error
        try:
            lib.kutscope_abi_version.restype = ctypes.c_int32
            lib.kutscope_abi_version.argtypes = []
            version = int(lib.kutscope_abi_version())
        except AttributeError as error:
            raise ScopeBridgeError("bibliothèque sans version d'interface") from error
        if version != ABI_VERSION:
            raise ScopeBridgeError("version d'interface des signets différente de celle attendue")
        try:
            lib.kutscope_bookmark.restype = ctypes.c_int32
            lib.kutscope_bookmark.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p)]
            lib.kutscope_open.restype = ctypes.c_int32
            lib.kutscope_open.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_int64), ctypes.POINTER(ctypes.c_void_p)]
            lib.kutscope_close.restype = ctypes.c_int32
            lib.kutscope_close.argtypes = [ctypes.c_int64]
            lib.kutscope_open_count.restype = ctypes.c_int32
            lib.kutscope_open_count.argtypes = []
            lib.kutscope_free.restype = None
            lib.kutscope_free.argtypes = [ctypes.c_void_p]
        except AttributeError as error:
            raise ScopeBridgeError("bibliothèque des signets incomplète") from error
        return cls(lib, libc)

    def _take_text(self, pointer: ctypes.c_void_p) -> str | None:
        """Copie puis libère la chaîne du pont ; ``None`` si absente ou plus grande que :data:`MAX_RESPONSE_BYTES`."""
        address = pointer.value
        if not address:
            return None
        try:
            length = int(self._libc.strnlen(ctypes.c_void_p(address), MAX_RESPONSE_BYTES + 1))
            raw = None if length > MAX_RESPONSE_BYTES else ctypes.string_at(address, length)
        finally:
            self._lib.kutscope_free(pointer)
        return None if raw is None else raw.decode("utf-8", "replace")

    def create_bookmark(self, path: str) -> str | None:
        """Signet (base64) de ``path``, ou ``None`` (chemin trop long, inaccessible, pont en erreur)."""
        data = path.encode("utf-8", "surrogateescape") if isinstance(path, str) else b""
        if not data or len(data) > MAX_PATH_BYTES or b"\0" in data:
            return None
        out = ctypes.c_void_p()
        status = int(self._lib.kutscope_bookmark(data, ctypes.byref(out)))
        text = self._take_text(out)
        return text if status == OK and text and len(text) <= MAX_BOOKMARK_CHARS else None

    def open(self, bookmark: str) -> tuple[int, OpenedScope | None]:
        """Résout et ouvre l'accès. Rend ``(statut, accès)`` ; l'accès est ``None`` hors ``OK``."""
        if not isinstance(bookmark, str) or not bookmark or len(bookmark) > MAX_BOOKMARK_CHARS:
            return BAD_ARGUMENT, None
        handle = ctypes.c_int64(0)
        out = ctypes.c_void_p()
        status = int(self._lib.kutscope_open(bookmark.encode("ascii", "ignore"), ctypes.byref(handle), ctypes.byref(out)))
        text = self._take_text(out)
        if status != OK:
            return status, None
        try:
            data = json.loads(text or "")
            path = data["path"]
            refreshed = data.get("refreshed")
            if not isinstance(path, str) or not (refreshed is None or isinstance(refreshed, str)):
                raise ValueError
        except (ValueError, KeyError, TypeError):
            self._lib.kutscope_close(handle.value)                # un accès ouvert dont on ne peut lire la réponse est refermé
            return INTERNAL, None
        return OK, OpenedScope(int(handle.value), path, data.get("stale") is True, refreshed)

    def close(self, handle: int) -> bool:
        """Ferme un accès ouvert. ``False`` si l'identifiant est inconnu ou déjà fermé."""
        return int(self._lib.kutscope_close(int(handle))) == OK

    def open_count(self) -> int:
        """Accès encore ouverts côté natif (diagnostic)."""
        return int(self._lib.kutscope_open_count())


__all__ = [
    "ABI_VERSION", "BAD_ARGUMENT", "DENIED", "INTERNAL", "LIBRARY_NAME", "MAX_BOOKMARK_CHARS", "MAX_PATH_BYTES", "OK",
    "OpenedScope", "ScopeBridge", "ScopeBridgeError", "UNRESOLVABLE", "bundled_dylib_path",
]
