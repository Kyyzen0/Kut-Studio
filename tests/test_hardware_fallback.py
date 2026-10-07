"""Repli CPU, échec explicite, Render Queue et compatibilité des données (faux FFmpeg)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_hardware_encoding import FakeFFmpeg
from test_render_queue import (  # noqa: F401 - fixtures partagées
    TIMEOUT,
    _enqueue,
    _out,
    _project,
    _wait_idle,
    fake_ffmpeg,
    make_queue,
    queue,
)

from core.hardware_cache import CapabilityService, set_default_service
from core.hardware_encoding import HardwareEncoder
from core.render_job import ErrorKind, JobStatus, RenderJob
from core.render_presets import builtin_presets, custom_preset, get_preset, with_hardware
from core.user_settings import UserSettings, load_user_settings, save_user_settings


@pytest.fixture
def with_videotoolbox(tmp_path):
    """Service de capacités simulé : VideoToolbox validé, sans aucun GPU réel."""
    service = CapabilityService(
        command_provider=lambda: ["fake-ffmpeg"],
        cache_path=tmp_path / "caps.json",
        runner=FakeFFmpeg("videotoolbox"),
        environment={},
    )
    set_default_service(service)
    return service


@pytest.fixture
def launches(tmp_path, monkeypatch):
    """Fichier où le faux FFmpeg note les arguments de chaque lancement."""
    path = tmp_path / "launches.jsonl"
    monkeypatch.setenv("FAKE_FFMPEG_ARGS_FILE", str(path))

    def read() -> list[list[str]]:
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]

    return read


def _encoders_used(launches) -> list[str]:
    result = []
    for arguments in launches():
        result.append(arguments[arguments.index("-c:v") + 1] if "-c:v" in arguments else "")
    return result


def _job(queue, tmp_path, hardware, name="a.mp4"):
    spec = with_hardware(get_preset("h264_1080p"), hardware)
    return queue.enqueue(_project(tmp_path), spec, _out(tmp_path, name))


# --- Auto ------------------------------------------------------------------------------------------------------------


def test_auto_uses_the_detected_hardware_encoder_end_to_end(qtbot, queue, tmp_path, with_videotoolbox, launches):
    job = _job(queue, tmp_path, "auto")
    queue.start_all()
    _wait_idle(qtbot, queue)
    assert job.status is JobStatus.COMPLETED
    assert _encoders_used(launches) == ["h264_videotoolbox"]
    assert job.encoder == "h264_videotoolbox" and job.hardware_used == "videotoolbox"
    assert job.encoder_label == "H.264 · VideoToolbox" and job.fallback_reason == ""
    assert job.result.encoder == "h264_videotoolbox" and job.result.hardware_used == "videotoolbox"
    assert job.hardware == "auto"                                # la demande de l'utilisateur est conservée


def test_auto_without_hardware_renders_on_the_cpu_without_any_fallback_message(qtbot, queue, tmp_path, launches):
    job = _job(queue, tmp_path, "auto")                          # tests : détection désactivée
    queue.start_all()
    _wait_idle(qtbot, queue)
    assert job.status is JobStatus.COMPLETED and _encoders_used(launches) == ["libx264"]
    assert job.encoder_label == "H.264 · CPU" and job.fallback_reason == ""


def test_auto_falls_back_to_the_cpu_when_the_hardware_encoder_fails_at_launch(
    qtbot, queue, tmp_path, with_videotoolbox, launches, monkeypatch
):
    monkeypatch.setenv("FAKE_FFMPEG_FAIL_ENCODER", "videotoolbox")
    notices, selections = [], []
    queue.encoder_fallback.connect(lambda job_id, reason: notices.append((job_id, reason)))
    queue._engine.encoder_selected.connect(lambda choice: selections.append(choice.encoder))
    job = _job(queue, tmp_path, "auto")
    queue.start_all()
    _wait_idle(qtbot, queue)
    # Le job n'est pas perdu : il se termine, sur le CPU.
    assert job.status is JobStatus.COMPLETED and Path(job.output_path).exists()
    assert _encoders_used(launches) == ["h264_videotoolbox", "libx264"]   # un seul repli, fini
    assert selections == ["h264_videotoolbox", "libx264"]
    assert job.encoder == "libx264" and job.hardware_used == "cpu" and job.encoder_label == "H.264 · CPU"
    assert "VideoToolbox" in job.fallback_reason and "CPU" in job.fallback_reason
    assert job.result.fallback_reason == job.fallback_reason and job.result.hardware_used == "cpu"
    assert "Error initializing" in job.diagnostics               # raison conservée pour le support
    assert notices == [(job.id, job.fallback_reason)]            # l'utilisateur est prévenu, une fois


def test_the_cpu_fallback_keeps_the_same_output_settings(
    qtbot, queue, tmp_path, with_videotoolbox, launches, monkeypatch
):
    monkeypatch.setenv("FAKE_FFMPEG_FAIL_ENCODER", "videotoolbox")
    _job(queue, tmp_path, "auto")
    queue.start_all()
    _wait_idle(qtbot, queue)
    hardware, cpu = launches()
    assert "-crf" in cpu and cpu[cpu.index("-crf") + 1] == "20"   # CRF du preset, tel quel
    assert hardware[hardware.index("-filter_complex") + 1] == cpu[cpu.index("-filter_complex") + 1]
    assert hardware[-1] == cpu[-1]                               # même fichier de sortie


def test_auto_never_loops_between_encoders_when_the_cpu_fails_too(
    qtbot, queue, tmp_path, with_videotoolbox, launches, monkeypatch
):
    monkeypatch.setenv("FAKE_FFMPEG_FAIL", "disque plein")
    job = _job(queue, tmp_path, "auto")
    queue.start_all()
    _wait_idle(qtbot, queue)
    assert job.status is JobStatus.FAILED and job.error_kind == ErrorKind.FFMPEG
    assert "disque plein" in job.error_message
    assert len(launches()) == 2                                  # matériel puis CPU : jamais plus


def test_auto_does_not_fall_back_once_frames_were_encoded(
    qtbot, queue, tmp_path, with_videotoolbox, launches, monkeypatch
):
    monkeypatch.setenv("FAKE_FFMPEG_FAIL_ENCODER_LATE", "videotoolbox")
    monkeypatch.setenv("FAKE_FFMPEG_SECONDS", "1.0")
    job = _job(queue, tmp_path, "auto")
    queue.start_all()
    _wait_idle(qtbot, queue)
    assert job.status is JobStatus.FAILED                        # pas de rendu en double à 90 %
    assert _encoders_used(launches) == ["h264_videotoolbox"]


def test_the_fallback_applies_to_each_job_independently(
    qtbot, queue, tmp_path, with_videotoolbox, launches, monkeypatch
):
    monkeypatch.setenv("FAKE_FFMPEG_FAIL_ENCODER", "videotoolbox")
    first = _job(queue, tmp_path, "auto", "one.mp4")
    second = _job(queue, tmp_path, "auto", "two.mp4")
    queue.start_all()
    _wait_idle(qtbot, queue)
    assert first.status is second.status is JobStatus.COMPLETED
    assert _encoders_used(launches) == ["h264_videotoolbox", "libx264"] * 2


# --- Choix explicite : jamais masqué --------------------------------------------------------------------------------------


def test_an_explicit_encoder_that_fails_is_reported_and_never_hidden(
    qtbot, queue, tmp_path, with_videotoolbox, launches, monkeypatch
):
    monkeypatch.setenv("FAKE_FFMPEG_FAIL_ENCODER", "videotoolbox")
    notices = []
    queue.encoder_fallback.connect(lambda *args: notices.append(args))
    job = _job(queue, tmp_path, "videotoolbox")
    queue.start_all()
    _wait_idle(qtbot, queue)
    assert job.status is JobStatus.FAILED and job.error_kind == ErrorKind.ENCODER
    assert "Error initializing" in job.error_message and job.can_retry_on_cpu
    assert _encoders_used(launches) == ["h264_videotoolbox"] and notices == []   # aucun repli caché
    assert job.hardware == "videotoolbox"                        # le choix de l'utilisateur n'a pas bougé


def test_an_explicit_encoder_that_is_not_available_fails_before_launching_ffmpeg(
    qtbot, queue, tmp_path, launches
):
    job = _job(queue, tmp_path, "nvenc")                         # aucune capacité détectée
    queue.start_all()
    _wait_idle(qtbot, queue)
    assert job.status is JobStatus.FAILED and job.error_kind == ErrorKind.ENCODER
    assert "désactivé" in job.error_message and launches() == []
    assert job.can_retry_on_cpu


def test_retry_on_cpu_changes_only_that_job_and_renders_it(
    qtbot, queue, tmp_path, with_videotoolbox, launches, monkeypatch
):
    monkeypatch.setenv("FAKE_FFMPEG_FAIL_ENCODER", "videotoolbox")
    job = _job(queue, tmp_path, "videotoolbox")
    other = _job(queue, tmp_path, "videotoolbox", "other.mp4")
    queue.start_job(job.id)
    _wait_idle(qtbot, queue)
    assert job.status is JobStatus.FAILED and other.status is JobStatus.WAITING
    assert queue.retry_on_cpu(job.id) and queue.start_job(job.id)
    _wait_idle(qtbot, queue)
    assert job.status is JobStatus.COMPLETED and job.hardware == "cpu"
    assert job.encoder == "libx264" and job.error_message == "" and job.diagnostics == ""
    assert other.hardware == "videotoolbox" and not job.can_retry_on_cpu
    assert not queue.retry_on_cpu("inconnu")


def test_cpu_jobs_never_trigger_a_detection(qtbot, queue, tmp_path, with_videotoolbox, launches):
    job = _job(queue, tmp_path, "cpu")
    queue.start_all()
    _wait_idle(qtbot, queue)
    assert job.status is JobStatus.COMPLETED and _encoders_used(launches) == ["libx264"]
    assert with_videotoolbox.scan_count == 0                     # le CPU ne dépend d'aucune détection


# --- Sérialisation et compatibilité ------------------------------------------------------------------------------------------


def test_render_job_serializes_the_hardware_information(tmp_path):
    job = RenderJob.create(
        project_fps=30.0, spec=with_hardware(get_preset("youtube"), "auto"), snapshot_path="/s.kut", output_path="/o.mp4"
    )
    job.encoder, job.hardware_used = "h264_videotoolbox", "videotoolbox"
    job.fallback_reason, job.diagnostics = "raison", "détail"
    again = RenderJob.from_dict(json.loads(json.dumps(job.to_dict())))
    assert (again.hardware, again.encoder, again.hardware_used) == ("auto", "h264_videotoolbox", "videotoolbox")
    assert (again.fallback_reason, again.diagnostics) == ("raison", "détail")
    assert again.encoder_label == "H.264 · VideoToolbox"


def test_jobs_saved_before_hardware_encoding_still_load():
    old = {
        "id": "abc123abc123", "name": "ancien", "snapshot_path": "/s.kut", "output_path": "/o.mp4",
        "container": "mp4", "video_codec": "h264", "audio_codec": "aac",
        "width": 1920, "height": 1080, "fps": 30, "quality": 20, "audio_bitrate": "192k",
        "hardware": "cpu", "preset_id": "h264_1080p", "status": "completed", "progress": 100,
        "result": {"output_bytes": 10, "encoder": "libx264", "hardware_used": "cpu"},
    }
    job = RenderJob.from_dict(old)
    assert job.encoder == "" and job.fallback_reason == "" and job.diagnostics == ""
    assert job.encoder_label == "H.264 · CPU"                    # relu depuis le résultat enregistré
    assert RenderJob.from_dict({**old, "status": "waiting", "result": None}).encoder_label == ""
    garbage = RenderJob.from_dict({**old, "hardware_used": "quantum", "fallback_reason": None, "diagnostics": 5})
    assert garbage.hardware_used == "cpu" and garbage.fallback_reason == ""


def test_old_render_queue_files_load_and_new_ones_roundtrip(tmp_path, make_queue, fake_ffmpeg):
    queue = make_queue(tmp_path / "q")
    job = _job(queue, tmp_path, "auto")
    job.encoder, job.hardware_used = "h264_videotoolbox", "videotoolbox"
    queue._persist()
    reloaded = make_queue(tmp_path / "q")
    reloaded.restore()
    assert [j.encoder for j in reloaded.jobs] == ["h264_videotoolbox"]
    assert reloaded.jobs[0].hardware == "auto"


def test_every_builtin_preset_is_still_valid_and_cpu_by_default():
    for spec in builtin_presets():
        assert spec.hardware == "cpu"
        export_format, preset, fps = spec.export_parts(25.0)
        assert preset.crf == spec.quality and fps == (spec.fps or 25.0)
    custom = custom_preset(hardware="auto")
    assert custom.hardware == "auto" and with_hardware(custom, "videotoolbox").hardware == "videotoolbox"
    assert custom_preset().hardware == "cpu" and custom_preset(hardware="inconnu").hardware == "cpu"


def test_export_encoder_setting_defaults_roundtrips_and_ignores_garbage(tmp_path, monkeypatch):
    monkeypatch.setenv("KUT_STUDIO_CONFIG_DIR", str(tmp_path))
    assert UserSettings().export_encoder == "auto"
    save_user_settings(UserSettings(export_encoder="videotoolbox"))
    assert load_user_settings().export_encoder == "videotoolbox"
    save_user_settings(UserSettings(export_encoder="n'importe quoi"))
    assert load_user_settings().export_encoder == "auto"
    (Path(tmp_path) / "settings.json").write_text(json.dumps({"language": "fr"}), encoding="utf-8")
    assert load_user_settings().export_encoder == "auto"          # anciens fichiers


# --- Moteur : commande construite --------------------------------------------------------------------------------------------------


def test_vaapi_command_places_the_device_before_the_inputs_and_uploads_the_final_frames(
    tmp_path, monkeypatch
):
    from core.export_engine import ExportEngine, ExportRequest
    from core.render_plan import build_render_plan

    monkeypatch.setattr("core.hardware_encoding.vaapi_device", lambda environment=None: "/dev/dri/renderD128")
    monkeypatch.setattr("core.export_engine._ffmpeg_path", ("ffmpeg",))
    set_default_service(CapabilityService(
        command_provider=lambda: ["fake"], cache_path=tmp_path / "c.json",
        runner=FakeFFmpeg("vaapi"), environment={},
    ))
    spec = with_hardware(get_preset("h264_1080p"), "vaapi")
    plan = build_render_plan(_project(tmp_path))
    export_format, preset, fps = spec.export_parts(plan.fps)
    engine = ExportEngine()
    engine._prepare_temporary_files(plan)
    command = engine._build_command(ExportRequest(plan, str(tmp_path / "o.mp4"), export_format, preset, fps, "vaapi"))
    assert command.index("-vaapi_device") < command.index("-i")
    graph = command[command.index("-filter_complex") + 1]
    assert graph.endswith("format=nv12,hwupload[vencoded]")
    assert command[command.index("-map") + 1] == "[vencoded]"
    assert "h264_vaapi" in command and "-qp" in command


def test_command_log_never_contains_user_paths(qtbot, queue, tmp_path, with_videotoolbox, caplog):
    import logging

    caplog.set_level(logging.INFO, logger="kut_studio.encoding")
    _job(queue, tmp_path, "auto")
    queue.start_all()
    _wait_idle(qtbot, queue)
    text = "\n".join(record.getMessage() for record in caplog.records)
    assert "Encodeur : H.264 · VideoToolbox" in text and "Commande FFmpeg" in text
    assert str(tmp_path) not in text


def test_an_unrelated_failure_with_an_explicit_encoder_is_not_labelled_an_encoder_error(
    qtbot, queue, tmp_path, with_videotoolbox, launches, monkeypatch
):
    monkeypatch.setenv("FAKE_FFMPEG_FAIL", "Error opening input file media.mp4: Invalid data")
    job = _job(queue, tmp_path, "videotoolbox")
    queue.start_all()
    _wait_idle(qtbot, queue)
    assert job.status is JobStatus.FAILED and job.error_kind == ErrorKind.FFMPEG
    assert not job.can_retry_on_cpu                               # changer d'encodeur n'y changerait rien
