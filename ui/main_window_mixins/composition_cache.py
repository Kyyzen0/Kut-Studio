"""Cache de rendu des compositions, côté fenêtre : planifier les morceaux, les servir au moniteur en direct.

Voir :mod:`core.composition_cache`. À l'arrêt, chaque composition de la séquence a ses morceaux demandés au moteur
d'aperçu (celui sous la tête de lecture d'abord, juste après le segment fidèle) ; une composition modifiée remplace
ses demandes en attente. Le moniteur en direct lit un morceau prêt à la place de la source principale
(:meth:`_composition_cache_source`, appelé par l'index de la timeline à chaque image).
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field

LOGGER = logging.getLogger(__name__)

# Urgence dans la file du moteur (plus petit = plus urgent) : le segment fidèle sous la tête passe avant (0), le
# morceau sous la tête avant les segments voisins (10), puis la suite de cette composition, puis les autres.
_PRIORITY_AT_PLAYHEAD = 5
_PRIORITY_SAME_COMPOSITION = 30
_PRIORITY_OTHER = 100

# Un morceau lu en lecture repasse par le cache disque (durée de vie, dernier usage pour le LRU) au plus une fois par
# seconde : à chaque image, ce serait une écriture sur disque 25 fois par seconde.
_LOOKUP_SECONDS = 1.0


@dataclass
class _CompositionCache:
    """Les morceaux d'une composition, pour la version de son contenu rendue (objet immuable : une édition en crée
    un autre, l'identité suffit à reconnaître la version)."""

    composition: object
    signature: tuple
    jobs: list
    paths: dict[int, str] = field(default_factory=dict)
    looked_up: dict[int, float] = field(default_factory=dict)


class CompositionCacheMixin:
    def _composition_caches(self) -> dict[str, _CompositionCache]:
        caches = getattr(self, "_comp_caches", None)
        if caches is None:
            caches = {}
            self._comp_caches = caches
        return caches

    def _composition_view_of(self, clip_id: str) -> str:
        """Le nœud que la page Composition montre pour ce clip (``""`` : la sortie)."""
        views = self._composition_preview_overrides() or {}
        return str(getattr(views.get(clip_id), "node_id", "") or "")

    def _composition_cache_signature(self, site) -> tuple:
        """Ce qui, hors du contenu de la composition, change ses morceaux : nœud montré, qualité, cadre, et les médias
        lus **avec leur identité sur disque** (date, taille) : un fichier remplacé au même chemin refait les morceaux,
        comme il refait les segments fidèles."""
        paths = {asset.id: asset.path for asset in self.project.media_assets}
        sequence = site.sequence
        return (
            self._composition_view_of(site.clip.id), str(self._render_quality), sequence.width, sequence.height,
            float(sequence.fps), str(self._flow_preference()),
            tuple(sorted(_file_identity(paths.get(asset_id, "")) for asset_id in site.clip.media_ids())),
        )

    def _schedule_composition_caches(self, center: float) -> None:
        """À l'arrêt : demande au moteur les morceaux manquants des compositions de la séquence. Jamais d'exception :
        sans morceau, le moniteur garde la source principale."""
        engine = getattr(self, "preview_engine", None)
        if engine is None or bool(getattr(self, "is_playing", False)):
            return
        try:
            engine.set_playing(False)                       # à l'arrêt, les rendus reprennent (comme les segments)
            self._request_composition_chunks(engine, float(center))
        except Exception:
            LOGGER.debug("Morceaux des compositions non planifiés : le moniteur garde leur source principale",
                         exc_info=True)

    def _request_composition_chunks(self, engine, center: float) -> None:
        from core.composition_cache import cache_owner, chunk_count, chunk_index, composition_chunk_job, \
            composition_sites
        from core.sequences import nested_source_time

        caches = self._composition_caches()
        active = self.project.active_sequence
        seen: set[str] = set()
        resolver = self._preview_resolver()
        for site in composition_sites(self.project):
            clip = site.clip
            seen.add(clip.id)
            signature = self._composition_cache_signature(site)
            cache = caches.get(clip.id)
            if cache is None or cache.composition is not clip.composition or cache.signature != signature:
                try:
                    jobs = [composition_chunk_job(self.project, site, index, quality=self._render_quality,
                                                  resolver=resolver, flow_preference=self._flow_preference(),
                                                  view=signature[0])
                            for index in range(chunk_count(clip.composition.duration))]
                except Exception:
                    LOGGER.debug("Morceaux de la composition %s non construits : le moniteur garde sa source "
                                 "principale", clip.id, exc_info=True)
                    caches.pop(clip.id, None)
                    continue
                cache = _CompositionCache(clip.composition, signature, jobs)
                caches[clip.id] = cache
                # Une version plus ancienne en file ne servira plus ; ce qui est sur disque reste (annuler la resservira).
                engine.cancel_owner(cache_owner(clip.id), keep=[job.key for job in jobs if job is not None])
            current = None
            if site.sequence is active:
                inner = nested_source_time(clip, float(center))
                if inner is not None and 0.0 <= inner < clip.composition.duration:
                    current = chunk_index(inner)
            for index, job in enumerate(cache.jobs):
                if job is None or index in cache.paths:
                    continue
                if current is None:
                    priority = _PRIORITY_OTHER + index
                elif index == current:
                    priority = _PRIORITY_AT_PLAYHEAD
                else:
                    priority = _PRIORITY_SAME_COMPOSITION + abs(index - current)
                try:
                    result = engine.request(job, priority)
                except Exception:
                    LOGGER.debug("Morceau %s de la composition %s non demandé", index, clip.id, exc_info=True)
                    continue
                if result.get("status") == "cached":
                    cache.paths[index] = str(result["path"])
        for clip_id in [clip_id for clip_id in caches if clip_id not in seen]:   # clip supprimé, masqué, désactivé
            del caches[clip_id]
            engine.cancel_owner(cache_owner(clip_id))

    def _refresh_composition_cache_paths(self) -> None:
        """Un rendu vient de finir : relève les morceaux arrivés sur disque."""
        engine = getattr(self, "preview_engine", None)
        if engine is None:
            return
        for cache in self._composition_caches().values():
            for index, job in enumerate(cache.jobs):
                if job is None or index in cache.paths:
                    continue
                path = engine.cache.path_for(job.key)
                if path.is_file():
                    cache.paths[index] = str(path)

    def _composition_cache_source(self, clip, inner_time: float):
        """Le morceau prêt de la composition de ``clip`` qui couvre ``inner_time`` : ``(fichier, début, fin)`` en temps
        de la composition, ou ``None`` (pas encore rendu, ou rendu pour une autre version, une autre qualité, un
        autre nœud montré). Appelé à chaque image de la lecture : une recherche dans un dictionnaire et un ``stat``."""
        from core.composition_cache import CHUNK_SECONDS, chunk_index

        cache = self._composition_caches().get(clip.id)
        if cache is None or cache.composition is not clip.composition:
            return None
        if cache.signature[0] != self._composition_view_of(clip.id) or cache.signature[1] != str(self._render_quality):
            return None
        index = chunk_index(inner_time)
        path = cache.paths.get(index)
        if path is None:
            return None
        now = time.monotonic()
        if now - cache.looked_up.get(index, float("-inf")) >= _LOOKUP_SECONDS:
            # Par le cache disque : un morceau périmé n'est plus servi, celui qu'on lit reste le plus récent du LRU.
            engine = getattr(self, "preview_engine", None)
            found = engine.cache.lookup(cache.jobs[index].key) if engine is not None else None
            cache.looked_up[index] = now
            if found is None:
                cache.paths.pop(index, None)                # il sera redemandé à l'arrêt
                return None
            path = cache.paths[index] = str(found)
        elif not os.path.isfile(path):                      # évincé entre deux passages par le cache
            cache.paths.pop(index, None)
            return None
        return path, index * CHUNK_SECONDS, (index + 1) * CHUNK_SECONDS


def _file_identity(path: str) -> tuple:
    try:
        stat = os.stat(path)
    except OSError:
        return (path, None, None)
    return (path, stat.st_mtime_ns, stat.st_size)
