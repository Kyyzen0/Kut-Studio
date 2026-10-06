"""Bibliothèque de SFX synthétisés, libres de droits : whoosh, impact, riser, passage de moteur, montée en régime…

Portée des sons de l'edit F1 (numpy, 48 kHz stéréo) : chaque son est **calculé**, jamais téléchargé, et chacun a sa
propre graine : le même identifiant donne toujours, au bit près, le même fichier. Un WAV effacé de la bibliothèque est
donc régénéré à l'identique et le projet qui l'utilise le retrouve à son chemin.

Chaque SFX porte une **ancre** : l'instant du son qui doit tomber sur le cut (le centre d'un whoosh, le début d'un
impact, la fin d'un riser, le point le plus proche d'un passage de voiture). Le placement sur les cuts
(:mod:`core.sfx_placement`) s'en sert.

Analyse et synthèse seulement : rien ici n'est sur le chemin de l'export (numpy n'y entre pas).
"""

from __future__ import annotations

import wave
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .platform_paths import user_data_dir

SAMPLE_RATE = 48000
SYNTH_VERSION = 1
"""À incrémenter si un son change : le nom des fichiers en dépend, aucun ancien WAV n'est resservi."""

LIBRARY_DIR_NAME = "sfx_library"


@dataclass(frozen=True)
class SfxSpec:
    """Un son de la bibliothèque.

    Attributes:
        id: identifiant stable (clé de traduction ``sfx.name.<id>``, nom du fichier).
        category: ``whoosh``, ``impact``, ``riser``, ``engine`` ou ``music``.
        anchor: instant du son qui tombe sur le cut (secondes depuis son début).
        gain_db: gain conseillé sur une piste SFX (les sons sont normalisés à −1 dBFS de crête).
    """

    id: str
    category: str
    anchor: float
    gain_db: float = -6.0


def _np() -> Any:
    import numpy as np

    return np


# --- outils -------------------------------------------------------------------------------------------------


def _t(duration: float):
    np = _np()
    return np.arange(int(duration * SAMPLE_RATE)) / SAMPLE_RATE


def _band(signal, low=None, high=None, order: int = 4):
    """Filtre passe-bande à réponse de Butterworth (en module), sans déphasage : par FFT, zéro-padding ×2."""
    np = _np()
    n = len(signal)
    spectrum = np.fft.rfft(signal, n=2 * n)
    freq = np.fft.rfftfreq(2 * n, 1 / SAMPLE_RATE)
    gain = np.ones_like(freq)
    if high:
        gain *= 1 / np.sqrt(1 + (freq / high) ** (2 * order))
    if low:
        gain *= 1 / np.sqrt(1 + (low / np.maximum(freq, 1e-3)) ** (2 * order))
    return np.fft.irfft(spectrum * gain)[:n]


def _stereo(mono, pan=0.0):
    """Panoramique à puissance constante (``pan`` : nombre ou tableau, −1 gauche, 1 droite)."""
    np = _np()
    pan = np.asarray(pan, dtype=float)
    left = np.cos((pan + 1) * np.pi / 4) * np.sqrt(2)
    right = np.sin((pan + 1) * np.pi / 4) * np.sqrt(2)
    return np.vstack([mono * left, mono * right])


# --- sons -------------------------------------------------------------------------------------------------------


def whoosh(duration: float, *, reverse: bool = False, seed: int = 11):
    """Souffle filtré dont la bande monte puis redescend, panoramique de gauche à droite."""
    np = _np()
    rng = np.random.default_rng(seed)
    t = _t(duration)
    noise = rng.normal(0, 1, len(t))
    u = t / duration
    centre = np.log2(400) + (np.log2(5000) - np.log2(400)) * np.sin(np.pi * u)
    out = np.zeros_like(t)
    for band in np.arange(np.log2(250), np.log2(9000), 0.5):
        out += _band(noise, 2 ** (band - 0.25), 2 ** (band + 0.25), 2) * np.exp(-((band - centre) ** 2) / 0.5)
    pan = np.linspace(-0.8, 0.8, len(t))
    return _stereo(out * np.sin(np.pi * u) ** 2, -pan if reverse else pan)


def impact(*, seed: int = 21):
    """Boum grave qui plonge (30 Hz) et éclat de cymbale, saturé doucement."""
    np = _np()
    rng = np.random.default_rng(seed)
    t = _t(2.0)
    freq = 30 + 45 * np.exp(-t / 0.15)
    boom = np.sin(2 * np.pi * np.cumsum(freq) / SAMPLE_RATE) * np.exp(-t / 0.55)
    crash = _band(rng.normal(0, 1, len(t)), 200, 7000, 2) * np.exp(-t / 0.45) * 0.5
    return _stereo(np.tanh(1.8 * (boom + crash)))


def riser(duration: float, *, seed: int = 31):
    """Montée : une bande de bruit qui grimpe de 300 Hz à 9 kHz, une sinusoïde qui monte de deux octaves."""
    np = _np()
    rng = np.random.default_rng(seed)
    t = _t(duration)
    noise = rng.normal(0, 1, len(t))
    out = np.zeros_like(t)
    centre = np.log2(300) + (np.log2(9000) - np.log2(300)) * (t / duration) ** 1.6
    for band in np.arange(np.log2(200), np.log2(12000), 0.5):
        out += _band(noise, 2 ** (band - 0.25), 2 ** (band + 0.25), 2) * np.exp(-((band - centre) ** 2) / 0.6)
    freq = 180 * 4 ** (t / duration)
    out += 0.25 * np.sin(2 * np.pi * np.cumsum(freq) / SAMPLE_RATE) * (t / duration)
    return _stereo(out * (t / duration) ** 2)


def reverse_cymbal(duration: float, *, seed: int = 41):
    np = _np()
    rng = np.random.default_rng(seed)
    t = _t(duration)
    return _stereo((_band(rng.normal(0, 1, len(t)), 3000, 14000, 2) * np.exp(-t / (duration * 0.35)))[::-1])


def _engine_tone(freq, rng, rough: float = 0.25):
    """Moteur : 13 harmoniques d'un fondamental variable, gigue lente d'amplitude, souffle d'admission, saturé."""
    np = _np()
    phase = 2 * np.pi * np.cumsum(freq) / SAMPLE_RATE
    out = np.zeros_like(phase)
    for k in range(1, 14):
        out += np.sin(k * phase + rng.uniform(0, 6.28)) / k ** 0.75
    jitter = _band(rng.normal(0, 1, len(phase)), None, 120, 2)
    out *= 1 + rough * jitter / (np.std(jitter) + 1e-9)
    out += 0.35 * _band(rng.normal(0, 1, len(phase)), 800, 5000, 2)
    return np.tanh(1.8 * out / np.std(out))


PASSBY_DURATION = 1.8
PASSBY_CLOSEST = 0.42
"""Le point le plus proche (et le pic du son) tombe à 42 % de la durée : 0,756 s."""


def passby(f_source: float, speed: float, shift_at: float | None, direction: int, *, seed: int = 51):
    """Passage d'une F1 : effet Doppler (343 m/s), atténuation avec la distance, panoramique qui traverse."""
    np = _np()
    rng = np.random.default_rng(seed)
    duration = PASSBY_DURATION
    t = _t(duration)
    x = speed * (t - duration * PASSBY_CLOSEST)
    distance = np.sqrt(x ** 2 + 7.0 ** 2)
    radial = speed * x / distance
    source = np.full_like(t, f_source) * (1 + 0.08 * (t / duration))
    if shift_at is not None:                       # passage de rapport : le régime chute puis remonte
        k = t > shift_at
        source[k] *= 0.82 + 0.18 * np.minimum(1, (t[k] - shift_at) / 0.35)
    signal = _band(_engine_tone(source * 343 / (343 + radial), rng) * (7.0 / distance) ** 1.2, 60, 7500, 2)
    fade = np.minimum(1, t / 0.05) * np.minimum(1, (duration - t) / 0.1)
    return _stereo(signal * fade, np.clip(direction * x / distance, -0.95, 0.95))


def rev(duration: float, f0: float, f1: float, shifts: tuple[float, ...], *, seed: int = 61):
    """Montée en régime à bord : chaque rapport grimpe, le passage coupe brièvement l'admission."""
    np = _np()
    rng = np.random.default_rng(seed)
    t = _t(duration)
    freq = np.zeros_like(t)
    start, low = 0.0, f0
    for bound in [*shifts, duration]:
        k = (t >= start) & (t < bound)
        u = (t[k] - start) / max(1e-3, bound - start)
        freq[k] = low + (f1 - low) * u ** 0.8
        start, low = bound, f1 * 0.72
    signal = _engine_tone(freq, rng, 0.18)
    envelope = np.minimum(1, t / 0.03) * np.minimum(1, (duration - t) / 0.12)
    for bound in shifts:
        envelope *= 1 - 0.75 * np.exp(-((t - bound) / 0.018) ** 2)
    return _stereo(_band(signal * envelope, 70, 8000, 2))


def beat_bed(bpm: float = 120.0, bars: int = 8, *, seed: int = 71):
    """Lit rythmique « phonk » : grosse caisse et 808 (1, et du 2), claps (2, 4), charleston en croches, cloche.

    Pour essayer un template ou la détection de tempo sans musique sous droits : la grille est exacte (premier temps
    à 0)."""
    np = _np()
    rng = np.random.default_rng(seed)
    beat = 60.0 / float(bpm)
    length = bars * 4 * beat
    out = np.zeros(int(length * SAMPLE_RATE) + SAMPLE_RATE)
    t_kick = _t(0.45)
    kick = np.tanh(2.2 * np.sin(2 * np.pi * np.cumsum(48 + 120 * np.exp(-t_kick / 0.035)) / SAMPLE_RATE)
                   * np.exp(-t_kick / 0.22))
    t_clap = _t(0.3)
    clap_env = sum((t_clap >= d) * np.exp(-(t_clap - d).clip(0) / 0.008) * 0.8 for d in (0.0, 0.011, 0.022))
    clap_env = clap_env + (t_clap >= 0.03) * np.exp(-(t_clap - 0.03).clip(0) / 0.09)
    clap = _band(rng.normal(0, 1, len(t_clap)) * clap_env, 900, 6000, 2)
    t_hat = _t(0.06)
    hat = _band(rng.normal(0, 1, len(t_hat)), 7000, None, 3) * np.exp(-t_hat / 0.014)
    t_bell = _t(0.32)
    roots = (73.42, 58.27, 49.0, 55.0)
    melody = (0, 3, 7, 3, 5, 3, 0, -2)

    def put(sound, at, gain):
        index = int(round(at * SAMPLE_RATE))
        out[index:index + len(sound)] += gain * sound[: max(0, len(out) - index)]

    for bar in range(bars):
        start = bar * 4 * beat
        root = roots[bar % 4]
        for step in range(16):
            at = start + step * beat / 4
            if step in (0, 6):
                put(kick, at, 0.9)
                t_808 = _t(beat * (1.5 if step == 0 else 2.5))
                bass = np.tanh(3.0 * np.sin(2 * np.pi * np.cumsum(root * (1 + 0.9 * np.exp(-t_808 / 0.025)))
                                            / SAMPLE_RATE) * np.exp(-t_808 / 0.9)) * 0.5
                put(bass, at, 1.0)
            if step in (4, 12):
                put(clap, at, 0.55)
            if step % 2 == 0:
                put(hat, at, 0.22)
            if step % 2 == 0:
                semi = melody[(bar * 8 + step // 2) % len(melody)]
                f = 587.33 * 2 ** (semi / 12)
                phase = 2 * np.pi * f * t_bell
                bell = sum(np.sin(k * phase) / k for k in (1, 3, 5)) * np.exp(-t_bell / 0.075)
                put(np.tanh(1.6 * bell), at, 0.25)
    out = out[: int(length * SAMPLE_RATE)]
    fade = np.minimum(1, (length - _t(length)) / 0.05)
    return _stereo(np.tanh(1.4 * out / (np.max(np.abs(out)) or 1.0)) * fade)


# --- catalogue ---------------------------------------------------------------------------------------------------

_PASSBYS = ((610, 85, None, 1), (560, 75, 1.1, -1), (650, 90, None, -1), (590, 80, 0.9, 1))

CATALOG: tuple[tuple[SfxSpec, Callable[[], Any]], ...] = (
    (SfxSpec("whoosh_short", "whoosh", 0.2, -9.0), lambda: whoosh(0.4, seed=12)),
    (SfxSpec("whoosh", "whoosh", 0.25, -9.0), lambda: whoosh(0.5, seed=11)),
    (SfxSpec("whoosh_long", "whoosh", 0.35, -8.0), lambda: whoosh(0.7, reverse=True, seed=13)),
    (SfxSpec("impact", "impact", 0.0, -5.0), lambda: impact(seed=21)),
    (SfxSpec("riser_1s", "riser", 1.0, -6.0), lambda: riser(1.0, seed=31)),
    (SfxSpec("riser_2s", "riser", 2.0, -6.0), lambda: riser(2.0, seed=32)),
    (SfxSpec("reverse_cymbal", "riser", 1.2, -8.0), lambda: reverse_cymbal(1.2, seed=41)),
    *(
        (SfxSpec(f"passby_{n}", "engine", PASSBY_DURATION * PASSBY_CLOSEST, -4.0),
         (lambda spec=spec, n=n: passby(*spec, seed=50 + n)))
        for n, spec in enumerate(_PASSBYS, 1)
    ),
    (SfxSpec("rev_1", "engine", 0.05, -7.0), lambda: rev(1.4, 260, 700, (0.45, 0.95), seed=61)),
    (SfxSpec("rev_2", "engine", 0.05, -7.0), lambda: rev(1.0, 320, 760, (0.5,), seed=62)),
    (SfxSpec("beat_bed_120", "music", 0.0, -3.0), lambda: beat_bed(120.0, 8, seed=71)),
)
"""Sons de la bibliothèque, dans l'ordre d'affichage."""

SFX_SPECS: dict[str, SfxSpec] = {spec.id: spec for spec, _render in CATALOG}


def render_sfx(sfx_id: str):
    """Signal stéréo (2, N), normalisé à −1 dBFS de crête."""
    np = _np()
    for spec, render in CATALOG:
        if spec.id == sfx_id:
            signal = np.asarray(render(), dtype=np.float64)
            peak = float(np.max(np.abs(signal))) or 1.0
            return signal / peak * 10 ** (-1.0 / 20)
    raise KeyError(f"SFX inconnu : {sfx_id!r}.")


def write_wav(path: Path, stereo) -> Path:
    """WAV 16 bits stéréo à 48 kHz, écrit de façon atomique."""
    np = _np()
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = (np.clip(np.asarray(stereo).T, -1, 1) * 32767).astype("<i2")
    temporary = path.with_name(f".{path.name}.tmp")
    with wave.open(str(temporary), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(pcm.tobytes())
    temporary.replace(path)
    return path


def library_dir() -> Path:
    return user_data_dir() / LIBRARY_DIR_NAME


def sfx_path(sfx_id: str, directory: Path | None = None) -> Path:
    return (directory or library_dir()) / f"{sfx_id}-v{SYNTH_VERSION}.wav"


def ensure_sfx_file(sfx_id: str, directory: Path | None = None) -> Path:
    """Chemin du WAV de ``sfx_id``, synthétisé s'il manque (identique au bit près d'une fois sur l'autre)."""
    path = sfx_path(sfx_id, directory)
    if path.is_file() and path.stat().st_size > 44:
        return path
    return write_wav(path, render_sfx(sfx_id))


def sfx_duration(sfx_id: str) -> float:
    """Durée exacte du son (secondes), sans le synthétiser s'il est déjà en bibliothèque."""
    path = ensure_sfx_file(sfx_id)
    with wave.open(str(path), "rb") as handle:
        return handle.getnframes() / float(handle.getframerate())


__all__ = [
    "CATALOG", "SAMPLE_RATE", "SFX_SPECS", "SYNTH_VERSION", "SfxSpec", "beat_bed", "ensure_sfx_file",
    "library_dir", "render_sfx", "sfx_duration", "sfx_path", "write_wav",
]
