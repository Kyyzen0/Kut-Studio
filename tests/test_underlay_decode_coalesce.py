"""Underlays GPU : les jobs de décodage périmés ne démarrent pas le travail coûteux."""

from __future__ import annotations

import threading
import time

from ui import preview_panel


def test_stale_underlay_jobs_skip_decode_before_work(qtbot, monkeypatch):
    panel = preview_panel.PreviewPanel(
        lambda: None,
        lambda: None,
        lambda _d: None,
        lambda: None,
        lambda: None,
    )
    qtbot.addWidget(panel)

    class _FakeView:
        def forget_source(self, _source_id):
            return None

        def set_video_frame(self, *_args):
            return None

        def set_composite(self, *_args, **_kwargs):
            return None

    panel.gpu_view = _FakeView()
    started = []
    release = threading.Event()

    def _slow_decode(path: str, seconds: float):
        started.append((path, seconds))
        release.wait(timeout=2.0)
        return None

    monkeypatch.setattr(preview_panel, "_decode_underlay_frame", _slow_decode)

    # Première demande : le worker va se bloquer dans decode.
    panel.set_video_underlays(
        (
            (
                "clip:a",
                "/tmp/a.mp4",
                0.1,
                (1, 0, 0, 1, 0, 0),
                (0, 0, 10, 10),
                1.0,
                "normal",
                (),
            ),
        )
    )
    deadline = time.time() + 2.0
    while not started and time.time() < deadline:
        time.sleep(0.01)
    assert started == [("/tmp/a.mp4", 0.1)]

    # Scrub rapide : deux clés plus récentes — le job 0.1 en cours et ceux en file
    # doivent voir la clé périmée avant (ou après) decode.
    panel.set_video_underlays(
        (
            (
                "clip:a",
                "/tmp/a.mp4",
                0.2,
                (1, 0, 0, 1, 0, 0),
                (0, 0, 10, 10),
                1.0,
                "normal",
                (),
            ),
        )
    )
    panel.set_video_underlays(
        (
            (
                "clip:a",
                "/tmp/a.mp4",
                0.3,
                (1, 0, 0, 1, 0, 0),
                (0, 0, 10, 10),
                1.0,
                "normal",
                (),
            ),
        )
    )
    release.set()
    deadline = time.time() + 2.0
    while time.time() < deadline and len(started) < 2:
        time.sleep(0.01)
        qtbot.wait(10)

    # Seule la dernière demande (0.3) doit encore décoder après la première ;
    # 0.2 a été coalescé (périmé avant decode).
    assert ("/tmp/a.mp4", 0.3) in started
    assert ("/tmp/a.mp4", 0.2) not in started
