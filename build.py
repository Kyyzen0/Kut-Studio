"""Construction PyInstaller portable de Kut-Studio."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


APP_NAME = "Kut-Studio"
ROOT = Path(__file__).resolve().parent


def _icon_for(platform_name: str) -> Path | None:
    candidates = {
        "darwin": ROOT / "icon.icns",
        "win32": ROOT / "icon.ico",
        "linux": ROOT / "icon-light.png",
    }
    key = "win32" if platform_name.startswith("win") else platform_name
    icon = candidates.get(key)
    return icon if icon is not None and icon.is_file() else None


def _bundled_binaries(platform_name: str, environment: dict[str, str]) -> list[Path]:
    suffix = ".exe" if platform_name.startswith("win") else ""
    directory = environment.get("KUT_STUDIO_FFMPEG_DIR")
    roots = [Path(directory)] if directory else []
    binaries: list[Path] = []
    for name in ("ffmpeg", "ffprobe"):
        explicit = environment.get(f"KUT_STUDIO_{name.upper()}")
        candidates = ([Path(explicit)] if explicit else []) + [
            root / f"{name}{suffix}" for root in roots
        ]
        match = next((path for path in candidates if path.is_file()), None)
        if match is not None:
            binaries.append(match.resolve())
    return binaries


def build_command(
    *,
    platform_name: str | None = None,
    environment: dict[str, str] | None = None,
) -> list[str]:
    """Retourne la commande PyInstaller adaptée au système courant."""
    platform_name = platform_name or sys.platform
    environment = dict(os.environ if environment is None else environment)
    separator = ";" if platform_name.startswith("win") else ":"
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",
        "--distpath",
        str(ROOT / "dist"),
        "--workpath",
        str(ROOT / "build" / "work"),
        "--specpath",
        str(ROOT / "build" / "spec"),
        "--name",
        APP_NAME,
    ]
    assets = ROOT / "assets"
    if assets.is_dir():
        command.extend(["--add-data", f"{assets}{separator}assets"])
    icon = _icon_for(platform_name)
    if icon is not None:
        command.extend(["--icon", str(icon)])
    if platform_name == "darwin":
        command.extend(["--osx-bundle-identifier", "com.kutstudio.app"])
    for binary in _bundled_binaries(platform_name, environment):
        command.extend(["--add-binary", f"{binary}{separator}bin"])
    command.append(str(ROOT / "main.py"))
    return command


def main() -> int:
    (ROOT / "build" / "spec").mkdir(parents=True, exist_ok=True)
    config_dir = ROOT / "build" / ".pyinstaller"
    config_dir.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ)
    environment.setdefault("PYINSTALLER_CONFIG_DIR", str(config_dir))
    subprocess.check_call(build_command(), cwd=ROOT, env=environment)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
