"""Drapeau de construction « Store » (ADR-0013, étape 4) : il est faux par défaut, ne vient que du module généré par
``build.py --flavor store``, coupe toute la chaîne de mise à jour et limite FFmpeg au bundle.

Les tests qui démarrent l'application tournent dans un sous-processus (``tests/store_flavor_driver.py``) : la saveur est
choisie à l'import, et le drapeau est posé comme le fait le bundle, par le module ``core._build_flavor_generated``."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import build
from core import build_flavor, tool_paths
from core.build_flavor import StoreRestrictionError

ROOT = Path(__file__).resolve().parent.parent
DRIVER = Path(__file__).with_name("store_flavor_driver.py")
CHECK_MENU = "Rechercher des mises à jour…"


def _drive(flavor: str, tmp_path: Path) -> dict:
    config = tmp_path / f"cfg-{flavor}"
    config.mkdir()
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", KUT_STUDIO_CONFIG_DIR=str(config), KUT_STUDIO_LANG="fr",
               KUT_STUDIO_UPDATE_CHECK="off", HOME=str(config), USERPROFILE=str(config))
    done = subprocess.run([sys.executable, str(DRIVER), flavor], capture_output=True, text=True, timeout=180,
                          env=env, cwd=config)
    assert done.returncode == 0, done.stderr[-800:]
    return json.loads(done.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def reports(tmp_path_factory):
    base = tmp_path_factory.mktemp("flavor")
    return {flavor: _drive(flavor, base) for flavor in ("source", "store")}


# --- (a) table STORE vrai / faux x chaque point d'entrée --------------------------------------------------------------


def test_menu_help_has_the_check_entry_only_outside_the_store(reports):
    assert CHECK_MENU in reports["source"]["help"] and reports["source"]["has_check_action"]
    assert CHECK_MENU not in reports["store"]["help"] and not reports["store"]["has_check_action"]
    assert reports["store"]["help"] == ["À propos de Kut-Studio"]


def test_toolbar_button_exists_only_outside_the_store(reports):
    assert reports["source"]["notice_button"] is True
    assert reports["store"]["notice_button"] is False


def test_preferences_group_exists_only_outside_the_store(reports):
    assert reports["source"]["prefs_updates_box"] is True
    assert reports["store"]["prefs_updates_box"] is False


def test_command_palette_follows_the_menus(reports):
    """La palette est construite depuis les menus : l'entrée disparaît d'elle-même en Store."""
    def updates(flavor):
        return [t for t in reports[flavor]["palette"] if "mise" in t.lower() and "jour" in t.lower()]

    assert updates("source") == ["Rechercher des mises à jour"]
    assert updates("store") == [] and reports["store"]["palette"]


def test_about_says_mac_app_store_and_has_no_site_or_signature_keys(reports):
    store, source = reports["store"]["about"], reports["source"]["about"]
    assert "Mac App Store" in store
    assert "https://" not in store and "Clés de signature" not in store and "Installation" not in store
    assert "Mac App Store" not in source and "Clés de signature" in source


def test_direct_calls_and_signals_do_nothing_in_the_store(reports):
    """``check_for_updates``, ``open_update_offer`` et le signal de la préférence atteignent le modèle sans rien changer."""
    assert reports["store"]["check_enabled_after_signal"] is False


# --- (b) aucun module de mise à jour importé au démarrage -----------------------------------------------------------------


def test_store_startup_imports_no_update_module(reports):
    assert reports["store"]["modules"] == []


def test_source_startup_still_imports_the_update_chain(reports):
    """Garde-fou du test précédent : hors Store, la même mesure voit bien les modules (elle n'est pas aveugle)."""
    assert {"core.updates", "core.update_service", "ui.update_dialog"} <= set(reports["source"]["modules"])


def test_adr_module_list_is_the_one_checked(reports):
    adr = (ROOT / "docs" / "adr" / "0013-publication-mac-app-store.md").read_text(encoding="utf-8")
    from store_flavor_driver import UPDATE_MODULES

    for module in ("updates", "update_service", "update_install", "update_helper", "update_keys", "update_signature",
                   "update_auto_install"):
        assert module in adr and any(module == m.rsplit(".", 1)[-1] for m in UPDATE_MODULES)
    assert set(build.STORE_EXCLUDED_MODULES) == set(UPDATE_MODULES)


# --- (c) hors Store : valeur par défaut, aucune surcharge possible ---------------------------------------------------------


def test_default_is_not_store_and_environment_cannot_change_it(monkeypatch):
    assert build_flavor.STORE is False and build_flavor.is_store() is False
    for name in ("KUT_STUDIO_STORE", "KUT_STUDIO_FLAVOR", "KUT_STUDIO_FLAVOUR", "STORE", "KUT_STUDIO_BUILD_FLAVOR"):
        monkeypatch.setenv(name, "1")
    assert build_flavor._generated_store() is False


def test_require_not_store_only_refuses_in_the_store(monkeypatch):
    build_flavor.require_not_store("x")
    monkeypatch.setattr(build_flavor, "STORE", True)
    with pytest.raises(StoreRestrictionError):
        build_flavor.require_not_store("x")


def test_generated_module_is_the_only_source_and_only_true_counts(monkeypatch):
    """Seul ``STORE = True`` (booléen) du module généré compte ; une chaîne « 1 » ou « true » ne suffit pas."""
    import types

    fake = types.ModuleType("core._build_flavor_generated")
    monkeypatch.setitem(sys.modules, "core._build_flavor_generated", fake)
    assert build_flavor._generated_store() is False, "module sans STORE"
    fake.STORE = "true"                                           # type: ignore[attr-defined]
    assert build_flavor._generated_store() is False
    fake.STORE = True                                             # type: ignore[attr-defined]
    assert build_flavor._generated_store() is True
    assert build_flavor.STORE is False, "la valeur publiée est figée à l'import"


# --- gardes côté modèle : le service refuse même appelé directement --------------------------------------------------------


def test_update_chain_refuses_in_the_store(monkeypatch, tmp_path):
    from core import update_helper, update_install, update_service, updates, user_settings
    from core.versioning import Version

    monkeypatch.setattr(build_flavor, "STORE", True)
    monkeypatch.delenv("KUT_STUDIO_UPDATE_CHECK", raising=False)
    assert updates.automatic_checks_allowed({}) is False, "même sans KUT_STUDIO_UPDATE_CHECK=off"
    assert updates.automatic_checks_allowed({"KUT_STUDIO_UPDATE_CHECK": "on"}) is False
    with pytest.raises(StoreRestrictionError):
        update_service.HttpClient()
    with pytest.raises(StoreRestrictionError):
        update_helper.launch_helper(object())                     # type: ignore[arg-type]
    with pytest.raises(StoreRestrictionError):
        update_install.stage_update(tmp_path / "a.zip", tmp_path / "K.app", expected_version=Version.parse("1.0.0"),
                                    installed_version=Version.parse("0.9.0"), required_arch="arm64", pid=1,
                                    signature_verified=True)
    assert user_settings._coerce_skipped_version("1.2.3") == "1.2.3"       # conservé, pas effacé (hors Store il servait)
    assert user_settings._coerce_skipped_version("pas une version !") == ""
    assert user_settings._coerce_skipped_version(123) == ""
    assert user_settings._coerce_skipped_version("1" * 65) == ""


def test_checker_and_downloader_refuse_in_the_store(monkeypatch):
    from core import update_service

    checker = update_service.UpdateChecker.__new__(update_service.UpdateChecker)
    downloader = update_service.UpdateDownloader.__new__(update_service.UpdateDownloader)
    monkeypatch.setattr(build_flavor, "STORE", True)
    with pytest.raises(StoreRestrictionError):
        update_service.UpdateChecker.check(checker, mode=None, include_prereleases=False, skipped_version=None)  # type: ignore[arg-type]
    with pytest.raises(StoreRestrictionError):
        update_service.UpdateDownloader.start(downloader, object())  # type: ignore[arg-type]


def test_outside_the_store_the_guards_do_not_interfere(monkeypatch):
    from core import updates

    assert updates.automatic_checks_allowed({}) is True
    assert updates.automatic_checks_allowed({"KUT_STUDIO_UPDATE_CHECK": "off"}) is False


def test_store_keeps_the_four_update_settings_untouched():
    from types import SimpleNamespace

    from ui.main_window_mixins.updates_store import UpdatesMixin

    mixin = UpdatesMixin()
    settings = SimpleNamespace(check_updates=True, include_prereleases=True, skipped_update_version="1.2.3",
                               last_update_check=12.5)
    mixin._init_updates(settings)
    assert mixin._update_settings_fields() == {"check_updates": True, "include_prereleases": True,
                                               "skipped_update_version": "1.2.3", "last_update_check": 12.5}


def test_store_mixin_covers_every_public_method_the_window_calls():
    from ui.main_window_mixins.updates import UpdatesMixin as Full
    from ui.main_window_mixins.updates_store import UpdatesMixin as Lean

    needed = {name for name in vars(Full) if not name.startswith("__") and callable(getattr(Full, name))}
    external = {"_init_updates", "_update_settings_fields", "_apply_update_settings", "set_update_check_enabled",
                "set_update_include_prereleases", "check_for_updates", "open_update_offer", "_build_update_notice",
                "_retranslate_update_notice", "_shutdown_updates", "about_text", "show_about"}
    assert external <= needed
    assert external <= {name for name in vars(Lean)}


# --- (d) FFmpeg : le bundle seul --------------------------------------------------------------------------------------------


def _fake_bundle(root: Path, names=("ffmpeg", "ffprobe")) -> Path:
    (root / "bin").mkdir(parents=True)
    for name in names:
        tool = root / "bin" / name
        tool.write_text("#!/bin/sh\n")
        tool.chmod(0o755)
    return root


def _foreign_tools(tmp_path: Path, monkeypatch) -> dict[str, Path]:
    """Un FFmpeg « externe » partout où l'on en cherche d'habitude : variables, PATH, dossier Homebrew simulé."""
    foreign = _fake_bundle(tmp_path / "foreign")
    path_dir = foreign / "bin"
    monkeypatch.setenv("PATH", str(path_dir))
    monkeypatch.setenv("KUT_STUDIO_FFMPEG_DIR", str(path_dir))
    monkeypatch.setenv("KUT_STUDIO_FFMPEG", str(path_dir / "ffmpeg"))
    monkeypatch.setenv("KUT_STUDIO_FFPROBE", str(path_dir / "ffprobe"))
    return {"dir": path_dir}


def test_outside_the_store_environment_and_path_still_win(tmp_path, monkeypatch):
    foreign = _foreign_tools(tmp_path, monkeypatch)
    assert tool_paths.find_media_tool("ffmpeg") == str((foreign["dir"] / "ffmpeg").resolve())
    assert tool_paths.bundled_tool_path("ffprobe") == str((foreign["dir"] / "ffprobe").resolve())


def test_store_resolves_only_the_bundle(tmp_path, monkeypatch):
    _foreign_tools(tmp_path, monkeypatch)
    bundle = _fake_bundle(tmp_path / "App")
    monkeypatch.setattr(build_flavor, "STORE", True)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    assert tool_paths.find_media_tool("ffmpeg") == str((bundle / "bin" / "ffmpeg").resolve())
    assert tool_paths.find_media_tool("ffprobe") == str((bundle / "bin" / "ffprobe").resolve())
    assert tool_paths.require_media_tool("ffmpeg") == str((bundle / "bin" / "ffmpeg").resolve())


def test_store_without_bundle_binary_fails_clearly_and_never_falls_back(tmp_path, monkeypatch):
    _foreign_tools(tmp_path, monkeypatch)
    monkeypatch.setattr(build_flavor, "STORE", True)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    fallback = tmp_path / "fallback"
    fallback.mkdir()
    for name in ("ffmpeg", "ffprobe"):
        (fallback / name).write_text("#!/bin/sh\n")
        (fallback / name).chmod(0o755)
    monkeypatch.setitem(tool_paths._CONVENTIONAL_TOOL_DIRS, sys.platform, (str(fallback),))
    assert tool_paths.find_media_tool("ffmpeg") is None, "ni PATH, ni variables, ni Homebrew"
    assert tool_paths.find_media_tool("ffprobe", fallback_dirs=[str(fallback)]) is None
    with pytest.raises(tool_paths.MissingBundledToolError, match="Mac App Store"):
        tool_paths.require_media_tool("ffmpeg")
    assert "Mac App Store" in tool_paths.missing_tool_message("FFmpeg", "défaut")
    assert tool_paths.extend_search_path(platform_name="darwin", environment={"PATH": "/usr/bin"}) == ()


def test_outside_the_store_missing_tool_message_is_unchanged(monkeypatch):
    assert tool_paths.missing_tool_message("FFmpeg", "défaut") == "défaut"
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(tool_paths, "find_media_tool", lambda name, **_: None)
    with pytest.raises(FileNotFoundError):
        tool_paths.require_media_tool("ffmpeg")


def test_export_and_film_report_a_bundle_problem_in_the_store(monkeypatch):
    from core import export_engine, film_render

    monkeypatch.setattr(build_flavor, "STORE", True)
    monkeypatch.setattr(export_engine, "_ffmpeg_path", None)
    monkeypatch.setattr(export_engine, "find_media_tool", lambda name: None)
    with pytest.raises(ImportError, match="Mac App Store"):
        export_engine.require_ffmpeg()
    monkeypatch.setattr("core.tool_paths.find_media_tool", lambda name, **_: None)
    with pytest.raises(film_render.FilmError, match="Mac App Store"):
        film_render.ffmpeg_tool()


# --- (e) build.py --flavor -----------------------------------------------------------------------------------------------


def test_flavor_module_source_is_pure_and_strict():
    assert build.flavor_module_source("store").splitlines()[-1] == "STORE = True"
    assert build.flavor_module_source("developer-id").splitlines()[-1] == "STORE = False"
    with pytest.raises(ValueError):
        build.flavor_module_source("beta")


def test_prepare_flavor_module_writes_for_store_and_removes_a_stale_file(tmp_path):
    path = tmp_path / "_build_flavor_generated.py"
    assert build.prepare_flavor_module("store", path) == path
    namespace: dict[str, object] = {}
    exec(path.read_text(encoding="utf-8"), namespace)
    assert namespace["STORE"] is True
    assert build.prepare_flavor_module("developer-id", path) is None
    assert not path.exists(), "un reste d'une construction Store ne doit pas contaminer une construction Developer ID"


def test_generated_module_is_not_in_the_repository():
    assert not build.FLAVOR_MODULE.exists()
    ignored = subprocess.run(["git", "check-ignore", "-q", "core/_build_flavor_generated.py"], cwd=ROOT)
    assert ignored.returncode == 0


def test_default_command_is_unchanged_and_store_command_adds_flag_and_exclusions():
    default = build.build_command(platform_name="darwin", environment={})
    assert default == build.build_command(platform_name="darwin", environment={}, flavor="developer-id")
    assert "--hidden-import" not in default and "--exclude-module" not in default
    store = build.build_command(platform_name="darwin", environment={}, flavor="store")
    assert store[store.index("--hidden-import") + 1] == "core._build_flavor_generated"
    excluded = {store[i + 1] for i, part in enumerate(store) if part == "--exclude-module"}
    assert excluded == set(build.STORE_EXCLUDED_MODULES)
    assert "--codesign-identity" not in store and not any(str(part).endswith(".pkg") for part in store)
    with pytest.raises(ValueError):
        build.build_command(flavor="inconnue")


def test_store_build_requires_ffmpeg_and_ffprobe_in_the_bundle(tmp_path):
    assert "ffprobe" in (build.store_bundle_problem([tmp_path / "ffmpeg"]) or "")
    assert build.store_bundle_problem([]) is not None
    assert build.store_bundle_problem([tmp_path / "ffmpeg", tmp_path / "ffprobe"]) is None
    assert build.store_bundle_problem([tmp_path / "ffmpeg.exe", tmp_path / "ffprobe.exe"]) is None


def test_main_store_refuses_without_ffmpeg_and_always_removes_the_module(monkeypatch, capsys):
    monkeypatch.delenv("KUT_STUDIO_FFMPEG_DIR", raising=False)
    monkeypatch.delenv("KUT_STUDIO_FFMPEG", raising=False)
    monkeypatch.delenv("KUT_STUDIO_FFPROBE", raising=False)
    assert build.main(["--flavor", "store"]) == 2
    assert "KUT_STUDIO_FFMPEG_DIR" in capsys.readouterr().err
    assert not build.FLAVOR_MODULE.exists()


def test_main_removes_the_generated_module_even_when_the_build_fails(monkeypatch, tmp_path):
    for name in ("ffmpeg", "ffprobe"):
        (tmp_path / name).write_text("")
    monkeypatch.setenv("KUT_STUDIO_FFMPEG_DIR", str(tmp_path))
    seen: list[bool] = []

    def failing(flavor):
        seen.append(build.FLAVOR_MODULE.exists())
        raise RuntimeError("PyInstaller a échoué")

    monkeypatch.setattr(build, "_build", failing)
    with pytest.raises(RuntimeError):
        build.main(["--flavor", "store"])
    assert seen == [True] and not build.FLAVOR_MODULE.exists()


def test_parse_arguments_defaults_to_developer_id():
    assert build.parse_arguments([]).flavor == "developer-id"
    assert build.parse_arguments(["--flavor", "store"]).flavor == "store"
    with pytest.raises(SystemExit):
        build.parse_arguments(["--flavor", "autre"])
