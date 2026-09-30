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
    """Cache disque borne avec invalidation chirurgicale et LRU."""

    def __init__(self, directory=None, **kwargs):
        budget = kwargs.get("budget_bytes", DEFAULT_BUDGET_BYTES)
        ttl = kwargs.get("ttl_seconds", DEFAULT_TTL_SECONDS)
        self.directory = Path(default_cache_dir(directory))
        self.directory.mkdir(parents=True, exist_ok=True)
        self.budget_bytes = max(1, int(budget))
        self.ttl_seconds = float(ttl) if ttl else 0.0
        self.hits = 0
        self.misses = 0

    def path_for(self, key):
        """Chemin deterministe du segment (sans le creer)."""
        return self.directory / (segment_key_string(key) + ".mp4")

    def lookup(self, key):
        """Retourne le chemin si le segment existe et est frais."""
        path = self.path_for(key)
        if not path.is_file():
            self.misses += 1
            return None
        if self.ttl_seconds > 0:
            try:
                age = time.time() - path.stat().st_mtime
            except OSError:
                self.misses += 1
                return None
            if age > self.ttl_seconds:
                try:
                    path.unlink()
                except OSError:
                    pass
                self.misses += 1
                return None
        self.hits += 1
        try:
            path.touch()
        except OSError:
            pass
        return path

    def store(self, key, source_path):
        """Copie atomique d'un segment rendu vers le cache."""
        import shutil
        import tempfile

        target = self.path_for(key)
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
        self.evict_if_needed()
        return target

    def invalidate_clip(self, clip_id):
        """Supprime les segments d'un clip (toute plage, toute qualite)."""
        safe = "".join(
            c if (c.isalnum() or c in ("-", "_")) else "_" for c in str(clip_id)
        )[:48] or "clip"
        removed = 0
        for path in list(self.directory.glob(safe + "_*.mp4")):
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
        return removed

    def invalidate_all(self):
        """Vide tout le cache disque."""
        removed = 0
        for path in list(self.directory.glob("*.mp4")):
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
        return removed

    def evict_if_needed(self):
        """Eviction LRU (mtime) jusqu'au budget."""
        import os as _os

        files = []
        total = 0
        for path in self.directory.glob("*.mp4"):
            try:
                stat = path.stat()
            except OSError:
                continue
            files.append((stat.st_mtime_ns, stat.st_size, path))
            total += stat.st_size
        files.sort(key=lambda item: item[0])
        evicted = 0
        index = 0
        # Si un seul segment depasse deja le budget, on le garde
        # (mieux vaut un apercu que rien) ; l'eviction ne s'applique
        # qu'a partir de 2 fichiers.
        while total > self.budget_bytes and len(files) - evicted > 1:
            if index >= len(files):
                break
            _mtime, size, path = files[index]
            index += 1
            try:
                _os.remove(path)
                total -= size
                evicted += 1
            except OSError:
                pass
        return evicted

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
        """Compteurs + occupation disque."""
        total = 0
        count = 0
        for path in self.directory.glob("*.mp4"):
            try:
                total += path.stat().st_size
                count += 1
            except OSError:
                pass
        return {
            "entries": count,
            "bytes": total,
            "hits": self.hits,
            "misses": self.misses,
            "budget_bytes": self.budget_bytes,
        }
