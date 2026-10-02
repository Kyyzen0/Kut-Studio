"""Un segment d'aperçu vide n'entre jamais dans le cache.

FFmpeg pouvait sortir avec le code 0 sans rien écrire (le fichier temporaire vient de ``mkstemp``) ; le segment
vide était copié dans le cache, servi tel quel pendant sept jours (la durée de vie du cache) et jamais refait :
le moniteur n'affichait rien à cet endroit tant que le cache n'était pas purgé à la main.
"""

from __future__ import annotations

import tempfile

import pytest

from core.preview_cache import DiskPreviewCache, PreviewSegmentKey
from core.preview_engine import PreviewEngine, PreviewJob
from core.project_factory import create_default_project
from core.render_plan import build_render_plan
from core.task_queue import TaskQueue


def _key() -> PreviewSegmentKey:
    return PreviewSegmentKey(clip_id="clip-a", start=0.0, end=2.0, quality="standard", params_hash="p")


def test_the_cache_refuses_an_empty_segment(tmp_path):
    cache = DiskPreviewCache(directory=tmp_path / "cache", ttl_seconds=0)
    empty = tmp_path / "empty.mp4"
    empty.write_bytes(b"")
    with pytest.raises(ValueError, match="vide"):
        cache.store(_key(), str(empty))
    assert cache.lookup(_key()) is None and cache.stats()["entries"] == 0


def test_an_engine_whose_render_wrote_nothing_caches_nothing_and_says_why(tmp_path):
    cache = DiskPreviewCache(directory=tmp_path / "cache", ttl_seconds=0)

    def render(job, token):
        target = tmp_path / "out.mp4"
        target.write_bytes(b"")                         # FFmpeg « a réussi » sans rien écrire
        return str(target)

    engine = PreviewEngine(task_queue=TaskQueue(), cache=cache, render_fn=render)
    job = PreviewJob(key=_key(), plan=build_render_plan(create_default_project()), start=0.0, duration=2.0)
    assert engine.request(job)["status"] == "pending"
    engine.pump(4)

    assert cache.lookup(_key()) is None
    assert engine.state().cached_segments == 0
    assert "vide" in engine.state().last_error
    assert engine.request(job)["status"] == "pending"   # pas servi comme « en cache » : il sera refait


def test_the_default_render_rejects_a_zero_exit_code_without_output(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr("core.preview_engine._run_cancellable", lambda command, token, *, timeout: (0, ""))
    engine = PreviewEngine(cache=None)
    job = PreviewJob(key=_key(), plan=build_render_plan(create_default_project()), start=0.0, duration=2.0)

    with pytest.raises(RuntimeError, match="aucun fichier"):
        engine._default_render(job, None)

    assert not list(tmp_path.glob("kut-preview-*"))      # aucun temporaire ne reste
