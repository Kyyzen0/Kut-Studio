"""Cache des vecteurs de mouvement : clé exacte, cycle complet, entrées corrompues, budget, et jamais de rendu différent."""

from __future__ import annotations

import os

import numpy as np
import pytest
from flow_scenes import Body, Scene, linear, rgb

from core.cache_manager import KIND_FLOW, KINDS, CacheManager
from core.flow_cache import FlowCache, cache_key
from core.optical_flow import Fallback, OpticalFlowEngine, PairAnalysis
from core.time_remapping import FlowQuality

W, H = 160, 90


@pytest.fixture
def media(tmp_path):
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"0" * 1000)
    return str(path)


def key(media, frame=3, **overrides):
    engine = OpticalFlowEngine(FlowQuality.BALANCED)
    values = {"width": W, "height": H, "conformation": "scale=160:90", "identity": engine.identity}
    values.update(overrides)
    return cache_key(media, frame, **values)


def real_pair() -> tuple[OpticalFlowEngine, np.ndarray, np.ndarray, PairAnalysis]:
    scene = Scene(W, H, [Body(30, linear(50, 45, 4, 2))])
    engine = OpticalFlowEngine(FlowQuality.BALANCED)
    frame_a, frame_b = rgb(scene.render(2.0)), rgb(scene.render(3.0))
    return engine, frame_a, frame_b, engine.estimator.analyze(frame_a, frame_b)


# ---------------------------------------------------------------------------
# Clé
# ---------------------------------------------------------------------------


def test_the_key_is_stable_and_depends_on_everything_that_changes_the_result(media, tmp_path):
    base = key(media)
    assert key(media) == base
    assert key(media, 4) != base                                                    # une autre paire
    assert key(media, width=320, height=180) != base                                # une autre grille de travail
    assert key(media, conformation="scale=160:90,pad=160:90:0:0") != base           # un autre cadrage
    assert key(media, identity=OpticalFlowEngine(FlowQuality.BEST).identity) != base  # un autre moteur ou réglage
    other = tmp_path / "other.mp4"
    other.write_bytes(b"0" * 1000)
    assert key(str(other)) != base                                                  # un autre média (un proxy, par exemple)


def test_a_modified_media_file_changes_the_key(media):
    before = key(media)
    with open(media, "ab") as handle:
        handle.write(b"x")
    assert key(media) != before


def test_the_key_never_depends_on_the_speed_or_the_clip(media):
    """Le mouvement est celui du média : la clé n'a aucun paramètre de vitesse, de courbe, de clip ni de position."""
    import inspect

    assert set(inspect.signature(cache_key).parameters) == {
        "media_path", "first_frame", "width", "height", "conformation", "identity"
    }


# ---------------------------------------------------------------------------
# Cycle complet
# ---------------------------------------------------------------------------


def test_a_pair_comes_back_identical_and_makes_the_same_image(tmp_path, media):
    cache = FlowCache(tmp_path / "flow")
    engine, frame_a, frame_b, pair = real_pair()
    assert cache.load(key(media)) is None
    cache.store(key(media), pair)
    restored = cache.load(key(media))
    assert restored is not None and restored.status is Fallback.NONE
    cold = engine.interpolator.interpolate(frame_a, frame_b, pair, 0.5)
    warm = engine.interpolator.interpolate(frame_a, frame_b, restored, 0.5)
    assert np.array_equal(cold.pixels, warm.pixels)                                 # le cache ne change jamais le rendu


@pytest.mark.parametrize("status", [Fallback.SCENE_CUT, Fallback.IDENTICAL])
def test_a_cut_or_identical_pair_is_cached_without_any_field(tmp_path, media, status):
    cache = FlowCache(tmp_path / "flow")
    cache.store(key(media), PairAnalysis(status))
    restored = cache.load(key(media))
    assert restored is not None and restored.status is status and restored.forward is None
    assert cache.stats()["bytes"] < 2000


def test_a_corrupted_entry_is_discarded_and_counts_as_absent(tmp_path, media):
    cache = FlowCache(tmp_path / "flow")
    cache.store(key(media), real_pair()[3])
    path = next((tmp_path / "flow").glob("pair-*.npz"))
    path.write_bytes(b"not an archive")
    assert cache.load(key(media)) is None and not path.exists()


def test_an_entry_with_an_inconsistent_shape_is_discarded(tmp_path, media):
    cache = FlowCache(tmp_path / "flow")
    (tmp_path / "flow").mkdir()
    path = tmp_path / "flow" / f"pair-{key(media)}.npz"
    with open(path, "wb") as handle:
        np.savez(handle, flow=np.zeros((2, 4, 4), np.float16), status=np.array([0], np.int8))
    assert cache.load(key(media)) is None and not path.exists()


def test_a_write_is_atomic_and_leaves_no_temporary_file(tmp_path, media, monkeypatch):
    cache = FlowCache(tmp_path / "flow")
    pair = real_pair()[3]
    real_replace = os.replace

    def failing(source, target):
        raise OSError("disque plein")

    monkeypatch.setattr(os, "replace", failing)
    cache.store(key(media), pair)                                                   # l'échec d'écriture est ignoré
    monkeypatch.setattr(os, "replace", real_replace)
    assert list((tmp_path / "flow").iterdir()) == []                                # ni entrée partielle ni ``.tmp``
    assert cache.load(key(media)) is None


def test_cleanup_removes_writes_abandoned_by_a_killed_process_but_never_a_live_one(tmp_path):
    cache = FlowCache(tmp_path / "flow")
    (tmp_path / "flow").mkdir()
    abandoned = tmp_path / "flow" / ".pair-abc.123.tmp.npz"
    live = tmp_path / "flow" / ".frames-def.456.tmp.mkv"
    for item in (abandoned, live):
        item.write_bytes(b"partial")
    os.utime(abandoned, (1000, 1000))                                               # intact depuis très longtemps
    assert cache.cleanup_orphans() == 1 and not abandoned.exists() and live.exists()   # l'écriture vivante d'une autre instance reste
    assert cache.purge() >= 0 and list((tmp_path / "flow").iterdir()) == []         # une purge explicite emporte tout


# ---------------------------------------------------------------------------
# Budget (même contrat que les autres couches)
# ---------------------------------------------------------------------------


def test_eviction_removes_the_least_recently_used_entries_first(tmp_path, media):
    cache = FlowCache(tmp_path / "flow")
    pair = real_pair()[3]
    for frame in range(3):
        cache.store(key(media, frame), pair)
        path = next(item for item in (tmp_path / "flow").glob("pair-*.npz") if item.name == f"pair-{key(media, frame)}.npz")
        os.utime(path, (1000 + frame, 1000 + frame))
    size = cache.stats()["bytes"] // 3
    assert cache.stats()["entries"] == 3
    assert cache.load(key(media, 0)) is not None                                    # la plus ancienne vient d'être lue : la plus récente
    freed = cache.evict_bytes(size)
    assert freed >= size and cache.stats()["entries"] == 2
    assert cache.load(key(media, 1)) is None and cache.load(key(media, 0)) is not None


def test_the_cache_manager_counts_budgets_and_purges_the_flow_layer(tmp_path, media):
    cache = FlowCache(tmp_path / "flow")
    for frame in range(4):
        cache.store(key(media, frame), real_pair()[3])
    manager = CacheManager(flow=cache, max_bytes=cache.stats()["bytes"] // 2)
    assert KIND_FLOW in KINDS
    usage = {item.kind: item for item in manager.usage()}
    assert usage[KIND_FLOW].entries == 4
    assert manager.enforce() > 0 and cache.stats()["bytes"] <= manager.max_bytes
    assert manager.purge(KIND_FLOW) >= 0 and cache.stats()["entries"] == 0
    cache.store(key(media, 0), real_pair()[3])
    manager.purge("all")
    assert cache.stats()["entries"] == 0


def test_two_threads_writing_the_same_key_never_share_a_temporary_file(tmp_path, media):
    """Export, aperçu et analyse préparent parfois la même clé en même temps : chaque écriture a son fichier."""
    import threading

    cache = FlowCache(tmp_path / "flow")
    assert cache.stream_temporary("k") != cache.stream_temporary("k")                  # un nom par appel, pas par processus
    pair, errors = real_pair()[3], []

    def write():
        try:
            for _ in range(6):
                cache.store(key(media), pair)
        except BaseException as error:  # noqa: BLE001 - le test dit ce qui s'est passé dans le fil
            errors.append(error)

    threads = [threading.Thread(target=write) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    assert cache.load(key(media)) is not None                                          # l'entrée finale est complète et lisible
    assert [item.name for item in (tmp_path / "flow").iterdir()] == [f"pair-{key(media)}.npz"]    # aucun ``.tmp`` oublié


def test_promoting_the_same_stream_twice_from_two_writers_keeps_both_writers_files_apart(tmp_path):
    cache = FlowCache(tmp_path / "flow")
    first, second = cache.stream_temporary("k"), cache.stream_temporary("k")
    first.write_bytes(b"a")
    second.write_bytes(b"b")
    cache.promote_stream(first, "k", {})
    cache.promote_stream(second, "k", {})                                              # ne lève pas : son fichier n'a pas disparu
    assert cache.stream_path("k").read_bytes() == b"b" and not first.exists() and not second.exists()
