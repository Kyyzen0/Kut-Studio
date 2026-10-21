"""Égaliseur paramétrique à 4 bandes, rendu par le vrai FFmpeg : chaque bande applique le gain réglé, à sa fréquence."""

from __future__ import annotations

import math
import subprocess

import numpy as np
import pytest
from audio_scenes import write_wav
from render_probe import needs_ffmpeg

from core.audio_effects_model import AudioEffectType, create_audio_effect, parameter_specs
from core.export_engine import ExportEngine, _build_clip_audio_effect_filters, filter_graph_arguments, input_arguments
from core.project_model import Clip, MediaAsset, Project, Track
from core.render_plan import build_render_plan

RATE = 48000
FLAT = {"low_gain_db": 0.0, "mid1_gain_db": 0.0, "mid2_gain_db": 0.0, "high_gain_db": 0.0}


def _eq(**params):
    return create_audio_effect(AudioEffectType.PARAMETRIC_EQ, params={**FLAT, **params})


def _gain_db(tmp_path, frequency: float, effect, *, as_insert: bool = False) -> float:
    """Gain mesuré (dB) d'un sinus de ``frequency`` Hz traversant ``effect``, au milieu du signal (hors transitoire)."""
    seconds = 2.0
    tone = 0.1 * np.sin(2 * math.pi * frequency * np.arange(int(seconds * RATE)) / RATE)
    path = write_wav(tmp_path / f"tone_{frequency:.0f}.wav", np.stack([tone, tone], axis=1), rate=RATE)
    asset = MediaAsset("a", str(path), "a", seconds, 0, 0, 0.0, "audio", has_audio=True)
    clip = Clip("c", "a", "A1", 0.0, 0.0, seconds)
    project = Project("p", width=64, height=36, fps=25.0, media_assets=[asset],
                      tracks=[Track("A1", "A1", "audio", clips=[clip])])
    if as_insert:
        project.tracks[0].inserts = [effect]
    else:
        clip.audio_effects = [effect]
    graph, _video, audio, inputs = ExportEngine._build_filter_complex(
        build_render_plan(project), 64, 36, 25.0, None, audio_only=True)
    command = ["ffmpeg", "-v", "error", "-nostdin"]
    for source in inputs:
        command += input_arguments(source)
    command += filter_graph_arguments(graph, []) + ["-map", f"[{audio}]", "-c:a", "pcm_f32le", "-f", "f32le", "-"]
    completed = subprocess.run(command, capture_output=True, timeout=120)
    assert completed.returncode == 0, completed.stderr.decode("utf-8", "replace")
    out = np.frombuffer(completed.stdout, dtype="<f4").reshape(-1, 2)[:, 0]
    middle = slice(RATE // 2, RATE + RATE // 2)
    return 20.0 * math.log10(float(np.sqrt(np.mean(out[middle] ** 2))) / float(np.sqrt(np.mean(tone[middle] ** 2))))


# --- Forme du filtre (sans FFmpeg) ---------------------------------------------------------------------------------


def test_a_flat_equalizer_emits_no_filter_and_each_moved_band_emits_one():
    assert _build_clip_audio_effect_filters((_eq(),)) == []
    filters = _build_clip_audio_effect_filters(
        (_eq(low_gain_db=3.0, mid1_gain_db=-4.0, mid2_gain_db=5.0, high_gain_db=-6.0),))
    assert [f.split("=")[0] for f in filters] == ["bass", "equalizer", "equalizer", "treble"]
    assert len(_build_clip_audio_effect_filters((_eq(mid2_gain_db=2.0, mid2_hz=1234.0, mid2_q=2.5),))) == 1


def test_every_band_is_declared_and_the_default_equalizer_is_audible():
    names = [spec.name for spec in parameter_specs(AudioEffectType.PARAMETRIC_EQ)]
    assert names == ["low_gain_db", "low_hz", "mid1_gain_db", "mid1_hz", "mid1_q",
                     "mid2_gain_db", "mid2_hz", "mid2_q", "high_gain_db", "high_hz"]
    assert _build_clip_audio_effect_filters((create_audio_effect(AudioEffectType.PARAMETRIC_EQ),))


# --- Vrai FFmpeg ------------------------------------------------------------------------------------------------------


@needs_ffmpeg
@pytest.mark.parametrize("gain", [-12.0, 12.0])
def test_the_bell_bands_apply_their_gain_at_their_frequency_and_leave_distant_tones_alone(tmp_path, gain):
    for band, centre, far in (("mid1", 400.0, 3000.0), ("mid2", 3000.0, 200.0)):
        effect = _eq(**{f"{band}_gain_db": gain, f"{band}_hz": centre, f"{band}_q": 4.0})
        assert _gain_db(tmp_path, centre, effect) == pytest.approx(gain, abs=1.0), band
        assert abs(_gain_db(tmp_path, far, effect)) < 1.0, f"{band} ne doit pas toucher {far} Hz"


@needs_ffmpeg
def test_the_q_factor_narrows_the_bell(tmp_path):
    wide = _eq(mid1_gain_db=12.0, mid1_hz=1000.0, mid1_q=0.5)
    narrow = _eq(mid1_gain_db=12.0, mid1_hz=1000.0, mid1_q=10.0)
    assert _gain_db(tmp_path, 1500.0, wide) > _gain_db(tmp_path, 1500.0, narrow) + 3.0


@needs_ffmpeg
@pytest.mark.parametrize("gain", [-12.0, 12.0])
def test_the_shelves_move_what_is_beyond_their_corner(tmp_path, gain):
    low = _eq(low_gain_db=gain, low_hz=300.0)
    assert _gain_db(tmp_path, 40.0, low) == pytest.approx(gain, abs=1.5)
    assert abs(_gain_db(tmp_path, 4000.0, low)) < 1.0
    high = _eq(high_gain_db=gain, high_hz=4000.0)
    assert _gain_db(tmp_path, 14000.0, high) == pytest.approx(gain, abs=1.5)
    assert abs(_gain_db(tmp_path, 200.0, high)) < 1.0


@needs_ffmpeg
@pytest.mark.parametrize("corner", ["minimum", "maximum"])
def test_every_parameter_at_its_bounds_exports(tmp_path, corner):
    specs = parameter_specs(AudioEffectType.PARAMETRIC_EQ)
    params = {spec.name: (spec.minimum if corner == "minimum" else spec.maximum) for spec in specs}
    gain = _gain_db(tmp_path, 1000.0, create_audio_effect(AudioEffectType.PARAMETRIC_EQ, params=params))
    assert math.isfinite(gain)


@needs_ffmpeg
def test_the_equalizer_works_as_a_track_insert_too(tmp_path):
    effect = _eq(mid1_gain_db=9.0, mid1_hz=500.0, mid1_q=3.0)
    assert _gain_db(tmp_path, 500.0, effect, as_insert=True) == pytest.approx(9.0, abs=1.0)
