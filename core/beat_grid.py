"""Grille rythmique d'une séquence : tempo (BPM), premier temps, temps par mesure.

Un montage « sur le temps » coupe là où la musique frappe. La grille est une donnée du montage (elle vit dans la
séquence et le ``.kut``) ; tout le reste la lit : la règle de la timeline dessine les temps, le magnétisme s'y accroche,
« Couper sur les temps » et « Répartir sur la grille » en déduisent leurs positions (:mod:`core.beat_edit`).

Les temps sont en **secondes de la timeline** : ``offset + k × 60 / bpm`` (``k`` entier, positif ou négatif). Un temps
``k`` multiple de ``beats_per_bar`` (compté depuis ``offset``) est un premier temps de mesure.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

MIN_BPM = 20.0
MAX_BPM = 300.0
MAX_BEATS_PER_BAR = 16


@dataclass(frozen=True)
class BeatGrid:
    """Tempo et calage d'une séquence.

    Attributes:
        bpm: temps par minute (20 à 300).
        offset: instant d'un premier temps de mesure (secondes de la timeline) ; la grille s'étend des deux côtés.
        beats_per_bar: temps par mesure (4 pour du 4/4).
    """

    bpm: float
    offset: float = 0.0
    beats_per_bar: int = 4

    def __post_init__(self) -> None:
        bpm = float(self.bpm)
        if not (bpm == bpm and MIN_BPM <= bpm <= MAX_BPM):
            raise ValueError(f"Tempo refusé : {self.bpm} BPM (entre {MIN_BPM:g} et {MAX_BPM:g}).")
        offset = float(self.offset)
        if not math.isfinite(offset):
            raise ValueError("Calage de la grille non fini.")
        beats = int(self.beats_per_bar)
        if not 1 <= beats <= MAX_BEATS_PER_BAR:
            raise ValueError(f"Temps par mesure refusés : {self.beats_per_bar}.")
        object.__setattr__(self, "bpm", bpm)
        object.__setattr__(self, "offset", offset)
        object.__setattr__(self, "beats_per_bar", beats)

    @property
    def beat(self) -> float:
        """Durée d'un temps (secondes)."""
        return 60.0 / self.bpm

    @property
    def bar(self) -> float:
        return self.beat * self.beats_per_bar

    def index_at(self, t: float) -> float:
        """Position de ``t`` en temps (fractionnaire) depuis ``offset``."""
        return (float(t) - self.offset) / self.beat

    def time_of(self, index: int | float) -> float:
        return self.offset + float(index) * self.beat

    def beat_times(self, start: float, end: float, *, every: int = 1) -> list[float]:
        """Temps (un sur ``every``) compris dans ``[start, end]``, arrondis à la microseconde."""
        every = max(1, int(every))
        first = math.ceil(self.index_at(start) / every - 1e-9) * every
        times = []
        index = first
        while True:
            t = round(self.time_of(index), 6)
            if t > end + 1e-9:
                return times
            times.append(t)
            index += every

    def is_downbeat(self, index: int) -> bool:
        return int(index) % self.beats_per_bar == 0

    def nearest_beat(self, t: float, *, subdivision: int = 1) -> float:
        """Temps (ou subdivision : 2 = croches, 4 = doubles) le plus proche de ``t``."""
        step = self.beat / max(1, int(subdivision))
        return round(self.offset + round((float(t) - self.offset) / step) * step, 6)


def beat_grid_to_dict(grid: BeatGrid | None) -> dict | None:
    if grid is None:
        return None
    return {"bpm": grid.bpm, "offset": grid.offset, "beats_per_bar": grid.beats_per_bar}


def beat_grid_from_dict(raw: object) -> BeatGrid | None:
    """Grille relue d'un ``.kut`` : absente ou abîmée, la séquence n'a simplement pas de grille."""
    if not isinstance(raw, dict):
        return None
    try:
        return BeatGrid(float(raw["bpm"]), float(raw.get("offset", 0.0)), int(raw.get("beats_per_bar", 4)))
    except (KeyError, TypeError, ValueError):
        return None


def tap_tempo(taps: list[float]) -> float | None:
    """Tempo de frappes successives (secondes) : moyenne des écarts, ``None`` sous trois frappes ou hors bornes.

    Les frappes de plus de deux secondes d'écart recommencent la mesure (l'utilisateur s'est arrêté)."""
    recent: list[float] = []
    for t in taps:
        if recent and t - recent[-1] > 2.0:
            recent = []
        recent.append(float(t))
    if len(recent) < 3:
        return None
    intervals = [b - a for a, b in zip(recent, recent[1:]) if b > a]
    if not intervals:
        return None
    bpm = 60.0 / (sum(intervals) / len(intervals))
    return round(bpm, 1) if MIN_BPM <= bpm <= MAX_BPM else None


__all__ = [
    "BeatGrid", "MAX_BEATS_PER_BAR", "MAX_BPM", "MIN_BPM", "beat_grid_from_dict", "beat_grid_to_dict", "tap_tempo",
]
