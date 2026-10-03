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
