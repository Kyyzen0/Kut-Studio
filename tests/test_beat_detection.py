"""Détection du tempo : boucles de batterie synthétiques à tempo et calage connus."""

from __future__ import annotations

import time

import numpy as np
import pytest

from core.beat_detection import (
    ANALYSIS_RATE,
    TempoDetectionJob,
    TempoDetectionSnapshot,
    detect_tempo,
    estimate_tempo,
    plays_at_media_speed,
)
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


def write_wav(path, signal: np.ndarray) -> None:
    """Écrit ``signal`` (mono, 8 kHz, dans [-1, 1]) en WAV 16 bits."""
    import wave

    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(RATE)
        handle.writeframes((signal * 32767).astype("<i2").tobytes())


@needs_ffmpeg
def test_detection_reads_a_real_file_through_ffmpeg(tmp_path):
    import subprocess

    raw = tmp_path / "loop.wav"
    write_wav(raw, drum_loop(120.0, 0.25, 20.0))
    music = tmp_path / "loop.m4a"                       # un vrai format compressé, rééchantillonné à la lecture
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(raw), "-ar", "48000", "-c:a", "aac", "-b:a", "128k",
                    str(music)], check=True, timeout=60)
    estimate = detect_tempo(str(music), start=2.0)
    assert estimate.bpm == pytest.approx(120.0, abs=0.5)
    phase = (estimate.downbeat - 0.25) % 2.0
    assert min(phase, 2.0 - phase) <= 0.03
    grid = estimate.grid_for_clip(timeline_start=10.0, source_in=2.0)
    assert grid.bpm == pytest.approx(120.0, abs=0.5)


# --- Mesure sur la file d'analyse --------------------------------------------------------------------------------------


def test_a_tempo_job_needs_a_duration():
    with pytest.raises(ValueError):
        TempoDetectionJob("music.wav", start=0.0, duration=0.0)


def test_a_tempo_job_cancelled_before_it_runs_does_not_decode(tmp_path):
    job = TempoDetectionJob(str(tmp_path / "absent.wav"), start=0.0, duration=5.0)
    job.cancel()
    job.run()
    assert job.snapshot() == TempoDetectionSnapshot("cancelled", "", None)


@needs_ffmpeg
def test_a_tempo_job_measures_a_real_file(tmp_path):
    music = tmp_path / "loop.wav"
    write_wav(music, drum_loop(120.0, 0.25, 12.0))
    job = TempoDetectionJob(str(music), start=1.0, duration=10.0, session_id="s")
    assert job.snapshot().state == "queued"
    job.run()
    snapshot = job.snapshot()
    assert snapshot.state == "finished" and snapshot.result is not None
    assert snapshot.result.bpm == pytest.approx(120.0, abs=0.5)
    phase = (snapshot.result.downbeat - 0.25) % 2.0              # en temps du média, malgré le départ à 1 s
    assert min(phase, 2.0 - phase) <= 0.03


@needs_ffmpeg
def test_a_tempo_job_reports_an_unreadable_file(tmp_path):
    broken = tmp_path / "broken.wav"
    broken.write_bytes(b"pas du son")
    job = TempoDetectionJob(str(broken), start=0.0, duration=5.0)
    job.run()
    snapshot = job.snapshot()
    assert snapshot.state == "failed" and snapshot.message and snapshot.result is None


def test_only_a_clip_played_at_media_speed_can_carry_a_measured_grid():
    from core.project_model import Clip

    clip = Clip(id="m", asset_id="a", track_id="A1", timeline_start=1.0, source_in=0.0, source_out=8.0)
    assert plays_at_media_speed(clip)
    clip.sequence_id = "seq"                                     # imbriqué : ses temps ne sont pas ceux du média
    assert not plays_at_media_speed(clip)
