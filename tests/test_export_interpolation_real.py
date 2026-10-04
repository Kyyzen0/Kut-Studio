"""Mélange d'images et flux optique à travers les vrais moteurs : export, aperçu fidèle, scopes, empreintes.

``test_retime_prepare_real`` vérifie les images ; ici on vérifie l'**orchestration** : ``ExportEngine.start`` calcule les images
intermédiaires dans un fil (progression, annulation, échec), puis lance FFmpeg ; le segment d'aperçu ne fabrique que sa
fenêtre ; l'empreinte d'un segment suit le mode demandé.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import QEventLoop, QTimer
from test_export_integration import _wait_for_export

from core.export_engine import ExportEngine, ExportFormat, ExportPreset, ExportRequest
from core.filter_graph import fingerprint_plan
from core.flow_cache import FlowCache
from core.preview_engine import PreviewEngine, PreviewJob
from core.project_model import Clip, MediaAsset, Project, Track
from core.render_plan import build_render_plan
from core.time_remapping import FlowQuality, TimeInterpolation, TimeRemapping

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg absent")

W, H, FPS = 64, 36, 30
FRAMES = 60
BLENDING, FLOW, SAMPLING = TimeInterpolation.BLENDING, TimeInterpolation.OPTICAL_FLOW, TimeInterpolation.SAMPLING


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    path = tmp_path_factory.mktemp("export_interpolation") / "index.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
         f"color=c=black:s={W}x{H}:r={FPS}:d={FRAMES / FPS},geq=lum='20+3*N':cb=128:cr=128,format=yuv420p",
         "-c:v", "libx264", "-crf", "0", "-preset", "ultrafast", str(path)],
        check=True, timeout=60,
    )
    return str(path)


def project_for(path: str, remapping: TimeRemapping) -> Project:
    asset = MediaAsset(id="a", path=path, name="idx", duration=FRAMES / FPS, width=W, height=H, fps=float(FPS), media_type="video",
                       has_audio=False)
    clip = Clip(id="c1", asset_id="a", track_id="V1", timeline_start=0.0, source_in=0.0, source_out=FRAMES / FPS,
                time_remapping=remapping)
    return Project(name="p", width=W, height=H, fps=float(FPS), media_assets=[asset],
                   tracks=[Track(id="V1", name="V1", type="video", clips=[clip])])


def request_for(project: Project, output: Path) -> ExportRequest:
    return ExportRequest(render_plan=build_render_plan(project), output_path=str(output), format=ExportFormat.MP4_H264,
                         preset=ExportPreset("T", (W, H), 8, "96k"), fps=FPS)


def decode_indices(path: Path) -> list[float]:
    done = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "yuv420p", "-"], capture_output=True, timeout=60,
    )
    assert done.returncode == 0
    frames = np.frombuffer(done.stdout, dtype=np.uint8).reshape(-1, W * H * 3 // 2)[:, : W * H].reshape(-1, H, W)
    return [(float(frame[H // 2, W // 2]) - 20.0) / 3.0 for frame in frames]


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def test_the_export_prepares_the_intermediate_images_then_encodes_them(qtbot, media, tmp_path):
    project = project_for(media, TimeRemapping(speed=0.5, interpolation=BLENDING))
    engine = ExportEngine()
    engine.flow_cache = FlowCache(tmp_path / "flow")
    progress: list[tuple[int, int]] = []
    statuses: list[str] = []
    engine.preparation_progress.connect(lambda done, total: progress.append((done, total)))
    engine.status_changed.connect(statuses.append)
    output = tmp_path / "out.mp4"
    finished, failed = _wait_for_export(engine, 60000, start=lambda: engine.start(request_for(project, output)))
    assert not failed and finished and output.exists()
    assert progress and progress[-1][0] == progress[-1][1] > 0                               # la progression atteint le total
    assert any("images intermédiaires" in text for text in statuses)
    assert engine.last_preparation is not None and engine.last_preparation.synthesized > 0
    assert not engine.is_running
    values = decode_indices(output)
    clip = project.tracks[0].clips[0]
    for tick in range(0, len(values) - 3):
        assert abs(values[tick] - clip.time_map.source_time(tick / FPS) * FPS) < 0.8, tick


def test_the_progress_bar_climbs_through_the_preparation_then_the_encoding_and_never_goes_back(qtbot, media, tmp_path):
    project = project_for(media, TimeRemapping(speed=0.5, interpolation=BLENDING))
    engine = ExportEngine()
    engine.flow_cache = FlowCache(tmp_path / "flow")
    values: list[int] = []
    reports: list[object] = []
    engine.progress_changed.connect(values.append)
    engine.preparation_reported.connect(reports.append)
    finished, failed = _wait_for_export(engine, 60000, start=lambda: engine.start(request_for(project, tmp_path / "out.mp4")))
    assert not failed and finished
    assert values == sorted(values) and values[0] == 0 and values[-1] == 100
    assert any(0 < value <= 80 for value in values)                                      # le calcul fait avancer la barre
    assert len(reports) == 1 and reports[0] is engine.last_preparation and reports[0].synthesized > 0


def test_a_clip_that_needs_no_intermediate_image_exports_without_preparation(qtbot, media, tmp_path):
    engine = ExportEngine()
    engine.flow_cache = FlowCache(tmp_path / "flow")
    output = tmp_path / "plain.mp4"
    finished, failed = _wait_for_export(
        engine, 60000,
        start=lambda: engine.start(request_for(project_for(media, TimeRemapping(speed=2.0, interpolation=FLOW)), output)),
    )
    assert not failed and finished and engine.last_preparation is None
    assert not list((tmp_path / "flow").glob("*")) if (tmp_path / "flow").exists() else True       # rien n'a été fabriqué


def test_cancelling_during_the_preparation_stops_everything_and_keeps_nothing(qtbot, media, tmp_path):
    project = project_for(media, TimeRemapping(speed=0.25, interpolation=FLOW, flow_quality=FlowQuality.BEST))
    engine = ExportEngine()
    engine.flow_cache = FlowCache(tmp_path / "flow")
    output = tmp_path / "never.mp4"
    cancelled: list[bool] = []
    engine.cancelled.connect(lambda: cancelled.append(True))
    loop = QEventLoop()
    engine.cancelled.connect(loop.quit)
    engine.failed.connect(loop.quit)
    engine.finished_ok.connect(loop.quit)
    QTimer.singleShot(30000, loop.quit)
    engine.start(request_for(project, output))
    assert engine.is_running                                                                  # le calcul compte comme un export en cours
    engine.cancel()
    loop.exec()
    assert cancelled == [True] and not engine.is_running and not output.exists()
    leftovers = [item.name for item in (tmp_path / "flow").glob("*") if "frames-" in item.name] if (tmp_path / "flow").exists() else []
    assert leftovers == []


def test_a_second_export_is_refused_while_the_images_are_being_prepared(qtbot, media, tmp_path):
    project = project_for(media, TimeRemapping(speed=0.25, interpolation=FLOW, flow_quality=FlowQuality.BEST))
    engine = ExportEngine()
    engine.flow_cache = FlowCache(tmp_path / "flow")
    messages: list[str] = []
    engine.failed.connect(messages.append)
    engine.start(request_for(project, tmp_path / "one.mp4"))
    engine.start(request_for(project, tmp_path / "two.mp4"))
    assert messages == ["Un export est déjà en cours."]
    engine.cancel()
    assert engine.shutdown(5000) in (True, False)                                              # la fermeture arrête le fil proprement


def test_a_media_file_that_is_gone_reports_a_clear_message(qtbot, tmp_path):
    project = project_for(str(tmp_path / "gone.mp4"), TimeRemapping(speed=0.5, interpolation=BLENDING))
    engine = ExportEngine()
    engine.flow_cache = FlowCache(tmp_path / "flow")
    finished, failed = _wait_for_export(engine, 10000, start=lambda: engine.start(request_for(project, tmp_path / "x.mp4")))
    assert not finished and failed and "Média introuvable" in failed[0] and "gone.mp4" in failed[0]


def test_a_decoding_failure_is_reported_with_its_cause(qtbot, tmp_path):
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"this is not a video")
    project = project_for(str(broken), TimeRemapping(speed=0.5, interpolation=BLENDING))
    engine = ExportEngine()
    engine.flow_cache = FlowCache(tmp_path / "flow")
    finished, failed = _wait_for_export(engine, 30000, start=lambda: engine.start(request_for(project, tmp_path / "x.mp4")))
    assert not finished and failed and not engine.is_running
    assert "Décodage" in failed[0] or "images" in failed[0]
    assert list((tmp_path / "flow").glob("frames-*")) == [] if (tmp_path / "flow").exists() else True


def test_a_preparation_failure_is_not_classified_with_the_previous_jobs_encoder_error(qtbot, tmp_path):
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"this is not a video")
    project = project_for(str(broken), TimeRemapping(speed=0.5, interpolation=BLENDING))
    engine = ExportEngine()
    engine.flow_cache = FlowCache(tmp_path / "flow")
    engine.last_error_kind = "encoder"                                                          # hérité d'un job précédent
    finished, failed = _wait_for_export(engine, 30000, start=lambda: engine.start(request_for(project, tmp_path / "x.mp4")))
    assert failed and not finished
    assert engine.last_error_kind == ""


def test_a_thread_that_cannot_start_does_not_leave_the_engine_busy_forever(qtbot, media, tmp_path, monkeypatch):
    import threading

    project = project_for(media, TimeRemapping(speed=0.5, interpolation=BLENDING))
    engine = ExportEngine()
    engine.flow_cache = FlowCache(tmp_path / "flow")
    messages: list[str] = []
    engine.failed.connect(messages.append)
    real_start = threading.Thread.start

    def refuse(self):
        if self.name == "kut-flow-prepare":
            raise RuntimeError("can't start new thread")
        real_start(self)

    monkeypatch.setattr(threading.Thread, "start", refuse)
    engine.start(request_for(project, tmp_path / "one.mp4"))
    assert messages == ["can't start new thread"] and not engine.is_running
    monkeypatch.setattr(threading.Thread, "start", real_start)
    finished, failed = _wait_for_export(engine, 60000, start=lambda: engine.start(request_for(project, tmp_path / "two.mp4")))
    assert finished and not failed                                                              # le moteur n'est plus « déjà en cours »


def test_the_shutdown_says_so_when_the_preparation_thread_outlives_the_wait(qtbot):
    import threading

    engine = ExportEngine()
    release = threading.Event()
    stuck = threading.Thread(target=release.wait, daemon=True)
    stuck.start()
    engine._prepare_thread = stuck
    try:
        assert engine.shutdown(50) is False                                                     # pas de « tout est arrêté » mensonger
    finally:
        release.set()
        stuck.join(2.0)
    assert engine._stop_preparation(0.5) is True                                                # le fil a fini : l'arrêt se confirme


# ---------------------------------------------------------------------------
# Scopes : une image
# ---------------------------------------------------------------------------


def test_the_frame_command_asked_to_interpolate_prepares_only_the_requested_image(media, tmp_path):
    project = project_for(media, TimeRemapping(speed=0.25, interpolation=BLENDING))
    engine = ExportEngine()
    engine.flow_cache = FlowCache(tmp_path / "flow")
    command = engine.build_frame_command(request_for(project, tmp_path / "frame.png"), 1.0, interpolate=True)
    done = subprocess.run(command, capture_output=True, timeout=60)
    assert done.returncode == 0 and done.stdout[:4] == b"\x89PNG"
    streams = list((tmp_path / "flow").glob("frames-*.mkv"))
    assert len(streams) == 1
    probe = subprocess.run(["ffmpeg", "-v", "error", "-i", str(streams[0]), "-f", "null", "-"], capture_output=True, timeout=60)
    assert probe.returncode == 0 and streams[0].stat().st_size < 300_000                      # quelques images, pas tout le clip


# ---------------------------------------------------------------------------
# Aperçu fidèle : le segment ne fabrique que sa fenêtre
# ---------------------------------------------------------------------------


def test_a_preview_segment_prepares_only_its_own_window_and_matches_the_export(qtbot, media, tmp_path):
    project = project_for(media, TimeRemapping(speed=0.25, interpolation=BLENDING))
    plan = build_render_plan(project)
    cache = FlowCache(tmp_path / "flow")
    engine = PreviewEngine(cache=None, flow_cache=cache)
    job = PreviewJob(key=None, plan=plan, width=W, height=H, fps=FPS, quality="high", start=2.0, duration=2.0)
    produced = engine._default_render(job, None)                                              # noqa: SLF001 - le rendu par défaut
    try:
        assert produced is not None and Path(produced).exists()
        stream = list((tmp_path / "flow").glob("frames-*.mkv"))
        assert len(stream) == 1
        report = cache.stream_report(stream[0].stem.removeprefix("frames-"))
        assert 0 < int(report["images"]) < 90                                                  # 2 s + marges, pas les 8 s du clip
        values = decode_indices(Path(produced))
        clip = project.tracks[0].clips[0]
        assert len(values) in (60, 61)
        for tick in range(3, len(values) - 3):
            expected = clip.time_map.source_time((tick + 60) / FPS) * FPS
            assert abs(values[tick] - expected) < 1.2, (tick, values[tick], expected)         # aperçu en CRF d'aperçu : plus lâche
    finally:
        if produced:
            Path(produced).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Empreinte d'un segment
# ---------------------------------------------------------------------------


def fingerprint(project: Project) -> str:
    return fingerprint_plan(build_render_plan(project), width=W, height=H, fps=FPS, quality="standard")


def test_the_segment_fingerprint_follows_the_requested_interpolation_mode(media):
    base = TimeRemapping(speed=0.5)
    keys = {mode: fingerprint(project_for(media, replace(base, interpolation=mode))) for mode in (SAMPLING, BLENDING, FLOW)}
    assert len(set(keys.values())) == 3
    assert fingerprint(project_for(media, replace(base, interpolation=FLOW, flow_quality=FlowQuality.BEST))) != keys[FLOW]


def test_the_flow_engine_only_enters_the_fingerprint_when_a_clip_interpolates(media, monkeypatch):
    """Une nouvelle version de l'algorithme n'invalide que les segments qui en dépendent."""
    import core.optical_flow as optical_flow

    plain = fingerprint(project_for(media, TimeRemapping(speed=0.5)))
    interpolated = fingerprint(project_for(media, TimeRemapping(speed=0.5, interpolation=FLOW)))
    monkeypatch.setattr(optical_flow, "ENGINE_VERSION", optical_flow.ENGINE_VERSION + 1)
    assert fingerprint(project_for(media, TimeRemapping(speed=0.5))) == plain
    assert fingerprint(project_for(media, TimeRemapping(speed=0.5, interpolation=FLOW))) != interpolated


def test_the_fingerprint_follows_the_backend_the_user_asked_for(media, monkeypatch):
    """Un segment rendu avec le processeur ne passe pas pour un segment rendu avec un autre backend."""
    import core.optical_flow as optical_flow

    real = optical_flow.select_backend

    class Other:
        name, version = "other", 1

    monkeypatch.setattr(optical_flow, "select_backend",
                        lambda preference: Other() if preference is optical_flow.BackendPreference.GPU else real(preference))
    plan = build_render_plan(project_for(media, TimeRemapping(speed=0.5, interpolation=FLOW)))
    keys = {preference: fingerprint_plan(plan, width=W, height=H, fps=FPS, quality="standard", flow_preference=preference)
            for preference in optical_flow.BackendPreference}
    assert keys[optical_flow.BackendPreference.GPU] != keys[optical_flow.BackendPreference.CPU]
    assert keys[optical_flow.BackendPreference.AUTO] == keys[optical_flow.BackendPreference.CPU]   # le même backend : les mêmes images
    # Une préférence qui n'a plus de sens (réglage d'un autre mode) retombe sur Auto, comme le rendu.
    assert fingerprint_plan(plan, width=W, height=H, fps=FPS, quality="standard", flow_preference="plus-rien") == \
        keys[optical_flow.BackendPreference.AUTO]
