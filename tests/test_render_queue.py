"""Render Queue : jobs, presets, persistance, exécution (faux FFmpeg + FFmpeg réel)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from core.export_engine import ExportEngine, ExportFormat
from core.project_model import Clip, MediaAsset, Project, Track
from core.render_job import ErrorKind, JobStatus, RenderJob, RenderResult
from core.render_presets import (
    CUSTOM_PRESET_ID,
    builtin_presets,
    custom_preset,
    default_preset,
    export_format_for,
    get_preset,
)
from core.render_queue import (
    INTERRUPTED_MESSAGE,
    RenderQueue,
    partial_path_for,
)
from core.render_queue_store import RenderQueueStore
from core.video_encoders import HardwareEncoder, coerce_hardware, resolve_video_encoder

ROOT = Path(__file__).resolve().parent.parent
FAKE_FFMPEG = ROOT / "tests" / "fixtures" / "fake_ffmpeg.py"
TIMEOUT = 20000


# --- helpers ---------------------------------------------------------------------------


@pytest.fixture
def fake_ffmpeg(monkeypatch):
    monkeypatch.setattr("core.export_engine._ffmpeg_path", (sys.executable, str(FAKE_FFMPEG)))
    monkeypatch.setenv("FAKE_FFMPEG_SECONDS", "0.3")


def _project(tmp_path: Path, name: str = "Projet test") -> Project:
    media = tmp_path / "source.mp4"
    media.write_bytes(b"\x00" * 64)
    asset = MediaAsset(
        id="a1", path=str(media), name="source", duration=10.0,
        width=1920, height=1080, fps=30.0, media_type="video",
    )
    clip = Clip(
        id="c1", asset_id="a1", track_id="V1", timeline_start=0.0,
        source_in=0.0, source_out=4.0, label="Clip",
    )
    return Project(
        name=name, width=1920, height=1080, fps=30.0, media_assets=[asset],
        tracks=[Track(id="V1", name="V1", type="video", clips=[clip])],
    )


@pytest.fixture
def make_queue(tmp_path):
    created: list[RenderQueue] = []

    def factory(directory: Path | None = None) -> RenderQueue:
        queue = RenderQueue(ExportEngine(), RenderQueueStore(directory or tmp_path / "queue"))
        created.append(queue)
        return queue

    yield factory
    for queue in created:
        queue.shutdown()


@pytest.fixture
def queue(make_queue, fake_ffmpeg):
    return make_queue()


def _out(tmp_path: Path, name: str) -> str:
    return str(tmp_path / name)


def _enqueue(queue, tmp_path, name="a.mp4", preset="h264_1080p", project=None):
    spec = get_preset(preset) or default_preset()
    return queue.enqueue(project or _project(tmp_path), spec, _out(tmp_path, name))


def _wait_idle(qtbot, queue, timeout=TIMEOUT):
    qtbot.waitUntil(lambda: not queue.is_running, timeout=timeout)


def _status_log(queue) -> list[tuple[str, JobStatus]]:
    log: list[tuple[str, JobStatus]] = []

    def record(job_id: str) -> None:
        job = queue.job(job_id)
        entry = (job_id, job.status)
        if job is not None and (not log or log[-1] != entry) and (
            not any(e == entry for e in log)
        ):
            log.append(entry)

    queue.job_updated.connect(record)
    return log


def _pid_alive(pid: int) -> bool:
    if sys.platform == "win32":  # pragma: no cover - vérification POSIX seulement
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


# --- RenderJob : création, sérialisation ------------------------------------------------------


def test_job_is_created_from_a_preset_with_all_the_expected_fields(tmp_path):
    spec = get_preset("youtube")
    job = RenderJob.create(
        spec=spec, snapshot_path=str(tmp_path / "p.kut"), output_path=_out(tmp_path, "yt.mp4"),
        project_name="Mon film", duration_seconds=12.5, master_gain_db=-3.0, now=1000.0,
    )
    assert job.id and job.status is JobStatus.WAITING and job.progress == 0
    assert job.name == "Mon film · YouTube"
    assert job.file_name == "yt.mp4"
    assert (job.container, job.video_codec, job.audio_codec) == ("mp4", "h264", "aac")
    assert job.resolution == (1920, 1080) and job.fps == 30 and job.quality == 18
    assert job.preset_id == "youtube" and job.hardware == "cpu"
    assert job.created_at == 1000.0 and job.started_at is None and job.finished_at is None
    assert job.error_message == "" and job.result is None
    assert job.master_gain_db == -3.0 and job.duration_seconds == 12.5


def test_job_ids_are_unique(tmp_path):
    spec = default_preset()
    ids = {RenderJob.create(spec=spec, snapshot_path="x", output_path="y.mp4").id for _ in range(50)}
    assert len(ids) == 50


def test_job_serialization_round_trips_every_state(tmp_path):
    job = RenderJob.create(
        spec=get_preset("prores_master"), snapshot_path="s.kut", output_path="o.mov",
        project_name="P", duration_seconds=3.0, now=10.0,
    )
    assert RenderJob.from_dict(json.loads(json.dumps(job.to_dict()))) == job
    job.mark_rendering(now=11.0)
    job.progress = 42
    assert RenderJob.from_dict(job.to_dict()) == job
    job.mark_completed(
        RenderResult(1234, 2.5, 3.0, "prores_ks", "cpu", None), now=15.0
    )
    restored = RenderJob.from_dict(json.loads(json.dumps(job.to_dict())))
    assert restored == job and restored.result.output_bytes == 1234 and restored.progress == 100
    job.mark_failed("boom", ErrorKind.FFMPEG, now=16.0)
    assert RenderJob.from_dict(job.to_dict()) == job
    job.mark_cancelled("stop", now=17.0)
    assert RenderJob.from_dict(job.to_dict()).status is JobStatus.CANCELLED


@pytest.mark.parametrize(
    "broken",
    [None, 3, [], {}, {"id": "x"}, {"id": "x", "snapshot_path": "s", "output_path": "o", "container": "avi"},
     {"id": "x", "snapshot_path": "s", "output_path": "o", "width": 0}],
)
def test_job_deserialization_rejects_unusable_entries(broken):
    with pytest.raises(ValueError):
        RenderJob.from_dict(broken)


def test_job_deserialization_tolerates_corrupt_optional_fields():
    job = RenderJob.from_dict({
        "id": "x", "snapshot_path": "s", "output_path": "o.mp4", "status": "weird",
        "progress": 999, "hardware": "quantum", "created_at": "nope", "result": 5,
        "master_gain_db": float("nan"),
    })
    assert job.status is JobStatus.WAITING and job.progress == 100
    assert job.hardware == "cpu" and job.created_at == 0.0 and job.result is None
    assert job.master_gain_db == 0.0


def test_there_is_no_paused_status():
    assert {s.value for s in JobStatus} == {"waiting", "rendering", "completed", "failed", "cancelled"}


# --- Presets ---------------------------------------------------------------------------------------


def test_required_presets_exist_with_the_expected_settings():
    by_id = {spec.id: spec for spec in builtin_presets()}
    assert {"h264_1080p", "h264_1440p", "h264_4k", "youtube", "tiktok", "prores_master"} <= set(by_id)
    assert by_id["h264_1080p"].resolution == (1920, 1080)
    assert by_id["h264_1440p"].resolution == (2560, 1440)
    assert by_id["h264_4k"].resolution == (3840, 2160)
    assert by_id["tiktok"].resolution == (1080, 1920)
    assert (by_id["prores_master"].container, by_id["prores_master"].video_codec) == ("mov", "prores_ks")
    assert CUSTOM_PRESET_ID not in by_id  # Custom est construit, pas figé


def test_presets_only_produce_configuration_for_the_existing_engine():
    for spec in builtin_presets():
        export_format, export_preset, fps = spec.export_parts()
        assert isinstance(export_format, ExportFormat)
        assert export_preset.resolution == spec.resolution and fps == spec.fps
        assert export_format.container == spec.container and export_format.codec == spec.video_codec


def test_preset_drives_the_engine_command_without_extra_ffmpeg_logic(tmp_path):
    project = _project(tmp_path)
    from core.render_plan import build_render_plan

    for preset_id, expected_codec in (("tiktok", "libx264"), ("prores_master", "prores_ks")):
        job = RenderJob.create(
            spec=get_preset(preset_id), snapshot_path="s", output_path=_out(tmp_path, f"{preset_id}.mov"),
        )
        command = ExportEngine()._build_command(job.to_request(build_render_plan(project)))
        assert command[command.index("-c:v") + 1] == expected_codec
    tiktok = RenderJob.create(spec=get_preset("tiktok"), snapshot_path="s", output_path=_out(tmp_path, "t.mp4"))
    command = ExportEngine()._build_command(tiktok.to_request(build_render_plan(project)))
    assert any("1080" in part and "1920" in part for part in command)


def test_custom_preset_is_validated():
    spec = custom_preset(width=1280, height=720, fps=24, quality=23)
    assert spec.id == CUSTOM_PRESET_ID and spec.summary().endswith("1280×720 · 24 fps")
    with pytest.raises(ValueError):
        custom_preset(container="mp4", video_codec="prores_ks")
    with pytest.raises(ValueError):
        custom_preset(width=0)
    with pytest.raises(ValueError):
        custom_preset(fps=0)
    with pytest.raises(ValueError):
        custom_preset(audio_codec="flac")
    assert export_format_for("mov", "prores") is ExportFormat.MOV_PRORES


# --- Encodeurs matériels (préparation) ----------------------------------------------------------------


def test_every_planned_hardware_family_is_accepted_by_the_data_model():
    assert {h.value for h in HardwareEncoder} == {
        "cpu", "auto", "videotoolbox", "nvenc", "qsv", "amf", "vaapi",
    }
    for family in HardwareEncoder:
        spec = custom_preset(hardware=family.value)
        job = RenderJob.create(spec=spec, snapshot_path="s", output_path="o.mp4")
        assert RenderJob.from_dict(job.to_dict()).hardware == family.value
    assert coerce_hardware("garbage") is HardwareEncoder.CPU


def test_cpu_choice_is_unchanged_and_unknown_codecs_are_rejected():
    cpu = resolve_video_encoder("h264", speed_preset="medium", quality=20)
    assert cpu.args == ("-c:v", "libx264", "-preset", "medium", "-crf", "20")
    assert cpu.fallback_reason is None
    # Sans capacités matérielles (ici : désactivées par la configuration des tests),
    # ``auto`` retombe sur le CPU sans bruit, et un encodeur explicite est refusé.
    auto = resolve_video_encoder("h264", quality=20, hardware="auto")
    assert auto.used is HardwareEncoder.CPU and auto.fallback_reason is None
    with pytest.raises(ValueError):
        resolve_video_encoder("vp9")


# --- Stockage -----------------------------------------------------------------------------------------------


def test_store_round_trip_skips_invalid_jobs_and_drops_duplicates(tmp_path):
    store = RenderQueueStore(tmp_path / "q")
    good = RenderJob.create(spec=default_preset(), snapshot_path="s", output_path="o.mp4")
    store.save([good])
    data = json.loads(store.queue_file.read_text(encoding="utf-8"))
    data["jobs"] += [{"id": "broken"}, good.to_dict(), "junk"]
    store.queue_file.write_text(json.dumps(data), encoding="utf-8")
    assert store.load() == [good]


def test_store_sets_a_corrupt_file_aside_and_starts_empty(tmp_path):
    store = RenderQueueStore(tmp_path / "q")
    store.directory.mkdir(parents=True)
    store.queue_file.write_text("{ pas du json", encoding="utf-8")
    assert store.load() == []
    assert store.queue_file.with_name("queue.json.corrupt").exists()
    assert RenderQueueStore(tmp_path / "vide").load() == []


def test_queue_file_stays_small_and_never_embeds_the_project(queue, tmp_path):
    _enqueue(queue, tmp_path)
    text = queue.store.queue_file.read_text(encoding="utf-8")
    assert "tracks" not in text and "media_assets" not in text
    assert len(text) < 3000


def test_snapshot_freezes_the_project_at_enqueue_time(queue, tmp_path):
    project = _project(tmp_path)
    job = _enqueue(queue, tmp_path, project=project)
    project.tracks[0].clips[0].source_out = 9.0  # l'utilisateur continue à monter
    from core.project_io import load_project

    assert load_project(job.snapshot_path).tracks[0].clips[0].source_out == 4.0


# --- Création / ordre ------------------------------------------------------------------------------------------


def test_enqueue_creates_a_waiting_job_and_persists_it(queue, tmp_path):
    seen = []
    queue.jobs_changed.connect(lambda: seen.append(1))
    job = _enqueue(queue, tmp_path)
    assert job.status is JobStatus.WAITING and queue.jobs == (job,)
    assert Path(job.snapshot_path).is_file() and seen
    assert [j.id for j in queue.store.load()] == [job.id]
    assert job.duration_seconds == pytest.approx(4.0)


def test_enqueue_validates_early(queue, tmp_path):
    spec = default_preset()
    with pytest.raises(ValueError, match="dossier de sortie"):
        queue.enqueue(_project(tmp_path), spec, str(tmp_path / "nope" / "x.mp4"))
    empty = Project(name="vide", width=1920, height=1080, fps=30.0, media_assets=[], tracks=[])
    with pytest.raises(ValueError, match="Aucun média"):
        queue.enqueue(empty, spec, _out(tmp_path, "x.mp4"))
    _enqueue(queue, tmp_path, "same.mp4")
    with pytest.raises(ValueError, match="déjà"):
        _enqueue(queue, tmp_path, "same.mp4")
    assert len(queue.jobs) == 1  # aucun instantané orphelin non plus
    assert len(list(queue.store.jobs_dir.iterdir())) == 1


def test_output_extension_follows_the_preset_container(queue, tmp_path):
    job = queue.enqueue(_project(tmp_path), get_preset("prores_master"), _out(tmp_path, "film.mp4"))
    assert job.output_path.endswith("film.mov")


def test_waiting_jobs_can_be_reordered_and_the_order_is_the_run_order(qtbot, queue, tmp_path):
    a, b, c = (_enqueue(queue, tmp_path, f"{n}.mp4") for n in "abc")
    assert queue.move(c.id, -2) and [j.id for j in queue.jobs] == [c.id, a.id, b.id]
    assert queue.move(c.id, +1) and [j.id for j in queue.jobs] == [a.id, c.id, b.id]
    assert not queue.move(a.id, -1)  # déjà en tête
    assert queue.move(b.id, -99) and queue.jobs[0] is b
    assert [j.id for j in queue.store.load()] == [j.id for j in queue.jobs]
    log = _status_log(queue)
    queue.start_all()
    _wait_idle(qtbot, queue)
    started = [job_id for job_id, status in log if status is JobStatus.RENDERING]
    assert started == [j.id for j in queue.jobs]


def test_only_waiting_jobs_can_be_moved(qtbot, queue, tmp_path):
    a = _enqueue(queue, tmp_path, "a.mp4")
    queue.start_all()
    _wait_idle(qtbot, queue)
    assert a.status is JobStatus.COMPLETED and not queue.move(a.id, 1)


# --- Exécution ---------------------------------------------------------------------------------------------------


def test_job_runs_to_completion_with_progress_and_result(qtbot, queue, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_FFMPEG_SECONDS", "0.6")
    job = _enqueue(queue, tmp_path)
    progress: list[int] = []
    queue.job_updated.connect(lambda _id: progress.append(job.progress))
    finished = []
    queue.run_finished.connect(finished.append)
    assert queue.start_all()
    _wait_idle(qtbot, queue)
    assert job.status is JobStatus.COMPLETED and job.progress == 100
    assert progress == sorted(progress) and any(0 < p < 100 for p in progress)
    assert Path(job.output_path).exists()
    assert not Path(partial_path_for(job)).exists()
    assert job.started_at and job.finished_at and job.finished_at >= job.started_at
    assert job.result.encoder == "libx264" and job.result.hardware_used == "cpu"
    assert job.result.timeline_seconds == pytest.approx(4.0) and job.result.output_bytes == 0
    assert finished == [{"completed": 1, "failed": 0, "cancelled": 0, "outputs": [job.output_path]}]


def test_start_job_runs_only_that_job(qtbot, queue, tmp_path):
    a, b = (_enqueue(queue, tmp_path, f"{n}.mp4") for n in "ab")
    assert queue.start_job(b.id)
    _wait_idle(qtbot, queue)
    assert b.status is JobStatus.COMPLETED and a.status is JobStatus.WAITING
    assert not queue.start_job(b.id)  # déjà terminé


def test_start_job_during_a_run_promotes_it_to_next(qtbot, queue, tmp_path):
    a, b, c = (_enqueue(queue, tmp_path, f"{n}.mp4") for n in "abc")
    log = _status_log(queue)
    queue.start_all()
    qtbot.waitUntil(lambda: a.status is JobStatus.RENDERING, timeout=TIMEOUT)
    assert queue.start_job(c.id)
    _wait_idle(qtbot, queue)
    order = [job_id for job_id, status in log if status is JobStatus.RENDERING]
    assert order == [a.id, c.id, b.id]


def test_several_successive_jobs_all_complete_in_order(qtbot, queue, tmp_path):
    jobs = [_enqueue(queue, tmp_path, f"{n}.mp4") for n in "abcd"]
    summary = []
    queue.run_finished.connect(summary.append)
    overall = []
    queue.overall_progress_changed.connect(overall.append)
    queue.start_all()
    _wait_idle(qtbot, queue)
    assert all(j.status is JobStatus.COMPLETED for j in jobs)
    assert all(Path(j.output_path).exists() for j in jobs)
    assert summary[0]["completed"] == 4
    assert overall == sorted(overall) and overall[-1] == 100
    assert not queue._engine.is_running and queue.current_job is None


def test_only_one_ffmpeg_runs_at_a_time(qtbot, queue, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_FFMPEG_SECONDS", "0.4")
    jobs = [_enqueue(queue, tmp_path, f"{n}.mp4") for n in "abc"]
    peak = 0

    def sample():
        nonlocal peak
        peak = max(peak, sum(1 for j in jobs if j.status is JobStatus.RENDERING))

    queue.job_updated.connect(lambda _id: sample())
    queue.start_all()
    _wait_idle(qtbot, queue)
    assert peak == 1


def test_overall_progress_is_weighted_by_duration(queue, tmp_path):
    short, long_ = _enqueue(queue, tmp_path, "s.mp4"), _enqueue(queue, tmp_path, "l.mp4")
    short.duration_seconds, long_.duration_seconds = 1.0, 3.0
    queue._batch = {short.id, long_.id}
    short.mark_completed(RenderResult())
    long_.status, long_.progress = JobStatus.RENDERING, 50
    assert queue.overall_progress() == int((1 * 100 + 3 * 50) / 4)
    long_.mark_failed("x")  # un échec ne compte plus
    assert queue.overall_progress() == 100


# --- Erreurs FFmpeg ------------------------------------------------------------------------------------------------------


def test_ffmpeg_error_marks_the_job_failed_with_a_readable_message(qtbot, queue, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_FFMPEG_FAIL", "Invalid data found when processing input")
    job = _enqueue(queue, tmp_path)
    summary = []
    queue.run_finished.connect(summary.append)
    queue.start_all()
    _wait_idle(qtbot, queue)
    assert job.status is JobStatus.FAILED and job.error_kind == ErrorKind.FFMPEG
    assert "Invalid data" in job.error_message
    assert not Path(job.output_path).exists() and not Path(partial_path_for(job)).exists()
    assert summary[0]["failed"] == 1 and queue.is_busy is False


def test_one_failing_job_does_not_stop_the_rest_of_the_queue(qtbot, queue, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_FFMPEG_FAIL_ON", "bad")
    ok1, bad, ok2 = (_enqueue(queue, tmp_path, n) for n in ("ok1.mp4", "bad.mp4", "ok2.mp4"))
    queue.start_all()
    _wait_idle(qtbot, queue)
    assert [j.status for j in (ok1, bad, ok2)] == [
        JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.COMPLETED,
    ]


def test_a_failed_render_keeps_a_previous_good_file(qtbot, queue, tmp_path, monkeypatch):
    target = tmp_path / "keep.mp4"
    target.write_bytes(b"ancien rendu")
    monkeypatch.setenv("FAKE_FFMPEG_FAIL", "boom")
    job = _enqueue(queue, tmp_path, "keep.mp4")
    queue.start_all()
    _wait_idle(qtbot, queue)
    assert job.status is JobStatus.FAILED and target.read_bytes() == b"ancien rendu"


def test_unreadable_snapshot_fails_the_job_cleanly(qtbot, queue, tmp_path):
    job = _enqueue(queue, tmp_path)
    Path(job.snapshot_path).write_text("pas un projet", encoding="utf-8")
    queue.start_all()
    _wait_idle(qtbot, queue)
    assert job.status is JobStatus.FAILED and job.error_kind == ErrorKind.INVALID
    job2 = _enqueue(queue, tmp_path, "b.mp4")
    Path(job2.snapshot_path).unlink()
    queue.start_all()
    _wait_idle(qtbot, queue)
    assert job2.error_kind == ErrorKind.SNAPSHOT_MISSING


# --- Annulation, retry, suppression -----------------------------------------------------------------------------------------


def test_cancelling_a_waiting_job(queue, tmp_path):
    job = _enqueue(queue, tmp_path)
    assert queue.cancel(job.id) and job.status is JobStatus.CANCELLED
    assert not queue.cancel(job.id)  # déjà terminé
    assert not Path(job.output_path).exists()


def test_cancelling_a_running_render_kills_ffmpeg_and_cleans_up(qtbot, queue, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_FFMPEG_SECONDS", "30")
    pid_file = tmp_path / "pid.txt"
    monkeypatch.setenv("FAKE_FFMPEG_PID_FILE", str(pid_file))
    target = tmp_path / "keep.mp4"
    target.write_bytes(b"ancien rendu")
    job = _enqueue(queue, tmp_path, "keep.mp4")
    queue.start_all()
    qtbot.waitUntil(lambda: queue._engine.is_running and pid_file.exists()
                    and pid_file.read_text().strip(), timeout=TIMEOUT)  # fichier créé puis écrit : attendre le contenu
    pid = int(pid_file.read_text())
    assert queue.cancel(job.id)
    _wait_idle(qtbot, queue)
    assert job.status is JobStatus.CANCELLED
    assert not queue._engine.is_running and not _pid_alive(pid)
    assert target.read_bytes() == b"ancien rendu" and not Path(partial_path_for(job)).exists()


def test_cancelling_one_job_lets_the_queue_continue(qtbot, queue, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_FFMPEG_SECONDS", "30")
    a, b = _enqueue(queue, tmp_path, "a.mp4"), _enqueue(queue, tmp_path, "b.mp4")
    queue.start_all()
    qtbot.waitUntil(lambda: queue._engine.is_running, timeout=TIMEOUT)
    monkeypatch.setenv("FAKE_FFMPEG_SECONDS", "0.2")  # le suivant sera court
    queue.cancel(a.id)
    _wait_idle(qtbot, queue)
    assert a.status is JobStatus.CANCELLED and b.status is JobStatus.COMPLETED


def test_stop_cancels_the_current_render_and_keeps_the_rest_waiting(qtbot, queue, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_FFMPEG_SECONDS", "30")
    a, b = _enqueue(queue, tmp_path, "a.mp4"), _enqueue(queue, tmp_path, "b.mp4")
    summary = []
    queue.run_finished.connect(summary.append)
    queue.start_all()
    qtbot.waitUntil(lambda: queue._engine.is_running, timeout=TIMEOUT)
    queue.stop()
    _wait_idle(qtbot, queue)
    qtbot.waitUntil(lambda: bool(summary), timeout=TIMEOUT)
    assert a.status is JobStatus.CANCELLED and b.status is JobStatus.WAITING
    assert summary[0]["cancelled"] == 1


def test_cancel_before_ffmpeg_is_launched(qtbot, queue, tmp_path):
    job = _enqueue(queue, tmp_path)
    queue._begin_run("all", None)
    queue._start_next()  # job préparé (RENDERING) mais FFmpeg pas encore lancé
    assert job.status is JobStatus.RENDERING and not queue._launched
    assert queue.cancel(job.id)
    assert job.status is JobStatus.CANCELLED and queue.current_job is None
    qtbot.wait(100)  # le lancement différé ne doit rien démarrer
    assert not queue._engine.is_running and job.status is JobStatus.CANCELLED


def test_retry_a_failed_job_then_it_succeeds(qtbot, queue, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_FFMPEG_FAIL", "boom")
    job = _enqueue(queue, tmp_path)
    queue.start_all()
    _wait_idle(qtbot, queue)
    assert job.status is JobStatus.FAILED
    monkeypatch.delenv("FAKE_FFMPEG_FAIL")
    assert queue.retry(job.id)
    assert job.status is JobStatus.WAITING and job.error_message == "" and job.progress == 0
    queue.start_job(job.id)
    _wait_idle(qtbot, queue)
    assert job.status is JobStatus.COMPLETED


def test_retry_a_completed_job_rerenders_it(qtbot, queue, tmp_path):
    job = _enqueue(queue, tmp_path)
    queue.start_all()
    _wait_idle(qtbot, queue)
    first_finish = job.finished_at
    assert queue.retry(job.id) and job.status is JobStatus.WAITING and job.result is None
    queue.start_all()
    _wait_idle(qtbot, queue)
    assert job.status is JobStatus.COMPLETED and job.finished_at >= first_finish


def test_retry_is_refused_for_waiting_or_missing_snapshot(queue, tmp_path):
    job = _enqueue(queue, tmp_path)
    assert not queue.retry(job.id) and not queue.retry("inconnu")
    queue.cancel(job.id)
    Path(job.snapshot_path).unlink()
    assert not queue.retry(job.id)
    assert job.status is JobStatus.FAILED and job.error_kind == ErrorKind.SNAPSHOT_MISSING


def test_remove_deletes_the_job_and_its_snapshot_but_not_a_running_one(qtbot, queue, tmp_path, monkeypatch):
    waiting = _enqueue(queue, tmp_path, "w.mp4")
    snapshot_dir = queue.store.snapshot_dir(waiting.id)
    assert snapshot_dir.exists() and queue.remove(waiting.id)
    assert queue.jobs == () and not snapshot_dir.exists()
    assert not queue.remove("inconnu")
    monkeypatch.setenv("FAKE_FFMPEG_SECONDS", "30")
    running = _enqueue(queue, tmp_path, "r.mp4")
    queue.start_all()
    qtbot.waitUntil(lambda: queue._engine.is_running, timeout=TIMEOUT)
    assert not queue.remove(running.id)
    queue.stop()
    _wait_idle(qtbot, queue)
    assert queue.remove(running.id)


def test_clear_finished_keeps_failures_unless_asked(qtbot, queue, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_FFMPEG_FAIL_ON", "bad")
    jobs = [_enqueue(queue, tmp_path, n) for n in ("ok.mp4", "bad.mp4", "wait.mp4")]
    queue.cancel(jobs[2].id)
    queue.start_all()
    _wait_idle(qtbot, queue)
    waiting = _enqueue(queue, tmp_path, "later.mp4")
    assert queue.clear_finished() == 2  # terminé + annulé
    assert [j.status for j in queue.jobs] == [JobStatus.FAILED, JobStatus.WAITING]
    assert queue.clear_finished(include_failed=True) == 1
    assert queue.jobs == (waiting,)


# --- Persistance et récupération ---------------------------------------------------------------------------------------------------


def test_waiting_and_finished_jobs_survive_a_restart(qtbot, queue, make_queue, tmp_path):
    done, waiting = _enqueue(queue, tmp_path, "done.mp4"), _enqueue(queue, tmp_path, "later.mp4")
    queue.start_job(done.id)
    _wait_idle(qtbot, queue)
    queue.shutdown()
    reborn = make_queue()
    assert reborn.restore() == 2
    assert [(j.id, j.status) for j in reborn.jobs] == [
        (done.id, JobStatus.COMPLETED), (waiting.id, JobStatus.WAITING),
    ]
    reborn.start_all()
    _wait_idle(qtbot, reborn)
    assert reborn.job(waiting.id).status is JobStatus.COMPLETED


def test_a_job_rendering_at_crash_time_is_marked_interrupted_never_completed(queue, make_queue, tmp_path):
    job = _enqueue(queue, tmp_path)
    job.mark_rendering()
    job.progress = 80
    partial = Path(partial_path_for(job))
    partial.write_bytes(b"x" * 10)
    queue.store.save(list(queue.jobs))  # état persisté « pendant le rendu », sans arrêt propre
    reborn = make_queue()
    reborn.restore()
    restored = reborn.job(job.id)
    assert restored.status is JobStatus.FAILED and restored.error_kind == ErrorKind.INTERRUPTED
    assert restored.error_message == INTERRUPTED_MESSAGE and restored.result is None
    assert not partial.exists()
    assert reborn.store.load()[0].status is JobStatus.FAILED  # corrigé aussi sur disque
    assert reborn.retry(job.id) and reborn.job(job.id).status is JobStatus.WAITING


def test_waiting_job_without_snapshot_is_failed_on_restore_and_orphans_are_pruned(queue, make_queue, tmp_path):
    job = _enqueue(queue, tmp_path)
    shutil.rmtree(queue.store.snapshot_dir(job.id))
    orphan = queue.store.jobs_dir / "orphelin"
    orphan.mkdir(parents=True)
    (orphan / "project.kut").write_text("{}", encoding="utf-8")
    reborn = make_queue()
    reborn.restore()
    assert reborn.job(job.id).error_kind == ErrorKind.SNAPSHOT_MISSING
    assert not orphan.exists()


def test_restore_with_no_file_is_empty(make_queue):
    assert make_queue().restore() == 0


def test_persistence_failure_does_not_break_the_queue(qtbot, queue, tmp_path, monkeypatch):
    job = _enqueue(queue, tmp_path)

    def broken(_jobs):
        raise OSError("disque en lecture seule")

    monkeypatch.setattr(queue.store, "save", broken)
    queue.start_all()
    _wait_idle(qtbot, queue)
    assert job.status is JobStatus.COMPLETED and "lecture seule" in queue.last_persist_error


# --- Fermeture et processus ---------------------------------------------------------------------------------------------------------------


def test_shutdown_kills_ffmpeg_and_leaves_no_orphan(qtbot, queue, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_FFMPEG_SECONDS", "60")
    pid_file = tmp_path / "pid.txt"
    monkeypatch.setenv("FAKE_FFMPEG_PID_FILE", str(pid_file))
    running, waiting = _enqueue(queue, tmp_path, "r.mp4"), _enqueue(queue, tmp_path, "w.mp4")
    queue.start_all()
    qtbot.waitUntil(lambda: queue._engine.is_running and pid_file.exists()
                    and pid_file.read_text().strip(), timeout=TIMEOUT)  # fichier créé puis écrit : attendre le contenu
    pid = int(pid_file.read_text())
    assert queue.shutdown() is True
    assert not queue._engine.is_running and not _pid_alive(pid)
    assert running.status is JobStatus.CANCELLED and waiting.status is JobStatus.WAITING
    assert not Path(partial_path_for(running)).exists()
    on_disk = {j.id: j.status for j in queue.store.load()}
    assert on_disk == {running.id: JobStatus.CANCELLED, waiting.id: JobStatus.WAITING}
    qtbot.wait(100)  # aucun enchaînement tardif après la fermeture
    assert waiting.status is JobStatus.WAITING and not queue._engine.is_running


def test_engine_shutdown_is_idempotent_and_reports_state(fake_ffmpeg):
    engine = ExportEngine()
    assert not engine.is_running and engine.process_id == 0
    assert engine.shutdown() is True


def test_engine_does_not_start_two_renders(qtbot, queue, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_FFMPEG_SECONDS", "30")
    _enqueue(queue, tmp_path)
    queue.start_all()
    qtbot.waitUntil(lambda: queue._engine.is_running, timeout=TIMEOUT)
    assert not queue.start_all()  # une exécution est déjà active
    queue.stop()
    _wait_idle(qtbot, queue)


def test_late_engine_signals_from_outside_the_queue_are_ignored(queue, tmp_path):
    job = _enqueue(queue, tmp_path)
    queue._engine.finished_ok.emit("/nulle/part.mp4")
    queue._engine.failed.emit("export direct d'un autre appelant")
    queue._engine.progress_changed.emit(50)
    assert job.status is JobStatus.WAITING and job.progress == 0


# --- FFmpeg absent / disponible --------------------------------------------------------------------------------------------------------------


def test_missing_ffmpeg_fails_jobs_with_a_clear_message_and_recovers(qtbot, make_queue, tmp_path, monkeypatch):
    monkeypatch.setattr("core.export_engine._ffmpeg_path", None)
    monkeypatch.setattr("core.export_engine.find_media_tool", lambda _name: None)
    queue = make_queue()
    assert queue.ffmpeg_available() is False
    job = _enqueue(queue, tmp_path)
    queue.start_all()
    _wait_idle(qtbot, queue)
    assert job.status is JobStatus.FAILED and job.error_kind == ErrorKind.FFMPEG_MISSING
    assert "FFmpeg" in job.error_message
    monkeypatch.setattr("core.export_engine._ffmpeg_path", (sys.executable, str(FAKE_FFMPEG)))
    monkeypatch.setenv("FAKE_FFMPEG_SECONDS", "0.2")
    assert queue.ffmpeg_available() is True
    queue.retry(job.id)
    queue.start_all()
    _wait_idle(qtbot, queue)
    assert job.status is JobStatus.COMPLETED


def _real_ffmpeg() -> tuple[str, str]:
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("ffmpeg / ffprobe indisponibles")
    return ffmpeg, ffprobe


def test_real_ffmpeg_renders_a_queued_job_end_to_end(qtbot, make_queue, tmp_path):
    ffmpeg, ffprobe = _real_ffmpeg()
    source = tmp_path / "red.mp4"
    subprocess.run(
        [ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi", "-i",
         "color=c=red:s=160x90:r=15:d=2", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
         "-t", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(source)],
        check=True, capture_output=True,
    )
    project = Project(
        name="Réel", width=160, height=90, fps=15.0,
        media_assets=[MediaAsset(id="r", path=str(source), name="red", duration=2.0,
                                 width=160, height=90, fps=15.0, media_type="video")],
        tracks=[Track(id="V1", name="V1", type="video", clips=[
            Clip(id="c", asset_id="r", track_id="V1", timeline_start=0.0, source_in=0.0, source_out=2.0)])],
    )
    queue = make_queue()
    spec = custom_preset(width=160, height=90, fps=15, quality=28, audio_bitrate="96k")
    job = queue.enqueue(project, spec, _out(tmp_path, "real.mp4"))
    queue.start_all()
    _wait_idle(qtbot, queue, timeout=60000)
    assert job.status is JobStatus.COMPLETED, job.error_message
    output = Path(job.output_path)
    assert output.stat().st_size > 0
    duration = float(subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(output)],
        check=True, capture_output=True, text=True,
    ).stdout.strip())
    assert abs(duration - 2.0) < 0.5
    assert job.result.output_bytes == output.stat().st_size > 0
    assert not Path(partial_path_for(job)).exists()


def test_real_ffmpeg_error_is_reported_readably(qtbot, make_queue, tmp_path):
    _real_ffmpeg()
    project = _project(tmp_path)  # « source.mp4 » n'est pas un vrai média
    queue = make_queue()
    job = queue.enqueue(project, custom_preset(width=160, height=90, fps=15), _out(tmp_path, "bad.mp4"))
    queue.start_all()
    _wait_idle(qtbot, queue, timeout=60000)
    assert job.status is JobStatus.FAILED and job.error_kind == ErrorKind.FFMPEG
    assert job.error_message.strip() and not Path(job.output_path).exists()
    assert not Path(partial_path_for(job)).exists()


# --- correctifs de revue ---------------------------------------------------------------------


def test_start_job_during_a_single_run_also_runs_the_promoted_job(qtbot, queue, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_FFMPEG_SECONDS", "0.5")
    a, b, c = (_enqueue(queue, tmp_path, f"{n}.mp4") for n in "abc")
    log = _status_log(queue)
    queue.start_job(a.id)
    qtbot.waitUntil(lambda: a.status is JobStatus.RENDERING, timeout=TIMEOUT)
    assert queue.start_job(c.id)  # pendant le rendu de « a »
    _wait_idle(qtbot, queue)
    assert a.status is JobStatus.COMPLETED and c.status is JobStatus.COMPLETED
    assert b.status is JobStatus.WAITING  # jamais demandé : reste en attente
    assert [i for i, s in log if s is JobStatus.RENDERING] == [a.id, c.id]


@pytest.mark.parametrize("bad_id", ["../../victime", "/tmp/victime", "a/b", "..", "", "x" * 65, "a b"])
def test_unsafe_job_ids_are_rejected_on_load(bad_id):
    with pytest.raises(ValueError):
        RenderJob.from_dict({"id": bad_id, "snapshot_path": "s", "output_path": "o.mp4"})


def test_a_crafted_queue_file_cannot_delete_outside_the_queue_directory(tmp_path):
    victim = tmp_path / "victime"
    victim.mkdir()
    (victim / "important.txt").write_text("à garder", encoding="utf-8")
    store = RenderQueueStore(tmp_path / "q")
    good = RenderJob.create(spec=default_preset(), snapshot_path="s", output_path="o.mp4")
    store.save([good])
    data = json.loads(store.queue_file.read_text(encoding="utf-8"))
    for bad in ("../../victime", str(victim)):
        data["jobs"].append({**good.to_dict(), "id": bad})
    store.queue_file.write_text(json.dumps(data), encoding="utf-8")
    assert [j.id for j in store.load()] == [good.id]  # les identifiants piégés sont ignorés
    for bad in ("../../victime", str(victim), ".."):
        with pytest.raises(ValueError):
            store.snapshot_dir(bad)
        store.delete_snapshot(bad)  # sans effet
    assert (victim / "important.txt").read_text(encoding="utf-8") == "à garder"
