"""Construction PyInstaller portable de Kut-Studio.

La version vient de ``core/app_version.py`` (source unique) : sous macOS elle est écrite dans ``Info.plist``, puis le
bundle est re-signé (l'``Info.plist`` fait partie du sceau de la signature). Avec ``KUT_STUDIO_CODESIGN_IDENTITY``
(identité « Developer ID Application: … » présente dans un trousseau), PyInstaller signe chaque binaire avec le
runtime renforcé et ``tools/release/entitlements.plist`` ; sans elle, la signature reste ad hoc. La notarisation est
une étape séparée du workflow de release (voir ``docs/updates.md``).
"""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from core.app_version import APP_NAME, APP_VERSION
from core.versioning import Version

ROOT = Path(__file__).resolve().parent
ENTITLEMENTS = ROOT / "tools" / "release" / "entitlements.plist"
CODESIGN_IDENTITY_ENV = "KUT_STUDIO_CODESIGN_IDENTITY"
MICROPHONE_USAGE = "Kut-Studio uses the microphone only while you record a voice-over."
"""Exigé par macOS pour l'enregistrement de voix off (et par le runtime renforcé d'une application notarisée)."""


def _icon_for(platform_name: str) -> Path | None:
    icons = ROOT / "assets" / "icons"
    candidates = {
        "darwin": icons / "icon.icns",
        "win32": icons / "icon.ico",  # généré par ``python -m tools.make_icons``
        "linux": icons / "icon-light.png",
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
        identity = environment.get(CODESIGN_IDENTITY_ENV, "").strip()
        if identity:
            command.extend(["--codesign-identity", identity, "--osx-entitlements-file", str(ENTITLEMENTS)])
    for binary in _bundled_binaries(platform_name, environment):
        command.extend(["--add-binary", f"{binary}{separator}bin"])
    command.append(str(ROOT / "main.py"))
    return command


def macos_info_overrides(version: str = APP_VERSION) -> dict[str, str]:
    """Entrées d'``Info.plist`` ajoutées au bundle construit."""
    parsed = Version.parse(version)
    return {
        "CFBundleShortVersionString": str(parsed),
        # Numérique seulement (MAJEUR.MINEUR.CORRECTIF) : macOS compare ce champ entre deux versions.
        "CFBundleVersion": parsed.core,
        "NSMicrophoneUsageDescription": MICROPHONE_USAGE,
    }


def codesign_command(app: Path, identity: str | None, entitlements: Path | None) -> list[str]:
    """Re-signature du bundle (sceau d'``Info.plist`` et des ressources) ; les binaires internes restent signés."""
    if not identity:
        return ["codesign", "--force", "--sign", "-", str(app)]
    command = ["codesign", "--force", "--sign", identity, "--options", "runtime", "--timestamp"]
    if entitlements is not None:
        command += ["--entitlements", str(entitlements)]
    return command + [str(app)]


def finalize_macos_bundle(
    app: Path,
    *,
    version: str = APP_VERSION,
    identity: str | None = None,
    entitlements: Path | None = None,
    run: Callable[[Sequence[str]], object] = subprocess.check_call,
) -> None:
    """Écrit la version dans ``Info.plist``, re-signe le bundle, puis vérifie sa signature (échec = construction ratée)."""
    info_path = app / "Contents" / "Info.plist"
    with info_path.open("rb") as stream:
        info = plistlib.load(stream)
    info.update(macos_info_overrides(version))
    with info_path.open("wb") as stream:
        plistlib.dump(info, stream)
    run(codesign_command(app, identity, entitlements))
    run(["codesign", "--verify", "--deep", "--strict", "--verbose=2", str(app)])


def main() -> int:
    (ROOT / "build" / "spec").mkdir(parents=True, exist_ok=True)
    config_dir = ROOT / "build" / ".pyinstaller"
    config_dir.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ)
    environment.setdefault("PYINSTALLER_CONFIG_DIR", str(config_dir))
    subprocess.check_call(build_command(), cwd=ROOT, env=environment)
    if sys.platform == "darwin":
        identity = environment.get(CODESIGN_IDENTITY_ENV, "").strip() or None
        finalize_macos_bundle(
            ROOT / "dist" / f"{APP_NAME}.app",
            identity=identity,
            entitlements=ENTITLEMENTS if identity else None,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
