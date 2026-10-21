"""Le moniteur affiche une image dès qu'on le positionne, sans passer par Lecture puis Pause.

Vrai ``QMediaPlayer`` et vrai fichier (FFmpeg) : un lecteur resté à l'arrêt ne livre aucune image au ``QVideoSink``
après ``setSource`` / ``setPosition``. Seul ce qui est observable dans Qt compte ici (le rendu Metal, lui, ne se
vérifie que dans la vraie fenêtre).
"""

from __future__ import annotations

import time

import pytest
from PySide6.QtCore import QUrl
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QApplication
from render_probe import lavfi_video, needs_ffmpeg
from test_performance_ui import _window  # noqa: F401 - helper partagé

from ui.preview_panel import PreviewPanel

pytestmark = needs_ffmpeg
DEADLINE = 15.0  # généreux : runners Windows lents


def _release(panel) -> None:
    """Arrête le lecteur et vide sa source avant la destruction différée (pas de décodage pendant le teardown)."""
    try:
        panel.player.stop()
        panel.player.setSource(QUrl())
    except RuntimeError:  # objet C++ déjà détruit
        return
    for _ in range(5):
        QApplication.processEvents()


@pytest.fixture
def panel(qtbot):
    widget = PreviewPanel(
        lambda: None, lambda: None, lambda _d: None, lambda: None, lambda: None
    )
    qtbot.addWidget(widget)
    yield widget
    _release(widget)


def _counter(panel: PreviewPanel) -> list[int]:
    """Compte les images valides reçues par le puits vidéo du lecteur."""
    sink = panel.player.videoSink()
    if sink is None:
        pytest.skip("QtMultimedia sans puits vidéo : image non observable")
    count = [0]

    def on_frame(frame) -> None:
        if frame.isValid():
            count[0] += 1

    sink.videoFrameChanged.connect(on_frame)
    return count


def _wait(predicate, deadline: float = DEADLINE) -> bool:
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    QApplication.processEvents()
    return bool(predicate())


def _media(tmp_path, name: str):
    return lavfi_video(
        tmp_path / name, "testsrc2=d=2", size=(64, 36), fps=25, seconds=2.0
    )


def test_preview_at_without_play_delivers_a_frame_and_pauses(panel, tmp_path):
    count = _counter(panel)
    panel.preview_at(str(_media(tmp_path, "a.mp4")), 1.0)
    if not _wait(lambda: count[0] >= 1):
        pytest.skip(
            "QtMultimedia ne décode pas dans cet environnement"
        ) if panel.player.error() != QMediaPlayer.NoError else pytest.fail(
            "aucune image livrée après preview_at sans lecture"
        )
    assert panel.player.playbackState() == QMediaPlayer.PausedState


def test_each_step_of_a_source_change_delivers_a_frame(panel, tmp_path):
    count = _counter(panel)
    a, b = str(_media(tmp_path, "a.mp4")), str(_media(tmp_path, "b.mp4"))
    seen = 0
    for step, action in enumerate(
        (
            lambda: panel.preview_at(a, 0.5),
            lambda: panel.preview_at(b, 0.5),
            panel.show_empty,
            lambda: panel.preview_at(a, 0.5),
        )
    ):
        action()
        if step == 2:  # show_empty : pas d'image attendue
            continue
        assert _wait(lambda: count[0] > seen), f"étape {step} : aucune image"
        seen = count[0]


def test_seek_while_paused_delivers_a_frame_each_time(panel, tmp_path):
    count = _counter(panel)
    path = str(_media(tmp_path, "a.mp4"))
    panel.preview_at(path, 0.2)
    assert _wait(lambda: count[0] >= 1)
    before = count[0]
    panel.preview_at(path, 1.2)
    assert _wait(lambda: count[0] > before)


def test_playing_branch_never_calls_pause(panel, tmp_path, monkeypatch):
    path = str(_media(tmp_path, "a.mp4"))
    panel.preview_at(path, 0.0)
    calls = []
    monkeypatch.setattr(panel.player, "pause", lambda: calls.append("pause"))
    panel.player.stop()
    panel.preview_at(path, 0.5, playing=True)
    assert calls == []


def test_missing_source_is_never_paused(panel, tmp_path, monkeypatch):
    # Le lecteur ne reçoit pas le chemin absent : un vrai QMediaPlayer journalise « Could not open media » depuis un
    # thread de Qt Multimedia, ce qui plantait un worker xdist Linux (voir keep_preview_player_off_the_disk).
    calls = []
    monkeypatch.setattr(panel.player, "setSource", lambda *_a, **_k: None)
    monkeypatch.setattr(panel.player, "pause", lambda: calls.append("pause"))
    panel.preview_at(str(tmp_path / "absent.mp4"), 3.0)
    panel.preview_at(str(tmp_path / "absent.mp4"), 0.0)
    assert calls == []


def test_paused_preview_keeps_transport_state_in_the_window(
    qtbot, monkeypatch, tmp_path
):
    window = _window(qtbot, monkeypatch, tmp_path)
    tooltip, icon = (
        window.preview_panel.play_button.toolTip(),
        window.preview_panel.play_button.icon().cacheKey(),
    )
    window.preview_panel.preview_at(str(_media(tmp_path, "a.mp4")), 0.5)
    for _ in range(20):
        QApplication.processEvents()
    assert window.is_playing is False
    assert window.preview_panel.play_button.toolTip() == tooltip
    assert window.preview_panel.play_button.icon().cacheKey() == icon
    assert not window.timeline_timer.isActive()
    assert not window.live_mix_active
