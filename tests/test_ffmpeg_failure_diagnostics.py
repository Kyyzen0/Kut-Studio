"""Les échecs FFmpeg (export, file de rendu, aperçu fidèle, proxies) arrivent dans le fichier de journal.

Complète ``test_diagnostics_coverage.py`` : même méthode (la panne est provoquée par l'API publique, puis on relit le
**fichier** de journal), pour les quatre endroits où l'application lance FFmpeg pour l'utilisateur. Sans cela, un
export qui échoue laissait un message à l'écran mais aucune trace pour un rapport de bogue.
"""

from __future__ import annotations

import logging
import sys
import tempfile
import threading
from pathlib import Path

import pytest
from test_render_queue import _enqueue, _wait_idle, fake_ffmpeg, make_queue, queue  # noqa: F401 - fixtures partagées

from core.diagnostics_log import install_diagnostics, uninstall_diagnostics


@pytest.fixture
def journal(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "excepthook", lambda *_a: None)
    monkeypatch.setattr(threading, "excepthook", lambda _a: None)
    monkeypatch.setattr(sys, "unraisablehook", lambda _a: None)
    path = install_diagnostics(tmp_path / "logs")
    assert path is not None

    def text() -> str:
        for handler in logging.getLogger("kut_studio").handlers:
            handler.flush()
        return Path(path).read_text(encoding="utf-8") if Path(path).exists() else ""

    yield text
    uninstall_diagnostics()


def test_a_failed_export_and_its_queue_job_reach_the_log_file(qtbot, queue, tmp_path, monkeypatch, journal):  # noqa: F811
    monkeypatch.setenv("FAKE_FFMPEG_FAIL", "Invalid data found when processing input")
    job = _enqueue(queue, tmp_path)
    queue.start_all()
    _wait_idle(qtbot, queue)

    assert job.status.value == "failed"
    text = journal()
    assert "Export échoué (code 1" in text and "Invalid data found when processing input" in text
    assert f"File de rendu : tâche {job.id} en échec (ffmpeg)" in text


def test_ffmpeg_that_cannot_start_is_logged(qtbot, queue, tmp_path, monkeypatch, journal):  # noqa: F811
    monkeypatch.setattr("core.export_engine._ffmpeg_path", (str(tmp_path / "ffmpeg-inexistant"),))
    _enqueue(queue, tmp_path)
    queue.start_all()
    _wait_idle(qtbot, queue)

    assert "FFmpeg n'a pas pu démarrer" in journal()


def test_a_preview_segment_that_ffmpeg_cannot_render_is_logged(tmp_path, monkeypatch, journal):
    from core.preview_cache import PreviewSegmentKey
    from core.preview_engine import PreviewEngine, PreviewJob
    from core.project_factory import create_default_project
    from core.render_plan import build_render_plan

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(
        "core.preview_engine._run_cancellable", lambda command, token, *, timeout: (1, "Conversion failed!")
    )
    key = PreviewSegmentKey(clip_id="clip-a", start=0.0, end=2.0, quality="standard", params_hash="p")
    job = PreviewJob(key=key, plan=build_render_plan(create_default_project()), start=0.0, duration=2.0)

    with pytest.raises(RuntimeError, match="a echoue"):
        PreviewEngine(cache=None)._default_render(job, None)

    assert "Aperçu fidèle : FFmpeg a échoué (code 1) : Conversion failed!" in journal()


def test_a_failed_proxy_generation_is_logged(tmp_path, journal):
    from core.cache_keys import SignatureMemo
    from core.proxy_manager import ProxyManager, ProxyState, RunResult

    source = tmp_path / "clip.mp4"
    source.write_bytes(b"S" * 1000)

    def failing_runner(command, cancel, on_progress, on_start):
        on_start(4242)
        return RunResult(1, "Error while decoding stream #0:0: Invalid data")

    manager = ProxyManager(
        tmp_path / "proxies", ffmpeg_command=lambda: ["ffmpeg-fake"], runner=failing_runner,
        memo=SignatureMemo(ttl=0.0), disk_ttl=0.0,
    )
    try:
        manager.request(str(source), duration=2.0)
        deadline = threading.Event()
        for _ in range(800):
            if manager.info(str(source), None).state is ProxyState.ERROR:
                break
            deadline.wait(0.01)
        assert manager.info(str(source), None).state is ProxyState.ERROR
    finally:
        manager.shutdown()

    text = journal()
    assert "Proxy : génération échouée" in text and "clip.mp4" in text
