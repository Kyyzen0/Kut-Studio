"""Complétude des verbes : toute fonction **publique d'édition** du modèle a un verbe, ou une raison écrite de ne pas en avoir.

Une fonction publique des modules d'édition de ``core/`` (``tools/scripting_debt.py``, ``EDIT_MODULES``) est soit **référencée** par
le code de ``core/scripting/`` (analyse AST), soit **en dette** dans ``tests/data/script_verb_debt.json`` avec une raison
(``lecture seule``, ``interne``, ``interface seulement`` ou ``à faire : …``). La dette ne peut que diminuer : une fonction neuve
force une décision, une fonction devenue verbe sort de la dette. ``python -m tools.scripting_debt verbs --list`` la montre.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from tools import scripting_debt as tool


def _debt() -> dict[str, str]:
    return tool.load_verb_debt()


def test_every_editing_function_has_a_verb_or_a_reason():
    comparison = tool.compare_verbs(_debt())
    assert not comparison.undecided, (
        f"Fonction(s) publique(s) d'édition sans verbe ni raison : {comparison.undecided}. Écrivez le verbe (core/scripting/), ou "
        "ajoutez l'entrée à tests/data/script_verb_debt.json avec une raison de tools/scripting_debt.py (REASONS), visible en revue."
    )
    assert not comparison.modules, (
        f"Module(s) de core/ avec des fonctions à premier paramètre `project`, absents de EDIT_MODULES et READ_MODULES "
        f"(tools/scripting_debt.py) : {comparison.modules}. Dites si c'est une surface d'édition."
    )


def test_the_debt_only_shrinks():
    comparison = tool.compare_verbs(_debt())
    assert not comparison.resolved, (
        f"Ces fonctions ont maintenant un verbe mais sont encore dans la dette : {comparison.resolved}. Retirez-les "
        "(python -m tools.scripting_debt verbs --update-baseline) : la dette ne doit pas pouvoir y revenir."
    )
    assert not comparison.vanished, (
        f"Entrées de dette dont la fonction n'existe plus (renommée ?) : {comparison.vanished}"
    )
    assert not comparison.bad_reason, (
        f"Raison inconnue (permises : {sorted(tool.REASONS)}) : {comparison.bad_reason}"
    )


def test_the_two_tables_of_modules_do_not_overlap_and_name_real_modules():
    assert not set(tool.EDIT_MODULES) & set(tool.READ_MODULES)
    for module in (*tool.EDIT_MODULES, *tool.READ_MODULES):
        assert (tool.ROOT / "core" / f"{module}.py").is_file(), (
            f"core/{module}.py n'existe plus"
        )
    assert all(
        reason.strip()
        for reason in (
            *tool.EDIT_MODULES.values(),
            *tool.READ_MODULES.values(),
            *tool.REASONS.values(),
        )
    )


def test_the_shipped_verbs_are_seen_as_referenced():
    """L'analyse voit bien les verbes existants (sinon la dette serait vide de sens)."""
    used = tool.referenced_names()
    for name in (
        "core.timeline_operations.add_clip_to_track",
        "core.track_operations.add_track",
        "core.audio_buses.add_bus",
        "core.sequences.insert_sequence_clip",
        "core.multicam_ops.create_multicam_source",
        "core.color_grading.ColorGradingService.set_grade",
    ):
        assert name in used, name
    assert len(used & set(tool.candidate_verbs())) > 50


# --- Le garde-fou prouve qu'il échoue --------------------------------------------------------------------------------------


def test_removing_a_debt_entry_makes_the_function_undecided_and_adding_a_referenced_one_is_refused():
    debt = _debt()
    victim = next(iter(sorted(debt)))
    assert tool.compare_verbs(
        {k: v for k, v in debt.items() if k != victim}
    ).undecided == [victim]
    referenced = "core.timeline_operations.add_clip_to_track"
    assert tool.compare_verbs(debt | {referenced: "interne"}).resolved == [referenced]
    assert tool.compare_verbs(
        debt | {"core.timeline_operations.fonction_disparue": "interne"}
    ).vanished == ["core.timeline_operations.fonction_disparue"]
    assert tool.compare_verbs(debt | {victim: "parce que"}).bad_reason == [victim]


def _fake_tree(tmp_path: Path, model: str, verb: str) -> Path:
    (tmp_path / "core" / "scripting").mkdir(parents=True)
    (tmp_path / "core" / "model.py").write_text(
        textwrap.dedent(model), encoding="utf-8"
    )
    (tmp_path / "core" / "scripting" / "verbs.py").write_text(
        textwrap.dedent(verb), encoding="utf-8"
    )
    return tmp_path


def test_the_analysis_on_a_small_tree(tmp_path, monkeypatch):
    monkeypatch.setattr(tool, "EDIT_MODULES", {"model": "test"})
    monkeypatch.setattr(tool, "SERVICE_CLASSES", {"model": ("Service",)})
    root = _fake_tree(
        tmp_path,
        """
        def edit_a(project): ...
        def edit_b(project): ...
        def edit_c(project): ...
        def _private(project): ...
        def helper(value): ...
        class Service:
            def used(self): ...
            def unused(self): ...
        """,
        """
        from ..model import edit_a, edit_b as renamed, Service
        from .. import model as module
        def build():
            service = Service()
            service.used()
            module.edit_c(1)
            return edit_a, renamed
        """,
    )
    assert tool.candidate_verbs(root) == {
        "core.model.edit_a": "model",
        "core.model.edit_b": "model",
        "core.model.edit_c": "model",
        "core.model.Service.used": "model",
        "core.model.Service.unused": "model",
    }
    used = tool.referenced_names(root)
    assert {
        "core.model.edit_a",
        "core.model.edit_b",
        "core.model.edit_c",
        "core.model.Service.used",
    } <= used
    assert tool.compare_verbs({}, root).undecided == ["core.model.Service.unused"]


def test_an_import_that_is_never_used_is_not_a_reference(tmp_path, monkeypatch):
    monkeypatch.setattr(tool, "EDIT_MODULES", {"model": "test"})
    monkeypatch.setattr(tool, "SERVICE_CLASSES", {})
    root = _fake_tree(
        tmp_path,
        "def edit_a(project): ...\ndef edit_b(project): ...\n",
        "from ..model import edit_a, edit_b\n\nedit_a(1)\n",
    )
    assert tool.compare_verbs({}, root).undecided == ["core.model.edit_b"]


def test_a_new_editing_function_is_undecided_until_it_has_a_verb_or_a_reason(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(tool, "EDIT_MODULES", {"model": "test"})
    monkeypatch.setattr(tool, "SERVICE_CLASSES", {})
    root = _fake_tree(
        tmp_path,
        "def edit_a(project): ...\n",
        "from ..model import edit_a\n\nedit_a(1)\n",
    )
    assert tool.compare_verbs({}, root).clean
    (root / "core" / "model.py").write_text(
        "def edit_a(project): ...\ndef brand_new(project): ...\n", encoding="utf-8"
    )
    assert tool.compare_verbs({}, root).undecided == ["core.model.brand_new"]
    assert tool.compare_verbs({"core.model.brand_new": "interne"}, root).clean


def test_a_new_core_module_with_project_functions_must_be_classified(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(tool, "EDIT_MODULES", {})
    monkeypatch.setattr(tool, "READ_MODULES", {})
    root = _fake_tree(tmp_path, "def edit_a(project): ...\n", "")
    assert tool.unclassified_modules(root) == ["model"]
    monkeypatch.setattr(tool, "READ_MODULES", {"model": "lecture"})
    assert tool.unclassified_modules(root) == []


def test_a_module_whose_only_function_takes_a_clip_must_be_classified_too(
    tmp_path, monkeypatch
):
    """Même critère que les candidats (FIRST_PARAMS) : `clip`, `track` ou `sequence` en premier paramètre compte comme `project`."""
    monkeypatch.setattr(tool, "EDIT_MODULES", {})
    monkeypatch.setattr(tool, "READ_MODULES", {})
    for first in ("clip", "track", "sequence"):
        root = _fake_tree(tmp_path / first, f"def edit_it({first}): ...\n", "")
        assert tool.unclassified_modules(root) == ["model"], first
    assert (
        tool.unclassified_modules(
            _fake_tree(tmp_path / "other", "def helper(value): ...\n", "")
        )
        == []
    )
