"""Supervision des processus enfants (FFmpeg, ffprobe) : aucun ne survit à Kut-Studio, même après un arrêt brutal.

Point de lancement **unique** des processus de ``core/`` et ``ui/`` (un test garde-fou l'impose) :

* :func:`supervised_run` remplace ``subprocess.run`` (sondes, miniatures, détection matérielle) ;
* :func:`supervised_popen` remplace ``subprocess.Popen`` (aperçu fidèle, proxies, tracking, mesures) : gestionnaire
  de contexte qui tue l'enfant s'il tourne encore en sortie, l'attend et le désenregistre ;
* :func:`register_pid` / :func:`release` pour le seul ``QProcess`` (export et file de rendu), enregistré dès
  ``started``.

Protection après un arrêt brutal (détails et options écartées : ``docs/process-supervision.md``) :

* **Windows** : objet Job « tuer à la fermeture » par instance (:class:`~core.process_platform.KillOnCloseJob`) ;
* **macOS / Linux** : processus **gardien** (l'application relancée avec :data:`REAPER_FLAG`) bloqué sur un tube
  dont seul le parent tient l'écriture. À l'EOF (parent mort, par n'importe quelle voie, ou fermeture), il tue les
  enfants enregistrés dont l'identité est vérifiée, puis son propre groupe de processus, que les enfants ``Popen``
  rejoignent avant ``exec``.

Partout : un **registre par instance** sous ``user_cache_dir()/processes/<instance>/``, balayé au démarrage suivant
(:func:`sweep_dead_instances`) si tout le reste a échoué, et une **identité vérifiée** avant chaque arrêt
(:mod:`core.process_platform`) : jamais un PID réutilisé, jamais l'enfant d'une autre instance vivante.

Rien n'est lancé à l'import ni au démarrage : le superviseur naît au premier enfant. Une défaillance de la
supervision est journalisée et n'empêche jamais de lancer FFmpeg.
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .platform_paths import user_cache_dir
from .process_platform import (
    KillOnCloseJob,
    KillOutcome,
    ProbeState,
    ProcessIdentity,
    ProcessOps,
    native_ops,
    same_process,
)

LOGGER = logging.getLogger("kut_studio.process")

REAPER_FLAG = "--kut-process-reaper"
"""Argument qui fait de l'application le gardien d'une instance (traité dans ``main.py`` avant Qt)."""
SLEEPER_FLAG = "--kut-process-sleeper"
"""Argument d'un enfant inerte (auto-contrôle du smoke test, application gelée comprise)."""
REGISTRY_DIR_NAME = "processes"
OWNER_FILE = "owner.json"
CHILD_SUFFIX = ".child"
REGISTRY_FORMAT = 1
ORPHAN_REGISTRY_MAX_AGE_SECONDS = 24 * 3600.0
"""Un dossier d'instance sans ``owner.json`` lisible (création interrompue) est supprimé après ce délai."""
_REGISTRATION_ATTRIBUTE = "_kut_supervision"
_REAPER_LAUNCH_ATTEMPTS = 3


def registry_root(custom: str | os.PathLike[str] | None = None) -> Path:
    """Dossier des registres d'instances (jamais le dossier temporaire : il doit survivre à l'application)."""
    return Path(custom) if custom is not None else user_cache_dir() / REGISTRY_DIR_NAME


def _record(identity: ProcessIdentity) -> dict[str, object]:
    return {"pid": identity.pid, "token": identity.token, "name": identity.name}


def _identity_from(data: object) -> ProcessIdentity | None:
    if not isinstance(data, dict):
        return None
    try:
        pid = int(data["pid"])
    except (KeyError, TypeError, ValueError):
        return None
    if pid <= 0:
        return None
    return ProcessIdentity(pid, str(data.get("token") or ""), str(data.get("name") or ""))


# --- Registre disque d'une instance ---------------------------------------------------------------------------------


class InstanceRegistry:
    """Dossier d'une instance : ``owner.json`` (l'application) et un fichier ``<pid>-<jeton>.child`` par enfant.

    Un fichier par enfant plutôt qu'un journal : la liste des enfants vivants est la liste du dossier, sans
    compactage ; créer ou retirer une entrée coûte un appel système.
    """

    def __init__(self, directory: str | os.PathLike[str]) -> None:
        self.directory = Path(directory)

    @classmethod
    def create(cls, root: Path, instance_id: str, owner: ProcessIdentity | None) -> InstanceRegistry:
        directory = root / instance_id
        directory.mkdir(parents=True, exist_ok=True)
        payload = {
            "format": REGISTRY_FORMAT,
            "instance": instance_id,
            "owner": _record(owner) if owner is not None else None,
            "created": time.time(),
        }
        # Écrit à part puis renommé : un balayage ne lit jamais un propriétaire à moitié écrit.
        staging = directory / f".{OWNER_FILE}.tmp"
        staging.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(staging, directory / OWNER_FILE)
        return cls(directory)

    def _entry(self, identity: ProcessIdentity) -> Path:
        token = "".join(c if c.isalnum() or c in ".-" else "_" for c in identity.token) or "x"
        return self.directory / f"{identity.pid}-{token}{CHILD_SUFFIX}"

    def add(self, identity: ProcessIdentity) -> None:
        self._entry(identity).write_text(json.dumps(_record(identity)), encoding="utf-8")

    def remove(self, identity: ProcessIdentity) -> None:
        try:
            self._entry(identity).unlink()
        except FileNotFoundError:
            pass

    def owner(self) -> tuple[bool, ProcessIdentity | None]:
        """``(lisible, identité)`` : un ``owner.json`` absent ou abîmé ne permet de rien conclure."""
        try:
            data = json.loads((self.directory / OWNER_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False, None
        if not isinstance(data, dict):
            return False, None
        return True, _identity_from(data.get("owner"))

    def children(self) -> list[ProcessIdentity]:
        try:
            paths = sorted(self.directory.glob(f"*{CHILD_SUFFIX}"))
        except OSError:
            return []
        found: list[ProcessIdentity] = []
        for path in paths:
            try:
                identity = _identity_from(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                identity = None
            if identity is not None:
                found.append(identity)
        return found

    def age_seconds(self) -> float:
        try:
            return max(0.0, time.time() - self.directory.stat().st_mtime)
        except OSError:
            return 0.0

    def destroy(self) -> None:
        shutil.rmtree(self.directory, ignore_errors=True)


# --- Balayage --------------------------------------------------------------------------------------------------------


@dataclass
class SweepReport:
    """Bilan d'un balayage (au démarrage, ou par le gardien à la mort de son parent)."""

    killed: list[ProcessIdentity] = field(default_factory=list)
    spared: list[tuple[ProcessIdentity, KillOutcome]] = field(default_factory=list)
    """Enfants enregistrés **non** tués : autre processus sous le même PID, identité illisible, échec."""
    removed: int = 0
    """Registres supprimés."""
    kept: int = 0
    """Registres laissés intacts (instance vivante, ou impossible à prouver morte)."""
    errors: list[str] = field(default_factory=list)


def owner_state(registry: InstanceRegistry, ops: ProcessOps) -> ProbeState:
    """``GONE`` seulement si l'instance est **prouvée** morte : PID disparu, ou réutilisé (autre jeton).

    Tout doute (fichier illisible, identité inconnue) donne ``UNKNOWN`` : on ne touche à rien.
    """
    readable, owner = registry.owner()
    if not readable or owner is None:
        return ProbeState.UNKNOWN
    probe = ops.probe(owner.pid)
    if probe.state is ProbeState.GONE:
        return ProbeState.GONE
    if probe.state is ProbeState.ALIVE and probe.identity is not None and owner.token:
        if probe.identity.token != owner.token:
            return ProbeState.GONE
        return ProbeState.ALIVE if same_process(owner, probe.identity) else ProbeState.UNKNOWN
    return ProbeState.UNKNOWN


def sweep_instance(
    directory: str | os.PathLike[str],
    ops: ProcessOps,
    *,
    require_dead_owner: bool = True,
    report: SweepReport | None = None,
) -> SweepReport:
    """Arrête les enfants vérifiés d'un registre puis le supprime.

    ``require_dead_owner`` (balayage au démarrage) : rien n'est touché tant que le propriétaire n'est pas prouvé
    mort. Le gardien, lui, balaie le registre de son propre parent sans cette condition (EOF du tube).
    """
    report = report if report is not None else SweepReport()
    registry = InstanceRegistry(directory)
    if require_dead_owner:
        state = owner_state(registry, ops)
        if state is not ProbeState.GONE:
            readable, _owner = registry.owner()
            if (not readable and not registry.children()
                    and registry.age_seconds() > ORPHAN_REGISTRY_MAX_AGE_SECONDS):
                registry.destroy()  # création interrompue, il y a longtemps : aucun enfant n'a pu y être noté
                report.removed += 1
            else:
                report.kept += 1
            return report
    failed = False
    for child in registry.children():
        outcome = ops.kill_if_same(child)
        if outcome is KillOutcome.KILLED:
            report.killed.append(child)
        elif outcome is not KillOutcome.ALREADY_GONE:
            report.spared.append((child, outcome))
            failed = failed or outcome is KillOutcome.FAILED
    if failed:
        report.kept += 1  # un enfant vérifié n'a pas pu être tué : le prochain balayage réessaiera
    else:
        registry.destroy()
        report.removed += 1
    return report


def sweep_dead_instances(
    root: str | os.PathLike[str] | None = None,
    *,
    ops: ProcessOps | None = None,
) -> SweepReport:
    """Balayage au démarrage : enfants laissés par des instances mortes (le gardien ou le job n'a pas pu agir).

    Ne lève jamais ; le bilan va au journal de diagnostic quand quelque chose a été fait.
    """
    report = SweepReport()
    try:
        directory = registry_root(root)
        if not directory.is_dir():
            return report
        operations = ops if ops is not None else native_ops()
        for entry in sorted(directory.iterdir()):
            if not entry.is_dir():
                continue
            try:
                sweep_instance(entry, operations, require_dead_owner=True, report=report)
            except Exception as error:  # noqa: BLE001 - un registre abîmé n'empêche pas les autres
                report.errors.append(f"{entry.name} : {error}")
    except Exception as error:  # noqa: BLE001 - le démarrage ne dépend jamais du balayage
        report.errors.append(str(error))
    if report.killed or report.removed or report.spared:
        LOGGER.info(
            "Supervision : balayage au démarrage — %d enfant(s) orphelin(s) arrêté(s), %d épargné(s), "
            "%d registre(s) supprimé(s), %d instance(s) vivante(s) intacte(s)",
            len(report.killed), len(report.spared), report.removed, report.kept,
        )
    for message in report.errors:
        LOGGER.warning("Supervision : balayage au démarrage — %s", message)
    return report


# --- Gardien (macOS / Linux) -----------------------------------------------------------------------------------------


def _kill_own_group() -> None:
    """Dernier geste du gardien : ``SIGKILL`` à son groupe (lui compris), **seulement** s'il en est le chef.

    Le gardien est lancé chef d'un groupe neuf ; seuls y entrent les enfants ``Popen`` de son instance. S'il n'en
    était pas le chef (groupe refusé au lancement), ce groupe serait celui de l'application ou du terminal : on ne
    touche à rien.
    """
    getpgrp, killpg = getattr(os, "getpgrp", None), getattr(os, "killpg", None)
    if getpgrp is None or killpg is None or getpgrp() != os.getpid():
        return
    killpg(os.getpid(), signal.SIGKILL)


def run_reaper(
    registry_dir: str | os.PathLike[str],
    *,
    stdin_fd: int = 0,
    ops: ProcessOps | None = None,
    kill_own_group: Callable[[], None] = _kill_own_group,
) -> SweepReport:
    """Corps du gardien : attend l'EOF du tube (parent mort ou fermé), arrête les enfants, puis son groupe."""
    while True:
        try:
            chunk = os.read(stdin_fd, 4096)
        except OSError:
            break
        if not chunk:
            break
    report = SweepReport()
    try:
        sweep_instance(registry_dir, ops if ops is not None else native_ops(), require_dead_owner=False,
                       report=report)
    except Exception as error:  # noqa: BLE001 - le groupe doit être tué quoi qu'il arrive
        report.errors.append(str(error))
    if report.killed or report.spared or report.errors:
        LOGGER.warning(
            "Supervision : tube du parent fermé (parent mort) — %d enfant(s) arrêté(s), %d épargné(s) %s%s",
            len(report.killed), len(report.spared),
            [f"{identity.pid}:{outcome.value}" for identity, outcome in report.spared],
            f" ; erreurs : {report.errors}" if report.errors else "",
        )
    for handler in logging.getLogger("kut_studio").handlers:
        handler.flush()
    kill_own_group()
    return report


def _reaper_logging() -> None:
    """Le gardien écrit dans le journal de diagnostic de l'application (ajout simple, sans rotation)."""
    from .diagnostics_log import log_file_path

    try:
        path = log_file_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(path, encoding="utf-8", delay=True)
    except OSError:
        return
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s gardien-%(process)d %(name)s : %(message)s"))
    root = logging.getLogger("kut_studio")
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def _ignore_stop_signals() -> None:
    """Seul l'EOF arrête le gardien : un Ctrl-C ou une fermeture de terminal ne doit pas le tuer avant son parent."""
    for name in ("SIGINT", "SIGHUP", "SIGTERM", "SIGQUIT"):
        number = getattr(signal, name, None)
        if number is not None:
            try:
                signal.signal(number, signal.SIG_IGN)
            except (OSError, ValueError):
                pass


def helper_command(flag: str, *arguments: str) -> list[str]:
    """Commande qui relance l'application dans un mode auxiliaire, en source comme gelée (PyInstaller).

    Gelée, ``sys.executable`` est l'application elle-même (``python -m`` n'existe pas) : l'argument est traité dans
    ``main.py`` avant Qt. En source, on passe aussi par ``main.py`` : même chemin que l'application construite.
    """
    if getattr(sys, "frozen", False):
        return [sys.executable, flag, *arguments]
    root = Path(__file__).resolve().parent.parent
    script = root / "main.py"
    if script.is_file():
        return [sys.executable, str(script), flag, *arguments]
    bootstrap = (
        "import sys; sys.path.insert(0, sys.argv.pop(1)); "
        "from core.process_supervisor import helper_mode_exit_code; "
        "raise SystemExit(helper_mode_exit_code(sys.argv) or 0)"
    )
    return [sys.executable, "-c", bootstrap, str(root), flag, *arguments]


def helper_mode_exit_code(argv: Sequence[str]) -> int | None:
    """Modes auxiliaires de l'application (appelé en tête de ``main.py``) ; ``None`` = lancement normal."""
    arguments = list(argv[1:])
    if REAPER_FLAG in arguments:
        index = arguments.index(REAPER_FLAG)
        if index + 1 >= len(arguments):
            return 2
        _ignore_stop_signals()
        _reaper_logging()
        run_reaper(arguments[index + 1])
        return 0
    if SLEEPER_FLAG in arguments:
        index = arguments.index(SLEEPER_FLAG)
        try:
            seconds = float(arguments[index + 1]) if index + 1 < len(arguments) else 60.0
        except ValueError:
            seconds = 60.0
        time.sleep(max(0.0, min(seconds, 600.0)))
        return 0
    return None


# --- Superviseur -----------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Registration:
    """Un enfant enregistré, désigné par son identité vérifiable."""

    identity: ProcessIdentity


def _is_running(ops: ProcessOps, identity: ProcessIdentity) -> bool:
    probe = ops.probe(identity.pid)
    return (probe.state is ProbeState.ALIVE and probe.identity is not None
            and same_process(identity, probe.identity))


class ProcessSupervisor:
    """Superviseur d'une instance de l'application (une instance = un processus).

    Le registre, le gardien (POSIX) et l'objet Job (Windows) sont créés au premier enfant. Les options servent aux
    tests : fausse plateforme (``ops``), faux job, dossier de registres, protection désactivée.
    """

    def __init__(
        self,
        registry_root_dir: str | os.PathLike[str] | None = None,
        *,
        ops: ProcessOps | None = None,
        use_reaper: bool | None = None,
        use_job: bool | None = None,
        job: KillOnCloseJob | None = None,
        instance_id: str | None = None,
    ) -> None:
        windows = sys.platform.startswith("win")
        self.instance_id = instance_id or f"{os.getpid()}-{uuid.uuid4().hex[:12]}"
        self._root = Path(registry_root_dir) if registry_root_dir is not None else None
        self._ops = ops
        self._use_reaper = (not windows) if use_reaper is None else bool(use_reaper)
        self._use_job = (windows or job is not None) if use_job is None else bool(use_job)
        self._job = job
        self._lock = threading.RLock()
        self._started = False
        self._closed = False
        self._registry: InstanceRegistry | None = None
        self._reaper: subprocess.Popen[bytes] | None = None
        self._reaper_failures = 0
        self._live: dict[tuple[int, str], Registration] = {}
        self._warned: set[str] = set()

    # -- état (diagnostic, tests) ------------------------------------------------------------------------------------

    @property
    def ops(self) -> ProcessOps:
        if self._ops is None:
            self._ops = native_ops()
        return self._ops

    @property
    def registry_directory(self) -> Path | None:
        registry = self._registry
        return registry.directory if registry is not None else None

    @property
    def reaper_pid(self) -> int | None:
        reaper = self._reaper
        return reaper.pid if reaper is not None and reaper.poll() is None else None

    @property
    def uses_reaper(self) -> bool:
        return self._use_reaper

    @property
    def uses_job(self) -> bool:
        return self._use_job

    def live_registrations(self) -> list[Registration]:
        with self._lock:
            return list(self._live.values())

    # -- mise en route paresseuse --------------------------------------------------------------------------------------

    def _warn_once(self, key: str, message: str, *args: object) -> None:
        if key in self._warned:
            LOGGER.debug("Supervision : " + message, *args)
            return
        self._warned.add(key)
        LOGGER.warning("Supervision : " + message, *args)

    def _ensure_started(self) -> None:
        """Registre de l'instance, au premier enfant (sous verrou)."""
        if self._started:
            return
        self._started = True
        try:
            probe = self.ops.probe(os.getpid())
            owner = probe.identity if probe.state is ProbeState.ALIVE else None
            self._registry = InstanceRegistry.create(registry_root(self._root), self.instance_id, owner)
        except Exception as error:  # noqa: BLE001 - FFmpeg se lance quand même
            self._warn_once("registry", "registre des processus indisponible (%s) ; pas de balayage possible", error)

    def _job_object(self) -> KillOnCloseJob:
        with self._lock:
            if self._job is None:
                self._job = KillOnCloseJob()
            return self._job

    def _reaper_group(self) -> int | None:
        """Groupe du gardien, lancé (ou relancé) au besoin ; ``None`` sans gardien (sous verrou)."""
        if not self._use_reaper or self._closed:
            return None
        reaper = self._reaper
        if reaper is not None and reaper.poll() is None:
            return reaper.pid
        if reaper is not None:
            self._warn_once("reaper-exit", "gardien terminé (code %s) : relancé", reaper.returncode)
            self._reaper = None
        if self._reaper_failures >= _REAPER_LAUNCH_ATTEMPTS:
            return None
        registry = self._registry
        directory = registry.directory if registry is not None else registry_root(self._root) / self.instance_id
        try:
            # Chef d'un groupe neuf (``process_group=0``) ; le bout d'écriture du tube reste au seul parent
            # (descripteur non héritable, ``close_fds``) : sa fermeture, même par le noyau, est l'EOF attendu.
            self._reaper = subprocess.Popen(
                helper_command(REAPER_FLAG, str(directory)),
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                close_fds=True, process_group=0,
            )
        except Exception as error:  # noqa: BLE001 - FFmpeg se lance quand même
            self._reaper_failures += 1
            self._warn_once("reaper-launch", "gardien impossible à lancer (%s) ; enfants non protégés", error)
            return None
        return self._reaper.pid

    # -- lancement -----------------------------------------------------------------------------------------------------

    def _adopt(self, pid: int) -> Registration | None:
        """Après le lancement : job (Windows), identité, registre. Ne lève jamais."""
        try:
            if self._use_job:
                self._job_object().assign(pid)
            probe = self.ops.probe(pid)
            if probe.state is ProbeState.GONE:
                return None  # déjà terminé : rien à protéger
            if probe.identity is None:
                self._warn_once("identity", "identité du processus %d illisible (%s)", pid, probe.detail)
                return None
            registration = Registration(probe.identity)
            with self._lock:
                self._live[(probe.identity.pid, probe.identity.token)] = registration
                registry = self._registry
            if registry is not None:
                try:
                    registry.add(probe.identity)
                except OSError as error:
                    self._warn_once("registry-write", "écriture du registre impossible (%s)", error)
            return registration
        except Exception:  # noqa: BLE001 - la supervision n'empêche jamais de lancer FFmpeg
            if "adopt" not in self._warned:
                self._warned.add("adopt")
                LOGGER.exception("Supervision : enregistrement du processus %d impossible", pid)
            return None

    def popen(self, command: Sequence[str | os.PathLike[str]], **options: Any) -> subprocess.Popen[Any]:
        """``subprocess.Popen`` supervisé : l'appelant appelle ensuite :meth:`finish` (ou passe par :meth:`supervised`).

        Par défaut ``stdin`` est fermé (``DEVNULL`` : FFmpeg ne lit jamais le terminal) et, sous Windows, aucune
        console ne s'ouvre. Sous POSIX l'enfant rejoint le groupe du gardien **avant** ``exec``.
        """
        arguments = list(command)
        options.setdefault("stdin", subprocess.DEVNULL)
        if sys.platform.startswith("win"):
            options["creationflags"] = int(options.get("creationflags") or 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        with self._lock:
            self._ensure_started()
            group = self._reaper_group()
        if group is not None and "process_group" not in options and "start_new_session" not in options:
            try:
                process = subprocess.Popen(arguments, process_group=group, **options)
            except FileNotFoundError:
                raise
            except OSError as error:
                # Groupe refusé (gardien mort entre-temps…) : l'enfant est lancé quand même, protégé par le registre.
                self._warn_once("group", "groupe du gardien refusé (%s)", error)
                process = subprocess.Popen(arguments, **options)
        else:
            process = subprocess.Popen(arguments, **options)
        setattr(process, _REGISTRATION_ATTRIBUTE, self._adopt(process.pid))
        return process

    def finish(self, process: subprocess.Popen[Any]) -> None:
        """Tue l'enfant s'il tourne encore, attend sa fin, puis le retire du registre."""
        try:
            if process.poll() is None:
                process.kill()
            process.wait()
        except OSError:
            pass
        finally:
            self.release(getattr(process, _REGISTRATION_ATTRIBUTE, None))

    @contextmanager
    def supervised(self, command: Sequence[str | os.PathLike[str]], **options: Any) -> Iterator[subprocess.Popen[Any]]:
        """``with supervisor.supervised(cmd, ...) as process:`` — à la sortie, l'enfant est fini et oublié."""
        process = self.popen(command, **options)
        try:
            yield process
        finally:
            self.finish(process)

    def run(
        self,
        command: Sequence[str | os.PathLike[str]],
        *,
        input: Any = None,
        capture_output: bool = False,
        timeout: float | None = None,
        check: bool = False,
        **options: Any,
    ) -> subprocess.CompletedProcess[Any]:
        """Équivalent supervisé de ``subprocess.run`` : mêmes arguments, mêmes exceptions (``OSError``,
        ``subprocess.TimeoutExpired`` après avoir tué l'enfant, ``CalledProcessError`` avec ``check``)."""
        if input is not None:
            if "stdin" in options:
                raise ValueError("stdin et input ne peuvent pas être utilisés ensemble.")
            options["stdin"] = subprocess.PIPE
        if capture_output:
            if "stdout" in options or "stderr" in options:
                raise ValueError("capture_output exclut stdout et stderr.")
            options["stdout"] = subprocess.PIPE
            options["stderr"] = subprocess.PIPE
        with self.supervised(command, **options) as process:
            try:
                stdout, stderr = process.communicate(input, timeout=timeout)
            except subprocess.TimeoutExpired as error:
                process.kill()
                if sys.platform.startswith("win"):
                    error.stdout, error.stderr = process.communicate()
                else:
                    process.wait()
                raise
            code = process.wait()
        if check and code:
            raise subprocess.CalledProcessError(code, process.args, output=stdout, stderr=stderr)
        return subprocess.CompletedProcess(process.args, code, stdout, stderr)

    def register_pid(self, pid: int) -> Registration | None:
        """Enregistre un enfant lancé hors de :meth:`popen` (``QProcess``) ; à appeler dès ``started``."""
        if pid <= 0:
            return None
        with self._lock:
            self._ensure_started()
            self._reaper_group()  # le gardien doit exister pour le protéger
        return self._adopt(int(pid))

    def release(self, registration: Registration | None) -> None:
        """Oublie un enfant terminé (idempotent)."""
        if registration is None:
            return
        identity = registration.identity
        with self._lock:
            self._live.pop((identity.pid, identity.token), None)
            registry = self._registry
        if registry is not None:
            try:
                registry.remove(identity)
            except OSError:
                pass

    # -- fermeture -----------------------------------------------------------------------------------------------------

    def shutdown(self, timeout: float = 3.0) -> bool:
        """Fermeture normale : arrête (identité vérifiée) puis attend tout enfant encore enregistré.

        Retourne ``False`` si un enfant tourne encore à l'échéance (journalisé).
        """
        pending = self.live_registrations()
        if not pending:
            return True
        killed = 0
        for registration in pending:
            if self.ops.kill_if_same(registration.identity) is KillOutcome.KILLED:
                killed += 1
        deadline = time.monotonic() + max(0.0, timeout)
        remaining = list(pending)
        while remaining:
            remaining = [item for item in remaining if _is_running(self.ops, item.identity)]
            if not remaining or time.monotonic() >= deadline:
                break
            time.sleep(0.02)
        for registration in pending:
            if registration not in remaining:
                self.release(registration)
        LOGGER.info("Supervision : fermeture — %d processus enfant(s) arrêté(s)", killed)
        if remaining:
            LOGGER.warning(
                "Supervision : %d processus enfant(s) encore vivant(s) après la fermeture : %s",
                len(remaining), [item.identity.pid for item in remaining],
            )
        return not remaining

    def _stop_reaper(self, timeout: float) -> None:
        """Ferme le tube du gardien (même effet que la mort du parent) et l'attend."""
        with self._lock:
            reaper, self._reaper = self._reaper, None
        if reaper is None:
            return
        try:
            if reaper.stdin is not None:
                reaper.stdin.close()
            reaper.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            reaper.kill()
            reaper.wait()
        except OSError:
            pass

    def close(self, timeout: float = 2.0) -> None:
        """Fin de l'instance (``atexit``) : enfants arrêtés, gardien libéré, job fermé, registre supprimé."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
        try:
            clean = self.shutdown(timeout)
            self._stop_reaper(timeout)
            if self._job is not None:
                self._job.close()
            registry, self._registry = self._registry, None
            if registry is not None and clean:
                registry.destroy()
        except Exception:  # noqa: BLE001 - jamais d'exception à la sortie de l'application
            LOGGER.exception("Supervision : fermeture du superviseur")


# --- Superviseur de l'application ------------------------------------------------------------------------------------

_default: ProcessSupervisor | None = None
_default_lock = threading.Lock()


def default_supervisor() -> ProcessSupervisor:
    """Superviseur de ce processus, créé au premier enfant (fermé par ``atexit``)."""
    global _default
    with _default_lock:
        if _default is None:
            _default = ProcessSupervisor()
            atexit.register(_default.close)
        return _default


def active_supervisor() -> ProcessSupervisor | None:
    """Le superviseur s'il existe déjà (n'en crée jamais)."""
    return _default


def supervised_popen(
    command: Sequence[str | os.PathLike[str]], **options: Any
) -> AbstractContextManager[subprocess.Popen[Any]]:
    """``with supervised_popen(cmd, stdout=PIPE) as process:`` — remplace ``subprocess.Popen``."""
    return default_supervisor().supervised(command, **options)


def supervised_run(command: Sequence[str | os.PathLike[str]], **options: Any) -> subprocess.CompletedProcess[Any]:
    """Remplace ``subprocess.run`` (mêmes arguments, mêmes exceptions)."""
    return default_supervisor().run(command, **options)


def register_pid(pid: int) -> Registration | None:
    """Enregistre le PID d'un ``QProcess`` démarré (signal ``started``)."""
    return default_supervisor().register_pid(pid)


def release(registration: Registration | None) -> None:
    """Oublie un enfant enregistré par :func:`register_pid` (signal ``finished``)."""
    supervisor = _default
    if supervisor is not None and registration is not None:
        supervisor.release(registration)


def shutdown_children(timeout: float = 3.0) -> bool:
    """Étape de fermeture de la fenêtre : tout enfant encore enregistré est tué puis attendu."""
    supervisor = _default
    return True if supervisor is None else supervisor.shutdown(timeout)


def supervision_self_check(timeout: float = 15.0) -> str | None:
    """Auto-contrôle (smoke test, application gelée comprise) ; ``None`` si la protection fonctionne.

    Un enfant réel (l'application en mode :data:`SLEEPER_FLAG`) est enregistré comme un ``QProcess`` (hors du
    groupe du gardien : seul le registre vérifié peut le tuer), puis la protection est déclenchée comme à la mort du
    parent : tube du gardien fermé (POSIX) ou poignée du job fermée (Windows). L'enfant doit mourir.
    """
    supervisor = ProcessSupervisor()
    if not (supervisor.uses_reaper or supervisor.uses_job):
        return None
    creation = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform.startswith("win") else 0
    try:
        child = subprocess.Popen(
            helper_command(SLEEPER_FLAG, "60"), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, creationflags=creation,
        )
    except OSError as error:
        return f"lancement d'un enfant impossible : {error}"
    try:
        if supervisor.register_pid(child.pid) is None:
            return "enfant non enregistré (identité illisible)"
        if supervisor.uses_reaper:
            if supervisor.reaper_pid is None:
                return "gardien non lancé"
            supervisor._stop_reaper(timeout)
        else:
            job = supervisor._job
            if job is None or not job.active:
                return "objet Job inactif"
            job.close()
        try:
            child.wait(timeout=timeout)  # il dort 60 s : une fin avant l'échéance est un arrêt
        except subprocess.TimeoutExpired:
            return "l'enfant a survécu au déclenchement de la protection"
        return None
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
        supervisor.close()


__all__ = [
    "InstanceRegistry", "ProcessSupervisor", "REAPER_FLAG", "Registration", "SLEEPER_FLAG", "SweepReport",
    "active_supervisor", "default_supervisor", "helper_command", "helper_mode_exit_code", "owner_state",
    "register_pid", "registry_root", "release", "run_reaper", "shutdown_children", "supervised_popen",
    "supervised_run", "supervision_self_check", "sweep_dead_instances", "sweep_instance",
]
