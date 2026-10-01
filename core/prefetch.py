"""Préchargement intelligent des segments d'aperçu autour de la tête de lecture.

Logique **pure** (aucun Qt, aucun FFmpeg) : à partir de la position et de
la vitesse récente de la tête de lecture, décide *quels* segments rendre,
dans *quel ordre*, et lesquels ne valent plus la peine d'être gardés en
file.

Pourquoi : avant, les segments étaient planifiés à partir de la position
exacte de la tête (non alignés), 4 par 4, sans jamais retirer les demandes
devenues inutiles. Un utilisateur qui balaie la timeline remplissait la
file puis le disque de segments qu'il ne reverrait jamais.

Politique
---------

- **Balayage rapide** (|vitesse| ≥ ``fast_speed``) : seul le segment
  courant est demandé ; tout le reste de la file est abandonné.
- **Lecture / défilement lent** : le segment courant d'abord, puis les
  suivants **dans le sens du mouvement** (jamais derrière).
- **À l'arrêt** : segment courant, un devant, un derrière, un second devant
  (autant de segments qu'avant, mais alignés sur une grille : ils
  resservent d'une position à l'autre).

La priorité suit la distance au segment courant (plus petit = plus urgent,
comme :mod:`core.task_queue`).
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass

DEFAULT_SEGMENT_SECONDS = 2.0

PRIORITY_CURRENT = 0
PRIORITY_NEAR = 10
"""Pas entre deux rangs de priorité : 10, 20, 30…"""


@dataclass(frozen=True)
class PrefetchRequest:
    """Un segment à rendre : indice de grille, priorité et rôle."""

    index: int
    priority: int
    role: str  # "current", "ahead" ou "behind"


class PrefetchPlanner:
    """Estime la vitesse de la tête de lecture et planifie les segments."""

    def __init__(
        self,
        *,
        segment_seconds: float = DEFAULT_SEGMENT_SECONDS,
        fast_speed: float = 6.0,
        still_speed: float = 0.25,
        smoothing: float = 0.5,
        max_gap_seconds: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if segment_seconds <= 0:
            raise ValueError("La durée d'un segment doit être strictement positive.")
        self.segment_seconds = float(segment_seconds)
        self.fast_speed = float(fast_speed)
        self.still_speed = float(still_speed)
        self._smoothing = float(smoothing)
        self._max_gap = float(max_gap_seconds)
        self._clock = clock
        self._last_position: float | None = None
        self._last_time: float | None = None
        self._velocity = 0.0

    # -- vitesse ------------------------------------------------------------------

    @property
    def velocity(self) -> float:
        """Vitesse lissée de la tête, en secondes de timeline par seconde réelle."""
        return self._velocity

    def reset(self) -> None:
        self._last_position = None
        self._last_time = None
        self._velocity = 0.0

    def note_position(self, position: float, now: float | None = None) -> float:
        """Enregistre une position et retourne la vitesse lissée.

        Un long silence (``max_gap_seconds``) remet la vitesse à zéro :
        l'utilisateur s'est arrêté, la tête est « à l'arrêt ».
        """
        moment = self._clock() if now is None else float(now)
        if self._last_position is not None and self._last_time is not None:
            elapsed = moment - self._last_time
            if elapsed > self._max_gap:
                self._velocity = 0.0
            elif elapsed > 1e-6:
                instant = (float(position) - self._last_position) / elapsed
                self._velocity = (
                    self._smoothing * instant + (1.0 - self._smoothing) * self._velocity
                )
        self._last_position = float(position)
        self._last_time = moment
        return self._velocity

    # -- grille ----------------------------------------------------------------------

    def index_of(self, seconds: float) -> int:
        return max(0, int(math.floor(float(seconds) / self.segment_seconds + 1e-9)))

    def bounds_of(self, index: int) -> tuple[float, float]:
        start = index * self.segment_seconds
        return (start, start + self.segment_seconds)

    def last_index(self, duration: float) -> int:
        if duration <= 0:
            return 0
        return max(0, int(math.ceil(float(duration) / self.segment_seconds)) - 1)

    # -- plan -------------------------------------------------------------------------

    def plan(
        self,
        center: float,
        *,
        duration: float,
        velocity: float | None = None,
    ) -> list[PrefetchRequest]:
        """Segments à rendre, du plus urgent au moins urgent."""
        speed = self._velocity if velocity is None else float(velocity)
        base = self.index_of(center)
        last = self.last_index(duration)
        if base > last:
            return []
        requests = [PrefetchRequest(base, PRIORITY_CURRENT, "current")]
        magnitude = abs(speed)
        if magnitude >= self.fast_speed:
            return requests
        if magnitude <= self.still_speed:
            offsets = [(1, "ahead"), (-1, "behind"), (2, "ahead")]
        else:
            step = 1 if speed > 0 else -1
            role = "ahead"
            offsets = [(step * k, role) for k in (1, 2, 3)]
        rank = 1
        for offset, role in offsets:
            index = base + offset
            if 0 <= index <= last:
                requests.append(PrefetchRequest(index, PRIORITY_NEAR * rank, role))
                rank += 1
        return requests

    def keep_range(
        self, center: float, *, duration: float, velocity: float | None = None
    ) -> tuple[int, int]:
        """Intervalle d'indices (inclus) dont les demandes en file restent utiles.

        Tout segment en attente hors de cet intervalle peut être abandonné.
        """
        planned = self.plan(center, duration=duration, velocity=velocity)
        indices = [request.index for request in planned] or [self.index_of(center)]
        return (min(indices), max(indices))


__all__ = [
    "DEFAULT_SEGMENT_SECONDS",
    "PRIORITY_CURRENT",
    "PRIORITY_NEAR",
    "PrefetchPlanner",
    "PrefetchRequest",
]
