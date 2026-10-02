"""Une panne locale ne doit jamais bloquer l'application, un worker ou un job.

Chaque test reprend un cas constaté pendant l'audit : fichier de cache abîmé qui empêchait le
démarrage, tâche qui lève et tue le worker, analyse de tracking qui reste « en cours » pour toujours,
rendu d'aperçu qui survit à l'annulation.
"""

from __future__ import annotations

import json
import sys
import threading

import pytest
from test_segment_fingerprint import _project

from core.hardware_cache import CapabilityService
from core.hardware_encoding import SCHEMA_VERSION, HardwareCapabilities
from core.preview_cache import DiskPreviewCache
from core.prefetch import PRIORITY_CURRENT
from core.preview_engine import PreviewEngine, _run_cancellable
from core.preview_segments import build_segment_job
from core.task_queue import TaskQueue
from core.tracking_engine import TrackingCache, TrackingJob, TrackingRequest


# --- Caches abîmés : jamais indispensables ------------------------------------------------------------------------

UNREADABLE_CAPABILITIES = [
    {"schema": SCHEMA_VERSION, "encoders": 5},
    {"schema": SCHEMA_VERSION, "software": 5},
    {"schema": SCHEMA_VERSION, "hwaccels": 7},
    {"schema": SCHEMA_VERSION, "scanned_at": "hier"},
]
# Éléments invalides à l'intérieur de listes : écartés un à un, sans plantage.
TOLERATED_CAPABILITIES = [
    {"schema": SCHEMA_VERSION, "encoders": [5, "x", None]},
    {"schema": SCHEMA_VERSION, "decoders": "x"},
    {"schema": SCHEMA_VERSION, "decoders": [1, 2]},
    {"schema": SCHEMA_VERSION, "validated": object},
]


@pytest.mark.parametrize("payload", UNREADABLE_CAPABILITIES, ids=lambda p: json.dumps(p)[:60])
def test_a_malformed_capability_cache_reads_as_no_cache(payload):
    """Avant : ``{"schema": 2, "encoders": 5}`` levait TypeError et la fenêtre ne démarrait plus."""
    assert HardwareCapabilities.from_dict(payload) is None


@pytest.mark.parametrize("payload", TOLERATED_CAPABILITIES, ids=lambda p: json.dumps(p, default=str)[:60])
def test_invalid_items_inside_a_capability_cache_never_raise(payload):
    result = HardwareCapabilities.from_dict(payload)
    assert result is None or isinstance(result, HardwareCapabilities)


@pytest.mark.parametrize("payload", [None, [], "texte", 5, {"schema": SCHEMA_VERSION, "encoders": 5}])
def test_the_capability_service_survives_any_cache_file(tmp_path, payload):
    path = tmp_path / "caps.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    service = CapabilityService(command_provider=lambda: None, cache_path=path, environment={})
    assert service.cached() is None


@pytest.mark.parametrize("content", ["null", "[]", '{"data": 5}', '{"data": {}, "stop_index": "abc"}', "{pas du json"])
def test_a_malformed_tracking_cache_entry_is_recomputed(tmp_path, content):
    (tmp_path / "t-k.json").write_text(content, encoding="utf-8")
    assert TrackingCache(directory=tmp_path).load("k", 25.0) is None


def test_an_unusable_tracking_cache_directory_does_not_raise(tmp_path):
    blocker = tmp_path / "pas-un-dossier"
    blocker.write_text("fichier", encoding="utf-8")        # un fichier à la place du dossier
    cache = TrackingCache(directory=blocker)
    assert cache.load("k", 25.0) is None
    from core.tracking_engine import StopReason, TrackerOutcome

    cache.store("k", TrackerOutcome("t", samples={}, reason=StopReason.RANGE_END), 25.0)   # sans exception


# --- Workers et jobs qui ne restent jamais bloqués ----------------------------------------------------------------


def test_a_task_that_raises_does_not_stop_the_queue():
    """Avant : l'exception remontait de ``pump`` et tuait le thread worker (plus aucune tâche exécutée)."""
    queue = TaskQueue()
    ran = []

    def boom(_token):
        raise RuntimeError("tâche en échec")

    queue.submit("a", boom)
    queue.submit("b", lambda _token: ran.append("b"))
    assert queue.pump(2) == 2
    assert ran == ["b"]


def test_a_tracking_job_that_raises_ends_as_failed_instead_of_running_forever(monkeypatch):
    request = TrackingRequest(clip_id="c", media_path="/nonexistent.mp4", media_size=(64, 64), rate=25.0,
                              trackers=(), start_index=0, end_index=10)

    def explode(*_args, **_kwargs):
        raise OSError("dossier de cache illisible")

    monkeypatch.setattr("core.tracking_engine.run_tracking", explode)
    job = TrackingJob(request)
    job.run()
    assert job.wait(1.0), "le job doit signaler sa fin"
    snapshot = job.snapshot()
    assert snapshot.state == "failed" and "dossier de cache illisible" in snapshot.result.message


# --- Annulation des rendus d'aperçu -------------------------------------------------------------------------------


def test_cancel_all_stops_a_preview_render_that_is_already_running(tmp_path):
    """Avant : seuls les rendus en file étaient annulés ; FFmpeg survivait à la fermeture (jusqu'à 120 s)."""
    started = threading.Event()
    outcome: dict[str, int] = {}

    def render(job, token):
        started.set()
        code, _stderr = _run_cancellable([sys.executable, "-c", "import time; time.sleep(60)"], token, timeout=120)
        outcome["code"] = code
        return None

    engine = PreviewEngine(task_queue=TaskQueue(), cache=DiskPreviewCache(directory=tmp_path / "cache"),
                           render_fn=render)
    engine.request(build_segment_job(_project(tmp_path), 0, quality="standard"), PRIORITY_CURRENT)
    worker = threading.Thread(target=lambda: engine.pump(1), daemon=True)
    worker.start()
    assert started.wait(15), "le rendu n'a pas démarré"
    engine.cancel_all()
    worker.join(timeout=20)
    assert not worker.is_alive(), "le processus enfant devait être tué par l'annulation"
    assert outcome["code"] != 0
