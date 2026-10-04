"""Cache disque des vecteurs de mouvement (données recalculables, jamais une source de vérité).

Deux sortes d'entrées partagent le dossier, le budget et l'ordre d'éviction :

* ``pair-<clé>.npz`` — l'analyse d'**une paire** d'images consécutives (:class:`~core.optical_flow.PairAnalysis` : les deux
  flux, leur confiance, ou « coupure » / « images identiques ») ;
* ``frames-<clé>.mkv`` (+ ``.json``) — le **flux d'images préparé** d'un clip (:mod:`core.retime_prepare`) : les images
  fabriquées, sans perte, relues par le graphe d'export ; son rapport d'exécution est dans le ``.json`` voisin.

Même contrat que ``TrackingCache`` et ``AudioSyncCache`` :

* la clé contient tout ce qui change le résultat : le **média réellement analysé** (chemin et signature : un proxy et l'original
  sont deux entrées, l'export ne lit jamais un flux calculé sur un proxy), l'indice de la première image, la grille de travail
  (taille et filtre de mise au cadre), le moteur (version, backend, réglages) ;
* le mouvement ne dépend **ni de la vitesse ni de la courbe du clip** : déplacer un point de vitesse, changer 25 % en 40 %,
  couper ou déplacer le clip ne recalcule rien — seul le plan des images à fabriquer change ;
* une entrée illisible ou incohérente vaut une entrée absente (jetée et journalisée) ;
* l'écriture est atomique (``.tmp`` puis ``os.replace``) et un échec d'écriture est ignoré ;
* plusieurs instances de l'application partagent le dossier sans se gêner : une entrée n'apparaît que complète, et deux écritures
  concurrentes de la même clé produisent le même contenu.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from .cache_keys import SignatureMemo, source_signature
from .platform_paths import user_cache_dir

if TYPE_CHECKING:
    from .optical_flow import PairAnalysis

LOGGER = logging.getLogger("kut_studio.flow")

CACHE_SUBDIRECTORY = "flow"
_FRESH_SIGNATURES = SignatureMemo(ttl=0.0)
"""Signature relue à chaque appel : le mémo partagé garde une valeur 2 s, trop pour valider un résultat d'analyse."""
_PREFIX = "pair-"
_SUFFIX = ".npz"
def _writer_tag() -> str:
    """Nom propre à *une* écriture : processus **et** appel. Deux fils du même processus (export, aperçu, analyse) qui préparent la
    même clé n'écrivent jamais dans le même fichier ; deux instances de l'application non plus."""
    return f"{os.getpid()}-{uuid.uuid4().hex[:8]}"


_STREAM_PREFIX = "frames-"
_STREAM_SUFFIX = ".mkv"
_REPORT_SUFFIX = ".json"
ORPHAN_AGE_SECONDS = 3600.0
"""Âge au-delà duquel un fichier ``.tmp`` est tenu pour abandonné (un fichier en cours d'écriture est touché sans cesse)."""


def cache_directory() -> Path:
    """Dossier du cache de mouvement (sous ``user_cache_dir()``, jamais ``tempfile``)."""
    return Path(user_cache_dir()) / CACHE_SUBDIRECTORY


def cache_key(
    media_path: str, first_frame: int, *, width: int, height: int, conformation: str, identity: tuple[object, ...]
) -> str:
    """Clé stable de l'analyse de la paire ``(first_frame, first_frame + 1)`` de ``media_path``.

    Args:
        media_path: le fichier **réellement décodé** pour l'analyse (le proxy, ou l'original).
        first_frame: indice de la première image de la paire.
        width: largeur de la grille de travail (les images analysées, après mise au cadre).
        height: hauteur de la grille de travail.
        conformation: filtres qui amènent le média à la grille de travail (``scale``, ``pad``…) : un autre cadrage change
            les images, donc le mouvement.
        identity: :attr:`core.optical_flow.OpticalFlowEngine.identity` (moteur, backend, réglages d'analyse).
    """
    signature = source_signature(media_path, _FRESH_SIGNATURES)
    payload = {
        "path": os.path.abspath(media_path),
        "signature": signature.token if signature is not None else "missing",
        "frame": int(first_frame),
        "grid": [int(width), int(height)],
        "conformation": conformation,
        "engine": list(identity),
    }
    return hashlib.sha1(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


class FlowCache:
    """Analyses de paires en ``.npz`` sous ``user_cache_dir()/flow`` (LRU par date de dernier accès)."""

    def __init__(self, directory: Path | None = None) -> None:
        self._directory = Path(directory) if directory is not None else cache_directory()

    @property
    def directory(self) -> Path:
        return self._directory

    def _path(self, key: str) -> Path:
        return self._directory / f"{_PREFIX}{key}{_SUFFIX}"

    def load(self, key: str) -> PairAnalysis | None:
        """Analyse stockée, ou ``None`` (absente, illisible ou incohérente : alors supprimée)."""
        import numpy as np

        from .optical_flow import PairAnalysis

        path = self._path(key)
        if not path.exists():
            return None
        try:
            with np.load(path, allow_pickle=False) as archive:
                pair = PairAnalysis.unpack({"flow": archive["flow"], "status": archive["status"]})
            os.utime(path)  # dernier accès : ordre d'éviction
            return pair
        except Exception as exc:  # noqa: BLE001 - une entrée de cache ne doit jamais faire échouer l'analyse
            LOGGER.warning("Cache de mouvement : entrée %s illisible, ignorée (%s)", key, exc)
            try:
                path.unlink()
            except OSError:
                pass
            return None

    def store(self, key: str, pair: PairAnalysis) -> None:
        """Écrit l'analyse de façon atomique ; un échec d'écriture est journalisé et ignoré."""
        import numpy as np

        path = self._path(key)
        temporary = path.with_name(f".{path.stem}.{_writer_tag()}.tmp{_SUFFIX}")
        try:
            self._directory.mkdir(parents=True, exist_ok=True)
            with temporary.open("wb") as handle:  # un objet fichier : ``np.savez("x.tmp")`` ajouterait ``.npz``
                packed = pair.pack()
                np.savez(handle, flow=packed["flow"], status=packed["status"])
            os.replace(temporary, path)
        except OSError as exc:
            LOGGER.warning("Cache de mouvement : écriture impossible (%s)", exc)
            try:
                temporary.unlink()
            except OSError:
                pass

    # -- flux d'images préparés ---------------------------------------------------------------------------------

    def stream_path(self, key: str) -> Path:
        """Chemin d'un flux préparé complet (il n'existe qu'une fois promu : jamais un fichier partiel)."""
        return self._directory / f"{_STREAM_PREFIX}{key}{_STREAM_SUFFIX}"

    def stream_temporary(self, key: str) -> Path:
        """Chemin d'écriture d'un flux en cours : un par appel (plusieurs fils ou instances peuvent préparer la même clé)."""
        self._directory.mkdir(parents=True, exist_ok=True)
        return self._directory / f".{_STREAM_PREFIX}{key}.{_writer_tag()}.tmp{_STREAM_SUFFIX}"

    def has_stream(self, key: str) -> bool:
        path = self.stream_path(key)
        if not path.is_file():
            return False
        try:
            os.utime(path)  # dernier accès : ordre d'éviction
        except OSError:
            pass
        return True

    def promote_stream(self, temporary: Path, key: str, report: dict[str, object]) -> Path:
        """Rend le flux visible, atomiquement ; son rapport est écrit avant lui (un flux n'apparaît jamais sans son rapport)."""
        target = self.stream_path(key)
        sidecar = target.with_suffix(_REPORT_SUFFIX)
        partial = sidecar.with_name(f".{sidecar.name}.{_writer_tag()}.tmp")
        try:
            partial.write_text(json.dumps(report, sort_keys=True), encoding="utf-8")
            os.replace(partial, sidecar)
            os.replace(temporary, target)
        except OSError:
            for leftover in (partial, temporary):
                try:
                    leftover.unlink()
                except OSError:
                    pass
            raise
        return target

    def stream_report(self, key: str) -> dict[str, object]:
        """Rapport d'exécution du flux (vide s'il est absent ou illisible : le flux, lui, reste valable)."""
        try:
            loaded = json.loads(self.stream_path(key).with_suffix(_REPORT_SUFFIX).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return loaded if isinstance(loaded, dict) else {}

    # -- gestion du budget (même contrat que les autres couches de ``CacheManager``) -------------------------------

    def _files(self) -> list[Path]:
        if not self._directory.is_dir():
            return []
        return [
            item for item in self._directory.iterdir()
            if (item.name.startswith(_PREFIX) and item.suffix == _SUFFIX)
            or (item.name.startswith(_STREAM_PREFIX) and item.suffix == _STREAM_SUFFIX)
        ]

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
            if item.suffix == _STREAM_SUFFIX:
                try:
                    item.with_suffix(_REPORT_SUFFIX).unlink()
                except OSError:
                    pass
        return freed

    def purge(self) -> int:
        """Supprime toutes les entrées (et les écritures abandonnées) ; retourne les octets libérés."""
        freed = self.evict_bytes(2**62)
        self.cleanup_orphans(0.0)                      # une purge est un choix explicite : tout part, écritures en cours comprises
        return freed

    def cleanup_orphans(self, max_age_seconds: float = ORPHAN_AGE_SECONDS) -> int:
        """Retire les écritures abandonnées par un arrêt brutal (``.tmp`` plus vieux que ``max_age_seconds``) ; retourne leur nombre.

        Une préparation en cours — de cette instance ou **d'une autre** application ouverte en même temps — réécrit son
        fichier sans cesse : seul un fichier qui n'a pas bougé depuis longtemps est abandonné. L'effacer sous les pieds d'une
        préparation vivante ferait échouer son export.
        """
        removed = 0
        if not self._directory.is_dir():
            return 0
        now = time.time()
        for item in self._directory.iterdir():
            if not (item.name.startswith(".") and ".tmp" in item.name):
                continue
            try:
                if now - item.stat().st_mtime < max_age_seconds:
                    continue
                item.unlink()
                removed += 1
            except OSError:
                continue
        return removed


__all__ = ["FlowCache", "cache_directory", "cache_key"]
