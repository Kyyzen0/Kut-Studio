"""Shared pytest configuration for Kut-Studio."""

import os
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# The tests build Qt widgets but do not require an on-screen desktop session.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def _isolate_kut_studio_config(monkeypatch, tmp_path):
    """Empêche les tests de lire ou modifier les presets de l'utilisateur."""
    monkeypatch.setenv("KUT_STUDIO_CONFIG_DIR", str(tmp_path / "config"))


@pytest.fixture(scope="session", autouse=True)
def _release_media_players_at_exit():
    """Arrête les ``QMediaPlayer`` encore vivants avant la fin du processus.

    Les tests créent des fenêtres sans les fermer : leurs threads FFmpeg
    peuvent alors journaliser pendant la finalisation de Python, ce qui
    fait planter le gestionnaire de messages Qt de pytest-qt.
    """
    yield
    try:
        from PySide6.QtCore import QCoreApplication, QUrl
        from PySide6.QtMultimedia import QMediaPlayer
        from PySide6.QtWidgets import QApplication
    except ImportError:  # pragma: no cover - Qt absent
        return
    app = QApplication.instance()
    if app is None:
        return
    for widget in QApplication.topLevelWidgets():
        for player in widget.findChildren(QMediaPlayer):
            try:
                player.stop()
                player.setSource(QUrl())
            except RuntimeError:  # objet C++ déjà détruit
                pass
    QCoreApplication.processEvents()
