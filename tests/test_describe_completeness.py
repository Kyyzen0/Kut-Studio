"""Complétude de ``describe`` : tout champ du modèle est **décrit**, **exclu** (raison écrite) ou **en dette** (« partie B »).

La dette (``tests/data/describe_field_debt.json``) ne peut que diminuer, comme le plafond mypy du projet : un champ neuf du modèle
oblige à une décision (ce test échoue), et un champ désormais décrit ou exclu doit sortir de la dette (il échoue aussi).
``python -m tools.scripting_debt fields --list`` montre la dette ; ``--update-baseline`` en retire ce qui est résolu.
"""

from __future__ import annotations

import dataclasses

from describe_project import rich_project

from core.scripting.describe import describe
from core.scripting.describe_coverage import (
    COVERED_CLASSES, DERIVED, DESCRIBED, EXCLUDED, NOT_DESCRIBED_SECTIONS, SHAPE_ALIASES,
)
from tools import scripting_debt as debt_tool


def _debt() -> dict[str, str]:
    return debt_tool.load_field_debt()


def _paths(node, prefix: str = "", found: set[str] | None = None) -> set[str]:
    """Tous les chemins de clés d'une sortie : ``sequences[].tracks[].id``."""
    found = set() if found is None else found
    if isinstance(node, dict):
        for key, value in node.items():
            path = f"{prefix}.{key}" if prefix else key
            found.add(path)
            if key != "params":                   # ``params`` d'un effet : table nom -> nombre, les noms sont ceux du modèle
                _paths(value, path, found)
    elif isinstance(node, list):
        for item in node:
            _paths(item, prefix + "[]", found)
    return found


def test_every_model_field_is_described_excluded_or_in_the_debt():
    comparison = debt_tool.compare_fields(_debt())
    assert not comparison.undecided, (
        f"Champ(s) du modèle sans décision : {comparison.undecided}. Décrivez-le dans describe (DESCRIBED dans "
        "core/scripting/describe_coverage.py), excluez-le avec une raison (EXCLUDED) ou, pour la partie B, ajoutez-le à "
        "tests/data/describe_field_debt.json avec sa section."
    )


def test_the_debt_only_shrinks():
    comparison = debt_tool.compare_fields(_debt())
    assert not comparison.resolved, (
        f"Ces champs sont désormais décrits ou exclus mais encore dans la dette : {comparison.resolved}. Retirez-les "
        f"(python -m tools.scripting_debt fields --update-baseline) : c'est ce qui empêche de les y remettre."
    )
    assert not comparison.vanished, f"Entrées de dette sans champ correspondant (renommé ou supprimé) : {comparison.vanished}"
    assert not comparison.bad_section, f"Section inconnue dans la dette : {comparison.bad_section} (permises : {NOT_DESCRIBED_SECTIONS})"
    assert not comparison.bad_tables, comparison.bad_tables


def test_the_sections_named_in_the_output_are_exactly_the_sections_of_the_debt():
    assert sorted(set(_debt().values())) == list(NOT_DESCRIBED_SECTIONS), (
        "not_described (NOT_DESCRIBED_SECTIONS) doit nommer exactement les sections de la dette : une section décrite en partie B en "
        "sort, une section neuve s'y ajoute."
    )


def test_the_covered_classes_are_the_model_dataclasses():
    classes = debt_tool.model_classes()
    assert sorted(classes) == sorted(COVERED_CLASSES)
    assert all(dataclasses.is_dataclass(cls) for cls in classes.values())


def _normalized(path: str) -> str:
    """Le chemin d'une forme répétée ramené à celui de la forme déclarée (:data:`SHAPE_ALIASES`), jusqu'à ce qu'il n'y ait plus d'écho."""
    changed = True
    while changed:
        changed = False
        for alias, primary in sorted(SHAPE_ALIASES.items(), key=lambda item: -len(item[0])):
            if path == alias or path.startswith((alias + ".", alias + "[")):
                path = primary + path[len(alias):]
                changed = True
                break
    return path


def test_every_described_path_exists_in_a_real_output_and_every_output_key_is_declared():
    """Un champ « décrit » dont la clé n'existe pas serait un mensonge ; une clé émise que personne ne déclare, une dérive.
    Les échos d'une forme (:data:`SHAPE_ALIASES`) doivent exister aussi, et leurs clés sont celles de la forme déclarée."""
    raw = _paths(describe(rich_project(), check_media=True))
    missing_aliases = sorted(
        alias for alias in SHAPE_ALIASES if not any(path == alias or path.startswith(alias + ".") for path in raw)
    )
    assert not missing_aliases, f"Échos de forme absents de la sortie du projet riche : {missing_aliases}"
    emitted = {_normalized(path) for path in raw}
    declared = {_normalized(path) for mapping in DESCRIBED.values() for path in mapping.values()}
    missing = sorted(declared - emitted)
    assert not missing, f"Chemins déclarés décrits mais absents de la sortie du projet riche : {missing}"
    known = declared | set(DERIVED)
    prefixes = {path[:index] for path in known for index in range(len(path)) if path[index] in ".["}
    undeclared = sorted(path for path in emitted if path not in known and path not in prefixes)
    assert not undeclared, (
        f"Clés émises par describe qu'aucun champ ni DERIVED ne déclare : {undeclared} (core/scripting/describe_coverage.py)."
    )
    assert sorted(set(DERIVED) - emitted) == [], "DERIVED déclare des clés que la sortie n'émet pas"


def test_every_exclusion_has_a_written_reason():
    for cls, mapping in EXCLUDED.items():
        for field, reason in mapping.items():
            assert len(reason.strip()) > 15, f"{cls}.{field} : raison d'exclusion trop courte"


# --- Le garde-fou prouve qu'il échoue --------------------------------------------------------------------------------------


def test_a_new_model_field_is_refused_until_decided(monkeypatch):
    monkeypatch.setitem(DESCRIBED["Marker"], "colour", "sequences[].markers[].colour")
    comparison = debt_tool.compare_fields(_debt())
    assert any("colour" in problem for problem in comparison.bad_tables)          # décrit mais inexistant dans le modèle
    monkeypatch.undo()
    trimmed = {name: section for name, section in _debt().items() if name != "Clip.text_style"}
    assert debt_tool.compare_fields(trimmed).undecided == ["Clip.text_style"]


def test_a_described_field_must_leave_the_debt():
    debt = _debt() | {"Clip.label": "text_style"}
    assert debt_tool.compare_fields(debt).resolved == ["Clip.label"]
    debt = _debt() | {"Track.armed": "text_style"}                                    # exclu : sort aussi de la dette
    assert debt_tool.compare_fields(debt).resolved == ["Track.armed"]


def test_a_stale_entry_and_a_bad_section_are_refused():
    assert debt_tool.compare_fields(_debt() | {"Clip.renamed_away": "text_style"}).vanished == ["Clip.renamed_away"]
    assert debt_tool.compare_fields(_debt() | {"Clip.text_style": "n_importe_quoi"}).bad_section == ["Clip.text_style"]


def test_a_field_cannot_be_both_described_and_excluded(monkeypatch):
    monkeypatch.setitem(EXCLUDED["Clip"], "label", "raison écrite assez longue pour passer")
    assert any("à la fois décrit et exclu" in problem for problem in debt_tool.compare_fields(_debt()).bad_tables)

