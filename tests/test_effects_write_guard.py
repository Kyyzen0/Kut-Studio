"""Garde : toute écriture de ``clip.effects`` passe par le point d'entrée du cycle de vie (ADR-0008, décision 7).

Les images clés ``fx.<effet>.<paramètre>`` de ``Clip.animation`` n'ont de sens que tant que l'effet existe. Le seul endroit qui
remplace une chaîne d'effets en tenant l'animation à jour est :func:`core.effects_model.set_clip_effects`
(:func:`~core.effects_model.prune_effect_animation`). Une écriture directe (``.effects =``, ``.effects.append(`` …) dans ``core/``
ou ``ui/`` laisserait des courbes orphelines ou qui animent le mauvais effet : elle doit être **classée ici**, avec sa raison.

La recherche se fait sur l'arbre syntaxique (docstrings et commentaires ne comptent pas). ``tools/`` est hors périmètre.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCANNED = ("core", "ui")
MUTATORS = {"append", "extend", "insert", "remove", "pop", "clear", "sort", "reverse"}

# (fichier, fonction englobante) -> raison. Chaque entrée doit exister (pas d'entrée périmée) et ne couvre que cette fonction.
ALLOWED: dict[tuple[str, str], str] = {
    (
        "core/effects_model.py",
        "set_clip_effects",
    ): "c'est le point d'entrée lui-même : il écrit la chaîne puis appelle prune_effect_animation",
    (
        "core/project_templates.py",
        "_Builder.night_look",
    ): "crée un clip tout neuf (add_graphic_clip) : sans animation, donc aucune image clé fx.* à tenir à jour",
}


def _is_effects(node: ast.AST) -> bool:
    return isinstance(node, ast.Attribute) and node.attr == "effects"


def _writes(tree: ast.AST) -> list[tuple[int, str, str]]:
    """``(ligne, fonction englobante, forme)`` de chaque écriture d'un attribut ``.effects`` du module."""
    found: list[tuple[int, str, str]] = []

    def visit(node: ast.AST, scope: tuple[str, ...]) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            scope = (*scope, node.name)
        name = ".".join(part for part in scope if part)
        targets: list[ast.AST] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            targets = [node.target]
        elif isinstance(node, ast.Delete):
            targets = list(node.targets)
        for target in targets:
            for part in ast.walk(target):
                if _is_effects(part):
                    found.append((node.lineno, name, "affectation"))
                    break
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in MUTATORS
            and _is_effects(node.func.value)
        ):
            found.append((node.lineno, name, f".{node.func.attr}("))
        for child in ast.iter_child_nodes(node):
            visit(child, scope)

    visit(tree, ())
    return found


def scan() -> list[tuple[str, int, str, str]]:
    result = []
    for folder in SCANNED:
        for path in sorted((ROOT / folder).rglob("*.py")):
            relative = path.relative_to(ROOT).as_posix()
            for line, scope, form in _writes(
                ast.parse(path.read_text(encoding="utf-8"), relative)
            ):
                result.append((relative, line, scope, form))
    return result


def unclassified(writes) -> list[tuple[str, int, str, str]]:
    return [item for item in writes if (item[0], item[2]) not in ALLOWED]


def test_every_write_of_clip_effects_is_classified():
    missing = unclassified(scan())
    assert not missing, (
        "Écriture de .effects hors du point d'entrée (core.effects_model.set_clip_effects) et hors de la liste blanche : "
        + "; ".join(
            f"{path}:{line} ({scope or 'module'}, {form})"
            for path, line, scope, form in missing
        )
    )


def test_the_allow_list_has_no_stale_entry():
    seen = {(path, scope) for path, _line, scope, _form in scan()}
    assert set(ALLOWED) <= seen, (
        f"entrées de la liste blanche sans écriture : {sorted(set(ALLOWED) - seen)}"
    )
    assert all(reason.strip() for reason in ALLOWED.values())


def test_the_guard_catches_a_new_unclassified_write():
    """La garde est vérifiée sur un faux module : chaque forme d'écriture est vue, et une écriture qui n'est pas dans la
    liste blanche est signalée."""
    source = (
        "def sneaky(clip, effect):\n"
        "    clip.effects = []\n"
        "    clip.effects.append(effect)\n"
        "    clip.effects.remove(effect)\n"
        "    clip.effects += [effect]\n"
        "    del clip.effects[0]\n"
        "    clip.effects[0] = effect\n"
        "    return clip.effects\n"
    )
    forms = _writes(ast.parse(source))
    assert [form for _line, _scope, form in forms] == [
        "affectation",
        ".append(",
        ".remove(",
        "affectation",
        "affectation",
        "affectation",
    ]
    assert all(scope == "sneaky" for _line, scope, _form in forms)
    assert unclassified(
        [("core/not_listed.py", line, scope, form) for line, scope, form in forms]
    )


def test_reads_are_not_writes():
    source = "def read(clip):\n    return [e for e in clip.effects] + list(clip.effects) + clip.effects[:1]\n"
    assert _writes(ast.parse(source)) == []
