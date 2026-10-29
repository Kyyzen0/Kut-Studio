"""Construction PyInstaller portable de Kut-Studio.

La version vient de ``core/app_version.py`` (source unique) : sous macOS elle est écrite dans ``Info.plist``, puis le
bundle est re-signé (l'``Info.plist`` fait partie du sceau de la signature). Avec ``KUT_STUDIO_CODESIGN_IDENTITY``
(identité « Developer ID Application: … » présente dans un trousseau), PyInstaller signe chaque binaire avec le
runtime renforcé et ``tools/release/entitlements.plist`` ; sans elle, la signature reste ad hoc. La notarisation est
une étape séparée du workflow de release (voir ``docs/updates.md``).

``--flavor store`` (ADR-0013, étape 4) fixe le drapeau ``core.build_flavor.STORE`` dans le bundle : il écrit le petit
module ``core/_build_flavor_generated.py`` avant PyInstaller (jamais une variable d'environnement), l'inclut dans le
bundle, exclut les modules de mise à jour, exige FFmpeg et ffprobe dans le bundle, puis retire le module généré (une
exécution depuis les sources ne devient jamais « Store » par accident). Aucun ``.pkg`` ni signature Store ici : c'est
l'étape 7. ``--flavor developer-id`` (défaut) ne change rien.
"""

from __future__ import annotations

import argparse
import os
import plistlib
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from core.app_version import APP_NAME, APP_VERSION
from core.versioning import BUNDLE_VERSION_KEY, Version

ROOT = Path(__file__).resolve().parent
ENTITLEMENTS = ROOT / "tools" / "release" / "entitlements.plist"
CODESIGN_IDENTITY_ENV = "KUT_STUDIO_CODESIGN_IDENTITY"
FLAVOR_DEVELOPER_ID = "developer-id"
FLAVOR_STORE = "store"
FLAVORS = (FLAVOR_DEVELOPER_ID, FLAVOR_STORE)
FLAVOR_MODULE = ROOT / "core" / "_build_flavor_generated.py"
"""Module généré par ``--flavor store`` (ignoré par git, retiré après la construction)."""
FLAVOR_HIDDEN_IMPORT = "core._build_flavor_generated"
STORE_EXCLUDED_MODULES = (
    "core.updates", "core.update_service", "core.update_install", "core.update_helper", "core.update_keys",
    "core.update_signature", "ui.update_auto_install", "ui.update_dialog", "ui.main_window_mixins.updates",
)
"""Modules de mise à jour absents du bundle Store (la liste de l'ADR-0013 §3 ; ``core.ed25519`` reste, il est générique)."""
MICROPHONE_USAGE = "Kut-Studio uses the microphone only while you record a voice-over."
"""Exigé par macOS pour l'enregistrement de voix off (et par le runtime renforcé d'une application notarisée)."""


def flavor_module_source(flavor: str) -> str:
    """Contenu du module généré pour ``flavor`` ; ``ValueError`` pour une saveur inconnue."""
    if flavor not in FLAVORS:
        raise ValueError(f"saveur inconnue : {flavor!r} (attendu : {', '.join(FLAVORS)})")
    return (
        '"""Généré par ``build.py --flavor`` : ne pas modifier, ne pas versionner (voir core/build_flavor.py)."""\n'
        f"STORE = {flavor == FLAVOR_STORE}\n"
    )


def prepare_flavor_module(flavor: str, path: Path = FLAVOR_MODULE) -> Path | None:
    """Écrit le module généré pour la saveur Store ; le retire sinon (un reste d'une construction Store précédente
    ferait passer à tort un bundle Developer ID pour un bundle Store). Retourne le fichier écrit, ou ``None``."""
    source = flavor_module_source(flavor)
    if flavor == FLAVOR_STORE:
        path.write_text(source, encoding="utf-8")
        return path
    path.unlink(missing_ok=True)
    return None


def store_bundle_problem(binaries: Sequence[Path]) -> str | None:
    """Message d'erreur si la construction Store n'embarque pas à la fois FFmpeg et ffprobe, sinon ``None``."""
    names = {Path(binary).stem for binary in binaries}
    missing = [name for name in ("ffmpeg", "ffprobe") if name not in names]
    if not missing:
        return None
    return (
        f"Construction Store : {' et '.join(missing)} absent(s). La version Store n'utilise que le FFmpeg de son bundle ; "
        "indiquez le dossier du FFmpeg LGPL avec KUT_STUDIO_FFMPEG_DIR (voir docs/ffmpeg-lgpl.md)."
    )


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
    flavor: str = FLAVOR_DEVELOPER_ID,
) -> list[str]:
    """Retourne la commande PyInstaller adaptée au système courant (et à la saveur : Developer ID ou Store)."""
    if flavor not in FLAVORS:
        raise ValueError(f"saveur inconnue : {flavor!r} (attendu : {', '.join(FLAVORS)})")
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
    # Kut-3D (rendu 3D) : importé en différé, donc invisible à l'analyse de PyInstaller ; ses shaders `.qsb` sont des
    # données lues à côté du module (`kut3d/gpu/shader_files.py`) : même chemin relatif dans l'application.
    command.extend(["--collect-submodules", "kut3d"])
    shaders = ROOT / "kut3d" / "gpu" / "shaders"
    command.extend(["--add-data", f"{shaders}{separator}kut3d/gpu/shaders"])
    if flavor == FLAVOR_STORE:
        # Le module généré est importé dans un ``try`` (``core/build_flavor.py``) : l'inclure explicitement ne dépend
        # pas de l'analyse de PyInstaller. Les modules de mise à jour ne sont pas embarqués.
        command.extend(["--hidden-import", FLAVOR_HIDDEN_IMPORT])
        for module in STORE_EXCLUDED_MODULES:
            command.extend(["--exclude-module", module])
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


def macos_info_overrides(version: str = APP_VERSION) -> dict[str, object]:
    """Entrées d'``Info.plist`` ajoutées au bundle construit."""
    parsed = Version.parse(version)
    return {
        # Apple n'accepte que 1 à 3 entiers dans ces deux clés : une préversion (« 0.9.1-beta.1 ») y est ramenée à
        # « 0.9.1 » (une métadonnée invalide ferait rejeter ou mal identifier le bundle signé/notarisé). La version
        # complète vit dans ``KutStudioVersion`` ; l'installation automatique la lit de préférence.
        "CFBundleShortVersionString": parsed.core,
        "CFBundleVersion": parsed.core,
        BUNDLE_VERSION_KEY: str(parsed),
        "NSMicrophoneUsageDescription": MICROPHONE_USAGE,
        # Double-clic et « Ouvrir avec » sur un .kut : le Finder lance l'application, qui reçoit le fichier.
        "CFBundleDocumentTypes": [
            {
                "CFBundleTypeName": "Projet Kut-Studio",
                "CFBundleTypeExtensions": ["kut"],
                "CFBundleTypeRole": "Editor",
                "LSHandlerRank": "Owner",
            }
        ],
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


def parse_arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Construit Kut-Studio avec PyInstaller.")
    parser.add_argument(
        "--flavor", choices=FLAVORS, default=FLAVOR_DEVELOPER_ID,
        help="developer-id (défaut, inchangé) ou store (Mac App Store : sans mises à jour, FFmpeg du bundle seul)",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    flavor = parse_arguments(argv).flavor
    if flavor == FLAVOR_STORE:
        problem = store_bundle_problem(_bundled_binaries(sys.platform, dict(os.environ)))
        if problem:
            print(problem, file=sys.stderr)
            return 2
    try:
        prepare_flavor_module(flavor)
        return _build(flavor)
    finally:
        FLAVOR_MODULE.unlink(missing_ok=True)       # les sources ne restent jamais « Store » après une construction


def _build(flavor: str) -> int:
    (ROOT / "build" / "spec").mkdir(parents=True, exist_ok=True)
    config_dir = ROOT / "build" / ".pyinstaller"
    config_dir.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ)
    environment.setdefault("PYINSTALLER_CONFIG_DIR", str(config_dir))
    subprocess.check_call(build_command(flavor=flavor), cwd=ROOT, env=environment)
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
