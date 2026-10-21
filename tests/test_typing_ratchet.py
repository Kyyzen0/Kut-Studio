"""Cliquet de typage : la dette ``mypy`` ne peut que diminuer.

``pyproject.toml`` liste les modules de ``core/`` encore en dette (``ignore_errors``). La règle : on retire un module de
la liste dès qu'il est propre, **on n'en ajoute jamais**, et un module nettoyé ne revient pas. ``mypy`` lui-même
(étape « Type check » de la CI) vérifie que les modules hors liste sont propres ; ce test garde **la liste** :

* elle est identique à ``tests/mypy_debt_baseline.txt`` — ajouter un module à la dette échoue ici, avec un message qui
  dit pourquoi, et en retirer un sans toucher la baseline aussi (la baseline ne doit pas rester plus longue que la
  dette : sinon on pourrait remettre le module plus tard sans que rien ne bronche) ;
* chaque module listé existe (une entrée périmée cacherait un module renommé, donc non vérifié) ;
* la liste ne contient aucun joker (``core.*``) qui désactiverait la vérification d'un coup.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _debt() -> list[str]:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    overrides = config["tool"]["mypy"]["overrides"]
    return sorted(
        module for override in overrides if override.get("ignore_errors") for module in override["module"]
    )


def _baseline() -> list[str]:
    lines = (ROOT / "tests" / "mypy_debt_baseline.txt").read_text(encoding="utf-8").splitlines()
    return sorted(line.strip() for line in lines if line.strip() and not line.startswith("#"))


def test_no_module_enters_the_typing_debt():
    new = sorted(set(_debt()) - set(_baseline()))
    assert not new, (
        f"Module(s) ajouté(s) à la dette de typage : {new}. La dette ne fait que diminuer : corrigez les types du "
        "module au lieu de l'ajouter à `ignore_errors` (un module nouveau doit être propre dès sa création)."
    )


def test_a_cleaned_module_is_removed_from_the_baseline_too():
    stale = sorted(set(_baseline()) - set(_debt()))
    assert not stale, (
        f"Module(s) propres, mais encore dans tests/mypy_debt_baseline.txt : {stale}. Retirez-les de la baseline : "
        "c'est ce qui empêche de les remettre un jour dans la dette."
    )


def test_every_debt_entry_is_an_existing_module_without_wildcard():
    for module in _debt():
        assert "*" not in module, f"Joker interdit dans la dette de typage : {module}"
        path = ROOT.joinpath(*module.split(".")).with_suffix(".py")
        assert path.is_file(), f"{module} est listé en dette mais {path.relative_to(ROOT)} n'existe plus : retirez-le."


def test_the_debt_list_is_sorted_and_without_duplicates():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    listed = [m for o in config["tool"]["mypy"]["overrides"] if o.get("ignore_errors") for m in o["module"]]
    assert len(listed) == len(set(listed)), "doublon dans la dette de typage"
    assert listed == sorted(listed), "la liste de dette doit rester triée (relecture et fusion plus simples)"
