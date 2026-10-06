"""Détection du tempo : boucles de batterie synthétiques à tempo et calage connus."""

from __future__ import annotations

import time

import numpy as np
import pytest

from core.beat_detection import ANALYSIS_RATE, detect_tempo, estimate_tempo
from render_probe import needs_ffmpeg

RATE = ANALYSIS_RATE


def _kick(rate: int = RATE) -> np.ndarray:
    t = np.arange(int(0.25 * rate)) / rate
    phase = 2 * np.pi * np.cumsum(50 + 110 * np.exp(-t / 0.03)) / rate
    return np.sin(phase) * np.exp(-t / 0.12)


def _snare(rng, rate: int = RATE) -> np.ndarray:
    t = np.arange(int(0.18 * rate)) / rate
    return rng.normal(0, 0.5, t.size) * np.exp(-t / 0.05)


def _hat(rng, rate: int = RATE) -> np.ndarray:
    t = np.arange(int(0.05 * rate)) / rate
    return np.diff(rng.normal(0, 0.3, t.size + 1)) * np.exp(-t / 0.012)


def drum_loop(bpm: float, offset: float, seconds: float, *, seed: int = 3) -> np.ndarray:
    """4/4 : grosse caisse sur 1 et sur le « et » de 2, caisse claire sur 2 et 4, charleston en croches ; premier
    temps à ``offset``. La grosse caisse n'est pas symétrique (comme dans un vrai rythme) : le 1 est reconnaissable."""
    rng = np.random.default_rng(seed)
    out = np.zeros(int(seconds * RATE) + RATE)
    beat = 60.0 / bpm
    kick, snare = _kick(), _snare(rng)
    index = 0
    while offset + index * beat / 2 < seconds:
        t = offset + index * beat / 2
        start = int(round(t * RATE))
        hat = _hat(rng)
        out[start:start + hat.size] += 0.35 * hat
        eighth = index % 8
        if eighth in (0, 3):
            out[start:start + kick.size] += 0.8 * kick
        if eighth in (2, 6):
            out[start:start + snare.size] += 0.8 * snare
        index += 1
    return np.clip(out[: int(seconds * RATE)], -1, 1)


@pytest.mark.parametrize(("bpm", "offset"), [(90.0, 0.31), (120.0, 0.05), (128.0, 0.4), (140.0, 0.12)])
def test_tempo_and_downbeat_of_a_drum_loop(bpm, offset):
    estimate = estimate_tempo(drum_loop(bpm, offset, 30.0))
    assert estimate.bpm == pytest.approx(bpm, abs=0.5)
    beat = 60.0 / bpm
    # Le premier temps de mesure tombe sur une grosse caisse « 1 » (à une mesure près), à 20 ms près.
    phase = (estimate.downbeat - offset) % (4 * beat)
    assert min(phase, 4 * beat - phase) <= 0.02, estimate
    assert estimate.confidence > 2.0


def test_estimation_is_fast_enough_for_the_interface():
    start = time.perf_counter()
    estimate_tempo(drum_loop(120.0, 0.0, 60.0))
    assert time.perf_counter() - start < 5.0


def test_a_too_short_excerpt_is_refused():
    with pytest.raises(ValueError):
        estimate_tempo(np.zeros(500))


@needs_ffmpeg
def test_detection_reads_a_real_file_through_ffmpeg(tmp_path):
    import subprocess
    import wave

    signal = drum_loop(120.0, 0.25, 20.0)
    raw = tmp_path / "loop.wav"
    with wave.open(str(raw), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(RATE)
        handle.writeframes((signal * 32767).astype("<i2").tobytes())
    music = tmp_path / "loop.m4a"                       # un vrai format compressé, rééchantillonné à la lecture
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(raw), "-ar", "48000", "-c:a", "aac", "-b:a", "128k",
                    str(music)], check=True, timeout=60)
    estimate = detect_tempo(str(music), start=2.0)
    assert estimate.bpm == pytest.approx(120.0, abs=0.5)
    phase = (estimate.downbeat - 0.25) % 2.0
    assert min(phase, 2.0 - phase) <= 0.03
    grid = estimate.grid_for_clip(timeline_start=10.0, source_in=2.0)
    assert grid.bpm == pytest.approx(120.0, abs=0.5)
