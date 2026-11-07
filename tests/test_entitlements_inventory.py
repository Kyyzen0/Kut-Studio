"""Inventaire des entrées gardées (ADR-0013, étape 12, ``docs/entitlements.md`` annexe) : un test échoue si une entrée n'est pas gardée.

Deux contrôles par lecture du code (AST), sans rien exécuter :

* **chaque entrée de l'inventaire appelle sa garde** (la liste ci-dessous est celle de la documentation) ;
* **aucun nouvel appelant** des puits d'écriture ou d'export (``run_export``, ``engine.start``, livrables, ``save_srt``,
  ``Session``, ``ExportEngine``) hors de la liste permise : une entrée neuve, qui contournerait les gardes, fait échouer le test
  et oblige à la classer (gardée, ou volontairement non gardée avec sa raison).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# (fichier, fonction qualifiée, noms dont l'un doit apparaître dans le corps)
GUARDED: list[tuple[str, str, frozenset[str]]] = [
    # --- modèle (interface) ---
    ("core/edit_history.py", "ProjectHistory.record", frozenset({"edit_refusal"})),
    ("core/edit_history.py", "ProjectHistory.undo", frozenset({"require_edit"})),
    ("core/edit_history.py", "ProjectHistory.redo", frozenset({"require_edit"})),
    ("core/edit_history.py", "ProjectHistory.go_to", frozenset({"require_edit"})),
    # --- moteur / scripts / CLI ---
    ("core/scripting/session.py", "Session._open_with_snapshot", frozenset({"require_edit"})),
    ("core/scripting/session.py", "Transaction.run", frozenset({"require_edit"})),
    ("core/cli.py", "_cmd_apply", frozenset({"require_edit"})),
    ("core/cli.py", "_cmd_export", frozenset({"require_export"})),
    ("core/cli.py", "run", frozenset({"EntitlementRefused"})),
    ("core/film.py", "Film.save", frozenset({"require_edit"})),
    ("core/film.py", "Film.render_shot", frozenset({"require_edit"})),
    ("core/film.py", "Film.export", frozenset({"require_export"})),
    # --- export ---
    ("core/export_runner.py", "run_export", frozenset({"require_export"})),
    ("core/export_engine.py", "ExportEngine.start", frozenset({"export_refusal"})),
    ("core/render_queue.py", "RenderQueue.enqueue", frozenset({"require_export"})),
    ("core/render_queue.py", "RenderQueue.start_all", frozenset({"_export_refused"})),
    ("core/render_queue.py", "RenderQueue.start_job", frozenset({"_export_refused"})),
    ("core/render_queue.py", "RenderQueue._start_next", frozenset({"_export_refused"})),
    ("core/social_deliverables.py", "make_cover", frozenset({"require_export"})),
    ("core/social_deliverables.py", "make_preview_copy", frozenset({"require_export"})),
    ("core/social_deliverables.py", "make_deliverables", frozenset({"require_export"})),
    # --- interface ---
    ("ui/shortcut_manager.py", "ShortcutManager.trigger", frozenset({"command_guard"})),
    ("ui/main_window.py", "MainWindow.dropEvent", frozenset({"_edit_refused_ui"})),
    ("ui/main_window_mixins/media_import.py", "MediaImportMixin.on_asset_dropped", frozenset({"_edit_refused_ui"})),
    ("ui/main_window_mixins/media_import.py", "MediaImportMixin.import_media_via_dialog", frozenset({"_edit_refused_ui"})),
    ("ui/main_window_mixins/media_import.py", "MediaImportMixin.import_media_to_project", frozenset({"_edit_refused_ui"})),
    ("ui/main_window_mixins/sequences.py", "SequencesMixin.on_sequence_dropped", frozenset({"_edit_refused_ui"})),
    ("ui/main_window_mixins/templates.py", "TemplatesMixin.fill_slots_from_photo_drop", frozenset({"_edit_refused_ui"})),
    ("ui/main_window_mixins/project_files.py", "ProjectFilesMixin._enqueue_current_export", frozenset({"_export_refused_ui"})),
    ("ui/main_window_mixins/social.py", "SocialMixin.export_format_versions", frozenset({"_export_refused_ui"})),
    ("ui/main_window_mixins/subtitles_graphics.py", "SubtitlesGraphicsMixin.export_subtitles_via_dialog", frozenset({"_export_refused_ui"})),
    ("ui/main_window_mixins/subtitles_graphics.py", "SubtitlesGraphicsMixin.export_subtitles_to_path", frozenset({"require_export"})),
    ("ui/main_window_mixins/entitlements.py", "EntitlementsMixin._settle_forbidden_edits", frozenset({"edit_refusal"})),
    ("ui/main_window_mixins/project_files.py", "ProjectFilesMixin.save_project_file", frozenset({"_settle_forbidden_edits"})),
    ("ui/main_window_mixins/project_files.py", "ProjectFilesMixin.save_project_as", frozenset({"_settle_forbidden_edits"})),
    ("ui/main_window_mixins/project_files.py", "ProjectFilesMixin._write_autosave", frozenset({"_settle_forbidden_edits"})),
    ("ui/main_window_mixins/library_organization.py", "LibraryOrganizationMixin._on_tag_manager_requested",
     frozenset({"_edit_refused_ui"})),
    ("ui/main_window_mixins/recording.py", "RecordingMixin.on_record_toggled", frozenset({"_edit_refused_ui"})),
    ("ui/main_window_mixins/history.py", "HistoryMixin.undo_last", frozenset({"EntitlementRefused"})),
    ("ui/main_window_mixins/history.py", "HistoryMixin.redo_last", frozenset({"EntitlementRefused"})),
    ("ui/main_window_mixins/history.py", "HistoryMixin.go_to_history_state", frozenset({"EntitlementRefused"})),
]


def _parse(relative: str) -> ast.Module:
    return ast.parse((ROOT / relative).read_text(encoding="utf-8"))


def _find(tree: ast.Module, qualname: str) -> ast.AST | None:
    scope: list[ast.AST] = [tree]
    for part in qualname.split("."):
        found = None
        for node in scope[-1].body:                     # type: ignore[attr-defined]
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


@pytest.mark.parametrize("path, qualname, guards", GUARDED, ids=[f"{p.split('/')[-1]}::{q}" for p, q, _g in GUARDED])
def test_every_inventoried_entry_calls_its_guard(path, qualname, guards):
    node = _find(_parse(path), qualname)
    assert node is not None, f"{path} : {qualname} n'existe plus (mettre à jour l'inventaire et docs/entitlements.md)"
    assert guards & _names(node), f"{path} : {qualname} n'appelle plus sa garde ({sorted(guards)})"


def _sources(*roots: str):
    for root in roots:
        base = ROOT / root
        for path in ([base] if base.is_file() else sorted(base.rglob("*.py"))):
            yield path.relative_to(ROOT).as_posix(), ast.parse(path.read_text(encoding="utf-8"))


def _enclosing(tree: ast.Module):
    """``(qualname, nœud)`` de chaque appel / référence, rattaché à sa fonction englobante."""
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


def _callers(names: set[str], *, receiver: set[str] | None = None, calls_only: bool = False) -> set[tuple[str, str]]:
    """Où ``names`` est appelé (ou, sans ``calls_only``, simplement référencé : passé à ``submit``…) : ``(fichier, fonction)``.

    ``receiver`` : ne retient que ``<receveur>.<nom>`` dont le receveur s'appelle ainsi (``self._engine.start``).
    """
    found = set()
    for path, tree in _sources("core", "ui", "main.py"):
        for qualname, node in _enclosing(tree):
            target = node.func if isinstance(node, ast.Call) else (None if calls_only else node)
            if target is None:
                continue
            if receiver is None and isinstance(target, ast.Name) and target.id in names and isinstance(target.ctx, ast.Load):
                found.add((path, qualname))
            elif isinstance(target, ast.Attribute) and target.attr in names:
                owner = target.value
                if receiver is None or (isinstance(owner, ast.Attribute) and owner.attr in receiver) \
                        or (isinstance(owner, ast.Name) and owner.id in receiver):
                    found.add((path, qualname))
    return found


def test_no_new_caller_of_run_export():
    allowed = {("core/cli.py", "_cmd_export"), ("core/film.py", "Film.export")}
    unexpected = {c for c in _callers({"run_export"}) if c not in allowed and c[0] != "core/export_runner.py"}
    assert not unexpected, f"nouvel appelant de run_export : {sorted(unexpected)} -> le garder, puis l'inscrire ici et dans docs/entitlements.md"


def test_no_new_starter_of_the_export_engine():
    starters = _callers({"start"}, receiver={"_engine", "export_engine", "engine"}, calls_only=True)
    allowed = {("core/render_queue.py", "RenderQueue._launch_current"), ("core/render_queue.py", "RenderQueue._poll_preparation")}
    assert starters == allowed, f"engine.start : {sorted(starters ^ allowed)}"


def test_no_new_writer_of_deliverables_or_subtitles():
    deliverables = _callers({"make_deliverables", "make_cover", "make_preview_copy"})
    deliverables = {c for c in deliverables if c[0] != "core/social_deliverables.py"}
    assert deliverables == {("core/render_queue.py", "RenderQueue._start_deliverables")}, sorted(deliverables)
    srt = {c for c in _callers({"save_srt"}, calls_only=True) if c[0] != "core/subtitle_io.py"}
    assert srt == {("ui/main_window_mixins/subtitles_graphics.py", "SubtitlesGraphicsMixin.export_subtitles_to_path")}, sorted(srt)


def test_script_sessions_are_only_opened_by_the_cli():
    sessions = {c for c in _callers({"Session"}, calls_only=True) if not c[0].startswith("core/scripting/")}
    assert sessions == {("core/cli.py", "_apply_script_to_project")}, sorted(sessions)


def test_only_one_dialog_edits_the_live_project_and_it_is_guarded():
    """Un dialogue qui reçoit le projet vivant et le modifie lui-même (hors signaux) échappe à l'historique : seul
    ``TagManagerDialog`` le fait (recherche de ``create_tag`` / ``rename_tag`` / ``delete_tag`` hors du cœur)."""
    writers = {path for path, tree in _sources("ui") for _q, node in _enclosing(tree)
               if isinstance(node, ast.Attribute) and node.attr in {"create_tag", "rename_tag", "delete_tag"}}
    assert writers == {"ui/library_organization_widgets.py"}, sorted(writers)
    assert _callers({"TagManagerDialog"}, calls_only=True) == {
        ("ui/main_window_mixins/library_organization.py", "LibraryOrganizationMixin._on_tag_manager_requested")}


def test_every_deferred_edit_timer_is_flushed_by_finalize_pending_edit_sessions():
    """Les minuteurs d'édition différée (``*_session_timer``, ``_subtitle_edit_timer``) sont tous clos par
    ``_finalize_pending_edit_sessions`` : c'est ce que ``_settle_forbidden_edits`` appelle avant d'écrire le projet."""
    timers = set()
    for _path, tree in _sources("ui"):
        for _q, node in _enclosing(tree):
            if isinstance(node, ast.Attribute) and node.attr.endswith(("_session_timer", "_edit_timer")):
                timers.add(node.attr)
    assert timers == {"_color_session_timer", "_graphic_session_timer", "_transform_session_timer", "_subtitle_edit_timer"}, \
        sorted(timers)
    node = _find(_parse("ui/main_window_mixins/history.py"), "HistoryMixin._finalize_pending_edit_sessions")
    assert node is not None and {"_flush_subtitle_history_record", "_finalize_transform_session", "_finalize_color_history",
                                 "_finalize_graphic_history"} <= _names(node)


def test_apply_script_goes_through_the_guarded_session():
    node = _find(_parse("core/scripting/script.py"), "apply_script")
    assert node is not None and "apply" in _names(node)


def test_export_engines_are_only_created_where_they_never_start_a_render():
    """``ExportEngine(...)`` : la fenêtre (file de rendu, garde de ``start``), les scopes et la validation matérielle (commande
    construite, jamais lancée)."""
    creators = {c[0] for c in _callers({"ExportEngine"}, calls_only=True) if c[0] != "core/export_engine.py"}
    assert creators == {"ui/main_window.py", "ui/main_window_mixins/scopes.py", "core/hardware_validation.py"}, sorted(creators)


def test_the_documentation_lists_every_inventoried_entry():
    text = (ROOT / "docs" / "entitlements.md").read_text(encoding="utf-8")
    missing = [f"{path} : {qualname}" for path, qualname, _g in GUARDED if qualname.split(".")[-1] not in text]
    assert not missing, f"docs/entitlements.md ne cite pas : {missing}"


# -- le pont StoreKit (ADR-0013, étape 13) : qui l'installe, qui l'appelle -----------------------------------------------------


def test_only_the_store_controller_installs_the_entitlement_service():
    """``install_service`` n'est appelé, en production, que par le contrôleur du build Store (jamais hors Store)."""
    installers = {c for c in _callers({"install_service"}, calls_only=True) if c[0] != "core/entitlements/guard.py"}
    assert installers == {("ui/store_entitlements.py", "StoreEntitlementsController.__init__")}, sorted(installers)
    creators = {c for c in _callers({"StoreEntitlementsController"}, calls_only=True) if c[0] != "ui/store_entitlements.py"}
    assert creators == {("ui/main_window_mixins/entitlements.py", "EntitlementsMixin._start_store_entitlements")}, sorted(creators)
    starters = _callers({"_start_store_entitlements"}, calls_only=True)
    assert starters == {("ui/main_window_mixins/entitlements.py", "EntitlementsMixin._init_entitlements")}, sorted(starters)
    node = _find(_parse("ui/main_window_mixins/entitlements.py"), "EntitlementsMixin._init_entitlements")
    assert node is not None and {"is_store", "_start_store_entitlements"} <= _names(node)       # derrière is_store()


def test_the_storekit_bridge_is_only_driven_by_the_worker_modules():
    """Les appels BLOQUANTS du pont (``call``, ``poll_update``, ``shutdown``) ne sont écrits que dans les deux modules du thread de
    travail ; le chargement (``StoreKitBridge.load``) que par le contrôleur Store. Aucun appel depuis ``ui/`` hors chargement."""
    bridge_modules = {"core/entitlements/storekit_bridge.py", "core/entitlements/storekit_verifier.py",
                      "core/entitlements/storekit_source.py"}
    blocking = {c for c in _callers({"poll_update", "shutdown"}, receiver={"bridge", "_bridge"}, calls_only=True)} | \
        {c for c in _callers({"call"}, receiver={"bridge"}, calls_only=True)}
    assert {c for c in blocking if c[0] not in bridge_modules} == set(), sorted(blocking)
    loaders = {c for c in _callers({"load"}, receiver={"StoreKitBridge"}, calls_only=True) if c[0] != "core/entitlements/storekit_bridge.py"}
    assert loaders == {("ui/store_entitlements.py", "bundled_bridge_loader")}, sorted(loaders)
    for path in ("core/entitlements/storekit_bridge.py", "core/entitlements/storekit_verifier.py",
                 "core/entitlements/storekit_source.py"):
        imports = {n.module for n in ast.walk(_parse(path)) if isinstance(n, ast.ImportFrom)} | \
            {a.name for n in ast.walk(_parse(path)) if isinstance(n, ast.Import) for a in n.names}
        assert not any(name and name.startswith(("PySide6", "ui")) for name in imports), (path, imports)        # sans Qt
