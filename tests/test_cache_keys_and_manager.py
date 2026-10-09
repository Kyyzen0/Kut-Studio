"""Clés de cache déterministes, mémo de signatures, index disque, gestionnaire global."""

from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest

from core.cache_keys import (
    MISSING_TOKEN,
    SignatureMemo,
    audio_envelope_key,
    file_exists,
    probe_key,
    proxy_key,
    thumbnail_key,
)
from core.cache_manager import KIND_PREVIEW, KIND_PROXY, CacheManager
from core.cache_store import MemoryCache
from core.media_previews import audio_envelope_cache_key, thumbnail_cache_key
from core.preview_cache import DiskPreviewCache, PreviewSegmentKey
from core.project_model import Clip, MediaAsset, Project, Track


class _Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


# --- SignatureMemo ---------------------------------------------------------------------


def test_memo_caches_stat_calls_until_the_ttl_expires(tmp_path):
    clock = _Clock()
    memo = SignatureMemo(ttl=2.0, clock=clock)
    media = tmp_path / "a.mp4"
    media.write_bytes(b"x" * 10)
    first = memo.get(str(media))
    for _ in range(50):
        assert memo.get(str(media)) == first
    assert memo.stat_calls == 1
    clock.now = 3.0
    media.write_bytes(b"x" * 20)
    assert memo.get(str(media)).size == 20 and memo.stat_calls == 2


def test_memo_invalidate_forces_a_fresh_read(tmp_path):
    memo = SignatureMemo(ttl=1000.0, clock=_Clock())
    media = tmp_path / "a.mp4"
    media.write_bytes(b"x")
    assert memo.exists(str(media))
    media.unlink()
    assert memo.exists(str(media))            # encore dans le TTL
    memo.invalidate(str(media))
    assert not memo.exists(str(media))
    memo.invalidate()                          # tout oublier ne lève rien


def test_memo_reports_a_missing_file_as_none_without_raising(tmp_path):
    memo = SignatureMemo()
    assert memo.get(str(tmp_path / "absent.mp4")) is None
    assert not file_exists("") and not file_exists(str(tmp_path / "absent.mp4"))


def test_memo_is_safe_from_several_threads(tmp_path):
    memo = SignatureMemo()
    media = tmp_path / "a.mp4"
    media.write_bytes(b"x")
    errors = []

    def worker():
        try:
            for _ in range(200):
                assert memo.get(str(media)) is not None
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors


# --- Clés ----------------------------------------------------------------------------------


def test_derived_keys_change_with_the_source_file_and_only_with_it(tmp_path):
    memo = SignatureMemo(ttl=0.0)
    media = tmp_path / "a.mp4"
    other = tmp_path / "b.mp4"
    media.write_bytes(b"x" * 10)
    other.write_bytes(b"y" * 10)
    before_thumb = thumbnail_key(str(media), 1.234, 160, memo)
    before_wave = audio_envelope_key(str(media), memo)
    untouched = thumbnail_key(str(other), 1.234, 160, memo)
    media.write_bytes(b"x" * 11)
    assert thumbnail_key(str(media), 1.234, 160, memo) != before_thumb
    assert audio_envelope_key(str(media), memo) != before_wave
    assert thumbnail_key(str(other), 1.234, 160, memo) == untouched   # invalidation précise


def test_keys_are_deterministic_and_quantize_the_time(tmp_path):
    memo = SignatureMemo(ttl=0.0)
    media = tmp_path / "a.mp4"
    media.write_bytes(b"x")
    assert thumbnail_key(str(media), 1.04, 160, memo) == thumbnail_key(str(media), 0.96, 160, memo)
    assert thumbnail_key(str(media), 1.0, 160, memo) != thumbnail_key(str(media), 1.0, 320, memo)
    assert audio_envelope_key(str(media), memo) == audio_envelope_key(str(media), memo)


def test_missing_files_get_a_stable_key_instead_of_an_error(tmp_path):
    path = str(tmp_path / "nope.mp4")
    assert MISSING_TOKEN in thumbnail_key(path, 0, 160, SignatureMemo())
    assert probe_key(path) is None


def test_media_previews_and_cache_keys_are_the_single_shared_definition(tmp_path):
    media = tmp_path / "a.mp4"
    media.write_bytes(b"x")
    from core.cache_keys import default_signatures

    default_signatures.invalidate()
    assert thumbnail_cache_key(str(media), 2.0, 160) == thumbnail_key(str(media), 2.0, 160)
    assert audio_envelope_cache_key(str(media)) == audio_envelope_key(str(media))
    import core.media_cache as media_cache

    # Les anciennes définitions dupliquées ont disparu.
    assert not hasattr(media_cache, "thumbnail_cache_key")
    assert not hasattr(media_cache, "waveform_cache_key")
    assert not hasattr(media_cache, "proxy_cache_key")
    assert media_cache.probe_cache_key(str(media)) == probe_key(str(media))


def test_proxy_key_depends_on_the_source_path_and_the_profile_fingerprint_only():
    assert proxy_key("/a.mp4", "f1") == proxy_key("/a.mp4", "f1")
    assert proxy_key("/a.mp4", "f1") != proxy_key("/a.mp4", "f2")
    assert proxy_key("/a.mp4", "f1") != proxy_key("/b.mp4", "f1")
    assert proxy_key("/a.mp4", "f1").startswith("proxy-")


# --- Cache mémoire (existant) : LRU, limite, purge ---------------------------------------------------------


def test_memory_cache_lru_size_limit_and_prefix_purge():
    cache = MemoryCache(max_bytes=100, max_entries=100)
    for index in range(10):
        cache.put(f"thumb:/m/a:{index}", b"x", size_bytes=30, namespace="project")
    assert cache.stats().bytes <= 100 and cache.stats().entries <= 3   # la limite d'octets est tenue
    assert cache.get("thumb:/m/a:9") is not None and cache.get("thumb:/m/a:0") is None
    cache.invalidate_prefix("thumb:/m/a:")
    assert cache.stats().entries == 0 and cache.stats().bytes == 0
    cache.put("k", 1, size_bytes=1)
    hits = cache.stats().hits
    assert cache.get("k") == 1 and cache.stats().hits == hits + 1


# --- Cache disque des segments : index ----------------------------------------------------------------------------


def _key(n, clip="c"):
    return PreviewSegmentKey(f"{clip}{n}", float(n), float(n) + 2.0, "standard", "h")


def _store(cache, n, size=100, clip="c", tmp_path=None):
    source = (tmp_path or cache.directory.parent) / f"src{n}.mp4"
    source.write_bytes(b"x" * size)
    return cache.store(_key(n, clip), source)


def test_store_does_not_rescan_the_directory(tmp_path, monkeypatch):
    cache = DiskPreviewCache(directory=tmp_path / "c", budget_bytes=10 ** 9)
    _store(cache, 0)                      # charge l'index
    scans = []
    original = cache.rescan
    monkeypatch.setattr(cache, "rescan", lambda: (scans.append(1), original())[1])
    for n in range(1, 30):
        _store(cache, n)
    assert scans == []                    # plus de parcours du dossier à chaque rendu
    assert cache.stats()["entries"] == 30 and cache.stats()["bytes"] == 3000


def test_lru_eviction_removes_the_least_recently_used_segment_first(tmp_path):
    cache = DiskPreviewCache(directory=tmp_path / "c", budget_bytes=350)
    for n in range(3):
        _store(cache, n)                  # 300 octets
    cache.lookup(_key(0))                 # « 0 » redevient récent
    _store(cache, 3)                      # dépasse : « 1 » est le plus ancien
    assert cache.lookup(_key(1)) is None
    assert cache.lookup(_key(0)) is not None and cache.lookup(_key(3)) is not None
    assert cache.stats()["bytes"] <= 350


def test_a_single_oversized_segment_is_kept(tmp_path):
    cache = DiskPreviewCache(directory=tmp_path / "c", budget_bytes=10)
    _store(cache, 0, size=500)
    assert cache.lookup(_key(0)) is not None


def test_externally_deleted_files_are_detected(tmp_path):
    cache = DiskPreviewCache(directory=tmp_path / "c", budget_bytes=10 ** 9)
    path = _store(cache, 0)
    _store(cache, 1)
    path.unlink()                         # supprimé à la main
    assert cache.lookup(_key(0)) is None  # un échec propre, pas une exception
    assert cache.stats()["entries"] == 1 and cache.stats()["bytes"] == 100


def test_files_added_by_another_process_are_picked_up_by_stats(tmp_path):
    cache = DiskPreviewCache(directory=tmp_path / "c", budget_bytes=10 ** 9)
    _store(cache, 0)
    cache.path_for(_key(9)).write_bytes(b"z" * 40)   # écrit hors du cache
    before = os.stat(cache.directory).st_mtime_ns
    # Date explicitement différente : la résolution des horodatages (Windows) peut masquer un ajout immédiat.
    os.utime(cache.directory, ns=(before + 10**9, before + 10**9))
    stats = cache.stats()
    assert stats["entries"] == 2 and stats["bytes"] == 140


def test_invalidation_by_clip_and_purge_update_the_index(tmp_path):
    cache = DiskPreviewCache(directory=tmp_path / "c", budget_bytes=10 ** 9)
    for n in range(3):
        _store(cache, n, clip="alpha")
    for n in range(2):
        _store(cache, 10 + n, clip="beta")
    assert cache.invalidate_clip("alpha0") == 1
    assert cache.invalidate_clips(["alpha1", "alpha2", "beta10"]) == 3
    assert cache.stats()["entries"] == 1
    assert cache.purge() == 1 and cache.stats() == {**cache.stats(), "entries": 0, "bytes": 0}


def test_evict_bytes_frees_at_least_the_requested_amount_oldest_first(tmp_path):
    cache = DiskPreviewCache(directory=tmp_path / "c", budget_bytes=10 ** 9)
    for n in range(5):
        _store(cache, n)
    assert cache.evict_bytes(250) >= 250
    assert cache.stats()["entries"] == 2
    assert cache.lookup(_key(4)) is not None   # les plus récents survivent


# --- CacheManager --------------------------------------------------------------------------------------------------------------


class _FakeProxies:
    """Proxies factices : (chemin, taille, dernier usage)."""

    def __init__(self, directory, sizes):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        self.files = {}
        for index, (name, size, used, source) in enumerate(sizes):
            path = directory / f"proxy-{name}.mp4"
            path.write_bytes(b"p" * size)
            path.with_suffix(".json").write_text(f'{{"source": "{source}"}}', encoding="utf-8")
            self.files[path] = (size, used)
        self.deleted = []
        self.evicted = []

    def entries(self):
        return [(p, s, u) for p, (s, u) in self.files.items()]

    def usage_bytes(self):
        return sum(s for s, _ in self.files.values())

    def evict_path(self, path):
        self.evicted.append(path.name)
        self.files.pop(path, None)

    def delete(self, source):
        self.deleted.append(source)

    def delete_all(self):
        self.files.clear()


def _manager(tmp_path, *, budget, previews_sizes=(), proxy_sizes=(), pinned=()):
    previews = DiskPreviewCache(directory=tmp_path / "prev", budget_bytes=10 ** 12)
    for n, size in enumerate(previews_sizes):
        _store(previews, n, size=size, tmp_path=tmp_path)
    proxies = _FakeProxies(tmp_path / "prox", list(proxy_sizes))
    memory = MemoryCache()
    return CacheManager(memory=memory, previews=previews, proxies=proxies, max_bytes=budget,
                        pinned_sources=lambda: list(pinned)), previews, proxies


def test_usage_reports_each_layer(tmp_path):
    manager, _prev, _prox = _manager(tmp_path, budget=10 ** 9, previews_sizes=[100, 100],
                                     proxy_sizes=[("a", 500, 1, "/m/a.mp4")])
    usage = {item.kind: item for item in manager.usage()}
    assert usage[KIND_PREVIEW].bytes == 200 and usage[KIND_PROXY].bytes == 500
    assert manager.disk_bytes() == 700
    stats = manager.stats()
    assert stats["disk_bytes"] == 700 and stats["max_bytes"] == 10 ** 9


def test_under_budget_nothing_is_evicted(tmp_path):
    manager, previews, proxies = _manager(tmp_path, budget=10 ** 6, previews_sizes=[100] * 3,
                                          proxy_sizes=[("a", 500, 1, "/m/a.mp4")])
    assert manager.enforce() == 0
    assert previews.stats()["entries"] == 3 and proxies.evicted == []


def test_enforce_evicts_previews_before_proxies(tmp_path):
    manager, previews, proxies = _manager(tmp_path, budget=600, previews_sizes=[100] * 3,
                                          proxy_sizes=[("a", 500, 1, "/m/a.mp4")])
    freed = manager.enforce()                       # 800 octets pour un budget de 600
    assert freed >= 200 and manager.disk_bytes() <= 600
    assert proxies.evicted == []                    # les segments suffisent : le proxy reste


def test_proxies_are_evicted_least_recently_used_first_and_pinned_ones_survive(tmp_path):
    manager, _prev, proxies = _manager(
        tmp_path, budget=1000,
        proxy_sizes=[("old", 600, 1, "/m/old.mp4"), ("pinned", 600, 0, "/m/open.mp4"),
                     ("new", 600, 9, "/m/new.mp4")],
        pinned=["/m/open.mp4"],
    )
    manager.enforce()                               # 1800 → doit passer sous 1000
    assert proxies.evicted == ["proxy-old.mp4"] or proxies.evicted[0] == "proxy-old.mp4"
    assert "proxy-pinned.mp4" not in proxies.evicted    # le proxy du projet ouvert est épinglé


def test_changing_the_budget_applies_immediately(tmp_path):
    manager, previews, _prox = _manager(tmp_path, budget=10 ** 9, previews_sizes=[100] * 4)
    assert manager.set_max_bytes(150) >= 250
    assert previews.stats()["bytes"] <= 150 or previews.stats()["entries"] == 1


def test_purge_by_layer_and_everything(tmp_path):
    manager, previews, proxies = _manager(tmp_path, budget=10 ** 9, previews_sizes=[100] * 2,
                                          proxy_sizes=[("a", 500, 1, "/m/a.mp4")])
    manager.memory.put("k", 1, size_bytes=10)
    assert manager.purge("preview") == 200 and previews.stats()["entries"] == 0
    assert proxies.files
    assert manager.purge("proxy") == 500 and not proxies.files
    assert manager.purge("memory") >= 0 and manager.memory.get("k") is None
    with pytest.raises(ValueError):
        manager.purge("n'importe quoi")
    assert manager.purge() == 0                      # déjà vide : rien à libérer, pas d'erreur


def test_purge_project_only_removes_what_belongs_to_the_project(tmp_path):
    manager, previews, proxies = _manager(tmp_path, budget=10 ** 9)
    for n in range(3):
        _store(previews, n, clip="mine", tmp_path=tmp_path)
    _store(previews, 50, clip="theirs", tmp_path=tmp_path)
    media = tmp_path / "m.mp4"
    media.write_bytes(b"x")
    project = Project(
        name="p",
        media_assets=[MediaAsset(id="a", path=str(media), name="a", duration=5, width=1, height=1,
                                 fps=25.0, media_type="video")],
        tracks=[Track(id="V1", name="V1", type="video", clips=[
            Clip(id=f"mine{n}", asset_id="a", track_id="V1", timeline_start=n * 2.0,
                 source_in=0, source_out=2.0) for n in range(3)])],
    )
    from core.cache_keys import thumbnail_key

    manager.memory.put(thumbnail_key(str(media), 1.0, 160), b"t", size_bytes=1)
    manager.memory.put("thumb:/autre/projet:x", b"t", size_bytes=1)
    manager.purge_project(project)
    assert previews.stats()["entries"] == 1                       # « theirs » survit
    assert proxies.deleted == [str(media)]
    assert manager.memory.get(thumbnail_key(str(media), 1.0, 160)) is None
    assert manager.memory.get("thumb:/autre/projet:x") is not None


def test_the_manager_works_with_missing_layers(tmp_path):
    manager = CacheManager(max_bytes=10)
    assert manager.usage() == [] and manager.enforce() == 0 and manager.purge() == 0
    assert manager.disk_bytes() == 0
    _ = Path
