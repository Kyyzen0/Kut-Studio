"""Le Master coupé exporte du **silence** : ``volume=0dB`` est le gain unité, pas la coupure.

Régression mesurée le 2026-10-04 (FFmpeg 9.0.2) : avec ``master_muted=True``, le filtre Master était ``volume=0dB``. Dans
FFmpeg un nombre suivi de ``dB`` est un gain en décibels (0 dB = x1,0) et un nombre seul est un facteur linéaire ; le son sortait
donc intact. Le test de chaîne qui existait comparait la chaîne à ``"volume=0dB"`` : il figeait le défaut au lieu de le voir.

Les rendus sont réels et relus **en échantillons flottants** : ni la conversion en 16 bits de ``volumedetect`` ni l'encodeur
n'arrondissent un résidu à zéro. Le niveau absolu d'un export non coupé dépend du mixage (``amix``, limiteur) qui évolue à part :
les tests comparent donc le Master coupé et le gain Master à l'export non coupé du même plan, jamais à un niveau en dur.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from core.export_engine import ExportEngine, _build_master_filter, _ffmpeg_command_prefix
from core.filter_graph import build_preview_command
from core.project_model import Clip, MediaAsset, Project, Track
from core.render_plan import build_render_plan

requires_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg indisponible")

W, H, FPS = 160, 90, 25
SECONDS = 4.0
AMPLITUDE = 0.5
SILENCE = 1e-6  # un facteur linéaire 0 donne des zéros exacts ; la marge n'existe que pour un arrondi de décodage
AAC_SILENCE = 1e-3  # −60 dBFS : un encodeur AAC peut laisser un résidu d'amorce, jamais un signal audible


def _run(command: list[str]) -> None:
    done = subprocess.run(command, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr[-1500:]


def _ffmpeg(*arguments: str) -> list[str]:
    return [*_ffmpeg_command_prefix(), "-v", "error", "-y", *arguments]


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    """Un sinus stéréo d'amplitude connue (0,5 = −6 dBFS) et une vidéo muette."""
    folder = tmp_path_factory.mktemp("master_mute")
    tone = folder / "tone.wav"
    wave = f"{AMPLITUDE}*sin(2*PI*440*t)"
    _run(_ffmpeg("-f", "lavfi", "-i", f"aevalsrc={wave}|{wave}:sample_rate=48000:duration={SECONDS}", "-c:a", "pcm_s16le", str(tone)))
    video = folder / "video.mp4"
    _run(_ffmpeg("-f", "lavfi", "-i", f"color=c=gray:s={W}x{H}:r={FPS}:d={SECONDS}", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)))
    return {"tone": tone, "video": video}


def _project(media) -> Project:
    assets = [
        MediaAsset("video", str(media["video"]), "video", SECONDS, W, H, float(FPS), "video", False),
        MediaAsset("tone", str(media["tone"]), "tone", SECONDS, 0, 0, 0.0, "audio", True),
    ]
    tracks = [
        Track("V1", "V1", "video", clips=[Clip("v", "video", "V1", 0.0, 0.0, SECONDS)]),
        Track("A1", "A1", "audio", clips=[Clip("a", "tone", "A1", 0.0, 0.0, SECONDS)]),
    ]
    return Project("master-mute", width=W, height=H, fps=float(FPS), media_assets=assets, tracks=tracks)


def _export(plan, path: Path, codec: str = "pcm_f32le") -> Path:
    """Rend le graphe de l'export réel ; ``pcm_f32le`` (en ``.mkv``) garde les échantillons flottants intacts."""
    graph, video, audio, inputs = ExportEngine._build_filter_complex(plan, W, H, FPS, None)
    command = _ffmpeg()
    for item in inputs:
        command += ["-i", item]
    command += ["-filter_complex", graph, "-map", f"[{video}]", "-map", f"[{audio}]", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-c:a", codec, str(path)]
    _run(command)
    return path


def _samples(path: Path) -> np.ndarray:
    command = [*_ffmpeg_command_prefix(), "-v", "error", "-i", str(path), "-vn", "-ac", "2", "-ar", "48000", "-f", "f32le", "-"]
    raw = subprocess.run(command, capture_output=True, timeout=60).stdout
    return np.frombuffer(raw, dtype=np.float32).reshape(-1, 2)


def _peak(path: Path) -> float:
    return float(np.abs(_samples(path)).max())


# ---------------------------------------------------------------------------
# Rendus réels
# ---------------------------------------------------------------------------


@requires_ffmpeg
def test_a_muted_master_exports_silence(media, tmp_path):
    """Le cas mesuré : avant le correctif, la crête d'un export coupé était celle d'un export non coupé."""
    project = _project(media)
    audible = _peak(_export(build_render_plan(project), tmp_path / "audible.mkv"))
    muted = _peak(_export(build_render_plan(project, master_muted=True), tmp_path / "muted.mkv"))
    assert audible > 0.1, "le témoin doit s'entendre, sinon le test ne prouve rien"
    assert muted <= SILENCE, f"Master coupé : crête {muted:.6f} (non coupé : {audible:.6f})"


@requires_ffmpeg
def test_a_muted_master_keeps_the_audio_track_and_its_duration(media, tmp_path):
    """Couper le Master rend une piste muette *valide* (même durée), pas une piste absente ni tronquée."""
    project = _project(media)
    audible = _samples(_export(build_render_plan(project), tmp_path / "audible.mkv"))
    muted = _samples(_export(build_render_plan(project, master_muted=True), tmp_path / "muted.mkv"))
    assert len(muted) == len(audible) > 0


@requires_ffmpeg
def test_a_muted_master_is_silent_in_the_delivered_aac_too(media, tmp_path):
    """Le format réellement livré (AAC dans MP4) : aucun signal audible ne doit survivre à l'encodage."""
    out = _export(build_render_plan(_project(media), master_muted=True), tmp_path / "muted.mp4", codec="aac")
    assert _peak(out) <= AAC_SILENCE


@requires_ffmpeg
def test_master_gain_is_unchanged_by_the_mute_fix(media, tmp_path):
    """La branche gain garde ses décibels : -6 dB divise l'amplitude par ~2 (0,501) et n'éteint rien."""
    project = _project(media)
    audible = _peak(_export(build_render_plan(project), tmp_path / "audible.mkv"))
    quieter = _peak(_export(build_render_plan(project, master_gain_db=-6.0), tmp_path / "quieter.mkv"))
    assert quieter == pytest.approx(audible * 10 ** (-6.0 / 20), rel=0.02)


@requires_ffmpeg
def test_a_muted_preview_segment_is_silent(media, tmp_path):
    """L'aperçu rend le même graphe que l'export : un segment d'aperçu muet l'est vraiment."""
    plan = build_render_plan(_project(media), master_muted=True)
    out = tmp_path / "segment.mp4"
    _run(build_preview_command(plan, width=W, height=H, fps=FPS, quality="draft", output_path=str(out)))
    assert 0 < len(_samples(out))
    assert _peak(out) <= AAC_SILENCE


# ---------------------------------------------------------------------------
# Forme du graphe (sans FFmpeg)
# ---------------------------------------------------------------------------


def test_the_muted_filter_is_a_linear_zero_not_zero_decibels():
    project = Project("p", width=W, height=H, fps=float(FPS), media_assets=[], tracks=[])
    muted = _build_master_filter(build_render_plan(project, master_muted=True))
    assert muted == "volume=0"
    assert "dB" not in muted, "un nombre suivi de dB est un gain en décibels : 0dB est le gain unité"


def test_the_muted_master_filter_wins_over_a_master_gain():
    project = Project("p", width=W, height=H, fps=float(FPS), media_assets=[], tracks=[])
    assert _build_master_filter(build_render_plan(project, master_muted=True, master_gain_db=+6.0)) == "volume=0"


def test_the_muted_filter_is_in_the_final_mix_of_the_export_graph(media):
    """Le filtre atteint bien le graphe : ``volume=0`` suivi d'un autre étage, jamais le ``0dB`` d'avant."""
    plan = build_render_plan(_project(media), master_muted=True)
    graph = ExportEngine._build_filter_complex(plan, W, H, FPS, None)[0]
    assert "volume=0," in graph
    assert "volume=0dB" not in graph


def test_an_unmuted_master_without_gain_adds_no_filter():
    project = Project("p", width=W, height=H, fps=float(FPS), media_assets=[], tracks=[])
    assert _build_master_filter(build_render_plan(project)) == ""
