"""Garde globale des tests : un vrai ``QMediaPlayer`` n'ouvre jamais un fichier local qui n'existe pas.

Un lecteur qui reçoit un chemin fictif (``/media/a.mp4``, projet de démonstration, ``/x/other.mp4``) journalise « Could not
open media » **depuis un thread du pool de Qt Multimedia**, à un instant que le test ne maîtrise pas. Si le message tombe
pendant que pytest-qt change de gestionnaire de messages (début ou fin de test), PySide déréférence un rappel à moitié
restauré et le worker meurt (SIGSEGV sous xdist, voir ``multicam_stubs.keep_preview_player_off_the_disk``). On retire la
cause : ``QMediaPlayer.setSource`` ignore une ``QUrl`` locale dont le fichier est absent. Un fichier existant, une URL vide
(« fermer le média ») ou distante passent au vrai lecteur. Un test qui VEUT l'erreur de lecture porte
``@pytest.mark.real_media_player``.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

GUARD_FLAG = "_kut_missing_media_guard"


def _is_missing_local_file(url: Any) -> bool:
    try:
        if not url.isLocalFile():
            return False
        return not os.path.exists(url.toLocalFile())
    except (AttributeError, RuntimeError):
        return False


def make_guarded(original: Callable[..., Any]) -> Callable[..., Any]:
    """Enveloppe ``setSource`` : ignore (sans rien ouvrir) une URL locale inexistante."""

    def setSource(self, url, *args, **kwargs):  # noqa: N802 - API Qt
        if _is_missing_local_file(url):
            return None
        return original(self, url, *args, **kwargs)

    setattr(setSource, GUARD_FLAG, True)
    setSource.__wrapped__ = original  # type: ignore[attr-defined]
    return setSource


def install(monkeypatch) -> bool:
    """Pose la garde sur la classe (retirée par ``monkeypatch``). Faux si Qt Multimedia est absent."""
    try:
        from PySide6.QtMultimedia import QMediaPlayer
    except ImportError:  # pragma: no cover - Qt absent
        return False
    current = QMediaPlayer.setSource
    if getattr(current, GUARD_FLAG, False):
        return True
    monkeypatch.setattr(QMediaPlayer, "setSource", make_guarded(current))
    return True
