"""Lecture seule appliquée aux entrées du cœur (ADR-0013, étape 12) : modèle, export, file de rendu, livrables, scripts, CLI, Film.

Chaque entrée est testée séparément, contrôle exigé (Store) puis hors Store. Le refus ne change rien (ni projet, ni historique,
ni fichier) ; hors Store, rien ne change.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from core import cli
from core.edit_history import ProjectHistory
from core.entitlements import EntitlementRefused, ExportBlockedError, ReadOnlyError
from core.project_io import project_payload, save_project
from core.project_model import MediaAsset, Project, Track
from core.scripting import Session, apply_script, parse_script
from core.timeline_operations import add_clip_to_track
from tests.entitlements_support import DAY, T0, Clock, expired, install, lifetime, store_flavor, trial  # noqa: F401 - fixtures
from ui import i18n

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg absent")


@pytest.fixture(autouse=True)
def _french(monkeypatch):
    monkeypatch.setenv("KUT_STUDIO_LANG", "fr")
    i18n.reset_for_tests()
    yield
    i18n.reset_for_tests()


@pytest.fixture(scope="module")
def media(tmp_path_factory) -> str:
    if shutil.which("ffmpeg") is None:
        pytest.skip("FFmpeg absent : ces projets référencent un vrai média")
    path = tmp_path_factory.mktemp("ent") / "source.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=30:d=1,format=yuv420p", "-f", "lavfi",
         "-i", "sine=frequency=440:duration=1", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(path)],
        check=True, timeout=60)
    return str(path)


def _project(media_path: str = "/x/m.mp4") -> Project:
    asset = MediaAsset("m", media_path, "source", 1.0, 320, 180, 30.0, "video", has_audio=True)
    project = Project("Démo", width=320, height=180, fps=30.0, media_assets=[asset],
                      tracks=[Track("V1", "V1", "video"), Track("A1", "A1", "audio")])
    add_clip_to_track(project, "m", "V1", 0.0)
    return project


@pytest.fixture
def kut(media, tmp_path) -> Path:
    path = tmp_path / "demo.kut"
    save_project(_project(media), str(path))
    return path


READ_ONLY = expired()          # essai fini : lecture seule
PAID = lifetime()


# -- 1. le modèle : l'historique ------------------------------------------------------------------------------------------------


def _edited_history(install_service):
    """Un projet, son historique, puis une édition faite *en place* (comme l'interface) mais pas encore enregistrée."""
    install_service(PAID)                                                    # permis le temps de préparer
    project = _project()
    history = ProjectHistory()
    history.reset(project)
    project.name = "Modifié"
    return project, history


def test_history_record_refuses_in_read_only_and_puts_the_project_back(install):
    project, history = _edited_history(install)
    install(READ_ONLY)
    with pytest.raises(ReadOnlyError):
        history.record(project, "Renommer")
    assert project.name == "Démo", "l'édition non enregistrée est annulée EN PLACE (même objet)"
    assert len(history) == 1 and history.is_dirty is False


def test_history_record_calls_the_listener_instead_of_raising(install):
    project, history = _edited_history(install)
    install(READ_ONLY)
    seen = []
    history.on_edit_refused = seen.append
    history.record(project, "Renommer")                                      # ne lève pas : l'interface a un écouteur
    assert [type(item) for item in seen] == [ReadOnlyError] and project.name == "Démo" and len(history) == 1


def test_history_record_works_when_allowed(install):
    project, history = _edited_history(install)
    install(trial(T0))
    history.record(project, "Renommer")
    assert project.name == "Modifié" and len(history) == 2


@pytest.mark.parametrize("operation", ["undo", "redo", "go_to"])
def test_history_undo_redo_jump_are_refused_in_read_only(install, operation):
    project, history = _edited_history(install)
    history.record(project, "Renommer")
    project.name = "Encore"
    history.record(project, "Renommer encore")
    history.undo()                                                           # peut annuler ET rétablir
    install(READ_ONLY)
    call = {"undo": history.undo, "redo": history.redo, "go_to": lambda: history.go_to(0)}[operation]
    revision = history.revision
    with pytest.raises(ReadOnlyError):
        call()
    assert history.revision == revision, "l'historique n'a pas bougé"


def test_history_is_unchanged_outside_the_store(tmp_path):
    from core import entitlements

    entitlements.set_enforced_provider(None)
    entitlements.install_service(None)
    project = _project()
    history = ProjectHistory()
    history.reset(project)
    project.name = "Autre"
    history.record(project, "x")
    assert history.undo() is not None and history.redo() is not None


# -- 2. scripts : la session -----------------------------------------------------------------------------------------------------


def test_script_session_refuses_before_any_effect_in_read_only(install):
    install(READ_ONLY)
    project = _project()
    before = project_payload(project)
    session = Session(project)
    ran = []                                                                 # aucune opération n'est même appelée
    with pytest.raises(ReadOnlyError):
        session.apply([("add", lambda p: ran.append(1))], label="Ajout")
    with pytest.raises(ReadOnlyError):
        with session.transaction("T") as tx:
            tx.run(lambda p: ran.append(2))
    from core.scripting.session import Transaction
    with pytest.raises(ReadOnlyError):
        Transaction(session, "T", None).run(lambda p: ran.append(3))         # sans passer par ``__enter__``
    assert ran == [] and project_payload(project) == before and not session.history.can_undo
    assert session._open is False, "aucune transaction ne reste ouverte"


def test_apply_script_verbs_are_refused_in_read_only(install):
    script = parse_script({"kut_script": 1, "ops": [{"op": "add_track", "type": "video"}]})
    project = _project()
    before = project_payload(project)
    install(READ_ONLY)
    with pytest.raises(ReadOnlyError):
        apply_script(Session(project), script)
    assert project_payload(project) == before
    install(PAID)
    assert apply_script(Session(project), script).ok and project_payload(project) != before


def test_script_session_works_when_allowed_and_outside_the_store(install):
    install(PAID)
    project = _project()
    report = Session(project).apply([("add", lambda p: p.tracks.append(Track("X", "X", "video")))], label="Ajout")
    assert report.ok and any(track.id == "X" for track in project.tracks)


def test_script_undo_redo_refused_in_read_only(install):
    install(PAID)
    project = _project()
    session = Session(project)
    session.apply([("add", lambda p: p.tracks.append(Track("X", "X", "video")))], label="Ajout")
    install(READ_ONLY)
    with pytest.raises(ReadOnlyError):
        session.undo()


# -- 3. ligne de commande ---------------------------------------------------------------------------------------------------------


def _run(*argv, capsys):
    code = cli.run(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _script_file(tmp_path) -> Path:
    path = tmp_path / "script.json"
    path.write_text(json.dumps({"kut_script": 1, "ops": [{"op": "add_track", "type": "video"}]}), encoding="utf-8")
    return path


def test_cli_apply_is_refused_in_read_only_and_writes_nothing(install, kut, tmp_path, capsys, monkeypatch):
    install(READ_ONLY)
    before = kut.read_bytes()
    out_file = tmp_path / "out.kut"
    monkeypatch.setattr(cli, "_load", lambda *_a, **_k: pytest.fail("le projet est lu alors que l'écriture est refusée"))
    code, _out, err = _run("apply", str(_script_file(tmp_path)), str(kut), "-o", str(out_file), "--json", capsys=capsys)
    assert code == 1 and "lecture seule" in err
    assert not out_file.exists() and kut.read_bytes() == before
    code, out, _err = _run("apply", str(_script_file(tmp_path)), str(kut), "--dry-run", "--json", capsys=capsys)
    assert code == 1 and json.loads(out)["error"]["kind"] == "read_only", "même un essai à blanc (écriture de script) est refusé"


def test_cli_apply_works_when_allowed(install, kut, tmp_path, capsys):
    install(trial(T0))
    out_file = tmp_path / "out.kut"
    code, _out, _err = _run("apply", str(_script_file(tmp_path)), str(kut), "-o", str(out_file), capsys=capsys)
    assert code == 0 and out_file.is_file()


def test_cli_export_is_refused_in_read_only_before_anything(install, kut, tmp_path, capsys, monkeypatch):
    install(READ_ONLY)
    monkeypatch.setattr(cli, "_load", lambda *_a, **_k: pytest.fail("le projet est chargé alors que l'export est refusé"))
    output = tmp_path / "o.mp4"
    code, out, err = _run("export", str(kut), "-o", str(output), "--json", capsys=capsys)
    payload = json.loads(out)
    assert code == 1 and payload["error"]["kind"] == "export_blocked" and "bloqué" in err
    assert not output.exists() and sorted(p.name for p in tmp_path.iterdir()) == ["demo.kut"]


@needs_ffmpeg
def test_a_real_export_is_refused_then_allowed(install, kut, tmp_path, capsys, qapp):
    output = tmp_path / "out.mp4"
    install(READ_ONLY)
    code, _o, _e = _run("export", str(kut), "-o", str(output), capsys=capsys)
    assert code == 1 and not output.exists()
    install(PAID)                                                            # l'utilisateur achète : le même appel réussit
    code, out, _e = _run("export", str(kut), "-o", str(output), capsys=capsys)
    assert code == 0 and output.is_file() and "Export terminé" in out


def test_cli_reading_commands_still_work_in_read_only(install, kut, capsys):
    install(READ_ONLY)
    for command in (("info", str(kut)), ("validate", str(kut)), ("describe", str(kut)), ("presets",), ("schema",)):
        code, _out, _err = _run(*command, capsys=capsys)
        assert code == 0, command


def test_cli_messages_are_translated(install, kut, tmp_path, capsys, monkeypatch):
    install(READ_ONLY)
    for lang, word in (("en", "read-only"), ("es", "solo lectura")):
        monkeypatch.setenv("KUT_STUDIO_LANG", lang)
        i18n.reset_for_tests()
        _code, _out, err = _run("apply", str(_script_file(tmp_path)), str(kut), "--dry-run", capsys=capsys)
        assert word in err


# -- 4. export : exécuteur, moteur, file de rendu, livrables, film ---------------------------------------------------------------


def _request():
    from core.export_engine import ExportFormat, ExportPreset, ExportRequest
    from core.render_plan import RenderPlan

    plan = RenderPlan(width=64, height=36, fps=30.0, duration=1.0, video_layers=[], audio_layers=[], missing_media=[])
    return ExportRequest(render_plan=plan, output_path="/nonexistent/out.mp4", format=ExportFormat.MP4_H264,
                         preset=ExportPreset("p", (64, 36), 28, "96k"), fps=30.0)


def test_run_export_raises_in_read_only_and_touches_no_file(install, tmp_path):
    from core.export_runner import run_export

    install(READ_ONLY)
    request = _request()
    from dataclasses import replace
    request = replace(request, output_path=str(tmp_path / "o.mp4"))
    with pytest.raises(ExportBlockedError):
        run_export(request)
    assert list(tmp_path.iterdir()) == []


def test_export_engine_start_emits_failed_and_starts_nothing(install, qapp):
    from core.export_engine import ExportEngine

    install(READ_ONLY)
    engine = ExportEngine()
    messages = []
    engine.failed.connect(messages.append)
    engine.start(_request())
    assert len(messages) == 1 and "bloqué" in messages[0] and engine.last_error_kind == "entitlement"
    assert engine.is_running is False


def test_render_queue_refuses_enqueue_and_every_start(install, qapp, tmp_path):
    from core.export_engine import ExportEngine
    from core.render_presets import default_preset
    from core.render_queue import RenderQueue
    from core.render_queue_store import RenderQueueStore

    install(PAID)
    queue = RenderQueue(ExportEngine(), RenderQueueStore(tmp_path / "queue"))
    job = queue.enqueue(_project(), default_preset(), str(tmp_path / "a.mp4"))
    install(READ_ONLY)
    refused = []
    queue.export_refused.connect(refused.append)
    assert queue.export_allowed is False
    assert queue.start_all() is False and queue.start_job(job.id) is False
    assert len(refused) == 2 and not queue.is_running
    with pytest.raises(ExportBlockedError):
        queue.enqueue(_project(), default_preset(), str(tmp_path / "b.mp4"))
    assert len(queue.jobs) == 1


def test_render_queue_stops_starting_jobs_when_the_state_changes_mid_run(install, qapp, tmp_path):
    from core.export_engine import ExportEngine
    from core.render_job import JobStatus
    from core.render_presets import default_preset
    from core.render_queue import RenderQueue
    from core.render_queue_store import RenderQueueStore

    install(PAID)
    queue = RenderQueue(ExportEngine(), RenderQueueStore(tmp_path / "queue"))
    job = queue.enqueue(_project(), default_preset(), str(tmp_path / "a.mp4"))
    queue._mode = "all"                                                      # une exécution est en cours quand l'essai finit
    install(READ_ONLY)
    queue._start_next()
    assert job.status is JobStatus.WAITING and queue.current_job is None and not queue.is_running


def test_render_queue_is_unchanged_outside_the_store(tmp_path, qapp):
    from core import entitlements
    from core.export_engine import ExportEngine
    from core.render_presets import default_preset
    from core.render_queue import RenderQueue
    from core.render_queue_store import RenderQueueStore

    entitlements.set_enforced_provider(None)
    entitlements.install_service(None)
    queue = RenderQueue(ExportEngine(), RenderQueueStore(tmp_path / "queue"))
    assert queue.export_allowed is True
    queue.enqueue(_project(), default_preset(), str(tmp_path / "a.mp4"))


def test_deliverables_are_refused_in_read_only(install, tmp_path):
    from core.social_deliverables import Deliverables, make_cover, make_deliverables, make_preview_copy

    install(READ_ONLY)
    source = str(tmp_path / "v.mp4")
    for call in (
        lambda: make_cover(source, 0.5, 2.0, 30.0),
        lambda: make_preview_copy(source, 64, 36, 2.0),
        lambda: make_deliverables(source, Deliverables(cover=True), width=64, height=36, duration=2.0, fps=30.0),
    ):
        with pytest.raises(ExportBlockedError):
            call()
    assert list(tmp_path.iterdir()) == []
    with pytest.raises(ExportBlockedError):                                  # même quand rien n'est demandé : la garde est en tête
        make_deliverables(source, Deliverables(), width=64, height=36, duration=2.0, fps=30.0)


def test_film_write_paths_are_refused_in_read_only(install, tmp_path):
    from core.film import Film

    install(READ_ONLY)
    film = Film(tmp_path / "f.kut", size=(64, 36), fps=24.0)
    with pytest.raises(ReadOnlyError):
        film.save()
    with pytest.raises(ReadOnlyError):
        film.render_shot(lambda t: None, duration=1.0, name="plan")
    with pytest.raises(ExportBlockedError):
        film.export(tmp_path / "f.mp4")
    assert list(tmp_path.iterdir()) == []


def test_every_refusal_is_one_typed_exception_with_a_translated_message(install):
    install(READ_ONLY)
    refusal = ExportBlockedError(install_state := __import__("core.entitlements", fromlist=["x"]).installed_service().state())
    assert isinstance(refusal, EntitlementRefused) and not isinstance(refusal, ValueError)
    assert refusal.message_key == "entitlements.export.expired" and install_state.kind.value == "expired"
    texts = set()
    for lang in ("fr", "en", "es"):
        i18n.set_language(lang)
        texts.add(refusal.user_message())
    assert len(texts) == 3 and not any(text.startswith("[") for text in texts)


def test_enforced_without_a_service_fails_closed(store_flavor):
    from core.entitlements import edit_refusal, export_refusal

    assert edit_refusal() is not None and export_refusal() is not None
    assert edit_refusal().state.reason == "no_service"
