"""Arrêt d'un enfant et de ses descendants (``core/process_tree.py``) : le shim FFmpeg de Chocolatey sous Windows.

Tout est simulé (faux processus, faux ``taskkill``, plateforme choisie) : aucun test ne dépend de Windows ni d'un vrai FFmpeg.
"""

from __future__ import annotations

import subprocess

from core.process_tree import kill_process_tree


class _FakeProcess:
    def __init__(
        self,
        events: list[str],
        pid: int = 4242,
        running: bool = True,
        kill_error: OSError | None = None,
    ) -> None:
        self.pid = pid
        self._events = events
        self._running = running
        self._kill_error = kill_error

    def poll(self):
        return None if self._running else 0

    def kill(self) -> None:
        self._events.append("kill")
        if self._kill_error is not None:
            raise self._kill_error


def _launcher(events: list[str], error: Exception | None = None):
    def run(command, **options):
        events.append("taskkill:" + " ".join(command))
        assert options["timeout"] > 0 and options["check"] is False
        if error is not None:
            raise error

    return run


def test_on_windows_the_whole_tree_is_killed_before_the_parent():
    events: list[str] = []
    kill_process_tree(_FakeProcess(events), windows=True, run=_launcher(events))
    assert events == ["taskkill:taskkill /PID 4242 /T /F", "kill"], (
        "taskkill d'abord : il suit les enfants du parent encore vivant"
    )


def test_off_windows_only_the_process_itself_is_killed():
    events: list[str] = []
    kill_process_tree(_FakeProcess(events), windows=False, run=_launcher(events))
    assert events == ["kill"]


def test_an_already_finished_process_needs_no_taskkill():
    events: list[str] = []
    kill_process_tree(
        _FakeProcess(events, running=False), windows=True, run=_launcher(events)
    )
    assert events == ["kill"]


def test_a_taskkill_that_cannot_run_still_kills_the_parent_and_never_raises(caplog):
    for error in (
        FileNotFoundError("taskkill"),
        subprocess.TimeoutExpired("taskkill", 10.0),
    ):
        events: list[str] = []
        with caplog.at_level("WARNING", logger="kut_studio.process"):
            kill_process_tree(
                _FakeProcess(events), windows=True, run=_launcher(events, error)
            )
        assert events[-1] == "kill"
    assert "taskkill impossible" in caplog.text


def test_a_parent_that_cannot_be_killed_does_not_raise():
    events: list[str] = []
    kill_process_tree(
        _FakeProcess(events, kill_error=PermissionError("refusé")), windows=False
    )
    assert events == ["kill"]
