"""Stratégie de cache des médias.

Les fichiers sources ne sont pas décodés pour être mis en mémoire.
L'import ne demande à ``ffprobe`` que des métadonnées, et le résultat
est réutilisé tant que la taille et la date du fichier n'ont pas
changé.

Toutes les clés de cache (sondes, miniatures, formes d'onde, proxies)
sont définies dans :mod:`core.cache_keys` : une seule source, pour ne
plus dupliquer d'entrées. Le calcul des dérivés passe par
:class:`core.task_queue.TaskQueue`, avec une priorité plus haute pour ce
qui est visible à l'écran.

Les métadonnées de sonde vivent dans l'espace ``global`` (un même
fichier sert à plusieurs projets). Les futurs aperçus dérivés d'un
projet vivent dans l'espace ``project`` et sont vidés à la fermeture.
"""

from __future__ import annotations

from .cache_keys import probe_key
from .cache_store import MemoryCache
from .media_probe import probe_media
from .project_model import MediaAsset


PROBE_NAMESPACE = "global"
PROJECT_NAMESPACE = "project"


def probe_cache_key(path: str) -> str | None:
    """Clé stable d'une sonde, ou ``None`` si le fichier est illisible."""
    return probe_key(path)


def cached_probe(cache: MemoryCache, path: str, probe=None) -> MediaAsset:
    """Sonde ``path`` en réutilisant le cache si le fichier n'a pas changé.

    La sonde reste synchrone : ``ffprobe`` ne lit que l'en-tête, et
    l'import doit pouvoir signaler une erreur tout de suite. Le gain
    est d'éviter un second appel sur le même fichier.

    ``probe`` permet à l'interface de passer sa propre fonction, que
    les tests remplacent. La valeur par défaut lit ``ffprobe``.
    """
    probe_fn = probe_media if probe is None else probe
    key = probe_cache_key(path)
    if key is not None:
        cached = cache.get(key)
        if isinstance(cached, MediaAsset):
            return cached
    asset = probe_fn(path)
    if key is not None:
        cache.put(key, asset, size_bytes=512, namespace=PROBE_NAMESPACE)
    return asset
