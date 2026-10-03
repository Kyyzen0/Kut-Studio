"""Gestion globale des caches : budget disque, éviction LRU, purge, statistiques.

Vue d'ensemble de la stratégie de cache de Kut-Studio
-----------------------------------------------------

=================  ===========================  ==========================  ========================
Couche             Contenu                      Clé / invalidation          Éviction
=================  ===========================  ==========================  ========================
mémoire            sondes, miniatures, ondes    :mod:`core.cache_keys`      LRU, budget d'octets
                                                (signature du fichier)      (profil de performance)
disque « preview » segments d'aperçu fidèles    clip + plage + qualité +    LRU (index), budget
                                                empreinte **du segment**    propre + budget global
disque « mograph » images des calques motion    empreinte de l'état évalué  LRU (dernier usage),
                   graphics (:mod:`core.mograph_stream`)                    budget global
disque « tracking » résultats d'analyse de     média + plage + zone +     LRU (dernier usage),
                   tracking (:mod:`core.tracking_engine`)  réglages + version         budget global
disque « flow »    vecteurs de mouvement par   média analysé + image +    LRU (dernier usage),
                   (:mod:`core.flow_cache`)    grille + moteur            budget global
disque « proxies » proxies médias               chemin source + empreinte   LRU (dernier usage),
                                                du profil + signature       budget global ; les
                                                source (marqueur)           proxies du projet
                                                                            ouvert sont épinglés
=================  ===========================  ==========================  ========================

Aucune couche ne duplique le contenu d'une autre : les miniatures et ondes
ne sont pas des proxies, les segments d'aperçu ne sont pas des images
isolées. Toutes les clés viennent de :mod:`core.cache_keys` (ou de
:mod:`core.preview_cache` pour les segments).

Le :class:`CacheManager` ne stocke rien : il **observe** les trois couches
et applique une politique commune.

- ``max_bytes`` : budget disque global (segments + proxies), configurable ;
- :meth:`enforce` : sous le budget, ne fait rien ; au-dessus, retire d'abord
  les segments d'aperçu (recalculables en quelques secondes), puis les
  proxies les moins récemment utilisés **qui n'appartiennent pas au projet
  ouvert** ;
- :meth:`purge` / :meth:`purge_project` : purge complète ou ciblée.

Les caches ne contiennent jamais de données essentielles : tout peut être
recalculé, et le projet reste ouvrable sans eux.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass

DEFAULT_MAX_BYTES = 4 * 1024 ** 3
KIND_MEMORY = "memory"
KIND_PREVIEW = "preview"
KIND_PROXY = "proxy"
KIND_MOGRAPH = "mograph"
KIND_TRACKING = "tracking"
KIND_MULTICAM = "multicam"
KIND_FLOW = "flow"
KINDS = (KIND_MEMORY, KIND_PREVIEW, KIND_PROXY, KIND_MOGRAPH, KIND_TRACKING, KIND_MULTICAM, KIND_FLOW)


@dataclass(frozen=True)
class CacheUsage:
    """Occupation d'une couche de cache."""

    kind: str
    entries: int
    bytes: int


class CacheManager:
    """Politique commune des caches : budget, éviction, purge, statistiques."""

    def __init__(
        self,
        *,
        memory=None,
        previews=None,
        proxies=None,
        max_bytes: int = DEFAULT_MAX_BYTES,
        pinned_sources: Callable[[], Iterable[str]] | None = None,
        mograph=None,
        tracking=None,
        multicam=None,
        flow=None,
    ) -> None:
        self.memory = memory
        self.previews = previews
        self.proxies = proxies
        self.mograph = mograph
        self.tracking = tracking
        self.multicam = multicam
        self.flow = flow
        self._max_bytes = max(1, int(max_bytes))
        self._pinned = pinned_sources

    # -- réglages ----------------------------------------------------------------

    @property
    def max_bytes(self) -> int:
        return self._max_bytes

    def set_max_bytes(self, max_bytes: int) -> int:
        """Change le budget disque global puis l'applique. Retourne les octets libérés."""
        self._max_bytes = max(1, int(max_bytes))
        return self.enforce()

    # -- statistiques ------------------------------------------------------------

    def usage(self) -> list[CacheUsage]:
        """Occupation de chaque couche (mémoire, segments, proxies)."""
        result: list[CacheUsage] = []
        if self.memory is not None:
            stats = self.memory.stats()
            result.append(CacheUsage(KIND_MEMORY, stats.entries, stats.bytes))
        if self.previews is not None:
            stats = self.previews.stats()
            result.append(CacheUsage(KIND_PREVIEW, int(stats["entries"]), int(stats["bytes"])))
        if self.proxies is not None:
            entries = self.proxies.entries()
            result.append(CacheUsage(KIND_PROXY, len(entries), self.proxies.usage_bytes()))
        if self.mograph is not None:
            stats = self.mograph.stats()
            result.append(CacheUsage(KIND_MOGRAPH, int(stats["entries"]), int(stats["bytes"])))
        if self.tracking is not None:
            stats = self.tracking.stats()
            result.append(CacheUsage(KIND_TRACKING, int(stats["entries"]), int(stats["bytes"])))
        if self.multicam is not None:
            stats = self.multicam.stats()
            result.append(CacheUsage(KIND_MULTICAM, int(stats["entries"]), int(stats["bytes"])))
        if self.flow is not None:
            stats = self.flow.stats()
            result.append(CacheUsage(KIND_FLOW, int(stats["entries"]), int(stats["bytes"])))
        return result

    def disk_bytes(self) -> int:
        """Octets sur disque (segments + proxies) : ce que le budget borne."""
        return sum(item.bytes for item in self.usage() if item.kind != KIND_MEMORY)

    def stats(self) -> dict:
        """Résumé simple pour l'interface et les diagnostics."""
        usage = {item.kind: {"entries": item.entries, "bytes": item.bytes} for item in self.usage()}
        hits = misses = 0
        if self.memory is not None:
            memory_stats = self.memory.stats()
            hits, misses = memory_stats.hits, memory_stats.misses
        return {
            "usage": usage,
            "disk_bytes": self.disk_bytes(),
            "max_bytes": self._max_bytes,
            "memory_hits": hits,
            "memory_misses": misses,
        }

    # -- éviction ------------------------------------------------------------------

    def enforce(self) -> int:
        """Ramène le disque sous le budget. Retourne le nombre d'octets libérés."""
        excess = self.disk_bytes() - self._max_bytes
        if excess <= 0:
            return 0
        freed = 0
        if self.previews is not None:
            freed += int(self.previews.evict_bytes(excess))
        if freed < excess and self.mograph is not None:
            # Images de calques : recalculables, avant les proxies.
            freed += int(self.mograph.evict_bytes(excess - freed))
        if freed < excess and self.tracking is not None:
            # Analyses : petites et recalculables, mais plus lentes que des images.
            freed += int(self.tracking.evict_bytes(excess - freed))
        if freed < excess and self.multicam is not None:
            # Enveloppes audio de synchronisation : quelques Mo, recalculées en une fraction de seconde de décodage.
            freed += int(self.multicam.evict_bytes(excess - freed))
        if freed < excess and self.flow is not None:
            # Vecteurs de mouvement : petits, mais chaque paire coûte des centaines de ms à recalculer ; après les images.
            freed += int(self.flow.evict_bytes(excess - freed))
        if freed >= excess or self.proxies is None:
            return freed
        pinned = {os.path.abspath(p) for p in (self._pinned() if self._pinned else ())}
        for path, size, _last_used in sorted(self.proxies.entries(), key=lambda item: item[2]):
            if freed >= excess:
                break
            if self._is_pinned(path, pinned):
                continue
            if self.proxies.evict_path(path) is not False:   # ``False`` : toujours sur le disque
                freed += size
        return freed

    def _is_pinned(self, proxy_file, pinned: set[str]) -> bool:
        """Un proxy du projet ouvert n'est jamais évincé par le budget."""
        if not pinned:
            return False
        import json

        try:
            marker = json.loads(proxy_file.with_suffix(".json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return os.path.abspath(str(marker.get("source", ""))) in pinned

    # -- purge ------------------------------------------------------------------------

    def purge(self, kind: str = "all") -> int:
        """Vide une couche (``memory``, ``preview``, ``proxy``) ou toutes. Retourne les octets libérés.

        Purger les proxies ne casse aucun projet : l'aperçu lit les originaux.
        """
        if kind not in (*KINDS, "all"):
            raise ValueError(f"Couche de cache inconnue : {kind!r}")
        before = sum(item.bytes for item in self.usage())
        if kind in (KIND_MEMORY, "all") and self.memory is not None:
            self.memory.clear()
        if kind in (KIND_PREVIEW, "all") and self.previews is not None:
            self.previews.purge()
        # Les images de calques sont des dérivés d'aperçu : purgées avec lui.
        if kind in (KIND_PREVIEW, KIND_MOGRAPH, "all") and self.mograph is not None:
            self.mograph.purge()
        if kind in (KIND_TRACKING, "all") and self.tracking is not None:
            self.tracking.purge()
        if kind in (KIND_MULTICAM, "all") and self.multicam is not None:
            self.multicam.purge()
        if kind in (KIND_FLOW, "all") and self.flow is not None:
            self.flow.purge()
        if kind in (KIND_PROXY, "all") and self.proxies is not None:
            self.proxies.delete_all()
        after = sum(item.bytes for item in self.usage())
        return max(0, before - after)

    def purge_project(self, project) -> int:
        """Retire ce qui ne sert qu'à ``project`` : ses proxies, segments et dérivés mémoire.

        Les proxies sont partagés par **chemin source** : ceux d'un média
        aussi utilisé dans un autre projet sont retirés eux aussi (ils se
        régénèrent à la demande).
        """
        before = sum(item.bytes for item in self.usage())
        assets = [a for a in getattr(project, "media_assets", ()) if getattr(a, "path", "")]
        if self.proxies is not None:
            for asset in assets:
                self.proxies.delete(asset.path)
        if self.previews is not None:
            all_tracks = getattr(project, "all_tracks", None)
            tracks = all_tracks() if callable(all_tracks) else project.tracks
            clip_ids = [clip.id for track in tracks for clip in track.clips]
            self.previews.invalidate_clips(clip_ids)
        if self.memory is not None:
            for asset in assets:
                absolute = os.path.abspath(asset.path)
                self.memory.invalidate_prefix(f"thumb:{absolute}:")
                self.memory.invalidate_prefix(f"wave:{absolute}:")
        after = sum(item.bytes for item in self.usage())
        return max(0, before - after)


__all__ = [
    "DEFAULT_MAX_BYTES",
    "KINDS",
    "KIND_MEMORY",
    "KIND_PREVIEW",
    "KIND_MOGRAPH",
    "KIND_FLOW",
    "KIND_MULTICAM",
    "KIND_PROXY",
    "KIND_TRACKING",
    "CacheManager",
    "CacheUsage",
]
