"""Karaoké synchronisé sur la voix : temps des mots estimés depuis l'enveloppe d'énergie."""

from __future__ import annotations

import subprocess
import wave

import numpy as np
import pytest

from core.word_timing import (
    distribute_words,
    estimate_word_times,
    syllable_weight,
    voiced_segments,
    word_times_from_voice,
)
from render_probe import needs_ffmpeg


def test_syllable_weights_follow_the_spoken_length():
    assert syllable_weight("go") == 1 and syllable_weight("Singapour") == 3 and syllable_weight("GP") == 2
    assert syllable_weight("2026") == 4 and syllable_weight("👇") == 0.5


def _speech(words: list[tuple[float, float]], seconds: float, rate: int = 8000, seed: int = 4) -> np.ndarray:
    """Bruit voisé (bande 300–3000 Hz) pendant chaque ``(début, durée)``, silence (souffle faible) ailleurs."""
    rng = np.random.default_rng(seed)
    signal = rng.normal(0, 0.002, int(seconds * rate))
    for start, length in words:
        n = int(length * rate)
        t = np.arange(n) / rate
        voice = sum(np.sin(2 * np.pi * f * t + rng.uniform(0, 6)) for f in (180, 360, 720, 1440))
        signal[int(start * rate): int(start * rate) + n] += 0.2 * voice * np.hanning(n)
    return signal


def _envelope(signal: np.ndarray, rate: int = 8000) -> np.ndarray:
    block = rate // 100
    frames = signal[: signal.size // block * block].reshape(-1, block)
    return np.log(np.sqrt((frames ** 2).mean(axis=1)) + 1e-5)


def test_pauses_separate_the_words_of_a_phrase():
    spoken = [(0.30, 0.25), (0.70, 0.40), (1.40, 0.22), (1.90, 0.55)]
    times = estimate_word_times("go full send now", _envelope(_speech(spoken, 3.0)))
    for (start, _length), estimated in zip(spoken, times):
        assert estimated == pytest.approx(start, abs=0.06)


def test_words_without_voice_are_refused():
    assert voiced_segments(np.full(300, -9.0)) == []
    with pytest.raises(ValueError):
        distribute_words(["silence"], [])


@needs_ffmpeg
def test_word_times_are_read_from_a_real_voice_file_and_shifted(tmp_path):
    spoken = [(0.20, 0.30), (0.80, 0.30), (1.50, 0.45)]
    signal = _speech(spoken, 2.5)
    raw = tmp_path / "vo.wav"
    with wave.open(str(raw), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes((np.clip(signal, -1, 1) * 32767).astype("<i2").tobytes())
    voice = tmp_path / "vo.m4a"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(raw), "-ar", "48000", "-c:a", "aac", str(voice)],
                   check=True, timeout=60)
    times = word_times_from_voice("on lap three", str(voice), media_start=0.0, duration=2.5, shift=1.0)
    assert [round(t, 1) for t in times] == pytest.approx([1.2, 1.8, 2.5], abs=0.11)
