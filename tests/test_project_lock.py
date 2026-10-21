"""Le verrou consultatif d'un projet (ADR-0005, décision 3) : ``core/project_lock.py``, ``apply`` et la fenêtre.

Ce qui est garanti : un script ne réécrit jamais un projet que la fenêtre tient ouvert ; un verrou n'est repris que si son propriétaire
est **prouvé** disparu (le doute profite au propriétaire) ; un PID réutilisé ne passe pas pour l'ancien propriétaire ; on ne retire que
son propre verrou ; la fenêtre prévient mais ne refuse jamais d'ouvrir.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
from main_window_harness import build_window, install_dialogs

from core import cli
from core.process_platform import Probe, ProbeState, ProcessIdentity
from core.project_io import load_project, save_project
from core.project_lock import (
    LOCK_VERSION,
    LockOwner,
    LockVerdict,
    ProjectLock,
    ProjectLockedError,
    judge,
    lock_file_for,
    read_lock,
)
from core.project_model import MediaAsset, Project, Track
from ui import i18n

ROOT = Path(__file__).resolve().parent.parent
HOST = socket.gethostname()


class FakeOps:
    """Une sonde de processus déterministe : ``alive[pid]`` est le jeton d'un processus vivant ; ``unknown`` : pids illisibles."""

    def __init__(
        self, alive: dict[int, str] | None = None, unknown: set[int] | None = None
    ) -> None:
        self.alive = dict(alive or {})
        self.unknown = set(unknown or ())

    def probe(self, pid: int) -> Probe:
        if pid in self.unknown:
            return Probe(ProbeState.UNKNOWN)
        if pid in self.alive:
            return Probe(ProbeState.ALIVE, ProcessIdentity(pid, self.alive[pid], "kut"))
        return Probe(ProbeState.GONE)

    def kill_if_same(self, expected):  # pragma: no cover - jamais appelée par le verrou
        raise AssertionError("le verrou ne tue jamais")


OTHER = 4_000_001  # un PID qui n'est pas celui du test
ME = os.getpid()


def _write_owner(
    path: Path,
    *,
    pid: int = OTHER,
    token: str = "t1",
    host: str = HOST,
    kind: str = "app",
    started: float | None = None,
    nonce: str = "n-other",
) -> LockOwner:
    owner = LockOwner(
        pid,
        token,
        "kut",
        host,
        kind,
        started if started is not None else time.time(),
        nonce,
    )
    lock_file_for(path).write_text(
        json.dumps({"kut_lock": LOCK_VERSION, **owner.__dict__}), encoding="utf-8"
    )
    return owner


@pytest.fixture
def project_file(tmp_path) -> Path:
    path = tmp_path / "demo.kut"
    path.write_text("{}", encoding="utf-8")
    return path


# --- Le module --------------------------------------------------------------------------------------------------------------


def test_acquire_writes_who_holds_the_lock_and_release_removes_it(project_file):
    lock = ProjectLock(project_file, kind="cli", ops=FakeOps({ME: "me"}))
    assert lock.acquire() is None and lock.held
    owner = read_lock(project_file)
    assert owner is not None and (owner.pid, owner.token, owner.kind, owner.host) == (
        ME,
        "me",
        "cli",
        HOST,
    )
    assert lock_file_for(project_file).name == "demo.kut.lock"
    lock.release()
    assert not lock_file_for(project_file).exists() and not lock.held
    lock.release()  # idempotent


def test_a_symlink_and_its_target_share_one_lock(project_file, tmp_path):
    if sys.platform.startswith("win"):
        pytest.skip("liens symboliques : droits particuliers sous Windows")
    link = tmp_path / "lien.kut"
    link.symlink_to(project_file)
    assert lock_file_for(link) == lock_file_for(project_file)
    with ProjectLock(link, ops=FakeOps({ME: "me"})):
        assert read_lock(project_file) is not None


def test_a_lock_held_by_a_living_other_process_refuses_and_is_left_untouched(
    project_file,
):
    _write_owner(project_file, pid=OTHER, token="t1")
    before = lock_file_for(project_file).read_bytes()
    with pytest.raises(ProjectLockedError) as caught:
        ProjectLock(project_file, ops=FakeOps({OTHER: "t1", ME: "me"})).acquire()
    assert caught.value.verdict is LockVerdict.HELD and caught.value.owner.pid == OTHER
    assert lock_file_for(project_file).read_bytes() == before


def test_a_lock_of_a_vanished_process_is_stale_and_taken_over(project_file):
    _write_owner(project_file, pid=OTHER)
    lock = ProjectLock(project_file, ops=FakeOps({ME: "me"}))
    stale = lock.acquire()
    assert stale is not None and stale.pid == OTHER and lock.replaced_stale == stale
    owner = read_lock(project_file)
    assert owner is not None and owner.pid == ME


def _flaky_read(monkeypatch, outcomes):
    """``Path.read_bytes`` rend, tour à tour, ``outcomes`` (une exception est levée) puis lit pour de bon ; rend le compteur d'appels."""
    real = Path.read_bytes
    calls = {"n": 0}

    def fake(self):
        if self.name.endswith(".kut.lock") and calls["n"] < len(outcomes):
            outcome = outcomes[calls["n"]]
            calls["n"] += 1
            raise outcome
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", fake)
    return calls


def test_a_transient_permission_error_reading_the_lock_is_retried(
    project_file, monkeypatch
):
    """Windows : lire le verrou pendant qu'un autre le supprime ou le remplace lève PermissionError ; on réévalue, on ne plante pas."""
    _write_owner(project_file, pid=OTHER)
    calls = _flaky_read(monkeypatch, [PermissionError(13, "Accès refusé")] * 3)
    lock = ProjectLock(project_file, ops=FakeOps({ME: "me"}))
    stale = lock.acquire()
    assert calls["n"] == 3 and stale is not None and stale.pid == OTHER


def test_a_lock_that_vanishes_while_unreadable_is_simply_taken(
    project_file, monkeypatch
):
    _write_owner(project_file, pid=OTHER)
    real = Path.read_bytes
    state = {"done": False}

    def fake(self):
        if self.name.endswith(".kut.lock") and not state["done"]:
            state["done"] = True
            self.unlink()  # l'autre processus l'a supprimé pendant notre lecture
            raise PermissionError(13, "suppression en attente")
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", fake)
    lock = ProjectLock(project_file, ops=FakeOps({ME: "me"}))
    assert lock.acquire() is None
    owner = read_lock(project_file)
    assert owner is not None and owner.pid == ME


def test_a_persistent_permission_error_refuses_cleanly_and_does_not_loop(
    project_file, monkeypatch
):
    _write_owner(project_file, pid=OTHER)
    monkeypatch.setattr("core.project_lock._TRANSIENT_READ_SECONDS", 0.05)
    calls = _flaky_read(monkeypatch, [PermissionError(13, "Accès refusé")] * 10_000)
    lock = ProjectLock(project_file, ops=FakeOps({ME: "me"}))
    started = time.monotonic()
    with pytest.raises(ProjectLockedError) as raised:
        lock.acquire()
    assert (
        raised.value.verdict is LockVerdict.UNKNOWN
        and time.monotonic() - started < 2.0
        and calls["n"] < 10_000
    )
    assert lock_file_for(
        project_file
    ).exists()  # le verrou de l'autre n'a pas été touché


def test_release_survives_a_persistent_permission_error(project_file, monkeypatch):
    lock = ProjectLock(project_file, ops=FakeOps({ME: "me"}))
    lock.acquire()
    monkeypatch.setattr("core.project_lock._TRANSIENT_READ_SECONDS", 0.05)
    _flaky_read(monkeypatch, [PermissionError(13, "Accès refusé")] * 10_000)
    lock.release()  # ne lève pas


def test_a_permission_error_creating_next_to_a_pending_delete_is_a_lock_not_a_crash(
    project_file, monkeypatch
):
    """Windows : créer en O_EXCL un fichier dont la suppression est en attente lève PermissionError au lieu de FileExistsError."""
    _write_owner(project_file, pid=OTHER, token="live")
    real_open = os.open

    def fake_open(path, flags, mode=0o777):
        if str(path).endswith(".kut.lock") and flags & os.O_EXCL:
            raise PermissionError(13, "Accès refusé")
        return real_open(path, flags, mode)

    monkeypatch.setattr(os, "open", fake_open)
    lock = ProjectLock(project_file, ops=FakeOps({ME: "me", OTHER: "live"}))
    with pytest.raises(ProjectLockedError):
        lock.acquire()


def test_a_transient_permission_error_creating_is_retried_until_the_name_is_free(
    project_file, monkeypatch
):
    """Windows : le nom en suppression en attente finit par se libérer ; ``exists()`` peut répondre ``False`` entre-temps."""
    real_open = os.open
    calls = {"n": 0}

    def fake_open(path, flags, mode=0o777):
        if str(path).endswith(".kut.lock") and flags & os.O_EXCL:
            calls["n"] += 1
            if calls["n"] <= 3:
                raise PermissionError(13, "Accès refusé")
        return real_open(path, flags, mode)

    monkeypatch.setattr(os, "open", fake_open)
    lock = ProjectLock(project_file, ops=FakeOps({ME: "me"}))
    assert lock.acquire() is None
    assert calls["n"] == 4
    assert read_lock(project_file).pid == ME


def test_a_permission_error_creating_in_a_folder_without_a_lock_stays_an_oserror(
    project_file, monkeypatch
):
    real_open = os.open

    def fake_open(path, flags, mode=0o777):
        if str(path).endswith(".kut.lock") and flags & os.O_EXCL:
            raise PermissionError(13, "Accès refusé")
        return real_open(path, flags, mode)

    monkeypatch.setattr(os, "open", fake_open)
    with pytest.raises(PermissionError):
        ProjectLock(project_file, ops=FakeOps({ME: "me"})).acquire()


def test_a_reused_pid_does_not_pass_for_the_old_owner(project_file):
    """Le PID existe, mais c'est un autre processus (autre heure de début) : le propriétaire est mort, le verrou est périmé."""
    _write_owner(project_file, pid=OTHER, token="ancien")
    lock = ProjectLock(project_file, ops=FakeOps({OTHER: "autre-processus", ME: "me"}))
    assert lock.acquire() is not None
    assert read_lock(project_file).pid == ME


@pytest.mark.parametrize(
    "case, kwargs, ops",
    [
        ("autre machine", {"host": "un-autre-poste"}, FakeOps()),
        ("identité illisible", {}, FakeOps(unknown={OTHER})),
        ("vivant mais jeton inconnu", {"token": ""}, FakeOps({OTHER: "x"})),
    ],
)
def test_when_in_doubt_the_lock_holds(project_file, case, kwargs, ops):
    """Autre machine, sonde illisible, identité non prouvée : on ne reprend jamais un verrou qu'on ne peut pas prouver périmé."""
    _write_owner(project_file, **kwargs)
    with pytest.raises(ProjectLockedError):
        ProjectLock(project_file, ops=ops).acquire()
    assert read_lock(project_file) is not None, case


def test_this_process_can_retake_its_own_lock(project_file):
    first = ProjectLock(project_file, ops=FakeOps({ME: "me"}))
    first.acquire()
    second = ProjectLock(project_file, ops=FakeOps({ME: "me"}))
    assert second.acquire() is None, (
        "le processus qui tient le verrou n'est pas bloqué par lui-même"
    )
    second.release()
    assert not lock_file_for(project_file).exists()


def test_release_removes_only_its_own_file(project_file):
    ours = ProjectLock(project_file, ops=FakeOps({ME: "me"}))
    ours.acquire()
    _write_owner(
        project_file, pid=OTHER, nonce="reprise-par-un-autre"
    )  # un autre l'a repris entre-temps
    ours.release()
    owner = read_lock(project_file)
    assert owner is not None and owner.nonce == "reprise-par-un-autre"


def test_an_unreadable_recent_lock_holds_but_an_old_one_is_broken_and_replaced(
    project_file,
):
    lock_file_for(project_file).write_text("pas du json", encoding="utf-8")
    with pytest.raises(ProjectLockedError) as caught:
        ProjectLock(project_file, ops=FakeOps({ME: "me"})).acquire()
    assert caught.value.owner is None and caught.value.verdict is LockVerdict.UNKNOWN
    old = time.time() - 600
    os.utime(lock_file_for(project_file), (old, old))
    assert ProjectLock(project_file, ops=FakeOps({ME: "me"})).acquire() is None
    assert read_lock(project_file).pid == ME


def test_lock_files_with_wrong_shapes_are_unreadable_not_trusted(project_file):
    for payload in (
        '{"kut_lock": 99}',
        '{"kut_lock": 1, "pid": "x"}',
        '{"kut_lock": 1, "pid": -3}',
        "[]",
        "",
    ):
        lock_file_for(project_file).write_text(payload, encoding="utf-8")
        assert read_lock(project_file) is None, payload


def test_judge_names_the_four_situations():
    ops = FakeOps({OTHER: "t1", ME: "me"})
    owner = LockOwner(OTHER, "t1", "kut", HOST, "app", 0.0, "n")
    assert judge(owner, ops=ops) is LockVerdict.HELD
    assert (
        judge(LockOwner(ME, "me", "kut", HOST, "app", 0.0, "n"), ops=ops)
        is LockVerdict.OURS
    )
    assert (
        judge(LockOwner(7, "t", "kut", HOST, "app", 0.0, "n"), ops=ops)
        is LockVerdict.STALE
    )
    assert (
        judge(LockOwner(OTHER, "t1", "kut", "ailleurs", "app", 0.0, "n"), ops=ops)
        is LockVerdict.UNKNOWN
    )


def test_a_read_only_folder_is_an_oserror_not_a_lock_refusal(tmp_path):
    if sys.platform.startswith("win") or os.geteuid() == 0:
        pytest.skip("droits POSIX non root requis")
    folder = tmp_path / "ro"
    folder.mkdir()
    project = folder / "p.kut"
    project.write_text("{}", encoding="utf-8")
    folder.chmod(0o500)
    try:
        with pytest.raises(OSError):
            ProjectLock(project, ops=FakeOps({ME: "me"})).acquire()
    finally:
        folder.chmod(0o700)


# --- Entre vrais processus ----------------------------------------------------------------------------------------------------

_HOLDER = (
    "import sys, time\n"
    "sys.path.insert(0, sys.argv[2])\n"
    "from core.project_lock import ProjectLock\n"
    "lock = ProjectLock(sys.argv[1], kind='app')\n"
    "lock.acquire()\n"
    "print('ready', flush=True)\n"
    "time.sleep(120)\n"
)


@pytest.fixture
def hold():
    """Fabrique : ``hold(chemin)`` lance un vrai processus Python qui tient le verrou de ce projet (vraie sonde de la plateforme)."""
    processes: list[subprocess.Popen] = []

    def start(path: Path) -> subprocess.Popen:
        process = subprocess.Popen(
            [sys.executable, "-c", _HOLDER, str(path), str(ROOT)],
            stdout=subprocess.PIPE,
            text=True,
            cwd=ROOT,
            env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        )
        processes.append(process)
        assert (
            process.stdout is not None and process.stdout.readline().strip() == "ready"
        )
        return process

    yield start
    for process in processes:
        process.kill()
        process.wait(timeout=10)


def test_a_real_process_holds_the_lock_then_dies_and_the_lock_becomes_stale(
    project_file, hold
):
    foreign_holder = hold(project_file)
    with pytest.raises(ProjectLockedError) as caught:
        ProjectLock(project_file, kind="cli").acquire()
    assert (
        caught.value.owner.pid == foreign_holder.pid
        and caught.value.verdict is LockVerdict.HELD
    )
    foreign_holder.kill()
    foreign_holder.wait(timeout=10)
    lock = ProjectLock(project_file, kind="cli")
    stale = lock.acquire()
    assert stale is not None and stale.pid == foreign_holder.pid, (
        "prouvé disparu : repris, et signalé"
    )
    lock.release()


# --- apply -------------------------------------------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _french(monkeypatch):
    monkeypatch.setenv("KUT_STUDIO_LANG", "fr")
    i18n.reset_for_tests()
    yield
    i18n.reset_for_tests()


@pytest.fixture
def apply_files(tmp_path) -> tuple[Path, Path]:
    project = tmp_path / "p.kut"
    save_project(
        Project(
            "Démo",
            width=64,
            height=36,
            fps=30.0,
            media_assets=[
                MediaAsset(
                    "m", "/x/a.mp4", "a", 10.0, 64, 36, 30.0, "video", has_audio=True
                )
            ],
            tracks=[Track("V1", "V1", "video"), Track("A1", "A1", "audio")],
        ),
        str(project),
    )
    script = tmp_path / "s.json"
    script.write_text(
        json.dumps({"kut_script": 1, "ops": [{"op": "add_bus", "id": "voix"}]}),
        encoding="utf-8",
    )
    return script, project


def _run(*argv: str, capsys) -> tuple[int, str, str]:
    code = cli.run(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_apply_in_place_refuses_a_project_the_application_holds(
    apply_files, hold, capsys
):
    script, project = apply_files
    foreign_holder = hold(project)
    before = project.read_bytes()
    code, stdout, err = _run(
        "apply", str(script), str(project), "--in-place", "--json", capsys=capsys
    )
    data = json.loads(stdout)
    assert code == cli.EXIT_FAILED and data["error"]["kind"] == "project_locked"
    assert (
        data["error"]["owner"]["pid"] == foreign_holder.pid
        and data["error"]["owner"]["kind"] == "app"
    )
    assert (
        data["error"]["lock_file"].endswith("p.kut.lock")
        and "supprimez le fichier" in err
    )
    assert project.read_bytes() == before, "le projet de la fenêtre n'a pas été touché"
    assert [
        path.name for path in project.parent.iterdir() if path.name.startswith(".")
    ] == [], "aucun temporaire laissé"


def test_apply_overwrite_onto_a_held_project_is_refused_too(
    apply_files, hold, tmp_path, capsys
):
    script, project = apply_files
    other = tmp_path / "copie.kut"
    other.write_bytes(project.read_bytes())
    hold(other)
    code, stdout, _err = _run(
        "apply",
        str(script),
        str(project),
        "-o",
        str(other),
        "--overwrite",
        "--json",
        capsys=capsys,
    )
    assert (
        code == cli.EXIT_FAILED
        and json.loads(stdout)["error"]["kind"] == "project_locked"
    )
    assert other.read_bytes() == project.read_bytes(), (
        "la copie tenue n'a pas été réécrite"
    )


def test_apply_dry_run_and_a_new_output_file_ignore_the_lock(
    apply_files, hold, tmp_path, capsys
):
    script, project = apply_files
    hold(project)
    code, _out, _err = _run(
        "apply", str(script), str(project), "--dry-run", capsys=capsys
    )
    assert code == 0, "un essai à blanc n'écrit rien : le verrou ne le concerne pas"
    new = tmp_path / "nouveau.kut"
    code, _out, _err = _run(
        "apply", str(script), str(project), "-o", str(new), capsys=capsys
    )
    assert code == 0 and new.exists(), (
        "lire un projet tenu par la fenêtre est permis ; on écrit ailleurs"
    )


def test_apply_takes_a_stale_lock_and_says_so_then_leaves_no_lock_behind(
    apply_files, hold, capsys
):
    script, project = apply_files
    foreign_holder = hold(project)
    foreign_holder.kill()
    foreign_holder.wait(timeout=10)
    code, _out, err = _run(
        "apply", str(script), str(project), "--in-place", capsys=capsys
    )
    assert code == 0 and "Verrou périmé remplacé" in err
    assert [bus.id for bus in load_project(str(project)).buses] == ["voix"]
    assert not lock_file_for(project).exists(), "le verrou du CLI est relâché"


def test_apply_releases_its_lock_even_when_the_script_is_refused(apply_files, capsys):
    script, project = apply_files
    script.write_text(
        json.dumps({"kut_script": 1, "ops": [{"op": "remove_bus", "bus": "absent"}]}),
        encoding="utf-8",
    )
    code, _out, _err = _run(
        "apply", str(script), str(project), "--in-place", capsys=capsys
    )
    assert code == cli.EXIT_FAILED and not lock_file_for(project).exists()


def test_apply_takes_the_lock_before_reading_the_project(
    apply_files, monkeypatch, capsys
):
    """Le verrou précède la lecture : si la fenêtre enregistre entre la lecture et l'écriture, le script réécrirait une version périmée."""
    script, project = apply_files
    order: list[str] = []
    real_acquire = ProjectLock.acquire
    real_load = cli._load

    def spy_acquire(self):
        order.append("verrou")
        return real_acquire(self)

    def spy_load(*args, **kwargs):
        order.append("lecture")
        return real_load(*args, **kwargs)

    monkeypatch.setattr(ProjectLock, "acquire", spy_acquire)
    monkeypatch.setattr(cli, "_load", spy_load)
    code, _out, _err = _run(
        "apply", str(script), str(project), "--in-place", capsys=capsys
    )
    assert code == 0 and order == ["verrou", "lecture"]


def test_apply_lock_messages_are_translated(apply_files, hold, capsys):
    script, project = apply_files
    hold(project)
    _code, _out, err = _run(
        "apply", str(script), str(project), "--in-place", "--lang", "en", capsys=capsys
    )
    assert "Project locked" in err and "delete the file" in err
    _code, _out, err = _run(
        "apply", str(script), str(project), "--in-place", "--lang", "es", capsys=capsys
    )
    assert "Proyecto bloqueado" in err and "elimine el archivo" in err


# --- La fenêtre --------------------------------------------------------------------------------------------------------------


@pytest.fixture
def window(qtbot, monkeypatch, tmp_path):
    dialogs = install_dialogs(monkeypatch)
    win = build_window(qtbot, monkeypatch, tmp_path / "config")
    win.dialogs = dialogs
    return win


def _saved(tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    save_project(
        Project(
            name, width=64, height=36, fps=30.0, tracks=[Track("V1", "V1", "video")]
        ),
        str(path),
    )
    return path


def test_opening_a_project_locks_it_and_closing_or_switching_releases_it(
    window, tmp_path
):
    first, second = _saved(tmp_path, "a.kut"), _saved(tmp_path, "b.kut")
    window._load_project_from_path(str(first))
    assert (
        read_lock(first) is not None
        and read_lock(first).kind == "app"
        and read_lock(first).pid == ME
    )
    window._load_project_from_path(str(second))
    assert not lock_file_for(first).exists() and read_lock(second) is not None, (
        "changer de projet relâche l'ancien"
    )
    window._install_new_project(Project("Neuf", width=64, height=36, fps=30.0))
    assert not lock_file_for(second).exists(), (
        "un nouveau projet n'est pas encore un fichier"
    )
    window._load_project_from_path(str(first))
    steps = dict(window._shutdown_steps())
    steps["verrou du projet"]()
    assert not lock_file_for(first).exists(), "la fermeture relâche le verrou"


def test_saving_as_moves_the_lock_to_the_new_file(window, tmp_path, monkeypatch):
    first = _saved(tmp_path, "a.kut")
    window._load_project_from_path(str(first))
    target = tmp_path / "copie.kut"
    from PySide6.QtWidgets import QFileDialog

    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", lambda *_a, **_k: (str(target), "")
    )
    window.save_project_as()
    assert not lock_file_for(first).exists() and read_lock(target) is not None


def test_a_project_held_by_another_program_still_opens_but_the_user_is_warned(
    window, tmp_path, hold
):
    held = tmp_path / "tenu.kut"
    save_project(
        Project(
            "Tenu", width=64, height=36, fps=30.0, tracks=[Track("V1", "V1", "video")]
        ),
        str(held),
    )
    foreign_holder = hold(held)
    window._load_project_from_path(str(held))
    assert window.current_project_path == str(held), "l'ouverture n'est jamais refusée"
    warnings = window.dialogs.of_kind("warning")
    assert (
        len(warnings) == 1
        and str(foreign_holder.pid) in warnings[0][1]
        and "supprimez le fichier" in warnings[0][1]
    )
    assert read_lock(held).pid == foreign_holder.pid, (
        "le verrou de l'autre est resté intact"
    )


# --- Revue Codex : horodatages, reprise concurrente, verrou avant lecture, liens symboliques ---------------------------------


@pytest.mark.parametrize(
    "started", ["NaN", "Infinity", "-Infinity", "-5", "1e30", "true", '"hier"']
)
def test_a_lock_with_an_absurd_start_time_is_unreadable_not_trusted(
    project_file, started
):
    """Le décodeur JSON de Python accepte NaN et Infinity ; ``time.localtime`` lèverait ensuite au lieu de refuser proprement."""
    lock_file_for(project_file).write_text(
        '{"kut_lock": 1, "pid": %d, "token": "t", "name": "kut", "host": "%s", "kind": "app", "started": %s, "nonce": "n"}'
        % (OTHER, HOST, started),
        encoding="utf-8",
    )
    assert read_lock(project_file) is None
    with pytest.raises(ProjectLockedError) as caught:
        ProjectLock(project_file, ops=FakeOps({ME: "me"})).acquire()
    assert caught.value.owner is None, (
        "récent et illisible : le verrou tient, sans exception de date"
    )


def test_a_takeover_that_loses_the_race_does_not_remove_the_winners_live_lock(
    project_file, monkeypatch
):
    """Revue Codex : A compare, est suspendu ; B retire le verrou périmé et pose le sien ; A reprend et retirait ce verrou VIVANT."""
    _write_owner(project_file, pid=OTHER, token="mort")
    ops = FakeOps({ME: "me", 4_000_002: "vivant"})
    original_guard = ProjectLock._reclaim_guard
    interleaved: list[str] = []

    def guard_after_b_won(self):
        # B passe avant que A prenne la garde : retire le verrou périmé et pose le sien (un vrai propriétaire vivant).
        lock_file_for(project_file).unlink()
        _write_owner(project_file, pid=4_000_002, token="vivant", nonce="b-gagne")
        interleaved.append("B a gagné")
        return original_guard(self)

    monkeypatch.setattr(ProjectLock, "_reclaim_guard", guard_after_b_won)
    with pytest.raises(ProjectLockedError) as caught:
        ProjectLock(project_file, ops=ops).acquire()
    assert interleaved == ["B a gagné"] and caught.value.owner.nonce == "b-gagne"
    survivor = read_lock(project_file)
    assert survivor is not None and survivor.nonce == "b-gagne", (
        "le verrou vivant de B est intact"
    )


def test_the_reclaim_guard_is_released_and_a_stuck_one_is_never_stolen(
    project_file, monkeypatch
):
    _write_owner(project_file, pid=OTHER)
    guard = lock_file_for(project_file).with_name(
        lock_file_for(project_file).name + ".reclaim"
    )
    assert ProjectLock(project_file, ops=FakeOps({ME: "me"})).acquire() is not None
    assert not guard.exists(), "la garde ne survit pas à la reprise"
    lock_file_for(project_file).unlink()
    _write_owner(project_file, pid=OTHER)
    guard.write_text("", encoding="utf-8")
    monkeypatch.setattr("core.project_lock._GUARD_WAIT_SECONDS", 0.1)
    with pytest.raises(ProjectLockedError) as caught:
        ProjectLock(project_file, ops=FakeOps({ME: "me"})).acquire()
    assert caught.value.lock_file == guard, "le message désigne la garde à supprimer"
    assert guard.exists() and read_lock(project_file).pid == OTHER, (
        "ni la garde ni le verrou périmé n'ont été touchés"
    )


def _guard_permission_errors(monkeypatch, project_file, times):
    real_open = os.open
    calls = {"n": 0}

    def fake_open(path, flags, mode=0o777):
        if str(path).endswith(".kut.lock.reclaim") and flags & os.O_EXCL:
            calls["n"] += 1
            if calls["n"] <= times:
                raise PermissionError(13, "Accès refusé")
        return real_open(path, flags, mode)

    monkeypatch.setattr(os, "open", fake_open)
    return calls


def test_a_permission_error_on_a_guard_pending_delete_is_waited_out_not_a_crash(
    project_file, monkeypatch
):
    """Windows : créer la garde de reprise pendant que l'autre la supprime lève PermissionError ; on attend, on ne plante pas."""
    _write_owner(project_file, pid=OTHER)
    monkeypatch.setattr("core.project_lock.time.sleep", lambda _s: None)
    calls = _guard_permission_errors(monkeypatch, project_file, 3)
    stale = ProjectLock(project_file, ops=FakeOps({ME: "me"})).acquire()
    assert calls["n"] == 4 and stale is not None and stale.pid == OTHER
    assert read_lock(project_file).pid == ME


def _persistent_guard_refusal(monkeypatch, project_file, *, windows, guard_on_disk):
    _write_owner(project_file, pid=OTHER)
    guard = lock_file_for(project_file).with_name(
        lock_file_for(project_file).name + ".reclaim"
    )
    if guard_on_disk:
        guard.write_text("", encoding="utf-8")
    monkeypatch.setattr("core.project_lock._PERMISSION_MEANS_PENDING_DELETE", windows)
    monkeypatch.setattr("core.project_lock._GUARD_WAIT_SECONDS", 0.0)
    monkeypatch.setattr("core.project_lock.time.sleep", lambda _s: None)
    _guard_permission_errors(monkeypatch, project_file, 10_000)
    return guard


def test_windows_a_guard_that_keeps_refusing_while_absent_is_a_locked_project(
    project_file, monkeypatch
):
    """``exists()`` peut mentir sous Windows : une garde absente du disque qui refuse encore est une suppression en attente."""
    guard = _persistent_guard_refusal(
        monkeypatch, project_file, windows=True, guard_on_disk=False
    )
    with pytest.raises(ProjectLockedError) as caught:
        ProjectLock(project_file, ops=FakeOps({ME: "me"})).acquire()
    assert caught.value.lock_file == guard and read_lock(project_file).pid == OTHER


def test_windows_a_guard_that_keeps_refusing_while_present_is_a_locked_project(
    project_file, monkeypatch
):
    guard = _persistent_guard_refusal(
        monkeypatch, project_file, windows=True, guard_on_disk=True
    )
    with pytest.raises(ProjectLockedError) as caught:
        ProjectLock(project_file, ops=FakeOps({ME: "me"})).acquire()
    assert caught.value.lock_file == guard and read_lock(project_file).pid == OTHER


def test_posix_a_permission_error_on_the_guard_is_a_real_permission_error(
    project_file, monkeypatch
):
    _persistent_guard_refusal(
        monkeypatch, project_file, windows=False, guard_on_disk=False
    )
    with pytest.raises(PermissionError):
        ProjectLock(project_file, ops=FakeOps({ME: "me"})).acquire()


_CONTENDER = (
    "import os, sys, time\n"
    "sys.path.insert(0, sys.argv[3])\n"
    "from core.project_lock import ProjectLock, ProjectLockedError\n"
    "while not os.path.exists(sys.argv[2]):\n"
    "    time.sleep(0.005)\n"
    "lock = ProjectLock(sys.argv[1], kind='cli')\n"
    "try:\n"
    "    lock.acquire()\n"
    "    print('won', flush=True)\n"
    "    time.sleep(60)\n"
    "except ProjectLockedError:\n"
    "    print('locked', flush=True)\n"
)


def test_six_real_processes_racing_for_one_stale_lock_give_exactly_one_winner(
    project_file, tmp_path
):
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait(timeout=30)
    _write_owner(
        project_file, pid=dead.pid, token="trop-ancien"
    )  # un propriétaire réellement disparu
    start = tmp_path / "go"
    children = [
        subprocess.Popen(
            [
                sys.executable,
                "-c",
                _CONTENDER,
                str(project_file),
                str(start),
                str(ROOT),
            ],
            stdout=subprocess.PIPE,
            text=True,
            cwd=ROOT,
        )
        for _ in range(6)
    ]
    try:
        time.sleep(1.0)  # tous importent, puis attendent le départ
        start.write_text("go", encoding="utf-8")
        outcomes = [child.stdout.readline().strip() for child in children]
        assert sorted(outcomes) == ["locked"] * 5 + ["won"], outcomes
        winner = children[outcomes.index("won")]
        assert read_lock(project_file).pid == winner.pid, (
            "le verrou posé est celui du gagnant"
        )
    finally:
        for child in children:
            child.kill()
            child.wait(timeout=10)


def test_the_window_takes_the_lock_before_it_first_reads_the_project(
    window, tmp_path, monkeypatch
):
    """Revue Codex : lu d'abord, verrouillé ensuite, un script pouvait écrire entre les deux ; la fenêtre installait alors un instantané périmé."""
    path = _saved(tmp_path, "a.kut")
    seen: dict[str, bool] = {}
    real_load = load_project

    def spy(target, *args, **kwargs):
        seen.setdefault("locked_when_read", lock_file_for(path).exists())
        return real_load(target, *args, **kwargs)

    monkeypatch.setattr("ui.main_window_mixins.project_files.load_project", spy)
    window._load_project_from_path(str(path))
    assert seen["locked_when_read"] is True


def test_a_project_that_fails_to_open_leaves_no_lock_and_keeps_the_open_projects_lock(
    window, tmp_path
):
    good, other = _saved(tmp_path, "a.kut"), _saved(tmp_path, "b.kut")
    broken = tmp_path / "casse.kut"
    broken.write_text("pas du json", encoding="utf-8")
    window._load_project_from_path(str(good))
    window._load_project_from_path(str(broken))
    assert window.dialogs.of_kind("critical"), "l'erreur est présentée"
    assert not lock_file_for(broken).exists(), (
        "le verrou pris pour le projet illisible est relâché"
    )
    assert window.current_project_path == str(good) and read_lock(good).pid == ME, (
        "le projet resté ouvert garde son verrou"
    )
    window._load_project_from_path(
        str(good)
    )  # le même projet, rouvert : toujours verrouillé
    assert read_lock(good) is not None
    window._load_project_from_path(str(other))
    assert not lock_file_for(good).exists() and read_lock(other) is not None


def test_a_failed_reopen_of_the_open_project_does_not_drop_its_lock(window, tmp_path):
    path = _saved(tmp_path, "a.kut")
    window._load_project_from_path(str(path))
    path.write_text("abîmé", encoding="utf-8")
    window._load_project_from_path(str(path))
    assert read_lock(path) is not None and read_lock(path).pid == ME


def test_atomic_writes_go_through_a_symlink_instead_of_replacing_it(tmp_path):
    """Revue Codex : le renommage final remplaçait le lien par un fichier ordinaire ; le projet visé (et son verrou) ne bougeait plus."""
    if sys.platform.startswith("win"):
        pytest.skip("liens symboliques : droits particuliers sous Windows")
    from core.atomic_io import atomic_write_text

    real = tmp_path / "vrai.txt"
    real.write_text("avant", encoding="utf-8")
    link = tmp_path / "lien.txt"
    link.symlink_to(real)
    atomic_write_text(link, "après")
    assert (
        link.is_symlink()
        and real.read_text(encoding="utf-8") == "après"
        and os.path.realpath(link) == str(real.resolve())
    )


def test_saving_a_project_opened_through_a_symlink_keeps_the_link_and_the_lock_aligned(
    window, tmp_path
):
    if sys.platform.startswith("win"):
        pytest.skip("liens symboliques : droits particuliers sous Windows")
    real = _saved(tmp_path, "vrai.kut")
    link = tmp_path / "lien.kut"
    link.symlink_to(real)
    window._load_project_from_path(str(link))
    window.project.name = "Renommé"
    window.save_project_file()
    assert link.is_symlink(), "le lien est resté un lien"
    assert load_project(str(real)).name == "Renommé", (
        "c'est le projet visé qui a changé"
    )
    assert read_lock(real) is not None and lock_file_for(link) == lock_file_for(real)


def test_exists_raising_permission_error_counts_as_a_lock_being_deleted(
    tmp_path, monkeypatch
) -> None:
    """Sous Windows ``Path.exists()`` lève ``PermissionError`` sur un fichier en suppression : c'est un verrou présent, pas un plantage."""
    lock = ProjectLock(tmp_path / "demo.kut.lock")

    def raising(self: Path) -> bool:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "exists", raising)
    assert lock._lock_present() is True
