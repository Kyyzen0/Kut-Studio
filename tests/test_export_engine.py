"""Tests pour le moteur d'export basé ``RenderPlan`` (``core.export_engine``).

Ces tests vérifient la construction de la commande FFmpeg et le
traitement des événements ``QProcess``. Le moteur d'export ne lance
aucun processus : les binaires FFmpeg sont mockés via ``monkeypatch``.
"""

from pathlib import Path

import pytest
from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QApplication

from core import export_engine
from core.export_engine import (
    ExportEngine,
    ExportFormat,
    ExportPreset,
    ExportRequest,
    _build_layer_filter,
)
from core.project_model import Clip, MediaAsset, Project, Track
from core.render_plan import RenderPlan, build_render_plan


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    """Crée l'application Qt requise par ``ExportEngine`` (QObject)."""
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def engine(monkeypatch):
    """Retourne un ``ExportEngine`` avec un chemin ffmpeg simulé."""
    monkeypatch.setattr(export_engine.shutil, "which", lambda _: "/usr/bin/ffmpeg")
    monkeypatch.setattr(export_engine, "_ffmpeg_path", "/usr/bin/ffmpeg")
    return ExportEngine()


def _make_project_with_video(
    video_path: str, *, v2_clip: tuple[float, float] | None = None
) -> Project:
    """Construit un projet minimal avec un clip V1 (et optionnellement V2)."""
    asset = MediaAsset(
        id="asset-v",
        path=video_path,
        name="V",
        duration=10.0,
        width=1920,
        height=1080,
        fps=30.0,
        media_type="video",
    )
    v1 = Track(
        id="V1",
        name="V1",
        type="video",
        clips=[
            Clip(
                id="v1-clip",
                asset_id="asset-v",
                track_id="V1",
                timeline_start=0.0,
                source_in=0.0,
                source_out=4.0,
            ),
        ],
    )
    tracks = [v1]
    if v2_clip is not None:
        v2 = Track(
            id="V2",
            name="V2",
            type="video",
            clips=[
                Clip(
                    id="v2-clip",
                    asset_id="asset-v",
                    track_id="V2",
                    timeline_start=v2_clip[0],
                    source_in=0.0,
                    source_out=v2_clip[1] - v2_clip[0],
                ),
            ],
        )
        tracks.append(v2)
    return Project(
        name="Render-Plan-Test",
        width=1920,
        height=1080,
        fps=30.0,
        media_assets=[asset],
        tracks=tracks,
    )


def make_request(
    plan: RenderPlan, tmp_path: Path, export_format: ExportFormat
) -> ExportRequest:
    """Construit un ``ExportRequest`` simple à partir d'un ``RenderPlan``."""
    return ExportRequest(
        render_plan=plan,
        output_path=str(tmp_path / "output.mp4"),
        format=export_format,
        preset=ExportPreset("Haute", (1920, 1080), 18, "192k"),
        fps=30,
    )


# ---------------------------------------------------------------------------
# Construction de la commande : MP4 / H.264
# ---------------------------------------------------------------------------


def test_build_command_mp4_h264_uses_filter_complex(engine, tmp_path):
    """Une commande MP4 H.264 doit utiliser ``-filter_complex`` (pas de concat)."""
    project = _make_project_with_video(str(tmp_path / "source.mp4"))
    plan = build_render_plan(project)
    request = make_request(plan, tmp_path, ExportFormat.MP4_H264)

    command = engine._build_command(request)

    assert "-filter_complex" in command
    assert "-f" not in command or "concat" not in command
    assert "-c:v" in command
    assert command[command.index("-c:v") + 1] == "libx264"
    assert command[-1] == request.output_path
    # L'argument qui suit ``-i`` doit être la source du clip, pas un concat.txt.
    for index in [i for i, arg in enumerate(command) if arg == "-i"]:
        assert not command[index + 1].endswith("concat.txt")


def test_build_command_includes_black_background(engine, tmp_path):
    """Le filter_complex doit contenir un fond noir calibré sur la durée."""
    project = _make_project_with_video(str(tmp_path / "source.mp4"))
    plan = build_render_plan(project)
    request = make_request(plan, tmp_path, ExportFormat.MP4_H264)

    command = engine._build_command(request)

    filter_index = command.index("-filter_complex")
    filter_complex = command[filter_index + 1]
    assert "color=c=black" in filter_complex
    assert "s=1920x1080" in filter_complex
    assert "r=30" in filter_complex
    assert "d=4.0" in filter_complex


def test_build_command_shifts_layer_by_timeline_start(engine, tmp_path):
    """Le filter_complex doit décaler la couche à sa position de timeline."""
    # V1 clip placé à t=2s.
    asset = MediaAsset(
        id="a", path=str(tmp_path / "source.mp4"), name="A",
        duration=10.0, width=1920, height=1080, fps=30.0, media_type="video",
    )
    project = Project(
        name="Shift",
        tracks=[
            Track(
                id="V1", name="V1", type="video",
                clips=[
                    Clip(
                        id="c", asset_id="a", track_id="V1",
                        timeline_start=2.0, source_in=0.0, source_out=3.0,
                    ),
                ],
            ),
        ],
        media_assets=[asset],
    )
    plan = build_render_plan(project)
    request = make_request(plan, tmp_path, ExportFormat.MP4_H264)

    command = engine._build_command(request)

    filter_complex = command[command.index("-filter_complex") + 1]
    assert "setpts=PTS+2.0" in filter_complex
    # Et le trim doit utiliser la plage source.
    assert "trim=start=0.0:end=3.0" in filter_complex


def test_build_command_v2_overlays_after_v1(engine, tmp_path):
    """V2 doit être appliqué après V1 dans la chaîne d'overlays."""
    project = _make_project_with_video(
        str(tmp_path / "source.mp4"), v2_clip=(2.0, 5.0)
    )
    plan = build_render_plan(project)
    request = make_request(plan, tmp_path, ExportFormat.MP4_H264)

    command = engine._build_command(request)

    filter_complex = command[command.index("-filter_complex") + 1]

    # Les couches doivent apparaître dans l'ordre : V1 puis V2.
    v1_pos = filter_complex.find("[0:v]")
    v2_pos = filter_complex.find("[1:v]")
    assert 0 <= v1_pos < v2_pos, (
        "Le clip V1 doit apparaître dans le filter_complex avant V2."
    )

    # Le dernier overlay doit produire ``[vout]`` à partir de la couche V2.
    assert filter_complex.rfind("overlay=eof_action=pass") > v2_pos
    assert "[vout]" in filter_complex


def test_build_command_respects_trim(engine, tmp_path):
    """Les trims ``source_in`` / ``source_out`` doivent apparaître dans le filtre."""
    asset = MediaAsset(
        id="a", path=str(tmp_path / "source.mp4"), name="A",
        duration=10.0, width=1920, height=1080, fps=30.0, media_type="video",
    )
    project = Project(
        name="Trim",
        tracks=[
            Track(
                id="V1", name="V1", type="video",
                clips=[
                    Clip(
                        id="c", asset_id="a", track_id="V1",
                        timeline_start=0.0, source_in=2.0, source_out=5.5,
                    ),
                ],
            ),
        ],
        media_assets=[asset],
    )
    plan = build_render_plan(project)
    request = make_request(plan, tmp_path, ExportFormat.MP4_H264)

    command = engine._build_command(request)
    filter_complex = command[command.index("-filter_complex") + 1]
    assert "trim=start=2.0:end=5.5" in filter_complex


def test_build_command_with_gap_uses_background_duration(engine, tmp_path):
    """Un trou entre deux clips V1 ne raccourcit pas la durée totale."""
    asset = MediaAsset(
        id="a", path=str(tmp_path / "source.mp4"), name="A",
        duration=10.0, width=1920, height=1080, fps=30.0, media_type="video",
    )
    project = Project(
        name="Gap",
        tracks=[
            Track(
                id="V1", name="V1", type="video",
                clips=[
                    Clip(
                        id="first", asset_id="a", track_id="V1",
                        timeline_start=0.0, source_in=0.0, source_out=2.0,
                    ),
                    Clip(
                        id="second", asset_id="a", track_id="V1",
                        timeline_start=5.0, source_in=0.0, source_out=2.0,
                    ),
                ],
            ),
        ],
        media_assets=[asset],
    )
    plan = build_render_plan(project)
    # timeline_duration = max(2, 7) = 7s → le fond noir doit durer 7s.
    assert plan.duration == pytest.approx(7.0)

    request = make_request(plan, tmp_path, ExportFormat.MP4_H264)
    command = engine._build_command(request)
    filter_complex = command[command.index("-filter_complex") + 1]
    assert "d=7.0" in filter_complex


def test_build_command_has_no_audio(engine, tmp_path):
    """Le filtre de sortie doit être muet (``-an``) à ce stade."""
    project = _make_project_with_video(str(tmp_path / "source.mp4"))
    plan = build_render_plan(project)
    request = make_request(plan, tmp_path, ExportFormat.MP4_H264)

    command = engine._build_command(request)

    assert "-an" in command


def test_build_command_mov_prores(engine, tmp_path):
    """Une commande MOV ProRes doit utiliser le codec prores_ks."""
    project = _make_project_with_video(str(tmp_path / "source.mp4"))
    plan = build_render_plan(project)
    request = make_request(plan, tmp_path, ExportFormat.MOV_PRORES)

    command = engine._build_command(request)

    assert command[command.index("-c:v") + 1] == "prores_ks"
    assert command[command.index("-profile:v") + 1] == "3"


# ---------------------------------------------------------------------------
# Helpers privés (testés directement)
# ---------------------------------------------------------------------------


def test_build_layer_filter_includes_required_chain():
    """Le filtre d'une couche doit appliquer trim, scale, pad, fps, setpts."""
    from core.render_plan import RenderLayer

    layer = RenderLayer(
        clip_id="c", asset_id="a", track_id="V1", track_index=0,
        source_path="/tmp/x.mp4", source_in=1.0, source_out=4.0,
        timeline_start=2.5, timeline_end=5.5,
    )
    filter_str = _build_layer_filter(0, layer, 640, 360, 30)

    assert filter_str.startswith("[0:v]")
    assert "trim=start=1.0:end=4.0" in filter_str
    assert "setpts=PTS-STARTPTS" in filter_str
    assert "scale=640:360:force_original_aspect_ratio=decrease" in filter_str
    assert "pad=640:360:(ow-iw)/2:(oh-ih)/2:black" in filter_str
    assert "fps=30" in filter_str
    assert "setpts=PTS+2.5/TB" in filter_str
    assert filter_str.endswith("[v0]")


def test_build_filter_complex_returns_output_label():
    """Le filter_complex doit déclarer un label de sortie exploitable."""
    plan = RenderPlan(
        width=320, height=240, fps=30.0, duration=4.0,
        video_layers=(),
    )
    filter_complex, output_label = ExportEngine._build_filter_complex(
        plan, 320, 240, 30
    )
    assert output_label == "bg"
    assert "color=c=black" in filter_complex


# ---------------------------------------------------------------------------
# Validation de la requête
# ---------------------------------------------------------------------------


def test_export_request_validates_fps(engine, tmp_path):
    """Un ``fps`` nul ou négatif doit être rejeté."""
    plan = RenderPlan(
        width=1920, height=1080, fps=30.0, duration=4.0,
        video_layers=(),
    )
    with pytest.raises(ValueError, match="fréquence"):
        ExportRequest(
            render_plan=plan,
            output_path=str(tmp_path / "out.mp4"),
            format=ExportFormat.MP4_H264,
            preset=ExportPreset("X", (1920, 1080), 18, "192k"),
            fps=0,
        )


def test_export_request_validates_resolution(engine, tmp_path):
    """Une résolution nulle ou négative doit être rejetée."""
    plan = RenderPlan(
        width=0, height=0, fps=30.0, duration=4.0,
        video_layers=(),
    )
    with pytest.raises(ValueError, match="résolution"):
        ExportRequest(
            render_plan=plan,
            output_path=str(tmp_path / "out.mp4"),
            format=ExportFormat.MP4_H264,
            preset=ExportPreset("X", (0, 0), 18, "192k"),
            fps=30,
        )


# ---------------------------------------------------------------------------
# Progression et cycle de vie
# ---------------------------------------------------------------------------


def test_parse_progress_valid(engine):
    """Une ligne ``out_time_ms`` valide donne un pourcentage borné."""
    engine._duration_seconds = 10.0

    assert engine._parse_progress("out_time_ms=5000000") == 50


def test_parse_progress_invalid(engine):
    """Une ligne non reconnue ne produit pas de pourcentage."""
    engine._duration_seconds = 10.0

    assert engine._parse_progress("random output") is None


def test_process_error_does_not_emit_failed_when_cancel_requested(engine):
    """Une erreur QProcess pendant une annulation ne doit pas lever ``failed``."""
    engine._cancel_requested = True
    messages: list[str] = []
    engine.failed.connect(messages.append)

    engine._process_error(QProcess.Crashed)

    assert messages == []


def test_start_emits_failed_when_plan_has_no_layers(engine, tmp_path, monkeypatch):
    """Un plan sans couches vidéo doit émettre ``failed`` synchroniquement."""
    monkeypatch.setattr(export_engine.shutil, "which", lambda _: "/usr/bin/ffmpeg")
    monkeypatch.setattr(export_engine, "_ffmpeg_path", "/usr/bin/ffmpeg")

    engine = ExportEngine()
    failed_messages: list[str] = []
    engine.failed.connect(failed_messages.append)

    plan = RenderPlan(
        width=1920, height=1080, fps=30.0, duration=0.0,
        video_layers=(),
    )
    request = ExportRequest(
        render_plan=plan,
        output_path=str(tmp_path / "out.mp4"),
        format=ExportFormat.MP4_H264,
        preset=ExportPreset("X", (1920, 1080), 18, "192k"),
        fps=30,
    )
    engine.start(request)

    assert failed_messages, "failed doit être émis quand le plan est vide."
    assert "média" in failed_messages[0].lower() or "video" in failed_messages[0].lower()