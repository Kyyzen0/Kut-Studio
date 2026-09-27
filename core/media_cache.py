"""Stratégie de cache des médias.

Les fichiers sources ne sont pas décodés pour être mis en mémoire.
L'import ne demande à ``ffprobe`` que des métadonnées, et le résultat
est réutilisé tant que la taille et la date du fichier n'ont pas
changé.

Les clés de miniatures, de formes d'onde et de proxies sont définies
ici pour que les prochains travaux les partagent. Leur calcul n'est
pas lancé : il devra passer par :class:`core.task_queue.TaskQueue`,
avec une priorité plus haute pour ce qui est visible à l'écran.

Les métadonnées de sonde vivent dans l'espace ``global`` (un même
fichier sert à plusieurs projets). Les futurs aperçus dérivés d'un
projet vivent dans l'espace ``project`` et sont vidés à la fermeture.
"""

from __future__ import annotations

import os

from .cache_store import MemoryCache
from .media_probe import probe_media
from .project_model import MediaAsset


PROBE_NAMESPACE = "global"
PROJECT_NAMESPACE = "project"


def probe_cache_key(path: str) -> str | None:
    """Clé stable d'une sonde, ou ``None`` si le fichier est illisible."""
    try:
        stat = os.stat(path)
    except OSError:
        return None
    absolute = os.path.abspath(path)
    return f"probe:{absolute}:{stat.st_mtime_ns}:{stat.st_size}"


def thumbnail_cache_key(path: str, time_seconds: float, width: int) -> str:
    """Clé d'une miniature. Le décodage n'est pas fait ici."""
    return f"thumb:{os.path.abspath(path)}:{round(float(time_seconds), 3)}:{int(width)}"


def waveform_cache_key(path: str, pixels_per_second: float) -> str:
    """Clé d'une forme d'onde. Le calcul audio n'est pas fait ici."""
    return f"wave:{os.path.abspath(path)}:{round(float(pixels_per_second), 3)}"


def proxy_cache_key(path: str, divisor: int) -> str:
    """Clé d'un proxy basse résolution. Le transcodage n'est pas fait ici."""
    return f"proxy:{os.path.abspath(path)}:1/{max(1, int(divisor))}"


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
