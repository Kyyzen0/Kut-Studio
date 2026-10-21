"""Kut-3D dans l'application empaquetée : commande de la sonde GPU, drapeau auxiliaire, commande PyInstaller, auto-contrôle."""

from __future__ import annotations

import subprocess
import sys

import pytest

import build
from core import kut3d_check
from core.process_supervisor import (
    KUT3D_PROBE_FLAG,
    helper_command,
    helper_mode_exit_code,
)
from kut3d.gpu import availability, probe_worker, shader_files


def test_the_probe_flag_is_the_same_in_kut3d_and_in_the_supervisor() -> None:
    assert KUT3D_PROBE_FLAG == probe_worker.PROBE_FLAG


def test_frozen_probe_command_is_exactly_the_helper_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(
        sys, "executable", "/Applications/Kut-Studio.app/Contents/MacOS/Kut-Studio"
    )
    command = availability._worker_command()
    assert command == [sys.executable, probe_worker.PROBE_FLAG]
    assert command == helper_command(probe_worker.PROBE_FLAG)


def test_source_probe_command_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert availability._worker_command() == [
        sys.executable,
        "-m",
        "kut3d.gpu.probe_worker",
    ]


def test_the_supervisor_runs_the_probe_for_its_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []
    monkeypatch.setattr(
        probe_worker, "main", lambda argv=None: calls.append(list(argv or [])) or 2
    )
    assert helper_mode_exit_code(["Kut-Studio", probe_worker.PROBE_FLAG]) == 2
    assert calls == [[probe_worker.PROBE_FLAG]]
    assert helper_mode_exit_code(["Kut-Studio"]) is None


def test_the_probe_worker_prints_only_json_lines(
    capfd: pytest.CaptureFixture[str],
) -> None:
    code = probe_worker.main()
    out = capfd.readouterr().out.strip().splitlines()
    assert len(out) == 1 and out[0].startswith("{"), out
    assert code in (0, 2)


def test_the_real_probe_through_the_application_entry_point() -> None:
    """Même chemin que gelé : `main.py <drapeau>` (via helper_command), la ligne JSON est lue comme le fait le père."""
    done = subprocess.run(
        helper_command(probe_worker.PROBE_FLAG),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    last = next(
        (ln for ln in reversed(done.stdout.splitlines()) if ln.startswith("{")), ""
    )
    assert last, (done.stdout, done.stderr)


@pytest.mark.parametrize(
    ("platform_name", "separator"), [("win32", ";"), ("darwin", ":"), ("linux", ":")]
)
def test_build_command_embeds_kut3d_and_its_shaders(
    platform_name: str, separator: str
) -> None:
    command = build.build_command(platform_name=platform_name, environment={})
    assert command[command.index("--collect-submodules") + 1] == "kut3d"
    data = [command[i + 1] for i, arg in enumerate(command) if arg == "--add-data"]
    assert f"{shader_files.SHADER_DIR}{separator}kut3d/gpu/shaders" in data
    assert any(p.suffix == ".qsb" for p in shader_files.SHADER_DIR.iterdir())


def test_the_self_check_passes_in_the_repository() -> None:
    assert kut3d_check.kut3d_self_check() == ""


def test_the_self_check_reports_missing_shaders(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setattr(shader_files, "SHADER_DIR", tmp_path)
    assert "shaders" in kut3d_check.kut3d_self_check()
