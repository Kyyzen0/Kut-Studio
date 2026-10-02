"""Cache de textures GPU : budget, LRU, purge, comptage des fuites.

Ne garde que ce qui **resservira** sans changer : mattes de masques fixes,
images de calques immobiles. Les images vidéo ne sont jamais mises en cache
(chaque image est nouvelle ; leurs textures sont réutilisées par
redimensionnement, voir ``ui/gpu_preview.py``).

Mesuré (``tools/perf/gpu_bench.py``) : réutiliser la matte d'un masque fixe
évite une rastérisation Qt **et** un envoi par image ; c'est la seule raison
d'être de ce cache.

Garanties :

- jamais plus de ``budget_bytes`` octets vivants (une entrée plus grosse que
  le budget n'est pas gardée) ;
- l'entrée la moins récemment utilisée part en premier ;
- chaque texture créée est libérée une fois (``created == released + vivantes``),
  vérifié par les tests sur N cycles ;
- :meth:`purge` vide tout (changement de projet, pression mémoire, perte du
  périphérique).

Module pur : les textures sont des objets opaques créés et libérés par des
fonctions fournies par le backend.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass

DEFAULT_BUDGET_BYTES = 256 * 1024 * 1024
MIN_BUDGET_BYTES = 16 * 1024 * 1024


def default_budget(system_memory: int | None) -> int:
    """Budget Auto : 1/16 de la mémoire (unifiée ou non), entre 64 et 512 Mo."""
    if not system_memory:
        return DEFAULT_BUDGET_BYTES
    return int(max(64 * 1024 * 1024, min(512 * 1024 * 1024, system_memory // 16)))


@dataclass(frozen=True)
class CacheStats:
    entries: int
    bytes: int
    budget_bytes: int
    hits: int
    misses: int
    evictions: int
    created: int
    released: int

    @property
    def live(self) -> int:
        return self.created - self.released


class GpuTextureCache:
    def __init__(self, budget_bytes: int = DEFAULT_BUDGET_BYTES,
                 release: Callable[[object], None] | None = None) -> None:
        self._budget = max(MIN_BUDGET_BYTES, int(budget_bytes))
        self._release = release or (lambda _handle: None)
        self._items: OrderedDict[object, tuple[object, int]] = OrderedDict()
        self._bytes = 0
        self._lock = threading.RLock()
        self.hits = self.misses = self.evictions = self.created = self.released = 0

    @property
    def budget_bytes(self) -> int:
        return self._budget

    def set_budget(self, budget_bytes: int) -> None:
        with self._lock:
            self._budget = max(MIN_BUDGET_BYTES, int(budget_bytes))
            self._trim(self._budget)

    def get(self, key: object):
        with self._lock:
            entry = self._items.get(key)
            if entry is None:
                self.misses += 1
                return None
            self._items.move_to_end(key)
            self.hits += 1
            return entry[0]

    def acquire(self, key: object, size_bytes: int, create: Callable[[], object]):
        """Texture de ``key`` ; ``create()`` n'est appelé qu'en cas d'absence.

        Une texture plus grosse que le budget est rendue **sans** être gardée :
        l'appelant doit alors la libérer lui-même (``cached=False``).
        Retourne ``(texture, cached)``.
        """
        with self._lock:
            entry = self._items.get(key)
            if entry is not None:
                self._items.move_to_end(key)
                self.hits += 1
                return entry[0], True
            self.misses += 1
        handle = create()
        size = max(0, int(size_bytes))
        with self._lock:
            self.created += 1
            if size > self._budget:
                return handle, False
            self._trim(self._budget - size)
            self._items[key] = (handle, size)
            self._bytes += size
        return handle, True

    def discard_uncached(self, handle) -> None:
        """Libère une texture rendue par :meth:`acquire` avec ``cached=False``."""
        with self._lock:
            self.released += 1
        self._release(handle)

    def invalidate(self, predicate: Callable[[object], bool]) -> int:
        with self._lock:
            victims = [key for key in self._items if predicate(key)]
            handles = [self._pop(key) for key in victims]
        for handle in handles:
            self._release(handle)
        return len(handles)

    def purge(self) -> int:
        """Libère tout ; retourne les octets rendus."""
        with self._lock:
            freed = self._bytes
            handles = [self._pop(key) for key in list(self._items)]
        for handle in handles:
            self._release(handle)
        return freed

    def stats(self) -> CacheStats:
        with self._lock:
            return CacheStats(len(self._items), self._bytes, self._budget, self.hits, self.misses,
                              self.evictions, self.created, self.released)

    def _pop(self, key):
        handle, size = self._items.pop(key)
        self._bytes -= size
        self.released += 1
        return handle

    def _trim(self, limit: int) -> None:
        while self._items and self._bytes > max(0, limit):
            key = next(iter(self._items))
            handle = self._pop(key)
            self.evictions += 1
            self._release(handle)


__all__ = ["DEFAULT_BUDGET_BYTES", "CacheStats", "GpuTextureCache", "default_budget"]
