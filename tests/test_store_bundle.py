"""Bundle Store (ADR-0013, étape 7a) : cohérence des fichiers de packaging, et vérification du ``Kut-Studio.app`` construit.

Le build complet (PyInstaller, ~10 min, FFmpeg LGPL) n'est pas lancé en CI : les tests qui exigent ``dist/Kut-Studio.app``
sautent avec leur raison. Ceux de cohérence (droits, Info.plist) tournent partout."""

from __future__ import annotations

import os
import plistlib
import sys
from pathlib import Path

import pytest

import build
from tools import check_store_bundle as check

ROOT = Path(__file__).resolve().parent.parent
APP = Path(os.environ.get("KUT_STUDIO_STORE_APP", check.DEFAULT_APP)).resolve()
BUNDLE = pytest.mark.skipif(
    sys.platform != "darwin" or not check.executable_of(APP).is_file(),
    reason="bundle Store absent : python build.py --flavor store (macOS, KUT_STUDIO_FFMPEG_DIR = FFmpeg LGPL) puis relancer",
)


def _plist(path: Path) -> dict:
    with path.open("rb") as stream:
        return plistlib.load(stream)


# --- cohérence des fichiers de packaging (partout) ---------------------------------------------------------------------


def test_store_entitlements_are_the_sandbox_set_without_network():
    entitlements = _plist(build.STORE_ENTITLEMENTS)
    assert entitlements == {
        "com.apple.security.app-sandbox": True,
        "com.apple.security.files.user-selected.read-write": True,
        "com.apple.security.files.bookmarks.app-scope": True,
        "com.apple.security.device.audio-input": True,
    }
    # Même contenu que la variante retenue du spike (sans allow-unsigned-executable-memory), jamais de réseau.
    assert entitlements == _plist(ROOT / "packaging" / "store-spike" / "entitlements" / "main-no-ume.plist")
    assert "com.apple.security.network.client" not in entitlements


def test_store_entitlements_cover_what_the_app_asks_of_the_system():
    # L'usage du micro (voix off) exige à la fois le droit et la phrase d'usage dans Info.plist.
    assert _plist(build.STORE_ENTITLEMENTS)["com.apple.security.device.audio-input"] is True
    assert build.macos_info_overrides()["NSMicrophoneUsageDescription"] == build.MICROPHONE_USAGE
    # Les signets à portée d'application (libkutscope) ne marchent qu'avec ce droit.
    assert _plist(build.STORE_ENTITLEMENTS)["com.apple.security.files.bookmarks.app-scope"] is True


def test_store_info_overrides_are_complete_and_consistent_with_the_swift_floor():
    extra = build.store_info_overrides()
    assert extra["LSApplicationCategoryType"] == "public.app-category.video"
    assert extra["LSMinimumSystemVersion"] == "13.0"
    assert not set(extra) & set(build.macos_info_overrides()), "pas de clé définie deux fois"
    for source in (build.STOREKIT_SOURCE, build.SCOPE_SOURCE):
        assert "13.0" in (source / "build.sh").read_text(encoding="utf-8"), f"{source} : plancher macOS différent"


def test_store_entitlements_are_used_for_store_signing_only(tmp_path):
    environment = {build.CODESIGN_IDENTITY_ENV: "Test Identity"}
    store = build.build_command(platform_name="darwin", environment=environment, flavor=build.FLAVOR_STORE)
    developer = build.build_command(platform_name="darwin", environment=environment)
    assert store[store.index("--osx-entitlements-file") + 1] == str(build.STORE_ENTITLEMENTS)
    assert developer[developer.index("--osx-entitlements-file") + 1] == str(build.ENTITLEMENTS)


def test_finalize_merges_store_keys_only_for_the_store_flavor(tmp_path):
    def finalize(flavor: str) -> dict:
        app = tmp_path / flavor / "Kut-Studio.app"
        (app / "Contents").mkdir(parents=True)
        with (app / "Contents" / "Info.plist").open("wb") as stream:
            plistlib.dump({"CFBundleName": "Kut-Studio"}, stream)
        build.finalize_macos_bundle(app, run=lambda command: None, flavor=flavor)
        return _plist(app / "Contents" / "Info.plist")

    assert finalize(build.FLAVOR_STORE)["LSMinimumSystemVersion"] == "13.0"
    assert "LSMinimumSystemVersion" not in finalize(build.FLAVOR_DEVELOPER_ID)


def test_bad_dependency_accepts_only_system_and_internal_paths():
    assert not check.bad_dependency("/usr/lib/libSystem.B.dylib")
    assert not check.bad_dependency("/System/Library/Frameworks/StoreKit.framework/StoreKit")
    assert not check.bad_dependency("@rpath/libswiftCore.dylib")
    assert check.bad_dependency("/opt/homebrew/lib/libx264.dylib")
    assert check.bad_dependency("/Users/someone/build/libkutstore.dylib")


# --- le bundle construit (macOS, après build.py --flavor store) --------------------------------------------------------


@pytest.fixture(scope="module")
def report():
    return check.run_all(APP)


@BUNDLE
def test_store_bundle_passes_every_check(report):
    assert report["problems"] == []


@BUNDLE
def test_store_bundle_flag_and_excluded_modules(report):
    assert report["check_modules"]["store_flag"] is True
    modules = check.embedded_modules(APP)
    for name in check.FORBIDDEN_MODULES:
        assert name not in modules
    # Les exclusions ne sont pas plus larges que prévu : le moteur d'export, les transitions et les mixins de l'interface restent.
    for name in ("core.build_flavor", "core.export_engine", "core.transitions", "core.transcription"):
        assert name in modules, name


@BUNDLE
def test_store_bundle_has_no_hidden_missing_import(report):
    assert report["check_missing_imports"]["absent_du_bundle"] > 0
    assert check.check_missing_imports(APP)[0] == []


@BUNDLE
def test_store_bundle_starts_isolated_without_touching_the_real_config(tmp_path):
    real = Path.home() / "Library" / "Application Support" / "Kut-Studio"

    def snapshot() -> dict[str, float]:
        return {str(p): p.stat().st_mtime for p in real.rglob("*")} if real.exists() else {}

    before = snapshot()
    home, config = tmp_path / "home", tmp_path / "config"
    done = check.run_smoke(APP, home, config)
    assert done.returncode == 0, done.stderr[-800:]
    assert snapshot() == before, "le bundle a touché la vraie configuration"
    created = [p for p in home.rglob("*")]
    assert all(home in p.parents for p in created)  # tout ce qui est écrit reste sous le HOME vide


@BUNDLE
def test_store_bundle_never_calls_a_ffmpeg_found_on_the_path(tmp_path):
    decoys = tmp_path / "decoy"
    decoys.mkdir()
    marker = tmp_path / "DECOY_CALLED"
    for name in ("ffmpeg", "ffprobe"):
        script = decoys / name
        script.write_text(f"#!/bin/sh\ntouch {marker}\n", encoding="utf-8")
        script.chmod(0o755)
    env = check.isolated_environment(tmp_path / "home", tmp_path / "config")
    env["PATH"] = f"{decoys}:{env.get('PATH', '/usr/bin:/bin')}"
    (tmp_path / "home").mkdir()
    done = check._run([str(check.executable_of(APP)), "--smoke-test"], env=env, cwd=str(tmp_path / "home"), timeout=240)
    assert done.returncode == 0, done.stderr[-800:]
    assert not marker.exists(), "le bundle Store a lancé un FFmpeg du PATH"


def test_relative_bundle_path_is_resolved_before_the_cwd_changes(tmp_path, monkeypatch):
    app = tmp_path / "work" / "Kut-Studio.app"
    executable = app / "Contents" / "MacOS" / "Kut-Studio"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.chdir(tmp_path / "work")
    relative = Path("Kut-Studio.app")
    assert check.executable_of(relative).is_absolute()
    done = check.run_smoke(relative, tmp_path / "home", tmp_path / "config")      # cwd du sous-processus = HOME temporaire
    assert done.returncode == 0, done.stderr


def test_smoke_launch_failure_is_a_readable_failure_not_a_traceback(tmp_path):
    done = check.run_smoke(tmp_path / "absent.app", tmp_path / "home", tmp_path / "config")
    assert done.returncode == 1 and "lancement impossible" in done.stderr
