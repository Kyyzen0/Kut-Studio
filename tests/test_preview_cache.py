"""Cache disque, invalidation, concurrence, annulation (tache 30)."""

from __future__ import annotations


def _key(clip="clip-a", start=0.0, end=2.0, quality="standard", params="p1"):
    from core.preview_cache import PreviewSegmentKey

    return PreviewSegmentKey(
        clip_id=clip, start=start, end=end, quality=quality, params_hash=params
    )


def test_cache_paths_deterministic(tmp_path):
    from core.preview_cache import DiskPreviewCache

    cache = DiskPreviewCache(directory=tmp_path / "c1", ttl_seconds=0)
    key = _key()
    assert cache.path_for(key) == cache.path_for(key)
    assert cache.lookup(key) is None


def test_cache_store_lookup_and_invalidate_clip(tmp_path):
    from core.preview_cache import DiskPreviewCache

    cache = DiskPreviewCache(directory=tmp_path / "c2", ttl_seconds=0)
    src = tmp_path / "seg.mp4"
    src.write_bytes(b"fake-mp4-bytes")
    key_a = _key(clip="clip-a")
    key_b = _key(clip="clip-b")
    cache.store(key_a, str(src))
    cache.store(key_b, str(src))
    assert cache.lookup(key_a) is not None
    cache.invalidate_clip("clip-a")
    assert cache.lookup(key_a) is None
    assert cache.lookup(key_b) is not None


def test_cache_evicts_oldest_over_budget(tmp_path):
    from core.preview_cache import DiskPreviewCache

    cache = DiskPreviewCache(
        directory=tmp_path / "c3", budget_bytes=40, ttl_seconds=0
    )
    src = tmp_path / "seg.mp4"
    src.write_bytes(b"0123456789ABCDEF")
    import time

    for index in range(4):
        cache.store(_key(clip="c%d" % index, params="p%d" % index), str(src))
        time.sleep(0.01)
    assert cache.stats()["bytes"] <= 40


def test_render_qualities():
    from core.preview_render import (
        coerce_render_quality,
        preview_scale_factor,
        render_quality_label,
    )

    assert coerce_render_quality("nope") == "standard"
    assert preview_scale_factor("draft") == 0.25
    assert preview_scale_factor("high") == 1.0
    assert render_quality_label("draft") == "Brouillon"
    assert render_quality_label("high") == "Haute"


def test_engine_request_pump_and_cancel(tmp_path):
    from core.preview_cache import DiskPreviewCache, PreviewSegmentKey
    from core.preview_engine import PreviewEngine, PreviewJob
    from core.task_queue import TaskQueue

    cache = DiskPreviewCache(directory=tmp_path / "eng", ttl_seconds=0)
    produced = {}

    def fake_render(job, token):
        target = tmp_path / ("out-%s.mp4" % job.key.clip_id)
        target.write_bytes(b"segment")
        produced[job.key.clip_id] = str(target)
        return str(target)

    engine = PreviewEngine(
        task_queue=TaskQueue(), cache=cache, render_fn=fake_render
    )
    _project, plan = _make_plan()
    key = PreviewSegmentKey(
        clip_id="clip-x", start=0.0, end=2.0,
        quality="standard", params_hash="p",
    )
    job = PreviewJob(key=key, plan=plan, start=0.0, duration=2.0)
    assert engine.request(job)["status"] == "pending"
    assert engine.pump(4) == 1
    assert cache.lookup(key) is not None
    # Second appel : servi depuis le cache, sans nouveau rendu.
    assert engine.request(job)["status"] == "cached"
    # Source de repli = media source pendant la generation.
    assert engine.fallback_source(job) != ""


def _make_plan():
    from core.project_factory import create_default_project
    from core.render_plan import build_render_plan

    project = create_default_project()
    return project, build_render_plan(project)


def test_engine_obsolete_task_does_not_store(tmp_path):
    from core.preview_cache import DiskPreviewCache, PreviewSegmentKey
    from core.preview_engine import PreviewEngine, PreviewJob
    from core.task_queue import TaskQueue

    cache = DiskPreviewCache(directory=tmp_path / "obs", ttl_seconds=0)

    def fake_render(job, token):
        target = tmp_path / "late.mp4"
        target.write_bytes(b"late")
        return str(target)

    engine = PreviewEngine(
        task_queue=TaskQueue(), cache=cache, render_fn=fake_render
    )
    _project, plan = _make_plan()
    key = PreviewSegmentKey(
        clip_id="clip-o", start=0.0, end=2.0,
        quality="standard", params_hash="p",
    )
    job = PreviewJob(key=key, plan=plan)
    engine.request(job)
    engine.request(job)  # seconde generation : la premiere devient obsolete
    engine.pump(4)
    # Une seule ecriture, pas de doublon concurrent inutile.
    assert cache.stats()["entries"] == 1


def test_engine_paused_during_playback(tmp_path):
    from core.preview_cache import DiskPreviewCache, PreviewSegmentKey
    from core.preview_engine import PreviewEngine, PreviewJob
    from core.task_queue import TaskQueue

    cache = DiskPreviewCache(directory=tmp_path / "pause", ttl_seconds=0)
    calls = []

    def fake_render(job, token):
        calls.append(1)
        target = tmp_path / "s.mp4"
        target.write_bytes(b"s")
        return str(target)

    engine = PreviewEngine(
        task_queue=TaskQueue(), cache=cache, render_fn=fake_render
    )
    _project, plan = _make_plan()
    key = PreviewSegmentKey(
        clip_id="clip-p", start=0.0, end=2.0,
        quality="standard", params_hash="p",
    )
    engine.request(PreviewJob(key=key, plan=plan))
    engine.set_playing(True)
    assert engine.pump(4) == 0
    assert calls == []
    engine.set_playing(False)
    assert engine.pump(4) == 1
