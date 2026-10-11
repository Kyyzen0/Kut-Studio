"""Mixage en direct : le son de l'export à partir de la tête de lecture, ses vumètres, mesurés sur le vrai FFmpeg.

Le juge est l'export : le mixage pris à une origine doit être, échantillon pour échantillon, celui de l'export à partir
de cet instant (effets, fondus, automation, gains, pan, Master).
"""

from __future__ import annotations

import math
import subprocess

import numpy as np
import pytest
from audio_scenes import write_wav
from render_probe import needs_ffmpeg

from core.audio_automation import AutomationPoint, TrackAutomation
from core.export_engine import ExportEngine, filter_graph_arguments, input_arguments
from core.live_mix import (
    BLOCK_FRAMES,
    PREROLL_SECONDS,
    SAMPLE_RATE,
    MixMeter,
    build_live_mix_command,
    live_mix_plan,
)
from core.project_model import Clip, MediaAsset, Project, Track
from core.render_plan import build_render_plan


def _tone(seconds: float, frequency: float, amplitude: float) -> np.ndarray:
    t = np.arange(int(seconds * SAMPLE_RATE)) / SAMPLE_RATE
    wave = amplitude * np.sin(2 * math.pi * frequency * t)
    return np.stack([wave, wave], axis=1)


def _asset(tmp_path, name, signal) -> MediaAsset:
    path = write_wav(tmp_path / f"{name}.wav", signal, rate=SAMPLE_RATE)     # (l'aide écrit à 16 kHz par défaut)
    return MediaAsset(name, str(path), name, len(signal) / SAMPLE_RATE, 0, 0, 0.0, "audio", has_audio=True)


def _project(tmp_path) -> Project:
    """Musique sur 20 s (fondus, gain, pan, automation) ; voix de 6 à 14 s (fondus) ; un clip fini avant 10 s."""
    music = _asset(tmp_path, "music", _tone(20.0, 220.0, 0.25))
    voice = _asset(tmp_path, "voice", _tone(10.0, 660.0, 0.2))
    early = _asset(tmp_path, "early", _tone(5.0, 330.0, 0.2))
    bed = Clip("bed", "music", "A1", 0.0, 0.0, 20.0, gain_db=-3.0, pan=-0.4, fade_in=1.0, fade_out=2.0)
    talk = Clip("talk", "voice", "A2", 6.0, 1.0, 9.0, fade_in=0.5, fade_out=0.5)
    intro = Clip("intro", "early", "A2", 0.0, 0.0, 5.0)
    music_track = Track("A1", "A1", "audio", clips=[bed])
    music_track.automation = TrackAutomation(points=(AutomationPoint(0.0, 0.0), AutomationPoint(12.0, -12.0),
                                                     AutomationPoint(18.0, -2.0)))
    return Project("p", width=64, height=36, fps=25.0, media_assets=[music, voice, early],
                   tracks=[music_track, Track("A2", "A2", "audio", clips=[intro, talk])])


def _run(command) -> np.ndarray:
    completed = subprocess.run(list(command), capture_output=True, timeout=120)
    assert completed.returncode == 0, completed.stderr.decode("utf-8", "replace")
    return np.frombuffer(completed.stdout, dtype="<f4")


def _export_mix(plan) -> np.ndarray:
    """Le mixage de l'export, du début, en PCM flottant stéréo."""
    graph, _video, audio, inputs = ExportEngine._build_filter_complex(plan, 64, 36, 25.0, None, audio_only=True)
    command = ["ffmpeg", "-v", "error", "-nostdin"]
    for path in inputs:
        command += input_arguments(path)
    command += filter_graph_arguments(graph, []) + ["-map", f"[{audio}]", "-c:a", "pcm_f32le", "-f", "f32le", "-"]
    return _run(command).reshape(-1, 2)


def _live(project, start, **options):
    live = build_live_mix_command(project, start, **options)
    assert live is not None
    frames = _run(live.command).reshape(-1, live.channels)
    return live, frames[live.preroll_frames:]


@needs_ffmpeg
@pytest.mark.parametrize("start", [0.0, 2.0, 9.5, 13.0])
def test_the_live_mix_is_the_export_mix_from_the_playhead(tmp_path, start):
    project = _project(tmp_path)
    reference = _export_mix(build_render_plan(project))[int(start * SAMPLE_RATE):]
    live, frames = _live(project, start)
    heard = frames[:, 0:2]
    count = min(len(heard), len(reference))
    assert count >= int((20.0 - start) * SAMPLE_RATE) - 2
    assert np.abs(heard[:count] - reference[:count]).max() < 1e-4, "fondus, automation, gain, pan : à l'échantillon"
    if start > PREROLL_SECONDS:
        assert live.preroll_frames == int(PREROLL_SECONDS * SAMPLE_RATE)


@needs_ffmpeg
def test_each_track_is_metered_after_its_fader_and_the_tracks_sum_to_the_mix(tmp_path):
    project = _project(tmp_path)
    live, frames = _live(project, 7.0)
    assert live.track_ids == ("A1", "A2") and live.channels == 8
    tracks = frames[:, 4:6] + frames[:, 6:8]
    assert np.abs(tracks - frames[:, 0:2]).max() < 1e-4, "le limiteur n'agit pas ici : la somme des pistes est le mix"
    project.tracks[1].muted = True
    live, frames = _live(project, 7.0)
    assert live.track_ids == ("A1",), "une piste muette n'est ni entendue ni mesurée"


@needs_ffmpeg
def test_the_master_is_heard(tmp_path):
    project = _project(tmp_path)
    _live_cmd, plain = _live(project, 7.0)
    _live_cmd, quieter = _live(project, 7.0, master_gain_db=-6.0)
    ratio = np.abs(quieter[:, 0:2]).max() / np.abs(plain[:, 0:2]).max()
    assert ratio == pytest.approx(10 ** (-6 / 20), rel=1e-3)
    _live_cmd, muted = _live(project, 7.0, master_muted=True)
    assert np.abs(muted[:, 0:2]).max() == 0.0


def test_only_what_remains_is_opened(tmp_path):
    """Un clip fini avant l'origine n'est ni ouvert ni décodé : le coût ne dépend plus de la position."""
    project = _project(tmp_path)
    plan, origin = live_mix_plan(project, 12.0)
    assert origin == pytest.approx(12.0 - PREROLL_SECONDS)
    assert {layer.clip_id for layer in plan.audio_layers} == {"bed", "talk"} and not plan.video_layers
    live = build_live_mix_command(project, 12.0)
    assert not any("early.wav" in part for part in live.command)
    assert build_live_mix_command(project, 20.0) is None, "plus rien à jouer"


@needs_ffmpeg
def test_loudness_reads_the_ebu_reference_tone(tmp_path):
    """EBU Tech 3341, cas 1 : une sinusoïde de 1 kHz à −23 dBFS sur les deux canaux mesure −23,0 LUFS."""
    tone = _asset(tmp_path, "tone", _tone(8.0, 1000.0, 10 ** (-23 / 20)))
    project = Project("p", width=64, height=36, fps=25.0, media_assets=[tone],
                      tracks=[Track("A1", "A1", "audio", clips=[Clip("t", "tone", "A1", 0.0, 0.0, 8.0)])])
    live = build_live_mix_command(project, 5.0)
    frames = _run(live.command).reshape(-1, live.channels)
    meter = MixMeter(live.channels, live.start)
    meter.feed(frames[:live.preroll_frames], played=False)
    readings = meter.feed(frames[live.preroll_frames:])
    assert readings and readings[0].time == pytest.approx(5.0 + BLOCK_FRAMES / SAMPLE_RATE)
    middle = readings[len(readings) // 2]
    assert middle.momentary_lufs == pytest.approx(-23.0, abs=0.1)
    assert middle.short_term_lufs == pytest.approx(-23.0, abs=0.1)
    assert middle.peak_db == pytest.approx(-23.0, abs=0.05)
    assert middle.rms_db == pytest.approx(-26.01, abs=0.05)
    assert middle.track_peaks_db == (pytest.approx(-23.0, abs=0.05),)


def test_the_meter_waits_for_whole_blocks_and_starts_from_the_preroll():
    meter = MixMeter(4, 10.0)
    silence = np.zeros((BLOCK_FRAMES * 3 // 2, 4), dtype=np.float32)
    assert meter.feed(silence, played=False) == []
    loud = np.full((BLOCK_FRAMES, 4), 0.5, dtype=np.float32)
    (first,) = meter.feed(loud)
    assert first.time == pytest.approx(10.0 + BLOCK_FRAMES / SAMPLE_RATE)
    assert first.peak_db == pytest.approx(-6.02, abs=0.01) and first.track_peaks_db == ()
