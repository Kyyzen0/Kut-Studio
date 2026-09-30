"""Tests d'intégration pour l'export FFmpeg basé ``RenderPlan``.

Ces tests utilisent principalement un faux binaire ``ffmpeg`` pour
valider l'orchestration ``MainWindow`` + ``ExportEngine`` sans
déclencher de vrai rendu. Un test d'intégration final génère de
petites vidéos temporaires via ``ffmpeg`` et sonde la sortie via
``ffprobe`` pour vérifier la durée du montage.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.project_model import Clip, MediaAsset, Project, Track  # noqa: E402
from core.render_plan import build_render_plan  # noqa: E402


FAKE_FFMPEG_PYTHON = ROOT / "tests" / "fixtures" / "fake_ffmpeg.py"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_ffmpeg_path(monkeypatch):
    """Force le module ``export_engine`` à utiliser un faux portable."""
    command = (sys.executable, str(FAKE_FFMPEG_PYTHON))
    monkeypatch.setattr("core.export_engine._ffmpeg_path", command)
    return command


def _make_video_asset(path: str, asset_id: str = "asset_1") -> MediaAsset:
    """Construit un ``MediaAsset`` factice à partir d'un chemin."""
    return MediaAsset(
        id=asset_id,
        path=path,
        name=Path(path).name,
        duration=10.0,
        width=1920,
        height=1080,
        fps=30.0,
        media_type="video",
    )


def _make_video_project(input_files: list[str]) -> Project:
    """Construit un ``Project`` avec deux clips contigus sur V1."""
    assets = [_make_video_asset(path, f"asset_{i}") for i, path in enumerate(input_files)]
    clips = [
        Clip(
            id=f"clip_{i}",
            asset_id=assets[i].id,
            track_id="V1",
            timeline_start=float(i * 4),
            source_in=0.0,
            source_out=4.0,
            label=f"Clip {i}",
        )
        for i in range(len(input_files))
    ]
    return Project(
        name="Export Test",
        width=1920,
        height=1080,
        fps=30.0,
        media_assets=assets,
        tracks=[Track(id="V1", name="V1", type="video", clips=clips)],
    )


def _make_subtitle_project() -> Project:
    """Construit un projet ne contenant qu'un clip de sous-titre."""
    asset = MediaAsset(
        id="asset_sub",
        path="",
        name="Subtitle",
        duration=10.0,
        width=0,
        height=0,
        fps=0.0,
        media_type="subtitle",
        has_audio=False,
    )
    clip = Clip(
        id="sub_1",
        asset_id="asset_sub",
        track_id="S1",
        timeline_start=0.0,
        source_in=0.0,
        source_out=5.0,
        label="Sous-titre",
        text="Hello",
    )
    return Project(
        name="Subtitle Test",
        width=1920,
        height=1080,
        fps=30.0,
        media_assets=[asset],
        tracks=[Track(id="S1", name="S1", type="subtitle", clips=[clip])],
    )


def _create_dummy_input_files(tmp_path: Path) -> list[str]:
    """Crée deux fichiers vidéo vides simulant des sources média."""
    files: list[str] = []
    for i in range(2):
        path = tmp_path / f"input_{i}.mp4"
        path.write_bytes(b"\x00" * 100)
        files.append(str(path))
    return files


def _wait_for_export(engine, timeout_ms: int = 10000) -> tuple[list[str], list[str]]:
    """Bloque jusqu'à la fin de l'export, retourne (finished_ok, failed)."""
    finished: list[str] = []
    failed: list[str] = []
    engine.finished_ok.connect(finished.append)
    engine.failed.connect(failed.append)

    loop = QEventLoop()
    engine.finished_ok.connect(loop.quit)
    engine.failed.connect(loop.quit)
    QTimer.singleShot(timeout_ms, loop.quit)
    loop.exec()
    return finished, failed


# ---------------------------------------------------------------------------
# Tests orchestrateur (faux ffmpeg)
# ---------------------------------------------------------------------------


def test_export_full_pipeline_emits_finished_ok(
    qtbot, tmp_path, fake_ffmpeg_path, monkeypatch
):
    """Lance un export complet, vérifie que ``finished_ok`` est émis."""
    from ui.main_window import MainWindow

    monkeypatch.setattr("ui.main_window.QMessageBox.information", lambda *_: None)
    monkeypatch.setattr("ui.main_window.QMessageBox.critical", lambda *_: None)

    input_files = _create_dummy_input_files(tmp_path)
    output_file = tmp_path / "output.mp4"

    window = MainWindow()
    qtbot.addWidget(window)
    window.project = _make_video_project(input_files)
    window.timeline_panel.set_project(window.project)

    from core.export_engine import (
        ExportEngine,
        ExportFormat,
        ExportPreset,
        ExportRequest,
    )

    engine = window.export_engine
    request = ExportRequest(
        render_plan=build_render_plan(window.project),
        output_path=str(output_file),
        format=ExportFormat.MP4_H264,
        preset=ExportPreset(name="Standard", resolution=(1920, 1080), crf=23, audio_bitrate="128k"),
        fps=30,
    )
    engine.start(request)
    finished, failed = _wait_for_export(engine)

    assert not failed, f"Export a échoué : {failed}"
    assert finished, "Le signal finished_ok n'a pas été émis"
    assert output_file.exists(), f"Le fichier de sortie n'a pas été créé : {output_file}"


def test_export_with_no_exportable_clips_emits_failed(
    qtbot, tmp_path, fake_ffmpeg_path
):
    """Un plan sans clip vidéo doit émettre ``failed`` avec un message clair."""
    from ui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    window.project = _make_subtitle_project()
    window.timeline_panel.set_project(window.project)

    from core.export_engine import (
        ExportEngine,
        ExportFormat,
        ExportPreset,
        ExportRequest,
    )

    engine = window.export_engine
    failed_messages: list[str] = []
    engine.failed.connect(failed_messages.append)

    request = ExportRequest(
        render_plan=build_render_plan(window.project),
        output_path=str(tmp_path / "output.mp4"),
        format=ExportFormat.MP4_H264,
        preset=ExportPreset(name="Standard", resolution=(1920, 1080), crf=23, audio_bitrate="128k"),
        fps=30,
    )
    engine.start(request)

    assert failed_messages, "failed doit être émis quand il n'y a aucun média à exporter"
    assert (
        "média" in failed_messages[0].lower()
        or "video" in failed_messages[0].lower()
    ), f"Message d'erreur inattendu : {failed_messages[0]}"


def test_export_engine_module_path_validation(qtbot, tmp_path):
    """Vérifie que ``shutil.which`` est appelé pour valider ffmpeg au chargement."""
    import core.export_engine as engine_module

    assert engine_module._ffmpeg_path is not None, (
        "ffmpeg (ou le fake pour les tests) doit être trouvable dans le PATH"
    )


def test_export_request_validates_fps_and_resolution(qtbot, tmp_path, fake_ffmpeg_path):
    """Des paramètres invalides doivent être rejetés avant le lancement de ffmpeg."""
    from core.export_engine import ExportFormat, ExportPreset, ExportRequest
    from core.render_plan import RenderPlan

    empty_plan = RenderPlan(
        width=1920, height=1080, fps=30.0, duration=4.0,
        video_layers=(),
    )

    with pytest.raises(ValueError, match="fréquence"):
        ExportRequest(
            render_plan=empty_plan,
            output_path=str(tmp_path / "out.mp4"),
            format=ExportFormat.MP4_H264,
            preset=ExportPreset(name="Standard", resolution=(1920, 1080), crf=23, audio_bitrate="128k"),
            fps=0,
        )

    with pytest.raises(ValueError, match="résolution"):
        ExportRequest(
            render_plan=empty_plan,
            output_path=str(tmp_path / "out.mp4"),
            format=ExportFormat.MP4_H264,
            preset=ExportPreset(name="Standard", resolution=(0, 0), crf=23, audio_bitrate="128k"),
            fps=30,
        )


# ---------------------------------------------------------------------------
# Test d'intégration réel avec FFmpeg
# ---------------------------------------------------------------------------


def _require_ffmpeg() -> tuple[str, str]:
    """Retourne les chemins de ffmpeg et ffprobe, ou skip le test."""
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("ffmpeg/ffprobe introuvables dans le PATH")
    return ffmpeg, ffprobe


def _generate_color_clip(
    ffmpeg: str, output_path: Path, *, color: str, duration: float, fps: int = 15
) -> None:
    """Génère une vidéo de couleur unie via le démultiplexeur lavfi."""
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s=160x90:r={fps}:d={duration}",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            str(output_path),
        ],
        check=True,
        capture_output=True,
    )


def _probe_duration(ffprobe: str, path: Path) -> float:
    """Retourne la durée en secondes d'un fichier via ``ffprobe``."""
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(result.stdout.strip())


def _build_overlap_project(red_path: Path, blue_path: Path) -> Project:
    """Projet : V1 rouge [0, 2], V2 bleu [1, 4] — chevauchement V2 au-dessus."""
    return Project(
        name="Real-Overlap",
        width=160,
        height=90,
        fps=15.0,
        media_assets=[
            MediaAsset(
                id="red", path=str(red_path), name="Red",
                duration=2.0, width=160, height=90, fps=15.0, media_type="video",
            ),
            MediaAsset(
                id="blue", path=str(blue_path), name="Blue",
                duration=4.0, width=160, height=90, fps=15.0, media_type="video",
            ),
        ],
        tracks=[
            Track(
                id="V1", name="V1", type="video",
                clips=[
                    Clip(
                        id="v1-red", asset_id="red", track_id="V1",
                        timeline_start=0.0, source_in=0.0, source_out=2.0,
                    ),
                ],
            ),
            Track(
                id="V2", name="V2", type="video",
                clips=[
                    Clip(
                        id="v2-blue", asset_id="blue", track_id="V2",
                        timeline_start=1.0, source_in=0.0, source_out=3.0,
                    ),
                ],
            ),
        ],
    )


def _build_gap_project(red_path: Path, blue_path: Path) -> Project:
    """Projet : V1 rouge [0, 2], V1 bleu [3, 5] — trou entre 2s et 3s."""
    return Project(
        name="Real-Gap",
        width=160,
        height=90,
        fps=15.0,
        media_assets=[
            MediaAsset(
                id="red", path=str(red_path), name="Red",
                duration=2.0, width=160, height=90, fps=15.0, media_type="video",
            ),
            MediaAsset(
                id="blue", path=str(blue_path), name="Blue",
                duration=2.0, width=160, height=90, fps=15.0, media_type="video",
            ),
        ],
        tracks=[
            Track(
                id="V1", name="V1", type="video",
                clips=[
                    Clip(
                        id="v1-red", asset_id="red", track_id="V1",
                        timeline_start=0.0, source_in=0.0, source_out=2.0,
                    ),
                    Clip(
                        id="v1-blue", asset_id="blue", track_id="V1",
                        timeline_start=3.0, source_in=0.0, source_out=2.0,
                    ),
                ],
            ),
        ],
    )


def test_real_ffmpeg_export_overlap_has_correct_duration(qtbot, tmp_path):
    """Export réel : V1+V2 qui se chevauchent, durée = max(end)."""
    ffmpeg, ffprobe = _require_ffmpeg()

    # Vidéos sources courtes pour garder le test rapide.
    red_path = tmp_path / "red.mp4"
    blue_path = tmp_path / "blue.mp4"
    _generate_color_clip(ffmpeg, red_path, color="red", duration=2.0)
    _generate_color_clip(ffmpeg, blue_path, color="blue", duration=4.0)

    project = _build_overlap_project(red_path, blue_path)
    plan = build_render_plan(project)
    assert plan.duration == pytest.approx(4.0)

    from core.export_engine import (
        ExportEngine,
        ExportFormat,
        ExportPreset,
        ExportRequest,
    )

    output_path = tmp_path / "out_overlap.mp4"
    request = ExportRequest(
        render_plan=plan,
        output_path=str(output_path),
        format=ExportFormat.MP4_H264,
        preset=ExportPreset(name="Test", resolution=(160, 90), crf=28, audio_bitrate="96k"),
        fps=15,
    )

    engine = ExportEngine()
    engine.start(request)
    finished, failed = _wait_for_export(engine, timeout_ms=30000)

    assert not failed, f"ffmpeg a échoué : {failed}"
    assert finished, "finished_ok aurait dû être émis"
    assert output_path.exists()

    duration = _probe_duration(ffprobe, output_path)
    assert abs(duration - 4.0) < 0.5, (
        f"Durée attendue ≈ 4s, obtenue {duration:.3f}s"
    )


def test_real_ffmpeg_export_with_gap_has_correct_duration(qtbot, tmp_path):
    """Export réel : un trou de 1s entre deux clips doit être conservé."""
    ffmpeg, ffprobe = _require_ffmpeg()

    red_path = tmp_path / "red.mp4"
    blue_path = tmp_path / "blue.mp4"
    _generate_color_clip(ffmpeg, red_path, color="red", duration=2.0)
    _generate_color_clip(ffmpeg, blue_path, color="blue", duration=2.0)

    project = _build_gap_project(red_path, blue_path)
    plan = build_render_plan(project)
    assert plan.duration == pytest.approx(5.0)

    from core.export_engine import (
        ExportEngine,
        ExportFormat,
        ExportPreset,
        ExportRequest,
    )

    output_path = tmp_path / "out_gap.mp4"
    request = ExportRequest(
        render_plan=plan,
        output_path=str(output_path),
        format=ExportFormat.MP4_H264,
        preset=ExportPreset(name="Test", resolution=(160, 90), crf=28, audio_bitrate="96k"),
        fps=15,
    )

    engine = ExportEngine()
    engine.start(request)
    finished, failed = _wait_for_export(engine, timeout_ms=30000)

    assert not failed, f"ffmpeg a échoué : {failed}"
    assert finished, "finished_ok aurait dû être émis"
    assert output_path.exists()

    duration = _probe_duration(ffprobe, output_path)
    assert abs(duration - 5.0) < 0.5, (
        f"Durée attendue ≈ 5s (avec trou), obtenue {duration:.3f}s"
    )


# ---------------------------------------------------------------------------
# Tâche 10 — Test d'intégration audio réel (ffmpeg + ffprobe)
# ---------------------------------------------------------------------------


def _generate_color_with_tone(
    ffmpeg: str,
    output_path: Path,
    *,
    color: str,
    tone_freq: int,
    duration: float,
    fps: int = 15,
) -> None:
    """Génère une vidéo couleurisée avec une tonalité audio intégrée."""
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s=160x90:r={fps}:d={duration}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={tone_freq}:sample_rate=48000:duration={duration}",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            "-ac",
            "2",
            "-shortest",
            str(output_path),
        ],
        check=True,
        capture_output=True,
    )


def _generate_audio_tone(
    ffmpeg: str,
    output_path: Path,
    *,
    frequency: int,
    duration: float,
) -> None:
    """Génère un fichier audio pur (tonalité sinusoïdale)."""
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={frequency}:sample_rate=48000:duration={duration}",
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            "-ac",
            "2",
            str(output_path),
        ],
        check=True,
        capture_output=True,
    )


def _probe_streams(ffprobe: str, path: Path) -> dict:
    """Retourne les infos de flux (``streams`` + ``format``) d'un fichier."""
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    import json as _json

    return _json.loads(result.stdout)


def _build_audio_mix_project(
    video_with_tone_path: Path,
    audio_tone_path: Path,
) -> Project:
    """Projet : V1 (vidéo avec tonalité 440 Hz) + A1 (tonalité 880 Hz décalée) + trou."""
    video_asset = MediaAsset(
        id="vid-tone",
        path=str(video_with_tone_path),
        name="VidTone",
        duration=4.0,
        width=160,
        height=90,
        fps=15.0,
        media_type="video",
        has_audio=True,
    )
    audio_asset = MediaAsset(
        id="audio-tone",
        path=str(audio_tone_path),
        name="AudioTone",
        duration=3.0,
        width=0,
        height=0,
        fps=0.0,
        media_type="audio",
        has_audio=True,
    )
    return Project(
        name="AudioMix",
        width=160,
        height=90,
        fps=15.0,
        media_assets=[video_asset, audio_asset],
        tracks=[
            Track(
                id="V1", name="V1", type="video",
                clips=[
                    Clip(
                        id="vid-clip", asset_id="vid-tone", track_id="V1",
                        timeline_start=0.0, source_in=0.0, source_out=4.0,
                    ),
                ],
            ),
            Track(
                id="A1", name="A1", type="audio",
                clips=[
                    Clip(
                        id="audio-clip", asset_id="audio-tone", track_id="A1",
                        # Le clip audio démarre à 2 s : trou audio [0, 2].
                        timeline_start=2.0, source_in=0.0, source_out=3.0,
                    ),
                ],
            ),
        ],
    )


def test_real_ffmpeg_export_produces_video_and_audio_streams(qtbot, tmp_path):
    """Un projet avec vidéo + audio produit un export avec flux vidéo + audio."""
    ffmpeg, ffprobe = _require_ffmpeg()

    # 1. Génère une vidéo rouge 4 s avec tonalité 440 Hz embarquée.
    video_path = tmp_path / "vid.mp4"
    _generate_color_with_tone(
        ffmpeg, video_path, color="red", tone_freq=440, duration=4.0,
    )

    # 2. Génère un fichier audio pur 3 s avec tonalité 880 Hz.
    audio_path = tmp_path / "song.m4a"
    _generate_audio_tone(ffmpeg, audio_path, frequency=880, duration=3.0)

    # 3. Construit le projet mixte.
    project = _build_audio_mix_project(video_path, audio_path)
    plan = build_render_plan(project)
    # La durée totale = max(4, 2+3) = 5 s.
    assert plan.duration == pytest.approx(5.0)
    # Au moins une couche audio (issue de la vidéo + le clip audio pur).
    assert len(plan.audio_layers) >= 2

    # 4. Exporte.
    from core.export_engine import (
        ExportEngine,
        ExportFormat,
        ExportPreset,
        ExportRequest,
    )

    output_path = tmp_path / "out_with_audio.mp4"
    request = ExportRequest(
        render_plan=plan,
        output_path=str(output_path),
        format=ExportFormat.MP4_H264,
        preset=ExportPreset(name="Test", resolution=(160, 90), crf=28, audio_bitrate="96k"),
        fps=15,
    )

    engine = ExportEngine()
    engine.start(request)
    finished, failed = _wait_for_export(engine, timeout_ms=30000)

    assert not failed, f"ffmpeg a échoué : {failed}"
    assert finished, "finished_ok aurait dû être émis"
    assert output_path.exists(), "Le fichier de sortie n'a pas été créé"

    # 5. Vérifie la présence d'un flux vidéo ET d'un flux audio.
    streams = _probe_streams(ffprobe, output_path)
    has_video = any(s.get("codec_type") == "video" for s in streams["streams"])
    has_audio = any(s.get("codec_type") == "audio" for s in streams["streams"])
    assert has_video, "Le fichier final doit contenir un flux vidéo"
    assert has_audio, "Le fichier final doit contenir un flux audio"

    # 6. Vérifie la durée.
    duration = float(streams["format"]["duration"])
    assert abs(duration - 5.0) < 0.5, (
        f"Durée attendue ≈ 5s, obtenue {duration:.3f}s"
    )


# ---------------------------------------------------------------------------
# Tâche 11 — Test d'intégration sous-titres (FFmpeg réel)
# ---------------------------------------------------------------------------


def _build_subtitle_project(video_path: Path) -> Project:
    """Projet minimal : une vidéo 4 s + un sous-titre incrusté."""
    video_asset = MediaAsset(
        id="asset-vid",
        path=str(video_path),
        name="Vid",
        duration=4.0,
        width=160,
        height=90,
        fps=15.0,
        media_type="video",
    )
    sub_asset = MediaAsset(
        id="asset-sub",
        path="",
        name="Sub",
        duration=4.0,
        width=0,
        height=0,
        fps=0.0,
        media_type="subtitle",
        has_audio=False,
    )
    return Project(
        name="SubExport",
        width=160,
        height=90,
        fps=15.0,
        media_assets=[video_asset, sub_asset],
        tracks=[
            Track(
                id="V1", name="V1", type="video",
                clips=[
                    Clip(
                        id="vid", asset_id="asset-vid", track_id="V1",
                        timeline_start=0.0, source_in=0.0, source_out=4.0,
                    ),
                ],
            ),
            Track(
                id="S1", name="S1", type="subtitle",
                clips=[
                    Clip(
                        id="sub", asset_id="asset-sub", track_id="S1",
                        timeline_start=1.0, source_in=0.0, source_out=2.5,
                        text="Bonjour le monde",
                    ),
                ],
            ),
        ],
    )


def test_real_ffmpeg_export_burns_subtitles_into_mp4(qtbot, tmp_path):
    """Export réel avec sous-titres : FFmpeg termine, vidéo+audio présents,
    fichier SRT temporaire nettoyé, filtre ``subtitles`` présent dans la
    commande.

    Ce test skip si la build FFmpeg locale n'a pas libass (filtre
    ``subtitles`` indisponible).
    """
    import subprocess as _subprocess

    ffmpeg, ffprobe = _require_ffmpeg()

    # Détection rapide de libass.
    try:
        check = _subprocess.run(
            [ffmpeg, "-hide_banner", "-filters"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except Exception:
        libass_available = False
    else:
        libass_available = " subtitles " in f" {check.stdout} "
    if not libass_available:
        pytest.skip(
            "La build FFmpeg locale ne contient pas libass : "
            "filtre 'subtitles' indisponible."
        )

    # 1. Vidéo temporaire courte, sans audio (pour vérifier que le
    # moteur ajoute quand même un flux audio silencieux).
    video_path = tmp_path / "src.mp4"
    _generate_color_clip(
        ffmpeg, video_path, color="blue", duration=4.0,
    )

    # 2. Construction du projet.
    project = _build_subtitle_project(video_path)
    plan = build_render_plan(project)
    assert len(plan.subtitle_cues) == 1
    assert plan.subtitle_cues[0].text == "Bonjour le monde"

    # 3. Export.
    from core.export_engine import (
        ExportEngine,
        ExportFormat,
        ExportPreset,
        ExportRequest,
    )

    output_path = tmp_path / "out_sub.mp4"
    request = ExportRequest(
        render_plan=plan,
        output_path=str(output_path),
        format=ExportFormat.MP4_H264,
        preset=ExportPreset(name="Test", resolution=(160, 90), crf=28, audio_bitrate="96k"),
        fps=15,
    )

    engine = ExportEngine()
    # Préparation manuelle des fichiers temporaires : on veut inspecter
    # la commande AVANT de lancer FFmpeg.
    engine._prepare_temporary_files(plan)
    try:
        command = engine._build_command(request)
    finally:
        engine._cleanup_temporary_files()

    # Le filtre ``subtitles=`` doit apparaître dans la commande.
    filter_complex = command[command.index("-filter_complex") + 1]
    assert "subtitles=" in filter_complex
    assert "[vfinal]" in filter_complex

    # Aucun SRT permanent à côté du code source avant export.
    repo_root = Path(__file__).resolve().parent.parent
    leftover_pre = list(repo_root.glob("**/*.srt"))
    leftover_pre = [
        path
        for path in leftover_pre
        if not path.is_relative_to(tmp_path)
        and "kut-studio-subtitles-" in path.name
    ]
    assert leftover_pre == [], (
        "Aucun SRT temporaire de Kut-Studio ne doit exister avant l'export."
    )

    engine.start(request)
    finished, failed = _wait_for_export(engine, timeout_ms=30000)

    assert not failed, f"ffmpeg a échoué : {failed}"
    assert finished, "finished_ok aurait dû être émis"
    assert output_path.exists()

    # 4. Le SRT temporaire est nettoyé après succès.
    import glob as _glob

    leftover_post = _glob.glob("/tmp/kut-studio-subtitles-*.srt") + _glob.glob(
        "/var/folders/**/kut-studio-subtitles-*.srt", recursive=True
    )
    assert leftover_post == [], (
        f"SRT temporaires non nettoyés : {leftover_post}"
    )

    # 5. Le fichier de sortie est inspectable.
    streams = _probe_streams(ffprobe, output_path)
    has_video = any(s.get("codec_type") == "video" for s in streams["streams"])
    has_audio = any(s.get("codec_type") == "audio" for s in streams["streams"])
    assert has_video, "Le fichier final doit contenir un flux vidéo"
    assert has_audio, "Le fichier final doit contenir un flux audio"
    duration = float(streams["format"]["duration"])
    assert abs(duration - 4.0) < 0.5, (
        f"Durée attendue ≈ 4s, obtenue {duration:.3f}s"
    )


# ---------------------------------------------------------------------------
# Tâche 13 — Intégration réelle d'un export animé
# ---------------------------------------------------------------------------


def _build_animated_project(video_path: Path, audio_path: Path) -> Project:
    """Projet vidéo + audio 4 s avec une animation d'opacité + position."""
    from core.visual_effects import ClipTransform, TransformKeyframe

    video_asset = MediaAsset(
        id="asset-vid-anim",
        path=str(video_path),
        name="Vid",
        duration=4.0,
        width=160,
        height=90,
        fps=15.0,
        media_type="video",
    )
    audio_asset = MediaAsset(
        id="asset-aud-anim",
        path=str(audio_path),
        name="Tone",
        duration=4.0,
        width=0,
        height=0,
        fps=0.0,
        media_type="audio",
        has_audio=True,
    )
    sub_asset = MediaAsset(
        id="asset-sub-anim",
        path="",
        name="Sub",
        duration=4.0,
        width=0,
        height=0,
        fps=0.0,
        media_type="subtitle",
        has_audio=False,
    )
    video_clip = Clip(
        id="vid-anim",
        asset_id="asset-vid-anim",
        track_id="V1",
        timeline_start=0.0,
        source_in=0.0,
        source_out=4.0,
        # Base : opacité 1.0 + scale 1.0 + position 0.0.
        transform=ClipTransform(scale=1.0, opacity=1.0),
        # Animation d'opacité de 1.0 à 0.4 sur [0, 4] et position_x
        # de 0.0 → 0.5 sur [0, 4].
        transform_keyframes=[
            TransformKeyframe(
                property_name="opacity", time_seconds=0.0, value=1.0
            ),
            TransformKeyframe(
                property_name="opacity", time_seconds=4.0, value=0.4
            ),
            TransformKeyframe(
                property_name="position_x", time_seconds=0.0, value=0.0
            ),
            TransformKeyframe(
                property_name="position_x", time_seconds=4.0, value=0.5
            ),
        ],
    )
    audio_clip = Clip(
        id="aud-anim",
        asset_id="asset-aud-anim",
        track_id="A1",
        timeline_start=0.0,
        source_in=0.0,
        source_out=4.0,
    )
    sub_clip = Clip(
        id="sub-anim",
        asset_id="asset-sub-anim",
        track_id="S1",
        timeline_start=1.0,
        source_in=0.0,
        source_out=2.0,
        text="Animation",
    )
    return Project(
        name="Animated",
        width=160,
        height=90,
        fps=15.0,
        media_assets=[video_asset, audio_asset, sub_asset],
        tracks=[
            Track(id="V1", name="V1", type="video", clips=[video_clip]),
            Track(id="A1", name="A1", type="audio", clips=[audio_clip]),
            Track(id="S1", name="S1", type="subtitle", clips=[sub_clip]),
        ],
    )


def _dump_frame_png(
    ffmpeg: str, path: Path, ts_seconds: float, output: Path
) -> Path | None:
    """Extrait une frame PNG unique à ``ts_seconds`` du fichier ``path``.

    Retourne le chemin du PNG écrit, ou ``None`` si ffmpeg a échoué.
    Utilisé pour comparer les frames init / final d'un rendu animé.
    """
    result = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            str(ts_seconds),
            "-i",
            str(path),
            "-frames:v",
            "1",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        return None
    return output if output.exists() else None


def _probe_frame_color(
    ffmpeg: str, path: Path, ts_seconds: float
) -> bytes:
    """Extrait la frame à ``ts_seconds`` via un dump PNG et retourne les octets.

    Permet de comparer deux frames échantillonnées en bytes : si
    l'animation est effectivement rendue, les bytes diffèrent.
    """
    with tempfile.NamedTemporaryFile(
        prefix=f"frame_{ts_seconds}_",
        suffix=".png",
        delete=False,
    ) as tmp:
        tmp_path = Path(tmp.name)
    try:
        out = _dump_frame_png(ffmpeg, path, ts_seconds, tmp_path)
        if out is None:
            return b""
        return out.read_bytes()
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass


def test_real_ffmpeg_export_with_animated_transform(qtbot, tmp_path):
    """Export réel avec opacité, échelle, rotation et position animées.

    Limites de FFmpeg : les filtres standards ``scale``, ``rotate``,
    ``overlay`` et ``colorchannelmixer`` n'acceptent pas la variable
    ``T`` (temps) dans leurs expressions sur la version installée.
    Kut-Studio utilise donc ``geq`` (expression par pixel) pour
    appliquer l'opacité animée, seule propriété directement
    animable par FFmpeg via une expression.

    Le test vérifie que :
    - l'export réussit (finished_ok) ;
    - la durée est correcte ;
    - le flux vidéo est présent ;
    - la commande contient bien le filtre ``geq`` et l'expression
      interpolée ``if(lt(T\\,t)\\,A\\,B)`` attendue ;
    - aucun fichier temporaire n'est laissé sur disque.
    """
    ffmpeg, ffprobe = _require_ffmpeg()

    # 1. Vidéo rouge 4 s avec tonalité (le moteur s'occupera de l'audio).
    video_path = tmp_path / "anim_src.mp4"
    _generate_color_with_tone(
        ffmpeg, video_path, color="red", tone_freq=440, duration=4.0
    )

    # 2. Construction du projet animé (sans sous-titre pour isoler le test).
    from core.visual_effects import ClipTransform, TransformKeyframe

    video_asset = MediaAsset(
        id="asset-vid-anim",
        path=str(video_path),
        name="Vid",
        duration=4.0,
        width=160,
        height=90,
        fps=15.0,
        media_type="video",
    )
    video_clip = Clip(
        id="vid-anim",
        asset_id="asset-vid-anim",
        track_id="V1",
        timeline_start=0.0,
        source_in=0.0,
        source_out=4.0,
        transform=ClipTransform(
            position_x=0.0, scale=1.0, rotation=0.0, opacity=1.0
        ),
        transform_keyframes=[
            # Opacité : 1.0 → 0.4 sur 4 s (animée via ``geq``).
            TransformKeyframe(property_name="opacity", time_seconds=0.0, value=1.0),
            TransformKeyframe(property_name="opacity", time_seconds=4.0, value=0.4),
            TransformKeyframe(property_name="scale", time_seconds=0.0, value=1.0),
            TransformKeyframe(property_name="scale", time_seconds=4.0, value=0.8),
            TransformKeyframe(property_name="rotation", time_seconds=0.0, value=0.0),
            TransformKeyframe(property_name="rotation", time_seconds=4.0, value=15.0),
            TransformKeyframe(property_name="position_x", time_seconds=0.0, value=0.0),
            TransformKeyframe(property_name="position_x", time_seconds=4.0, value=0.1),
        ],
    )
    project = Project(
        name="Animated",
        width=160,
        height=90,
        fps=15.0,
        media_assets=[video_asset],
        tracks=[
            Track(id="V1", name="V1", type="video", clips=[video_clip]),
        ],
    )
    plan = build_render_plan(project)
    assert any(layer.transform_keyframes for layer in plan.video_layers)

    # 3. Construction de la commande.
    from core.export_engine import (
        ExportEngine,
        ExportFormat,
        ExportPreset,
        ExportRequest,
    )

    output_path = tmp_path / "anim_out.mp4"
    request = ExportRequest(
        render_plan=plan,
        output_path=str(output_path),
        format=ExportFormat.MP4_H264,
        preset=ExportPreset(
            name="Animated", resolution=(160, 90), crf=28, audio_bitrate="96k"
        ),
        fps=15,
    )
    engine = ExportEngine()

    # 4. Présence des filtres animés dans le filter_complex.
    engine._prepare_temporary_files(plan)
    try:
        command = engine._build_command(request)
    finally:
        engine._cleanup_temporary_files()
    filter_index = command.index("-filter_complex")
    filter_complex = command[filter_index + 1]
    # Quand l'opacité est animée, ``geq`` doit apparaître (le
    # ``colorchannelmixer`` standard n'accepte pas ``T``).
    assert "geq=" in filter_complex
    # L'expression d'opacité animée utilise ``if(lt(T\\,t)\\,A\\,B)``
    # (forme avec virgules échappées pour passer -filter_complex).
    assert "lt(T\\," in filter_complex
    assert "scale=w=" in filter_complex
    assert "rotate=" in filter_complex
    assert "lt(t,4.0)" in filter_complex
    assert "overlay=" in filter_complex

    # 5. Exécution réelle de l'engine (sans sous-titre → engine finit
    # l'export même sans libass).
    engine.start(request)
    finished, failed = _wait_for_export(engine, timeout_ms=60000)
    assert not failed, f"Export a échoué : {failed}"
    assert finished, "finished_ok aurait dû être émis"
    assert output_path.exists()

    # 6. Vérifie les flux vidéo (l'audio peut être absent si le projet
    # n'a pas de pistes audio — c'est attendu ici).
    streams = _probe_streams(ffprobe, output_path)
    has_video = any(s.get("codec_type") == "video" for s in streams["streams"])
    assert has_video
    duration = float(streams["format"]["duration"])
    assert abs(duration - 4.0) < 0.5, (
        f"Durée attendue ≈ 4s, obtenue {duration:.3f}s"
    )

    # 7. Aucun fichier temporaire ou média de test ne doit subsister
    # dans le dossier du test ou le /tmp système.
    import glob as _glob

    leftover = _glob.glob("/tmp/kut-studio-subtitles-*.srt")
    assert leftover == [], f"SRT temporaires non nettoyés : {leftover}"
