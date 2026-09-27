"""Tests de l'enregistrement audio avec source simulée.

L'enregistrement doit être vérifiable **sans matériel**. L'enregistreur
accepte une source injectable : ici une source tonale synthétise un
signal réel au sens du format. On vérifie que la prise obtenue est
exploitable par le mixeur : durée cohérente, piste armée, clip audio
valide.
"""

import wave

import pytest

from core.audio_mixer import mix_at
from core.audio_recorder import (
    AudioRecorder,
    AudioRecorderError,
    ToneSource,
    pcm_duration,
    write_wav,
)
from core.project_model import Clip, MediaAsset, Project, Track


# ---------------------------------------------------------------------------
# Source simulable
# ---------------------------------------------------------------------------


def test_tone_source_produces_expected_duration():
    recorder = AudioRecorder(source=ToneSource(sample_rate=8000, block_frames=800))
    recorder.start()
    for _ in range(10):
        recorder._pull()
    pcm, rate, channels = recorder.stop()
    assert rate == 8000
    assert channels == 1
    assert pcm_duration(pcm, rate, channels) == pytest.approx(10 * 800 / 8000, rel=0.2)


def test_tone_source_respects_injected_rate():
    """Le WAV produit doit porter le bon taux, pas celui par défaut."""
    recorder = AudioRecorder(source=ToneSource(sample_rate=16000))
    recorder.start()
    pcm, rate, _channels = recorder.stop()
    assert rate == 16000
    assert pcm_duration(pcm, rate, 1) == pytest.approx(0.0, abs=0.2)


def test_recorder_reports_running_state():
    recorder = AudioRecorder(source=ToneSource())
    assert recorder.is_recording is False
    recorder.start()
    assert recorder.is_recording is True
    recorder.stop()
    assert recorder.is_recording is False


def test_start_twice_is_a_noop():
    recorder = AudioRecorder(source=ToneSource())
    recorder.start()
    reader = recorder._io
    recorder.start()
    assert recorder._io is reader
    recorder.stop()


def test_stop_without_start_returns_empty():
    recorder = AudioRecorder(source=ToneSource())
    pcm, rate, channels = recorder.stop()
    assert pcm == b""
    assert rate > 0
    assert channels == 1


def test_stop_on_failing_source_is_survivable():
    class _Failing(ToneSource):
        def stop(self) -> None:
            raise RuntimeError("déconnexion")

    recorder = AudioRecorder(source=_Failing())
    recorder.start()
    pcm, _rate, _channels = recorder.stop()   # ne doit pas lever
    assert isinstance(pcm, bytes)
    assert recorder.is_recording is False


def test_missing_source_raises_recorder_error():
    class _NoReader:
        sample_rate = 8000
        channels = 1

        def start(self):
            return None

        def stop(self) -> None:
            pass

    recorder = AudioRecorder(source=_NoReader())
    with pytest.raises(AudioRecorderError):
        recorder.start()
    assert recorder.is_recording is False


# ---------------------------------------------------------------------------
# WAV
# ---------------------------------------------------------------------------


def test_write_wav_produces_readable_file(tmp_path):
    recorder = AudioRecorder(source=ToneSource(sample_rate=8000, block_frames=400))
    recorder.start()
    for _ in range(5):
        recorder._pull()
    pcm, rate, channels = recorder.stop()
    path = tmp_path / "prise.wav"
    write_wav(str(path), pcm, rate, channels)
    with wave.open(str(path)) as handle:
        assert handle.getnchannels() == channels
        assert handle.getframerate() == rate
        assert handle.getnframes() > 0


def test_write_wav_creates_parent_directory(tmp_path):
    path = tmp_path / "sous" / "dossier" / "prise.wav"
    write_wav(str(path), b"\x00\x01" * 100, 8000, 1)
    assert path.exists()


def test_pcm_duration_of_empty_buffer():
    assert pcm_duration(b"", 48000, 1) == 0.0
    assert pcm_duration(b"abc", 0, 1) == 0.0


# ---------------------------------------------------------------------------
# Prise placée dans un projet
# ---------------------------------------------------------------------------


def test_recording_take_can_be_placed_on_armed_track(tmp_path):
    """Une prise audio compatible avec le mixeur, sans matériel."""
    recorder = AudioRecorder(source=ToneSource(sample_rate=8000, block_frames=800))
    recorder.start()
    for _ in range(20):
        recorder._pull()
    pcm, rate, channels = recorder.stop()
    path = tmp_path / "prise.wav"
    write_wav(str(path), pcm, rate, channels)
    duration = pcm_duration(pcm, rate, channels)
    assert duration > 0.0

    asset = MediaAsset(
        id="rec",
        path=str(path),
        name="Prise 1",
        duration=duration,
        width=0,
        height=0,
        fps=0.0,
        media_type="audio",
        has_audio=True,
    )
    track = Track(id="A1", name="A1", type="audio", armed=True)
    clip = Clip(
        id="take1",
        asset_id="rec",
        track_id="A1",
        timeline_start=0.0,
        source_in=0.0,
        source_out=duration,
    )
    track.clips.append(clip)
    project = Project(name="prise", media_assets=[asset], tracks=[track])

    # La piste armée est audible et la prise entre dans le mixage.
    spec = mix_at(project, duration / 2.0)
    assert spec.is_silent is False
    assert spec.entries[0].clip_id == "take1"
    # La prise est compatible avec les réglages du mixeur.
    clip.set_fade_in(min(0.2, duration / 2))
    assert clip.fade_in > 0.0
    clip.gain_db = -6.0
    assert spec.entries[0].gain_db == 0.0
    assert mix_at(project, duration / 2.0).entries[0].gain_db == pytest.approx(-6.0)


def test_armed_track_is_required_for_recording():
    """Sans piste armée, aucune prise n'a de piste d'accueil."""
    project = Project(name="vide")
    project.tracks.append(Track(id="A1", name="A1", type="audio", armed=False))
    assert not any(t.armed for t in project.tracks if t.type == "audio")
