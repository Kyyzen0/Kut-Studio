"""Flux d'angles factices pour les tests d'interface : un aplat de couleur par média, aucun processus FFmpeg."""

from __future__ import annotations

from core.multicam_feed import AngleFeed, Frame, TileProfile

COLOURS = [(220, 40, 40), (40, 200, 60), (50, 70, 230), (230, 190, 30)] * 6


class StubFeed(AngleFeed):
    """Flux factice : une image unie dont la couleur dépend du média ; aucun processus."""

    created: list[str] = []
    lag = 0.0

    def __init__(self, path: str, profile: TileProfile) -> None:
        super().__init__(path, profile)
        index = int("".join(ch for ch in path if ch.isdigit()) or 0)
        self._frame = Frame(0.0, 8, 8, bytes(COLOURS[index % len(COLOURS)]) * 64)
        self.updates: list[tuple[float, bool]] = []
        self.closed = False
        StubFeed.created.append(path)

    def update(self, media_time: float, playing: bool) -> None:
        self.updates.append((media_time, playing))
        self._running = True

    @property
    def running(self) -> bool:
        return getattr(self, "_running", False) and not self.closed

    def frame_at(self, media_time: float) -> Frame:
        self.last_lag = StubFeed.lag
        return self._frame

    def close(self) -> None:
        self.closed = True


def keep_preview_player_off_the_disk(window, monkeypatch) -> None:
    """Le lecteur de l'aperçu ne doit pas ouvrir les médias fictifs (``/media/a.mp4``) des projets de test.

    Un vrai ``QMediaPlayer`` qui reçoit un chemin inexistant journalise « Could not open media » **depuis un thread du
    pool de Qt Multimedia**, à un instant que le test ne maîtrise pas. Si ce message tombe pendant que pytest-qt change de
    gestionnaire de messages (début ou fin de test), PySide déréférence un rappel à moitié restauré et le processus meurt
    (SIGSEGV observé sous xdist, jamais en lançant le fichier seul ; voir aussi ``_release_media_players_at_exit``,
    qui traite la même famille de plantage à la sortie du processus). Aucun test Multicam ne lit un vrai média ici.
    """
    monkeypatch.setattr(window.preview_panel.player, "setSource", lambda *_a, **_k: None)

