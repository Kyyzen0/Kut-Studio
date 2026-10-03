"""Son en direct d'un segment Multicam : les sources audio que la politique désigne, jouées à côté du moniteur.

Le moniteur temps réel ne lit que le son du fichier de l'image affichée. Avec une politique « son fixe » (l'enregistreur) ou
« mixte », ce n'est pas le son voulu : le lecteur du moniteur est alors coupé, et ce petit ensemble de lecteurs audio joue
les sources désignées, calées sur la tête de lecture (même tolérance de dérive que le moniteur). L'aperçu fidèle et l'export
donnent, eux, le mixage exact : ceci n'est que le retour en lecture directe.

Tout lecteur a pour parent la fenêtre (arrêté à la fermeture, jamais orphelin) ; ``player_factory`` permet de le remplacer dans
les tests, qui ne jouent jamais de vrai média.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from PySide6.QtCore import QObject, QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

MAX_SOURCES = 4
"""Nombre maximal de sources audio jouées en direct (au-delà, les premières seulement : le mixage exact vient de l'aperçu)."""
DRIFT_SECONDS = 0.20
"""Écart toléré entre la position d'un lecteur et la tête de lecture avant recalage (comme ``PreviewPanel``)."""


@dataclass
class _Slot:
    player: object
    output: object
    path: str = ""


def _default_factory(parent: QObject):
    player = QMediaPlayer(parent)
    output = QAudioOutput(parent)
    output.setVolume(1.0)
    player.setAudioOutput(output)
    return player, output


class AuxAudio(QObject):
    """Joue jusqu'à :data:`MAX_SOURCES` sources audio calées sur la timeline."""

    def __init__(self, parent: QObject | None = None, *, player_factory: Callable[[QObject], tuple] | None = None) -> None:
        super().__init__(parent)
        self._factory = player_factory or _default_factory
        self._slots: list[_Slot] = []

    def sync(self, wanted: Sequence[tuple[str, float]], playing: bool) -> None:
        """Joue ``(chemin, instant dans le média)`` pour chaque source voulue ; arrête les autres."""
        wanted = list(wanted)[:MAX_SOURCES]
        remaining = list(self._slots)
        assigned: list[tuple[_Slot, float]] = []
        for path, media_time in wanted:
            slot = next((item for item in remaining if item.path == path), None)
            if slot is None:
                slot = next((item for item in remaining if not item.path), None)
            if slot is None and len(self._slots) < MAX_SOURCES:
                player, output = self._factory(self)
                slot = _Slot(player, output)
                self._slots.append(slot)
            if slot is None:
                slot = remaining[0] if remaining else None
            if slot is None:
                continue
            if slot in remaining:
                remaining.remove(slot)
            assigned.append((slot, media_time))
            self._drive(slot, path, media_time, playing)
        for slot in remaining:                      # sources qui ne sont plus voulues
            self._release(slot)

    def _drive(self, slot: _Slot, path: str, media_time: float, playing: bool) -> None:
        player = slot.player
        target_ms = int(max(0.0, media_time) * 1000)
        if slot.path != path:
            slot.path = path
            player.setSource(QUrl.fromLocalFile(path))
            player.setPosition(target_ms)
        elif abs(player.position() - target_ms) > DRIFT_SECONDS * 1000:
            player.setPosition(target_ms)
        if playing:
            player.play()
        else:
            player.pause()

    @staticmethod
    def _release(slot: _Slot) -> None:
        slot.player.stop()
        if slot.path:
            slot.player.setSource(QUrl())
        slot.path = ""

    def stop(self) -> None:
        """Arrête toutes les sources (la tête de lecture a quitté le segment)."""
        for slot in self._slots:
            if slot.path:
                self._release(slot)

    def active_paths(self) -> list[str]:
        return [slot.path for slot in self._slots if slot.path]

    def release(self) -> None:
        """Fermeture de la fenêtre : arrête et détache tous les lecteurs."""
        self.stop()
        self._slots.clear()


__all__ = ["AuxAudio", "DRIFT_SECONDS", "MAX_SOURCES"]
