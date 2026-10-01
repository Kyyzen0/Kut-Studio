"""Cache disque deterministe des segments d'apercu (tache 30).

Chaque segment est identifie par : clip, plage temporelle, qualite
et empreinte des parametres appliques (effets, transform, grade,
LUT, remappage, sous-titres, transitions). Le fichier est un MP4
pre-rendu via le meme graphe de filtres que l'export.

Regles :
- cle deterministe, sans horodatage ;
- invalidation chirurgicale : seuls les segments affectes sont
  supprimes (par clip, par plage, ou tout) ;
- nettoyage LRU borne en octets + TTL optionnel ;
- jamais ecrit dans le .kut (dossier cache utilisateur / tmp).
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .platform_paths import user_cache_dir


SEGMENT_SECONDS = 2.0
DEFAULT_BUDGET_BYTES = 512 * 1024 * 1024
DEFAULT_TTL_SECONDS = 7 * 24 * 3600


def default_cache_dir(custom=None):
    """Dossier de cache (jamais dans le projet .kut)."""
    if custom:
        return Path(custom)
    # Compatibilité : cette variable a toujours désigné directement le
    # dossier des segments, contrairement au cache système qui porte un
    # sous-dossier ``preview``.
    env = os.environ.get("KUT_STUDIO_CACHE_DIR")
    if env:
        return Path(env)
    return user_cache_dir() / "preview"


def _quantize(value, step=0.001):
    return round(float(value) / step) * step


@dataclass(frozen=True)
class PreviewSegmentKey:
    """Cle logique d'un segment de previsualisation."""

    clip_id: str
    start: float
    end: float
    quality: str
    params_hash: str

    def normalized(self):
        from .preview_render import coerce_render_quality

        return PreviewSegmentKey(
            clip_id=str(self.clip_id),
            start=_quantize(self.start),
            end=_quantize(self.end),
            quality=coerce_render_quality(self.quality),
            params_hash=str(self.params_hash),
        )


def segment_key_string(key):
    """Chaine stable utilisee pour le nom de fichier."""
    norm = key.normalized()
    payload = {
        "clip_id": norm.clip_id,
        "start": norm.start,
        "end": norm.end,
        "quality": norm.quality,
        "params_hash": norm.params_hash,
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=True)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
    safe_clip = "".join(
        c if (c.isalnum() or c in ("-", "_")) else "_" for c in norm.clip_id
    )[:48]
    return "%s_%s_%s" % (safe_clip or "clip", norm.quality, digest)


class DiskPreviewCache:
    """Cache disque borné : invalidation chirurgicale, LRU, index en mémoire.

    L'ancienne version parcourait **tout** le dossier à chaque ``store``
    (``glob`` + ``stat`` de chaque segment) pour appliquer le budget :
    un coût proportionnel au nombre de segments, payé à chaque rendu.
    Un index ``nom -> (taille, dernier usage)`` est maintenant construit
    une fois (``scandir``) puis tenu à jour ; ``store`` et l'éviction sont
    en O(1) amorti pour l'index. Un fichier supprimé à la main est
    détecté à la première lecture (``lookup`` le retire de l'index) ou
    par :meth:`rescan`.
    """

    def __init__(self, directory=None, **kwargs):
        budget = kwargs.get("budget_bytes", DEFAULT_BUDGET_BYTES)
        ttl = kwargs.get("ttl_seconds", DEFAULT_TTL_SECONDS)
        self.directory = Path(default_cache_dir(directory))
        self.directory.mkdir(parents=True, exist_ok=True)
        self.budget_bytes = max(1, int(budget))
        self.ttl_seconds = float(ttl) if ttl else 0.0
        self.hits = 0
        self.misses = 0
        self._lock = threading.RLock()
        self._index = None  # nom -> [taille, dernier usage en ns] ; chargé à la demande
        self._total = 0
        self._dir_mtime = None  # date du dossier à la dernière synchronisation

    # -- index -----------------------------------------------------------------

    def rescan(self):
        """Reconstruit l'index depuis le disque (après une modification externe)."""
        index = {}
        total = 0
        try:
            with os.scandir(self.directory) as entries:
                for entry in entries:
                    if not entry.name.endswith(".mp4"):
                        continue
                    try:
                        stat = entry.stat()
                    except OSError:
                        continue
                    index[entry.name] = [stat.st_size, stat.st_mtime_ns]
                    total += stat.st_size
        except OSError:
            pass
        with self._lock:
            self._index = index
            self._total = total
            self._dir_mtime = self._read_dir_mtime()

    def _read_dir_mtime(self):
        try:
            return os.stat(self.directory).st_mtime_ns
        except OSError:
            return None

    def _sync_if_changed(self):
        """Relit le disque si un autre processus a modifié le dossier.

        Détecté par la date du dossier, mise à jour après chacune de nos
        propres écritures : un seul ``stat``, pas un parcours.
        """
        if self._index is not None and self._read_dir_mtime() != self._dir_mtime:
            self.rescan()

    def _ensure_index(self):
        if self._index is None:
            self.rescan()
        return self._index

    def _forget(self, name):
        entry = self._index.pop(name, None) if self._index is not None else None
        if entry is not None:
            self._total -= entry[0]

    # -- API ---------------------------------------------------------------------

    def path_for(self, key):
        """Chemin deterministe du segment (sans le creer)."""
        return self.directory / (segment_key_string(key) + ".mp4")

    def lookup(self, key):
        """Retourne le chemin si le segment existe et est frais."""
        path = self.path_for(key)
        with self._lock:
            index = self._ensure_index()
            if not path.is_file():
                self._forget(path.name)
                self.misses += 1
                return None
            if self.ttl_seconds > 0:
                try:
                    age = time.time() - path.stat().st_mtime
                except OSError:
                    self._forget(path.name)
                    self.misses += 1
                    return None
                if age > self.ttl_seconds:
                    try:
                        path.unlink()
                    except OSError:
                        pass
                    self._forget(path.name)
                    self.misses += 1
                    return None
            self.hits += 1
            try:
                path.touch()
            except OSError:
                pass
            entry = index.get(path.name)
            if entry is not None:
                entry[1] = time.time_ns()
            return path

    def store(self, key, source_path):
        """Copie atomique d'un segment rendu vers le cache."""
        import shutil
        import tempfile

        target = self.path_for(key)
        # Le dossier a pu être supprimé en cours de session (nettoyage manuel) :
        # on le recrée plutôt que d'échouer à chaque rendu suivant.
        self.directory.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=target.name + ".", suffix=".tmp", dir=str(self.directory)
        )
        try:
            os.close(fd)
            shutil.copyfile(str(source_path), tmp_name)
            os.replace(tmp_name, target)
        finally:
            try:
                if os.path.exists(tmp_name):
                    os.remove(tmp_name)
            except OSError:
                pass
        try:
            size = target.stat().st_size
        except OSError:
            size = 0
        with self._lock:
            index = self._ensure_index()
            self._forget(target.name)
            index[target.name] = [size, time.time_ns()]
            self._total += size
            self._evict_indexed()
            self._dir_mtime = self._read_dir_mtime()
        return target

    def invalidate_clip(self, clip_id):
        """Supprime les segments d'un clip (toute plage, toute qualite)."""
        safe = "".join(
            c if (c.isalnum() or c in ("-", "_")) else "_" for c in str(clip_id)
        )[:48] or "clip"
        prefix = safe + "_"
        removed = 0
        with self._lock:
            index = self._ensure_index()
            for name in [n for n in index if n.startswith(prefix)]:
                try:
                    (self.directory / name).unlink()
                    removed += 1
                except OSError:
                    pass
                self._forget(name)
            self._dir_mtime = self._read_dir_mtime()
        return removed

    def invalidate_clips(self, clip_ids):
        """Supprime les segments de **plusieurs** clips en un seul passage.

        ``invalidate_clip`` en boucle coûterait un parcours de l'index par
        clip ; ici un seul (purge d'un projet entier).
        """
        prefixes = tuple(
            "".join(c if (c.isalnum() or c in ("-", "_")) else "_" for c in str(clip))[:48]
            + "_"
            for clip in clip_ids
        )
        if not prefixes:
            return 0
        removed = 0
        with self._lock:
            index = self._ensure_index()
            for name in [n for n in index if n.startswith(prefixes)]:
                try:
                    (self.directory / name).unlink()
                    removed += 1
                except OSError:
                    pass
                self._forget(name)
            self._dir_mtime = self._read_dir_mtime()
        return removed

    def invalidate_all(self):
        """Vide tout le cache disque."""
        removed = 0
        with self._lock:
            index = self._ensure_index()
            for name in list(index):
                try:
                    (self.directory / name).unlink()
                    removed += 1
                except OSError:
                    pass
                self._forget(name)
            self._dir_mtime = self._read_dir_mtime()
        return removed

    def purge(self):
        """Alias de :meth:`invalidate_all` (vocabulaire du gestionnaire de cache)."""
        return self.invalidate_all()

    def _evict_indexed(self):
        """Éviction LRU jusqu'au budget, d'après l'index (aucun accès disque de scan)."""
        evicted = 0
        # Un segment unique plus gros que le budget est conservé : mieux
        # vaut un aperçu que rien ; l'éviction ne vaut qu'à partir de 2 fichiers.
        while self._total > self.budget_bytes and len(self._index) > 1:
            name = min(self._index, key=lambda n: self._index[n][1])
            try:
                (self.directory / name).unlink()
            except OSError:
                pass
            self._forget(name)
            evicted += 1
        return evicted

    def evict_if_needed(self):
        """Resynchronise l'index avec le disque puis applique le budget."""
        self.rescan()
        with self._lock:
            return self._evict_indexed()

    def evict_bytes(self, target_bytes):
        """Libère au moins ``target_bytes`` en retirant les plus anciens segments.

        Sert au budget global (:class:`core.cache_manager.CacheManager`).
        Retourne le nombre d'octets réellement libérés.
        """
        freed = 0
        with self._lock:
            index = self._ensure_index()
            while freed < target_bytes and index:
                name = min(index, key=lambda n: index[n][1])
                size = index[name][0]
                try:
                    (self.directory / name).unlink()
                except OSError:
                    pass
                self._forget(name)
                freed += size
            self._dir_mtime = self._read_dir_mtime()
        return freed

    def entries_info(self):
        """Liste ``(chemin, taille, dernier usage en ns)`` des segments connus."""
        with self._lock:
            self._ensure_index()
            self._sync_if_changed()
            index = self._index
            return [
                (self.directory / name, entry[0], entry[1])
                for name, entry in index.items()
            ]

    def status_for_range(self, keys):
        """Etat du cache pour une liste de cles : cached/pending."""
        states = {}
        for key in keys:
            path = self.path_for(key)
            states[segment_key_string(key)] = (
                "cached" if path.is_file() else "pending"
            )
        return states

    def stats(self):
        """Compteurs + occupation disque (depuis l'index : sans parcours du dossier)."""
        with self._lock:
            self._ensure_index()
            self._sync_if_changed()
            index = self._index
            return {
                "entries": len(index),
                "bytes": self._total,
                "hits": self.hits,
                "misses": self.misses,
                "budget_bytes": self.budget_bytes,
            }
