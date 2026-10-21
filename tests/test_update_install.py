"""Installation automatique sous macOS : décisions, validation du bundle, staging, assistant et reprise.

Sécurité du poste : AUCUNE installation réelle. L'assistant (``/bin/sh``) tourne sur de vrais dossiers temporaires avec un
faux ``Kut-Studio.app`` (``Contents/Info.plist`` + exécutable factice) et une commande ``open`` remplacée par un stub ;
jamais le vrai ``open``, jamais ``pkill`` ni signal de plantage. Les outils système (``codesign``, ``lipo``, ``spctl``)
sont simulés par un exécuteur injecté ; ``ditto`` n'est lancé pour de vrai que sur macOS, dans un dossier temporaire.
"""

from __future__ import annotations

import hashlib
import json
import os
import plistlib
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path, PurePosixPath

import pytest

from core import update_helper, update_install
from core.update_helper import HelperPlan, helper_environment, make_plan, write_helper_script
from core.update_install import (
    CommandResult,
    FixedPolicy,
    InstallRefusal,
    SignaturePolicy,
    StageError,
    StageProblem,
    evaluate_install,
    read_outcome,
    recover_leftovers,
    stage_update,
    validate_bundle,
    write_started_marker,
)
from core.updates import InstallContext, InstallKind
from core.versioning import Version

needs_sh = pytest.mark.skipif(sys.platform == "win32", reason="l'assistant est un script /bin/sh (macOS ; Linux pour la CI)")
IDENTIFIER = "com.kutstudio.app"
OLD, NEW = "1.0.0", "2.0.0"
TEAM = "ABCDE12345"

GOOD_EXE = '#!/bin/sh\nprintf \'{"version":"%s","pid":%%s,"time":1}\' "$STUB_ALIVE_PID" > "$KUT_UPD_STARTED"\n'
SILENT_EXE = "#!/bin/sh\nexit 0\n"
STUB_OPEN = (
    "#!/bin/sh\n"
    'printf \'%s\\n\' "$1" >> "$STUB_OPEN_LOG"\n'
    'if [ -x "$1/Contents/MacOS/Kut-Studio" ]; then "$1/Contents/MacOS/Kut-Studio" >/dev/null 2>&1 & fi\n'
    "exit 0\n"
)


# ---------------------------------------------------------------------------
# Outils
# ---------------------------------------------------------------------------


def make_app(root: Path, *, version: str, executable: str, identifier: str = IDENTIFIER, name: str = "Kut-Studio.app") -> Path:
    app = root / name
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps({
        "CFBundleIdentifier": identifier, "CFBundleShortVersionString": version, "CFBundleExecutable": "Kut-Studio",
    }))
    exe = app / "Contents" / "MacOS" / "Kut-Studio"
    exe.write_text(executable, encoding="utf-8")
    exe.chmod(0o755)
    (app / "Contents" / "VERSION").write_text(version, encoding="utf-8")
    return app


def snapshot(root: Path) -> dict[str, object]:
    """Arbre complet : chemin → (type, taille, empreinte) ; deux instantanés égaux = rien n'a bougé."""
    state: dict[str, object] = {}
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root))
        if path.is_symlink():
            state[rel] = ("link", os.readlink(path))
        elif path.is_dir():
            state[rel] = ("dir",)
        else:
            state[rel] = ("file", path.stat().st_size, hashlib.sha1(path.read_bytes()).hexdigest(), path.stat().st_mode)
    return state


def dead_pid() -> int:
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait()
    return process.pid


class Scenario:
    """Un dossier d'applications temporaire (chemin avec espaces, accents et caractères de shell), l'ancienne application,
    la nouvelle déjà « extraite » dans son staging, un dossier ``outside`` qui ne doit jamais changer."""

    def __init__(self, tmp_path: Path, *, folder: str = "Mes Applications", new_executable: str | None = None,
                 pid: int | None = None) -> None:
        self.root = tmp_path / "monde"
        self.apps = self.root / folder
        self.apps.mkdir(parents=True)
        self.outside = self.root / "outside"
        self.outside.mkdir()
        (self.outside / "témoin.txt").write_text("intact", encoding="utf-8")
        self.updates = self.root / "cache" / "updates"
        self.updates.mkdir(parents=True)
        self.app = make_app(self.apps, version=OLD, executable=GOOD_EXE % OLD)
        self.pid = pid if pid is not None else dead_pid()
        self.plan: HelperPlan = make_plan(self.app, pid=self.pid, version=NEW, from_version=OLD, directory=self.updates)
        staged_root = self.apps / "tmp-stage"
        staged_root.mkdir()
        staged = make_app(staged_root, version=NEW, executable=new_executable if new_executable is not None else GOOD_EXE % NEW)
        staged.rename(self.plan.staging)
        staged_root.rmdir()
        self.stub = self.root / "stub-open.sh"
        self.stub.write_text(STUB_OPEN, encoding="utf-8")
        self.stub.chmod(0o755)
        self.open_log = self.root / "open.log"

    @property
    def installed_version(self) -> str:
        return (self.app / "Contents" / "VERSION").read_text(encoding="utf-8")

    def opened(self) -> list[str]:
        return self.open_log.read_text(encoding="utf-8").splitlines() if self.open_log.exists() else []

    def status(self):
        return read_outcome(self.plan.status)

    def run(self, **extra: str) -> subprocess.CompletedProcess[bytes]:
        environment = {
            "STUB_OPEN_LOG": str(self.open_log),
            "STUB_ALIVE_PID": str(os.getpid()),
            "KUT_UPD_START_SECONDS": "20",
            "KUT_UPD_GRACE_SECONDS": "0",
            **extra,
        }
        plan = HelperPlan(**{**self.plan.__dict__, "open_command": str(self.stub)})
        env = helper_environment(plan, environment)
        env["KUT_UPD_START_SECONDS"] = environment["KUT_UPD_START_SECONDS"]
        script = write_helper_script(self.updates, pid=self.pid)
        return subprocess.run(["/bin/sh", str(script)], env=env, capture_output=True, timeout=120, check=False)

    def log(self) -> str:
        return self.plan.log.read_text(encoding="utf-8") if self.plan.log.exists() else ""


@pytest.fixture
def scenario(tmp_path):
    return Scenario(tmp_path)


def assert_untouched_outside(scenario: Scenario, before: dict[str, object]) -> None:
    assert snapshot(scenario.outside) == before, "rien n'a été touché hors des dossiers de l'installation"


# ---------------------------------------------------------------------------
# Assistant : succès, retour arrière, interruption
# ---------------------------------------------------------------------------


@needs_sh
@pytest.mark.parametrize("folder", ["Applications", "Mes Applications é ü", "A 'b' \"c\" $HOME `x` &"])
def test_helper_replaces_the_application_and_removes_the_backup(tmp_path, folder):
    scenario = Scenario(tmp_path, folder=folder)
    before = snapshot(scenario.outside)
    done = scenario.run()
    assert done.returncode == 0, scenario.log()
    assert scenario.installed_version == NEW
    assert not scenario.plan.previous.exists() and not scenario.plan.staging.exists()
    assert sorted(p.name for p in scenario.apps.iterdir()) == ["Kut-Studio.app"], "plus aucun reste à côté"
    assert scenario.opened() == [str(scenario.app)]
    outcome = scenario.status()
    assert outcome is not None and (outcome.result, outcome.version, outcome.previous) == ("ok", NEW, OLD)
    assert not scenario.plan.lock.exists(), "le verrou est levé à la fin"
    assert "succès" in scenario.log() and "ancienne application renommée" in scenario.log()
    assert_untouched_outside(scenario, before)


@needs_sh
def test_a_new_application_that_never_starts_is_rolled_back(tmp_path):
    scenario = Scenario(tmp_path, new_executable=SILENT_EXE)
    before = snapshot(scenario.outside)
    done = scenario.run(KUT_UPD_START_SECONDS="1")
    assert done.returncode == 1, scenario.log()
    assert scenario.installed_version == OLD, "l'ancienne application est de retour"
    assert sorted(p.name for p in scenario.apps.iterdir()) == ["Kut-Studio.app"]
    outcome = scenario.status()
    assert outcome is not None and (outcome.result, outcome.reason) == ("rolled_back", "start_timeout")
    assert scenario.opened() == [str(scenario.app), str(scenario.app)], "nouvelle lancée, puis l'ancienne relancée"
    assert "retour arrière" in scenario.log()
    assert_untouched_outside(scenario, before)


@needs_sh
def test_a_new_application_that_dies_after_starting_is_rolled_back(tmp_path):
    scenario = Scenario(tmp_path, new_executable=GOOD_EXE % NEW)
    done = scenario.run(STUB_ALIVE_PID=str(dead_pid()))
    assert done.returncode == 1, scenario.log()
    assert scenario.installed_version == OLD
    outcome = scenario.status()
    assert outcome is not None and outcome.reason == "crashed_after_start"
    assert not scenario.plan.previous.exists() and not scenario.plan.staging.exists()


@needs_sh
def test_an_unreadable_started_file_is_rolled_back(tmp_path):
    marker = '#!/bin/sh\nprintf \'{"version":"2.0.0"}\' > "$KUT_UPD_STARTED"\n'
    scenario = Scenario(tmp_path, new_executable=marker)
    done = scenario.run()
    assert done.returncode == 1 and scenario.installed_version == OLD
    outcome = scenario.status()
    assert outcome is not None and outcome.reason == "started_marker_invalid"


@needs_sh
def test_a_stale_started_file_of_the_same_version_does_not_count(tmp_path):
    scenario = Scenario(tmp_path, new_executable=SILENT_EXE)
    write_started_marker(scenario.plan.started, NEW, os.getpid())     # reste d'un essai précédent
    done = scenario.run(KUT_UPD_START_SECONDS="1")
    assert done.returncode == 1 and scenario.installed_version == OLD


@needs_sh
def test_an_invalid_staging_changes_nothing(tmp_path):
    scenario = Scenario(tmp_path)
    (scenario.plan.staging / "Contents" / "Info.plist").unlink()
    before = snapshot(scenario.app)
    done = scenario.run()
    assert done.returncode == 4
    assert snapshot(scenario.app) == before, "l'ancienne application n'a pas bougé"
    assert not scenario.plan.previous.exists()
    outcome = scenario.status()
    assert outcome is not None and (outcome.result, outcome.reason) == ("failed", "staging_invalid")
    assert scenario.opened() == [str(scenario.app)], "l'ancienne est relancée : l'utilisateur l'avait fermée"


@needs_sh
def test_the_helper_never_deletes_a_folder_that_is_not_a_staging(tmp_path):
    scenario = Scenario(tmp_path)
    precious = scenario.apps / "precieux"
    scenario.plan.staging.rename(precious)
    (precious / "Contents" / "Info.plist").unlink()
    plan = HelperPlan(**{**scenario.plan.__dict__, "staging": precious})
    scenario.plan = plan
    scenario.run()
    assert precious.is_dir() and (precious / "Contents").is_dir(), "safe_rm refuse un nom qui n'est pas un staging"
    assert "suppression refusée" in scenario.log()


@needs_sh
def test_the_helper_gives_up_without_touching_anything_when_the_app_does_not_quit(tmp_path):
    waiter = subprocess.Popen(["/bin/sleep", "60"])
    try:
        scenario = Scenario(tmp_path, pid=waiter.pid)
        before_app = snapshot(scenario.app)
        before_outside = snapshot(scenario.outside)
        done = scenario.run(KUT_UPD_WAIT_SECONDS="1")
        assert done.returncode == 3, scenario.log()
        assert snapshot(scenario.app) == before_app and not scenario.plan.previous.exists()
        outcome = scenario.status()
        assert outcome is not None and outcome.reason == "wait_timeout"
        assert scenario.opened() == [], "l'application tourne encore : rien n'est relancé"
        assert_untouched_outside(scenario, before_outside)
    finally:
        waiter.terminate()
        waiter.wait()


@needs_sh
def test_the_helper_waits_for_the_application_to_quit_before_swapping(tmp_path):
    import threading

    waiter = subprocess.Popen(["/bin/sleep", "2"])
    scenario = Scenario(tmp_path, pid=waiter.pid)
    results: list[subprocess.CompletedProcess[bytes]] = []
    thread = threading.Thread(target=lambda: results.append(scenario.run(KUT_UPD_WAIT_SECONDS="30")))
    thread.start()
    time.sleep(0.5)
    assert scenario.installed_version == OLD and scenario.plan.staging.is_dir(), "rien n'est remplacé tant qu'elle tourne"
    waiter.wait()            # le parent réel de l'application (le système) la libère ; ici c'est le test
    thread.join()
    assert results[0].returncode == 0, scenario.log()
    assert scenario.installed_version == NEW


@needs_sh
@pytest.mark.parametrize(("variable", "value"), [
    ("KUT_UPD_APP", ""), ("KUT_UPD_PID", ""), ("KUT_UPD_STAGING", ""), ("KUT_UPD_APP", "relatif.app"),
    ("KUT_UPD_APP", "/chemin/sans-suffixe"), ("KUT_UPD_PID", "12x"),
])
def test_the_helper_refuses_missing_or_malformed_inputs(tmp_path, variable, value):
    scenario = Scenario(tmp_path)
    script = write_helper_script(scenario.updates, pid=scenario.pid)
    before = snapshot(scenario.root)
    env = helper_environment(HelperPlan(**{**scenario.plan.__dict__, "open_command": str(scenario.stub)}))
    env[variable] = value
    done = subprocess.run(["/bin/sh", str(script)], env=env, capture_output=True, timeout=60, check=False)
    assert done.returncode == 2
    assert snapshot(scenario.root) == before, "aucune modification"


@needs_sh
@pytest.mark.parametrize("step", ["renamed_previous", "installed", "launched"])
def test_a_helper_killed_at_any_step_leaves_a_recoverable_state_and_can_be_rerun(tmp_path, step):
    scenario = Scenario(tmp_path)
    before = snapshot(scenario.outside)
    killed = scenario.run(KUT_UPD_HALT_AFTER=step)
    assert killed.returncode == -9, "SIGKILL simulé par le crochet de test (le script se tue lui-même)"
    assert scenario.plan.lock.exists(), "un arrêt brutal ne nettoie rien"
    if step == "renamed_previous":
        assert scenario.plan.previous.is_dir() and not scenario.app.exists() and scenario.plan.staging.is_dir()
    else:
        assert scenario.plan.previous.is_dir() and scenario.app.is_dir() and not scenario.plan.staging.exists()
    finished = scenario.run()
    assert finished.returncode == 0, scenario.log()
    assert scenario.installed_version == NEW
    assert sorted(p.name for p in scenario.apps.iterdir()) == ["Kut-Studio.app"]
    assert_untouched_outside(scenario, before)


@needs_sh
def test_a_half_swapped_state_is_restored_at_the_next_start(tmp_path):
    scenario = Scenario(tmp_path)
    scenario.run(KUT_UPD_HALT_AFTER="renamed_previous")
    # Ni l'assistant ni l'application ne tournent : une copie qui démarre depuis ailleurs voit l'état et le répare.
    report = recover_leftovers(scenario.app, updates_dir=scenario.updates, lock=scenario.plan.lock,
                               status=scenario.plan.status, running_version=OLD)
    assert report.restored == scenario.plan.previous
    assert scenario.installed_version == OLD
    assert not scenario.plan.previous.exists()
    assert not scenario.plan.staging.exists(), "le staging orphelin est supprimé"


@needs_sh
def test_rolling_back_stops_what_runs_from_the_new_bundle(tmp_path):
    compiler = shutil.which("cc")
    if compiler is None:
        pytest.skip("pas de compilateur C pour fabriquer un exécutable qui dort")
    scenario = Scenario(tmp_path, new_executable=SILENT_EXE)
    source = tmp_path / "dort.c"
    source.write_text("#include <unistd.h>\nint main(void){ sleep(60); return 0; }\n", encoding="utf-8")
    new_exe = scenario.plan.staging / "Contents" / "MacOS" / "Kut-Studio"
    built = subprocess.run([compiler, str(source), "-o", str(new_exe)], capture_output=True, check=False)
    if built.returncode != 0:
        pytest.skip("compilation impossible sur cette machine")
    launcher = scenario.root / "open-sleeper.sh"
    pidfile = scenario.root / "sleeper.pid"
    launcher.write_text(
        '#!/bin/sh\nprintf \'%s\\n\' "$1" >> "$STUB_OPEN_LOG"\n'
        f'if [ -x "$1/Contents/MacOS/Kut-Studio" ] && [ ! -e "{pidfile}" ]; then\n'
        f'  "$1/Contents/MacOS/Kut-Studio" >/dev/null 2>&1 & echo $! > "{pidfile}"\nfi\nexit 0\n', encoding="utf-8")
    launcher.chmod(0o755)
    scenario.stub = launcher
    done = scenario.run(KUT_UPD_START_SECONDS="1")
    assert done.returncode == 1, scenario.log()
    pid = int(pidfile.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 10
    while update_install.pid_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not update_install.pid_alive(pid), "le processus lancé depuis la nouvelle application a été arrêté"
    assert scenario.installed_version == OLD


# ---------------------------------------------------------------------------
# Reprise au démarrage
# ---------------------------------------------------------------------------


def _recover(scenario: Scenario, *, running: str = NEW, alive=lambda pid: False):
    return recover_leftovers(scenario.app, updates_dir=scenario.updates, lock=scenario.plan.lock,
                             status=scenario.plan.status, running_version=running, alive=alive)


def test_an_unfinished_install_with_a_running_new_app_drops_the_backup(tmp_path):
    scenario = Scenario(tmp_path)
    shutil.rmtree(scenario.plan.staging)
    scenario.app.rename(scenario.plan.previous)
    make_app(scenario.apps, version=NEW, executable=GOOD_EXE % NEW)
    report = _recover(scenario)
    assert report.removed == (scenario.plan.previous,) and not scenario.plan.previous.exists()
    assert scenario.installed_version == NEW


def test_a_backup_of_the_running_version_is_kept(tmp_path):
    scenario = Scenario(tmp_path)
    shutil.rmtree(scenario.plan.staging)
    scenario.app.rename(scenario.plan.previous)
    make_app(scenario.apps, version=NEW, executable=GOOD_EXE % NEW)
    report = _recover(scenario, running=OLD)
    assert report.kept == (scenario.plan.previous,) and scenario.plan.previous.is_dir()


def test_recovery_leaves_everything_while_a_helper_is_alive(tmp_path):
    scenario = Scenario(tmp_path)
    scenario.plan.lock.write_text("4242", encoding="utf-8")
    before = snapshot(scenario.root)
    report = _recover(scenario, alive=lambda pid: pid == 4242)
    assert report.busy and snapshot(scenario.root) == before


def test_recovery_removes_a_stale_lock_and_orphan_stagings_but_not_a_live_one(tmp_path):
    scenario = Scenario(tmp_path)
    scenario.plan.lock.write_text("4242", encoding="utf-8")
    other = scenario.apps / ".Kut-Studio-update-777"
    other.mkdir()
    report = _recover(scenario, running=OLD, alive=lambda pid: pid == 777)
    assert not scenario.plan.lock.exists()
    assert other.is_dir(), "le staging d'une autre instance encore ouverte reste"
    assert scenario.plan.staging in report.removed and not scenario.plan.staging.exists()
    assert scenario.installed_version == OLD


def test_recovery_never_touches_unrelated_folders(tmp_path):
    scenario = Scenario(tmp_path)
    (scenario.apps / "Autre.app").mkdir()
    (scenario.apps / ".Kut-Studio.app.previous-1.0.0.failed").mkdir()
    before = snapshot(scenario.outside)
    _recover(scenario, running=OLD)
    assert (scenario.apps / "Autre.app").is_dir() and (scenario.apps / ".Kut-Studio.app.previous-1.0.0.failed").is_dir()
    assert_untouched_outside(scenario, before)


def test_read_outcome_refuses_anything_unexpected(tmp_path):
    path = tmp_path / "status.json"
    assert read_outcome(path) is None
    for content in ("", "[]", "{", '{"result":"weird","version":"1","previous":"0","reason":"x"}',
                    '{"result":"ok","version":1,"previous":"0","reason":"x"}', "x" * 10_000):
        path.write_text(content, encoding="utf-8")
        assert read_outcome(path) is None
    path.write_text('{"result":"ok","version":"2.0.0","previous":"1.0.0","reason":"started"}', encoding="utf-8")
    outcome = read_outcome(path)
    assert outcome is not None and outcome.result == "ok"


def test_started_marker_is_atomic_json_with_version_and_pid(tmp_path):
    path = tmp_path / "updates" / "started.json"
    write_started_marker(path, "2.0.0", 4321)
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == "2.0.0"
    assert '"version":"2.0.0"' in path.read_text(encoding="utf-8"), "le format que cherche l'assistant"
    assert '"pid":4321' in path.read_text(encoding="utf-8")
    assert sorted(p.name for p in path.parent.iterdir()) == ["started.json"]


# ---------------------------------------------------------------------------
# Plan, environnement, lancement
# ---------------------------------------------------------------------------


def test_plan_paths_are_siblings_of_the_application_for_atomic_renames(tmp_path):
    app = tmp_path / "Apps" / "Kut-Studio.app"
    plan = make_plan(app, pid=123, version="2.0.0", from_version="1.0.0-beta.1", directory=tmp_path / "cache")
    assert plan.staging.parent == plan.previous.parent == app.parent
    assert plan.staging.name == ".Kut-Studio-update-123" and plan.previous.name == ".Kut-Studio.app.previous-1.0.0-beta.1"


@pytest.mark.parametrize("version", ["", "2.0.0; rm -rf ~", "../x", "a" * 100, "1 2"])
def test_plan_refuses_versions_that_could_inject_into_paths_or_json(tmp_path, version):
    with pytest.raises(ValueError):
        make_plan(tmp_path / "Kut-Studio.app", pid=1, version=version, from_version="1.0.0", directory=tmp_path)
    with pytest.raises(ValueError):
        make_plan(tmp_path / "Kut-Studio.app", pid=1, version="2.0.0", from_version=version, directory=tmp_path)


def test_launch_is_detached_and_logs_to_install_log(tmp_path, monkeypatch):
    monkeypatch.setenv("DYLD_INSERT_LIBRARIES", "/nulle/part")
    plan = make_plan(tmp_path / "Apps" / "Kut-Studio.app", pid=os.getpid(), version="2.0.0", from_version="1.0.0",
                     directory=tmp_path / "cache")
    calls: list[tuple] = []

    class FakeProcess:
        pid = 4242

    def fake_popen(args, **kwargs):
        calls.append((args, kwargs))
        return FakeProcess()

    assert update_helper.launch_helper(plan, popen=fake_popen) == 4242
    ((args, kwargs),) = calls
    assert args[0] == "/bin/sh" and Path(args[1]).name == "install.sh"
    assert kwargs["start_new_session"] is True and kwargs["stdin"] == subprocess.DEVNULL
    assert kwargs["stderr"] == subprocess.STDOUT
    env = kwargs["env"]
    assert "DYLD_INSERT_LIBRARIES" not in env and env["KUT_UPD_APP"] == str(plan.app) and env["KUT_UPD_OPEN"] == "/usr/bin/open"
    script = Path(args[1])
    assert script.stat().st_mode & 0o777 == 0o700
    assert plan.app not in script.parents, "l'assistant est hors du bundle qu'il remplace"
    assert str(plan.app) not in script.read_text(encoding="utf-8"), "aucun chemin n'est écrit dans le script"


# ---------------------------------------------------------------------------
# Décision
# ---------------------------------------------------------------------------


class _Package:
    def __init__(self, status=None):
        if status is not None:
            self.signature_status = status


class _Status:
    def __init__(self, name):
        self.name = name


@pytest.fixture
def real_app(tmp_path):
    return make_app(tmp_path, version=OLD, executable=SILENT_EXE)


def _context(app: Path, **options) -> InstallContext:
    return InstallContext(InstallKind.MACOS_APP, PurePosixPath(str(app)), **options)


def test_signature_policy_reads_the_verified_status_of_the_package():
    policy = SignaturePolicy()
    assert policy.allows(_Package(_Status("VERIFIED"))) and policy.allows(_Package("verified"))
    assert not policy.allows(_Package(_Status("ABSENT"))) and not policy.allows(_Package(_Status("INVALID")))
    assert not policy.allows(_Package()), "sans statut de signature (lot 1 absent) : jamais"
    assert FixedPolicy(True).allows(object()) and not FixedPolicy(False).allows(object())


def test_install_is_available_only_for_a_verified_writable_app(real_app):
    decision = evaluate_install(_context(real_app), object(), policy=FixedPolicy(True), platform_name="darwin")
    assert decision.available and decision.refusal is None and decision.app == real_app
    decision = evaluate_install(_context(real_app), object(), policy=FixedPolicy(False), platform_name="darwin")
    assert not decision.available and decision.refusal is InstallRefusal.NOT_VERIFIED


@pytest.mark.parametrize(("context", "refusal"), [
    (InstallContext(InstallKind.SOURCE, PurePosixPath("/src")), InstallRefusal.SOURCE),
    (InstallContext(InstallKind.WINDOWS_FOLDER, PurePosixPath("/x")), InstallRefusal.NOT_MACOS),
    (InstallContext(InstallKind.LINUX_FOLDER, PurePosixPath("/x")), InstallRefusal.NOT_MACOS),
    (InstallContext(InstallKind.MACOS_APP, PurePosixPath("/Applications/Kut-Studio.app"), translocated=True),
     InstallRefusal.TRANSLOCATED),
    (InstallContext(InstallKind.MACOS_APP, None), InstallRefusal.NOT_BUNDLE),
    (InstallContext(InstallKind.MACOS_APP, PurePosixPath("/Applications/Autre.app")), InstallRefusal.NOT_BUNDLE),
    (InstallContext(InstallKind.MACOS_APP, PurePosixPath("/nulle/part/Kut-Studio.app")), InstallRefusal.MISSING_BUNDLE),
])
def test_install_is_refused_with_a_reason(context, refusal):
    decision = evaluate_install(context, object(), policy=FixedPolicy(True), platform_name="darwin")
    assert not decision.available and decision.refusal is refusal


def test_install_is_refused_on_other_systems_read_only_volumes_and_missing_rights(real_app):
    context = _context(real_app)
    assert evaluate_install(context, object(), policy=FixedPolicy(True), platform_name="linux").refusal is InstallRefusal.NOT_MACOS
    read_only = evaluate_install(context, object(), policy=FixedPolicy(True), platform_name="darwin",
                                 read_only=lambda path: True)
    assert read_only.refusal is InstallRefusal.READ_ONLY_VOLUME
    no_parent = evaluate_install(context, object(), policy=FixedPolicy(True), platform_name="darwin",
                                 access=lambda path, mode: path != str(real_app.parent))
    assert no_parent.refusal is InstallRefusal.PARENT_NOT_WRITABLE
    no_bundle = evaluate_install(context, object(), policy=FixedPolicy(True), platform_name="darwin",
                                 access=lambda path, mode: path != str(real_app))
    assert no_bundle.refusal is InstallRefusal.BUNDLE_NOT_WRITABLE


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="droits POSIX, hors root")
def test_a_really_unwritable_parent_folder_is_refused(tmp_path):
    parent = tmp_path / "lecture seule"
    parent.mkdir()
    app = make_app(parent, version=OLD, executable=SILENT_EXE)
    parent.chmod(0o555)
    try:
        decision = evaluate_install(_context(app), object(), policy=FixedPolicy(True), platform_name="darwin")
        assert decision.refusal is InstallRefusal.PARENT_NOT_WRITABLE
    finally:
        parent.chmod(0o755)


# ---------------------------------------------------------------------------
# Validation du nouveau bundle (outils système simulés)
# ---------------------------------------------------------------------------


class FakeTools:
    """Exécuteur injecté : enregistre les commandes et répond comme ``lipo``, ``codesign``, ``spctl``, ``ditto``."""

    def __init__(self, *, archs="arm64", verify=0, adhoc=True, team=None, assess=0, installed_team=None):
        self.archs, self.verify, self.adhoc, self.team, self.assess = archs, verify, adhoc, team, assess
        self.installed_team = installed_team
        self.installed: str | None = None            # chemin de l'application installée (réponses distinctes)
        self.commands: list[list[str]] = []

    def __call__(self, args):
        args = list(args)
        self.commands.append(args)
        tool = Path(args[0]).name
        if tool == "lipo":
            return CommandResult(0, self.archs + "\n")
        if tool == "codesign" and "--verify" in args:
            return CommandResult(self.verify, "", "invalid signature" if self.verify else "")
        if tool == "codesign":
            if self.installed is not None and args[-1] == self.installed:
                adhoc, team = self.installed_team is None, self.installed_team
            else:
                adhoc, team = self.adhoc, self.team
            text = "Signature=adhoc\n" if adhoc else "Signature size=9000\n"
            text += f"TeamIdentifier={team}\n" if team else "TeamIdentifier=not set\n"
            return CommandResult(0, "", text)
        if tool == "spctl":
            return CommandResult(self.assess, "", "rejected" if self.assess else "")
        if tool == "ditto":
            with zipfile.ZipFile(args[-2]) as archive:
                archive.extractall(args[-1])
            return CommandResult(0)
        return CommandResult(0)

    def tools(self) -> list[str]:
        return [Path(c[0]).name for c in self.commands]


def _validate(bundle, tools, *, version=NEW, installed=OLD, arch="arm64", identifier=IDENTIFIER, installed_team=None):
    return validate_bundle(bundle, expected_identifier=identifier, expected_version=Version.parse(version),
                           installed_version=Version.parse(installed), required_arch=arch, installed_team=installed_team,
                           run=tools)


def test_a_valid_ad_hoc_bundle_passes_with_codesign_only(tmp_path):
    bundle = make_app(tmp_path, version=NEW, executable=SILENT_EXE)
    tools = FakeTools()
    report = _validate(bundle, tools)
    assert report.version == Version.parse(NEW) and report.adhoc
    assert "spctl" not in tools.tools(), "ad hoc : codesign seul (documenté)"
    assert ["--verify", "--deep", "--strict"] == [a for a in next(c for c in tools.commands if "--verify" in c) if a.startswith("--")]


@pytest.mark.parametrize(("options", "problem"), [
    ({"identifier": "com.autre.app"}, StageProblem.IDENTIFIER),
    ({"version": "2.0.1"}, StageProblem.VERSION),
    ({"installed": "2.0.0"}, StageProblem.NOT_NEWER),
    ({"installed": "3.0.0"}, StageProblem.NOT_NEWER),
])
def test_a_bundle_with_the_wrong_identity_or_version_is_refused(tmp_path, options, problem):
    bundle = make_app(tmp_path, version=NEW, executable=SILENT_EXE)
    with pytest.raises(StageError) as raised:
        _validate(bundle, FakeTools(), **options)
    assert raised.value.problem is problem


def test_the_identifier_of_the_bundle_is_really_compared(tmp_path):
    bundle = make_app(tmp_path, version=NEW, executable=SILENT_EXE, identifier="com.evil.app")
    with pytest.raises(StageError) as raised:
        _validate(bundle, FakeTools())
    assert raised.value.problem is StageProblem.IDENTIFIER


def test_a_wrong_architecture_or_a_broken_signature_is_refused(tmp_path):
    bundle = make_app(tmp_path, version=NEW, executable=SILENT_EXE)
    with pytest.raises(StageError) as raised:
        _validate(bundle, FakeTools(archs="x86_64"))
    assert raised.value.problem is StageProblem.ARCHITECTURE
    with pytest.raises(StageError) as raised:
        _validate(bundle, FakeTools(verify=1))
    assert raised.value.problem is StageProblem.SIGNATURE
    assert _validate(bundle, FakeTools(archs="x86_64 arm64")).architectures == ("x86_64", "arm64")


def test_a_developer_id_bundle_must_pass_gatekeeper_and_keep_the_team(tmp_path):
    bundle = make_app(tmp_path, version=NEW, executable=SILENT_EXE)
    tools = FakeTools(adhoc=False, team=TEAM, installed_team=TEAM)
    _validate(bundle, tools, installed_team=TEAM)
    assert "spctl" in tools.tools()
    with pytest.raises(StageError) as raised:
        _validate(bundle, FakeTools(adhoc=False, team=TEAM, assess=3), installed_team=TEAM)
    assert raised.value.problem is StageProblem.GATEKEEPER
    with pytest.raises(StageError) as raised:
        _validate(bundle, FakeTools(adhoc=False, team="ZZZZZ99999"), installed_team=TEAM)
    assert raised.value.problem is StageProblem.TEAM
    with pytest.raises(StageError) as raised:
        _validate(bundle, FakeTools(adhoc=True), installed_team=TEAM)
    assert raised.value.problem is StageProblem.TEAM, "une application signée ne devient pas ad hoc en silence"


@pytest.mark.parametrize("breakage", ["no-plist", "bad-plist", "no-exe", "exe-slash", "huge-plist"])
def test_a_malformed_bundle_is_refused(tmp_path, breakage):
    bundle = make_app(tmp_path, version=NEW, executable=SILENT_EXE)
    plist = bundle / "Contents" / "Info.plist"
    if breakage == "no-plist":
        plist.unlink()
    elif breakage == "bad-plist":
        plist.write_text("pas un plist", encoding="utf-8")
    elif breakage == "no-exe":
        (bundle / "Contents" / "MacOS" / "Kut-Studio").unlink()
    elif breakage == "exe-slash":
        plist.write_bytes(plistlib.dumps({"CFBundleIdentifier": IDENTIFIER, "CFBundleShortVersionString": NEW,
                                          "CFBundleExecutable": "../../x"}))
    else:
        plist.write_bytes(b"x" * (2 * 1024 * 1024))
    with pytest.raises(StageError) as raised:
        _validate(bundle, FakeTools())
    assert raised.value.problem is StageProblem.BUNDLE


def test_error_details_are_bounded(tmp_path):
    bundle = make_app(tmp_path, version=NEW, executable=SILENT_EXE, identifier="x" * 5000)
    with pytest.raises(StageError) as raised:
        _validate(bundle, FakeTools())
    assert len(str(raised.value)) < 600


# ---------------------------------------------------------------------------
# Staging
# ---------------------------------------------------------------------------


def _archive(path: Path, *, version=NEW, identifier=IDENTIFIER, extra: dict[str, bytes] | None = None,
             prefix="Kut-Studio.app/") -> Path:
    with zipfile.ZipFile(path, "w") as bundle:
        bundle.writestr(f"{prefix}Contents/Info.plist", plistlib.dumps({
            "CFBundleIdentifier": identifier, "CFBundleShortVersionString": version, "CFBundleExecutable": "Kut-Studio"}))
        bundle.writestr(f"{prefix}Contents/MacOS/Kut-Studio", SILENT_EXE)
        for name, data in (extra or {}).items():
            bundle.writestr(name, data)
    return path


def _stage(archive, tools, *, app, verified=True, version=NEW, installed=OLD):
    return stage_update(archive, app, expected_version=Version.parse(version), installed_version=Version.parse(installed),
                        required_arch="arm64", pid=999, signature_verified=verified, run=tools)


def test_staging_extracts_next_to_the_app_and_validates(tmp_path):
    (tmp_path / "Apps").mkdir()
    app = make_app(tmp_path / "Apps", version=OLD, executable=SILENT_EXE)
    archive = _archive(tmp_path / "paquet.zip")
    tools = FakeTools()
    staging = _stage(archive, tools, app=app)
    assert staging == app.parent / ".Kut-Studio-update-999" and (staging / "Kut-Studio.app").is_dir()
    ditto = next(c for c in tools.commands if Path(c[0]).name == "ditto")
    assert ditto[1:4] == ["-x", "-k", "--noqtn"], "extraction sans propager la quarantaine"
    xattr = next(c for c in tools.commands if Path(c[0]).name == "xattr")
    assert xattr[1:3] == ["-dr", "com.apple.quarantine"] and xattr[-1].endswith("Kut-Studio.app")
    assert archive.exists(), "l'archive vérifiée n'est jamais modifiée"


def test_staging_refuses_to_run_without_a_verified_signature(tmp_path):
    (tmp_path / "Apps").mkdir()
    app = make_app(tmp_path / "Apps", version=OLD, executable=SILENT_EXE)
    before = snapshot(tmp_path / "Apps")
    tools = FakeTools()
    with pytest.raises(StageError) as raised:
        _stage(_archive(tmp_path / "p.zip"), tools, verified=False, app=app)
    assert raised.value.problem is StageProblem.NOT_VERIFIED
    assert tools.commands == [] and snapshot(tmp_path / "Apps") == before, "ni extraction ni retrait de quarantaine"


@pytest.mark.parametrize("failure", ["identifier", "version", "older", "arch", "codesign"])
def test_a_staging_that_fails_validation_is_deleted_and_the_app_is_untouched(tmp_path, failure):
    (tmp_path / "Apps").mkdir()
    app = make_app(tmp_path / "Apps", version=OLD, executable=SILENT_EXE)
    before = snapshot(app)
    options: dict = {}
    version, installed = NEW, OLD
    tools = FakeTools()
    if failure == "identifier":
        options["identifier"] = "com.autre"
    elif failure == "version":
        options["version"] = "2.0.1"
    elif failure == "older":
        options["version"], version, installed = "0.9.0", "0.9.0", OLD
    elif failure == "arch":
        tools = FakeTools(archs="x86_64")
    else:
        tools = FakeTools(verify=1)
    archive = _archive(tmp_path / "p.zip", **options)
    with pytest.raises(StageError):
        _stage(archive, tools, version=version, installed=installed, app=app)
    assert snapshot(app) == before
    assert sorted(p.name for p in app.parent.iterdir()) == ["Kut-Studio.app"], "plus de staging"


@pytest.mark.parametrize(("name", "prefix"), [("../evil", "Kut-Studio.app/"), ("/abs/evil", "Kut-Studio.app/"),
                                              ("Autre.app/x", "Kut-Studio.app/"), ("a\\b", "Kut-Studio.app/")])
def test_hostile_archive_entries_are_refused_before_extraction(tmp_path, name, prefix):
    (tmp_path / "Apps").mkdir()
    app = make_app(tmp_path / "Apps", version=OLD, executable=SILENT_EXE)
    archive = _archive(tmp_path / "p.zip", extra={name: b"x"})
    tools = FakeTools()
    with pytest.raises(StageError) as raised:
        _stage(archive, tools, app=app)
    assert raised.value.problem is StageProblem.ARCHIVE
    assert not any(Path(c[0]).name == "ditto" for c in tools.commands)


def test_an_archive_that_does_not_fit_on_the_disk_is_refused(tmp_path, monkeypatch):
    (tmp_path / "Apps").mkdir()
    app = make_app(tmp_path / "Apps", version=OLD, executable=SILENT_EXE)
    monkeypatch.setattr(shutil, "disk_usage", lambda path: shutil._ntuple_diskusage(10, 10, 0))
    with pytest.raises(StageError) as raised:
        _stage(_archive(tmp_path / "p.zip"), FakeTools(), app=app)
    assert raised.value.problem is StageProblem.DISK


def test_a_corrupt_archive_is_refused(tmp_path):
    (tmp_path / "Apps").mkdir()
    app = make_app(tmp_path / "Apps", version=OLD, executable=SILENT_EXE)
    bad = tmp_path / "p.zip"
    bad.write_bytes(b"pas une archive" * 100)
    with pytest.raises(StageError) as raised:
        _stage(bad, FakeTools(), app=app)
    assert raised.value.problem is StageProblem.ARCHIVE


@pytest.mark.skipif(sys.platform != "darwin" or not Path("/usr/bin/ditto").exists(), reason="ditto réel : macOS seulement")
def test_real_ditto_extracts_without_propagating_the_quarantine(tmp_path):
    from core.updates import macos_extended_attribute, mark_as_downloaded

    (tmp_path / "Apps").mkdir()
    app = make_app(tmp_path / "Apps", version=OLD, executable=SILENT_EXE)
    archive = _archive(tmp_path / "paquet.zip")
    assert mark_as_downloaded(archive, url="https://exemple.invalid/x", platform_name="darwin")
    fake = FakeTools()

    def runner(args):
        return update_install.run_command(args) if Path(args[0]).name in ("ditto", "xattr") else fake(args)

    staging = stage_update(archive, app, expected_version=Version.parse(NEW), installed_version=Version.parse(OLD),
                           required_arch="arm64", pid=999, signature_verified=True, run=runner)
    assert (staging / "Kut-Studio.app" / "Contents" / "Info.plist").is_file()
    assert macos_extended_attribute(staging / "Kut-Studio.app", "com.apple.quarantine") is None
    assert macos_extended_attribute(archive, "com.apple.quarantine") is not None, "le paquet garde sa marque"
