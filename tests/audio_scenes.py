"""Scènes sonores synthétiques (aucun média réel) pour les tests de synchronisation audio.

Chaque générateur rend un signal mono ``float64`` dans [-1, 1] à :data:`SAMPLE_RATE`. ``write_wav`` l'écrit en WAV 16 bits ;
``delayed`` retarde ou avance une scène pour simuler une caméra qui démarre plus tôt ou plus tard.
"""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16000


def write_wav(path: Path, signal: np.ndarray, rate: int = SAMPLE_RATE) -> Path:
    """WAV 16 bits ; un tableau 2D (échantillons × canaux) donne un fichier multicanal."""
    data = np.clip(signal, -1.0, 1.0)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1 if data.ndim == 1 else data.shape[1])
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes((data * 32767).astype("<i2").tobytes())
    return path


def band_noise(rng: np.random.Generator, count: int, low: float, high: float) -> np.ndarray:
    spectrum = np.fft.rfft(rng.standard_normal(count))
    frequencies = np.fft.rfftfreq(count, 1 / SAMPLE_RATE)
    spectrum[(frequencies < low) | (frequencies > high)] = 0
    noise = np.fft.irfft(spectrum, count)
    return noise / (np.abs(noise).max() + 1e-9)


def speech(rng: np.random.Generator, seconds: float) -> np.ndarray:
    """Rafales de bruit filtré (250–3200 Hz) de 80 à 250 ms séparées de silences, comme des syllabes."""
    count = int(seconds * SAMPLE_RATE)
    out = np.zeros(count)
    position = 0
    while position < count:
        length = int(rng.uniform(0.08, 0.25) * SAMPLE_RATE)
        gap = int(rng.uniform(0.04, 0.5) * SAMPLE_RATE)
        burst = band_noise(rng, length, 250, 3200) * rng.uniform(0.2, 1.0) * np.hanning(length)
        out[position:position + length] += burst[: count - position]
        position += length + gap
    return out * 0.6


def applause(rng: np.random.Generator, seconds: float) -> np.ndarray:
    count = int(seconds * SAMPLE_RATE)
    envelope = np.abs(band_noise(rng, count, 0.2, 6.0)) ** 1.5
    return band_noise(rng, count, 800, 6000) * (0.2 + envelope) * 0.5


def ambient(rng: np.random.Generator, seconds: float) -> np.ndarray:
    return band_noise(rng, int(seconds * SAMPLE_RATE), 50, 4000) * 0.3


def music_loop(seconds: float, period: float = 2.0) -> np.ndarray:
    """Quatre notes répétées à l'identique : plusieurs décalages se valent (la synchronisation est ambiguë)."""
    cell = int(period * SAMPLE_RATE)
    t = np.arange(cell) / SAMPLE_RATE
    quarter = period / 4
    pattern = sum(
        np.sin(2 * np.pi * freq * t) * np.exp(-3 * ((t - start * quarter) % quarter))
        for freq, start in [(220, 0), (330, 1), (440, 2), (294, 3)]
    ) * 0.3
    count = int(seconds * SAMPLE_RATE)
    return np.tile(pattern, count // cell + 1)[:count]


def music_varied(rng: np.random.Generator, seconds: float) -> np.ndarray:
    """Notes de durées et de hauteurs variées : un morceau qui ne se répète pas."""
    count = int(seconds * SAMPLE_RATE)
    out = np.zeros(count)
    position = 0
    notes = [196, 220, 247, 262, 294, 330, 349, 392]
    while position < count:
        length = int(rng.choice([0.25, 0.5, 0.75]) * SAMPLE_RATE)
        freq = rng.choice(notes) * rng.choice([1, 2])
        t = np.arange(length) / SAMPLE_RATE
        note = (np.sin(2 * np.pi * freq * t) + 0.4 * np.sin(4 * np.pi * freq * t)) * np.exp(-4 * t) * 0.3
        out[position:position + length] += note[: count - position]
        position += length
    return out


def delayed(signal: np.ndarray, seconds: float) -> np.ndarray:
    """``seconds > 0`` : le même son arrive plus tard dans l'enregistrement (la source a démarré plus tôt)."""
    shift = int(round(seconds * SAMPLE_RATE))
    return np.concatenate([np.zeros(shift), signal]) if shift >= 0 else signal[-shift:]


def window(signal: np.ndarray, start: float, seconds: float) -> np.ndarray:
    first = int(start * SAMPLE_RATE)
    return signal[first:first + int(seconds * SAMPLE_RATE)]
