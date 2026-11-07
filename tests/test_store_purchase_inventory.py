"""Inventaire (AST) des achats (ADR-0013, étape 15) : aucun achat n'est demandé hors du module d'achat et de son unique point d'entrée Qt."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _trees():
    for base in ("core", "ui", "main.py"):
        path = ROOT / base
        for file in ([path] if path.is_file() else sorted(path.rglob("*.py"))):
            yield file.relative_to(ROOT).as_posix(), ast.parse(file.read_text(encoding="utf-8"))


def _functions(tree: ast.Module):
    """``(qualname, fonction)`` de chaque fonction du module."""
    found = []

    def visit(node, trail):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = (*trail, child.name)
                if not isinstance(child, ast.ClassDef):
                    found.append((".".join(name), child))
                visit(child, name)
            else:
                visit(child, trail)

    visit(tree, ())
    return found


def _mentions(path: str, tree: ast.Module, names: set[str], *, calls_only: bool = False) -> set[tuple[str, str]]:
    hits = set()
    for qualname, function in _functions(tree):
        for node in ast.walk(function):
            if calls_only:
                if isinstance(node, ast.Call):
                    target = node.func
                    name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", None)
                    if name in names:
                        hits.add((path, qualname))
            elif (isinstance(node, ast.Name) and node.id in names) or (isinstance(node, ast.Attribute) and node.attr in names):
                hits.add((path, qualname))
    return hits


def test_the_purchase_operation_is_only_named_by_the_purchase_module():
    users = set()
    for path, tree in _trees():
        users |= _mentions(path, tree, {"OP_PURCHASE"})
    modules = {path for path, _q in users}
    assert modules <= {"core/entitlements/storekit_actions.py"}, sorted(users)
    assert users == {("core/entitlements/storekit_actions.py", "StoreActions._purchase")}, sorted(users)


def test_a_purchase_is_requested_only_from_the_store_controller_and_the_wall():
    callers = set()
    for path, tree in _trees():
        if path == "core/entitlements/storekit_actions.py":
            continue
        callers |= _mentions(path, tree, {"request_purchase"}, calls_only=True)
        callers |= _mentions(path, tree, {"buy"}, calls_only=True)
    assert callers == {
        ("ui/store_entitlements.py", "StoreEntitlementsController.buy"),         # l'unique point d'entrée Qt
        ("ui/store_paywall.py", "PaywallDialog._on_choose"),                     # appelé seulement par un clic sur « Choisir »
    }, sorted(callers)


def test_the_choose_button_is_the_only_trigger_of_the_wall_purchase():
    tree = ast.parse((ROOT / "ui" / "store_paywall.py").read_text(encoding="utf-8"))
    callers = _mentions("ui/store_paywall.py", tree, {"_on_choose"})
    assert callers == {("ui/store_paywall.py", "PaywallDialog._build_ui")}, sorted(callers)       # relié au clic de chaque bouton, nulle part ailleurs


def test_nothing_unlocks_from_a_purchase_result_only_the_verified_reread_publishes():
    """``publish`` (le seul écrivain de l'état) n'est appelé que par la lecture vérifiée de la source ; jamais par l'achat ni par l'interface."""
    callers = set()
    for path, tree in _trees():
        if path.startswith(("core/entitlements/", "ui/store_", "ui/main_window_mixins/entitlements.py")):
            callers |= _mentions(path, tree, {"publish"}, calls_only=True)
    assert callers == {("core/entitlements/storekit_source.py", "StoreKitEntitlementSource._refresh_locked")}, sorted(callers)
    actions = ast.parse((ROOT / "core" / "entitlements" / "storekit_actions.py").read_text(encoding="utf-8"))
    purchase = dict(_functions(actions))["StoreActions._purchase"]
    names = {n.attr for n in ast.walk(purchase) if isinstance(n, ast.Attribute)} | {n.id for n in ast.walk(purchase) if isinstance(n, ast.Name)}
    assert {"_refresh", "_paid"} <= names
    assert "verified" in ast.dump(purchase)                              # le drapeau « vérifié » du pont est contrôlé avant la relecture


def test_the_wall_modules_import_no_storekit_and_the_pure_modules_import_no_qt():
    for path in ("core/entitlements/storekit_actions.py", "core/entitlements/paywall.py"):
        tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
        imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        assert not any(m.startswith(("PySide6", "ui")) for m in imported), (path, imported)
    for path in ("ui/store_paywall.py", "ui/store_license.py", "ui/store_screens.py", "ui/store_texts.py"):
        tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
        imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        assert "core.entitlements.storekit_bridge" not in imported, path         # l'interface ne parle jamais au pont


def test_store_screens_are_imported_only_inside_store_branches():
    allowed = {
        "ui/store_screens.py": {"ui/main_window_mixins/entitlements.py"},
        "ui/store_license.py": {"ui/preferences_dialog.py"},
        "ui/store_paywall.py": {"ui/store_screens.py"},
    }
    for module, importers in allowed.items():
        short = module.removesuffix(".py").replace("/", ".")
        found = set()
        for path, tree in _trees():
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module == short:
                    found.add(path)
                    if path in importers and path != "ui/store_screens.py":
                        top_level = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
                        assert node not in top_level, f"{path} importe {short} au niveau du module (hors Store aussi)"
        assert found == importers, (module, sorted(found))


def test_no_price_is_hard_coded_in_the_application():
    """Les montants viennent de StoreKit (``displayPrice``) : ni 6,99 ni 49,99 ni 99,99 dans le code ou les textes de l'application."""
    offenders = []
    files = [*sorted((ROOT / "core" / "entitlements").rglob("*.py")), *sorted((ROOT / "ui").glob("*store*.py")),
             ROOT / "ui" / "main_window_mixins" / "entitlements.py"]
    for file in files:
        text = file.read_text(encoding="utf-8")
        if any(amount in text for amount in ("6,99", "6.99", "49,99", "49.99", "99,99", "99.99")):
            offenders.append(file.relative_to(ROOT).as_posix())
    assert not offenders, offenders
