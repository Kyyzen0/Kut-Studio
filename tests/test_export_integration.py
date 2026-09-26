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
from pathlib import Path

import pytest
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.project_model import Clip, MediaAsset, Project, Track  # noqa: E402
from core.render_plan import build_render_plan  # noqa: E402


FAKE_FFMPEG = ROOT / "tests" / "fixtures" / "fake_ffmpeg.sh"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_ffmpeg_path(monkeypatch):
    """Force le module ``export_engine`` à utiliser notre faux ffmpeg."""
    monkeypatch.setattr("core.export_engine._ffmpeg_path", str(FAKE_FFMPEG))
    return FAKE_FFMPEG


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
        width=1920,
        height=1080,
        fps=30.0,
        media_type="subtitle",
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