"""Cache mémoire borné, partagé par les médias et les calculs coûteux.

Le cache est volontairement simple : une clé stable, une taille
estimée, un espace de noms pour invalider un groupe, et une éviction
LRU dès que le nombre d'entrées ou le budget d'octets est dépassé.
Il ne connaît ni les images ni FFmpeg. Les miniatures, formes d'onde,
métadonnées et proxies pourront s'y loger plus tard sans changer
l'interface.

Un cache sans plafond grossit avec le projet. Ici le plafond est
obligatoire. ``size_bytes`` est une estimation fournie par l'appelant :
mieux vaut une borne haute que prétendre mesurer des objets Python.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass


@dataclass
class CacheStats:
    """Photo instantanée du cache, utile au diagnostic."""

    entries: int
    bytes: int
    hits: int
    misses: int
    budget_bytes: int


class MemoryCache:
    """Cache LRU avec budget d'octets et espaces de noms."""

    def __init__(
        self,
        *,
        max_bytes: int = 256 * 1024 * 1024,
        max_entries: int = 4096,
    ) -> None:
        if max_bytes <= 0:
            raise ValueError("Le budget du cache doit être strictement positif.")
        if max_entries <= 0:
            raise ValueError("Le nombre d'entrées doit être strictement positif.")
        self._max_bytes = int(max_bytes)
        self._max_entries = int(max_entries)
        self._entries: OrderedDict[str, tuple[object, int, str]] = OrderedDict()
        self._bytes = 0
        self.hits = 0
        self.misses = 0

    @property
    def max_bytes(self) -> int:
        return self._max_bytes

    def set_budget(self, max_bytes: int, max_entries: int | None = None) -> None:
        """Réduit ou agrandit le plafond, puis évince le surplus."""
        if max_bytes <= 0:
            raise ValueError("Le budget du cache doit être strictement positif.")
        self._max_bytes = int(max_bytes)
        if max_entries is not None:
            if max_entries <= 0:
                raise ValueError("Le nombre d'entrées doit être strictement positif.")
            self._max_entries = int(max_entries)
        self._evict()

    def get(self, key: str):
        """Retourne la valeur ou ``None`` si la clé est absente."""
        item = self._entries.get(key)
        if item is None:
            self.misses += 1
            return None
        self.hits += 1
        self._entries.move_to_end(key)
        return item[0]

    def put(
        self,
        key: str,
        value: object,
        *,
        size_bytes: int,
        namespace: str = "project",
    ) -> None:
        """Insère ``value``. Une clé déjà présente est remplacée."""
        size = max(1, int(size_bytes))
        if key in self._entries:
            self._bytes -= self._entries[key][1]
            del self._entries[key]
        self._entries[key] = (value, size, namespace)
        self._bytes += size
        self._entries.move_to_end(key)
        self._evict()

    def invalidate(self, key: str) -> None:
        """Retire une clé. Ne fait rien si elle est absente."""
        item = self._entries.pop(key, None)
        if item is not None:
            self._bytes -= item[1]

    def invalidate_prefix(self, prefix: str) -> None:
        """Retire toutes les clés qui commencent par ``prefix``."""
        for key in [key for key in self._entries if key.startswith(prefix)]:
            self.invalidate(key)

    def clear_namespace(self, namespace: str) -> None:
        """Libère un groupe entier, par exemple à la fermeture d'un projet."""
        for key, item in list(self._entries.items()):
            if item[2] == namespace:
                self._bytes -= item[1]
                del self._entries[key]

    def clear(self) -> None:
        """Vide le cache. Les compteurs de hits restent lisibles."""
        self._entries.clear()
        self._bytes = 0

    def stats(self) -> CacheStats:
        return CacheStats(
            entries=len(self._entries),
            bytes=self._bytes,
            hits=self.hits,
            misses=self.misses,
            budget_bytes=self._max_bytes,
        )

    def _evict(self) -> None:
        while self._entries and (
            self._bytes > self._max_bytes or len(self._entries) > self._max_entries
        ):
            _key, (_value, size, _namespace) = self._entries.popitem(last=False)
            self._bytes -= size
