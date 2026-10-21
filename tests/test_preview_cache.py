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


# ---------------------------------------------------------------------------
# Robustesse du planificateur (bugs corriges)
# ---------------------------------------------------------------------------


def _engine(tmp_path, name, **kwargs):
    """Engine isole : cache temporaire + rendu factice qui compte ses appels."""
    from core.preview_cache import DiskPreviewCache
    from core.preview_engine import PreviewEngine
    from core.task_queue import TaskQueue

    calls = []
    produced = {}

    def fake_render(job, token):
        calls.append(job.key.clip_id)
        target = tmp_path / ("%s-%s.mp4" % (name, job.key.clip_id))
        target.write_bytes(b"segment")
        produced[job.key.clip_id] = str(target)
        return str(target)

    engine = PreviewEngine(
        task_queue=TaskQueue(),
        cache=DiskPreviewCache(directory=tmp_path / name, ttl_seconds=0),
        render_fn=fake_render,
        **kwargs,
    )
    return engine, calls, produced


def _job_for(plan, clip="clip-a", params="p1"):
    from core.preview_engine import PreviewJob

    return PreviewJob(key=_key(clip=clip, params=params), plan=plan)


def test_pump_never_drops_jobs_over_concurrency(tmp_path):
    """La borne de concurrence met en attente, elle ne perd aucun segment."""
    engine, calls, _produced = _engine(tmp_path, "cap", max_concurrent=1)
    _project, plan = _make_plan()
    for index in range(3):
        job = _job_for(plan, clip="cap-%d" % index, params="p%d" % index)
        assert engine.request(job)["status"] == "pending"
    assert engine.state().pending == 3
    assert engine.pump(4) == 1
    assert engine.state().pending == 2
    assert engine.pump(4) == 1
    assert engine.pump(4) == 1
    assert engine.state().pending == 0
    assert len(calls) == 3


def test_duplicate_request_does_not_restart_render(tmp_path):
    """Une demande identique ne relance pas le rendu deja planifie."""
    engine, calls, _produced = _engine(tmp_path, "dup")
    _project, plan = _make_plan()
    job = _job_for(plan, clip="dup")
    assert engine.request(job)["status"] == "pending"
    assert engine.request(job)["status"] == "pending"
    assert engine.state().pending == 1
    assert engine.pump(4) == 1
    assert calls == ["dup"]
    assert engine.request(job)["status"] == "cached"


def test_invalidate_clip_also_covers_unattributed_segments(tmp_path):
    """Les segments « hors clip » (cle timeline) dependent de tout clip."""
    from core.preview_engine import PreviewJob

    engine, calls, _produced = _engine(tmp_path, "gap")
    _project, plan = _make_plan()
    key_gap = _key(clip="timeline", params="pg")
    key_other = _key(clip="clip-b", params="pb")
    engine.request(PreviewJob(key=key_gap, plan=plan))
    engine.request(PreviewJob(key=key_other, plan=plan))
    assert engine.state().pending == 2
    assert engine.invalidate_clip("clip-a") == 0
    assert engine.state().pending == 1  # clip-b reste en file
    assert engine.pump(4) == 1
    assert calls == ["clip-b"]
    assert engine.cache.lookup(key_gap) is None


def test_invalidate_clip_keeps_other_clips_queued(tmp_path):
    """L'invalidation est chirurgicale : les autres clips restent en file."""
    from core.preview_engine import PreviewJob

    engine, calls, _produced = _engine(tmp_path, "scope")
    _project, plan = _make_plan()
    key_a = _key(clip="clip-a", params="pa")
    key_b = _key(clip="clip-b", params="pb")
    engine.request(PreviewJob(key=key_a, plan=plan))
    engine.request(PreviewJob(key=key_b, plan=plan))
    assert engine.invalidate_clip("clip-a") == 0
    assert engine.state().pending == 1
    assert engine.pump(4) == 1
    assert calls == ["clip-b"]
    assert engine.cache.lookup(key_b) is not None
    assert engine.cache.lookup(key_a) is None




def test_identical_request_during_render_does_not_cancel_it(tmp_path):
    """Un tick d'interface ne doit jamais annuler le rendu en vol."""
    from core.preview_cache import DiskPreviewCache
    from core.preview_engine import PreviewEngine, PreviewJob
    from core.task_queue import TaskQueue

    cache = DiskPreviewCache(directory=tmp_path / "tick", ttl_seconds=0)
    holder = {}

    def fake_render(job, token):
        # La tete de lecture redemande exactement la meme plage.
        again = holder["engine"].request(holder["job"])
        assert again["status"] == "pending"
        target = tmp_path / "tick.mp4"
        target.write_bytes(b"segment")
        return str(target)

    engine = PreviewEngine(
        task_queue=TaskQueue(), cache=cache, render_fn=fake_render
    )
    _project, plan = _make_plan()
    key = _key(clip="clip-t", params="pt")
    job = PreviewJob(key=key, plan=plan)
    holder["engine"] = engine
    holder["job"] = job
    engine.request(job)
    engine.pump(4)
    # Le rendu en vol n'a pas ete supersede : il a bien abouti.
    assert cache.lookup(key) is not None


def test_invalidated_inflight_render_does_not_write(tmp_path):
    """Un rendu invalide en plein vol ne peut pas ressusciter le cache."""
    from core.preview_cache import DiskPreviewCache
    from core.preview_engine import PreviewEngine
    from core.task_queue import TaskQueue

    cache = DiskPreviewCache(directory=tmp_path / "flight", ttl_seconds=0)
    holder = {}

    def fake_render(job, token):
        # Le clip est modifie pendant que son segment se rend.
        holder["engine"].invalidate_clip("clip-z")
        target = tmp_path / "inflight.mp4"
        target.write_bytes(b"late")
        return str(target)

    engine = PreviewEngine(
        task_queue=TaskQueue(), cache=cache, render_fn=fake_render
    )
    holder["engine"] = engine
    _project, plan = _make_plan()
    key = _key(clip="clip-z", params="pz")
    engine.request(_job_for(plan, clip="clip-z", params="pz"))
    assert engine.pump(4) == 1
    assert cache.lookup(key) is None
    assert cache.stats()["entries"] == 0


def test_cancelled_inflight_render_does_not_write(tmp_path):
    """``cancel_all`` perime aussi les rendus deja en execution."""
    from core.preview_cache import DiskPreviewCache
    from core.preview_engine import PreviewEngine
    from core.task_queue import TaskQueue

    cache = DiskPreviewCache(directory=tmp_path / "cancel", ttl_seconds=0)
    holder = {}

    def fake_render(job, token):
        holder["engine"].cancel_all()
        target = tmp_path / "cancelled.mp4"
        target.write_bytes(b"late")
        return str(target)

    engine = PreviewEngine(
        task_queue=TaskQueue(), cache=cache, render_fn=fake_render
    )
    holder["engine"] = engine
    _project, plan = _make_plan()
    key = _key(clip="clip-c", params="pc")
    engine.request(_job_for(plan, clip="clip-c", params="pc"))
    assert engine.pump(4) == 1
    assert cache.lookup(key) is None
    assert cache.stats()["entries"] == 0


def test_state_cached_segments_follow_disk(tmp_path):
    """Le compteur publie reflete le cache, il ne croit pas indefiniment."""
    engine, _calls, _produced = _engine(tmp_path, "count")
    _project, plan = _make_plan()
    assert engine.state().cached_segments == 0
    for index in range(2):
        engine.request(_job_for(plan, clip="cnt-%d" % index, params="p%d" % index))
    engine.pump(4)
    engine.pump(4)
    assert engine.state().cached_segments == 2
    assert engine.invalidate_clip("cnt-0") == 1
    assert engine.state().cached_segments == 1


def test_engine_never_deletes_foreign_output(tmp_path):
    """Seuls les temporaires crees par le moteur sont supprimables."""
    from pathlib import Path

    engine, _calls, produced = _engine(tmp_path, "own")
    _project, plan = _make_plan()
    engine.request(_job_for(plan, clip="own-1"))
    engine.pump(4)
    foreign = produced["own-1"]
    assert engine._discard_output(foreign) is False
    assert Path(foreign).exists()

    own = tmp_path / "own-temp.mp4"
    own.write_bytes(b"temp")
    engine._temp_outputs.add(str(own))
    assert engine._discard_output(str(own)) is True
    assert not own.exists()


def test_fallback_source_prefers_topmost_layer(tmp_path):
    """Le repli montre la couche du dessus, pas celle du dessous."""
    from dataclasses import replace

    from core.preview_cache import DiskPreviewCache
    from core.preview_engine import PreviewEngine

    engine = PreviewEngine(cache=DiskPreviewCache(directory=tmp_path / "fb"))
    _project, plan = _make_plan()
    bottom = replace(
        plan.video_layers[0], clip_id="bottom", source_path="/media/bottom.mp4"
    )
    top = replace(plan.video_layers[0], clip_id="top", source_path="/media/top.mp4")
    stacked = replace(plan, video_layers=(bottom, top))
    assert engine.fallback_source(_job_for(stacked, clip="top")) == "/media/top.mp4"

    # Sans chemin de media, on retombe sur un identifiant stable.
    anonymous = replace(top, source_path="")
    nested = replace(stacked, video_layers=(bottom, anonymous))
    assert engine.fallback_source(_job_for(nested, clip="top")) == "source://top"
    empty = replace(stacked, video_layers=())
    assert engine.fallback_source(_job_for(empty, clip="x")) == ""



def test_last_error_clears_after_successful_render(tmp_path):
    """Une erreur transitoire ne reste pas collee a l'etat publie."""
    from core.preview_cache import DiskPreviewCache
    from core.preview_engine import PreviewEngine
    from core.task_queue import TaskQueue

    cache = DiskPreviewCache(directory=tmp_path / "err", ttl_seconds=0)
    flags = {"fail": True}

    def fake_render(job, token):
        if flags["fail"]:
            raise RuntimeError("FFmpeg apercu a echoue.")
        target = tmp_path / "ok.mp4"
        target.write_bytes(b"ok")
        return str(target)

    engine = PreviewEngine(
        task_queue=TaskQueue(), cache=cache, render_fn=fake_render
    )
    _project, plan = _make_plan()
    engine.request(_job_for(plan, clip="ko", params="p1"))
    engine.pump(4)
    assert "FFmpeg" in engine.state().last_error

    flags["fail"] = False
    engine.request(_job_for(plan, clip="ok", params="p2"))
    engine.pump(4)
    assert engine.state().last_error == ""
    assert engine.state().cached_segments == 1


def test_finished_key_can_be_requested_again(tmp_path):
    """Une cle oubliee apres rendu reste re-rendable (pas de faux perime)."""
    engine, calls, _produced = _engine(tmp_path, "again")
    _project, plan = _make_plan()
    job = _job_for(plan, clip="again-1")
    engine.request(job)
    engine.pump(4)
    # Le fichier est efface puis redemande : le moteur doit relancer.
    for path in engine.cache.directory.glob("*.mp4"):
        path.unlink()
    assert engine.request(job)["status"] == "pending"
    assert engine.pump(4) == 1
    assert calls == ["again-1", "again-1"]




def test_request_stops_when_subtitles_unsupported(tmp_path, monkeypatch):
    """Sans libass, l'apercu ne relance pas un rendu voue a l'echec."""
    from dataclasses import replace

    from core.preview_cache import DiskPreviewCache
    from core.preview_engine import PreviewEngine
    from core.subtitle_io import SubtitleCue
    from core.task_queue import TaskQueue

    monkeypatch.setattr(
        "core.filter_graph.ffmpeg_supports_subtitles", lambda: False
    )
    _project, base_plan = _make_plan()
    plan = replace(
        base_plan,
        subtitle_cues=(SubtitleCue(start=0.0, end=1.0, text="Hi"),),
        subtitle_styles=(),
    )
    job = _job_for(plan, clip="no-libass")
    engine = PreviewEngine(
        task_queue=TaskQueue(),
        cache=DiskPreviewCache(directory=tmp_path / "nope", ttl_seconds=0),
    )
    assert engine.request(job)["status"] == "unavailable"
    assert engine.state().pending == 0
    assert "libass" in engine.state().last_error

    # Un rendu injecte par l'appelant n'est pas concerne par ce diagnostic.
    injected = PreviewEngine(
        task_queue=TaskQueue(),
        cache=DiskPreviewCache(directory=tmp_path / "injected", ttl_seconds=0),
        render_fn=lambda _job, _token: None,
    )
    assert injected.request(job)["status"] == "pending"
