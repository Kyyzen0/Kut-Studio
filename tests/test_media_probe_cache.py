"""Le cache des sondes ne partage jamais un média modifiable entre deux imports.

Régression : le cache stockait et renvoyait la MÊME instance de ``MediaAsset``. Le relink modifie
``asset.path`` sur place : l'entrée du cache devenait celle du nouveau fichier, et importer ensuite l'ancien
chemin (même projet, ou un autre projet de la session) renvoyait le nouveau, sans détection de doublon.
"""

from __future__ import annotations

from core.cache_store import MemoryCache
from core.media_cache import cached_probe
from core.project_model import MediaAsset


def _file(tmp_path, name="a.mp4"):
    path = tmp_path / name
    path.write_bytes(b"x" * 64)
    return str(path)


def _probe_counter():
    calls = []

    def probe(path):
        calls.append(path)
        return MediaAsset(id=f"asset-probe{len(calls)}", path=path, name="a", duration=5.0, width=64, height=64,
                          fps=25.0, media_type="video", has_audio=False)

    return probe, calls


def test_a_second_import_reuses_the_probe_without_sharing_the_object(tmp_path):
    cache, (probe, calls) = MemoryCache(), _probe_counter()
    path = _file(tmp_path)
    first = cached_probe(cache, path, probe)
    second = cached_probe(cache, path, probe)
    assert len(calls) == 1, "la deuxième sonde vient du cache"
    assert first is not second and first.id != second.id
    assert (second.path, second.duration) == (first.path, first.duration)


def test_relinking_an_asset_never_changes_what_the_next_import_gets(tmp_path):
    cache, (probe, _calls) = MemoryCache(), _probe_counter()
    path = _file(tmp_path)
    imported = cached_probe(cache, path, probe)
    imported.path = _file(tmp_path, "b.mp4")                  # relink : modification sur place
    again = cached_probe(cache, path, probe)
    assert again.path == path


def test_editing_a_cached_hit_does_not_leak_into_the_cache(tmp_path):
    cache, (probe, _calls) = MemoryCache(), _probe_counter()
    path = _file(tmp_path)
    cached_probe(cache, path, probe)
    hit = cached_probe(cache, path, probe)
    hit.name = "renommé"
    hit.path = "/ailleurs.mp4"
    assert cached_probe(cache, path, probe).name == "a"
    assert cached_probe(cache, path, probe).path == path
