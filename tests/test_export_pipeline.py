"""Les étapes d'export partagées (ADR-0005, étape 1) : vérifier, préparer, construire la commande, sans boucle d'événements.

``check_export_request``, ``export_needs_preparation``, ``prepare_export`` et ``build_export_command`` sont ce que
:class:`~core.export_engine.ExportEngine` enchaîne pour l'interface ; un appelant sans interface (ligne de commande, script,
test) les enchaîne dans le même ordre. Ces tests prouvent trois choses : la commande est **la même** pour les deux, un clip
interpolé **a besoin** de la préparation (sans elle, l'export d'un script échouerait là où celui de l'interface réussit), et
ces fonctions **suffisent à exporter** avec le vrai FFmpeg, sans ``QProcess`` ni signal.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from core.export_engine import (
    FILTER_SCRIPT_THRESHOLD,
    ExportCommand,
    ExportEngine,
    ExportFormat,
    ExportPreset,
    ExportRequest,
    build_export_command,
    check_export_request,
    export_needs_preparation,
    prepare_export,
)
from core.flow_cache import FlowCache
from core.project_model import Clip, MediaAsset, Project, Track
from core.render_plan import build_render_plan
from core.retime_graph import RetimeError
from core.retime_layers import PrepareCancelled
from core.time_remapping import TimeInterpolation, TimeRemapping
from core.timeline_operations import add_subtitle_clip

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg absent")

W, H, FPS = 64, 36, 30
FRAMES = 30


@pytest.fixture(scope="module")
def media(tmp_path_factory) -> str:
    path = tmp_path_factory.mktemp("pipeline") / "source.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
         f"testsrc2=s={W}x{H}:r={FPS}:d={FRAMES / FPS},format=yuv420p",
         "-c:v", "libx264", "-crf", "20", "-preset", "ultrafast", str(path)],
        check=True, timeout=60,
    )
    return str(path)


def _project(media_path: str, *, remapping: TimeRemapping | None = None, clips: int = 1) -> Project:
    asset = MediaAsset(id="a", path=media_path, name="m", duration=FRAMES / FPS, width=W, height=H, fps=float(FPS),
                       media_type="video", has_audio=False)
    track = Track("V1", "V1", "video")
    for index in range(clips):
        track.clips.append(Clip(f"c{index}", "a", "V1", index * FRAMES / FPS, 0.0, FRAMES / FPS,
                                time_remapping=remapping or TimeRemapping()))
    return Project("p", width=W, height=H, fps=float(FPS), media_assets=[asset], tracks=[track])


def _request(project: Project, output: Path, **options) -> ExportRequest:
    return ExportRequest(
        render_plan=build_render_plan(project), output_path=str(output), format=ExportFormat.MP4_H264,
        preset=ExportPreset(name="test", resolution=(W, H), crf=28, audio_bitrate="64k"), fps=float(FPS), **options)


# --- Vérification de la requête ---------------------------------------------------------------------------------------


def test_a_plan_with_nothing_to_show_is_refused_before_any_work(tmp_path):
    empty = Project("p", width=W, height=H, fps=float(FPS), tracks=[Track("V1", "V1", "video")])
    with pytest.raises(ValueError, match="Aucun média vidéo"):
        check_export_request(_request(empty, tmp_path / "out.mp4"))


def test_subtitles_need_libass_and_the_refusal_says_so(media, tmp_path, monkeypatch):
    project = _project(media)
    project.tracks.append(Track("S1", "S1", "subtitle"))
    add_subtitle_clip(project, "Bonjour", 0.0, 1.0, "S1")
    request = _request(project, tmp_path / "out.mp4")
    assert request.render_plan.subtitle_cues
    monkeypatch.setattr("core.export_engine._ffmpeg_supports_subtitles", lambda: False)
    with pytest.raises(RuntimeError, match="libass"):
        check_export_request(request)
    monkeypatch.setattr("core.export_engine._ffmpeg_supports_subtitles", lambda: True)
    check_export_request(request)                                   # avec libass : accepté


def test_a_plain_project_passes_the_check_and_needs_no_preparation(media, tmp_path):
    request = _request(_project(media), tmp_path / "out.mp4")
    check_export_request(request)
    assert export_needs_preparation(request) is False


# --- Préparation ------------------------------------------------------------------------------------------------------


def test_a_blended_clip_needs_preparation_and_the_graph_refuses_to_build_without_it(media, tmp_path):
    """C'est la raison d'être de ``prepare_export`` dans l'API partagée : sans elle, la commande ne se construit pas."""
    project = _project(media, remapping=TimeRemapping(speed=0.5, interpolation=TimeInterpolation.BLENDING))
    request = _request(project, tmp_path / "out.mp4")
    assert export_needs_preparation(request) is True
    with pytest.raises(RetimeError):
        build_export_command(request)


def test_a_missing_media_is_not_prepared_and_the_command_refuses_it_clearly(media, tmp_path):
    project = _project(media, remapping=TimeRemapping(speed=0.5, interpolation=TimeInterpolation.BLENDING))
    project.media_assets.clear()                      # « introuvable » : le média a été retiré de la bibliothèque
    request = _request(project, tmp_path / "out.mp4")
    assert request.render_plan.missing_media
    assert export_needs_preparation(request) is False
    with pytest.raises(ValueError, match="Média introuvable"):
        build_export_command(request)


@needs_ffmpeg
def test_preparation_reports_its_progress_and_can_be_cancelled(media, tmp_path):
    project = _project(media, remapping=TimeRemapping(speed=0.5, interpolation=TimeInterpolation.BLENDING))
    request = _request(project, tmp_path / "out.mp4")
    steps: list[tuple[int, int]] = []
    result = prepare_export(request, cache=FlowCache(tmp_path / "flow"), progress=lambda done, total: steps.append((done, total)))
    assert result.streams and result.report.synthesized > 0
    assert steps and steps[-1][0] == steps[-1][1] > 0, "la progression finit à « tout fait »"
    with pytest.raises(PrepareCancelled):
        prepare_export(request, cache=FlowCache(tmp_path / "flow2"), cancelled=lambda: True)


# --- Commande : la même pour l'interface et pour un appelant sans interface ------------------------------------------


def test_the_engine_builds_exactly_the_shared_command(media, tmp_path, qapp):
    request = _request(_project(media), tmp_path / "out.mp4")
    engine = ExportEngine()
    engine._prepare_temporary_files(request.render_plan)
    shared = build_export_command(request, prepared=engine._prepared, subtitle_path=engine._current_srt_path)
    assert isinstance(shared, ExportCommand)
    assert engine._build_command(request) == list(shared.arguments)
    assert engine.last_encoder_choice == shared.encoder
    assert shared.arguments[-1] == str(tmp_path / "out.mp4")


@needs_ffmpeg
def test_with_a_prepared_clip_the_engine_and_the_shared_function_agree_and_the_command_exports(media, tmp_path, qapp):
    """Le même projet, un clip en mélange d'images : préparation partagée, même commande, et FFmpeg l'exécute."""
    project = _project(media, remapping=TimeRemapping(speed=0.5, interpolation=TimeInterpolation.BLENDING))
    output = tmp_path / "out.mp4"
    request = _request(project, output)
    preparation = prepare_export(request, cache=FlowCache(tmp_path / "flow"))

    engine = ExportEngine()
    engine._prepared = dict(preparation.streams)
    engine._prepare_temporary_files(request.render_plan)
    shared = build_export_command(request, prepared=preparation.streams, subtitle_path=engine._current_srt_path)
    assert engine._build_command(request) == list(shared.arguments)

    done = subprocess.run(list(shared.arguments), capture_output=True, timeout=120)
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")[-600:]
    assert output.is_file() and output.stat().st_size > 0
    count = subprocess.run(
        ["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries", "stream=nb_read_frames",
         "-of", "default=nokey=1:noprint_wrappers=1", str(output)], capture_output=True, text=True, timeout=60)
    assert int(count.stdout.strip()) == round(FRAMES / FPS / 0.5 * FPS), "demi-vitesse : deux fois plus d'images"
    engine._cleanup_temporary_files()


# --- Fichiers temporaires et refus ------------------------------------------------------------------------------------


def test_a_long_graph_goes_through_a_script_file_the_caller_can_clean(media, tmp_path):
    project = _project(media, clips=140)
    request = _request(project, tmp_path / "out.mp4")
    own: list[str] = []
    command = build_export_command(request, temporary_files=own)
    try:
        assert len(command.temporary_files) == 1 and own == list(command.temporary_files)
        script = Path(command.temporary_files[0])
        assert script.is_file() and len(script.read_text(encoding="utf-8")) > FILTER_SCRIPT_THRESHOLD
        assert str(script) in command.arguments, "la commande lit le graphe depuis ce fichier"
    finally:
        for path in command.temporary_files:
            if os.path.exists(path):
                os.remove(path)
    # Sans liste fournie, les fichiers ne sont que dans le résultat (rien n'est perdu pour l'appelant).
    again = build_export_command(request)
    try:
        assert len(again.temporary_files) == 1
    finally:
        for path in again.temporary_files:
            os.remove(path)


def test_a_missing_output_folder_is_refused_with_its_name(media, tmp_path):
    request = _request(_project(media), tmp_path / "absent" / "out.mp4")
    with pytest.raises(ValueError, match="dossier de sortie est introuvable"):
        build_export_command(request)


def test_a_short_graph_leaves_no_temporary_file(media, tmp_path):
    command = build_export_command(_request(_project(media), tmp_path / "out.mp4"))
    assert command.temporary_files == ()
