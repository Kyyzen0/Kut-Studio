"""Contrats macOS, Windows et Linux de la tâche 32.5."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


def test_native_config_directories_for_all_platforms() -> None:
    from core.platform_paths import user_config_dir

    assert user_config_dir(
        platform_name="win32",
        environment={"APPDATA": "C:/Users/A/AppData/Roaming"},
        home="C:/Users/A",
    ) == Path("C:/Users/A/AppData/Roaming/Kut-Studio")
    assert user_config_dir(
        platform_name="darwin", environment={}, home="/Users/a"
    ) == Path("/Users/a/Library/Application Support/Kut-Studio")
    assert user_config_dir(
        platform_name="linux",
        environment={"XDG_CONFIG_HOME": "/home/a/config"},
        home="/home/a",
    ) == Path("/home/a/config/kut-studio")


def test_native_cache_directories_for_all_platforms() -> None:
    from core.platform_paths import user_cache_dir

    assert user_cache_dir(
        platform_name="win32",
        environment={"LOCALAPPDATA": "C:/Users/A/AppData/Local"},
        home="C:/Users/A",
    ) == Path("C:/Users/A/AppData/Local/Kut-Studio/Cache")
    assert user_cache_dir(
        platform_name="darwin", environment={}, home="/Users/a"
    ) == Path("/Users/a/Library/Caches/Kut-Studio")
    assert user_cache_dir(
        platform_name="linux", environment={}, home="/home/a"
    ) == Path("/home/a/.cache/kut-studio")


def test_environment_overrides_are_portable() -> None:
    from core.platform_paths import user_cache_dir, user_config_dir

    env = {
        "KUT_STUDIO_CONFIG_DIR": "/portable/config",
        "KUT_STUDIO_CACHE_DIR": "/portable/cache",
    }
    assert user_config_dir(platform_name="win32", environment=env) == Path(
        "/portable/config"
    )
    assert user_cache_dir(platform_name="darwin", environment=env) == Path(
        "/portable/cache"
    )


def test_windows_font_directories_are_supported() -> None:
    from core.platform_paths import system_font_dirs

    paths = system_font_dirs(
        platform_name="win32",
        environment={
            "WINDIR": "C:/Windows",
            "LOCALAPPDATA": "C:/Users/A/AppData/Local",
        },
        home="C:/Users/A",
    )
    assert Path("C:/Windows/Fonts") in paths
    assert Path("C:/Users/A/AppData/Local/Microsoft/Windows/Fonts") in paths


def test_windows_subtitle_filter_uses_system_font_fallback() -> None:
    """Un chemin de police Windows ne doit pas casser le filtergraph FFmpeg."""
    from core.export_engine import _subtitle_fontsdir

    assert _subtitle_fontsdir(platform_name="win32") is None


def test_windows_filter_path_is_safe_in_a_quoted_filename_value() -> None:
    """Les chemins Windows deviennent compatibles avec ``filename='…'``."""
    from core.export_engine import _escape_filter_path

    assert _escape_filter_path(r"C:\Users\Runner\subtitle.srt") == (
        r"C\:/Users/Runner/subtitle.srt"
    )


def test_an_apostrophe_in_a_filter_path_closes_and_reopens_the_quoted_value() -> None:
    from core.export_engine import _escape_filter_path

    assert _escape_filter_path("/home/Jean d'Arc/s.srt") == "/home/Jean d'\\\\\\''Arc/s.srt"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg absent")
@pytest.mark.parametrize("folder", ["plain dir", "Jean d'Arc", "O'Brien's cut", "café été", "a,b[c];d"])
# Les sous-titres exigent libass : marque ``libass`` (sautée sans libass, échouée avec KUT_STUDIO_REQUIRE_LIBASS=1,
# voir tests/ffmpeg_caps.py). Ce n'est pas une régression de l'application : elle détecte l'absence de libass
# et refuse avec un message clair (build FFmpeg sans libass, ex. celle du runner macOS de la CI).
@pytest.mark.parametrize("kind", [pytest.param("subtitles", marks=pytest.mark.libass), "lut3d"])
def test_ffmpeg_opens_subtitle_and_lut_files_whatever_the_folder_name(tmp_path, folder, kind) -> None:
    """Régression : un dossier avec apostrophe (profil Windows « O'Brien ») cassait sous-titres et LUT."""
    from core.export_engine import _escape_filter_path

    directory = tmp_path / folder
    directory.mkdir()
    if kind == "subtitles":
        path = directory / "s.srt"
        path.write_text("1\n00:00:00,000 --> 00:00:00,500\nHi\n", encoding="utf-8")
        video_filter = f"subtitles=filename='{_escape_filter_path(str(path))}'"
    else:
        path = directory / "id.cube"
        rows = "\n".join(f"{r} {g} {b}" for b in (0, 1) for g in (0, 1) for r in (0, 1))
        path.write_text(f"LUT_3D_SIZE 2\n{rows}\n", encoding="utf-8")
        video_filter = f"lut3d=file='{_escape_filter_path(str(path))}'"
    completed = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=0.2",
         "-vf", video_filter, "-f", "null", "-"],
        capture_output=True, text=True, timeout=60,
    )
    assert completed.returncode == 0, completed.stderr


def test_bundled_media_tools_win_before_path(tmp_path) -> None:
    from core.tool_paths import bundled_tool_path

    binary = tmp_path / "bin" / "ffmpeg.exe"
    binary.parent.mkdir()
    binary.write_bytes(b"fake")
    assert bundled_tool_path(
        "ffmpeg",
        platform_name="win32",
        environment={},
        bundle_root=tmp_path,
    ) == str(binary.resolve())


def test_explicit_media_tool_override(tmp_path) -> None:
    from core.tool_paths import bundled_tool_path

    binary = tmp_path / "ffprobe"
    binary.write_bytes(b"fake")
    assert bundled_tool_path(
        "ffprobe",
        platform_name="linux",
        environment={"KUT_STUDIO_FFPROBE": str(binary)},
    ) == str(binary.resolve())


DOCK_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
"""Le ``PATH`` que ``launchd`` donne à une application lancée depuis le Dock ou le Finder."""


def test_extend_search_path_appends_existing_package_manager_dirs(tmp_path, monkeypatch) -> None:
    from core import tool_paths

    brew, missing = tmp_path / "homebrew" / "bin", tmp_path / "absent" / "bin"
    brew.mkdir(parents=True)
    monkeypatch.setattr(tool_paths, "_CONVENTIONAL_TOOL_DIRS", {"darwin": (str(brew), str(missing))})
    environment = {"PATH": DOCK_PATH}
    assert tool_paths.extend_search_path(platform_name="darwin", environment=environment) == (str(brew),)
    # À la suite : les outils du système gardent la priorité ; un dossier absent n'est pas ajouté.
    assert environment["PATH"] == os.pathsep.join([*DOCK_PATH.split(os.pathsep), str(brew)])


def test_extend_search_path_is_idempotent_and_keeps_a_complete_path(tmp_path, monkeypatch) -> None:
    from core import tool_paths

    brew = tmp_path / "bin"
    brew.mkdir()
    monkeypatch.setattr(tool_paths, "_CONVENTIONAL_TOOL_DIRS", {"darwin": (str(brew),)})
    complete = os.pathsep.join([str(brew), DOCK_PATH])  # lancement depuis un terminal
    environment = {"PATH": complete}
    assert tool_paths.extend_search_path(platform_name="darwin", environment=environment) == ()
    assert environment["PATH"] == complete
    environment = {"PATH": DOCK_PATH}
    tool_paths.extend_search_path(platform_name="darwin", environment=environment)
    assert tool_paths.extend_search_path(platform_name="darwin", environment=environment) == ()
    assert environment["PATH"].split(os.pathsep).count(str(brew)) == 1


@pytest.mark.parametrize("path", [":/usr/bin", "/usr/bin:", "/usr/bin::/bin"])
def test_extend_search_path_preserves_empty_components(path, tmp_path, monkeypatch) -> None:
    """Un composant vide du PATH est le dossier courant (POSIX) : l'ajout ne doit pas le retirer."""
    from core import tool_paths

    brew = tmp_path / "bin"
    brew.mkdir()
    monkeypatch.setattr(tool_paths, "_CONVENTIONAL_TOOL_DIRS", {"darwin": (str(brew),)})
    environment = {"PATH": path}
    assert tool_paths.extend_search_path(platform_name="darwin", environment=environment) == (str(brew),)
    assert environment["PATH"] == f"{path}{os.pathsep}{brew}"


def test_extend_search_path_handles_an_empty_path_and_other_platforms(tmp_path, monkeypatch) -> None:
    from core import tool_paths

    brew = tmp_path / "bin"
    brew.mkdir()
    monkeypatch.setattr(tool_paths, "_CONVENTIONAL_TOOL_DIRS", {"darwin": (str(brew),)})
    environment: dict[str, str] = {}
    assert tool_paths.extend_search_path(platform_name="darwin", environment=environment) == (str(brew),)
    assert environment["PATH"] == str(brew)  # pas de séparateur parasite devant
    environment = {"PATH": ""}
    assert tool_paths.extend_search_path(platform_name="darwin", environment=environment) == (str(brew),)
    assert environment["PATH"] == str(brew)
    untouched = {"PATH": "C:\\Windows\\System32"}
    assert tool_paths.extend_search_path(platform_name="win32", environment=untouched) == ()
    assert untouched == {"PATH": "C:\\Windows\\System32"}


@pytest.mark.skipif(sys.platform.startswith("win"), reason="faux exécutable POSIX")
def test_ffmpeg_installed_with_a_package_manager_is_found_from_a_dock_launch(tmp_path, monkeypatch) -> None:
    """Régression : « FFmpeg est introuvable » alors qu'il est installé (Homebrew), depuis le Dock."""
    from core import tool_paths

    brew, system = tmp_path / "bin", tmp_path / "usr-bin"
    brew.mkdir()
    system.mkdir()
    ffmpeg = brew / "ffmpeg"
    ffmpeg.write_text("#!/bin/sh\n")
    ffmpeg.chmod(0o755)
    monkeypatch.setattr(tool_paths, "_CONVENTIONAL_TOOL_DIRS", {sys.platform: (str(brew),)})
    for variable in ("KUT_STUDIO_FFMPEG", "KUT_STUDIO_FFMPEG_DIR"):
        monkeypatch.delenv(variable, raising=False)
    # Un PATH « de système » sans FFmpeg : on ne peut pas se fier à /usr/bin, où la CI Linux installe le vrai.
    monkeypatch.setenv("PATH", str(system))
    assert tool_paths.find_media_tool("ffmpeg") is None
    tool_paths.extend_search_path()
    assert tool_paths.find_media_tool("ffmpeg") == str(ffmpeg)


def test_build_command_uses_platform_separator_and_optional_binaries(tmp_path) -> None:
    from build import build_command

    ffmpeg = tmp_path / "ffmpeg.exe"
    ffprobe = tmp_path / "ffprobe.exe"
    ffmpeg.write_bytes(b"fake")
    ffprobe.write_bytes(b"fake")
    command = build_command(
        platform_name="win32",
        environment={"KUT_STUDIO_FFMPEG_DIR": str(tmp_path)},
    )
    data_value = command[command.index("--add-data") + 1]
    binaries = [
        command[index + 1]
        for index, value in enumerate(command)
        if value == "--add-binary"
    ]
    assert ";assets" in data_value
    assert len(binaries) == 2
    assert all(value.endswith(";bin") for value in binaries)
    assert command[-1].endswith("main.py")


def test_build_command_has_macos_bundle_identifier() -> None:
    from build import build_command

    command = build_command(platform_name="darwin", environment={})
    assert "--osx-bundle-identifier" in command
    assert "com.kutstudio.app" in command
