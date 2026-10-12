"""L'exécuteur d'export sans interface (``core/export_runner.py``, ADR-0005 étape 1) avec le vrai FFmpeg.

Un export complet, sans ``QProcess`` ni boucle d'événements : réussite (progression qui ne recule pas, fichier lisible), échec
(message de FFmpeg, aucun fichier tronqué), annulation (FFmpeg tué), repli sur le CPU d'un encodeur matériel qui échoue,
images intermédiaires, et le fichier final atomique.
"""

from __future__ import annotations

import shutil
import subprocess
import threading
from dataclasses import replace
from pathlib import Path

import pytest

import core.export_engine as engine_module
from core.export_engine import ExportFormat, ExportPreset, ExportRequest
from core.export_runner import ExportStatus, partial_output_path, run_export
from core.flow_cache import FlowCache
from core.hardware_encoding import HardwareEncoder
from core.project_model import Clip, MediaAsset, Project, Track
from core.render_plan import build_render_plan
from core.time_remapping import TimeInterpolation, TimeRemapping
from core.video_encoders import EncoderChoice

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg absent")

W, H, FPS = 64, 36, 30


@pytest.fixture(scope="module")
def media(tmp_path_factory) -> str:
    path = tmp_path_factory.mktemp("runner") / "source.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s={W}x{H}:r={FPS}:d=1,format=yuv420p",
         "-c:v", "libx264", "-crf", "20", "-preset", "ultrafast", str(path)], check=True, timeout=60)
    return str(path)


@pytest.fixture(scope="module")
def long_media(tmp_path_factory) -> str:
    """Assez gros et long pour qu'un export dure de quoi être annulé en cours de route."""
    path = tmp_path_factory.mktemp("runner-long") / "long.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=1280x720:r=30:d=20,format=yuv420p",
         "-c:v", "libx264", "-crf", "30", "-preset", "ultrafast", str(path)], check=True, timeout=120)
    return str(path)


def _project(path: str, *, seconds: float = 1.0, size=(W, H), remapping: TimeRemapping | None = None) -> Project:
    asset = MediaAsset("a", path, "m", seconds, size[0], size[1], float(FPS), "video", has_audio=False)
    clip = Clip("c", "a", "V1", 0.0, 0.0, seconds, time_remapping=remapping or TimeRemapping())
    return Project("p", width=size[0], height=size[1], fps=float(FPS), media_assets=[asset],
                   tracks=[Track("V1", "V1", "video", clips=[clip])])


def _request(project: Project, output: Path, *, size=(W, H), preset_name: str = "medium", **options) -> ExportRequest:
    return ExportRequest(
        render_plan=build_render_plan(project), output_path=str(output), format=ExportFormat.MP4_H264,
        preset=ExportPreset(name="test", resolution=size, crf=30, audio_bitrate="64k"), fps=float(FPS), **options)


def _frames(path: Path) -> int:
    done = subprocess.run(
        ["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries", "stream=nb_read_frames",
         "-of", "default=nokey=1:noprint_wrappers=1", str(path)], capture_output=True, text=True, timeout=60)
    return int(done.stdout.strip())


def _hidden_partials(folder: Path) -> list[str]:
    return [item.name for item in folder.iterdir() if ".partial" in item.name]


# --- Réussite ---------------------------------------------------------------------------------------------------------


def test_a_successful_export_reports_monotonic_progress_ends_at_100_and_leaves_one_file(media, tmp_path):
    output = tmp_path / "out.mp4"
    values: list[int] = []
    messages: list[str] = []
    encoders: list[EncoderChoice] = []
    result = run_export(_request(_project(media), output), progress=values.append, status=messages.append,
                        on_encoder=encoders.append)
    assert result.ok and result.status is ExportStatus.OK and result.message == ""
    assert output.is_file() and _frames(output) == FPS
    assert values == sorted(values) and values[-1] == 100 and max(values[:-1]) <= 99, "croissante, 100 seulement à la fin"
    assert messages[-1] == "Export terminé"
    assert len(encoders) == 1 and result.encoder == encoders[0] and not result.encoder.is_hardware
    assert result.fallback_reason == "" and result.preparation is None
    assert sorted(item.name for item in tmp_path.iterdir()) == ["out.mp4"], "ni .partial ni autre fichier à côté"


def test_the_callbacks_run_on_the_callers_thread(media, tmp_path):
    caller = threading.current_thread()
    seen: set[threading.Thread] = set()
    run_export(_request(_project(media), tmp_path / "out.mp4"),
               progress=lambda _value: seen.add(threading.current_thread()),
               status=lambda _text: seen.add(threading.current_thread()))
    assert seen == {caller}


def test_a_blended_clip_is_prepared_first_and_the_progress_covers_both_phases(media, tmp_path):
    project = _project(media, remapping=TimeRemapping(speed=0.5, interpolation=TimeInterpolation.BLENDING))
    output = tmp_path / "slow.mp4"
    values: list[int] = []
    messages: list[str] = []
    result = run_export(_request(project, output), progress=values.append, status=messages.append,
                        flow_cache=FlowCache(tmp_path / "flow"))
    assert result.ok, result.message
    assert "Calcul des images intermédiaires..." in messages
    assert result.preparation is not None and result.preparation.synthesized > 0
    assert _frames(output) == 2 * FPS, "demi-vitesse : deux fois plus d'images"
    assert values == sorted(values), "la barre ne recule jamais d'une phase à l'autre"
    assert any(v >= 80 for v in values[:-1]), "les images intermédiaires remplissent d'abord les 80 premiers %"
    assert values[-1] == 100


# --- Échec ------------------------------------------------------------------------------------------------------------


def test_a_media_ffmpeg_cannot_read_fails_with_its_message_and_leaves_no_file(tmp_path):
    broken = tmp_path / "broken.mp4"
    broken.write_text("ce n'est pas une vidéo", encoding="utf-8")
    output = tmp_path / "out.mp4"
    result = run_export(_request(_project(str(broken)), output))
    assert result.status is ExportStatus.FAILED and not result.ok
    assert result.message.strip(), "le message de FFmpeg est rendu à l'appelant"
    assert not output.exists(), "aucun fichier sous le nom demandé"
    assert _hidden_partials(tmp_path) == [], "ni fichier intermédiaire laissé derrière"


def test_a_previous_file_is_not_overwritten_by_a_failed_export(tmp_path):
    output = tmp_path / "out.mp4"
    output.write_bytes(b"version precedente")
    broken = tmp_path / "broken.mp4"
    broken.write_text("rien", encoding="utf-8")
    assert not run_export(_request(_project(str(broken)), output)).ok
    assert output.read_bytes() == b"version precedente"


def test_requests_that_cannot_succeed_are_refused_without_running_ffmpeg(media, tmp_path):
    empty = Project("p", width=W, height=H, fps=float(FPS), tracks=[Track("V1", "V1", "video")])
    refused = run_export(_request(empty, tmp_path / "out.mp4"))
    assert refused.status is ExportStatus.FAILED and "Aucun média vidéo" in refused.message

    missing_folder = run_export(_request(_project(media), tmp_path / "absent" / "out.mp4"))
    assert missing_folder.status is ExportStatus.FAILED and "dossier de sortie est introuvable" in missing_folder.message


# --- Annulation -------------------------------------------------------------------------------------------------------


def test_cancelling_midway_kills_ffmpeg_and_leaves_nothing(long_media, tmp_path):
    output = tmp_path / "out.mp4"
    state = {"progress": 0}
    result = run_export(
        _request(_project(long_media, seconds=20.0, size=(1280, 720)), output, size=(1280, 720)),
        progress=lambda value: state.__setitem__("progress", value),
        cancelled=lambda: state["progress"] >= 1,
    )
    assert result.status is ExportStatus.CANCELLED
    assert 1 <= state["progress"] < 100, "annulé en cours de route, pas à la fin"
    assert not output.exists() and _hidden_partials(tmp_path) == []


def test_cancelling_before_the_preparation_ends_stops_it(media, tmp_path):
    project = _project(media, remapping=TimeRemapping(speed=0.5, interpolation=TimeInterpolation.BLENDING))
    result = run_export(_request(project, tmp_path / "out.mp4"), cancelled=lambda: True,
                        flow_cache=FlowCache(tmp_path / "flow"))
    assert result.status is ExportStatus.CANCELLED
    assert not (tmp_path / "out.mp4").exists() and _hidden_partials(tmp_path) == []


# --- Repli sur le CPU -------------------------------------------------------------------------------------------------


def _bogus_hardware(monkeypatch, requested: HardwareEncoder) -> None:
    """Un encodeur « matériel » qui n'existe pas : FFmpeg échoue au lancement, avant la première image."""
    real = engine_module.resolve_video_encoder

    def fake(codec, **kwargs):
        if str(getattr(kwargs.get("hardware"), "value", kwargs.get("hardware"))) == requested.value:
            return EncoderChoice(requested=requested, used=HardwareEncoder.VIDEOTOOLBOX, encoder="h264_inexistant",
                                 args=("-c:v", "h264_inexistant"), codec="h264")
        return real(codec, **kwargs)

    monkeypatch.setattr(engine_module, "resolve_video_encoder", fake)


def test_auto_falls_back_to_the_cpu_when_the_hardware_encoder_fails_to_start(media, tmp_path, monkeypatch):
    _bogus_hardware(monkeypatch, HardwareEncoder.AUTO)
    output = tmp_path / "out.mp4"
    messages: list[str] = []
    encoders: list[EncoderChoice] = []
    result = run_export(_request(_project(media), output, hardware="auto"), status=messages.append,
                        on_encoder=encoders.append)
    assert result.ok, result.message
    assert result.fallback_reason and "rendu CPU utilisé" in result.fallback_reason
    assert result.fallback_reason in messages, "la raison du repli est annoncée"
    assert result.encoder is not None and not result.encoder.is_hardware and result.encoder.fallback_reason
    assert [e.is_hardware for e in encoders] == [True, False], "un lancement matériel, puis le CPU"
    assert output.is_file() and _frames(output) == FPS
    assert result.diagnostics, "la fin de la sortie d'erreur est gardée pour le support"


def test_an_explicit_hardware_encoder_that_fails_is_reported_not_hidden(media, tmp_path, monkeypatch):
    _bogus_hardware(monkeypatch, HardwareEncoder.VIDEOTOOLBOX)
    output = tmp_path / "out.mp4"
    encoders: list[EncoderChoice] = []
    result = run_export(_request(_project(media), output, hardware="videotoolbox"), on_encoder=encoders.append)
    assert result.status is ExportStatus.FAILED and result.fallback_reason == ""
    assert result.error_kind == "encoder" and result.diagnostics
    assert len(encoders) == 1, "aucun repli silencieux"
    assert not output.exists()


# --- Fichier atomique -------------------------------------------------------------------------------------------------


def test_the_partial_file_lives_next_to_the_output_with_the_same_extension(tmp_path):
    partial = Path(partial_output_path(str(tmp_path / "film.final.mp4")))
    assert partial.parent == tmp_path and partial.suffix == ".mp4" and partial.name.startswith(".film.final.")
    assert ".partial" in partial.name
    assert partial_output_path(str(tmp_path / "a.mp4")) != partial_output_path(str(tmp_path / "a.mp4")), "unique par export"


def test_the_final_file_only_appears_when_the_export_is_complete(media, tmp_path):
    """Pendant le rendu seul le fichier caché existe ; le nom demandé n'apparaît qu'une fois tout écrit."""
    output = tmp_path / "out.mp4"
    observed: list[tuple[bool, bool]] = []

    def watch(_value: int) -> None:
        observed.append((output.exists(), bool(_hidden_partials(tmp_path))))

    result = run_export(_request(_project(media), output), progress=watch)
    assert result.ok
    assert all(not final for final, _partial in observed[:-1]), "jamais de fichier final avant la fin"
    assert any(partial for _final, partial in observed), "le fichier intermédiaire a bien existé pendant le rendu"
    assert output.is_file() and _hidden_partials(tmp_path) == []


def test_non_atomic_mode_writes_the_requested_file_directly(media, tmp_path):
    output = tmp_path / "out.mp4"
    seen_final: list[bool] = []
    result = run_export(_request(_project(media), output), atomic=False,
                        progress=lambda _value: seen_final.append(output.exists()))
    assert result.ok and output.is_file()
    assert any(seen_final), "le fichier demandé existe dès le rendu"


def test_the_request_object_is_never_modified(media, tmp_path):
    request = _request(_project(media), tmp_path / "out.mp4")
    before = replace(request)
    run_export(request)
    assert request == before and request.output_path == str(tmp_path / "out.mp4")
