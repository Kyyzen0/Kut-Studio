"""Inventaire des accès aux fichiers de l'utilisateur dans le bac à sable (ADR-0013, étape 5, ``docs/sandbox-access.md``).

Contrôles par lecture du code (AST), sans rien exécuter. Un nouveau point d'entrée qui lit ou écrit un fichier de l'utilisateur sans
être déclaré FAIT ÉCHOUER le test et oblige à le classer :

* **chaque dialogue de fichiers** (``QFileDialog.get…``) appelle ``remember_user_path`` dans sa fonction ; un dialogue neuf non déclaré échoue ;
* **chaque source de chemins** hors dialogue (dépôt ``toLocalFile``, ouverture du Finder ``QFileOpenEvent``) est déclarée, avec la
  fonction qui mémorise le chemin ;
* **chaque lecteur / écrivain déclaré** appelle son accès (``scoped_access``, ``scoped_argument``, ``plan_scope``…) ;
* **le lancement de processus** ne se fait que dans ``process_supervisor`` (qui ouvre l'accès des chemins de la commande) et
  ``ExportEngine`` (qui ouvre celui du plan) ; un lancement direct ailleurs échoue ;
* **la façade** n'est utilisée que par la liste de modules déclarée (pas de lecture directe du gestionnaire, qui ignorerait le drapeau Store).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

DIALOG_CALLS = {"getOpenFileName", "getOpenFileNames", "getSaveFileName", "getExistingDirectory", "getExistingDirectoryUrl",
                "getOpenFileUrl", "getOpenFileUrls", "getSaveFileUrl"}

# (fichier, fonction qualifiée) de chaque dialogue : tous appellent ``remember_user_path`` dans la même fonction.
DIALOGS: set[tuple[str, str]] = {
    ("ui/scene3d_actions.py", "add_scene3d_from_dialog"),
    ("ui/transcription_settings.py", "TranscriptionSettingsGroup._choose"),
    ("ui/properties_panel_mixins/color.py", "ColorMixin._request_color_lut_import"),
    ("ui/main_window_mixins/library_organization.py", "LibraryOrganizationMixin._on_asset_relink_requested"),
    ("ui/main_window_mixins/subtitles_graphics.py", "SubtitlesGraphicsMixin.import_graphic_image"),
    ("ui/main_window_mixins/subtitles_graphics.py", "SubtitlesGraphicsMixin.import_subtitles_via_dialog"),
    ("ui/main_window_mixins/subtitles_graphics.py", "SubtitlesGraphicsMixin.export_subtitles_via_dialog"),
    ("ui/main_window_mixins/templates.py", "TemplatesMixin._choose_photos"),
    ("ui/main_window_mixins/project_files.py", "ProjectFilesMixin.save_project_as"),
    ("ui/main_window_mixins/project_files.py", "ProjectFilesMixin.open_project_file"),
    ("ui/main_window_mixins/project_files.py", "ProjectFilesMixin._ask_export_path"),
    ("ui/main_window_mixins/media_import.py", "MediaImportMixin.import_media_via_dialog"),
}

# Sources de chemins hors dialogue : (fichier, fonction) -> (fichier, fonction) qui appelle ``remember_user_path``.
PATH_SOURCES: dict[tuple[str, str], tuple[str, str]] = {
    ("ui/main_window.py", "MainWindow.dropEvent"): ("ui/main_window.py", "MainWindow.dropEvent"),
    ("ui/timeline_panel_mixins/sequences.py", "SequencesTimelineMixin._dropped_payload"):
        ("ui/main_window_mixins/templates.py", "TemplatesMixin.fill_slots_from_photo_drop"),
    ("ui/external_open.py", "_ExternalOpenFilter.eventFilter"):
        ("ui/main_window_mixins/project_files.py", "ProjectFilesMixin.open_external_project"),
}

# (fichier, fonction qualifiée, noms dont l'un doit apparaître dans la fonction — décorateurs compris)
GUARDED: list[tuple[str, str, frozenset[str]]] = [
    ("core/project_io.py", "load_project", frozenset({"scoped_access"})),
    ("core/project_io.py", "save_project", frozenset({"scoped_access"})),
    ("core/project_io.py", "write_project_payload", frozenset({"scoped_access"})),
    ("core/project_io.py", "_atomic_write_json", frozenset({"_write_in_place"})),
    ("core/media_probe.py", "probe_media", frozenset({"scoped_argument"})),
    ("core/media_io.py", "probe", frozenset({"scoped_argument"})),
    ("core/media_io.py", "MediaHandle.__init__", frozenset({"scoped_access"})),
    ("core/media_io.py", "MediaHandle.close", frozenset({"_release_scope"})),
    ("core/lut_importer.py", "parse_cube_lut", frozenset({"scoped_argument"})),
    ("core/subtitle_io.py", "load_srt", frozenset({"scoped_argument"})),
    ("core/subtitle_io.py", "save_srt", frozenset({"scoped_argument"})),
    ("core/scene3d.py", "load_scene", frozenset({"scoped_argument"})),
    ("core/mograph_raster.py", "_ImageCache.get", frozenset({"scoped_access"})),
    ("core/export_runner.py", "run_export", frozenset({"_with_user_files"})),
    ("core/export_runner.py", "_with_user_files", frozenset({"plan_scope"})),
    ("core/export_engine.py", "ExportEngine.start", frozenset({"_open_scope"})),
    ("core/export_engine.py", "ExportEngine._open_scope", frozenset({"plan_scope"})),
    ("core/export_engine.py", "ExportEngine._cleanup_temporary_files", frozenset({"_close_scope"})),
    ("core/process_supervisor.py", "ProcessSupervisor.popen", frozenset({"command_scope"})),
    ("core/process_supervisor.py", "ProcessSupervisor.finish", frozenset({"_SCOPE_ATTRIBUTE"})),
    ("ui/preview_panel.py", "PreviewPanel.load_video", frozenset({"hold"})),
    ("ui/preview_panel.py", "PreviewPanel.preview_at", frozenset({"hold"})),
    ("ui/main_window_mixins/project_files.py", "ProjectFilesMixin._load_project_from_path", frozenset({"report_offline_media"})),
    ("main.py", "main", frozenset({"start_for_store"})),
]

# Seuls fichiers qui lancent un processus (``subprocess.*`` ou ``QProcess``), avec ce qui ouvre l'accès aux fichiers.
PROCESS_LAUNCHERS = {
    "core/process_supervisor.py": "ProcessSupervisor.popen ouvre command_scope et finish le referme",
    "core/export_engine.py": "ExportEngine._open_scope ouvre plan_scope pour tout l'export",
}

# Modules qui peuvent importer le gestionnaire (``current_manager`` / ``install_manager``) : la façade les applique avec le drapeau Store.
MANAGER_USERS = {"core/security_scope/__init__.py", "ui/sandbox_access.py"}


def _parse(relative: str) -> ast.Module:
    return ast.parse((ROOT / relative).read_text(encoding="utf-8"))


def _find(tree: ast.Module, qualname: str) -> ast.AST | None:
    scope: list[ast.AST] = [tree]
    for part in qualname.split("."):
        found = None
        for node in scope[-1].body:                         # type: ignore[attr-defined]
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == part:
                found = node
                break
        if found is None:
            return None
        scope.append(found)
    return scope[-1]


def _names(node: ast.AST) -> set[str]:
    found = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            found.add(child.id)
        elif isinstance(child, ast.Attribute):
            found.add(child.attr)
    return found


def _sources(*roots: str):
    for root in roots:
        base = ROOT / root
        for path in ([base] if base.is_file() else sorted(base.rglob("*.py"))):
            yield path.relative_to(ROOT).as_posix(), ast.parse(path.read_text(encoding="utf-8"))


def _enclosing(tree: ast.Module):
    """``(qualname, nœud)`` de chaque nœud, rattaché à sa fonction englobante."""
    result: list[tuple[str, ast.AST]] = []

    def visit(node: ast.AST, trail: tuple[str, ...]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                visit(child, (*trail, child.name))
            else:
                result.append((".".join(trail), child))
                visit(child, trail)

    visit(tree, ())
    return result


def _sites(predicate) -> set[tuple[str, str]]:
    return {(path, qualname) for path, tree in _sources("core", "ui", "main.py") for qualname, node in _enclosing(tree) if predicate(node)}


def _is_dialog(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    return (isinstance(func, ast.Attribute) and func.attr in DIALOG_CALLS) or \
        (isinstance(func, ast.Name) and func.id == "QFileDialog")


def _is_path_source(node: ast.AST) -> bool:
    return isinstance(node, ast.Attribute) and node.attr == "toLocalFile" or \
        (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "file"
         and isinstance(node.func.value, ast.Name) and node.func.value.id == "event")


# -- dialogues ---------------------------------------------------------------------------------------------------------------


def test_every_file_dialog_is_declared():
    found = _sites(_is_dialog)
    assert found == DIALOGS, f"dialogue non déclaré (ou disparu) : {sorted(found ^ DIALOGS)} -> appeler remember_user_path, puis l'inscrire ici et dans docs/sandbox-access.md"


@pytest.mark.parametrize("path, qualname", sorted(DIALOGS), ids=[f"{p.split('/')[-1]}::{q}" for p, q in sorted(DIALOGS)])
def test_every_dialog_remembers_the_chosen_path(path, qualname):
    node = _find(_parse(path), qualname)
    assert node is not None, f"{path} : {qualname} n'existe plus (mettre à jour l'inventaire)"
    assert "remember_user_path" in _names(node), f"{path} : {qualname} ne mémorise plus le chemin choisi"


def test_every_path_source_outside_dialogs_is_declared_and_remembered():
    found = _sites(_is_path_source)
    assert found == set(PATH_SOURCES), f"source de chemins non déclarée (ou disparue) : {sorted(found ^ set(PATH_SOURCES))}"
    for (_path, _qualname), (target_path, target_qualname) in PATH_SOURCES.items():
        node = _find(_parse(target_path), target_qualname)
        assert node is not None and "remember_user_path" in _names(node), (target_path, target_qualname)


# -- lecteurs et écrivains ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("path, qualname, guards", GUARDED, ids=[f"{p.split('/')[-1]}::{q}" for p, q, _g in GUARDED])
def test_every_inventoried_reader_or_writer_opens_its_access(path, qualname, guards):
    node = _find(_parse(path), qualname)
    assert node is not None, f"{path} : {qualname} n'existe plus (mettre à jour l'inventaire et docs/sandbox-access.md)"
    assert guards & _names(node), f"{path} : {qualname} n'ouvre plus son accès ({sorted(guards)})"


def test_processes_are_only_launched_where_the_access_is_opened():
    launchers: set[str] = set()
    for path, tree in _sources("core", "ui", "main.py"):
        for _q, node in _enclosing(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == "subprocess" \
                    and func.attr in {"run", "Popen", "call", "check_call", "check_output"}:
                launchers.add(path)
            elif (isinstance(func, ast.Name) and func.id == "QProcess") or (isinstance(func, ast.Attribute) and func.attr == "QProcess"):
                launchers.add(path)
    assert launchers == set(PROCESS_LAUNCHERS), \
        f"lancement de processus hors du superviseur : {sorted(launchers ^ set(PROCESS_LAUNCHERS))} -> passer par process_supervisor (accès aux fichiers compris)"


def test_the_manager_is_only_reached_through_the_facade():
    users = {path for path, tree in _sources("core", "ui", "main.py") for _q, node in _enclosing(tree)
             if isinstance(node, ast.Name) and node.id in {"current_manager", "install_manager"} and isinstance(node.ctx, ast.Load)
             or isinstance(node, ast.alias) and node.name in {"current_manager", "install_manager"}}
    assert {u for u in users if not u.startswith("core/security_scope/")} <= MANAGER_USERS, sorted(users)


def test_the_scope_modules_do_not_import_qt():
    for path in sorted((ROOT / "core" / "security_scope").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module} | \
            {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        assert not any(name.startswith(("PySide6", "ui")) for name in imports), (path.name, imports)


def test_the_documentation_lists_every_inventoried_entry():
    text = (ROOT / "docs" / "sandbox-access.md").read_text(encoding="utf-8")
    missing = [f"{path} : {qualname}" for path, qualname, _g in GUARDED if qualname.split(".")[-1] not in text]
    missing += [f"{path} : {qualname}" for path, qualname in sorted(DIALOGS) if qualname.split(".")[-1] not in text]
    assert not missing, f"docs/sandbox-access.md ne cite pas : {missing}"
