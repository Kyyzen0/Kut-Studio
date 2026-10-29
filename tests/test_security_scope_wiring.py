"""Les chemins qui lisent / écrivent hors conteneur passent par le gestionnaire d'accès (ADR-0013, étape 5) ; hors Store, rien ne change.

Le pont est un faux (``tests/scope_fakes.py``) : on observe ``open_count()`` PENDANT la lecture, l'écriture, le sous-processus.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from core import build_flavor, security_scope
from core.process_supervisor import ProcessSupervisor
from core.project_factory import create_default_project
from core.project_io import load_project, save_project, write_project_payload, project_payload
from core.security_scope.manager import AccessReport
from core.security_scope.paths import command_paths, plan_paths
from core.security_scope.store import canonical
from tests.scope_fakes import make_manager


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Mode Store, gestionnaire installé sur un faux pont ; rend ``(manager, bridge, dossier utilisateur)``."""
    monkeypatch.setattr(build_flavor, "STORE", True)
    manager, bridge, _store = make_manager(tmp_path)
    security_scope.install_manager(manager)
    user = tmp_path / "user"
    user.mkdir()
    yield manager, bridge, user
    security_scope.install_manager(None)


def _watch_open_count(monkeypatch, bridge, owner, name):
    """Remplace ``owner.name`` par une enveloppe qui note ``bridge.open_count()`` à chaque appel."""
    seen: list[int] = []
    original = getattr(owner, name)

    def wrapper(*args, **kwargs):
        seen.append(bridge.open_count())
        return original(*args, **kwargs)

    monkeypatch.setattr(owner, name, wrapper)
    return seen


# -- projet : ouverture et enregistrement ------------------------------------------------------------------------------------


def test_project_save_and_load_run_inside_the_access(store, monkeypatch):
    manager, bridge, user = store
    target = user / "film.kut"
    target.write_text("{}", encoding="utf-8")
    assert manager.remember(str(target))
    project = create_default_project()
    writes = _watch_open_count(monkeypatch, bridge, sys.modules["core.project_io"], "_atomic_write_json")
    save_project(project, str(target))
    assert writes == [1] and bridge.open_count() == 0
    reads = _watch_open_count(monkeypatch, bridge, Path, "read_text")
    loaded = load_project(str(target))
    assert loaded.name == project.name and 1 in reads and bridge.open_count() == 0


def test_project_load_closes_the_access_when_the_file_is_corrupt(store):
    manager, bridge, user = store
    target = user / "casse.kut"
    target.write_text("{pas du json", encoding="utf-8")
    manager.remember(str(target))
    with pytest.raises(ValueError):
        load_project(str(target))
    assert bridge.open_count() == 0 and manager.open_count == 0


def test_project_without_bookmark_still_opens_when_the_system_grants_access(store):
    """Le gestionnaire ne bloque jamais : sans signet la lecture se fait (le système accorde l'accès de la session ou refuse)."""
    _manager, bridge, user = store
    target = user / "sans_signet.kut"
    save_project(create_default_project(), str(target))
    assert load_project(str(target)).tracks and bridge.open_count() == 0 and bridge.opened_log == []


def test_project_referencing_media_without_bookmarks_opens_and_media_are_offline(store, tmp_path):
    from ui.sandbox_access import offline_media_paths

    manager, bridge, user = store
    project = create_default_project()
    assert project.media_assets, "le projet de démonstration doit référencer des médias"
    for index, asset in enumerate(project.media_assets):
        asset.path = str(user / f"m{index}.mov")                                     # chemins hors conteneur, sans signet
    paths = [asset.path for asset in project.media_assets]
    target = user / "p.kut"
    save_project(project, str(target))
    manager.remember(str(target))
    loaded = load_project(str(target))                                               # ne plante pas, ne lit aucun média
    offline = offline_media_paths(loaded)
    assert set(offline) == {canonical(path) for path in paths} and bridge.open_count() == 0
    real = user / "m0.mov"                                                           # l'utilisateur relie : fichier + signet
    real.write_bytes(b"x")
    manager.remember(str(real))
    assert canonical(str(real)) not in offline_media_paths(loaded)


def test_outside_the_store_offline_report_is_empty(monkeypatch):
    from ui.sandbox_access import offline_media_paths

    monkeypatch.setattr(build_flavor, "STORE", False)
    assert offline_media_paths(create_default_project()) == []


def test_in_place_write_fallback_only_in_the_store(store, monkeypatch):
    _manager, _bridge, user = store
    target = user / "voisin_refuse.kut"
    target.write_text("ancien", encoding="utf-8")

    def denied(*args, **kwargs):
        raise PermissionError("Operation not permitted")

    monkeypatch.setattr("core.project_io.tempfile.mkstemp", denied)
    write_project_payload(project_payload(create_default_project()), str(target))     # Store : écrit en place
    assert load_project(str(target)).tracks
    monkeypatch.setattr(build_flavor, "STORE", False)
    target.write_text("ancien", encoding="utf-8")
    with pytest.raises(PermissionError):                                              # hors Store : comportement d'avant
        write_project_payload(project_payload(create_default_project()), str(target))
    assert target.read_text(encoding="utf-8") == "ancien"


def test_in_place_write_never_truncates_on_a_non_finite_value(store, monkeypatch):
    _manager, _bridge, user = store
    target = user / "nan.kut"
    target.write_text("intact", encoding="utf-8")
    monkeypatch.setattr("core.project_io.tempfile.mkstemp", lambda *a, **k: (_ for _ in ()).throw(PermissionError()))
    with pytest.raises(ValueError, match="non finie"):
        write_project_payload({"x": float("nan")}, str(target))
    assert target.read_text(encoding="utf-8") == "intact"


POSIX_ONLY = pytest.mark.skipif(sys.platform.startswith("win"), reason="/bin/sh : sous-processus de test POSIX")

# -- sous-processus ----------------------------------------------------------------------------------------------------------


@POSIX_ONLY
def test_every_supervised_child_keeps_its_files_open_until_finish(store, tmp_path):
    manager, bridge, user = store
    media = user / "clip.mov"
    media.write_bytes(b"x")
    manager.remember(str(media))
    supervisor = ProcessSupervisor(tmp_path / "reg", use_reaper=False, use_job=False)
    process = supervisor.popen(["/bin/sh", "-c", "sleep 5", "kut", str(media)])
    try:
        assert bridge.open_count() == 1                                              # ouvert pendant toute la vie de l'enfant
    finally:
        supervisor.finish(process)
    assert bridge.open_count() == 0
    supervisor.finish(process)                                                       # idempotent : pas de double fermeture
    assert len(bridge.closed_log) == 1


@POSIX_ONLY
def test_supervised_run_opens_around_the_command_and_closes_after(store, tmp_path):
    manager, bridge, user = store
    media = user / "clip.mov"
    media.write_bytes(b"x")
    manager.remember(str(media))
    supervisor = ProcessSupervisor(tmp_path / "reg", use_reaper=False, use_job=False)
    done = supervisor.run(["/bin/sh", "-c", "exit 0", "kut", str(media)])
    assert done.returncode == 0 and bridge.opened_log == [canonical(str(media))] and bridge.open_count() == 0


@POSIX_ONLY
def test_a_launch_failure_closes_the_access(store, tmp_path):
    manager, bridge, user = store
    media = user / "clip.mov"
    media.write_bytes(b"x")
    manager.remember(str(media))
    supervisor = ProcessSupervisor(tmp_path / "reg", use_reaper=False, use_job=False)
    with pytest.raises(OSError):
        supervisor.popen([str(tmp_path / "n'existe pas"), str(media)])
    assert bridge.open_count() == 0


@POSIX_ONLY
def test_the_supervisor_does_not_touch_the_scope_outside_the_store(tmp_path, monkeypatch):
    monkeypatch.setattr(build_flavor, "STORE", False)
    manager, bridge, _store = make_manager(tmp_path)
    security_scope.install_manager(manager)
    try:
        supervisor = ProcessSupervisor(tmp_path / "reg", use_reaper=False, use_job=False)
        assert supervisor.run(["/bin/sh", "-c", "exit 0", "kut", str(tmp_path)]).returncode == 0
        assert bridge.opened_log == []
    finally:
        security_scope.install_manager(None)


def test_command_paths_finds_arguments_and_quoted_filter_paths_only():
    found = command_paths(["ffmpeg", "-i", "/u/a b.mov", "-vf", "subtitles=filename='/u/s.srt':force_style='x'", "rel/path.mov",
                           Path("/u/p.wav"), 12, "-y"])
    assert found == ["/u/a b.mov", "/u/s.srt", "/u/p.wav"]
    assert command_paths(["/" + "a" * 5000]) == [] and command_paths(["x"] * 2000 + ["/u/tard"]) == []     # bornes


def test_plan_paths_walks_nested_plans_and_is_bounded():
    @dataclass(frozen=True)
    class Layer:
        source_path: str
        name: str = "n"

    @dataclass(frozen=True)
    class Plan:
        video_layers: tuple
        nested: dict

    plan = Plan((Layer("/u/a.mov"), Layer(""), Layer("relatif.mov")), {"s": Plan((Layer("/u/b.mov"),), {})})
    assert plan_paths(plan) == ["/u/a.mov", "/u/b.mov"]
    deep: object = Layer("/u/profond.mov")
    for _ in range(40):
        deep = (deep,)
    assert plan_paths(deep) == []                                                     # profondeur bornée


# -- lecture simple : LUT, sous-titres, sondes, poignée média ----------------------------------------------------------------


def test_subtitles_and_luts_are_read_and_written_inside_the_access(store, monkeypatch):
    from core import lut_importer, subtitle_io

    manager, bridge, user = store
    srt = user / "s.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nbonjour\n", encoding="utf-8")
    manager.remember(str(srt))
    seen = _watch_open_count(monkeypatch, bridge, Path, "read_bytes")
    assert subtitle_io.load_srt(str(srt))[0].text == "bonjour" and seen == [1] and bridge.open_count() == 0
    wrote = _watch_open_count(monkeypatch, bridge, subtitle_io, "atomic_write_text")
    subtitle_io.save_srt(subtitle_io.load_srt(str(srt)), str(srt))
    assert wrote == [1] and bridge.open_count() == 0
    with pytest.raises(lut_importer.LUTImportMissingFile):
        lut_importer.parse_cube_lut(str(user / "absent.cube"))
    assert bridge.open_count() == 0                                                   # fermé même sur l'exception


def test_media_handle_holds_the_access_until_closed(store, monkeypatch):
    from core import media_io

    manager, bridge, user = store
    media = user / "clip.mov"
    media.write_bytes(b"x")
    manager.remember(str(media))
    monkeypatch.setattr(media_io, "probe", lambda path: media_io.MediaInfo(str(path), 1.0, 2, 2, 25.0))
    monkeypatch.setattr(media_io, "_av", None)
    handle = media_io.MediaHandle(str(media))
    assert bridge.open_count() == 1                                                   # PyAV lirait image par image
    handle.close()
    handle.close()
    assert bridge.open_count() == 0 and len(bridge.closed_log) == 1
    monkeypatch.setattr(media_io, "probe", lambda path: (_ for _ in ()).throw(OSError("ffprobe")))
    with pytest.raises(OSError):
        media_io.MediaHandle(str(media))
    assert bridge.open_count() == 0                                                   # une sonde qui échoue ne laisse rien ouvert


def test_probe_media_runs_inside_the_access(store, monkeypatch):
    from core import media_probe

    manager, bridge, user = store
    media = user / "clip.mov"
    media.write_bytes(b"x")
    manager.remember(str(media))
    seen = _watch_open_count(monkeypatch, bridge, media_probe.os.path, "isfile")
    monkeypatch.setattr(media_probe, "find_media_tool", lambda name: None)
    with pytest.raises(media_probe.MediaProbeError):
        media_probe.probe_media(str(media))
    assert 1 in seen and bridge.open_count() == 0


# -- export ------------------------------------------------------------------------------------------------------------------


def _plan(media: Path):
    from core.render_plan import RenderPlan

    return RenderPlan(width=320, height=180, fps=25.0, duration=1.0, video_layers=(_layer(media),))


def _layer(media: Path):
    """Une couche minimale : seul ``source_path`` compte ici (le plan est parcouru, jamais rendu)."""
    from core.render_plan import RenderLayer

    import dataclasses

    kwargs = {}
    for item in dataclasses.fields(RenderLayer):
        if item.default is not dataclasses.MISSING or item.default_factory is not dataclasses.MISSING:
            continue
        kwargs[item.name] = str(media) if item.name == "source_path" else _zero(item.type)
    return RenderLayer(**kwargs)


def _zero(annotation):
    text = str(annotation)
    if "float" in text:
        return 0.0
    if "int" in text:
        return 0
    if "bool" in text:
        return False
    if "str" in text:
        return ""
    return None


def test_run_export_and_export_engine_hold_media_and_output_open(store, monkeypatch, tmp_path):
    from core import export_runner

    manager, bridge, user = store
    media = user / "clip.mov"
    media.write_bytes(b"x")
    manager.remember(str(media))
    plan = _plan(media)
    assert plan_paths(plan) == [str(media)]

    class Request:                                                                   # run_export ne lit ici que plan et sortie
        render_plan = plan
        output_path = str(user / "out.mp4")

    seen: list[int] = []

    def body(request, **options):
        seen.append(bridge.open_count())
        raise RuntimeError("après le corps")

    wrapped = export_runner._with_user_files(body)
    with pytest.raises(RuntimeError):
        wrapped(Request())
    assert seen == [1] and bridge.open_count() == 0                                   # ouvert pendant, fermé malgré l'exception


def test_export_engine_scope_lives_until_cleanup(store):
    from PySide6.QtCore import QCoreApplication

    from core.export_engine import ExportEngine

    manager, bridge, user = store
    media = user / "clip.mov"
    media.write_bytes(b"x")
    manager.remember(str(media))
    _app = QCoreApplication.instance() or QCoreApplication([])

    class Request:
        render_plan = _plan(media)
        output_path = str(user / "o.mp4")

    engine = ExportEngine()
    engine._open_scope(Request())                                                    # type: ignore[arg-type]
    assert bridge.open_count() == 1
    engine._open_scope(Request())                                                    # un nouvel export referme l'ancien : pas de fuite
    assert bridge.open_count() == 1 and len(bridge.closed_log) == 1
    engine._cleanup_temporary_files()                                                # point de sortie commun (fin, échec, annulation)
    assert bridge.open_count() == 0
    engine.shutdown(100)
    assert bridge.open_count() == 0


# -- dialogues : le choix de l'utilisateur est mémorisé --------------------------------------------------------------------------


def test_remember_user_path_stores_the_chosen_file(store):
    manager, bridge, user = store
    chosen = user / "choisi.mov"
    chosen.write_bytes(b"x")
    assert security_scope.remember_user_path(str(chosen)) and bridge.created == [canonical(str(chosen))]
    assert security_scope.remember_user_path("") is False and security_scope.remember_user_path(None) is False


def test_the_report_type_is_exported():
    assert AccessReport().needs_relink == () and subprocess  # noqa: B015 - garde les imports utilisés


# -- moniteur et messages ------------------------------------------------------------------------------------------------------


def test_source_scope_follows_the_current_source(store):
    from ui.source_scope import SourceScope

    manager, bridge, user = store
    first, second = user / "a.mov", user / "b.mov"
    for path in (first, second):
        path.write_bytes(b"x")
        manager.remember(str(path))
    scope = SourceScope()
    scope.hold(str(first))
    assert scope.active and bridge.open_count() == 1
    scope.hold(str(second))                                                   # changer de source referme l'ancienne
    assert bridge.open_count() == 1 and bridge.opened_log == [canonical(str(first)), canonical(str(second))]
    scope.hold(None)
    assert not scope.active and bridge.open_count() == 0
    scope.hold(str(first))
    scope.release()
    scope.release()
    assert bridge.open_count() == 0


def test_status_messages_carry_counts_never_paths(store):
    from core.security_scope import LaunchReport
    from ui import i18n
    from ui.sandbox_access import announce_launch_report, report_offline_media

    _manager, _bridge, user = store

    class Bar:
        def __init__(self):
            self.messages = []

        def showMessage(self, text, timeout=0):
            self.messages.append(text)

    class Window:
        def __init__(self, project):
            self.project, self.bar = project, Bar()

        def statusBar(self):
            return self.bar

    project = create_default_project()
    for index, asset in enumerate(project.media_assets):
        asset.path = str(user / f"secret-{index}.mov")
    window = Window(project)
    assert report_offline_media(window) == len(project.media_assets) > 0
    assert "secret" not in window.bar.messages[-1] and str(len(project.media_assets)) in window.bar.messages[-1]
    assert announce_launch_report(window, LaunchReport(lost=("/a",), stale=("/a", "/b"))) == 2
    assert announce_launch_report(window, LaunchReport()) == 0
    assert "{count}" not in i18n.translate("sandbox.access.offline", count=3)
