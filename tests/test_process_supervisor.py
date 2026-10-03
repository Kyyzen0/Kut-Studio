"""Supervision des processus enfants : aucun FFmpeg ne survit à Kut-Studio, même tué brutalement.

Deux familles de tests :

* **simulés** : fausse plateforme (:class:`FakeOps`, faux objet Job) injectée dans le vrai code ; arrêt du parent
  simulé par la fermeture du tube du gardien ; rapides et déterministes ;
* **réels** : un parent jetable (``tests/fixtures/supervised_parent.py``) lance de vrais enfants puis est tué par
  ``SIGKILL`` / ``TerminateProcess`` ; on attend (échéance généreuse) que ses enfants aient disparu. La mort d'un
  enfant est vérifiée par son **identité** (PID + heure de début) : un zombie POSIX répond encore à ``kill(pid, 0)``.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import pytest

from core import process_supervisor
from core.process_platform import (
    ERROR_ACCESS_DENIED,
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    KillOnCloseJob,
    KillOutcome,
    LinuxOps,
    Probe,
    ProbeState,
    ProcessIdentity,
    kill_on_close_limits,
    kill_refusal,
    native_ops,
    normalized_name,
    same_process,
)
from core.process_supervisor import (
    InstanceRegistry,
    ProcessSupervisor,
    run_reaper,
    sweep_dead_instances,
    sweep_instance,
)

ROOT = Path(__file__).resolve().parents[1]
PARENT = ROOT / "tests" / "fixtures" / "supervised_parent.py"
SLEEP = [sys.executable, "-c", "import time; time.sleep(300)"]
DEADLINE = 30.0
POSIX = not sys.platform.startswith("win")


# --- Fausse plateforme ------------------------------------------------------------------------------------------------


class FakeOps:
    """Table de processus en mémoire ; ``kill_if_same`` applique la **vraie** règle de décision."""

    def __init__(self) -> None:
        self.table: dict[int, ProcessIdentity] = {}
        self.unreadable: set[int] = set()
        self.killed: list[int] = []

    def spawn(self, pid: int, token: str = "t0", name: str = "ffmpeg") -> ProcessIdentity:
        identity = ProcessIdentity(pid, token, name)
        self.table[pid] = identity
        return identity

    def probe(self, pid: int) -> Probe:
        if pid in self.unreadable:
            return Probe(ProbeState.UNKNOWN)
        identity = self.table.get(pid)
        return Probe(ProbeState.ALIVE, identity) if identity else Probe(ProbeState.GONE)

    def kill_if_same(self, expected: ProcessIdentity) -> KillOutcome:
        refusal = kill_refusal(expected, self.probe(expected.pid))
        if refusal is not None:
            return refusal
        del self.table[expected.pid]
        self.killed.append(expected.pid)
        return KillOutcome.KILLED


def _registry(root: Path, owner: ProcessIdentity | None, *children: ProcessIdentity) -> InstanceRegistry:
    registry = InstanceRegistry.create(root, f"instance-{uuid.uuid4().hex[:8]}", owner)
    for child in children:
        registry.add(child)
    return registry


# --- Règles d'identité ------------------------------------------------------------------------------------------------


def test_names_are_compared_without_case_version_or_extension():
    # Le Python « framework » de macOS se ré-exécute : python3.14 devient Python, même PID, même heure de début.
    assert normalized_name("python3.14") == normalized_name("Python") == "python"
    assert normalized_name(r"C:\ffmpeg\bin\FFmpeg.exe") == normalized_name("/opt/bin/ffmpeg") == "ffmpeg"
    assert normalized_name("ffmpeg") != normalized_name("ffprobe")


def test_a_reused_pid_or_another_program_is_not_the_same_process():
    original = ProcessIdentity(42, "100.000001", "ffmpeg")
    assert same_process(original, ProcessIdentity(42, "100.000001", "ffmpeg"))
    assert not same_process(original, ProcessIdentity(42, "100.000002", "ffmpeg"))  # PID réutilisé
    assert not same_process(original, ProcessIdentity(42, "100.000001", "bash"))
    assert not same_process(ProcessIdentity(42, "", "ffmpeg"), ProcessIdentity(42, "", "ffmpeg"))  # jeton inconnu


def test_linux_identity_is_read_from_proc_stat(tmp_path):
    proc = tmp_path / "proc"
    (proc / "sys" / "kernel" / "random").mkdir(parents=True)
    (proc / "sys" / "kernel" / "random" / "boot_id").write_text("1b2c-3d4e\n")
    fields = ["S"] + ["0"] * 18 + ["987654"] + ["0"] * 20  # champ 3 = état, champ 22 = heure de début
    (proc / "77").mkdir()
    (proc / "77" / "stat").write_bytes(b"77 (my (odd) name) " + " ".join(fields).encode())
    (proc / "78").mkdir()
    (proc / "78" / "stat").write_bytes(b"78 (ffmpeg) " + " ".join(["Z"] + fields[1:]).encode())
    ops = LinuxOps(proc)
    probe = ops.probe(77)
    assert probe.state is ProbeState.ALIVE
    assert probe.identity == ProcessIdentity(77, "1b2c3d4e-987654", "my (odd) name")
    assert ops.probe(78).state is ProbeState.GONE  # zombie : terminé, pas encore récolté
    assert ops.probe(79).state is ProbeState.GONE


# --- Registres et balayage (simulés) ---------------------------------------------------------------------------------


def test_reaper_kills_only_verified_children_when_the_parent_pipe_closes(tmp_path):
    ops = FakeOps()
    first, second = ops.spawn(101, "a"), ops.spawn(102, "b")
    reused = ProcessIdentity(103, "old", "ffmpeg")
    ops.spawn(103, "new")  # PID réutilisé par un autre processus depuis l'enregistrement
    registry = _registry(tmp_path, ops.spawn(100, "owner", "kut-studio"), first, second, reused)
    group_killed = threading.Event()
    read_end, write_end = os.pipe()
    reaper = threading.Thread(target=run_reaper, args=(registry.directory,),
                              kwargs={"stdin_fd": read_end, "ops": ops, "kill_own_group": group_killed.set})
    reaper.start()
    try:
        os.write(write_end, b"le parent vit encore")
        reaper.join(timeout=0.2)
        assert reaper.is_alive() and ops.killed == [], "le gardien ne fait rien tant que le tube est ouvert"
    finally:
        os.close(write_end)  # mort du parent : le noyau ferme le bout d'écriture
    reaper.join(timeout=DEADLINE)
    os.close(read_end)
    assert not reaper.is_alive()
    assert sorted(ops.killed) == [101, 102]
    assert 103 in ops.table, "un PID réutilisé (autre jeton) n'est jamais tué"
    assert group_killed.is_set() and not registry.directory.exists()


def test_startup_sweep_leaves_a_living_instance_untouched(tmp_path):
    ops = FakeOps()
    child = ops.spawn(201)
    registry = _registry(tmp_path, ops.spawn(200, "owner", "kut-studio"), child)
    report = sweep_dead_instances(tmp_path, ops=ops)
    assert ops.killed == [] and report.kept == 1
    assert registry.children() == [child]


def test_startup_sweep_kills_verified_children_of_a_dead_instance(tmp_path):
    ops = FakeOps()
    orphan, reused = ops.spawn(301, "a"), ProcessIdentity(302, "old", "ffmpeg")
    ops.spawn(302, "new")
    dead = _registry(tmp_path, ProcessIdentity(300, "owner", "kut-studio"), orphan, reused)  # 300 : disparu
    report = sweep_dead_instances(tmp_path, ops=ops)
    assert ops.killed == [301] and report.killed == [orphan]
    assert report.spared == [(reused, KillOutcome.NOT_OURS)]
    assert not dead.directory.exists() and report.removed == 1


def test_an_owner_pid_reused_by_another_process_counts_as_dead(tmp_path):
    ops = FakeOps()
    ops.spawn(400, "someone-else", "bash")
    orphan = ops.spawn(401)
    _registry(tmp_path, ProcessIdentity(400, "kut-start", "kut-studio"), orphan)
    sweep_dead_instances(tmp_path, ops=ops)
    assert ops.killed == [401]


def test_nothing_is_killed_when_identities_cannot_be_read(tmp_path):
    ops = FakeOps()
    owner, child = ops.spawn(500, "owner", "kut-studio"), ops.spawn(501)
    ops.unreadable.update({500, 501})
    registry = _registry(tmp_path, owner, child)
    assert sweep_instance(registry.directory, ops).kept == 1  # propriétaire illisible : peut-être vivant
    report = sweep_instance(registry.directory, ops, require_dead_owner=False)
    assert ops.killed == [] and report.spared == [(child, KillOutcome.UNVERIFIABLE)]


# --- Objet Job Windows (API simulée) ---------------------------------------------------------------------------------


class FakeJobApi:
    def __init__(self, *, create=True, limits=True, assign_error=0):
        self.create, self.limits, self.assign_error = create, limits, assign_error
        self.calls: list[tuple] = []
        self.open_handles: set[int] = set()
        self._next = 1000
        self._error = 0

    def _handle(self) -> int:
        self._next += 1
        self.open_handles.add(self._next)
        return self._next

    def last_error(self):
        return self._error

    def create_job(self):
        if not self.create:
            self._error = ERROR_ACCESS_DENIED
            return None
        return self._handle()

    def set_kill_on_close(self, job):
        self.calls.append(("limits", job))
        return self.limits

    def open_process(self, pid, access):
        return self._handle()

    def assign_to_job(self, job, process):
        self.calls.append(("assign", job, process))
        self._error = self.assign_error
        return not self.assign_error

    def close(self, handle):
        self.open_handles.discard(handle)


def test_job_object_kills_children_on_close_and_never_leaks_process_handles():
    import ctypes

    limits = kill_on_close_limits()
    assert limits.BasicLimitInformation.LimitFlags == JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if ctypes.sizeof(ctypes.c_void_p) == 8:
        assert ctypes.sizeof(limits) == 144  # sizeof(JOBOBJECT_EXTENDED_LIMIT_INFORMATION) en 64 bits
    api = FakeJobApi()
    job = KillOnCloseJob(api)
    assert job.assign(11) and job.assign(12) and job.active
    assert [call[0] for call in api.calls] == ["limits", "assign", "assign"]
    assert len(api.open_handles) == 1, "seule la poignée du job reste ouverte"
    job.close()
    assert not api.open_handles and not job.active


def test_refused_job_object_falls_back_with_a_logged_warning(tmp_path, caplog):
    caplog.set_level(logging.WARNING, logger="kut_studio.process")
    job = KillOnCloseJob(FakeJobApi(create=False))
    supervisor = ProcessSupervisor(tmp_path, use_reaper=False, job=job)
    try:
        completed = supervisor.run([sys.executable, "-c", "print('lancé')"], capture_output=True, text=True,
                                   timeout=DEADLINE)
        assert completed.returncode == 0 and completed.stdout.strip() == "lancé"
        supervisor.run([sys.executable, "-c", "pass"], timeout=DEADLINE)
    finally:
        supervisor.close()
    warnings = [record.getMessage() for record in caplog.records if "objet Job refusé" in record.getMessage()]
    assert len(warnings) == 1, "journalisé une fois, jamais une exception"


def test_assignment_refused_inside_a_foreign_job_is_logged_and_the_child_still_runs(tmp_path, caplog):
    caplog.set_level(logging.WARNING, logger="kut_studio.process")
    supervisor = ProcessSupervisor(tmp_path, use_reaper=False,
                                   job=KillOnCloseJob(FakeJobApi(assign_error=ERROR_ACCESS_DENIED)))
    try:
        with supervisor.supervised(SLEEP) as process:
            assert process.poll() is None
            assert [r.identity.pid for r in supervisor.live_registrations()] == [process.pid]
    finally:
        supervisor.close()
    assert any("sans imbrication" in record.getMessage() for record in caplog.records)


def test_an_unwritable_registry_never_prevents_a_launch(tmp_path, caplog):
    caplog.set_level(logging.WARNING, logger="kut_studio.process")
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("")
    supervisor = ProcessSupervisor(blocker, use_reaper=False, use_job=False)
    try:
        assert supervisor.run([sys.executable, "-c", "pass"], timeout=DEADLINE).returncode == 0
    finally:
        supervisor.close()
    assert any("registre des processus indisponible" in record.getMessage() for record in caplog.records)


# --- Lancements réels ------------------------------------------------------------------------------------------------


def _running(identity: ProcessIdentity) -> bool:
    probe = native_ops().probe(identity.pid)
    return probe.state is ProbeState.ALIVE and probe.identity is not None and same_process(identity, probe.identity)


def _wait_until(condition, timeout: float = DEADLINE) -> bool:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.02)
    return True


def test_run_matches_subprocess_run_and_forgets_finished_children(tmp_path):
    supervisor = ProcessSupervisor(tmp_path)
    try:
        echoed = supervisor.run([sys.executable, "-c", "import sys; print(sys.stdin.read() == '')"],
                                capture_output=True, text=True, timeout=DEADLINE)
        assert echoed.stdout.strip() == "True", "stdin est fermé par défaut : FFmpeg ne lit jamais le terminal"
        with pytest.raises(subprocess.CalledProcessError):
            supervisor.run([sys.executable, "-c", "raise SystemExit(3)"], check=True, timeout=DEADLINE)
        with pytest.raises(FileNotFoundError):
            supervisor.run([str(tmp_path / "absent-ffmpeg")])
        started = time.monotonic()
        with pytest.raises(subprocess.TimeoutExpired):
            supervisor.run(SLEEP, timeout=0.5)
        assert time.monotonic() - started < DEADLINE
        registry = supervisor.registry_directory
        assert supervisor.live_registrations() == [] and list(registry.glob("*.child")) == []
    finally:
        supervisor.close()
    assert not registry.exists()


def test_native_probe_sees_exited_and_zombie_children_as_gone():
    ops = native_ops()
    me = ops.probe(os.getpid())
    assert me.state is ProbeState.ALIVE and me.identity.token and me.identity.name
    child = subprocess.Popen(SLEEP)
    try:
        identity = ops.probe(child.pid).identity
        assert identity is not None and _running(identity)
        child.kill()
        if not POSIX:
            child.wait()  # Popen garde la poignée ouverte : l'objet processus existe encore, terminé
        assert _wait_until(lambda: ops.probe(child.pid).state is ProbeState.GONE)
        if POSIX:
            os.kill(child.pid, 0)  # zombie : tué mais pas récolté, kill(pid, 0) le voit encore ; la sonde, non
    finally:
        child.kill()
        child.wait()


def test_native_kill_spares_a_pid_whose_identity_differs():
    ops = native_ops()
    child = subprocess.Popen(SLEEP)
    try:
        identity = ops.probe(child.pid).identity
        impostor = ProcessIdentity(identity.pid, identity.token + "1", identity.name)
        assert ops.kill_if_same(impostor) is KillOutcome.NOT_OURS
        assert child.poll() is None and _running(identity)
        assert ops.kill_if_same(identity) is KillOutcome.KILLED
        assert child.wait(timeout=DEADLINE) != 0
    finally:
        child.kill()
        child.wait()


class _Parent:
    """Un parent jetable qui supervise de vrais enfants."""

    def __init__(self, tmp_path: Path, *options: str) -> None:
        self.output = tmp_path / f"parent-{uuid.uuid4().hex[:8]}.json"
        env = dict(os.environ, KUT_STUDIO_LOG_DIR=str(tmp_path / "logs"))
        self.process = subprocess.Popen(
            [sys.executable, str(PARENT), str(tmp_path / "registries"), str(self.output), *options],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env=env,
        )
        ready = _wait_until(lambda: self.output.exists() or self.process.poll() is not None, timeout=60)
        if not ready or not self.output.exists():
            self.process.kill()
            _out, errors = self.process.communicate()
            pytest.fail(f"le parent n'a pas démarré (code {self.process.returncode}) : {errors.decode()[-2000:]}")
        report = json.loads(self.output.read_text(encoding="utf-8"))
        self.children = [ProcessIdentity(c["pid"], c["token"], c["name"]) for c in report["children"]]
        reaper = report["reaper"]
        self.reaper = ProcessIdentity(reaper["pid"], reaper["token"], reaper["name"]) if reaper else None
        self.registry = Path(report["registry"])

    def kill(self) -> None:
        """Arrêt brutal : SIGKILL (POSIX) ou TerminateProcess (Windows), jamais de code de fermeture."""
        self.process.kill()
        self.process.wait(timeout=DEADLINE)

    def close(self) -> int:
        _out, errors = self.process.communicate(timeout=DEADLINE)
        return self.process.returncode

    def cleanup(self) -> None:
        if self.process.poll() is None:
            self.process.kill()
            self.process.wait()
        for child in self.children:  # un échec du test ne doit pas laisser d'orphelin sur la machine
            native_ops().kill_if_same(child)


@pytest.fixture
def parents(tmp_path):
    created: list[_Parent] = []

    def make(*options: str) -> _Parent:
        parent = _Parent(tmp_path, *options)
        created.append(parent)
        return parent

    yield make
    for parent in created:
        parent.cleanup()


def _assert_guard_done(parent: _Parent, registries: Path) -> None:
    """Après la mort du parent : gardien terminé et registre retiré (POSIX), ou registre balayé (Windows)."""
    if parent.reaper is not None:
        assert _wait_until(lambda: not _running(parent.reaper)), "le gardien doit se terminer après son travail"
        assert not parent.registry.exists(), "le gardien retire le registre de son instance"
    else:  # objet Job : le noyau a tué les enfants ; le registre attend le balayage du prochain démarrage
        report = sweep_dead_instances(registries)
        assert report.killed == [] and not parent.registry.exists()


def test_brutally_killed_parent_takes_its_simultaneous_children_with_it(parents, tmp_path):
    parent = parents("--children", "3", "--run")
    assert len(parent.children) == 4 and all(_running(child) for child in parent.children)
    parent.kill()
    assert _wait_until(lambda: not any(_running(child) for child in parent.children)), \
        "des enfants (dont celui d'un run() encore actif) ont survécu à l'arrêt brutal du parent"
    _assert_guard_done(parent, tmp_path / "registries")


def test_brutally_killed_parent_takes_its_preview_render_with_it(parents):
    """Le cas du rapport de stabilisation : un segment d'aperçu fidèle survivait jusqu'à 120 s."""
    parent = parents("--children", "0", "--preview")
    assert len(parent.children) == 1 and _running(parent.children[0])
    parent.kill()
    assert _wait_until(lambda: not _running(parent.children[0])), "le rendu d'aperçu a survécu au parent"


def test_brutally_killed_parent_takes_a_real_ffmpeg_with_it(parents):
    if shutil.which("ffmpeg") is None:
        pytest.skip("FFmpeg absent de cette machine : variante FFmpeg réel non exécutée")
    parent = parents("--ffmpeg", "--children", "2")
    assert all(normalized_name(child.name) == "ffmpeg" for child in parent.children)
    parent.kill()
    assert _wait_until(lambda: not any(_running(child) for child in parent.children))


def test_brutally_killed_parent_takes_its_export_qprocess_with_it(parents):
    parent = parents("--children", "0", "--qprocess")
    assert len(parent.children) == 1 and _running(parent.children[0])
    parent.kill()
    assert _wait_until(lambda: not _running(parent.children[0])), "le FFmpeg de l'export a survécu"


def test_killing_one_instance_spares_the_children_of_another(parents, tmp_path):
    doomed, survivor = parents("--children", "2"), parents("--children", "2")
    doomed.kill()
    assert _wait_until(lambda: not any(_running(child) for child in doomed.children))
    _assert_guard_done(doomed, tmp_path / "registries")  # gardien terminé : il a fini de tuer ce qu'il tuerait
    assert all(_running(child) for child in survivor.children), "les enfants d'une autre instance survivent"
    report = sweep_dead_instances(tmp_path / "registries")
    assert report.killed == [] and survivor.registry.exists()
    assert all(_running(child) for child in survivor.children), "le balayage ne touche pas une instance vivante"
    assert survivor.close() == 0
    assert not any(_running(child) for child in survivor.children) and not survivor.registry.exists()


def test_startup_sweep_stops_orphans_that_no_guard_could_stop(parents, tmp_path):
    parent = parents("--unguarded", "--children", "2")
    parent.kill()
    # Contrôle négatif : sans gardien ni objet Job, les enfants survivent bien à leur parent.
    assert all(_running(child) for child in parent.children)
    report = sweep_dead_instances(tmp_path / "registries")
    assert sorted(item.pid for item in report.killed) == sorted(child.pid for child in parent.children)
    assert _wait_until(lambda: not any(_running(child) for child in parent.children))
    assert not parent.registry.exists()


def test_normal_close_stops_and_waits_for_every_registered_child(parents):
    parent = parents("--children", "2", "--run")
    assert parent.close() == 0, "shutdown() doit confirmer la fin de chaque enfant"
    assert not any(_running(child) for child in parent.children)
    assert not parent.registry.exists()


def test_closing_the_main_window_stops_every_child_still_registered(qtbot):
    """Étape « processus enfants » de ``_shutdown_steps`` : filet final de la fermeture normale."""
    from ui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    supervisor = process_supervisor.default_supervisor()
    child = supervisor.popen(SLEEP)  # par exemple une sonde lancée par un thread qui n'a pas encore fini
    try:
        assert window.close()
        assert child.wait(timeout=DEADLINE) != 0, "l'enfant devait être tué par la fermeture"
        assert all(item.identity.pid != child.pid for item in supervisor.live_registrations())
    finally:
        supervisor.finish(child)


def test_self_check_used_by_the_smoke_test_passes(monkeypatch, tmp_path):
    monkeypatch.setenv("KUT_STUDIO_LOG_DIR", str(tmp_path / "logs"))
    assert process_supervisor.supervision_self_check() is None
