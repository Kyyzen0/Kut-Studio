"""La garde globale (``tests/media_player_guard.py``) : un média inexistant n'atteint jamais le vrai lecteur."""

from __future__ import annotations

import struct
import wave

import pytest
from PySide6.QtCore import QUrl, qInstallMessageHandler
from PySide6.QtMultimedia import QMediaPlayer

from media_player_guard import GUARD_FLAG


def test_a_missing_local_file_is_never_handed_to_the_real_player(qtbot) -> None:
    messages: list[str] = []
    previous = qInstallMessageHandler(lambda _mode, _ctx, msg: messages.append(msg))
    try:
        player = QMediaPlayer()
        player.setSource(QUrl.fromLocalFile("/x/inexistant.mp4"))
        qtbot.wait(150)
        assert player.source().isEmpty()
        assert not [m for m in messages if "Could not open media" in m]
    finally:
        qInstallMessageHandler(previous)
        player.deleteLater()


def test_an_existing_file_still_reaches_the_real_player(tmp_path, qtbot) -> None:
    path = tmp_path / "tone.wav"
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(8000)
        out.writeframes(struct.pack("<800h", *([0] * 800)))
    assert getattr(QMediaPlayer.setSource, GUARD_FLAG, False), "la garde doit être installée"
    player = QMediaPlayer()
    url = QUrl.fromLocalFile(str(path))
    player.setSource(url)
    assert player.source() == url
    player.setSource(QUrl())
    assert player.source().isEmpty()
    player.deleteLater()


@pytest.mark.real_media_player
def test_the_marker_turns_the_guard_off(request) -> None:
    assert any(line.startswith("real_media_player") for line in request.config.getini("markers"))
    assert not getattr(QMediaPlayer.setSource, GUARD_FLAG, False)
