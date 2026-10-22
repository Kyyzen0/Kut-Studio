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


def write_lock(lock: Path, pid: int | None) -> None:
    """Verrou de l'assistant : un dossier qui contient le pid de son propriétaire."""
    lock.mkdir(parents=True)
    if pid is not None:
        (lock / "pid").write_text(str(pid), encoding="utf-8")


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

    def environment(self, **extra: str) -> dict[str, str]:
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
        return env

    def run(self, **extra: str) -> subprocess.CompletedProcess[bytes]:
        script = write_helper_script(self.updates, pid=self.pid)
        return subprocess.run(["/bin/sh", str(script)], env=self.environment(**extra), capture_output=True, timeout=120,
                              check=False)

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
def test_the_helper_refuses_a_staging_that_is_not_named_like_one(tmp_path):
    scenario = Scenario(tmp_path)
    precious = scenario.apps / "precieux"
    scenario.plan.staging.rename(precious)
    scenario.plan = HelperPlan(**{**scenario.plan.__dict__, "staging": precious})
    before = snapshot(scenario.apps)
    assert scenario.run().returncode == 2
    assert snapshot(scenario.apps) == before, "refusé avant tout examen : rien n'a bougé"


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
                               running_version=OLD)
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
                             running_version=running, alive=alive)


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
    write_lock(scenario.plan.lock, 4242)
    before = snapshot(scenario.root)
    report = _recover(scenario, alive=lambda pid: pid == 4242)
    assert report.busy and snapshot(scenario.root) == before


def test_recovery_removes_a_stale_lock_and_orphan_stagings_but_not_a_live_one(tmp_path):
    scenario = Scenario(tmp_path)
    write_lock(scenario.plan.lock, 4242)
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
    if sys.platform != "win32":
        assert script.stat().st_mode & 0o777 == 0o700
    assert plan.app not in script.parents, "l'assistant est hors du bundle qu'il remplace"
    assert str(plan.app) not in script.read_text(encoding="utf-8"), "aucun chemin n'est écrit dans le script"


# ---------------------------------------------------------------------------
# Décision
# ---------------------------------------------------------------------------


SUMS = b"0" * 64 + b"  Kut-Studio-2.0.0-macos-arm64.zip\n"


def _key(seed_byte: int):
    from core import ed25519
    from core.update_keys import PublicKey

    seed = bytes([seed_byte]) * 32
    return seed, PublicKey(ed25519.public_key_from_seed(seed))


def _download(sums: bytes = SUMS, *, signer: int | None = 1, listed: int = 1, signature: bytes | None = None):
    """Vrai ``DownloadResult`` dont la signature est vérifiée par ``verify_release_signature`` (liste de ``listed``)."""
    from core import ed25519
    from core.update_service import DownloadResult
    from core.update_signature import format_signature_file, verify_release_signature

    if signature is None and signer is not None:
        seed, public = _key(signer)
        signature = format_signature_file(public.key_id, ed25519.sign(seed, SUMS))
    result = verify_release_signature(sums, signature, [_key(listed)[1]])
    return DownloadResult(object(), signature=result)       # type: ignore[arg-type]


def test_signature_policy_allows_only_a_verified_signature_by_a_listed_key():
    policy = SignaturePolicy(keys=[_key(1)[1]])
    verified = _download()
    assert verified.signature_status.name == "VERIFIED" and policy.allows(verified)
    assert not SignaturePolicy().allows(verified), "liste de clés de l'application vide aujourd'hui : jamais"
    assert not SignaturePolicy(keys=[_key(9)[1]]).allows(verified), "la clé qui a signé n'est pas dans la liste"


@pytest.mark.parametrize("case", ["unsigned", "unknown_key", "forged", "garbage", "no_result"])
def test_signature_policy_refuses_everything_that_is_not_verified(case):
    policy = SignaturePolicy(keys=[_key(1)[1]])
    if case == "unsigned":
        package = _download(signer=None)                           # ABSENT
    elif case == "unknown_key":
        package = _download(signer=2)                              # signé par une clé que l'application ne connaît pas
    elif case == "forged":
        package = _download(sums=SUMS.replace(b"0", b"1", 1))      # la signature ne correspond pas aux sommes reçues
    elif case == "garbage":
        package = _download(signature=b"n'importe quoi")
    else:
        from core.update_service import DownloadResult

        package = DownloadResult(object())                         # type: ignore[arg-type]
    assert not policy.allows(package)
    assert not policy.allows(object()), "sans statut de signature : jamais"
    assert FixedPolicy(True).allows(object()) and not FixedPolicy(False).allows(object())


def test_the_unforgeable_status_is_the_enum_not_a_look_alike_string():
    class Fake:
        signature_status = "verified"
        signature = None

    assert not SignaturePolicy(keys=[_key(1)[1]]).allows(Fake())


@pytest.fixture
def real_app(tmp_path):
    return make_app(tmp_path, version=OLD, executable=SILENT_EXE)


def _context(app: Path, **options) -> InstallContext:
    return InstallContext(InstallKind.MACOS_APP, PurePosixPath(str(app)), **options)


def test_install_is_available_only_for_a_verified_writable_app(real_app):
    decision = evaluate_install(_context(real_app), object(), policy=FixedPolicy(True))
    assert decision.available and decision.refusal is None and decision.app == real_app
    decision = evaluate_install(_context(real_app), object(), policy=FixedPolicy(False))
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
    decision = evaluate_install(context, object(), policy=FixedPolicy(True))
    assert not decision.available and decision.refusal is refusal


def test_install_is_refused_on_read_only_volumes_and_missing_rights(real_app):
    context = _context(real_app)
    read_only = evaluate_install(context, object(), policy=FixedPolicy(True),
                                 read_only=lambda path: True)
    assert read_only.refusal is InstallRefusal.READ_ONLY_VOLUME
    no_parent = evaluate_install(context, object(), policy=FixedPolicy(True),
                                 access=lambda path, mode: path != str(real_app.parent))
    assert no_parent.refusal is InstallRefusal.PARENT_NOT_WRITABLE
    no_bundle = evaluate_install(context, object(), policy=FixedPolicy(True),
                                 access=lambda path, mode: path != str(real_app))
    assert no_bundle.refusal is InstallRefusal.BUNDLE_NOT_WRITABLE


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="droits POSIX, hors root")
def test_a_really_unwritable_parent_folder_is_refused(tmp_path):
    parent = tmp_path / "lecture seule"
    parent.mkdir()
    app = make_app(parent, version=OLD, executable=SILENT_EXE)
    parent.chmod(0o555)
    try:
        decision = evaluate_install(_context(app), object(), policy=FixedPolicy(True))
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
            for executable in Path(args[-1]).glob("*/Contents/MacOS/*"):
                executable.chmod(0o755)                  # ditto conserve les droits de l'archive
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
             prefix="Kut-Studio.app/", executable=SILENT_EXE) -> Path:
    with zipfile.ZipFile(path, "w") as bundle:
        bundle.writestr(f"{prefix}Contents/Info.plist", plistlib.dumps({
            "CFBundleIdentifier": identifier, "CFBundleShortVersionString": version, "CFBundleExecutable": "Kut-Studio"}))
        bundle.writestr(f"{prefix}Contents/MacOS/Kut-Studio", executable)
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
    assert staging == app.parent / ".Kut-Studio-update-999" and (staging / "Contents" / "Info.plist").is_file(), \
        "le staging est le bundle lui-même : l'assistant le renomme tel quel"
    assert sorted(p.name for p in app.parent.iterdir()) == [".Kut-Studio-update-999", "Kut-Studio.app"], "plus d'extraction"
    ditto = next(c for c in tools.commands if Path(c[0]).name == "ditto")
    assert ditto[1:4] == ["-x", "-k", "--noqtn"], "extraction sans propager la quarantaine"
    xattr = next(c for c in tools.commands if Path(c[0]).name == "xattr")
    assert xattr[1:3] == ["-dr", "com.apple.quarantine"] and xattr[-1].endswith("Kut-Studio.app")
    assert not (app.parent / ".Kut-Studio-update-999.extract").exists()
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
    assert mark_as_downloaded(archive, url="https://exemple.invalid/x")
    fake = FakeTools()

    def runner(args):
        return update_install.run_command(args) if Path(args[0]).name in ("ditto", "xattr") else fake(args)

    staging = stage_update(archive, app, expected_version=Version.parse(NEW), installed_version=Version.parse(OLD),
                           required_arch="arm64", pid=999, signature_verified=True, run=runner)
    assert (staging / "Contents" / "Info.plist").is_file()
    assert macos_extended_attribute(staging, "com.apple.quarantine") is None
    assert macos_extended_attribute(archive, "com.apple.quarantine") is not None, "le paquet garde sa marque"


@needs_sh
def test_a_staged_bundle_is_exactly_what_the_helper_installs(tmp_path):
    """De bout en bout : ``stage_update`` puis l'assistant sur le staging qu'il a produit (le même contrat)."""
    scenario = Scenario(tmp_path)
    shutil.rmtree(scenario.plan.staging)
    # Le faux ``Kut-Studio`` de l'archive écrit le fichier « démarré » comme la vraie application.
    archive = _archive(tmp_path / "paquet.zip", executable=GOOD_EXE % NEW)
    staged = stage_update(archive, scenario.app, expected_version=Version.parse(NEW), installed_version=Version.parse(OLD),
                          required_arch="arm64", pid=scenario.pid, signature_verified=True, run=FakeTools())
    assert staged == scenario.plan.staging
    done = scenario.run()
    assert done.returncode == 0, scenario.log()
    info = plistlib.loads((scenario.app / "Contents" / "Info.plist").read_bytes())
    assert info["CFBundleShortVersionString"] == NEW
    assert sorted(p.name for p in scenario.apps.iterdir()) == ["Kut-Studio.app"]


@pytest.mark.skipif(sys.platform != "darwin" or shutil.which("cc") is None or not Path("/usr/bin/codesign").exists(),
                    reason="codesign, lipo et un compilateur C réels : macOS seulement")
def test_the_real_system_tools_validate_a_real_ad_hoc_bundle(tmp_path):
    """Vrais ``codesign`` / ``lipo`` sur un vrai petit bundle ad hoc fabriqué dans un dossier temporaire."""
    import platform

    machine = platform.machine()
    app = make_app(tmp_path, version=NEW, executable=SILENT_EXE)
    source = tmp_path / "main.c"
    source.write_text("int main(void){ return 0; }\n", encoding="utf-8")
    (app / "Contents" / "VERSION").unlink()                 # un fichier de plus dans Contents/ ferait refuser le sceau
    exe = app / "Contents" / "MacOS" / "Kut-Studio"
    built = subprocess.run(["cc", str(source), "-o", str(exe)], capture_output=True, check=False)
    if built.returncode != 0:
        pytest.skip("compilation impossible")
    signed = subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", str(app)], capture_output=True, check=False)
    if signed.returncode != 0:
        pytest.skip("signature ad hoc impossible : " + signed.stderr.decode(errors="replace")[:200])
    report = validate_bundle(app, expected_identifier=IDENTIFIER, expected_version=Version.parse(NEW),
                             installed_version=Version.parse(OLD), required_arch=machine)
    assert report.adhoc and report.team is None and machine in report.architectures
    with pytest.raises(StageError) as raised:
        validate_bundle(app, expected_identifier=IDENTIFIER, expected_version=Version.parse(NEW),
                        installed_version=Version.parse(OLD), required_arch="x86_64" if machine == "arm64" else "arm64")
    assert raised.value.problem is StageProblem.ARCHITECTURE
    exe.write_bytes(exe.read_bytes() + b"\0altere")          # le sceau de signature ne correspond plus
    with pytest.raises(StageError) as raised:
        validate_bundle(app, expected_identifier=IDENTIFIER, expected_version=Version.parse(NEW),
                        installed_version=Version.parse(OLD), required_arch=machine)
    assert raised.value.problem in (StageProblem.SIGNATURE, StageProblem.ARCHITECTURE)


# ---------------------------------------------------------------------------
# Verrou exclusif, renommages sans imbrication, arrêt des processus (revue Codex de #262)
# ---------------------------------------------------------------------------

FUNCTIONS = update_helper.HELPER_SCRIPT.split("# ---- fin des fonctions ----")[0]


def run_shell(scenario: Scenario, code: str, **env: str) -> subprocess.CompletedProcess[str]:
    """Exécute les FONCTIONS du script d'assistant (sans son déroulé) suivies de ``code``."""
    environment = scenario.environment(**env)
    return subprocess.run(["/bin/sh", "-c", FUNCTIONS + code], env=environment, capture_output=True, text=True,
                          timeout=60, check=False)


def _wait_for(predicate, seconds: float = 20.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


@needs_sh
def test_safe_mv_never_nests_into_an_existing_destination(tmp_path):
    scenario = Scenario(tmp_path)
    src, dst = scenario.root / "src", scenario.root / "dst"
    (src / "Contents").mkdir(parents=True)
    (src / "Contents" / "x").write_text("x", encoding="utf-8")
    dst.mkdir()
    def tree():
        return {k: v for k, v in snapshot(scenario.root).items() if not k.startswith("cache")}

    before = tree()
    done = run_shell(scenario, f'safe_mv "{src}" "{dst}"')
    assert done.returncode != 0
    assert tree() == before, "ni déplacé ni imbriqué"
    dst.rmdir()
    assert run_shell(scenario, f'safe_mv "{src}" "{dst}"').returncode == 0
    assert (dst / "Contents" / "x").is_file() and not src.exists()
    assert not [p for p in scenario.root.iterdir() if ".mv." in p.name], "aucun nom temporaire ne reste"


@needs_sh
@pytest.mark.parametrize("appears_at", [1, 2])
def test_safe_mv_undoes_a_destination_that_appears_in_the_middle(tmp_path, appears_at):
    scenario = Scenario(tmp_path)
    src, dst = scenario.root / "src", scenario.root / "dst"
    (src / "Contents").mkdir(parents=True)
    (src / "Contents" / "x").write_text("x", encoding="utf-8")
    shadow = (f'n=0; mv() {{ n=$((n + 1)); if [ "$n" -eq {appears_at} ]; then mkdir "{dst}"; fi; command mv "$@"; }}\n'
              f'safe_mv "{src}" "{dst}"')
    done = run_shell(scenario, shadow)
    assert done.returncode != 0
    assert (src / "Contents" / "x").is_file(), "la source est revenue à sa place"
    assert dst.is_dir() and list(dst.iterdir()) == [], "rien n'a été imbriqué dans la destination apparue"


@needs_sh
def test_safe_rm_refuses_anything_that_is_not_a_staging_a_backup_or_a_stale_lock(tmp_path):
    scenario = Scenario(tmp_path)
    keep = [scenario.root / "precieux", scenario.apps / "Autre.app", scenario.apps / "Kut-Studio.app.old"]
    for folder in keep:
        folder.mkdir(exist_ok=True)
    doomed = scenario.apps / ".Kut-Studio-update-123"
    doomed.mkdir()
    code = "\n".join(f'safe_rm "{path}"' for path in [*keep, doomed, "/", "relatif/.Kut-Studio-update-1"])
    run_shell(scenario, code)
    assert all(folder.is_dir() for folder in keep) and not doomed.exists()
    assert "suppression refusée" in scenario.log()


@needs_sh
@pytest.mark.parametrize("variable", ["KUT_UPD_APP", "KUT_UPD_STAGING", "KUT_UPD_PREVIOUS", "KUT_UPD_LOCK", "KUT_UPD_STATUS"])
@pytest.mark.parametrize("bad", ["/", "/a/../b/Kut-Studio.app", "relatif", "/x//y", "/dossier/"])
def test_the_helper_refuses_dangerous_paths_before_doing_anything(tmp_path, variable, bad):
    scenario = Scenario(tmp_path)
    before = snapshot(scenario.root)
    script = write_helper_script(scenario.updates, pid=scenario.pid)
    env = scenario.environment()
    env[variable] = bad
    done = subprocess.run(["/bin/sh", str(script)], env=env, capture_output=True, timeout=60, check=False)
    assert done.returncode == 2
    after = {k: v for k, v in snapshot(scenario.root).items() if not k.startswith("monde") and "helper-" not in k}
    assert after == {k: v for k, v in before.items() if "helper-" not in k}, "rien n'a été touché"


@needs_sh
def test_the_helper_refuses_staging_or_backup_outside_the_application_folder(tmp_path):
    scenario = Scenario(tmp_path)
    other = scenario.root / "ailleurs"
    other.mkdir()
    for variable, value in (("KUT_UPD_STAGING", other / ".Kut-Studio-update-1"),
                            ("KUT_UPD_PREVIOUS", other / ".Kut-Studio.app.previous-1.0.0")):
        script = write_helper_script(scenario.updates, pid=scenario.pid)
        env = scenario.environment()
        env[variable] = str(value)
        assert subprocess.run(["/bin/sh", str(script)], env=env, capture_output=True, timeout=60, check=False).returncode == 2


@needs_sh
def test_two_helpers_on_the_same_tree_exactly_one_replaces_the_application(tmp_path):
    waiter = subprocess.Popen(["/bin/sleep", "60"])
    first = None
    try:
        scenario = Scenario(tmp_path, pid=waiter.pid)
        script = write_helper_script(scenario.updates, pid=scenario.pid)
        first = subprocess.Popen(["/bin/sh", str(script)], env=scenario.environment(), stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
        assert _wait_for(lambda: (scenario.plan.lock / "pid").is_file()), "le premier assistant tient le verrou"
        owner = (scenario.plan.lock / "pid").read_text(encoding="utf-8")
        before = snapshot(scenario.apps)
        second = subprocess.run(["/bin/sh", str(script)], env=scenario.environment(), capture_output=True, timeout=60,
                                check=False)
        assert second.returncode == 6, "le second sort proprement"
        assert snapshot(scenario.apps) == before, "il n'a rien examiné ni modifié (comparaison d'arbres)"
        assert (scenario.plan.lock / "pid").read_text(encoding="utf-8") == owner, "et n'a pas pris le verrou"
        busy = scenario.status()
        assert busy is not None and (busy.result, busy.reason) == ("failed", "already_running")
        assert "une autre mise à jour est déjà en cours" in scenario.log()
        waiter.terminate()
        waiter.wait()                                   # l'application « se ferme » : le premier continue seul
        assert first.wait(timeout=90) == 0
        assert scenario.installed_version == NEW
        assert sorted(p.name for p in scenario.apps.iterdir()) == ["Kut-Studio.app"], "pas d'imbrication, pas de reste"
        assert not scenario.plan.app.joinpath("Kut-Studio.app").exists()
        outcome = scenario.status()
        assert outcome is not None and outcome.result == "ok", "le statut final est celui de l'assistant qui a travaillé"
        assert not scenario.plan.lock.exists()
    finally:
        waiter.kill()
        waiter.wait()
        if first is not None and first.poll() is None:
            first.kill()
            first.wait()


@needs_sh
def test_a_live_lock_is_never_stolen(tmp_path):
    scenario = Scenario(tmp_path)
    write_lock(scenario.plan.lock, os.getpid())                 # un propriétaire vivant : ce processus de test
    before = snapshot(scenario.apps)
    done = scenario.run()
    assert done.returncode == 6
    assert snapshot(scenario.apps) == before
    assert (scenario.plan.lock / "pid").read_text(encoding="utf-8") == str(os.getpid())


@needs_sh
@pytest.mark.parametrize("pid", ["dead", "none", "garbage"])
def test_a_stale_lock_is_taken_over_safely(tmp_path, pid):
    scenario = Scenario(tmp_path)
    write_lock(scenario.plan.lock, None)
    if pid == "dead":
        (scenario.plan.lock / "pid").write_text(str(dead_pid()), encoding="utf-8")
    elif pid == "garbage":
        (scenario.plan.lock / "pid").write_text("pas un pid", encoding="utf-8")
    done = scenario.run()
    assert done.returncode == 0, scenario.log()
    assert scenario.installed_version == NEW
    assert "verrou périmé" in scenario.log()
    assert not scenario.plan.lock.exists() and not [p for p in scenario.updates.iterdir() if ".stale." in p.name]


@needs_sh
def test_the_lock_is_released_on_failure_and_on_termination(tmp_path):
    failing = Scenario(tmp_path / "a", new_executable=SILENT_EXE)
    assert failing.run(KUT_UPD_START_SECONDS="1").returncode == 1
    assert not failing.plan.lock.exists(), "libéré après un retour arrière"
    invalid = Scenario(tmp_path / "b")
    (invalid.plan.staging / "Contents" / "Info.plist").unlink()
    assert invalid.run().returncode == 4 and not invalid.plan.lock.exists(), "libéré après un échec"
    waiter = subprocess.Popen(["/bin/sleep", "60"])
    helper = None
    try:
        scenario = Scenario(tmp_path / "c", pid=waiter.pid)
        script = write_helper_script(scenario.updates, pid=scenario.pid)
        before = snapshot(scenario.apps)
        helper = subprocess.Popen(["/bin/sh", str(script)], env=scenario.environment(), stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL)
        assert _wait_for(lambda: (scenario.plan.lock / "pid").is_file())
        helper.terminate()                              # SIGTERM à notre propre enfant, l'assistant
        assert helper.wait(timeout=60) == 1
        assert not scenario.plan.lock.exists(), "libéré sur SIGTERM"
        assert snapshot(scenario.apps) == before
    finally:
        waiter.kill()
        waiter.wait()
        if helper is not None and helper.poll() is None:
            helper.kill()
            helper.wait()


def _sleeper(tmp_path: Path, *, ignore_term: bool) -> Path:
    compiler = shutil.which("cc")
    if compiler is None:
        pytest.skip("pas de compilateur C pour fabriquer un exécutable qui dort")
    source = tmp_path / ("dort_sourd.c" if ignore_term else "dort.c")
    handler = "signal(SIGTERM, SIG_IGN);" if ignore_term else ""
    source.write_text(f"#include <signal.h>\n#include <unistd.h>\nint main(void){{ {handler} sleep(60); return 0; }}\n",
                      encoding="utf-8")
    program = tmp_path / source.stem
    if subprocess.run([compiler, str(source), "-o", str(program)], capture_output=True, check=False).returncode != 0:
        pytest.skip("compilation impossible sur cette machine")
    return program


def _launcher(scenario: Scenario, pidfile: Path) -> None:
    launcher = scenario.root / "open-sleeper.sh"
    launcher.write_text(
        '#!/bin/sh\nprintf \'%s\\n\' "$1" >> "$STUB_OPEN_LOG"\n'
        f'if [ -x "$1/Contents/MacOS/Kut-Studio" ] && [ ! -e "{pidfile}" ]; then\n'
        f'  "$1/Contents/MacOS/Kut-Studio" >/dev/null 2>&1 & echo $! > "{pidfile}"\nfi\nexit 0\n', encoding="utf-8")
    launcher.chmod(0o755)
    scenario.stub = launcher


def _terminate(*pids: int) -> None:
    for pid in pids:
        try:
            os.kill(pid, 9)
        except (ProcessLookupError, PermissionError):
            pass


@needs_sh
def test_a_new_application_that_ignores_sigterm_is_killed_before_the_rollback(tmp_path):
    program = _sleeper(tmp_path, ignore_term=True)
    scenario = Scenario(tmp_path, new_executable=SILENT_EXE)
    shutil.copyfile(program, scenario.plan.staging / "Contents" / "MacOS" / "Kut-Studio")
    (scenario.plan.staging / "Contents" / "MacOS" / "Kut-Studio").chmod(0o755)
    # Deux « autres » processus de l'utilisateur, dont un d'un bundle voisin au nom proche : à ne jamais toucher.
    neighbour = scenario.apps / "Kut-Studio.app2" / "Contents" / "MacOS"
    neighbour.mkdir(parents=True)
    shutil.copyfile(program, neighbour / "Kut-Studio")
    (neighbour / "Kut-Studio").chmod(0o755)
    bystanders = [subprocess.Popen([str(neighbour / "Kut-Studio")]), subprocess.Popen(["/bin/sleep", "60"])]
    pidfile = scenario.root / "sleeper.pid"
    _launcher(scenario, pidfile)
    try:
        done = scenario.run(KUT_UPD_START_SECONDS="1", KUT_UPD_TERM_SECONDS="1", KUT_UPD_KILL_SECONDS="10")
        assert done.returncode == 1, scenario.log()
        sleeper = int(pidfile.read_text(encoding="utf-8"))
        assert _wait_for(lambda: not update_install.pid_alive(sleeper)), "le processus sourd à SIGTERM a été tué"
        assert "SIGKILL des processus qui résistent" in scenario.log()
        assert scenario.installed_version == OLD, "puis le retour arrière a eu lieu"
        outcome = scenario.status()
        assert outcome is not None and outcome.result == "rolled_back"
        assert all(process.poll() is None for process in bystanders), "aucun autre processus n'a été touché"
    finally:
        for process in bystanders:
            process.kill()
            process.wait()
        if pidfile.exists():
            _terminate(int(pidfile.read_text(encoding="utf-8")))


@needs_sh
def test_a_rollback_is_refused_while_a_process_of_the_new_version_survives(tmp_path):
    program = _sleeper(tmp_path, ignore_term=True)
    scenario = Scenario(tmp_path, new_executable=SILENT_EXE)
    shutil.copyfile(program, scenario.plan.staging / "Contents" / "MacOS" / "Kut-Studio")
    (scenario.plan.staging / "Contents" / "MacOS" / "Kut-Studio").chmod(0o755)
    pidfile = scenario.root / "sleeper.pid"
    _launcher(scenario, pidfile)
    kill_log = scenario.root / "kill.log"
    fake_kill = scenario.root / "fake-kill.sh"
    fake_kill.write_text(f'#!/bin/sh\necho "$@" >> "{kill_log}"\nexit 0\n', encoding="utf-8")   # ne tue rien
    fake_kill.chmod(0o755)
    try:
        done = scenario.run(KUT_UPD_START_SECONDS="1", KUT_UPD_TERM_SECONDS="1", KUT_UPD_KILL_SECONDS="1",
                            KUT_UPD_KILL=str(fake_kill))
        assert done.returncode == 7, scenario.log()
        assert "-TERM" in kill_log.read_text(encoding="utf-8") and "-KILL" in kill_log.read_text(encoding="utf-8")
        assert scenario.installed_version == NEW, "la nouvelle application n'a pas été déplacée"
        assert scenario.plan.previous.is_dir() and (scenario.plan.previous / "Contents" / "VERSION").read_text(
            encoding="utf-8") == OLD, "la précédente est intacte"
        assert sorted(p.name for p in scenario.apps.iterdir()) == [scenario.plan.previous.name, "Kut-Studio.app"]
        assert scenario.opened() == [str(scenario.app)], "la précédente n'a pas été lancée à côté"
        outcome = scenario.status()
        assert outcome is not None and (outcome.result, outcome.reason) == ("failed", "rollback_impossible")
        assert "retour arrière impossible" in scenario.log()
        assert not scenario.plan.lock.exists()
    finally:
        if pidfile.exists():
            _terminate(int(pidfile.read_text(encoding="utf-8")))


def test_pid_alive_tells_a_live_process_from_a_dead_one_without_signalling_it():
    """Sous Windows, ``os.kill(pid, 0)`` enverrait Ctrl+C à la console entière : ``pid_alive`` ne doit rien envoyer."""
    assert update_install.pid_alive(os.getpid())
    assert not update_install.pid_alive(dead_pid())
    assert not update_install.pid_alive(0) and not update_install.pid_alive(-5)


def test_no_code_path_sends_signal_zero_on_windows():
    import ast

    tree = ast.parse(Path(update_install.__file__).read_text(encoding="utf-8"))
    kills = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
             and node.func.attr == "kill" and isinstance(node.func.value, ast.Name) and node.func.value.id == "os"]
    assert len(kills) == 1, "le seul os.kill de core/update_install.py est celui de pid_alive"
    function = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "pid_alive")
    guard = next(node for node in function.body if isinstance(node, ast.If) and "win32" in ast.unparse(node.test))
    assert kills[0].lineno > guard.end_lineno, "après le test de plateforme Windows, qui retourne avant"
