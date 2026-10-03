"""Synchronisation audio : décalages exacts, honnêteté de la confiance, cache, journal, annulation.

Médias entièrement synthétiques (``tests/audio_scenes.py``), décodés par le vrai FFmpeg : la chaîne testée est celle de
l'application (décodage en flux, enveloppe, corrélation normalisée, GCC-PHAT). Règle centrale : **si le statut est
« excellent » ou « bon », le décalage est juste** ; dans tous les autres cas (bruit, musique périodique, sources sans
rapport, silence) on préfère « incertain » ou « échec » à une valeur fausse.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import numpy as np
import pytest

from audio_scenes import (
    SAMPLE_RATE,
    ambient,
    applause,
    band_noise,
    delayed,
    music_loop,
    music_varied,
    speech,
    window,
    write_wav,
)
from core import audio_sync
from core.audio_sync import AudioSyncJob, SyncSource, analyze_sync, status_for
from core.audio_sync_cache import AudioSyncCache, cache_key
from core.diagnostics_log import install_diagnostics, uninstall_diagnostics
from core.multicam_model import SyncStatus
from core.task_queue import CancelToken

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg absent")

SECONDS = 40.0
CONFIDENT = {SyncStatus.EXCELLENT, SyncStatus.GOOD}


@pytest.fixture(scope="module")
def scene() -> np.ndarray:
    return speech(np.random.default_rng(11), SECONDS + 40)


def _pair(tmp_path: Path, first: np.ndarray, second: np.ndarray, **extra) -> tuple[Path, Path]:
    return write_wav(tmp_path / "a.wav", first), write_wav(tmp_path / "b.wav", second, **extra)


def _measure(first: Path, second: Path, **kwargs):
    result = analyze_sync([SyncSource("a", str(first)), SyncSource("b", str(second))], reference="a", **kwargs)
    entry, reference = result.by_key()["b"], result.by_key()["a"]
    gap = None if entry.offset is None or reference.offset is None else entry.offset - reference.offset
    return entry, gap


def _a(scene: np.ndarray) -> np.ndarray:
    return window(scene, 0.0, SECONDS)


# --- Décalages exacts -----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("offset", [2.4, 17.83, -3.1, 0.0, 0.017])
def test_a_known_offset_is_recovered_to_the_millisecond(tmp_path, scene, offset):
    """B contient le même son ``offset`` secondes plus tard dans son enregistrement : B a démarré ``offset`` plus tôt."""
    first, second = _pair(tmp_path, _a(scene), window(delayed(scene, offset), 0.0, SECONDS))
    entry, gap = _measure(first, second)
    assert entry.status is SyncStatus.EXCELLENT and gap is not None
    assert gap == pytest.approx(-offset, abs=0.002)


def test_the_earliest_source_is_placed_at_zero_and_offsets_are_never_negative(tmp_path, scene):
    first, second = _pair(tmp_path, _a(scene), window(delayed(scene, 5.0), 0.0, SECONDS))
    result = analyze_sync([SyncSource("a", str(first)), SyncSource("b", str(second))], reference="a")
    offsets = [item.offset for item in result.sources]
    assert min(offsets) == 0.0 and all(value >= 0.0 for value in offsets)
    assert result.by_key()["a"].offset == pytest.approx(5.0, abs=0.002) and result.by_key()["b"].offset == 0.0


@pytest.mark.parametrize(
    ("noise", "gain", "tolerance"),
    [(0.0, 0.1, 0.002), (0.0, 4.0, 0.002), (0.3, 0.5, 0.01), (0.5, 3.0, 0.01), (0.0, 0.003, 0.002)],
)
def test_volume_differences_and_independent_noise_do_not_move_the_offset(tmp_path, scene, noise, gain, tolerance):
    rng = np.random.default_rng(3)
    second = window(delayed(scene, 2.4), 0.0, SECONDS) * gain + noise * band_noise(rng, int(SECONDS * SAMPLE_RATE), 50, 5000)
    first, other = _pair(tmp_path, _a(scene), second)
    entry, gap = _measure(first, other)
    assert entry.status in CONFIDENT and gap == pytest.approx(-2.4, abs=tolerance)


def test_silence_at_the_start_of_a_source_is_handled(tmp_path, scene):
    silent_head = np.concatenate([np.zeros(7 * SAMPLE_RATE), window(scene, 0.0, SECONDS - 7)])
    first, second = _pair(tmp_path, silent_head, _a(scene))
    entry, gap = _measure(first, second)
    assert entry.status in CONFIDENT and gap == pytest.approx(7.0, abs=0.002)


def test_hard_clipping_keeps_the_offset(tmp_path, scene):
    clipped = np.clip(window(delayed(scene, 4.0), 0.0, SECONDS) * 30, -1, 1)
    first, second = _pair(tmp_path, _a(scene), clipped)
    entry, gap = _measure(first, second)
    assert entry.status in CONFIDENT and gap == pytest.approx(-4.0, abs=0.002)


@pytest.mark.parametrize("kind", ["applause", "music_varied"])
def test_other_kinds_of_sound_synchronise_too(tmp_path, kind):
    rng = np.random.default_rng(21)
    material = {"applause": applause, "music_varied": music_varied}[kind](rng, SECONDS + 20)
    first, second = _pair(tmp_path, window(material, 0.0, SECONDS), window(delayed(material, 5.5), 0.0, SECONDS))
    entry, gap = _measure(first, second)
    assert entry.status in CONFIDENT and gap == pytest.approx(-5.5, abs=0.003)


def test_a_source_left_and_right_channels_with_different_content_still_synchronises(tmp_path, scene):
    other = speech(np.random.default_rng(3), SECONDS)
    stereo = np.stack([window(delayed(scene, 3.0), 0.0, SECONDS), other[: int(SECONDS * SAMPLE_RATE)]], axis=1)
    first, second = _pair(tmp_path, _a(scene), stereo)
    entry, gap = _measure(first, second)
    assert entry.status in CONFIDENT and gap == pytest.approx(-3.0, abs=0.003)


# --- Recouvrement partiel -------------------------------------------------------------------------------------------------


def test_a_camera_that_starts_later_and_ends_earlier_is_placed_inside_the_reference(tmp_path, scene):
    first = window(scene, 0.0, SECONDS + 20)
    second = window(scene, 20.0, 25.0)  # 25 s, démarre 20 s après la référence, finit avant elle
    first_path, second_path = _pair(tmp_path, first, second)
    entry, gap = _measure(first_path, second_path)
    assert entry.status in CONFIDENT and gap == pytest.approx(20.0, abs=0.002)


def test_a_source_that_starts_before_the_reference_gets_the_smallest_offset(tmp_path, scene):
    first, second = _pair(tmp_path, window(scene, 10.0, 25.0), window(scene, 0.0, SECONDS))
    result = analyze_sync([SyncSource("a", str(first)), SyncSource("b", str(second))], reference="a")
    assert result.by_key()["b"].offset == 0.0 and result.by_key()["a"].offset == pytest.approx(10.0, abs=0.002)


# --- Honnêteté : jamais « bon » quand on ne peut pas le dire ----------------------------------------------------------------


@pytest.mark.parametrize(
    "make",
    [
        lambda rng: (speech(rng, SECONDS), speech(np.random.default_rng(99), SECONDS)),
        lambda rng: (ambient(rng, SECONDS), ambient(rng, SECONDS)),
        lambda rng: (applause(rng, SECONDS), applause(rng, SECONDS)),
        lambda rng: (np.zeros(int(SECONDS * SAMPLE_RATE)), np.zeros(int(SECONDS * SAMPLE_RATE))),
    ],
    ids=["parole sans rapport", "ambiance sans rapport", "applaudissements sans rapport", "silence"],
)
def test_unrelated_or_silent_sources_are_never_reported_as_synchronised(tmp_path, make):
    first, second = make(np.random.default_rng(5))
    first_path, second_path = _pair(tmp_path, first, second)
    entry, _gap = _measure(first_path, second_path)
    assert entry.status in {SyncStatus.FAILED, SyncStatus.UNCERTAIN}
    if entry.status is SyncStatus.FAILED:
        assert entry.offset is None


@pytest.mark.parametrize("period", [2.0, 0.8])
def test_periodic_music_is_ambiguous_and_says_so(tmp_path, period):
    loop = music_loop(SECONDS + 20, period)
    first, second = _pair(tmp_path, window(loop, 0.0, SECONDS), window(delayed(loop, 5.5), 0.0, SECONDS))
    entry, _gap = _measure(first, second)
    assert entry.status not in CONFIDENT
    assert "périodique" in entry.detail


def test_a_signal_buried_in_louder_noise_fails_instead_of_guessing(tmp_path, scene):
    rng = np.random.default_rng(3)
    buried = window(delayed(scene, 2.4), 0.0, SECONDS) * 0.05 + 0.3 * band_noise(rng, int(SECONDS * SAMPLE_RATE), 50, 5000)
    first, second = _pair(tmp_path, _a(scene), buried)
    entry, _gap = _measure(first, second)
    assert entry.status not in CONFIDENT


def test_a_confident_status_is_never_wrong_over_many_random_cases(tmp_path):
    """Balayage : bruits, volumes, décalages et types de son variés ; « excellent » / « bon » exige un décalage juste."""
    rng = np.random.default_rng(2024)
    wrong: list[str] = []
    confident = 0
    for index in range(16):
        material = [speech, applause, ambient, music_varied][index % 4](rng, SECONDS + 30)
        offset = float(rng.uniform(-8, 20))
        noise = float(rng.choice([0.0, 0.2, 0.5, 1.0]))
        gain = float(rng.choice([0.05, 0.3, 1.0, 3.0]))
        second = window(delayed(material, offset), 0.0, SECONDS) * gain
        second = second + noise * band_noise(rng, second.size, 50, 5000)
        first, other = _pair(tmp_path, window(material, 0.0, SECONDS), second)
        entry, gap = _measure(first, other)
        if entry.status in CONFIDENT:
            confident += 1
            if gap is None or abs(gap + offset) > 0.005:
                wrong.append(f"cas {index}: attendu {-offset:.3f}, mesuré {gap}, {entry.status.value}, {entry.detail}")
    assert not wrong, "\n".join(wrong)
    assert confident >= 8  # le balayage reste informatif : une majorité de cas est mesurable


# --- Plusieurs sources ------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("count", [4, 8])
def test_many_sources_are_aligned_on_the_longest_one(tmp_path, scene, count):
    rng = np.random.default_rng(count)
    starts = [0.0] + [float(value) for value in rng.uniform(1.0, 20.0, size=count - 1)]
    sources = []
    for index, start in enumerate(starts):
        path = write_wav(tmp_path / f"s{index}.wav", window(scene, start, SECONDS - start / 2))
        sources.append(SyncSource(f"s{index}", str(path)))
    result = analyze_sync(sources)
    found = [item.offset for item in result.sources]
    assert all(value is not None for value in found) and min(found) == 0.0  # type: ignore[type-var]
    for item, start in zip(result.sources, starts):
        assert item.status in CONFIDENT | {SyncStatus.NONE}  # la référence n'est pas mesurée (statut « aucun »)
        assert item.offset == pytest.approx(start, abs=0.003)


def test_a_source_without_an_audio_stream_fails_alone_and_the_others_still_synchronise(tmp_path, scene):
    first, second = _pair(tmp_path, _a(scene), window(delayed(scene, 2.0), 0.0, SECONDS))
    silent_video = tmp_path / "video-only.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=64x64:r=10:d=3", "-an", str(silent_video)],
        check=True,
    )
    result = analyze_sync([SyncSource("a", str(first)), SyncSource("v", str(silent_video)), SyncSource("b", str(second))],
                          reference="a")
    by_key = result.by_key()
    assert by_key["v"].status is SyncStatus.FAILED and by_key["v"].offset is None and by_key["v"].detail
    assert by_key["b"].status in CONFIDENT and by_key["b"].offset == pytest.approx(0.0, abs=0.002)
    assert by_key["a"].offset == pytest.approx(2.0, abs=0.002)


def test_a_missing_file_fails_without_aborting_the_analysis(tmp_path, scene):
    first, second = _pair(tmp_path, _a(scene), window(delayed(scene, 2.0), 0.0, SECONDS))
    result = analyze_sync([SyncSource("a", str(first)), SyncSource("gone", str(tmp_path / "absent.wav")),
                           SyncSource("b", str(second))], reference="a")
    assert result.by_key()["gone"].status is SyncStatus.FAILED and result.by_key()["b"].status in CONFIDENT


def test_the_reference_defaults_to_the_longest_usable_source(tmp_path, scene):
    short = write_wav(tmp_path / "short.wav", window(scene, 5.0, 20.0))
    long = write_wav(tmp_path / "long.wav", window(scene, 0.0, SECONDS + 20))
    result = analyze_sync([SyncSource("short", str(short)), SyncSource("long", str(long))])
    assert result.reference == "long"
    with pytest.raises(ValueError):
        analyze_sync([SyncSource("x", str(short)), SyncSource("x", str(long))])


def test_a_weak_link_to_the_reference_is_chained_through_another_source(tmp_path, scene):
    """C ne se mesure bien qu'avec B (la référence A n'en capte presque rien) : le chaînage retrouve son décalage."""
    a = write_wav(tmp_path / "a.wav", window(scene, 0.0, 30.0))
    b = write_wav(tmp_path / "b.wav", window(scene, 18.0, 40.0))      # chevauche A sur 12 s
    c = write_wav(tmp_path / "c.wav", window(scene, 45.0, 30.0))      # ne chevauche pas A, chevauche B
    result = analyze_sync([SyncSource("a", str(a)), SyncSource("b", str(b)), SyncSource("c", str(c))], reference="a")
    by_key = result.by_key()
    assert by_key["b"].offset == pytest.approx(18.0, abs=0.003)
    assert by_key["c"].status in CONFIDENT and by_key["c"].offset == pytest.approx(45.0, abs=0.003)
    assert by_key["c"].reference_key == "b"


# --- Temps du média, formats ------------------------------------------------------------------------------------------------


def _ffmpeg(*arguments: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *arguments], check=True)


def test_an_audio_stream_that_starts_late_is_placed_in_media_time(tmp_path, scene):
    """Un flux audio qui démarre 0,5 s après la vidéo : le décalage est mesuré dans le temps du média, comme ``atrim``.

    (Seul, un flux audio qui démarre à 0,5 s est ramené à 0 par FFmpeg lui-même, à l'analyse comme à l'export : les deux
    lisent donc le même temps. L'écart ne compte que lorsqu'un autre flux, ici la vidéo, démarre à 0.)
    """
    wav = write_wav(tmp_path / "scene.wav", _a(scene))
    late = tmp_path / "late.mkv"
    _ffmpeg("-f", "lavfi", "-i", "color=c=green:s=64x64:r=10:d=40", "-itsoffset", "0.5", "-i", str(wav),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le", str(late))
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=start_time", "-of", "csv=p=0", str(late)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert float(probe) == pytest.approx(0.5, abs=0.01)
    entry, gap = _measure(wav, late)
    # Le même son arrive 0,5 s plus tard dans le média de ``late`` : il a démarré 0,5 s plus tôt.
    assert entry.status in CONFIDENT and gap == pytest.approx(-0.5, abs=0.003)


def test_a_compressed_audio_only_file_is_aligned_despite_its_encoder_delay(tmp_path, scene):
    wav = write_wav(tmp_path / "scene.wav", _a(scene))
    compressed = tmp_path / "scene.m4a"
    _ffmpeg("-i", str(wav), "-c:a", "aac", "-b:a", "128k", str(compressed))
    entry, gap = _measure(wav, compressed)
    assert entry.status in CONFIDENT and abs(gap or 0.0) < 0.004


def test_a_video_container_with_sound_is_analysed_like_a_wav(tmp_path, scene):
    wav = write_wav(tmp_path / "scene.wav", window(delayed(scene, 3.0), 0.0, SECONDS))
    video = tmp_path / "scene.mp4"
    _ffmpeg("-f", "lavfi", "-i", "color=c=blue:s=64x64:r=10:d=40", "-i", str(wav), "-shortest", "-c:v", "libx264",
            "-pix_fmt", "yuv420p", "-c:a", "aac", str(video))
    first = write_wav(tmp_path / "ref.wav", _a(scene))
    entry, gap = _measure(first, video)
    assert entry.status in CONFIDENT and gap == pytest.approx(-3.0, abs=0.005)


def test_an_analysed_range_shifts_the_reported_offset_with_its_start(tmp_path, scene):
    """``start`` désigne l'instant analysé de la source : le décalage rendu est celui de cet instant."""
    first, second = _pair(tmp_path, _a(scene), window(delayed(scene, 2.0), 0.0, SECONDS))
    result = analyze_sync(
        [SyncSource("a", str(first)), SyncSource("b", str(second), start=10.0, duration=25.0)], reference="a"
    )
    entry, reference = result.by_key()["b"], result.by_key()["a"]
    assert entry.status in CONFIDENT
    # b[start=10] = a[8] : l'instant 10 s de b tombe à 8 s sur la timeline de a.
    assert (entry.offset or 0.0) - (reference.offset or 0.0) == pytest.approx(8.0, abs=0.003)


def test_decoding_streams_in_blocks_without_holding_the_whole_signal(tmp_path):
    """La mémoire de l'analyse ne dépend pas de la durée : 10 minutes de son restent loin des 38 Mo du PCM brut."""
    import tracemalloc

    rng = np.random.default_rng(1)
    long = speech(rng, 600)
    first = write_wav(tmp_path / "a.wav", long)
    second = write_wav(tmp_path / "b.wav", delayed(long, 3.3)[: len(long)])
    tracemalloc.start()
    try:
        result = analyze_sync([SyncSource("a", str(first)), SyncSource("b", str(second))], reference="a")
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert result.by_key()["b"].status in CONFIDENT
    assert result.by_key()["b"].offset == pytest.approx(0.0, abs=0.003)
    assert peak < 40 * 2**20, f"pic mémoire numpy : {peak / 2**20:.1f} Mo"


# --- Cache ------------------------------------------------------------------------------------------------------------------


def test_the_envelope_is_computed_once_then_read_from_the_cache(tmp_path, scene, monkeypatch):
    first, second = _pair(tmp_path, _a(scene), window(delayed(scene, 2.4), 0.0, SECONDS))
    cache = AudioSyncCache(tmp_path / "cache")
    entry, gap = _measure(first, second, cache=cache)
    assert cache.stats()["entries"] == 2 and gap == pytest.approx(-2.4, abs=0.002)

    def fail(*_a, **_k):
        raise AssertionError("l'enveloppe devait venir du cache")

    monkeypatch.setattr(audio_sync, "raw_envelope", fail)
    entry, gap = _measure(first, second, cache=cache)
    assert entry.status in CONFIDENT and gap == pytest.approx(-2.4, abs=0.002)


def test_changing_the_media_the_range_or_the_algorithm_invalidates_the_cache(tmp_path, scene, monkeypatch):
    path = write_wav(tmp_path / "a.wav", _a(scene))
    base = cache_key(str(path), 0.0, None, audio_sync._PARAMS)
    assert cache_key(str(path), 0.0, None, audio_sync._PARAMS) == base
    assert cache_key(str(path), 5.0, None, audio_sync._PARAMS) != base
    assert cache_key(str(path), 0.0, 20.0, audio_sync._PARAMS) != base
    assert cache_key(str(path), 0.0, None, {**audio_sync._PARAMS, "rate": 4000}) != base
    monkeypatch.setattr("core.audio_sync_cache.AUDIO_SYNC_VERSION", 99)
    assert cache_key(str(path), 0.0, None, audio_sync._PARAMS) != base
    monkeypatch.undo()
    write_wav(path, window(scene, 3.0, SECONDS - 3))         # le média change : taille et date aussi
    assert cache_key(str(path), 0.0, None, audio_sync._PARAMS) != base


def test_a_corrupt_cache_entry_is_dropped_logged_and_recomputed(tmp_path, scene, journal):
    first, second = _pair(tmp_path, _a(scene), window(delayed(scene, 2.4), 0.0, SECONDS))
    cache = AudioSyncCache(tmp_path / "cache")
    _measure(first, second, cache=cache)
    victim = sorted(cache.directory.glob("env-*.npy"))[0]
    victim.write_bytes(b"pas un tableau numpy")
    entry, gap = _measure(first, second, cache=cache)
    assert entry.status in CONFIDENT and gap == pytest.approx(-2.4, abs=0.002)
    text = journal()
    assert "Cache de synchronisation" in text and "illisible" in text
    assert victim.exists() and victim.stat().st_size > 100  # recalculée et réécrite


def test_cache_budget_helpers_evict_the_oldest_entries_first(tmp_path):
    cache = AudioSyncCache(tmp_path / "cache")
    for index in range(3):
        cache.store(f"k{index}", np.arange(1000, dtype=np.float32))
        stamp = 1_700_000_000 + index
        import os

        os.utime(cache.directory / f"env-k{index}.npy", (stamp, stamp))
    stats = cache.stats()
    assert stats["entries"] == 3 and stats["bytes"] > 12000
    freed = cache.evict_bytes(1)
    assert freed > 0 and not (cache.directory / "env-k0.npy").exists() and (cache.directory / "env-k2.npy").exists()
    assert cache.load("k1") is not None and cache.load("missing") is None
    cache.purge()
    assert cache.stats() == {"entries": 0, "bytes": 0}


# --- Tâche d'arrière-plan, annulation, journal ----------------------------------------------------------------------------------


def test_the_job_reports_progress_and_the_result(tmp_path, scene):
    first, second = _pair(tmp_path, _a(scene), window(delayed(scene, 2.4), 0.0, SECONDS))
    job = AudioSyncJob((SyncSource("a", str(first)), SyncSource("b", str(second))), reference="a")
    assert job.snapshot().state == "queued"
    job.run()
    snapshot = job.snapshot()
    assert snapshot.state == "finished" and snapshot.progress == 1.0 and snapshot.result is not None
    assert snapshot.result.by_key()["b"].status in CONFIDENT


def test_a_cancelled_job_stops_without_a_result_or_an_error(tmp_path, scene):
    first, second = _pair(tmp_path, _a(scene), window(delayed(scene, 2.4), 0.0, SECONDS))
    job = AudioSyncJob((SyncSource("a", str(first)), SyncSource("b", str(second))))
    token = CancelToken()
    token.cancel()
    job.run(token)
    snapshot = job.snapshot()
    assert snapshot.state == "cancelled" and snapshot.result is not None and snapshot.result.cancelled
    assert all(item.offset is None for item in snapshot.result.sources)
    queued = AudioSyncJob((SyncSource("a", str(first)),))
    queued.cancel()
    queued.run()
    assert queued.snapshot().state == "cancelled" and queued.snapshot().result is None


def test_cancelling_during_the_analysis_kills_ffmpeg_promptly(tmp_path, scene):
    first, second = _pair(tmp_path, _a(scene), window(delayed(scene, 2.4), 0.0, SECONDS))
    calls = {"count": 0}

    def cancelled() -> bool:
        calls["count"] += 1
        return calls["count"] > 3

    result = analyze_sync([SyncSource("a", str(first)), SyncSource("b", str(second))], cancelled=cancelled)
    assert result.cancelled


def test_a_job_that_hits_an_unexpected_error_fails_cleanly(tmp_path, monkeypatch):
    def explode(*_a, **_k):
        raise RuntimeError("panne imprévue")

    monkeypatch.setattr(audio_sync, "analyze_sync", explode)
    job = AudioSyncJob((SyncSource("a", str(tmp_path / "x.wav")),))
    job.run()
    snapshot = job.snapshot()
    assert snapshot.state == "failed" and "panne imprévue" in snapshot.message


@pytest.fixture
def journal(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "excepthook", lambda *_a: None)
    monkeypatch.setattr(threading, "excepthook", lambda _a: None)
    monkeypatch.setattr(sys, "unraisablehook", lambda _a: None)
    path = install_diagnostics(tmp_path / "logs")
    assert path is not None

    def text() -> str:
        for handler in logging.getLogger("kut_studio").handlers:
            handler.flush()
        return Path(path).read_text(encoding="utf-8") if Path(path).exists() else ""

    yield text
    uninstall_diagnostics()


def test_the_analysis_logs_method_offsets_confidence_and_fallbacks_once(tmp_path, scene, journal):
    silent_video = tmp_path / "video-only.mp4"
    _ffmpeg("-f", "lavfi", "-i", "color=c=red:s=64x64:r=10:d=3", "-an", str(silent_video))
    first, second = _pair(tmp_path, _a(scene), window(delayed(scene, 2.4), 0.0, SECONDS))
    analyze_sync([SyncSource("a", str(first)), SyncSource("v", str(silent_video)), SyncSource("b", str(second))],
                 reference="a")
    text = journal()
    assert text.count("Synchronisation audio : 3 source(s)") == 1
    assert "b:" in text and "confiance" in text and "excellent" in text and "référence a" in text
    assert "video-only.mp4 n'a produit aucun son" in text and "v ignorée" in text


# --- Contrôle de démarrage ----------------------------------------------------------------------------------------------------------


def test_status_bands_are_ordered_and_the_self_check_passes():
    assert status_for(1.0) is SyncStatus.EXCELLENT and status_for(0.6) is SyncStatus.GOOD
    assert status_for(0.4) is SyncStatus.UNCERTAIN and status_for(0.1) is SyncStatus.FAILED
    assert audio_sync.self_check() is None
