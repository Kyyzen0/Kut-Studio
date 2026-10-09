"""Export réel (FFmpeg) de l'audio d'un clip remappé : on mesure ce qu'on entend, pas la chaîne de filtres.

Source : 4 s de ``440 Hz`` puis ``880 Hz`` (PCM sans perte). Chaque test exporte le graphe complet (projet → ``RenderPlan``
→ graphe d'export, le même que celui de l'aperçu fidèle), relit l'audio décodé et en mesure la **fréquence dominante** par
fenêtre (FFT) : la hauteur conservée (``atempo``) ou qui suit la vitesse (``asetrate``), l'ordre en ``reverse``, le silence
d'un arrêt, l'audio non remappé et la durée exacte sont des propriétés audibles.
"""

from __future__ import annotations

import shutil
import subprocess

import numpy as np
import pytest

from core.animation import InterpolationType, Keyframe
from core.export_engine import ExportEngine, input_arguments
from core.project_model import Clip, MediaAsset, Project, Track
from core.render_plan import build_render_plan
from core.time_map import SPEED_PROPERTY
from core.time_remapping import TimeRemapping

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg absent")

W, H, FPS = 64, 36, 30
RATE = 48000
SOURCE_SECONDS = 4.0
LOW, HIGH = 440.0, 880.0
HOLD = InterpolationType.HOLD


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    path = tmp_path_factory.mktemp("retime_audio") / "tone.mkv"
    tone = f"aevalsrc='0.5*sin(2*PI*if(lt(t\\,2)\\,{LOW:g}\\,{HIGH:g})*t)|0.5*sin(2*PI*if(lt(t\\,2)\\,{LOW:g}\\,{HIGH:g})*t)':s={RATE}:d={SOURCE_SECONDS}"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=black:s={W}x{H}:r={FPS}:d={SOURCE_SECONDS}",
         "-f", "lavfi", "-i", tone, "-c:v", "libx264", "-crf", "0", "-preset", "ultrafast", "-c:a", "pcm_s16le",
         "-shortest", str(path)],
        check=True, timeout=60,
    )
    return str(path)


def _project(media_path, *, remapping=None, animation=(), source=(0.0, SOURCE_SECONDS)):
    asset = MediaAsset(id="a", path=media_path, name="tone", duration=SOURCE_SECONDS, width=W, height=H,
                       fps=float(FPS), media_type="video", has_audio=True)
    clip = Clip(id="c1", asset_id="a", track_id="V1", timeline_start=0.0, source_in=source[0], source_out=source[1],
                time_remapping=remapping or TimeRemapping(), animation=list(animation))
    project = Project(name="p", width=W, height=H, fps=float(FPS), media_assets=[asset],
                      tracks=[Track(id="V1", name="V1", type="video", clips=[clip])])
    return project, clip


def exported_audio(project) -> np.ndarray:
    """Canal gauche de l'audio exporté, en flottants (-1…1), de 0 à la fin de la timeline."""
    plan = build_render_plan(project)
    graph, video, audio, inputs = ExportEngine._build_filter_complex(plan, W, H, FPS, None)
    assert audio, "le graphe doit produire une piste audio"
    command = ["ffmpeg", "-y", "-loglevel", "error"]
    for path in inputs:
        command += input_arguments(path)
    command += ["-filter_complex", graph, "-map", f"[{audio}]", "-f", "s16le", "-ar", str(RATE), "-ac", "2", "-",
                "-map", f"[{video}]", "-f", "null", "-"]       # une 2de sortie plutôt qu'un puits (FFmpeg 7.x)
    done = subprocess.run(command, capture_output=True, timeout=120)
    assert done.returncode == 0, done.stderr.decode(errors="replace")[-800:]
    samples = np.frombuffer(done.stdout, dtype=np.int16).reshape(-1, 2)[:, 0].astype(np.float64)
    return samples / 32768.0


def dominant(samples: np.ndarray, start: float, end: float) -> float:
    """Fréquence dominante (Hz) de ``samples`` entre ``start`` et ``end`` secondes (FFT à fenêtre de Hann, affinée)."""
    window = samples[int(start * RATE): int(end * RATE)]
    spectrum = np.abs(np.fft.rfft(window * np.hanning(len(window)), n=len(window) * 8))
    return float(np.argmax(spectrum)) * RATE / (len(window) * 8)


def level(samples: np.ndarray, start: float, end: float) -> float:
    window = samples[int(start * RATE): int(end * RATE)]
    return float(np.sqrt(np.mean(window**2)))


def assert_near(value: float, expected: float, *, tolerance: float = 0.02) -> None:
    assert abs(value - expected) <= expected * tolerance, (value, expected)


# ---------------------------------------------------------------------------
# Vitesse constante : la hauteur est conservée (atempo) ou suit la vitesse (asetrate)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("speed", [2.0, 0.5, 4.0])
def test_preserving_the_pitch_keeps_both_tones_at_any_speed(media, speed):
    project, clip = _project(media, remapping=TimeRemapping(speed=speed, preserve_pitch=True))
    audio = exported_audio(project)
    duration = clip.duration
    assert_near(dominant(audio, 0.1 * duration, 0.4 * duration), LOW)
    assert_near(dominant(audio, 0.6 * duration, 0.9 * duration), HIGH)


@pytest.mark.parametrize("speed", [2.0, 0.5])
def test_without_preserving_the_pitch_the_frequency_follows_the_speed(media, speed):
    project, clip = _project(media, remapping=TimeRemapping(speed=speed, preserve_pitch=False))
    audio = exported_audio(project)
    duration = clip.duration
    assert_near(dominant(audio, 0.1 * duration, 0.4 * duration), LOW * speed)
    assert_near(dominant(audio, 0.6 * duration, 0.9 * duration), HIGH * speed)


def test_the_exported_audio_lasts_exactly_the_clip_duration(media):
    for speed in (0.5, 1.5, 2.0, 3.0):
        project, clip = _project(media, remapping=TimeRemapping(speed=speed))
        audio = exported_audio(project)
        assert abs(len(audio) / RATE - clip.duration) < 0.002, (speed, len(audio) / RATE, clip.duration)


# ---------------------------------------------------------------------------
# Reverse, arrêt, courbe
# ---------------------------------------------------------------------------


def test_reverse_plays_the_second_tone_first(media):
    project, _clip = _project(media, remapping=TimeRemapping(reverse=True))
    audio = exported_audio(project)
    assert_near(dominant(audio, 0.5, 1.5), HIGH)
    assert_near(dominant(audio, 2.5, 3.5), LOW)


def test_a_zero_speed_stretch_is_silent_and_the_sound_resumes_where_it_stopped(media):
    """100 % pendant 1 s, 0 % pendant 1 s (arrêt), puis 100 % : le son se tait pendant l'arrêt puis reprend à 1 s de source."""
    keys = [Keyframe(SPEED_PROPERTY, 0.0, 1.0, HOLD), Keyframe(SPEED_PROPERTY, 1.0, 0.0, HOLD),
            Keyframe(SPEED_PROPERTY, 2.0, 1.0, HOLD)]
    project, clip = _project(media, remapping=TimeRemapping(anchor=0.0), animation=keys)
    audio = exported_audio(project)
    assert abs(len(audio) / RATE - clip.duration) < 0.002
    assert_near(dominant(audio, 0.2, 0.8), LOW)
    assert level(audio, 1.1, 1.9) < 0.01 * max(level(audio, 0.2, 0.8), 1e-9)
    assert_near(dominant(audio, 2.2, 2.8), LOW)                         # reprise à 1 s de source (toujours 440 Hz)
    assert_near(dominant(audio, clip.duration - 1.0, clip.duration - 0.2), HIGH)


def test_a_speed_ramp_keeps_the_tones_with_preserved_pitch_and_bends_them_without(media):
    keys = [Keyframe(SPEED_PROPERTY, 0.0, 1.0, InterpolationType.LINEAR),
            Keyframe(SPEED_PROPERTY, 2.0, 2.0, InterpolationType.LINEAR)]
    kept, clip = _project(media, remapping=TimeRemapping(preserve_pitch=True), animation=keys)
    audio = exported_audio(kept)
    assert_near(dominant(audio, 0.2, 0.8), LOW)                         # source 0-1 s : encore le premier ton
    bent, _ = _project(media, remapping=TimeRemapping(preserve_pitch=False), animation=list(keys))
    audio = exported_audio(bent)
    early, late = dominant(audio, 0.1, 0.6), dominant(audio, 1.2, 1.7)
    assert late > early * 1.25, (early, late)                           # la hauteur monte avec la vitesse


# ---------------------------------------------------------------------------
# Vidéo seule : l'audio garde son temps
# ---------------------------------------------------------------------------


def test_remapping_the_video_only_leaves_the_audio_at_normal_speed(media):
    project, clip = _project(media, remapping=TimeRemapping(speed=2.0, remap_audio=False))
    audio = exported_audio(project)
    assert abs(len(audio) / RATE - clip.duration) < 0.002
    # À 2× la vidéo dure 2 s ; l'audio, lui, joue la source à vitesse normale depuis le début : 440 Hz pendant toute la durée.
    assert_near(dominant(audio, 0.2, 0.9), LOW)
    assert_near(dominant(audio, 1.1, 1.8), LOW)
