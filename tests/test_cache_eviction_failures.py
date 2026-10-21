"""Un fichier de cache impossible à supprimer n'est ni oublié ni compté comme libéré.

Sous Windows, un segment que le lecteur est en train de lire (ou qu'un antivirus inspecte) ne se supprime pas.
Le cache d'aperçu retirait pourtant l'entrée de son index et comptait ses octets comme libérés ; les proxies
faisaient de même par l'intermédiaire du gestionnaire de cache. Le budget se croyait respecté alors que le
disque ne l'était pas, et l'éviction globale s'arrêtait trop tôt.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from core.cache_manager import CacheManager
from core.preview_cache import DiskPreviewCache, PreviewSegmentKey


def _key(clip: str) -> PreviewSegmentKey:
    return PreviewSegmentKey(clip_id=clip, start=0.0, end=2.0, quality="standard", params_hash="p")


@pytest.fixture
def locked(monkeypatch):
    """Noms de fichiers que ``Path.unlink`` refuse de supprimer (comme un fichier tenu ouvert)."""
    names: set[str] = set()
    real_unlink = Path.unlink

    def unlink(self, *args, **kwargs):
        if self.name in names:
            raise PermissionError(13, "Le fichier est utilisé par un autre processus", str(self))
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink)
    return names


def _cache_with(tmp_path, clips: list[str]) -> tuple[DiskPreviewCache, dict[str, str]]:
    cache = DiskPreviewCache(directory=tmp_path / "previews", ttl_seconds=0, budget_bytes=10_000)
    source = tmp_path / "segment.mp4"
    source.write_bytes(b"x" * 100)
    names: dict[str, str] = {}
    for age, clip in enumerate(clips):
        cache.store(_key(clip), str(source))
        names[clip] = cache.path_for(_key(clip)).name
        stamp = 1_700_000_000 + age                      # le premier est le plus ancien
        os.utime(cache.path_for(_key(clip)), (stamp, stamp))
        cache._index[names[clip]][1] = stamp * 10**9
    return cache, names


def test_evict_bytes_only_counts_what_was_really_deleted(tmp_path, locked):
    cache, names = _cache_with(tmp_path, ["old", "mid", "new"])
    locked.add(names["old"])                              # le plus ancien est tenu ouvert

    freed = cache.evict_bytes(100)

    assert freed == 100                                   # « mid » a été supprimé à sa place
    assert cache.path_for(_key("old")).is_file() and not cache.path_for(_key("mid")).is_file()
    assert names["old"] in cache._index                   # l'entrée n'est pas oubliée
    assert cache.stats()["bytes"] == 200                  # ce qui reste réellement sur le disque


def test_evict_bytes_terminates_when_nothing_can_be_deleted(tmp_path, locked):
    cache, names = _cache_with(tmp_path, ["a", "b"])
    locked.update(names.values())
    assert cache.evict_bytes(10_000) == 0
    assert cache.stats()["entries"] == 2


def test_budget_eviction_skips_a_locked_segment_and_still_terminates(tmp_path, locked):
    cache, names = _cache_with(tmp_path, ["old", "mid", "new"])
    cache.budget_bytes = 150                              # baissé après coup : ``store`` appliquerait déjà le budget
    locked.add(names["old"])

    evicted = cache.evict_if_needed()

    assert evicted == 1
    assert cache.path_for(_key("old")).is_file()
    assert cache.stats()["entries"] == 2 and cache.stats()["bytes"] == 200


def test_invalidation_counts_only_removed_segments(tmp_path, locked):
    cache, names = _cache_with(tmp_path, ["a", "b", "c"])
    locked.add(names["b"])

    assert cache.invalidate_clip("a") == 1
    assert cache.invalidate_clip("b") == 0
    assert names["b"] in cache._index
    assert cache.invalidate_clips(["b", "c"]) == 1        # « c » part, « b » reste
    assert cache.invalidate_all() == 0
    assert cache.stats()["entries"] == 1 and cache.path_for(_key("b")).is_file()
    locked.clear()
    assert cache.invalidate_all() == 1 and cache.stats()["entries"] == 0


def test_a_file_already_gone_counts_as_removed(tmp_path):
    cache, names = _cache_with(tmp_path, ["a"])
    cache.path_for(_key("a")).unlink()                    # supprimé à la main entre-temps
    assert cache.invalidate_all() == 1
    assert cache.stats()["entries"] == 0


# --- proxies et gestionnaire de cache --------------------------------------------------------------------


class _Proxies:
    """Proxies simulés : « tenu » ne peut pas être supprimé."""

    def __init__(self, sizes: dict[str, int], stuck: set[str]) -> None:
        self.sizes, self.stuck, self.evicted = dict(sizes), set(stuck), []

    def entries(self):
        return [(name, size, index) for index, (name, size) in enumerate(self.sizes.items())]

    def usage_bytes(self):
        return sum(self.sizes.values())

    def evict_path(self, path):
        if path in self.stuck:
            return False
        self.evicted.append(path)
        self.sizes.pop(path)
        return True


def test_the_global_budget_does_not_count_an_undeletable_proxy_as_freed():
    proxies = _Proxies({"tenu": 400, "libre": 400}, stuck={"tenu"})
    manager = CacheManager(proxies=proxies, max_bytes=500)          # 800 octets pour un budget de 500

    freed = manager.enforce()

    assert proxies.evicted == ["libre"]
    assert freed == 400                                              # pas 800 : « tenu » est toujours sur le disque
    assert manager.disk_bytes() == 400


def test_the_proxy_manager_reports_whether_the_proxy_is_gone(tmp_path, locked):
    from core.proxy_manager import ProxyManager

    manager = ProxyManager(tmp_path / "proxies", ffmpeg_command=lambda: ["fake"], runner=lambda *a: None)
    proxy = tmp_path / "proxies" / "proxy-abc.mp4"
    proxy.parent.mkdir(exist_ok=True)
    proxy.write_bytes(b"p" * 10)
    locked.add(proxy.name)
    assert manager.evict_path(proxy) is False and proxy.is_file()
    locked.clear()
    assert manager.evict_path(proxy) is True and not proxy.exists()
    assert manager.evict_path(proxy) is True                         # déjà absent : rien à libérer, rien à garder
    manager.shutdown()
