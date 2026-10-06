"""Render Queue : panneau, page Export et intégration à la fenêtre principale."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QMessageBox

from core.export_engine import ExportEngine
from core.project_model import Clip, MediaAsset, Project, Track
from core.render_job import JobStatus
from core.render_presets import (
    CUSTOM_PRESET_ID,
    builtin_presets,
    default_preset,
    get_preset,
    with_hardware,
)
from core.render_queue import RenderQueue, partial_path_for
from core.render_queue_store import RenderQueueStore
from ui import i18n
from ui.export_panel import ExportPanel
from ui.render_queue_panel import RenderQueuePanel, format_duration, format_size

ROOT = Path(__file__).resolve().parent.parent
FAKE_FFMPEG = ROOT / "tests" / "fixtures" / "fake_ffmpeg.py"
TIMEOUT = 20000


# --- helpers -------------------------------------------------------------------------------------


@pytest.fixture
def fake_ffmpeg(monkeypatch):
    monkeypatch.setattr("core.export_engine._ffmpeg_path", (sys.executable, str(FAKE_FFMPEG)))
    monkeypatch.setenv("FAKE_FFMPEG_SECONDS", "0.3")


def _project(tmp_path: Path, name: str = "Projet UI") -> Project:
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
def queue(tmp_path, fake_ffmpeg):
    queue = RenderQueue(ExportEngine(), RenderQueueStore(tmp_path / "queue"))
    yield queue
    queue.shutdown()


@pytest.fixture
def panel(qtbot, queue):
    widget = RenderQueuePanel(queue)
    qtbot.addWidget(widget)
    widget.show()
    return widget


def _add(queue, tmp_path, name="a.mp4", preset="h264_1080p"):
    return queue.enqueue(_project(tmp_path), get_preset(preset), str(tmp_path / name))


def _wait_idle(qtbot, queue):
    qtbot.waitUntil(lambda: not queue.is_running, timeout=TIMEOUT)


def _rows(panel) -> list[str]:
    tree = panel.tree
    return [tree.topLevelItem(i).data(0, 256) for i in range(tree.topLevelItemCount())]


# --- formats -----------------------------------------------------------------------------------------


def test_format_helpers():
    assert format_duration(None) == "—"
    assert format_duration(7.2) == "7 s" and format_duration(73.4) == "1 min 13 s"
    assert format_duration(3725) == "1 h 02 min"
    assert format_size(512) == "512 o" and format_size(1536) == "1.5 Ko"
    assert format_size(5 * 1024**3) == "5.0 Go"


# --- panneau -----------------------------------------------------------------------------------------


def test_empty_queue_shows_the_hint_and_disables_every_action(panel):
    assert not panel.empty_label.isHidden() and panel.tree.isHidden()
    for button in (panel.start_all_button, panel.start_button, panel.stop_button, panel.cancel_button,
                   panel.retry_button, panel.remove_button, panel.clear_button, panel.up_button,
                   panel.down_button, panel.open_file_button, panel.open_folder_button):
        assert not button.isEnabled()
    assert panel.copy_error_button.isHidden()


def test_panel_lists_waiting_jobs_in_queue_order_with_their_preset(panel, queue, tmp_path):
    a = _add(queue, tmp_path, "a.mp4", "youtube")
    b = _add(queue, tmp_path, "b.mp4", "tiktok")
    assert _rows(panel) == [a.id, b.id]
    first = panel.tree.topLevelItem(0)
    assert first.text(1) == "YouTube" and first.text(4) == "a.mp4"
    assert first.text(2) == i18n.translate("render.status.waiting")
    assert panel.start_all_button.isEnabled()
    assert not panel.tree.isHidden() and panel.empty_label.isHidden()


def test_selecting_a_job_enables_the_matching_actions(panel, queue, tmp_path):
    job = _add(queue, tmp_path)
    panel.select_job(job.id)
    assert panel.start_button.isEnabled() and panel.cancel_button.isEnabled()
    assert panel.remove_button.isEnabled() and panel.up_button.isEnabled()
    assert not panel.retry_button.isEnabled()
    assert job.output_path in panel.detail_label.text()


def test_move_buttons_reorder_the_queue_and_keep_the_selection(panel, queue, tmp_path):
    a, b, c = (_add(queue, tmp_path, f"{n}.mp4") for n in "abc")
    panel.select_job(c.id)
    panel.up_button.click()
    assert _rows(panel) == [a.id, c.id, b.id] and panel.selected_job() is c
    panel.up_button.click()
    assert _rows(panel) == [c.id, a.id, b.id]
    panel.select_job(a.id)
    panel.down_button.click()
    assert _rows(panel) == [c.id, b.id, a.id]


def test_start_all_runs_every_job_and_the_overall_bar_reaches_100(qtbot, panel, queue, tmp_path):
    jobs = [_add(queue, tmp_path, f"{n}.mp4") for n in "ab"]
    panel.start_all_button.click()
    qtbot.waitUntil(lambda: queue.is_running, timeout=TIMEOUT)
    assert panel.stop_button.isEnabled() and not panel.start_all_button.isEnabled()
    _wait_idle(qtbot, queue)
    assert all(j.status is JobStatus.COMPLETED for j in jobs)
    assert panel.overall_bar.value() == 100
    for job in jobs:
        assert panel.row_progress(job.id) == 100
        assert panel.tree.topLevelItem(_rows(panel).index(job.id)).text(2) == i18n.translate("render.status.completed")


def test_start_selected_runs_only_that_job(qtbot, panel, queue, tmp_path):
    a, b = _add(queue, tmp_path, "a.mp4"), _add(queue, tmp_path, "b.mp4")
    panel.select_job(b.id)
    panel.start_button.click()
    _wait_idle(qtbot, queue)
    assert b.status is JobStatus.COMPLETED and a.status is JobStatus.WAITING


def test_progress_updates_one_row_without_rebuilding_the_table(qtbot, panel, queue, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_FFMPEG_SECONDS", "0.8")
    job = _add(queue, tmp_path)
    rebuilds = []
    original = panel.rebuild
    queue.jobs_changed.disconnect(panel.rebuild)
    queue.jobs_changed.connect(lambda: (rebuilds.append(1), original()))
    seen: list[int] = []
    queue.job_updated.connect(lambda _id: seen.append(panel.row_progress(job.id)))
    queue.start_all()
    _wait_idle(qtbot, queue)
    during = sum(1 for value in seen if 0 < value < 100)
    assert during >= 3 and seen == sorted(seen)
    assert len(rebuilds) <= 4  # ajout, passage en rendu, fin : jamais un par tick de progression


def test_cancel_and_remove_buttons(qtbot, panel, queue, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_FFMPEG_SECONDS", "30")
    running, waiting = _add(queue, tmp_path, "r.mp4"), _add(queue, tmp_path, "w.mp4")
    panel.start_all_button.click()
    qtbot.waitUntil(lambda: queue._engine.is_running, timeout=TIMEOUT)
    panel.select_job(running.id)
    assert not panel.remove_button.isEnabled()  # pas de suppression pendant le rendu
    panel.cancel_button.click()
    qtbot.waitUntil(lambda: running.status is JobStatus.CANCELLED, timeout=TIMEOUT)
    panel.stop_button.click()
    _wait_idle(qtbot, queue)
    panel.select_job(waiting.id)
    panel.remove_button.click()
    assert _rows(panel) == [running.id]
    panel.select_job(running.id)
    assert panel.retry_button.isEnabled() and panel.remove_button.isEnabled()
    panel.clear_button.click()  # annulé : nettoyé
    assert _rows(panel) == []


def test_failure_is_shown_readably_and_can_be_copied_and_retried(qtbot, panel, queue, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_FFMPEG_FAIL", "Invalid data found when processing input")
    job = _add(queue, tmp_path)
    queue.start_all()
    _wait_idle(qtbot, queue)
    panel.select_job(job.id)
    text = panel.detail_label.text()
    assert i18n.translate("render.kind.ffmpeg") in text and "Invalid data" in text
    assert not panel.copy_error_button.isHidden()
    panel.copy_error_button.click()
    assert "Invalid data" in QGuiApplication.clipboard().text()
    monkeypatch.delenv("FAKE_FFMPEG_FAIL")
    panel.retry_button.click()
    assert job.status is JobStatus.WAITING
    panel.start_button.click()
    _wait_idle(qtbot, queue)
    assert job.status is JobStatus.COMPLETED


def test_retrying_a_completed_job_asks_before_replacing_the_file(qtbot, panel, queue, tmp_path, monkeypatch):
    job = _add(queue, tmp_path)
    queue.start_all()
    _wait_idle(qtbot, queue)
    panel.select_job(job.id)
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_k: QMessageBox.No)
    panel.retry_button.click()
    assert job.status is JobStatus.COMPLETED
    monkeypatch.setattr(QMessageBox, "question", lambda *_a, **_k: QMessageBox.Yes)
    panel.retry_button.click()
    assert job.status is JobStatus.WAITING


def test_open_file_and_open_folder_buttons(qtbot, panel, queue, tmp_path, monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr("ui.render_queue_panel.open_path", lambda path: opened.append(path) or True)
    job = _add(queue, tmp_path)
    panel.select_job(job.id)
    assert not panel.open_file_button.isEnabled() and panel.open_folder_button.isEnabled()
    queue.start_all()
    _wait_idle(qtbot, queue)
    panel.select_job(job.id)
    assert panel.open_file_button.isEnabled()
    panel.open_file_button.click()
    panel.open_folder_button.click()
    assert opened == [job.output_path, str(Path(job.output_path).parent)]
    Path(job.output_path).unlink()
    panel.select_job(job.id)
    panel._refresh_state()
    assert not panel.open_file_button.isEnabled()
    assert i18n.translate("render.detail.missing_file") in panel.detail_label.text()


def test_ffmpeg_missing_banner_disables_starting(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr("core.export_engine._ffmpeg_path", None)
    monkeypatch.setattr("core.export_engine.find_media_tool", lambda _name: None)
    queue = RenderQueue(ExportEngine(), RenderQueueStore(tmp_path / "queue"))
    panel = RenderQueuePanel(queue)
    qtbot.addWidget(panel)
    panel.show()
    assert not panel.ffmpeg_banner.isHidden()
    job = _add(queue, tmp_path)
    panel.select_job(job.id)
    assert not panel.start_all_button.isEnabled() and not panel.start_button.isEnabled()
    assert panel.remove_button.isEnabled()  # on peut toujours gérer la file


def test_ffmpeg_available_hides_the_banner(panel):
    assert panel.ffmpeg_banner.isHidden()


def test_panel_follows_the_language_live(panel, queue, tmp_path):
    original = i18n.current_language()
    try:
        _add(queue, tmp_path)
        i18n.set_language("en")
        assert panel.title_label.text() == "RENDER QUEUE"
        assert panel.start_all_button.text() == "Start all"
        assert panel.tree.topLevelItem(0).text(2) == "Waiting"
        assert panel.tree.headerItem().text(2) == "Status"
        i18n.set_language("es")
        assert panel.start_all_button.text() == "Iniciar todo"
    finally:
        i18n.set_language(original)


def test_every_render_key_exists_in_all_languages():
    for key in (
        [f"render.status.{s.value}" for s in JobStatus]
        + [f"render.preset.{spec.id}" for spec in builtin_presets()]
        + [f"render.preset.desc.{spec.id}" for spec in builtin_presets()]
        + [f"render.preset.{CUSTOM_PRESET_ID}", f"render.preset.desc.{CUSTOM_PRESET_ID}"]
        + [f"render.kind.{k}" for k in ("ffmpeg", "ffmpeg_missing", "invalid", "interrupted", "snapshot_missing", "io")]
    ):
        entry = i18n._TRANSLATIONS[key]
        assert set(entry) == {"fr", "en", "es"} and all(entry.values()), key


# --- page Export -----------------------------------------------------------------------------------------


@pytest.fixture
def export_panel(qtbot):
    widget = ExportPanel()
    qtbot.addWidget(widget)
    widget.show()
    return widget


def test_export_panel_offers_every_required_preset_and_custom(export_panel):
    ids = [export_panel.preset_combo.itemData(i) for i in range(export_panel.preset_combo.count())]
    assert ids == [s.id for s in builtin_presets()] + [CUSTOM_PRESET_ID]
    # Le sélecteur d'encodeur est « Automatique » par défaut ; le reste du preset est inchangé.
    assert export_panel.current_spec() == with_hardware(default_preset(), "auto")


def test_choosing_a_preset_changes_the_request_the_engine_receives(export_panel, tmp_path):
    from core.render_plan import build_render_plan

    plan = build_render_plan(_project(tmp_path))
    for preset_id in ("youtube", "tiktok", "prores_master", "h264_4k"):
        export_panel.preset_combo.setCurrentIndex(export_panel.preset_combo.findData(preset_id))
        spec = get_preset(preset_id)
        request = export_panel.build_request(plan, str(tmp_path / "x.mp4"))
        assert request.preset.resolution == spec.resolution and request.fps == spec.fps
        assert request.format.container == spec.container
        # ProRes n'a que l'encodeur CPU : « Automatique » n'y est pas proposé.
        assert request.hardware == ("cpu" if spec.video_codec == "prores_ks" else "auto")


def test_custom_controls_only_show_for_the_custom_preset(export_panel):
    assert export_panel.custom_frame.isHidden()
    export_panel.preset_combo.setCurrentIndex(export_panel.preset_combo.findData(CUSTOM_PRESET_ID))
    assert not export_panel.custom_frame.isHidden()
    export_panel.resolution_combo.setCurrentText("1280 × 720 (HD)")
    export_panel.fps_combo.setCurrentText("24")
    export_panel.quality_combo.setCurrentText("Élevée")
    spec = export_panel.current_spec()
    assert spec.id == CUSTOM_PRESET_ID and spec.resolution == (1280, 720)
    assert spec.fps == 24 and spec.quality == 18


def test_export_panel_keeps_its_historical_api(export_panel):
    for name in ("format_combo", "resolution_combo", "quality_combo", "fps_combo", "launch_button",
                 "cancel_button", "progress_bar", "status_label"):
        assert hasattr(export_panel, name)
    for name in ("set_status", "mark_export_started", "mark_export_finished",
                 "mark_export_error", "mark_export_cancelled", "build_request"):
        assert callable(getattr(export_panel, name))
    assert export_panel.launch_button.isEnabled() and not export_panel.cancel_button.isEnabled()


def test_export_panel_mirrors_the_running_job(qtbot, export_panel, queue, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_FFMPEG_SECONDS", "30")
    export_panel.set_queue(queue)
    job = _add(queue, tmp_path)
    queue.start_all()
    qtbot.waitUntil(lambda: queue._engine.is_running, timeout=TIMEOUT)
    assert export_panel.cancel_button.isEnabled() and not export_panel.progress_bar.isHidden()
    assert job.name in export_panel.status_label.text()
    export_panel.cancel_button.click()
    queue.cancel(job.id)
    _wait_idle(qtbot, queue)
    assert not export_panel.cancel_button.isEnabled()


# --- fenêtre principale -----------------------------------------------------------------------------------------


def _window(qtbot, monkeypatch, tmp_path, *, project=True):
    from ui.main_window import MainWindow

    calls = {"information": [], "question": []}
    monkeypatch.setattr(
        "ui.main_window.QMessageBox.information", lambda *a, **_k: calls["information"].append(a[2])
    )
    monkeypatch.setattr("ui.main_window.QMessageBox.critical", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "ui.main_window.QMessageBox.question",
        lambda *a, **_k: calls["question"].append(a[2]) or calls.get("answer", QMessageBox.Yes),
    )
    window = MainWindow()
    qtbot.addWidget(window)
    window.timeline_timer.stop()
    if project:
        window.project = _project(tmp_path)
        window.timeline_panel.set_project(window.project)
    window._calls = calls
    return window


def _choose_file(monkeypatch, path: Path):
    monkeypatch.setattr(
        "ui.main_window.QFileDialog.getSaveFileName", lambda *_a, **_k: (str(path), "")
    )


def test_launch_export_goes_through_the_queue_in_one_step(qtbot, fake_ffmpeg, monkeypatch, tmp_path):
    window = _window(qtbot, monkeypatch, tmp_path)
    _choose_file(monkeypatch, tmp_path / "film.mp4")
    window.export_panel.preset_combo.setCurrentIndex(window.export_panel.preset_combo.findData("youtube"))
    window.launch_export()
    queue = window.render_queue
    assert len(queue.jobs) == 1 and queue.jobs[0].preset_id == "youtube"
    qtbot.waitUntil(lambda: not queue.is_running, timeout=TIMEOUT)
    job = queue.jobs[0]
    assert job.status is JobStatus.COMPLETED and Path(job.output_path).exists()
    assert len(window._calls["information"]) == 1 and job.output_path in window._calls["information"][0]
    assert window.export_panel.status_label.text() == i18n.translate("render.export.done")


def test_add_to_queue_does_not_start_and_several_exports_can_be_queued(qtbot, fake_ffmpeg, monkeypatch, tmp_path):
    window = _window(qtbot, monkeypatch, tmp_path)
    for name, preset in (("a.mp4", "h264_1080p"), ("b.mp4", "tiktok"), ("c.mov", "prores_master")):
        _choose_file(monkeypatch, tmp_path / name)
        window.export_panel.preset_combo.setCurrentIndex(window.export_panel.preset_combo.findData(preset))
        window.enqueue_export()
    queue = window.render_queue
    assert [j.status for j in queue.jobs] == [JobStatus.WAITING] * 3 and not queue.is_running
    assert [j.preset_id for j in queue.jobs] == ["h264_1080p", "tiktok", "prores_master"]
    assert queue.jobs[2].output_path.endswith("c.mov")
    queue.start_all()
    qtbot.waitUntil(lambda: not queue.is_running, timeout=TIMEOUT)
    assert all(j.status is JobStatus.COMPLETED for j in queue.jobs)
    assert "3" in window._calls["information"][0]  # résumé de file


def test_the_queue_renders_the_version_added_even_if_the_project_changes_after(qtbot, fake_ffmpeg, monkeypatch, tmp_path):
    window = _window(qtbot, monkeypatch, tmp_path)
    _choose_file(monkeypatch, tmp_path / "frozen.mp4")
    window.enqueue_export()
    window.project.tracks[0].clips[0].source_out = 9.0
    from core.project_io import load_project

    assert load_project(window.render_queue.jobs[0].snapshot_path).tracks[0].clips[0].source_out == 4.0


def test_export_with_nothing_to_render_reports_the_error_and_adds_nothing(qtbot, fake_ffmpeg, monkeypatch, tmp_path):
    window = _window(qtbot, monkeypatch, tmp_path, project=False)
    window.project = Project(name="vide", width=1920, height=1080, fps=30.0, media_assets=[], tracks=[])
    _choose_file(monkeypatch, tmp_path / "x.mp4")
    window.launch_export()
    assert window.render_queue.jobs == ()
    assert "Aucun média" in window.export_panel.status_label.text()


def test_cancelling_the_file_dialog_does_nothing(qtbot, fake_ffmpeg, monkeypatch, tmp_path):
    window = _window(qtbot, monkeypatch, tmp_path)
    _choose_file(monkeypatch, Path(""))
    window.launch_export()
    assert window.render_queue.jobs == ()


def test_cancel_export_button_cancels_the_running_queue_job(qtbot, fake_ffmpeg, monkeypatch, tmp_path):
    monkeypatch.setenv("FAKE_FFMPEG_SECONDS", "30")
    window = _window(qtbot, monkeypatch, tmp_path)
    _choose_file(monkeypatch, tmp_path / "long.mp4")
    window.launch_export()
    queue = window.render_queue
    qtbot.waitUntil(lambda: queue._engine.is_running, timeout=TIMEOUT)
    window.export_panel.cancel_button.click()
    qtbot.waitUntil(lambda: not queue.is_running, timeout=TIMEOUT)
    assert queue.jobs[0].status is JobStatus.CANCELLED
    assert window._calls["information"] == []  # annulation d'un seul job : pas de popup
    assert window.export_panel.status_label.text() == i18n.translate("render.export.cancelled")


def test_single_failed_export_shows_the_error_without_a_popup(qtbot, fake_ffmpeg, monkeypatch, tmp_path):
    monkeypatch.setenv("FAKE_FFMPEG_FAIL", "boom")
    window = _window(qtbot, monkeypatch, tmp_path)
    _choose_file(monkeypatch, tmp_path / "bad.mp4")
    window.launch_export()
    qtbot.waitUntil(lambda: not window.render_queue.is_running, timeout=TIMEOUT)
    assert window.render_queue.jobs[0].status is JobStatus.FAILED
    assert window._calls["information"] == []
    assert "Échec" in window.export_panel.status_label.text()


def test_direct_engine_export_still_works(qtbot, fake_ffmpeg, monkeypatch, tmp_path):
    from core.export_engine import ExportRequest
    from core.render_plan import build_render_plan

    window = _window(qtbot, monkeypatch, tmp_path)
    spec = get_preset("h264_1080p")
    fmt, preset, fps = spec.export_parts()
    output = tmp_path / "direct.mp4"
    done: list[str] = []
    window.export_engine.finished_ok.connect(done.append)
    window.export_engine.start(ExportRequest(
        render_plan=build_render_plan(window.project), output_path=str(output),
        format=fmt, preset=preset, fps=fps,
    ))
    qtbot.waitUntil(lambda: bool(done), timeout=TIMEOUT)
    assert output.exists() and window.render_queue.jobs == ()  # la file n'a pas été touchée


def test_queue_survives_a_window_restart(qtbot, fake_ffmpeg, monkeypatch, tmp_path):
    first = _window(qtbot, monkeypatch, tmp_path)
    _choose_file(monkeypatch, tmp_path / "later.mp4")
    first.enqueue_export()
    job_id = first.render_queue.jobs[0].id
    first.close()
    second = _window(qtbot, monkeypatch, tmp_path, project=False)
    assert [j.id for j in second.render_queue.jobs] == [job_id]
    assert second.export_panel.queue_panel.tree.topLevelItemCount() == 1


# --- fermeture ------------------------------------------------------------------------------------------------------


def test_closing_without_a_render_never_asks(qtbot, fake_ffmpeg, monkeypatch, tmp_path):
    window = _window(qtbot, monkeypatch, tmp_path)
    _choose_file(monkeypatch, tmp_path / "w.mp4")
    window.enqueue_export()  # un job en attente ne bloque pas la fermeture
    assert window.close()
    assert window._calls["question"] == []
    assert window.render_queue.jobs[0].status is JobStatus.WAITING


def test_closing_during_a_render_can_be_cancelled(qtbot, fake_ffmpeg, monkeypatch, tmp_path):
    monkeypatch.setenv("FAKE_FFMPEG_SECONDS", "30")
    window = _window(qtbot, monkeypatch, tmp_path)
    window._calls["answer"] = QMessageBox.No
    _choose_file(monkeypatch, tmp_path / "long.mp4")
    window.launch_export()
    queue = window.render_queue
    qtbot.waitUntil(lambda: queue._engine.is_running, timeout=TIMEOUT)
    assert not window.close()  # refusé : la fenêtre reste ouverte
    assert window._calls["question"] and queue.is_busy and queue._engine.is_running
    assert queue.jobs[0].status is JobStatus.RENDERING
    queue.stop()
    qtbot.waitUntil(lambda: not queue.is_running, timeout=TIMEOUT)


def test_closing_during_a_render_confirmed_stops_ffmpeg_cleanly(qtbot, fake_ffmpeg, monkeypatch, tmp_path):
    monkeypatch.setenv("FAKE_FFMPEG_SECONDS", "60")
    pid_file = tmp_path / "pid.txt"
    monkeypatch.setenv("FAKE_FFMPEG_PID_FILE", str(pid_file))
    window = _window(qtbot, monkeypatch, tmp_path)
    for name in ("run.mp4", "wait.mp4"):
        _choose_file(monkeypatch, tmp_path / name)
        window.enqueue_export()
    queue = window.render_queue
    queue.start_all()
    qtbot.waitUntil(lambda: queue._engine.is_running and pid_file.exists()
                    and bool(pid_file.read_text().strip()), timeout=TIMEOUT)  # créé puis écrit : attendre le contenu
    pid = int(pid_file.read_text())
    assert window.close()
    text = window._calls["question"][0]
    assert "1" in text  # un export en attente sera conservé
    assert not queue._engine.is_running
    if sys.platform != "win32":
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    running, waiting = queue.jobs
    assert running.status is JobStatus.CANCELLED and waiting.status is JobStatus.WAITING
    assert not Path(partial_path_for(running)).exists()
    on_disk = [(j.id, j.status) for j in queue.store.load()]
    assert on_disk == [(running.id, JobStatus.CANCELLED), (waiting.id, JobStatus.WAITING)]


# --- fluidité -------------------------------------------------------------------------------------------------------------


def test_the_event_loop_stays_responsive_during_a_render(qtbot, fake_ffmpeg, monkeypatch, tmp_path):
    monkeypatch.setenv("FAKE_FFMPEG_SECONDS", "2.0")
    window = _window(qtbot, monkeypatch, tmp_path)
    window.show()
    _choose_file(monkeypatch, tmp_path / "smooth.mp4")
    stamps: list[float] = []
    ticker = QTimer()
    ticker.setInterval(20)
    ticker.timeout.connect(lambda: stamps.append(time.perf_counter()))
    ticker.start()
    started = time.perf_counter()
    window.launch_export()
    launch_call = time.perf_counter() - started
    queue = window.render_queue
    qtbot.waitUntil(lambda: not queue.is_running, timeout=TIMEOUT)
    ticker.stop()
    assert launch_call < 0.5  # le clic rend la main immédiatement
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    assert len(stamps) > 30  # ~2 s de rendu à 20 ms de période
    assert max(gaps) < 0.35  # jamais de gel perceptible de la boucle Qt
    assert queue.jobs[0].status is JobStatus.COMPLETED


def test_social_options_follow_the_preset_and_can_be_unticked(export_panel):
    combo = export_panel.preset_combo
    combo.setCurrentIndex(combo.findData("reels"))
    checks = (export_panel.loudness_check, export_panel.preview_copy_check, export_panel.cover_check)
    assert all(check.isChecked() for check in checks)
    spec = export_panel.current_spec()
    assert (spec.loudness_lufs, spec.preview_copy, spec.cover) == (-14.0, True, True)
    export_panel.preview_copy_check.setChecked(False)
    assert export_panel.current_spec().preview_copy is False and export_panel.current_spec().cover is True
    combo.setCurrentIndex(combo.findData("youtube"))
    assert not any(check.isChecked() for check in checks)
    assert all(check.text() and not check.text().startswith("render.") for check in checks)
