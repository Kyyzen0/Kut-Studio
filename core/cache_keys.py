"""Clés de cache déterministes et signature des fichiers sources.

Source unique pour **toutes** les clés des caches de Kut-Studio
(sondes, miniatures, formes d'onde, proxies). Avant ce module, les clés
de miniatures et de formes d'onde étaient définies deux fois
(``media_cache`` sans signature de fichier, ``media_previews`` avec) :
les deux versions pouvaient diverger et dupliquer des entrées.

Principes
---------

- **Déterministe** : mêmes entrées, même clé, sur toutes les machines
  (aucun horodatage de création, aucun identifiant de session).
- **Invalidation précise** : la clé d'un dérivé (miniature, onde, proxy)
  contient la *signature* du fichier source (``mtime_ns`` + taille). Un
  fichier modifié change de clé ; les dérivés des autres fichiers ne
  bougent pas. Rien n'a besoin d'être « vidé ».
- **Peu d'appels système** : :class:`SignatureMemo` mémorise
  brièvement ``os.stat``. La timeline interroge ces clés à chaque
  défilement ou zoom ; sans mémo, c'était un appel système par vignette
  et par clip visible.

Les clés de segments d'aperçu fidèles restent dans
:mod:`core.preview_cache` (elles dépendent du rendu, pas d'un fichier).
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class SourceSignature:
    """Identité d'un fichier à un instant : date de modification + taille."""

    mtime_ns: int
    size: int

    @property
    def token(self) -> str:
        return f"{self.mtime_ns}:{self.size}"


MISSING_TOKEN = "missing"
"""Jeton de signature d'un fichier absent (clé stable, jamais d'exception)."""


class SignatureMemo:
    """Mémo de ``os.stat`` à durée de vie courte, sûr entre threads.

    Un fichier modifié moins de ``ttl`` secondes après la dernière lecture
    garde brièvement son ancienne signature : acceptable pour des
    dérivés recalculables. :meth:`invalidate` force une relecture
    immédiate (réimport, relink, suppression volontaire).
    """

    def __init__(
        self,
        ttl: float = 2.0,
        *,
        clock: Callable[[], float] = time.monotonic,
        stat: Callable[[str], os.stat_result] = os.stat,
    ) -> None:
        self._ttl = float(ttl)
        self._clock = clock
        self._stat = stat
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[float, SourceSignature | None]] = {}
        self.stat_calls = 0

    def get(self, path: str) -> SourceSignature | None:
        """Signature de ``path``, ``None`` si le fichier est illisible ou absent."""
        key = os.path.abspath(path)
        now = self._clock()
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None and now - entry[0] <= self._ttl:
                return entry[1]
        try:
            stat = self._stat(key)
            signature: SourceSignature | None = SourceSignature(
                int(stat.st_mtime_ns), int(stat.st_size)
            )
        except OSError:
            signature = None
        with self._lock:
            self.stat_calls += 1
            self._entries[key] = (now, signature)
            if len(self._entries) > 50_000:  # borne de sécurité, jamais atteinte en usage normal
                self._entries.clear()
        return signature

    def exists(self, path: str) -> bool:
        return self.get(path) is not None

    def invalidate(self, path: str | None = None) -> None:
        """Oublie une signature (``path``) ou toutes."""
        with self._lock:
            if path is None:
                self._entries.clear()
            else:
                self._entries.pop(os.path.abspath(path), None)


default_signatures = SignatureMemo()
"""Mémo partagé par le processus (timeline, caches, proxies)."""


def source_signature(path: str, memo: SignatureMemo | None = None) -> SourceSignature | None:
    return (memo or default_signatures).get(path)


def file_exists(path: str, memo: SignatureMemo | None = None) -> bool:
    """``os.path.isfile`` mémorisé (voir :class:`SignatureMemo`)."""
    return bool(path) and (memo or default_signatures).exists(path)


def _token(path: str, memo: SignatureMemo | None) -> str:
    signature = source_signature(path, memo)
    return signature.token if signature is not None else MISSING_TOKEN


# ---------------------------------------------------------------------------
# Clés
# ---------------------------------------------------------------------------


def probe_key(path: str) -> str | None:
    """Clé d'une sonde ``ffprobe``, ou ``None`` si le fichier est illisible.

    Lecture directe (sans mémo) : la sonde se fait à l'import, où la
    fraîcheur compte plus que le coût d'un ``stat``.
    """
    try:
        stat = os.stat(path)
    except OSError:
        return None
    return f"probe:{os.path.abspath(path)}:{stat.st_mtime_ns}:{stat.st_size}"


def thumbnail_key(
    path: str, time_seconds: float, width: int, memo: SignatureMemo | None = None
) -> str:
    """Clé d'une miniature. Le temps est quantifié au dixième de seconde."""
    quantized = round(float(time_seconds), 1)
    return f"thumb:{os.path.abspath(path)}:{_token(path, memo)}:{quantized}:{int(width)}"


def waveform_key(path: str, bins: int, memo: SignatureMemo | None = None) -> str:
    """Clé d'une forme d'onde pour ``bins`` colonnes."""
    return f"wave:{os.path.abspath(path)}:{_token(path, memo)}:{int(bins)}"


def proxy_key(path: str, profile_fingerprint: str) -> str:
    """Clé stable d'un proxy : fichier source + empreinte du profil.

    Volontairement **sans** la signature du fichier : elle est stockée
    à côté du proxy (voir :mod:`core.proxy_manager`) pour pouvoir
    reconnaître un proxy *obsolète* (source modifiée) au lieu de
    l'ignorer comme s'il n'avait jamais existé, et le régénérer sur le
    même emplacement.
    """
    raw = f"{os.path.abspath(path)}|{profile_fingerprint}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return f"proxy-{digest}"


__all__ = [
    "MISSING_TOKEN",
    "SignatureMemo",
    "SourceSignature",
    "default_signatures",
    "file_exists",
    "probe_key",
    "proxy_key",
    "source_signature",
    "thumbnail_key",
    "waveform_key",
]
