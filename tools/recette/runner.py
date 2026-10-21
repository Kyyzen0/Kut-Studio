"""Lance une commande du banc et la mesure : durée, pic de mémoire, code de sortie, plantage, délai dépassé.

* La mémoire est le **pic de la somme des RSS** du processus et de ses descendants (FFmpeg compris), échantillonnée
  par ``ps`` toutes les ~0,4 s (POSIX) : un pic plus court que l'échantillonnage peut passer inaperçu ; ``None`` quand
  ``ps`` n'existe pas (Windows). ``own_peak_rss_mb`` est le pic exact du processus lancé lui-même (``wait4``).
* Un **plantage** est une mort par signal (``SIGSEGV``, ``SIGABRT``, ``SIGBUS``, ``SIGILL``…) ou un code NTSTATUS Windows
  d'erreur fatale : jamais un échec ordinaire (code 1). Le banc ne provoque aucun plantage : il observe celui d'un enfant.
* Un **délai dépassé** envoie ``SIGINT`` (le CLI l'entend comme un Ctrl-C : FFmpeg est arrêté, les fichiers temporaires sont
  nettoyés), puis ``SIGTERM``, et en dernier recours tue **son propre enfant** (jamais un autre processus).
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Mapping, Sequence
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
    crashed: bool = False
    signal_name: str | None = None
    timed_out: bool = False
    stdout_tail: str = ""
    stderr_tail: str = ""
    stdout_path: str = ""
    stderr_path: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out

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


def process_tree_rss_mb(root_pid: int, table: str | None = None) -> float | None:
    """Somme des RSS (Mo) de ``root_pid`` et de ses descendants d'après ``ps -A -o pid=,ppid=,rss=`` (ou ``table``)."""
    if table is None:
        if sys.platform.startswith("win"):
            return None
        try:
            done = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,rss="], capture_output=True, text=True, timeout=5, check=False)
        except (OSError, subprocess.SubprocessError):
            return None
        if done.returncode != 0:
            return None
        table = done.stdout
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


class _Sampler(threading.Thread):
    def __init__(self, pid: int) -> None:
        super().__init__(daemon=True, name="recette-rss")
        self.pid = pid
        self.peak: float | None = None
        self._stop_event = threading.Event()

    def run(self) -> None:
        while not self._stop_event.is_set():
            value = process_tree_rss_mb(self.pid)
            if value is not None and (self.peak is None or value > self.peak):
                self.peak = value
            self._stop_event.wait(SAMPLE_INTERVAL)

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
        sampler = _Sampler(process.pid)
        sampler.start()
        watchdog = _Watchdog(process, timeout, state) if timeout else None
        own_peak: float | None = None
        if hasattr(os, "wait4"):
            _, status, usage = os.wait4(process.pid, 0)
            code: int | None = os.waitstatus_to_exitcode(status)
            process.returncode = code
            scale = 1.0 if sys.platform == "darwin" else 1024.0
            own_peak = usage.ru_maxrss * scale / 2**20
        else:
            code = process.wait()
        if watchdog is not None:
            watchdog.stop()
        sampler.stop()
    elapsed = time.monotonic() - started
    crashed, name = classify_exit(code, timed_out=state["timed_out"])
    stderr_tail = _tail(err_path)
    if "Fatal Python error" in stderr_tail:
        crashed = True
    return RunResult(
        list(command), code, round(elapsed, 3), peak_rss_mb=_round(sampler.peak), own_peak_rss_mb=_round(own_peak), crashed=crashed,
        signal_name=name, timed_out=state["timed_out"], stdout_tail=_tail(out_path), stderr_tail=stderr_tail,
        stdout_path=str(out_path), stderr_path=str(err_path),
    )


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 1)


class _Watchdog(threading.Thread):
    """Délai dépassé : ``SIGINT`` (annulation propre du CLI), puis ``SIGTERM``, puis la mort du seul enfant lancé ici."""

    GRACE = 25.0

    def __init__(self, process: subprocess.Popen[bytes], timeout: float, state: dict[str, bool]) -> None:
        super().__init__(daemon=True, name="recette-watchdog")
        self.process, self.timeout, self.state = process, timeout, state
        self._stop_event = threading.Event()
        self.start()

    def run(self) -> None:
        if self._stop_event.wait(self.timeout):
            return
        self.state["timed_out"] = True
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
