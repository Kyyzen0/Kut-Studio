"""Cache disque des analyses de synchronisation audio (données recalculables, jamais une source de vérité).

On y range l'**enveloppe d'énergie** d'une source (quelques Mo au plus, même pour deux heures de son) : c'est la partie
coûteuse de l'analyse, réutilisée quand on relance une synchronisation. Même contrat que ``TrackingCache`` :

* la clé contient tout ce qui change le résultat : version de l'algorithme, chemin, signature du fichier
  (date de modification et taille), plage analysée, paramètres ; changer le média ou la plage invalide l'entrée ;
* une entrée illisible ou incohérente vaut une entrée absente (jetée et journalisée) ;
* l'écriture est atomique (``.tmp`` puis ``os.replace``) et un échec d'écriture est ignoré.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

from .atomic_io import atomic_open
from .cache_keys import SignatureMemo, source_signature
from .platform_paths import user_cache_dir

if TYPE_CHECKING:
    import numpy as np
    import numpy.typing as npt

LOGGER = logging.getLogger("kut_studio.multicam")

AUDIO_SYNC_VERSION = 1
"""Version de l'algorithme : à incrémenter dès qu'une enveloppe stockée ne serait plus celle recalculée."""

CACHE_SUBDIRECTORY = "multicam"
_FRESH_SIGNATURES = SignatureMemo(ttl=0.0)
"""Signature relue à chaque appel : le mémo partagé garde une valeur 2 s, trop pour valider un résultat d'analyse."""
_PREFIX = "env-"
_SUFFIX = ".npy"


def cache_directory() -> Path:
    """Dossier du cache de synchronisation (sous ``user_cache_dir()``, jamais ``tempfile``)."""
    return Path(user_cache_dir()) / CACHE_SUBDIRECTORY


def cache_key(path: str, start: float, duration: float | None, params: dict[str, object]) -> str:
    """Clé stable d'une enveloppe : version, fichier (chemin + signature), plage et paramètres."""
    signature = source_signature(path, _FRESH_SIGNATURES)
    payload = {
        "version": AUDIO_SYNC_VERSION,
        "path": os.path.abspath(path),
        "signature": signature.token if signature is not None else "missing",
        "start": round(float(start), 6),
        "duration": None if duration is None else round(float(duration), 6),
        "params": params,
    }
    return hashlib.sha1(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


class AudioSyncCache:
    """Enveloppes d'énergie en ``.npy`` sous ``user_cache_dir()/multicam`` (LRU par date de dernier accès)."""

    def __init__(self, directory: Path | None = None) -> None:
        self._directory = Path(directory) if directory is not None else cache_directory()

    @property
    def directory(self) -> Path:
        return self._directory

    def _path(self, key: str) -> Path:
        return self._directory / f"{_PREFIX}{key}{_SUFFIX}"

    def load(self, key: str) -> npt.NDArray[np.float32] | None:
        """Enveloppe stockée, ou ``None`` (absente, illisible ou incohérente : alors supprimée)."""
        import numpy as np

        path = self._path(key)
        if not path.exists():
            return None
        try:
            with path.open("rb") as handle:
                array = np.load(handle, allow_pickle=False)
            if array.ndim != 1 or array.dtype != np.float32 or not np.isfinite(array).all():
                raise ValueError("forme ou valeurs inattendues")
            os.utime(path)  # dernier accès : ordre d'éviction
            return array
        except Exception as exc:  # noqa: BLE001 - une entrée de cache ne doit jamais faire échouer l'analyse
            LOGGER.warning("Cache de synchronisation : entrée %s illisible, ignorée (%s)", key, exc)
            try:
                path.unlink()
            except OSError:
                pass
            return None

    def store(self, key: str, array: npt.NDArray[np.float32]) -> None:
        """Écrit l'enveloppe de façon atomique ; un échec d'écriture est journalisé et ignoré."""
        import numpy as np

        path = self._path(key)
        try:
            with atomic_open(path, "wb", durable=False) as handle:  # un objet fichier : ``np.save`` n'ajoute pas ``.npy``
                np.save(handle, np.ascontiguousarray(array, dtype=np.float32), allow_pickle=False)
        except OSError as exc:
            LOGGER.warning("Cache de synchronisation : écriture impossible (%s)", exc)

    # -- gestion du budget (même contrat que les autres couches de ``CacheManager``) -------------------------------

    def _files(self) -> list[Path]:
        if not self._directory.is_dir():
            return []
        return [item for item in self._directory.iterdir() if item.name.startswith(_PREFIX) and item.suffix == _SUFFIX]

    def stats(self) -> dict[str, int]:
        files = self._files()
        total = 0
        for item in files:
            try:
                total += item.stat().st_size
            except OSError:
                continue
        return {"entries": len(files), "bytes": total}

    def evict_bytes(self, count: int) -> int:
        """Supprime les entrées les plus anciennes jusqu'à libérer ``count`` octets ; retourne les octets libérés."""
        freed = 0
        dated: list[tuple[float, int, Path]] = []
        for item in self._files():
            try:
                info = item.stat()
            except OSError:
                continue
            dated.append((info.st_mtime, info.st_size, item))
        for _mtime, size, item in sorted(dated):
            if freed >= count:
                break
            try:
                item.unlink()
            except OSError:
                continue
            freed += size
        return freed

    def purge(self) -> int:
        """Supprime toutes les entrées ; retourne les octets libérés."""
        return self.evict_bytes(2**62)


__all__ = ["AUDIO_SYNC_VERSION", "AudioSyncCache", "cache_directory", "cache_key"]
