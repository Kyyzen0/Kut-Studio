"""Sons de test synthétiques et **déterministes** (aucun enregistrement, aucun alea de plateforme).

Le bruit vient d'un générateur à entiers 64 bits (SplitMix64) : mêmes graines, mêmes échantillons, quelle que soit la
version de NumPy. Les signaux (parole, musique, nappe) sont des sommes de sinusoïdes et d'enveloppes ; deux générations
donnent les mêmes octets sur une machine donnée, et des octets qui ne diffèrent d'un échantillon que par l'arrondi de la
fonction ``sin`` de la bibliothèque mathématique d'une autre plateforme (voir ``docs/recette/README.md``).

NumPy n'est importé qu'à l'appel (il n'est requis que pour l'analyse, comme dans le reste de l'application).
"""

from __future__ import annotations

import wave
from pathlib import Path
from typing import Any

SPEECH_RATE = 16000
MUSIC_RATE = 44100
_MASK = (1 << 64) - 1


def _np() -> Any:
    import numpy as np

    return np


def _mix(value: int) -> int:
    """SplitMix64 sur un entier Python (usage scalaire : ordonnancement des syllabes)."""
    value = (value + 0x9E3779B97F4A7C15) & _MASK
    value = ((value ^ (value >> 30)) * 0xBF58476D1CE4E5B9) & _MASK
    value = ((value ^ (value >> 27)) * 0x94D049BB133111EB) & _MASK
    return value ^ (value >> 31)


def unit(seed: int, index: int) -> float:
    """Nombre déterministe dans [0, 1[ pour (``seed``, ``index``)."""
    return (_mix((seed * 0x100000001B3 + index) & _MASK) >> 11) * (1.0 / (1 << 53))


def noise(seed: int, start: int, count: int) -> Any:
    """``count`` échantillons de bruit blanc dans [-1, 1[ à partir de l'indice ``start`` (vectorisé, par blocs)."""
    np = _np()
    out = np.empty(count, dtype=np.float64)
    block = 1 << 20
    base = np.uint64((seed * 0x100000001B3) & _MASK)
    c1, c2, c3 = np.uint64(0x9E3779B97F4A7C15), np.uint64(0xBF58476D1CE4E5B9), np.uint64(0x94D049BB133111EB)
    s30, s27, s31, s11 = np.uint64(30), np.uint64(27), np.uint64(31), np.uint64(11)
    for offset in range(0, count, block):
        length = min(block, count - offset)
        z = np.arange(start + offset, start + offset + length, dtype=np.uint64) + base + c1
        z = (z ^ (z >> s30)) * c2
        z = (z ^ (z >> s27)) * c3
        z = z ^ (z >> s31)
        out[offset:offset + length] = (z >> s11).astype(np.float64) * (2.0 / (1 << 53)) - 1.0
    return out


def speech(seed: int, seconds: float, rate: int = SPEECH_RATE, *, clap_times: tuple[float, ...] = ()) -> Any:
    """Des syllabes (80 à 250 ms, hauteur 90 à 220 Hz, harmoniques sous une bosse de formant) séparées de silences, avec un
    groupe de syllabes par « mot » : un rythme irrégulier qu'une corrélation d'enveloppes sait caler, et dont la parole est
    plausible pour la découpe en sous-titres. ``clap_times`` ajoute un « clap » (bip de 1 kHz, 100 ms) à ces instants."""
    np = _np()
    count = int(round(seconds * rate))
    out = np.zeros(count, dtype=np.float64)
    position = 0.0
    index = 0
    while position < seconds:
        length = 0.08 + 0.17 * unit(seed, index * 8 + 1)
        pitch = 90.0 + 130.0 * unit(seed, index * 8 + 2)
        formant = 400.0 + 1800.0 * unit(seed, index * 8 + 3)
        level = 0.25 + 0.75 * unit(seed, index * 8 + 4)
        word_break = unit(seed, index * 8 + 5) < 0.28
        gap = (0.35 + 0.45 * unit(seed, index * 8 + 6)) if word_break else (0.03 + 0.07 * unit(seed, index * 8 + 6))
        first = int(position * rate)
        n = min(int(length * rate), count - first)
        if n > 8:
            t = np.arange(n, dtype=np.float64) / rate
            harmonics = max(1, int(3200.0 / pitch))
            signal = np.zeros(n, dtype=np.float64)
            for h in range(1, harmonics + 1):
                weight = float(np.exp(-(((h * pitch) - formant) / 650.0) ** 2)) + 0.12 / h
                signal += weight * np.sin(2.0 * np.pi * pitch * h * t)
            signal *= np.hanning(n) * level / max(1.0, harmonics ** 0.5)
            out[first:first + n] += signal
        position += length + gap
        index += 1
    out *= 0.35
    for moment in clap_times:
        add_beep(out, rate, moment, 1000.0, 0.1, 0.8)
    return out


def add_beep(buffer: Any, rate: int, at: float, frequency: float, seconds: float, level: float) -> None:
    """Ajoute un bip (sinus fenêtré) à ``buffer`` à l'instant ``at`` ; sans effet s'il sort du tampon."""
    np = _np()
    first = int(at * rate)
    n = int(seconds * rate)
    if first < 0 or first >= len(buffer):
        return
    n = min(n, len(buffer) - first)
    t = np.arange(n, dtype=np.float64) / rate
    buffer[first:first + n] += level * np.sin(2.0 * np.pi * frequency * t) * np.hanning(n)


def music(seed: int, seconds: float, bpm: float = 120.0, rate: int = MUSIC_RATE) -> Any:
    """Un temps fort net : grosse caisse à chaque temps, caisse claire aux temps 2 et 4, charleston aux contretemps, basse
    sur une mesure de quatre notes. À 120 battements par minute un temps vaut exactement 22 050 échantillons : le tempo
    est détectable et le premier temps tombe à 0 s."""
    np = _np()
    count = int(round(seconds * rate))
    out = np.zeros(count, dtype=np.float64)
    beat = 60.0 / bpm
    bass_notes = (55.0, 55.0, 65.41, 49.0)
    index = 0
    while index * beat < seconds:
        first = int(round(index * beat * rate))
        # Grosse caisse : sinus balayé de 150 à 50 Hz, enveloppe exponentielle.
        n = min(int(0.28 * rate), count - first)
        if n > 0:
            t = np.arange(n, dtype=np.float64) / rate
            phase = 2.0 * np.pi * (50.0 * t - 100.0 * 0.03 * (np.exp(-t / 0.03) - 1.0))
            out[first:first + n] += 0.9 * np.sin(phase) * np.exp(-t / 0.11)
        # Caisse claire aux temps 2 et 4.
        if index % 2 == 1:
            n = min(int(0.2 * rate), count - first)
            if n > 0:
                t = np.arange(n, dtype=np.float64) / rate
                grain = noise(seed, first, n)
                out[first:first + n] += 0.45 * grain * np.exp(-t / 0.07)
        # Charleston au contretemps.
        half = first + int(round(beat * rate / 2.0))
        n = min(int(0.06 * rate), count - half)
        if n > 1:
            t = np.arange(n, dtype=np.float64) / rate
            grain = np.diff(noise(seed + 1, half, n + 1))
            out[half:half + n] += 0.3 * grain * np.exp(-t / 0.015)
        # Basse : un sinus par temps.
        n = min(int(beat * rate * 0.9), count - first)
        if n > 0:
            t = np.arange(n, dtype=np.float64) / rate
            out[first:first + n] += 0.28 * np.sin(2.0 * np.pi * bass_notes[(index // 4) % 4] * t) * np.exp(-t / 0.4)
        index += 1
    return out * 0.55


def pad(seed: int, seconds: float, rate: int = MUSIC_RATE) -> Any:
    """Une nappe lente (accords de sinus à modulation lente) : un fond sonore sans rythme pour le motion design."""
    np = _np()
    count = int(round(seconds * rate))
    t = np.arange(count, dtype=np.float64) / rate
    chords = ((220.0, 277.18, 329.63), (196.0, 246.94, 293.66), (174.61, 220.0, 261.63), (196.0, 246.94, 329.63))
    out = np.zeros(count, dtype=np.float64)
    for position, frequency in enumerate(chords[seed % len(chords)] + chords[(seed + 1) % len(chords)]):
        out += np.sin(2.0 * np.pi * frequency * t + 0.7 * position) * (0.5 + 0.5 * np.sin(2.0 * np.pi * (0.07 + 0.013 * position) * t))
    return out * (0.12 / 3.0)


def mic_noise(seed: int, seconds: float, rate: int = SPEECH_RATE, level: float = 0.012) -> Any:
    """Bruit de prise de son propre à une caméra (indépendant d'une caméra à l'autre)."""
    return noise(seed, 0, int(round(seconds * rate))) * level


def to_int16(signal: Any) -> Any:
    np = _np()
    return np.rint(np.clip(signal, -1.0, 1.0) * 32767.0).astype("<i2")


def write_wav(path: Path, signal: Any, rate: int) -> None:
    """WAV 16 bits mono, en-tête fixe (pas d'horodatage) : mêmes échantillons, mêmes octets."""
    data = to_int16(signal)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(data.tobytes())
