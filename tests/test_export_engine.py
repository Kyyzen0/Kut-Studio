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
from core.visual_effects import ClipTransform, TransformKeyframe


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


def test_animated_transform_uses_filter_specific_time_variables(engine, tmp_path):
    """Scale et rotation utilisent ``t`` ; l'opacité via geq garde ``T``."""
    project = _make_project_with_video(str(tmp_path / "source.mp4"))
    clip = project.tracks[0].clips[0]
    clip.transform = ClipTransform(scale=1.0, rotation=0.0, opacity=1.0)
    clip.transform_keyframes = [
        TransformKeyframe("scale", 1.0, 1.5),
        TransformKeyframe("rotation", 1.0, 45.0),
        TransformKeyframe("opacity", 1.0, 0.5),
    ]
    request = make_request(build_render_plan(project), tmp_path, ExportFormat.MP4_H264)

    command = engine._build_command(request)
    filter_complex = command[command.index("-filter_complex") + 1]

    assert filter_complex.count("lt(t,1.0)") >= 2
    assert "rotate=a='if(lt(t,1.0)" in filter_complex
    assert "geq=" in filter_complex
    assert "lt(T\\," in filter_complex


def test_position_animation_uses_export_size_and_clip_local_time(engine, tmp_path):
    """Les coordonnées animées suivent le préréglage et le début du clip."""
    project = _make_project_with_video(str(tmp_path / "source.mp4"))
    clip = project.tracks[0].clips[0]
    clip.timeline_start = 2.0
    clip.transform = ClipTransform(position_x=0.0, position_y=0.0)
    clip.transform_keyframes = [
        TransformKeyframe("position_x", 1.0, 0.5),
    ]
    plan = build_render_plan(project)
    request = ExportRequest(
        render_plan=plan,
        output_path=str(tmp_path / "output.mp4"),
        format=ExportFormat.MP4_H264,
        preset=ExportPreset("Petit", (320, 180), 18, "192k"),
        fps=30,
    )

    command = engine._build_command(request)
    filter_complex = command[command.index("-filter_complex") + 1]

    assert "lt((t-2.0),1.0)" in filter_complex
    assert "*320.0" in filter_complex
    assert "*1920.0" not in filter_complex


def test_build_command_v2_overlays_after_v1(engine, tmp_path):
    """V2 doit être appliqué après V1 dans la chaîne d'overlays."""
    project = _make_project_with_video(
        str(tmp_path / "source.mp4"), v2_clip=(2.0, 5.0)
    )
    plan = build_render_plan(project)
    request = make_request(plan, tmp_path, ExportFormat.MP4_H264)

    command = engine._build_command(request)

    filter_complex = command[command.index("-filter_complex") + 1]

    # Les couches vidéo sont numérotées ``[v0]`` puis ``[v1]`` selon
    # l'ordre des pistes (V1 avant V2). Les clips d'une même piste se
    # partagent le même flux d'entrée via le dédoublonnage.
    v1_pos = filter_complex.find("[v0]")
    v2_pos = filter_complex.find("[v1]")
    assert 0 <= v1_pos < v2_pos, (
        "Le clip V1 doit apparaître dans le filter_complex avant V2."
    )

    # Le dernier overlay doit produire ``[vout]`` à partir de la couche V2.
    assert filter_complex.rfind("eof_action=pass") > v2_pos
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


def test_build_command_includes_audio_mapping(engine, tmp_path):
    """La commande expose un flux audio AAC stéréo 48 kHz."""
    project = _make_project_with_video(str(tmp_path / "source.mp4"))
    plan = build_render_plan(project)
    request = make_request(plan, tmp_path, ExportFormat.MP4_H264)

    command = engine._build_command(request)

    # Plus de ``-an`` : l'export est désormais audio-inclusif.
    assert "-an" not in command
    # Le flux audio est mappé et encodé en AAC stéréo 48 kHz.
    assert "-map" in command
    assert any(arg == "aac" for arg in command)
    audio_map_index = command.index("-map") + 1
    assert command.count("-map") >= 2
    second_map_index = command.index("-map", audio_map_index) + 1
    assert command[second_map_index].startswith("[")


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
    filter_str = _build_layer_filter(0, layer, 0, 640, 360, 30)

    assert filter_str.startswith("[0:v]")
    assert "trim=start=1.0:end=4.0" in filter_str
    assert "setpts=PTS-STARTPTS" in filter_str
    assert "scale=640:360:force_original_aspect_ratio=decrease" in filter_str
    assert "pad=640:360:(ow-iw)/2:(oh-ih)/2:black" in filter_str
    assert "fps=30" in filter_str
    assert "setpts=PTS+2.5/TB" in filter_str
    assert filter_str.endswith("[v0]")


def test_build_filter_complex_returns_output_labels_and_inputs():
    """Le filter_complex doit déclarer les labels vidéo + audio + les inputs."""
    plan = RenderPlan(
        width=320, height=240, fps=30.0, duration=4.0,
        video_layers=(),
    )
    filter_complex, video_label, audio_label, input_paths = (
        ExportEngine._build_filter_complex(plan, 320, 240, 30, None)
    )
    assert video_label == "bg"
    assert audio_label == "aout"
    assert input_paths == []
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


def test_start_resets_partial_progress_buffer(engine, tmp_path, monkeypatch):
    """La sortie partielle d'un export précédent ne fuit pas vers le suivant."""
    project = _make_project_with_video(str(tmp_path / "source.mp4"))
    request = make_request(build_render_plan(project), tmp_path, ExportFormat.MP4_H264)
    engine._progress_buffer = "out_time_ms=500"
    monkeypatch.setattr(engine._process, "start", lambda *_: None)

    engine.start(request)

    assert engine._progress_buffer == ""


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


# ---------------------------------------------------------------------------
# Audio — tâche 10
# ---------------------------------------------------------------------------


def _audio_asset(asset_id: str = "asset-audio", path: str = "/tmp/song.mp3", duration: float = 30.0):
    """Construit un ``MediaAsset`` audio conforme à la validation."""
    return MediaAsset(
        id=asset_id,
        path=path,
        name="Song",
        duration=duration,
        width=0,
        height=0,
        fps=0.0,
        media_type="audio",
        has_audio=True,
    )


def _video_with_audio_asset(asset_id: str = "asset-vid", path: str = "/tmp/clip.mp4", duration: float = 5.0):
    """Construit un ``MediaAsset`` vidéo avec ``has_audio=True``."""
    return MediaAsset(
        id=asset_id,
        path=path,
        name="Clip",
        duration=duration,
        width=1920,
        height=1080,
        fps=30.0,
        media_type="video",
        has_audio=True,
    )


def test_filter_complex_contains_silent_source(engine, tmp_path):
    """Le filter_complex doit inclure une source silencieuse ``aevalsrc``."""
    asset = _video_with_audio_asset(path=str(tmp_path / "v.mp4"))
    project = Project(
        name="Silent",
        tracks=[Track(id="V1", name="V1", type="video", clips=[
            Clip(id="c", asset_id="asset-vid", track_id="V1",
                 timeline_start=0.0, source_in=0.0, source_out=5.0),
        ])],
        media_assets=[asset],
    )
    plan = build_render_plan(project)
    request = make_request(plan, tmp_path, ExportFormat.MP4_H264)

    command = engine._build_command(request)
    filter_complex = command[command.index("-filter_complex") + 1]

    assert "aevalsrc=0|0" in filter_complex
    assert "[silent_base]" in filter_complex


def test_filter_complex_includes_audio_layer_filters(engine, tmp_path):
    """Pour chaque ``AudioLayer``, le filter_complex doit appliquer ``atrim``."""
    audio = _audio_asset(path=str(tmp_path / "song.mp3"), duration=20.0)
    project = Project(
        name="Audio",
        tracks=[Track(id="A1", name="A1", type="audio", clips=[
            Clip(id="music", asset_id="asset-audio", track_id="A1",
                 timeline_start=2.0, source_in=1.0, source_out=6.0),
        ])],
        media_assets=[audio],
    )
    plan = build_render_plan(project)
    request = make_request(plan, tmp_path, ExportFormat.MP4_H264)

    command = engine._build_command(request)
    filter_complex = command[command.index("-filter_complex") + 1]

    # atrim + décalage temporel présents.
    assert "atrim=start=1.0:end=6.0" in filter_complex
    assert "asetpts=PTS+2.0/TB" in filter_complex
    # L'amix fusionne la base silencieuse avec le clip.
    assert "amix=inputs=" in filter_complex


def test_command_no_longer_uses_an(engine, tmp_path):
    """``-an`` ne doit plus apparaître : l'export est désormais audio-inclusif."""
    project = _make_project_with_video(str(tmp_path / "source.mp4"))
    plan = build_render_plan(project)
    request = make_request(plan, tmp_path, ExportFormat.MP4_H264)

    command = engine._build_command(request)

    assert "-an" not in command
    assert "aac" in command
    # -map est présent au moins deux fois : vidéo + audio.
    assert command.count("-map") >= 2


def test_export_without_audio_still_has_silent_audio_track(engine, tmp_path):
    """Une vidéo sans flux audio produit une piste audio silencieuse."""
    asset_no_audio = MediaAsset(
        id="v", path=str(tmp_path / "silent.mp4"), name="V",
        duration=4.0, width=1920, height=1080, fps=30.0,
        media_type="video", has_audio=False,
    )
    project = Project(
        name="NoAudio",
        tracks=[Track(id="V1", name="V1", type="video", clips=[
            Clip(id="c", asset_id="v", track_id="V1",
                 timeline_start=0.0, source_in=0.0, source_out=4.0),
        ])],
        media_assets=[asset_no_audio],
    )
    plan = build_render_plan(project)

    # Pas d'AudioLayer car has_audio=False.
    assert plan.audio_layers == ()

    request = make_request(plan, tmp_path, ExportFormat.MP4_H264)
    command = engine._build_command(request)
    filter_complex = command[command.index("-filter_complex") + 1]

    # La base silencieuse est néanmoins créée et mappée vers ``aout``.
    assert "[silent_base]" in filter_complex
    assert "[aout]" in filter_complex
    # Et la commande expose un ``-map`` vers ``[aout]``.
    map_indices = [i for i, arg in enumerate(command) if arg == "-map"]
    audio_map = command[map_indices[1] + 1]
    assert audio_map == "[aout]"


def test_command_uses_audio_bitrate_from_preset(engine, tmp_path):
    """Le réglage de débit audio du préréglage est transmis à FFmpeg."""
    project = _make_project_with_video(str(tmp_path / "source.mp4"))
    plan = build_render_plan(project)
    request = ExportRequest(
        render_plan=plan,
        output_path=str(tmp_path / "output.mp4"),
        format=ExportFormat.MP4_H264,
        preset=ExportPreset("Audio", (1920, 1080), 18, "96k"),
    )

    command = engine._build_command(request)
    bitrate_index = command.index("-b:a")
    assert command[bitrate_index + 1] == "96k"


def test_audio_layer_shift_via_setpts(engine, tmp_path):
    """Un clip audio placé plus tard est décalé via ``setpts=PTS+timeline_start/TB``."""
    audio = _audio_asset(path=str(tmp_path / "song.mp3"), duration=10.0)
    project = Project(
        name="Shift",
        tracks=[Track(id="A1", name="A1", type="audio", clips=[
            Clip(id="late", asset_id="asset-audio", track_id="A1",
                 timeline_start=3.5, source_in=0.0, source_out=2.0),
        ])],
        media_assets=[audio],
    )
    plan = build_render_plan(project)
    request = make_request(plan, tmp_path, ExportFormat.MP4_H264)

    command = engine._build_command(request)
    filter_complex = command[command.index("-filter_complex") + 1]

    assert "asetpts=PTS+3.5/TB" in filter_complex


def test_audio_and_video_share_same_duration(engine, tmp_path):
    """La durée finale audio et vidéo est identique (timeline_duration)."""
    audio = _audio_asset(path=str(tmp_path / "song.mp3"), duration=10.0)
    video = _video_with_audio_asset(path=str(tmp_path / "v.mp4"), duration=4.0)
    project = Project(
        name="SameDuration",
        tracks=[
            Track(id="V1", name="V1", type="video", clips=[
                Clip(id="v", asset_id="asset-vid", track_id="V1",
                     timeline_start=0.0, source_in=0.0, source_out=4.0),
            ]),
            Track(id="A1", name="A1", type="audio", clips=[
                Clip(id="a", asset_id="asset-audio", track_id="A1",
                     timeline_start=0.0, source_in=0.0, source_out=10.0),
            ]),
        ],
        media_assets=[video, audio],
    )
    plan = build_render_plan(project)
    # timeline_duration = max(4, 10) = 10.
    assert plan.duration == pytest.approx(10.0)

    request = make_request(plan, tmp_path, ExportFormat.MP4_H264)
    command = engine._build_command(request)
    filter_complex = command[command.index("-filter_complex") + 1]

    # Le fond noir et la base silencieuse couvrent tous deux 10s.
    assert "d=10.0" in filter_complex
    assert "duration=10.0" in filter_complex


# ---------------------------------------------------------------------------
# Sous-titres — tâche 11
# ---------------------------------------------------------------------------


def _subtitle_asset(asset_id: str = "asset-sub", duration: float = 5.0):
    return MediaAsset(
        id=asset_id,
        path="",
        name="Sous-titre",
        duration=duration,
        width=0,
        height=0,
        fps=0.0,
        media_type="subtitle",
        has_audio=False,
    )


def _video_for_subtitle_export(path: str):
    return MediaAsset(
        id="asset-v",
        path=path,
        name="V",
        duration=5.0,
        width=160,
        height=90,
        fps=15.0,
        media_type="video",
        has_audio=False,
    )


def test_filter_complex_includes_subtitles_filter(engine, tmp_path):
    """Le filtre ``subtitles=`` apparaît dans le filter_complex."""
    video = _video_for_subtitle_export(str(tmp_path / "v.mp4"))
    sub = _subtitle_asset()
    project = Project(
        name="Sub",
        tracks=[
            Track(id="V1", name="V1", type="video", clips=[
                Clip(id="v", asset_id="asset-v", track_id="V1",
                     timeline_start=0.0, source_in=0.0, source_out=4.0),
            ]),
            Track(id="S1", name="S1", type="subtitle", clips=[
                Clip(id="s", asset_id="asset-sub", track_id="S1",
                     timeline_start=0.0, source_in=0.0, source_out=2.0,
                     text="Bonjour"),
            ]),
        ],
        media_assets=[video, sub],
    )
    plan = build_render_plan(project)
    request = make_request(plan, tmp_path, ExportFormat.MP4_H264)

    # ``_prepare_temporary_files`` doit être appelé avant ``_build_command``.
    engine._prepare_temporary_files(plan)
    try:
        command = engine._build_command(request)
    finally:
        engine._cleanup_temporary_files()

    filter_complex = command[command.index("-filter_complex") + 1]
    assert "subtitles=" in filter_complex
    assert "force_style=" in filter_complex
    assert "[vfinal]" in filter_complex


def test_filter_complex_omits_subtitles_when_no_cues(engine, tmp_path):
    """Sans sous-titre actif, le filtre ``subtitles=`` n'est pas appliqué."""
    project = _make_project_with_video(str(tmp_path / "v.mp4"))
    plan = build_render_plan(project)
    assert plan.subtitle_cues == ()

    request = make_request(plan, tmp_path, ExportFormat.MP4_H264)
    command = engine._build_command(request)
    filter_complex = command[command.index("-filter_complex") + 1]
    assert "subtitles=" not in filter_complex


def test_no_temporary_srt_leftover_when_plan_has_no_subtitles(
    engine, tmp_path, monkeypatch
) -> None:
    """Sans sous-titres, aucun fichier SRT temporaire n'est créé."""
    import tempfile as _tempfile

    project = _make_project_with_video(str(tmp_path / "v.mp4"))
    plan = build_render_plan(project)

    monkeypatch.setattr(_tempfile, "tempdir", str(tmp_path))
    engine._prepare_temporary_files(plan)
    engine._cleanup_temporary_files()

    leftover = list(tmp_path.glob("kut-studio-subtitles-*.srt"))
    assert leftover == []


def test_temporary_srt_file_is_cleaned_up_after_start_failure(
    engine, tmp_path, monkeypatch
) -> None:
    """Si la construction de la commande échoue, le SRT temp est nettoyé."""
    import tempfile as _tempfile

    monkeypatch.setattr(_tempfile, "tempdir", str(tmp_path))
    sub = _subtitle_asset()
    video = _video_for_subtitle_export(str(tmp_path / "v.mp4"))
    project = Project(
        name="Boom",
        tracks=[
            Track(id="V1", name="V1", type="video", clips=[
                Clip(id="v", asset_id="asset-v", track_id="V1",
                     timeline_start=0.0, source_in=0.0, source_out=4.0),
            ]),
            Track(id="S1", name="S1", type="subtitle", clips=[
                Clip(id="s", asset_id="asset-sub", track_id="S1",
                     timeline_start=0.0, source_in=0.0, source_out=2.0,
                     text="Hello"),
            ]),
        ],
        media_assets=[video, sub],
    )
    plan = build_render_plan(project)

    # Préparation manuelle.
    engine._prepare_temporary_files(plan)
    created_files = list(tmp_path.glob("kut-studio-subtitles-*.srt"))
    assert len(created_files) == 1

    # On force la commande à échouer (dossier de sortie invalide).
    request = make_request(
        plan, tmp_path / "no_such_dir" / "out.mp4", ExportFormat.MP4_H264,
    )

    # ``start`` doit lever via ``failed`` (synchrone) et nettoyer.
    failed_messages: list[str] = []
    engine.failed.connect(failed_messages.append)
    engine.start(request)
    assert failed_messages, "Le moteur doit émettre failed en cas d'erreur"

    leftover = list(tmp_path.glob("kut-studio-subtitles-*.srt"))
    assert leftover == [], (
        "Aucun SRT temporaire ne doit subsister après un échec : "
        f"{leftover}"
    )


def test_srt_temporary_file_contains_formatted_cues(engine, tmp_path, monkeypatch):
    """Le SRT temporaire est bien formé et lisible."""
    import tempfile as _tempfile
    from core.subtitle_io import load_srt

    monkeypatch.setattr(_tempfile, "tempdir", str(tmp_path))
    sub = _subtitle_asset()
    video = _video_for_subtitle_export(str(tmp_path / "v.mp4"))
    project = Project(
        name="SubCues",
        tracks=[
            Track(id="V1", name="V1", type="video", clips=[
                Clip(id="v", asset_id="asset-v", track_id="V1",
                     timeline_start=0.0, source_in=0.0, source_out=4.0),
            ]),
            Track(id="S1", name="S1", type="subtitle", clips=[
                Clip(id="s1", asset_id="asset-sub", track_id="S1",
                     timeline_start=0.0, source_in=0.0, source_out=1.0,
                     text="Bonjour"),
                Clip(id="s2", asset_id="asset-sub", track_id="S1",
                     timeline_start=2.0, source_in=0.0, source_out=1.0,
                     text="Au revoir"),
            ]),
        ],
        media_assets=[video, sub],
    )
    plan = build_render_plan(project)

    engine._prepare_temporary_files(plan)
    try:
        temp_files = list(tmp_path.glob("kut-studio-subtitles-*.srt"))
        assert len(temp_files) == 1
        cues = load_srt(str(temp_files[0]))
        assert [c.text for c in cues] == ["Bonjour", "Au revoir"]
    finally:
        engine._cleanup_temporary_files()


def test_start_emits_failed_when_ffmpeg_lacks_subtitles_filter(
    qtbot, tmp_path, monkeypatch
) -> None:
    """Si FFmpeg n'a pas libass, ``start`` émet ``failed`` avec un message clair."""
    import core.export_engine as engine_module
    from core.export_engine import (
        ExportEngine,
        ExportFormat,
        ExportPreset,
        ExportRequest,
    )

    monkeypatch.setattr(
        engine_module, "_ffmpeg_supports_subtitles", lambda: False
    )
    monkeypatch.setattr(engine_module.shutil, "which", lambda _: "/usr/bin/ffmpeg")

    sub = _subtitle_asset()
    video = _video_for_subtitle_export(str(tmp_path / "v.mp4"))
    project = Project(
        name="NoLibass",
        tracks=[
            Track(id="V1", name="V1", type="video", clips=[
                Clip(id="v", asset_id="asset-v", track_id="V1",
                     timeline_start=0.0, source_in=0.0, source_out=4.0),
            ]),
            Track(id="S1", name="S1", type="subtitle", clips=[
                Clip(id="s", asset_id="asset-sub", track_id="S1",
                     timeline_start=0.0, source_in=0.0, source_out=2.0,
                     text="Bonjour"),
            ]),
        ],
        media_assets=[video, sub],
    )
    plan = build_render_plan(project)
    request = ExportRequest(
        render_plan=plan,
        output_path=str(tmp_path / "out.mp4"),
        format=ExportFormat.MP4_H264,
        preset=ExportPreset(
            name="X", resolution=(160, 90), crf=18, audio_bitrate="192k"
        ),
        fps=15,
    )

    engine = ExportEngine()
    failed: list[str] = []
    engine.failed.connect(failed.append)
    engine.start(request)

    assert failed, "failed doit être émis quand libass manque"
    assert "libass" in failed[0].lower()
