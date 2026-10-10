"""Détection du tempo et du calage d'une musique : de quoi poser la grille rythmique sans compter à la main.

Analyse en numpy (comme :mod:`core.audio_sync`), jamais dans le chemin de l'export :

1. **force d'attaque** : flux spectral (hausse de l'énergie par bande, en échelle logarithmique) par pas de 10 ms, sur
   le son décodé en mono à 8 kHz (:func:`core.audio_sync.read_window`, FFmpeg lancé par le superviseur) ;
2. **tempo** : autocorrélation de cette force, lue en peigne (période, double, triple, quadruple) entre 70 et 180 BPM,
   pondérée vers 120 BPM d'une octave pour trancher entre un tempo et son double ;
3. **affinage et calage** : autour du meilleur tempo, la période et la phase qui alignent le mieux une grille sur les
   attaques de **tout** le morceau (précision bien meilleure que le pas de 10 ms) ;
4. **premier temps** : parmi les ``beats_per_bar`` phases, celle dont les attaques graves (grosse caisse, 808) sont
   les plus fortes.

Le résultat est une suggestion : l'utilisateur la voit sur la règle et la corrige (tap tempo, calage).
"""

from __future__ import annotations

import math
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .beat_grid import BeatGrid

ANALYSIS_RATE = 8000
HOP = 80                         # 10 ms
FRAME = 256                      # 32 ms
FRAMES_PER_SECOND = ANALYSIS_RATE / HOP
MIN_BPM, MAX_BPM = 70.0, 180.0
PRIOR_BPM = 120.0
MAX_ANALYSIS_SECONDS = 90.0


@dataclass(frozen=True)
class TempoEstimate:
    """Tempo et calage estimés, en temps **du média** (0 = début du fichier analysé)."""

    bpm: float
    first_beat: float
    downbeat: float
    confidence: float

    def grid_for_clip(self, timeline_start: float, source_in: float, beats_per_bar: int = 4) -> BeatGrid:
        """Grille de la séquence pour un clip qui lit ce média de ``source_in`` à partir de ``timeline_start``."""
        return BeatGrid(round(self.bpm, 2), round(timeline_start + self.downbeat - source_in, 6), beats_per_bar)


def _np() -> Any:
    import numpy as np

    return np


def onset_strength(samples, rate: int = ANALYSIS_RATE):
    """Force d'attaque par pas de 10 ms (flux spectral positif, en log), centrée et normalisée ; et sa part grave."""
    np = _np()
    data = np.asarray(samples, dtype=np.float64)
    if rate != ANALYSIS_RATE:
        raise ValueError("Analyse prévue à 8 kHz.")
    count = 1 + max(0, (data.size - FRAME) // HOP)
    if data.size < FRAME * 4:
        raise ValueError("Extrait trop court pour mesurer un tempo.")
    index = np.arange(FRAME)[None, :] + HOP * np.arange(count)[:, None]
    frames = data[index] * np.hanning(FRAME)[None, :]
    magnitude = np.log1p(100.0 * np.abs(np.fft.rfft(frames, axis=1)))
    rise = np.maximum(0.0, np.diff(magnitude, axis=0, prepend=magnitude[:1]))
    full = rise.sum(axis=1)
    low = rise[:, : int(200 * FRAME / rate) + 1].sum(axis=1)          # sous 200 Hz : grosse caisse, basse
    result = []
    for curve in (full, low):
        smooth = np.convolve(curve, np.ones(31) / 31.0, mode="same")  # moyenne locale sur ~0,3 s
        curve = np.maximum(0.0, curve - smooth)
        scale = curve.std() or 1.0
        result.append(curve / scale)
    return result[0], result[1]


def _interp(np, curve, positions):
    return np.interp(positions, np.arange(curve.size), curve, left=0.0, right=0.0)


def _comb_score(np, acf, period: float) -> float:
    return float(sum(_interp(np, acf, np.array([k * period]))[0] / k for k in (1, 2, 3, 4)))


def _alignment(np, onset, period: float, phase: float) -> float:
    positions = np.arange(phase, onset.size - 1, period)
    if positions.size < 2:
        return 0.0
    return float(_interp(np, onset, positions).mean())


def estimate_tempo(samples, *, beats_per_bar: int = 4) -> TempoEstimate:
    """Tempo, premier temps et premier temps de mesure d'un extrait PCM mono à 8 kHz (``float`` dans [-1, 1])."""
    np = _np()
    onset, low = onset_strength(samples)
    n = onset.size
    spectrum = np.fft.rfft(onset - onset.mean(), n=2 * n)
    acf = np.fft.irfft(spectrum * np.conj(spectrum))[:n]
    acf = acf / (acf[0] or 1.0)
    candidates = np.arange(MIN_BPM, MAX_BPM + 1e-9, 0.25)
    scores = []
    for bpm in candidates:
        prior = math.exp(-0.5 * (math.log2(bpm / PRIOR_BPM) / 1.0) ** 2)
        scores.append(_comb_score(np, acf, 60.0 * FRAMES_PER_SECOND / bpm) * prior)
    coarse = float(candidates[int(np.argmax(scores))])
    # Affinage : la période et la phase qui alignent une grille sur les attaques de tout l'extrait.
    best = (-1.0, coarse, 0.0)
    for bpm in np.arange(coarse - 0.75, coarse + 0.7501, 0.02):
        period = 60.0 * FRAMES_PER_SECOND / bpm
        for phase in np.arange(0.0, period, 0.5):
            score = _alignment(np, onset, period, phase)
            if score > best[0]:
                best = (score, float(bpm), float(phase))
    score, bpm, phase = best
    period = 60.0 * FRAMES_PER_SECOND / bpm
    beat_scores = [_alignment(np, low, period * beats_per_bar, phase + k * period) for k in range(beats_per_bar)]
    bar_phase = phase + int(np.argmax(beat_scores)) * period
    # Une trame est datée par son centre (son début ferait arriver chaque attaque 16 ms trop tôt).
    centre = FRAME / 2.0 / ANALYSIS_RATE
    first_beat = phase / FRAMES_PER_SECOND + centre
    downbeat = bar_phase / FRAMES_PER_SECOND + centre
    # Confiance : alignement sur la grille comparé à la moyenne de la force d'attaque (1 = pas mieux que le hasard).
    confidence = float(score / (onset.mean() or 1.0))
    return TempoEstimate(round(bpm, 2), round(first_beat, 4), round(downbeat, 4), round(confidence, 3))


def detect_tempo(
    path: str, *, start: float = 0.0, duration: float | None = None, beats_per_bar: int = 4,
    cancelled: Callable[[], bool] = lambda: False,
) -> TempoEstimate:
    """Décode ``path`` (de ``start`` sur ``duration`` s, 90 s au plus) et en estime le tempo.

    Les temps du résultat sont comptés depuis ``start`` + 0 : ``first_beat`` et ``downbeat`` sont en temps du média.
    """
    from .audio_sync import SAMPLE_RATE, read_window

    if SAMPLE_RATE != ANALYSIS_RATE:            # même décodage que la synchronisation audio
        raise RuntimeError("Fréquence d'analyse incohérente.")
    seconds = MAX_ANALYSIS_SECONDS if duration is None else min(float(duration), MAX_ANALYSIS_SECONDS)
    samples = read_window(path, float(start), seconds, cancelled)
    estimate = estimate_tempo(samples, beats_per_bar=beats_per_bar)
    return TempoEstimate(estimate.bpm, round(estimate.first_beat + start, 4), round(estimate.downbeat + start, 4),
                         estimate.confidence)


def plays_at_media_speed(clip: Any) -> bool:
    """``True`` si un instant de la timeline et l'instant lu dans le média avancent ensemble (vitesse 1, sans retiming).

    La grille déduite d'une musique (:meth:`TempoEstimate.grid_for_clip`) n'est juste que pour un tel clip : retimé,
    inversé, figé, imbriqué ou accéléré, ses temps ne tombent plus aux mêmes instants de la timeline."""
    return not (
        clip.is_time_remapped or clip.has_speed_curve or clip.is_reversed or clip.is_frozen or clip.is_nested
        or clip.is_composition or abs(clip.speed - 1.0) > 1e-9
    )


@dataclass(frozen=True)
class TempoDetectionSnapshot:
    """État d'une mesure, lu par l'interface : ``queued``, ``running``, ``finished``, ``cancelled`` ou ``failed``."""

    state: str
    message: str
    result: TempoEstimate | None


class TempoDetectionJob:
    """Mesure du tempo d'une musique pour la file d'analyse : annulation (FFmpeg tué aussitôt) et résultat.

    Même contrat que :class:`core.scene_detection.SceneDetectionJob` : l'interface lit :meth:`snapshot` sur un minuteur.
    Le décodage et l'estimation durent une à quelques secondes, sans étape mesurable : pas de progression chiffrée.
    """

    def __init__(
        self, path: str, *, start: float, duration: float, beats_per_bar: int = 4, session_id: str = "",
    ) -> None:
        if duration <= 0:
            raise ValueError("Durée de mesure nulle")
        self.session_id = session_id
        self._path = path
        self._start = float(start)
        self._duration = float(duration)
        self._beats_per_bar = int(beats_per_bar)
        self._cancel = threading.Event()
        self._lock = threading.Lock()
        self._state = "queued"
        self._message = ""
        self._result: TempoEstimate | None = None

    def cancel(self) -> None:
        self._cancel.set()
        with self._lock:
            if self._state == "queued":
                self._state = "cancelled"

    def snapshot(self) -> TempoDetectionSnapshot:
        with self._lock:
            return TempoDetectionSnapshot(self._state, self._message, self._result)

    def run(self, token: object = None) -> None:
        from .audio_sync import SyncCancelled

        with self._lock:
            if self._cancel.is_set() or self._state == "cancelled":
                self._state = "cancelled"
                return
            self._state = "running"
        try:
            estimate = detect_tempo(
                self._path, start=self._start, duration=self._duration, beats_per_bar=self._beats_per_bar,
                cancelled=self._cancel.is_set,
            )
        except SyncCancelled:
            self._finish("cancelled")
            return
        except Exception as error:  # noqa: BLE001 - une musique illisible ne doit pas bloquer la file d'analyse
            self._finish("failed", message=str(error) or type(error).__name__)
            return
        self._finish("cancelled" if self._cancel.is_set() else "finished", result=estimate)

    def _finish(self, state: str, *, result: TempoEstimate | None = None, message: str = "") -> None:
        with self._lock:
            self._state = state
            self._result = result if state == "finished" else None
            self._message = message


__all__ = [
    "TempoDetectionJob", "TempoDetectionSnapshot", "TempoEstimate", "detect_tempo", "estimate_tempo",
    "onset_strength", "plays_at_media_speed",
]
