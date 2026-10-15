"""Recherche de FFmpeg : repli sur les dossiers conventionnels, journal de démarrage, appelants uniformes."""

from __future__ import annotations

import logging
import re
import subprocess
import sys
from pathlib import Path

import pytest

from core import tool_paths

posix_only = pytest.mark.skipif(sys.platform.startswith("win"), reason="faux exécutables POSIX")
ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def bare(monkeypatch, tmp_path):
    """Application « gelée » au PATH minimal : aucun outil embarqué, aucun ffmpeg dans le PATH."""
    for variable in ("KUT_STUDIO_FFMPEG", "KUT_STUDIO_FFMPEG_DIR", "KUT_STUDIO_FFPROBE"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", "", raising=False)
    system = tmp_path / "system"
    system.mkdir()
    # Gelée, l'application cherche aussi à côté de son exécutable : sur Linux ce serait /usr/bin, où la CI a FFmpeg.
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    monkeypatch.setattr(sys, "executable", str(app_dir / "Kut-Studio"))
    monkeypatch.setenv("PATH", str(system))
    return system


def _fake(directory: Path, name: str = "ffmpeg", mode: int = 0o755) -> Path:
    directory.mkdir(exist_ok=True)
    path = directory / name
    path.write_text("#!/bin/sh\n")
    path.chmod(mode)
    return path


@posix_only
def test_fallback_finds_a_tool_in_a_conventional_directory(bare, tmp_path) -> None:
    brew = tmp_path / "brew"
    fake = _fake(brew)
    assert tool_paths.find_media_tool("ffmpeg", fallback_dirs=[str(tmp_path / "absent"), str(brew)]) == str(fake)
    assert Path(tool_paths.find_media_tool("ffmpeg", fallback_dirs=[str(brew)])).is_absolute()


@posix_only
def test_fallback_uses_the_platform_table_by_default(bare, tmp_path, monkeypatch) -> None:
    brew = tmp_path / "brew"
    fake = _fake(brew)
    monkeypatch.setattr(tool_paths, "_CONVENTIONAL_TOOL_DIRS", {"darwin": (str(brew),)})
    assert tool_paths.find_media_tool("ffmpeg", platform_name="darwin") == str(fake)
    assert tool_paths.find_media_tool("ffmpeg", platform_name="freebsd") is None


@posix_only
def test_user_path_keeps_priority_over_the_fallback(bare, tmp_path) -> None:
    user_bin, brew = tmp_path / "user", tmp_path / "brew"
    mine = _fake(user_bin)
    _fake(brew)
    import os

    os.environ["PATH"] = str(user_bin)
    assert tool_paths.find_media_tool("ffmpeg", fallback_dirs=[str(brew)]) == str(mine)


@posix_only
def test_fallback_ignores_unusable_candidates(bare, tmp_path) -> None:
    not_exec = _fake(tmp_path / "noexec", mode=0o644)
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "ffmpeg").symlink_to(tmp_path / "nowhere")
    dir_named = tmp_path / "dirname"
    (dir_named / "ffmpeg").mkdir(parents=True)
    dirs = [str(not_exec.parent), str(broken), str(dir_named), str(tmp_path / "absent"), ""]
    assert tool_paths.find_media_tool("ffmpeg", fallback_dirs=dirs) is None


@posix_only
@pytest.mark.parametrize("name", ["../ffmpeg", "sub/ffmpeg", "..", ".", "", "a\\b"])
def test_fallback_refuses_names_with_a_separator(bare, tmp_path, name) -> None:
    _fake(tmp_path / "brew")
    (tmp_path / "brew" / "sub").mkdir()
    _fake(tmp_path / "brew" / "sub")
    assert tool_paths.find_media_tool(name, fallback_dirs=[str(tmp_path / "brew")]) is None


@posix_only
def test_no_fallback_on_windows(bare, tmp_path, monkeypatch) -> None:
    brew = tmp_path / "brew"
    _fake(brew, "ffmpeg.exe")
    _fake(brew, "ffmpeg")
    monkeypatch.setattr(tool_paths, "_CONVENTIONAL_TOOL_DIRS", {"win32": (str(brew),)})
    assert tool_paths.find_media_tool("ffmpeg", platform_name="win32") is None


def test_startup_report_names_the_search_without_a_subprocess(bare, tmp_path, monkeypatch, caplog) -> None:
    def forbidden(*_args, **_kwargs):
        raise AssertionError("aucun sous-processus au démarrage")

    for name in ("run", "Popen", "check_output", "call", "check_call"):
        monkeypatch.setattr(subprocess, name, forbidden)
    home_like = "/Users/alice/bin"
    monkeypatch.setenv("PATH", f"{bare}:{home_like}")
    with caplog.at_level(logging.INFO, logger="core.tool_paths"):
        tool_paths.log_tool_search(("/opt/homebrew/bin",))
    lines = [r.getMessage() for r in caplog.records if r.name == "core.tool_paths"]
    assert len(lines) == 1
    line = lines[0]
    assert "/opt/homebrew/bin" in line and "frozen=True" in line
    assert "ffmpeg : " in line and "ffprobe : " in line
    assert "alice" not in line and "/Users/<…>/bin" in line
    assert caplog.records[0].levelno == logging.INFO


def test_startup_report_says_when_a_tool_is_missing(bare, monkeypatch) -> None:
    monkeypatch.setattr(tool_paths, "_CONVENTIONAL_TOOL_DIRS", {})
    report = tool_paths.tool_search_report(())
    assert report["ffmpeg"] == "introuvable" and report["ajoutes"] == "aucun"


def test_main_logs_the_search_right_after_the_journal_is_installed() -> None:
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    body = source[source.index("added_dirs = extend_search_path()"):]
    assert body.index("install_diagnostics()") < body.index("log_tool_search(added_dirs)")


def test_no_direct_which_for_ffmpeg_tools_outside_tool_paths() -> None:
    pattern = re.compile(r"""shutil\.which\(\s*["'](ffmpeg|ffprobe)["']""")
    offenders = [
        str(path.relative_to(ROOT))
        for folder in ("core", "ui")
        for path in (ROOT / folder).rglob("*.py")
        if pattern.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == [], f"passer par core.tool_paths.find_media_tool : {offenders}"
