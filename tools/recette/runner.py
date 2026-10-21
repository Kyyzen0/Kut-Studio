"""Lance une commande du banc et la mesure : durée, pic de mémoire, code de sortie, plantage, délai dépassé.

* La mémoire est le **pic de la somme des RSS** du processus et de ses descendants (FFmpeg compris), échantillonnée
  par ``ps`` toutes les ~0,4 s (POSIX) : un pic plus court que l'échantillonnage peut passer inaperçu ; ``None`` quand
  ``ps`` n'existe pas (Windows). ``own_peak_rss_mb`` est le pic exact du processus lancé lui-même (``wait4``).
* Un **plantage** est une mort par signal (``SIGSEGV``, ``SIGABRT``, ``SIGBUS``, ``SIGILL``…) ou un code NTSTATUS Windows
  d'erreur fatale : jamais un échec ordinaire (code 1). Le banc ne provoque aucun plantage : il observe celui d'un enfant.
* **Garde-fous** : si le disque libre du volume surveillé tombe sous ``min_free_gb`` ou si la mémoire de l'arbre dépasse
  ``max_rss_mb``, le lanceur arrête l'enfant **proprement** (même escalade que le délai : ``SIGINT``, que le CLI entend comme
  un Ctrl-C) et le dit (``aborted``). Un export qui grossit sans fin ne doit pas remplir le disque de la machine de recette.
* Un **délai dépassé** envoie ``SIGINT`` (le CLI l'entend comme un Ctrl-C : FFmpeg est arrêté, les fichiers temporaires sont
  nettoyés), puis ``SIGTERM``, et en dernier recours tue **son propre enfant** (jamais un autre processus).
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

SAMPLE_INTERVAL = 0.4
TAIL_CHARS = 1500
"""Fin de la sortie conservée dans le rapport : jamais la sortie entière (un message d'erreur ne recopie pas l'entrée)."""
_CRASH_SIGNALS = {
    name: getattr(signal, name) for name in ("SIGSEGV", "SIGABRT", "SIGBUS", "SIGILL", "SIGFPE", "SIGSYS", "SIGTRAP")
    if hasattr(signal, name)
}


@dataclass
class RunResult:
    command: list[str]
    returncode: int | None
    elapsed_s: float
    peak_rss_mb: float | None = None
    own_peak_rss_mb: float | None = None
    peak_disk_mb: float | None = None
    crashed: bool = False
    signal_name: str | None = None
    timed_out: bool = False
    aborted: str | None = None
    stdout_tail: str = ""
    stderr_tail: str = ""
    stdout_path: str = ""
    stderr_path: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out and not self.aborted

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["ok"] = self.ok
        return data


def classify_exit(returncode: int | None, *, timed_out: bool = False) -> tuple[bool, str | None]:
    """``(plantage ?, nom du signal)`` d'un code de sortie. Un arrêt demandé par le banc (délai) n'est pas un plantage."""
    if returncode is None:
        return False, None
    if returncode < 0:
        number = -returncode
        name = signal.Signals(number).name if number in {s.value for s in signal.Signals} else f"SIG{number}"
        if timed_out and number in (signal.SIGINT, signal.SIGTERM, getattr(signal, "SIGKILL", -1)):
            return False, name
        return number in _CRASH_SIGNALS.values(), name
    if returncode >= 0xC0000000:                       # Windows : STATUS_ACCESS_VIOLATION (0xC0000005), pile, etc.
        return True, f"NTSTATUS_{returncode:#010x}"
    return False, None


def _tail(path: Path) -> str:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - TAIL_CHARS * 4))
            data = handle.read()
    except OSError:
        return ""
    return data.decode("utf-8", errors="replace")[-TAIL_CHARS:]


def _ps_table() -> str | None:
    """``ps -A -o pid=,ppid=,rss=`` ; ``None`` sans ``ps`` (images Debian minimales, Windows)."""
    if sys.platform.startswith("win") or shutil.which("ps") is None:
        return None
    try:
        done = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,rss="], capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def _page_kb() -> int:
    """Taille de page en Ko ; ``os.sysconf`` n'existe pas sous Windows : 4 Ko par défaut (inutilisé là : pas de ``/proc``)."""
    sysconf = getattr(os, "sysconf", None)
    try:
        return max(1, int(sysconf("SC_PAGE_SIZE")) // 1024) if sysconf else 4
    except (OSError, ValueError):
        return 4


def _proc_table(root: Path = Path("/proc"), page_kb: int | None = None) -> str | None:
    """Même table lue dans ``/proc`` (Linux sans ``ps`` : conteneurs Debian) ; ``None`` si ``/proc`` n'existe pas."""
    if not root.is_dir():
        return None
    page_kb = page_kb or _page_kb()
    lines = []
    for entry in root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text(encoding="utf-8", errors="replace")
            resident = int((entry / "statm").read_text().split()[1])
            parent = int(stat[stat.rindex(")") + 2:].split()[1])      # le nom (entre parenthèses) peut contenir des espaces
        except (OSError, ValueError, IndexError):
            continue
        lines.append(f"{entry.name} {parent} {resident * page_kb}")
    return "\n".join(lines)


def process_tree_rss_mb(root_pid: int, table: str | None = None) -> float | None:
    """Somme des RSS (Mo) de ``root_pid`` et de ses descendants d'après ``ps -A -o pid=,ppid=,rss=`` (ou ``table``)."""
    if table is None:
        table = _ps_table() or _proc_table()
        if table is None:
            return None
    children: dict[int, list[int]] = {}
    rss: dict[int, int] = {}
    for line in table.splitlines():
        parts = line.split()
        if len(parts) != 3 or not all(part.isdigit() for part in parts):
            continue
        pid, parent, size = (int(part) for part in parts)
        children.setdefault(parent, []).append(pid)
        rss[pid] = size
    if root_pid not in rss:
        return None
    total, stack, seen = 0, [root_pid], set()
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        total += rss.get(pid, 0)
        stack.extend(children.get(pid, ()))
    return total / 1024.0


def directory_size_mb(path: Path) -> float:
    """Taille des fichiers sous ``path`` (Mo), sans suivre les liens ; ce qui disparaît pendant le parcours est ignoré."""
    total = 0
    stack = [str(path)]
    while stack:
        try:
            with os.scandir(stack.pop()) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total / 2**20


class _Sampler(threading.Thread):
    def __init__(self, pid: int, watch: Sequence[Path] = (), guard: Callable[[str], None] | None = None,
                 min_free_gb: float | None = None, max_rss_mb: float | None = None, guard_path: Path | None = None) -> None:
        super().__init__(daemon=True, name="recette-rss")
        self.pid = pid
        self.watch = watch
        self.guard, self.min_free_gb, self.max_rss_mb, self.guard_path = guard, min_free_gb, max_rss_mb, guard_path
        self.reason: str | None = None
        self.peak: float | None = None
        self.peak_disk: float | None = None
        self._stop_event = threading.Event()

    def run(self) -> None:
        ticks = 0
        while not self._stop_event.is_set():
            value = process_tree_rss_mb(self.pid)
            if value is not None and (self.peak is None or value > self.peak):
                self.peak = value
            self._guard(value, ticks)
            if self.watch and ticks % 5 == 0:         # l'espace disque se mesure toutes les ~2 s
                size = sum(directory_size_mb(path) for path in self.watch)
                if self.peak_disk is None or size > self.peak_disk:
                    self.peak_disk = size
            ticks += 1
            self._stop_event.wait(SAMPLE_INTERVAL)
        if self.watch:                                      # dernière mesure : ce que l'enfant laisse en partant
            size = sum(directory_size_mb(path) for path in self.watch)
            if self.peak_disk is None or size > self.peak_disk:
                self.peak_disk = size

    def _guard(self, rss: float | None, ticks: int) -> None:
        if self.guard is None or self.reason is not None:
            return
        if self.max_rss_mb is not None and rss is not None and rss > self.max_rss_mb:
            self.reason = f"mémoire {rss:.0f} Mo au-delà du garde-fou ({self.max_rss_mb:.0f} Mo)"
        elif self.min_free_gb is not None and self.guard_path is not None and ticks % 5 == 0:
            try:
                free = shutil.disk_usage(self.guard_path).free / 2**30
            except OSError:
                return
            if free < self.min_free_gb:
                self.reason = f"disque libre {free:.1f} Go sous le garde-fou ({self.min_free_gb:.1f} Go)"
        if self.reason:
            self.guard(self.reason)

    def stop(self) -> None:
        self._stop_event.set()
        self.join(timeout=3.0)


def run_measured(
    command: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
    cwd: str | os.PathLike[str] | None = None,
    log_prefix: Path,
    timeout: float | None = None,
    watch_dirs: Sequence[Path] = (),
    min_free_gb: float | None = None,
    max_rss_mb: float | None = None,
    guard_path: Path | None = None,
) -> RunResult:
    """Exécute ``command`` (sans shell), sorties dans ``<log_prefix>.out`` et ``.err``, et rend ses mesures."""
    log_prefix.parent.mkdir(parents=True, exist_ok=True)
    out_path, err_path = Path(f"{log_prefix}.out"), Path(f"{log_prefix}.err")
    started = time.monotonic()
    state = {"timed_out": False}
    with out_path.open("wb") as out, err_path.open("wb") as err:
        try:
            process = subprocess.Popen(
                list(command), stdin=subprocess.DEVNULL, stdout=out, stderr=err, env=dict(env) if env is not None else None,
                cwd=str(cwd) if cwd is not None else None,
            )
        except OSError as error:
            return RunResult(list(command), None, 0.0, stderr_tail=f"Lancement impossible : {type(error).__name__}",
                             stdout_path=str(out_path), stderr_path=str(err_path))
        watchdog = _Watchdog(process, timeout, state)
        sampler = _Sampler(process.pid, watch_dirs, watchdog.fire, min_free_gb, max_rss_mb, guard_path)
        sampler.start()
        own_peak: float | None = None
        if hasattr(os, "wait4"):
            _, status, usage = os.wait4(process.pid, 0)
            code: int | None = os.waitstatus_to_exitcode(status)
            process.returncode = code
            scale = 1.0 if sys.platform == "darwin" else 1024.0
            own_peak = usage.ru_maxrss * scale / 2**20
        else:
            code = process.wait()
        watchdog.stop()
        sampler.stop()
    elapsed = time.monotonic() - started
    crashed, name = classify_exit(code, timed_out=state["timed_out"] or sampler.reason is not None)
    stderr_tail = _tail(err_path)
    if "Fatal Python error" in stderr_tail:
        crashed = True
    return RunResult(
        list(command), code, round(elapsed, 3), peak_rss_mb=_round(sampler.peak), own_peak_rss_mb=_round(own_peak), peak_disk_mb=_round(sampler.peak_disk), crashed=crashed,
        signal_name=name, timed_out=state["timed_out"], aborted=sampler.reason, stdout_tail=_tail(out_path), stderr_tail=stderr_tail,
        stdout_path=str(out_path), stderr_path=str(err_path),
    )


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 1)


class _Watchdog(threading.Thread):
    """Délai dépassé ou garde-fou : ``SIGINT`` (annulation propre du CLI), puis ``SIGTERM``, puis la mort du seul enfant lancé ici."""

    GRACE = 25.0

    def __init__(self, process: subprocess.Popen[bytes], timeout: float | None, state: dict[str, bool]) -> None:
        super().__init__(daemon=True, name="recette-watchdog")
        self.process, self.timeout, self.state = process, timeout, state
        self._stop_event = threading.Event()
        self._fired = threading.Event()
        self.start()

    def fire(self, reason: str = "") -> None:
        """Demande l'arrêt maintenant (garde-fou) ; sans effet si c'est déjà fait."""
        self._fired.set()

    def run(self) -> None:
        deadline = time.monotonic() + self.timeout if self.timeout else None
        while not self._stop_event.is_set() and not self._fired.is_set():
            if deadline is not None and time.monotonic() >= deadline:
                self.state["timed_out"] = True
                break
            self._stop_event.wait(0.25)
        if self._stop_event.is_set():
            return
        steps = ((getattr(signal, "SIGINT", None), self.GRACE), (getattr(signal, "SIGTERM", None), self.GRACE), (None, 0.0))
        for number, grace in steps:
            if self._stop_event.is_set():           # jamais ``poll()`` ici : il récolterait l'état que ``wait4`` attend
                return
            try:
                if os.name == "nt":
                    self.process.kill() if number is None else self.process.terminate()
                elif number is None:
                    os.kill(self.process.pid, getattr(signal, "SIGKILL", signal.SIGTERM))   # dernier recours, l'enfant lancé ici
                else:
                    os.kill(self.process.pid, number)       # ``os.kill`` et non ``send_signal`` : ce dernier appelle ``poll()``
            except (OSError, ValueError):
                return
            if self._stop_event.wait(grace):
                return

    def stop(self) -> None:
        self._stop_event.set()
