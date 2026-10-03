"""Garde-fous contre le code mort (voir ``docs/dead-code-audit.md``).

Chaque suppression de l'audit est accompagnée d'un test qui prouve que le chemin moderne est celui qu'utilise
l'application ; les tests génériques ci-dessous empêchent le retour d'un module que personne n'importe ou d'une
fonction privée que personne n'appelle. Tout se lit dans le source (graphe d'imports par ``ast``), sans exécuter
l'interface.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

from test_scopes import _window

ROOT = Path(__file__).resolve().parent.parent
PRODUCTION_PACKAGES = ("core", "ui")


def _module_name(path: Path) -> str:
    parts = list(path.relative_to(ROOT).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _python_files(*packages: str) -> dict[str, Path]:
    return {
        _module_name(path): path
        for package in packages
        for path in (ROOT / package).rglob("*.py")
    }


def _resolve(name: str, known: set[str]) -> str | None:
    """Module connu le plus long qui préfixe ``name`` (``core.a.b`` -> ``core.a`` si ``b`` est un attribut)."""
    parts = name.split(".")
    for end in range(len(parts), 0, -1):
        candidate = ".".join(parts[:end])
        if candidate in known:
            return candidate
    return None


def _imports_of(module: str, path: Path, known: set[str]) -> set[str]:
    """Modules du dépôt importés par ``module`` : imports absolus, relatifs, ``from paquet import module``."""
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        targets: list[str] = []
        if isinstance(node, ast.Import):
            targets = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")
                base = base[: len(base) - (node.level - 1)]
                target = ".".join(base + ([node.module] if node.module else []))
            else:
                target = node.module or ""
            targets = [target] + [f"{target}.{alias.name}" for alias in node.names]
        for name in targets:
            resolved = _resolve(name, known)
            if resolved:
                found.add(resolved)
    return found


def _reachable_from_main() -> tuple[set[str], set[str]]:
    files = _python_files(*PRODUCTION_PACKAGES)
    files["main"] = ROOT / "main.py"
    known = set(files)
    graph = {module: _imports_of(module, path, known) for module, path in files.items()}
    seen: set[str] = set()
    pending = ["main"]
    while pending:
        module = pending.pop()
        if module in seen:
            continue
        seen.add(module)
        pending.extend(graph[module])
        parent = module.rpartition(".")[0]
        while parent:                                   # importer ``a.b.c`` charge aussi ``a`` et ``a.b``
            if parent in known:
                pending.append(parent)
            parent = parent.rpartition(".")[0]
    return seen, known


def test_every_core_and_ui_module_is_reachable_from_the_entry_point():
    """Un module que ni ``main.py`` ni ses imports (paresseux compris) n'atteignent est du code mort : on le supprime.

    Avant l'audit, ``core/timeline_model.py`` (ancien modèle de timeline en dictionnaires) n'était importé que par
    son propre test. Un import dynamique (``importlib``) n'est pas vu ici : il n'en existe aucun dans ``core/``
    ni ``ui/``, et en ajouter un obligerait à étendre ce test en connaissance de cause.
    """
    seen, known = _reachable_from_main()
    orphans = sorted(
        module for module in known - seen if module.split(".")[0] in PRODUCTION_PACKAGES
    )
    assert not orphans, f"Modules que personne n'importe : {orphans}"


def test_the_legacy_timeline_model_is_gone_and_the_editor_cuts_through_timeline_operations(qtbot, monkeypatch):
    """L'interface coupe avec ``core.timeline_operations.cut_clip`` (sur un ``Project``), pas avec l'ancien module.

    L'ancien ``core.timeline_model.cut_clip`` travaillait sur une liste de dictionnaires et nommait les morceaux
    ``Intro_1`` / ``Intro_2`` ; la fenêtre produit ``intro-split-2`` parce qu'elle passe par le chemin moderne.
    """
    from core import timeline_operations
    from core.project_model import Project
    from ui.main_window_mixins import timeline_editing

    assert importlib.util.find_spec("core.timeline_model") is None

    calls: list[tuple[type, str]] = []
    modern_cut = timeline_editing.cut_clip

    def spy(project, clip_id, at):
        calls.append((type(project), clip_id))
        return modern_cut(project, clip_id, at)

    assert modern_cut is timeline_operations.cut_clip
    monkeypatch.setattr(timeline_editing, "cut_clip", spy)
    window = _window(qtbot, monkeypatch)

    window.cut_selected_clip("intro", 2.0)

    assert calls == [(Project, "intro")]
    assert {view.id for view in window.timeline_panel.clip_views} >= {"intro", "intro-split-2"}


def _names_imported_from(module_suffix: str) -> set[str]:
    """Noms importés de ``module_suffix`` (``from .audio_mixer import x`` ou ``from core.audio_mixer import x``)."""
    names: set[str] = set()
    for path in _python_files(*PRODUCTION_PACKAGES).values():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[-1] == module_suffix:
                names.update(alias.name for alias in node.names)
    return names


def test_every_public_function_of_the_audio_mixer_is_used_by_the_export():
    """Le panoramique est la seule chose que ``core.audio_mixer`` fournit encore, et l'export l'utilise.

    Avant l'audit, ``mix_at`` / ``MixSpec`` / ``fade_envelope``… n'étaient appelés que par leurs tests et
    décrivaient un mixage différent de celui du plan de rendu (voir ``test_audio_mixer``). Toute fonction publique
    ajoutée ici doit avoir un appelant en production.
    """
    tree = ast.parse((ROOT / "core" / "audio_mixer.py").read_text(encoding="utf-8"))
    public = {node.name for node in tree.body if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")}
    assert public == {"pan_to_gains", "pan_needs_filter"}
    assert public <= _names_imported_from("audio_mixer")
