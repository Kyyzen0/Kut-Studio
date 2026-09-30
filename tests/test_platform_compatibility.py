"""Contrats macOS, Windows et Linux de la tâche 32.5."""

from __future__ import annotations

from pathlib import Path


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

