"""Canal de résultat de la sonde GPU : un fichier, pas stdout (nul en mode fenêtré de PyInstaller sous Windows)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from core.process_supervisor import helper_command, helper_mode_exit_code
from kut3d.gpu import availability, probe_worker

ROOT = Path(__file__).resolve().parents[1]
OUT = probe_worker.OUT_FLAG


def test_emit_writes_the_file_when_stdout_is_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    target = tmp_path / "r.json"
    probe_worker._emit({"ok": True, "backend": "X"}, str(target))
    assert json.loads(target.read_text(encoding="utf-8")) == {
        "ok": True,
        "backend": "X",
    }
    assert not (tmp_path / "r.json.tmp").exists()


def test_emit_never_raises_without_stdout_or_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sys, "stdout", None)
    probe_worker._emit({"ok": False})
    probe_worker._emit({"ok": False}, str(tmp_path / "missing" / "r.json"))


def test_main_with_out_flag_writes_the_file_and_not_stdout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "stdout", None)
    target = tmp_path / "r.json"
    code = probe_worker.main([probe_worker.PROBE_FLAG, OUT, str(target)])
    assert code in (0, 2)
    data = json.loads(target.read_text(encoding="utf-8"))
    assert isinstance(data["ok"], bool)
    assert capfd.readouterr().out == ""


def test_unfrozen_mode_still_prints_a_json_line(
    capfd: pytest.CaptureFixture[str],
) -> None:
    code = probe_worker.main([])
    lines = capfd.readouterr().out.strip().splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["ok"] in (True, False)
    assert code in (0, 2)


def test_out_path_parsing() -> None:
    assert probe_worker.out_path_from(["a", OUT, "/x/y"]) == "/x/y"
    assert probe_worker.out_path_from(["a", OUT]) is None
    assert probe_worker.out_path_from([]) is None


def test_the_supervisor_forwards_the_output_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[list[str]] = []
    monkeypatch.setattr(
        probe_worker, "main", lambda argv=None: seen.append(list(argv or [])) or 0
    )
    assert (
        helper_mode_exit_code(["App", probe_worker.PROBE_FLAG, OUT, "/tmp/q.json"]) == 0
    )
    assert probe_worker.out_path_from(seen[0]) == "/tmp/q.json"


def test_worker_command_forms(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert availability._worker_command("/t/r.json") == helper_command(
        probe_worker.PROBE_FLAG, OUT, "/t/r.json"
    )
    monkeypatch.delattr(sys, "frozen")
    assert availability._worker_command("/t/r.json") == [
        sys.executable,
        "-m",
        "kut3d.gpu.probe_worker",
        OUT,
        "/t/r.json",
    ]
    assert availability._worker_command() == [
        sys.executable,
        "-m",
        "kut3d.gpu.probe_worker",
    ]


def _fake_worker(monkeypatch: pytest.MonkeyPatch, code: str, seen: list[str]) -> None:
    def command(out_path: str | None = None) -> list[str]:
        assert out_path is not None
        seen.append(out_path)
        return [sys.executable, "-c", textwrap.dedent(code), out_path]

    monkeypatch.setattr(availability, "_worker_command", command)
    monkeypatch.setattr(availability, "_pyside_installed", lambda: True)
    monkeypatch.delenv(availability.ENV_SWITCH, raising=False)


WRITE = "import sys, json; open(sys.argv[1], 'w').write({payload})"


def test_the_parent_reads_then_removes_the_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []
    _fake_worker(
        monkeypatch,
        WRITE.format(
            payload="json.dumps({'ok': True, 'backend': 'Metal', 'platform': 'cocoa'})"
        ),
        seen,
    )
    result = availability._run_probe("native", 20)
    assert result.ok and result.backend == "Metal" and result.qt_platform == "cocoa"
    assert not os.path.exists(os.path.dirname(seen[0]))


def test_timeout_cleans_up(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []
    _fake_worker(monkeypatch, "import time; time.sleep(30)", seen)
    result = availability._run_probe("native", 0.5)
    assert not result.ok and "répondu" in result.reason
    assert not os.path.exists(os.path.dirname(seen[0]))


def test_a_worker_that_crashes_without_writing(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []
    _fake_worker(
        monkeypatch,
        "import os, signal; os.kill(os.getpid(), signal.SIGKILL)"
        if sys.platform != "win32"
        else "import sys; sys.exit(3)",
        seen,
    )
    result = availability._run_probe("native", 20)
    assert not result.ok and result.reason
    assert not os.path.exists(os.path.dirname(seen[0]))


@pytest.mark.parametrize(
    "payload",
    [
        "'{not json'",
        "'[1, 2]'",
        "'x' * 100000",
        "json.dumps({'ok': 'yes', 'backend': 'M'})",
        "json.dumps({'foo': 1})",
    ],
)
def test_corrupt_huge_or_wrong_files_fail_cleanly(
    monkeypatch: pytest.MonkeyPatch, payload: str
) -> None:
    seen: list[str] = []
    _fake_worker(monkeypatch, WRITE.format(payload=payload), seen)
    result = availability._run_probe("native", 20)
    assert not result.ok and result.reason
    assert len(result.reason) < 500
    assert not os.path.exists(os.path.dirname(seen[0]))


def test_a_hostile_reason_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []
    _fake_worker(
        monkeypatch,
        WRITE.format(payload="json.dumps({'ok': False, 'reason': 'z' * 3000})"),
        seen,
    )
    assert len(availability._run_probe("native", 20).reason) <= 300


def test_end_to_end_with_closed_stdout_and_stderr(tmp_path: Path) -> None:
    """Vrai sous-processus dont `sys.stdout` / `sys.stderr` valent None, comme l'application gelée en mode fenêtré."""
    target = tmp_path / "r.json"
    script = textwrap.dedent(
        f"""
        import sys
        sys.path.insert(0, {str(ROOT)!r})
        sys.stdout = None
        sys.stderr = None
        from kut3d.gpu import probe_worker
        raise SystemExit(probe_worker.main(["x", probe_worker.OUT_FLAG, {str(target)!r}]))
        """
    )
    native = sys.platform == "darwin" and not os.environ.get("CI")
    env = {k: v for k, v in os.environ.items() if k != "QT_QPA_PLATFORM"}
    if not native:
        # Le canal de résultat ne demande ni GPU ni écran : « offscreen » évite que Qt abandonne le processus sur un
        # runner sans xcb-cursor (CI Linux) avant d'avoir écrit le résultat.
        env["QT_QPA_PLATFORM"] = "offscreen"
    done = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
        env=env,
    )
    assert done.returncode in (0, 2), done.stderr
    data = json.loads(target.read_text(encoding="utf-8"))
    assert isinstance(data["ok"], bool)
    if native:  # plateforme native, comme l'application : la sonde doit réussir
        assert data["ok"] is True, data


def test_the_real_probe_through_the_parent_uses_the_file() -> None:
    result = availability._run_probe(availability.NATIVE, 60)
    assert result.reason or result.ok
