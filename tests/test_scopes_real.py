"""Les scopes avec un vrai décodeur PNG, de vrais threads et un vrai FFmpeg.

Régression : tous les tests d'interface des scopes utilisaient un faux analyseur appelé depuis le thread
principal ; le vrai chemin n'a jamais été exécuté. Il échouait deux fois : le décodage PNG levait
TypeError (PySide6 refuse un QBuffer), et le résultat, posté depuis le thread d'analyse avec
``QTimer.singleShot``, n'arrivait jamais dans la boucle Qt. Les scopes ne s'affichaient jamais.
"""

from __future__ import annotations

import shutil
import threading

import pytest
from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QColor, QImage
from test_scopes import _ramp_frame, _window

from core.scopes import analyze_frame
from core.scopes_analyzer import ScopeAnalysis, ScopeRequest, png_to_scope_frame

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg absent")


def _png(width: int, height: int, color: QColor) -> bytes:
    image = QImage(width, height, QImage.Format_RGB888)
    image.fill(color)
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.WriteOnly)
    assert image.save(buffer, "PNG")
    return bytes(data)


def test_a_real_png_is_decoded_to_the_right_pixels():
    frame = png_to_scope_frame(_png(8, 4, QColor(200, 30, 30)))
    assert (frame.width, frame.height) == (8, 4)
    assert set(frame.pixels) == {(200, 30, 30)}


def test_a_corrupt_png_is_a_clear_error_not_a_crash():
    from core.scopes_analyzer import ScopeExtractionError

    with pytest.raises(ScopeExtractionError):
        png_to_scope_frame(b"ceci n'est pas un PNG")


def test_a_result_posted_from_the_analysis_thread_reaches_the_panel(qtbot, monkeypatch):
    """Le rappel est appelé depuis un autre thread que le thread Qt, comme en production."""
    window = _window(qtbot, monkeypatch)
    result = analyze_frame(_ramp_frame(16, 16), columns=16, vectorscope_bins=16)
    analysis = ScopeAnalysis(
        request=ScopeRequest(request_id=1, playhead=0.0, ffmpeg_command=("fake",)),
        result=result, elapsed_seconds=0.01, stale=False,
    )
    worker = threading.Thread(target=lambda: window._on_scopes_analysis_ready(analysis))
    worker.start()
    worker.join()
    assert window.scopes_panel.result() is None or window.scopes_panel.result() is result
    qtbot.waitUntil(lambda: window.scopes_panel.result() is result, timeout=3000)


@needs_ffmpeg
def test_the_real_analyzer_shows_the_analysed_frame_in_the_panel(qtbot, monkeypatch):
    """De bout en bout : FFmpeg → PNG sur stdout → décodage → analyse (thread) → panneau (thread Qt)."""
    window = _window(qtbot, monkeypatch)
    command = [shutil.which("ffmpeg"), "-v", "error", "-f", "lavfi", "-i", "color=c=0xC81E1E:s=32x32:d=1",
               "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"]
    window.scopes_analyzer.submit(playhead=0.0, ffmpeg_command=command, columns=16, vectorscope_bins=16,
                                  force=True)
    qtbot.waitUntil(lambda: window.scopes_panel.result() is not None, timeout=20000)
    red = window.scopes_panel.result().histogram_r
    brightest = max(range(256), key=lambda level: red[level])
    assert abs(brightest - 200) <= 8, brightest        # rouge ≈ 200 (conversion yuv → rgb de FFmpeg)
