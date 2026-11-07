"""Whisper absent de la version Store (ADR-0013, étape 8) : aucune détection de programme, aucun sous-processus, par
aucune entrée (interface, palette, préférences, raccourcis, tâche, ligne de commande) ; rien ne change hors Store.

La construction Store est simulée comme dans ``test_build_flavor_store.py`` : un sous-processus (``store_flavor_driver.py``)
pose ``core._build_flavor_generated`` avant d'importer l'application ; les tests de module changent ``STORE`` en place."""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import build
import main as application
from core import build_flavor, shortcuts, transcription
from core.auto_captions import CaptionLine, add_subtitle_lines
from core.build_flavor import StoreRestrictionError
from core.project_io import load_project, save_project
from core.project_model import Project
from core.render_plan import build_render_plan
from core.user_settings import UserSettings, load_user_settings, save_user_settings
from ui import i18n

ROOT = Path(__file__).resolve().parent.parent
DRIVER = Path(__file__).with_name("store_flavor_driver.py")


def _drive(flavor: str, tmp_path: Path) -> dict:
    config = tmp_path / f"cfg-{flavor}"
    config.mkdir()
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", KUT_STUDIO_CONFIG_DIR=str(config), KUT_STUDIO_LANG="fr",
               KUT_STUDIO_UPDATE_CHECK="off", HOME=str(config), USERPROFILE=str(config),
               KUT_STUDIO_WHISPER=sys.executable, KUT_STUDIO_WHISPER_MODEL=sys.executable)
    done = subprocess.run([sys.executable, str(DRIVER), flavor], capture_output=True, text=True, timeout=180,
                          env=env, cwd=config)
    assert done.returncode == 0, done.stderr[-800:]
    return json.loads(done.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def reports(tmp_path_factory):
    base = tmp_path_factory.mktemp("whisper-flavor")
    return {flavor: _drive(flavor, base) for flavor in ("source", "store")}


@pytest.fixture
def store(monkeypatch):
    monkeypatch.setattr(build_flavor, "STORE", True)


def _mentions(titles: list[str]) -> list[str]:
    needles = [i18n.translate(key) for key in ("transcription.menu.title", "transcription.menu.subtitles",
                                                  "transcription.menu.karaoke")]
    return [title for title in titles if any(needle in title for needle in needles)]


# --- l'application complète, avec et sans Store ------------------------------------------------------------------------


def test_the_menu_has_the_commands_only_outside_the_store(reports):
    i18n.set_language("fr")
    assert len(_mentions(reports["source"]["social_titles"])) == 3
    assert _mentions(reports["store"]["social_titles"]) == []


def test_the_palette_has_the_commands_only_outside_the_store(reports):
    assert len(_mentions(reports["source"]["palette_titles"])) == 2
    assert _mentions(reports["store"]["palette_titles"]) == []


def test_the_shortcuts_exist_only_outside_the_store(reports):
    assert {"auto_subtitles", "auto_karaoke"} <= set(reports["source"]["command_ids"])
    assert not {"auto_subtitles", "auto_karaoke"} & set(reports["store"]["command_ids"])
    assert reports["source"]["missing_handlers"] == [] and reports["store"]["missing_handlers"] == []


def test_the_preferences_group_exists_only_outside_the_store(reports):
    assert reports["source"]["prefs_transcription_box"] is True
    assert reports["store"]["prefs_transcription_box"] is False


def test_the_program_is_not_looked_for_in_the_store_even_when_everything_is_set(reports):
    assert reports["source"]["found_tool"] and reports["source"]["found_model"]       # réglage et variables : trouvés
    assert reports["store"]["found_tool"] is None and reports["store"]["found_model"] is None


def test_direct_calls_start_nothing_and_say_why_in_the_store(reports):
    assert reports["store"]["spawned"] == 0
    assert reports["store"]["job_refused"] is True
    assert reports["store"]["status"] == i18n.translate("transcription.message.store_unavailable")
    assert reports["store"]["whisper_setting_kept"] is True            # un signal de préférences n'écrit rien
    assert reports["source"]["job_refused"] is False and reports["source"]["whisper_setting_kept"] is False


def test_the_command_line_is_neither_started_nor_imported_in_the_store(reports):
    assert reports["store"]["cli_modules"] == []
    assert reports["store"]["cli_requested"] is False
    assert reports["source"]["cli_modules"] == ["core.cli"] and reports["source"]["cli_requested"] is True


def test_the_message_exists_in_the_three_languages():
    texts = {lang: i18n.translate_strict("transcription.message.store_unavailable", lang) for lang in ("fr", "en", "es")}
    assert len(set(texts.values())) == 3 and all(texts.values())


# --- le moteur et la tâche ---------------------------------------------------------------------------------------------


def test_discovery_touches_nothing_in_the_store(store, monkeypatch, tmp_path):
    program = tmp_path / "whisper-cli"
    program.write_text("#!/bin/sh\n")
    program.chmod(0o755)
    model = tmp_path / "ggml-x.bin"
    model.write_bytes(b"0")
    monkeypatch.setenv(transcription.WHISPER_ENV, str(program))
    monkeypatch.setenv(transcription.MODEL_ENV, str(model))

    def forbidden(*_a, **_k):
        raise AssertionError("aucune recherche attendue")

    monkeypatch.setattr(transcription.shutil, "which", forbidden)
    monkeypatch.setattr(transcription, "bundled_tool_path", forbidden)
    assert transcription.find_whisper(str(program)) is None
    assert transcription.find_whisper_model(str(model)) is None
    assert transcription.transcription_available() is False


def test_the_job_cannot_be_built_nor_started_in_the_store(store, monkeypatch):
    with pytest.raises(StoreRestrictionError):
        transcription.TranscriptionJob("ffmpeg", "w", "m", "a.wav", start=0.0, duration=1.0)

    def forbidden(*_a, **_k):
        raise AssertionError("aucun sous-processus attendu")

    monkeypatch.setattr(transcription, "supervised_popen", forbidden)
    job = object.__new__(transcription.TranscriptionJob)                 # contourne le constructeur : dernier rempart
    with pytest.raises(StoreRestrictionError):
        job._execute(["whisper-cli"], 0.0, 1.0)


def test_outside_the_store_discovery_and_the_job_are_unchanged(monkeypatch, tmp_path):
    program = tmp_path / "whisper-cli"
    program.write_text("#!/bin/sh\n")
    program.chmod(0o755)
    model = tmp_path / "ggml-x.bin"
    model.write_bytes(b"0")
    assert transcription.transcription_available() is True
    assert transcription.find_whisper(str(program)) == str(program.resolve())
    assert transcription.find_whisper_model(str(model)) == str(model.resolve())
    transcription.TranscriptionJob("ffmpeg", "w", "m", "a.wav", start=0.0, duration=1.0)


def test_shortcut_map_drops_the_commands_only_in_the_store(monkeypatch):
    absent = shortcuts.STORE_ABSENT_COMMANDS
    assert absent <= {c.id for c in shortcuts.ShortcutMap().commands}
    monkeypatch.setattr(build_flavor, "STORE", True)
    assert not absent & {c.id for c in shortcuts.ShortcutMap().commands}
    assert absent <= {c.id for c in shortcuts.ShortcutMap(commands=shortcuts.COMMANDS).commands}   # liste explicite : inchangée


# --- ce qui existe déjà reste utilisable -------------------------------------------------------------------------------


def test_a_project_with_generated_subtitles_opens_and_exports_in_the_store(store, tmp_path):
    project = Project(name="Déjà sous-titré", width=1080, height=1920)
    add_subtitle_lines(project, [CaptionLine(1.0, 2.0, "bonjour tout le monde", (0.0, 0.3, 0.6)),
                                 CaptionLine(2.0, 3.0, "deuxième ligne", (0.0, 0.4))])
    path = tmp_path / "p.kut"
    save_project(project, str(path))
    loaded = load_project(str(path))
    texts = [clip.text for track in loaded.tracks if track.type == "subtitle" for clip in track.clips]
    assert texts == ["bonjour tout le monde", "deuxième ligne"]
    plan = build_render_plan(loaded)
    assert [cue.text for cue in plan.subtitle_cues] == texts


def test_the_whisper_settings_survive_a_save_in_the_store(store, tmp_path):
    save_user_settings(UserSettings(whisper_path="/opt/whisper-cli", whisper_model="/m/ggml-base.bin"), tmp_path)
    settings = load_user_settings(tmp_path)
    assert (settings.whisper_path, settings.whisper_model) == ("/opt/whisper-cli", "/m/ggml-base.bin")


# --- build et point d'entrée -------------------------------------------------------------------------------------------


def _excluded(command: list[str]) -> set[str]:
    return {command[i + 1] for i, token in enumerate(command) if token == "--exclude-module"}


def test_the_store_bundle_leaves_out_the_cli_and_the_script_mode_only():
    store_excluded = _excluded(build.build_command(flavor="store"))
    assert {"core.cli", "core.scripting"} <= store_excluded
    assert "core" not in store_excluded and not any(m.startswith("core.film") for m in store_excluded)
    assert not _excluded(build.build_command(flavor="developer-id"))


def test_main_starts_the_command_line_only_outside_the_store(monkeypatch):
    argv = ["main.py", "info", "projet.kut"]
    assert application.cli_requested(argv) is True
    assert application.cli_requested(["main.py"]) is False
    monkeypatch.setattr(build_flavor, "STORE", True)
    assert application.cli_requested(argv) is False


# --- inventaire : aucun nouveau chemin vers le moteur sans passer par la garde ---------------------------------------


ENGINE_NAMES = {"TranscriptionJob", "find_whisper", "find_whisper_model", "whisper_command"}
GUARDS = {"transcription_available", "is_store", "require_not_store"}
SHIPPED = ("core", "ui", "kut3d")
"""Les paquets livrés dans l'application ; ``tools`` et ``tests`` ne le sont pas."""


def _modules():
    for package in SHIPPED:
        for path in sorted((ROOT / package).rglob("*.py")):
            yield path, ast.parse(path.read_text(encoding="utf-8"))


def _called_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            func = child.func
            names.add(func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else "")
    return names


def _engine_functions(tree: ast.AST):
    """Fonctions qui nomment un nom du moteur (appel ou référence), avec la liste de leurs noms appelés."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            used = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)} | {
                n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}
            if used & ENGINE_NAMES:
                yield node


def test_every_function_reaching_the_engine_goes_through_the_guard():
    offenders = []
    for path, tree in _modules():
        relative = path.relative_to(ROOT).as_posix()
        if relative == "core/transcription.py":
            continue                                                   # le moteur lui-même : vérifié ci-dessous
        for function in _engine_functions(tree):
            if not _called_names(function) & GUARDS:
                offenders.append(f"{relative}:{function.name}")
    assert not offenders, f"appel du moteur Whisper sans garde Store : {offenders}"


def test_the_engine_launches_a_process_or_searches_only_behind_a_guard():
    tree = ast.parse((ROOT / "core" / "transcription.py").read_text(encoding="utf-8"))
    risky = {"supervised_popen", "which", "bundled_tool_path", "glob"}
    required = {"find_whisper", "find_whisper_model", "__init__", "_execute"}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and (_called_names(node) & risky or node.name in required):
            if node.name == "__init__" and not _inside_job(tree, node):
                continue
            assert _called_names(node) & GUARDS or "is_store" in {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}, \
                f"core/transcription.py:{node.name} sans garde Store"


def _inside_job(tree: ast.AST, function: ast.FunctionDef) -> bool:
    return any(isinstance(n, ast.ClassDef) and n.name == "TranscriptionJob" and function in n.body for n in ast.walk(tree))


def test_no_shipped_module_other_than_the_engine_names_the_program():
    offenders = []
    for path, tree in _modules():
        relative = path.relative_to(ROOT).as_posix()
        if relative == "core/transcription.py" or relative.startswith("ui/i18n"):
            continue                                                   # le moteur ; les tables de textes (libellés seuls)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and "whisper-cli" in node.value \
                    and not _is_docstring(tree, node):
                offenders.append(f"{relative}:{node.lineno}")
    assert not offenders, f"« whisper-cli » hors du moteur : {offenders}"


def _is_docstring(tree: ast.AST, constant: ast.Constant) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and first.value is constant:
                return True
    return False


def test_nothing_shipped_imports_the_command_line_or_the_script_mode():
    offenders = []
    for path, tree in _modules():
        relative = path.relative_to(ROOT).as_posix()
        if relative == "core/cli.py" or relative.startswith("core/scripting/"):
            continue
        for node in ast.walk(tree):
            names = [alias.name for alias in node.names] if isinstance(node, ast.Import) else \
                [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            if isinstance(node, ast.ImportFrom) and node.level and node.module is None:
                names = [alias.name for alias in node.names]
            if any(name in ("core.cli", "cli", "core.scripting", "scripting") or name.startswith(("core.scripting.", "scripting."))
                   for name in names):
                offenders.append(f"{relative}:{node.lineno}")
    assert not offenders, f"importé alors que le bundle Store ne le livre pas : {offenders}"
