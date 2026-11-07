"""Éléments de liste / d'arbre : la dernière référence Python ne tombe jamais dans le destructeur C++ (``ui/qt_item_lifetime``).

Défaut mesuré (voir la docstring du module) : ``Shiboken::Object::destroy`` relit l'objet Python qu'il vient de laisser
libérer quand la liste ne tenait que cette référence ; selon le ramasse-miettes et l'allocateur, segfault sans trace
(« worker 'gwN' crashed »). Le test observe la condition elle-même, sans dépendre de la chance : un élément-sonde dont
``__del__`` note si l'objet C++ existe encore à la libération. Libéré pendant la destruction C++ (objet encore valide),
l'élément est exposé au défaut ; retenu, il n'est libéré qu'une fois l'objet C++ détruit.
"""

from __future__ import annotations

import ast
import gc
from pathlib import Path

import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QListWidget, QListWidgetItem, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from ui import qt_item_lifetime
from ui.qt_item_lifetime import retain

ROOT = Path(__file__).resolve().parent.parent
ITEM_CLASSES = {"QListWidgetItem", "QTreeWidgetItem", "QTableWidgetItem"}


class _ProbeListItem(QListWidgetItem):
    """Note, à sa libération, si son objet C++ existait encore."""

    seen: list[bool] = []

    def __del__(self) -> None:
        type(self).seen.append(shiboken6.isValid(self))


class _ProbeTreeItem(QTreeWidgetItem):
    seen: list[bool] = []

    def __del__(self) -> None:
        type(self).seen.append(shiboken6.isValid(self))


def _deliver_deletes() -> None:
    for _ in range(3):
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        QApplication.processEvents()


def _fill(items: type, widget, count: int = 6):
    for index in range(count):
        item = retain(items(f"element {index}"))
        (widget.addItem if isinstance(widget, QListWidget) else widget.addTopLevelItem)(item)


def _settle() -> None:
    """Rend la main à tout ce qui retenait un élément déjà détruit côté C++ (hors de tout destructeur)."""
    gc.collect()
    qt_item_lifetime.prune()
    gc.collect()


def test_a_cleared_list_never_frees_an_item_inside_its_cpp_destructor(qapp):
    _ProbeListItem.seen.clear()
    widget = QListWidget()
    _fill(_ProbeListItem, widget)
    widget.clear()
    assert _ProbeListItem.seen == [], "un élément a été libéré pendant la destruction C++ (sans retain)"
    _settle()
    assert _ProbeListItem.seen and not any(_ProbeListItem.seen), "libéré alors que l'objet C++ existait encore"


def test_a_destroyed_window_never_frees_its_items_inside_the_cpp_destructor(qapp):
    _ProbeListItem.seen.clear()
    _ProbeTreeItem.seen.clear()
    window = QWidget()
    layout = QVBoxLayout(window)
    listing, tree = QListWidget(), QTreeWidget()
    layout.addWidget(listing)
    layout.addWidget(tree)
    _fill(_ProbeListItem, listing)
    _fill(_ProbeTreeItem, tree)
    window.deleteLater()
    _deliver_deletes()
    assert _ProbeListItem.seen == [] and _ProbeTreeItem.seen == []
    del window, listing, tree, layout
    _settle()
    assert _ProbeListItem.seen and _ProbeTreeItem.seen
    assert not any(_ProbeListItem.seen) and not any(_ProbeTreeItem.seen)


def test_the_registry_releases_destroyed_items_and_keeps_live_ones(qapp):
    qt_item_lifetime.prune()
    baseline = qt_item_lifetime.retained_count()
    widget = QListWidget()
    items = [retain(QListWidgetItem(str(index))) for index in range(50)]
    for item in items:
        widget.addItem(item)
    del items
    assert qt_item_lifetime.retained_count() == baseline + 50
    assert qt_item_lifetime.prune() == 0                      # objets C++ vivants : rien n'est lâché
    widget.clear()
    assert qt_item_lifetime.prune() == 50                     # objets C++ détruits : tout est lâché
    assert qt_item_lifetime.retained_count() == baseline


def test_the_registry_does_not_grow_without_bound(qapp):
    qt_item_lifetime.prune()
    widget = QListWidget()
    for _ in range(40):
        for index in range(100):
            widget.addItem(retain(QListWidgetItem(str(index))))
        widget.clear()
    assert qt_item_lifetime.retained_count() <= 2 * 256 + 100     # nettoyage amorti, jamais 4 000 éléments


def _item_calls(path: Path) -> list[tuple[int, bool]]:
    """(ligne, sous ``retain(...)``) de chaque construction d'un élément de liste, d'arbre ou de tableau."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    parents = {id(child): node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ITEM_CLASSES:
            parent = parents.get(id(node))
            wrapped = (
                isinstance(parent, ast.Call) and isinstance(parent.func, ast.Name) and parent.func.id == "retain"
            )
            found.append((node.lineno, wrapped))
    return found


def test_every_item_created_in_the_ui_goes_through_retain():
    """Garde : ``retain(QListWidgetItem(...))`` partout dans ``ui/`` ; un élément oublié rouvre le segfault intermittent."""
    unwrapped = [
        f"{path.relative_to(ROOT)}:{line}"
        for path in sorted((ROOT / "ui").rglob("*.py"))
        for line, wrapped in _item_calls(path)
        if not wrapped
    ]
    assert not unwrapped, "éléments créés sans retain(...) : " + ", ".join(unwrapped)
