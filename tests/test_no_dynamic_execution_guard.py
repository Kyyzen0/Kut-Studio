"""Cliquet R1 (ADR-0005) : le produit n'exécute jamais de code construit à partir de données.

Un projet ``.kut`` (et tout fichier lu par l'application : préréglage, bibliothèque, cache) ne doit **jamais** pouvoir
faire exécuter du code. Aujourd'hui c'est vrai, mais seulement parce que personne n'a écrit ``eval(...)`` ou
``pickle.load(...)`` : rien ne l'empêcherait. Ce test l'empêche. Il lit le code en AST (donc un mot dans un
commentaire ou une chaîne ne compte pas) sur ``core/``, ``ui/``, ``main.py`` et ``build.py`` (``tests/`` et ``tools/``
exclus) et refuse :

* **dynamic-code** : ``eval``, ``exec``, ``compile`` (le builtin : ``re.compile`` n'est pas concerné) et ``__import__`` ;
* **dynamic-import** : ``importlib``, ``runpy``, ``imp`` importés, et les appels qui chargent du code
  (``import_module``, ``spec_from_file_location``, ``module_from_spec``, ``exec_module``, ``load_module``) ;
* **unsafe-deserialization** : ``pickle``, ``cPickle``, ``marshal``, ``shelve``, ``dill``, ``cloudpickle`` importés,
  ``yaml.load`` / ``unsafe_load`` / ``full_load``, et ``numpy.load`` sans ``allow_pickle=False`` ;
* **shell** : ``os.system``, ``os.popen``, et tout ``subprocess`` avec ``shell=True`` (les arguments se passent en
  liste, jamais par un shell : un nom de fichier venu d'un projet n'y devient pas une commande).

``tests/dynamic_execution_allowlist.json`` liste les sites tolérés, chacun avec sa raison. Une entrée est identifiée
par fichier, règle et portée (``Classe.methode``), pas par numéro de ligne, qui bouge. Un ajout apparaît dans la
revue de la PR : une exception visible, au lieu d'une exception silencieuse.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALLOWLIST = ROOT / "tests" / "dynamic_execution_allowlist.json"
RULES = frozenset({"dynamic-code", "dynamic-import", "unsafe-deserialization", "shell"})

DYNAMIC_CODE_CALLS = frozenset({"eval", "exec", "compile"})
DYNAMIC_IMPORT_MODULES = frozenset({"importlib", "runpy", "imp"})
CODE_LOADING_ATTRIBUTES = frozenset(
    {
        "import_module",
        "spec_from_file_location",
        "module_from_spec",
        "exec_module",
        "load_module",
    }
)
UNSAFE_MODULES = frozenset(
    {"pickle", "cPickle", "_pickle", "marshal", "shelve", "dill", "cloudpickle"}
)
UNSAFE_YAML_CALLS = frozenset(
    {"load", "unsafe_load", "full_load", "load_all", "unsafe_load_all", "full_load_all"}
)
SHELL_OS_CALLS = frozenset({"system", "popen"})


def _production_files() -> list[Path]:
    files = sorted(
        path for package in ("core", "ui") for path in (ROOT / package).rglob("*.py")
    )
    return files + [ROOT / "main.py", ROOT / "build.py"]


def _root_names(module: str | None) -> set[str]:
    return {(module or "").split(".")[0]}


def _attribute_call(node: ast.Call) -> tuple[str, str] | None:
    """``(objet, attribut)`` d'un appel ``objet.attribut(...)`` dont l'objet est un nom simple, sinon ``None``."""
    func = node.func
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return func.value.id, func.attr
    return None


def _keyword(node: ast.Call, name: str) -> ast.expr | None:
    return next(
        (keyword.value for keyword in node.keywords if keyword.arg == name), None
    )


def _is_true(node: ast.expr | None) -> bool:
    return isinstance(node, ast.Constant) and node.value is True


def _is_false(node: ast.expr | None) -> bool:
    return isinstance(node, ast.Constant) and node.value is False


class _Scanner(ast.NodeVisitor):
    """Relève les contrevenants avec leur portée (``Classe.methode``) : l'identifiant stable de l'allowlist."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.scope: list[str] = []
        self.found: list[
            tuple[str, str, str, int]
        ] = []  # (chemin, règle, portée, ligne)

    def _visit_scoped(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
    ) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    visit_FunctionDef = visit_AsyncFunctionDef = visit_ClassDef = _visit_scoped

    def _record(self, rule: str, node: ast.AST) -> None:
        self.found.append(
            (self.path, rule, ".".join(self.scope) or "<module>", node.lineno)
        )

    # --- imports ---------------------------------------------------------------------------------------------------

    def _check_module(self, module: str | None, node: ast.AST) -> None:
        root = _root_names(module)
        if root & DYNAMIC_IMPORT_MODULES:
            self._record("dynamic-import", node)
        if root & UNSAFE_MODULES:
            self._record("unsafe-deserialization", node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._check_module(alias.name, node)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self._check_module(node.module, node)
        self.generic_visit(node)

    # --- appels ----------------------------------------------------------------------------------------------------

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Name) and func.id in DYNAMIC_CODE_CALLS:
            self._record("dynamic-code", node)
        attribute = _attribute_call(node)
        if isinstance(func, ast.Attribute) and func.attr in CODE_LOADING_ATTRIBUTES:
            self._record("dynamic-import", node)
        if attribute is not None:
            owner, name = attribute
            if owner == "yaml" and name in UNSAFE_YAML_CALLS:
                self._record("unsafe-deserialization", node)
            if (
                owner in {"np", "numpy"}
                and name == "load"
                and not _is_false(_keyword(node, "allow_pickle"))
            ):
                self._record("unsafe-deserialization", node)
            if owner == "os" and name in SHELL_OS_CALLS:
                self._record("shell", node)
        if _is_true(_keyword(node, "shell")):
            self._record("shell", node)
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        # ``__import__`` appelé ou passé en valeur (``map(__import__, noms)``) : relevé une fois, ici.
        if node.id == "__import__":
            self._record("dynamic-code", node)
        self.generic_visit(node)


def _scan_source(source: str, path: str = "<test>") -> list[tuple[str, str, str, int]]:
    scanner = _Scanner(path)
    scanner.visit(ast.parse(source))
    return sorted(scanner.found)


def _violations() -> list[tuple[str, str, str, int]]:
    found: list[tuple[str, str, str, int]] = []
    for path in _production_files():
        found += _scan_source(
            path.read_text(encoding="utf-8"), path.relative_to(ROOT).as_posix()
        )
    return found


def _allowlist() -> list[dict]:
    return json.loads(ALLOWLIST.read_text(encoding="utf-8"))["entries"]


def _allowed_keys() -> set[tuple[str, str, str]]:
    return {(entry["path"], entry["rule"], entry["scope"]) for entry in _allowlist()}


def _report(found: list[tuple[str, str, str, int]]) -> str:
    return "\n".join(
        f"  {path}:{line} ({rule}, {scope})" for path, rule, scope, line in found
    )


def test_no_production_code_executes_dynamic_code_or_loads_unsafe_data():
    allowed = _allowed_keys()
    found = [violation for violation in _violations() if violation[:3] not in allowed]
    assert not found, (
        f"{len(found)} site(s) qui pourraient exécuter du code venu de données (ADR-0005, règle R1).\n"
        f"{_report(found)}\n"
        "Un projet ou un fichier lu par l'application ne doit jamais faire exécuter de code. Remplacez par une table "
        "fermée (dictionnaire de fonctions), json, np.load(..., allow_pickle=False) ou une liste d'arguments sans shell. "
        "Si l'usage est vraiment nécessaire et ne lit aucune donnée externe, ajoutez le site à "
        "tests/dynamic_execution_allowlist.json avec sa raison."
    )


def test_every_allowlist_entry_is_still_needed_and_justified():
    """Une exception qui n'existe plus dans le code ne doit pas rester : la liste ne fait que rétrécir."""
    present = {violation[:3] for violation in _violations()}
    entries = _allowlist()
    for entry in entries:
        assert entry["rule"] in RULES, f"règle inconnue : {entry}"
        assert str(entry.get("reason", "")).strip(), f"raison manquante : {entry}"
    stale = [
        entry
        for entry in entries
        if (entry["path"], entry["rule"], entry["scope"]) not in present
    ]
    assert not stale, f"entrées inutiles (le site a disparu) : {stale}"


# --- Le détecteur lui-même : chaque règle voit ce qu'elle doit voir, et rien de plus -------------------------------


def _rules(source: str) -> list[str]:
    return [rule for _path, rule, _scope, _line in _scan_source(source)]


def test_the_scanner_flags_each_dangerous_construct():
    assert _rules("eval(x)") == ["dynamic-code"]
    assert _rules("exec(code)") == ["dynamic-code"]
    assert _rules("compile(src, 'f', 'exec')") == ["dynamic-code"]
    assert _rules("__import__(name)") == ["dynamic-code"]
    assert _rules("import importlib") == ["dynamic-import"]
    assert _rules("from importlib import util") == ["dynamic-import"]
    assert _rules("import runpy") == ["dynamic-import"]
    assert _rules("mod = loader.import_module(name)") == ["dynamic-import"]
    assert _rules("spec = util.spec_from_file_location(n, p)") == ["dynamic-import"]
    assert _rules("import pickle") == ["unsafe-deserialization"]
    assert _rules("from pickle import loads") == ["unsafe-deserialization"]
    assert _rules("import marshal") == ["unsafe-deserialization"]
    assert _rules("yaml.load(text)") == ["unsafe-deserialization"]
    assert _rules("yaml.unsafe_load(text)") == ["unsafe-deserialization"]
    assert _rules("np.load(handle)") == ["unsafe-deserialization"]
    assert _rules("np.load(handle, allow_pickle=True)") == ["unsafe-deserialization"]
    assert _rules("os.system(cmd)") == ["shell"]
    assert _rules("os.popen(cmd)") == ["shell"]
    assert _rules("subprocess.run(cmd, shell=True)") == ["shell"]


def test_the_scanner_leaves_safe_look_alikes_alone():
    assert _rules("re.compile('a+')") == []
    assert _rules("PATTERN = _re.compile(r'x')") == []
    assert _rules("np.load(handle, allow_pickle=False)") == []
    assert _rules("yaml.safe_load(text)") == []
    assert _rules("subprocess.run(['ffmpeg', '-i', path], shell=False)") == []
    assert _rules("subprocess.run(['ffmpeg', path])") == []
    assert _rules("text = 'eval(x) and import pickle'  # exec(y)") == []
    assert _rules("def evaluate(x):\n    return x") == []
    assert _rules("model.exec_()") == [], "``exec_`` (Qt) n'est pas ``exec``"
    assert _rules("app.exec()") == [], (
        "``QApplication.exec()`` est une méthode, pas le builtin"
    )


def test_the_scanner_names_the_scope_of_the_offender():
    source = "class Loader:\n    def read(self, text):\n        return eval(text)\n"
    assert _scan_source(source, "core/x.py") == [
        ("core/x.py", "dynamic-code", "Loader.read", 3)
    ]
