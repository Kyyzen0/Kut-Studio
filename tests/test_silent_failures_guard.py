"""Cliquet des échecs silencieux : aucun ``except`` large qui avale tout, aucun ``assert`` en production.

Périmètre : ``core/``, ``ui/``, ``main.py`` et ``build.py`` (``tests/`` et ``tools/`` exclus), lu en AST.

* **except-pass** : un ``except`` nu, ``except Exception`` ou ``except BaseException`` (seul ou dans un tuple) dont le
  corps n'est que ``pass`` / ``...`` fait disparaître une erreur sans trace. Avant le lot « fiabilité » (2026-10),
  60 blocs de ce genre cachaient par exemple un aperçu jamais rafraîchi après une édition. Un bloc qui reste tolérant
  mais journalise (``LOGGER.debug(..., exc_info=True)``) est accepté. Les ``except OSError: pass`` de nettoyage, qui
  visent une erreur précise, ne sont pas concernés.
* **assert** : une assertion disparaît sous ``python -O`` ; l'invariant qu'elle gardait ne protège plus rien. On
  écrit une garde (``if x is None: return``) ou on lève une vraie exception (``RuntimeError``).

``tests/silent_failures_allowlist.json`` liste les sites qu'on déciderait un jour de tolérer, chacun avec sa raison.
Une entrée est identifiée par fichier, règle et portée (``Classe.methode``), pas par numéro de ligne, qui bouge.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALLOWLIST = ROOT / "tests" / "silent_failures_allowlist.json"
BROAD_EXCEPTIONS = frozenset({"Exception", "BaseException"})
RULES = frozenset({"except-pass", "assert"})


def _production_files() -> list[Path]:
    files = sorted(path for package in ("core", "ui") for path in (ROOT / package).rglob("*.py"))
    return files + [ROOT / "main.py", ROOT / "build.py"]


def _is_broad(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:
        return True
    names = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return any(isinstance(name, ast.Name) and name.id in BROAD_EXCEPTIONS for name in names)


def _does_nothing(body: list[ast.stmt]) -> bool:
    return all(
        isinstance(statement, ast.Pass)
        or (isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant)
            and statement.value.value is Ellipsis)
        for statement in body
    )


class _Scanner(ast.NodeVisitor):
    """Relève les contrevenants avec leur portée (``Classe.methode``) : l'identifiant stable de l'allowlist."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.scope: list[str] = []
        self.found: list[tuple[str, str, str, int]] = []   # (chemin, règle, portée, ligne)

    def _visit_scoped(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    visit_FunctionDef = visit_AsyncFunctionDef = visit_ClassDef = _visit_scoped

    def _record(self, rule: str, node: ast.AST) -> None:
        self.found.append((self.path, rule, ".".join(self.scope) or "<module>", node.lineno))

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if _is_broad(node) and _does_nothing(node.body):
            self._record("except-pass", node)
        self.generic_visit(node)

    def visit_Assert(self, node: ast.Assert) -> None:
        self._record("assert", node)
        self.generic_visit(node)


def _scan_source(source: str, path: str = "<test>") -> list[tuple[str, str, str, int]]:
    scanner = _Scanner(path)
    scanner.visit(ast.parse(source))
    return scanner.found


def _violations() -> list[tuple[str, str, str, int]]:
    found: list[tuple[str, str, str, int]] = []
    for path in _production_files():
        found += _scan_source(path.read_text(encoding="utf-8"), path.relative_to(ROOT).as_posix())
    return found


def _allowlist() -> list[dict]:
    return json.loads(ALLOWLIST.read_text(encoding="utf-8"))["entries"]


def _allowed_keys() -> set[tuple[str, str, str]]:
    return {(entry["path"], entry["rule"], entry["scope"]) for entry in _allowlist()}


def _report(found: list[tuple[str, str, str, int]]) -> str:
    return "\n".join(f"  {path}:{line} ({scope})" for path, _rule, scope, line in found)


def test_no_broad_except_swallows_an_error_silently():
    allowed = _allowed_keys()
    found = [v for v in _violations() if v[1] == "except-pass" and v[:3] not in allowed]
    assert not found, (
        f"{len(found)} « except » large(s) au corps vide (pass / ...) : l'erreur disparaît sans trace.\n"
        f"{_report(found)}\n"
        "Gardez la tolérance mais journalisez ce qui a échoué et la conséquence : "
        "LOGGER.debug(\"…\", exc_info=True) pour une tolérance voulue, LOGGER.warning pour un échec à voir. "
        "Sinon, ajoutez le site à tests/silent_failures_allowlist.json avec sa raison."
    )


def test_no_assert_statement_in_production_code():
    allowed = _allowed_keys()
    found = [v for v in _violations() if v[1] == "assert" and v[:3] not in allowed]
    assert not found, (
        f"{len(found)} « assert » en production : ils disparaissent sous python -O et dans un build gelé.\n"
        f"{_report(found)}\n"
        "Remplacez-les par une garde (if x is None: return) ou une vraie exception (RuntimeError), jamais "
        "AssertionError. Sinon, ajoutez le site à tests/silent_failures_allowlist.json avec sa raison."
    )


def test_every_allowlist_entry_is_complete_and_still_needed():
    entries = _allowlist()
    keys = [(entry.get("path"), entry.get("rule"), entry.get("scope")) for entry in entries]
    assert len(keys) == len(set(keys)), "doublon dans tests/silent_failures_allowlist.json"
    current = {v[:3] for v in _violations()}
    for entry in entries:
        assert set(entry) == {"path", "rule", "scope", "reason"}, f"entrée incomplète ou inconnue : {entry}"
        assert entry["rule"] in RULES, f"règle inconnue : {entry}"
        assert str(entry["reason"]).strip(), f"raison obligatoire : {entry}"
        assert (entry["path"], entry["rule"], entry["scope"]) in current, (
            f"entrée périmée (le site a été corrigé ou déplacé) : {entry}. Retirez-la : sinon le site pourrait "
            "revenir plus tard sans que rien ne bronche."
        )


def test_the_detector_sees_what_it_must_and_only_that():
    """Le garde ne doit ni rater un cas (il passerait à vide) ni crier sur un bloc qui journalise."""
    source = '''
import logging
LOGGER = logging.getLogger(__name__)

class Panel:
    def bare(self):
        try:
            work()
        except:
            pass

    def ellipsis(self):
        try:
            work()
        except Exception as error:
            ...

    def tuple_and_base(self):
        try:
            work()
        except (OSError, BaseException):
            pass

    def logged(self):
        try:
            work()
        except Exception:
            LOGGER.debug("échec", exc_info=True)
            pass

    def narrow(self):
        try:
            work()
        except OSError:
            pass

def check(value):
    assert value is not None
'''
    found = _scan_source(source)
    assert [(rule, scope) for _path, rule, scope, _line in found] == [
        ("except-pass", "Panel.bare"),
        ("except-pass", "Panel.ellipsis"),
        ("except-pass", "Panel.tuple_and_base"),
        ("assert", "check"),
    ]


def test_the_scan_covers_core_ui_and_both_entry_points():
    files = {path.relative_to(ROOT).as_posix() for path in _production_files()}
    assert {"main.py", "build.py", "core/project_io.py", "ui/main_window.py"} <= files
    assert not any(name.startswith(("tests/", "tools/")) for name in files)
