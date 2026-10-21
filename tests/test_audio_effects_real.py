"""Effets audio rendus par le vrai FFmpeg : chacun s'exporte, à toutes les bornes de ses paramètres, et fait son effet.

Les tests de ``test_audio_effects.py`` vérifient la forme de la chaîne (le nom du filtre, ses clés) sans jamais lancer
FFmpeg : quatre effets sur dix (amélioration de la voix, réduction de bruit, compresseur, limiteur) envoyaient des
valeurs que FFmpeg refuse, et tout export d'un projet qui les utilisait échouait. Ici le graphe est exécuté.
"""

from __future__ import annotations

import math
import subprocess

import numpy as np
import pytest
from audio_scenes import write_wav
from render_probe import needs_ffmpeg

from core.audio_effects_model import (
    AudioEffectType,
    create_audio_effect,
    parameter_specs,
)
from core.export_engine import ExportEngine, filter_graph_arguments, input_arguments
from core.project_model import Clip, MediaAsset, Project, Track
from core.render_plan import build_render_plan

RATE = 48000


def _render(tmp_path, signal: np.ndarray, effect) -> np.ndarray:
    """Le canal gauche du clip portant ``effect``, tel que l'export le mixe (graphe audio complet)."""
    path = write_wav(
        tmp_path / "input.wav", np.stack([signal, signal], axis=1), rate=RATE
    )
    asset = MediaAsset(
        "a", str(path), "a", len(signal) / RATE, 0, 0, 0.0, "audio", has_audio=True
    )
    clip = Clip("c", "a", "A1", 0.0, 0.0, len(signal) / RATE)
    clip.audio_effects = [effect]
    project = Project(
        "p",
        width=64,
        height=36,
        fps=25.0,
        media_assets=[asset],
        tracks=[Track("A1", "A1", "audio", clips=[clip])],
    )
    graph, _video, audio, inputs = ExportEngine._build_filter_complex(
        build_render_plan(project), 64, 36, 25.0, None, audio_only=True
    )
    command = ["ffmpeg", "-v", "error", "-nostdin"]
    for source in inputs:
        command += input_arguments(source)
    command += filter_graph_arguments(graph, []) + [
        "-map",
        f"[{audio}]",
        "-c:a",
        "pcm_f32le",
        "-f",
        "f32le",
        "-",
    ]
    completed = subprocess.run(command, capture_output=True, timeout=120)
    assert completed.returncode == 0, completed.stderr.decode("utf-8", "replace")
    return np.frombuffer(completed.stdout, dtype="<f4").reshape(-1, 2)[:, 0]


def _tone(frequency: float, amplitude: float, seconds: float = 2.0) -> np.ndarray:
    return amplitude * np.sin(
        2 * math.pi * frequency * np.arange(int(seconds * RATE)) / RATE
    )


def _db(value: float) -> float:
    return 20.0 * math.log10(max(value, 1e-9))


def _rms(signal: np.ndarray) -> float:
    return float(np.sqrt(np.mean(signal.astype(np.float64) ** 2)))


@needs_ffmpeg
@pytest.mark.parametrize("kind", list(AudioEffectType), ids=lambda kind: kind.value)
@pytest.mark.parametrize("corner", ["minimum", "default", "maximum"])
def test_every_effect_is_exported_at_every_bound_of_its_parameters(
    tmp_path, kind, corner
):
    """Tous les paramètres au plus bas, par défaut, au plus haut : FFmpeg accepte le graphe et sort du son."""
    params = {spec.name: getattr(spec, corner) for spec in parameter_specs(kind)}
    rendered = _render(
        tmp_path, _tone(440.0, 0.5), create_audio_effect(kind, params=params)
    )
    assert len(rendered) >= int(1.9 * RATE) and np.isfinite(rendered).all()


@needs_ffmpeg
def test_the_limiter_caps_the_peaks_at_its_ceiling(tmp_path):
    loud = _tone(440.0, 0.95)
    for limit_db in (-1.0, -6.0):
        out = _render(
            tmp_path,
            loud,
            create_audio_effect(AudioEffectType.LIMITER, params={"limit_db": limit_db}),
        )
        ceiling = 10 ** (limit_db / 20)
        assert np.abs(out[int(0.2 * RATE) :]).max() <= ceiling * 1.02, (
            f"plafond {limit_db} dB"
        )
        assert np.abs(out).max() >= ceiling * 0.9, (
            "il écrête au plafond, il ne coupe pas tout"
        )
    quiet = _render(
        tmp_path, _tone(440.0, 0.1), create_audio_effect(AudioEffectType.LIMITER)
    )
    assert np.abs(quiet).max() == pytest.approx(0.1, rel=0.02), (
        "sans « auto level » : un signal faible reste faible"
    )


@needs_ffmpeg
def test_the_compressor_reduces_a_loud_signal_by_its_ratio(tmp_path):
    loud = _tone(440.0, 0.8)  # ≈ −2 dBFS, 16 dB au-dessus d'un seuil à −18 dB
    effect = create_audio_effect(
        AudioEffectType.COMPRESSOR,
        params={
            "threshold_db": -18.0,
            "ratio": 4.0,
            "attack_ms": 1.0,
            "release_ms": 50.0,
            "makeup_db": 0.0,
        },
    )
    out = _render(tmp_path, loud, effect)[int(0.5 * RATE) :]
    reduction = _db(0.8) - _db(np.abs(out).max())
    assert 6.0 < reduction < 14.0, (
        f"réduction de {reduction:.1f} dB pour 16 dB de dépassement à 4:1"
    )
    made_up = create_audio_effect(
        AudioEffectType.COMPRESSOR,
        params={
            "threshold_db": -18.0,
            "ratio": 4.0,
            "attack_ms": 1.0,
            "release_ms": 50.0,
            "makeup_db": 6.0,
        },
    )
    boosted = _render(tmp_path, loud, made_up)[int(0.5 * RATE) :]
    assert _db(np.abs(boosted).max()) - _db(np.abs(out).max()) == pytest.approx(
        6.0, abs=0.7
    ), "gain de compensation"


@needs_ffmpeg
def test_voice_enhance_cuts_the_rumble_and_keeps_the_voice(tmp_path):
    effect = create_audio_effect(
        AudioEffectType.VOICE_ENHANCE, params={"frequency": 150.0, "intensity": 1.0}
    )
    rumble = _render(tmp_path, _tone(40.0, 0.5), effect)[RATE // 2 :]
    voice = _render(tmp_path, _tone(1000.0, 0.5), effect)[RATE // 2 :]
    assert _db(_rms(rumble)) < _db(_rms(_tone(40.0, 0.5))) - 8.0, (
        "les graves sous la coupure sont atténués"
    )
    assert _db(_rms(voice)) == pytest.approx(_db(_rms(_tone(1000.0, 0.5))), abs=0.5), (
        "la voix ne bouge pas"
    )
    gentle = create_audio_effect(
        AudioEffectType.VOICE_ENHANCE, params={"frequency": 150.0, "intensity": 0.0}
    )
    softer = _render(tmp_path, _tone(40.0, 0.5), gentle)[RATE // 2 :]
    assert _rms(softer) > _rms(rumble), "intensité faible : pente plus douce"


@needs_ffmpeg
def test_noise_reduce_lowers_the_noise_and_a_stronger_setting_lowers_it_more(tmp_path):
    noise = np.random.default_rng(7).normal(0.0, 0.02, 3 * RATE)
    weak = create_audio_effect(
        AudioEffectType.NOISE_REDUCE, params={"noise_floor_db": -30.0, "strength": 0.2}
    )
    strong = create_audio_effect(
        AudioEffectType.NOISE_REDUCE, params={"noise_floor_db": -30.0, "strength": 1.0}
    )
    weak_out = _rms(_render(tmp_path, noise, weak)[RATE:])
    strong_out = _rms(_render(tmp_path, noise, strong)[RATE:])
    assert strong_out < _rms(noise) * 0.7, "le bruit est réduit"
    assert strong_out < weak_out, "plus de force, plus de réduction"


def _impulse_response(tmp_path, kind, delay_ms: float) -> np.ndarray:
    """La sortie d'un effet d'écho pour une impulsion à 0,5 s, à partir de l'impulsion."""
    signal = np.zeros(2 * RATE)
    signal[RATE // 2] = 0.8
    effect = create_audio_effect(
        kind,
        params={"in_gain": 1.0, "out_gain": 1.0, "delays_ms": delay_ms, "decays": 0.6},
    )
    return _render(tmp_path, signal, effect)[RATE // 2 :]


@needs_ffmpeg
def test_the_echo_comes_back_after_its_delay_in_milliseconds(tmp_path):
    """Une impulsion revient 300 ms plus tard : ``aecho`` compte en millisecondes (le code lui passait des secondes,
    un écho 1000 fois trop court, inaudible)."""
    response = np.abs(_impulse_response(tmp_path, AudioEffectType.ECHO_LIGHT, 300.0))
    echo = int(np.argmax(response[int(0.05 * RATE) :])) + int(0.05 * RATE)
    assert echo / RATE == pytest.approx(0.3, abs=0.002)
    assert response[echo] > 0.1, "l'écho s'entend"


@needs_ffmpeg
def test_the_reverb_leaves_a_tail_of_the_stacked_delays(tmp_path):
    """Trois retards courts et décroissants (60, 40 et 25 ms pour un réglage de 60 ms) : une queue audible après
    l'impulsion, et plus longue quand le retard réglé l'est."""
    short = np.abs(_impulse_response(tmp_path, AudioEffectType.REVERB_LIGHT, 40.0))
    long = np.abs(_impulse_response(tmp_path, AudioEffectType.REVERB_LIGHT, 200.0))
    tail = slice(int(0.01 * RATE), int(0.2 * RATE))
    assert short[tail].max() > 0.05, (
        "il y a une queue (avant : tout tombait dans la première milliseconde)"
    )
    assert int(np.flatnonzero(long > 0.05)[-1]) > int(
        np.flatnonzero(short > 0.05)[-1]
    ), "un retard plus long, une queue plus longue"


def test_the_limiter_effect_is_left_out_when_ffmpeg_cannot_compensate_its_delay(
    monkeypatch,
):
    """Sans ``alimiter latency`` (FFmpeg ancien), le limiteur retarderait le clip de 5 ms derrière l'image : il n'est
    pas émis, comme le limiteur du mixage final. Avec l'option, il l'est, compensé."""
    from core import export_engine
    from core.export_engine import _build_clip_audio_effect_filters

    effect = create_audio_effect(AudioEffectType.LIMITER)
    monkeypatch.setattr(
        export_engine, "_ffmpeg_filter_has_option", lambda name, option: False
    )
    assert _build_clip_audio_effect_filters((effect,)) == []
    monkeypatch.setattr(
        export_engine,
        "_ffmpeg_filter_has_option",
        lambda name, option: (name, option) == ("alimiter", "latency"),
    )
    [line] = _build_clip_audio_effect_filters((effect,))
    assert line.startswith("alimiter=") and line.endswith(":level=0:latency=1")
