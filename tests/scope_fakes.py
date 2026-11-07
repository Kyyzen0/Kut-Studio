"""Faux pont des signets à portée de sécurité pour les tests (ADR-0013, étape 5) : aucun bac à sable réel, aucune bibliothèque."""

from __future__ import annotations

import os
from pathlib import Path

from core.security_scope import bridge as sb
from core.security_scope.bridge import OpenedScope
from core.security_scope.manager import ScopeManager
from core.security_scope.store import BookmarkStore


class FakeScopeBridge:
    """Un signet est le texte ``bm:<n>:<chemin>`` ; ``open`` compte les accès ouverts, ``close`` les referme (une fois chacun).

    Programmable : ``unresolvable`` / ``denied`` / ``stale`` (ensembles de chemins), ``refresh`` (un signet stale est recréé ou non),
    ``fail_bookmark`` (création refusée).
    """

    def __init__(self) -> None:
        self.generation = 0
        self.unresolvable: set[str] = set()
        self.denied: set[str] = set()
        self.stale: set[str] = set()
        self.refresh = True
        self.fail_bookmark: set[str] = set()
        self.handles: dict[int, str] = {}
        self.next_handle = 1
        self.opened_log: list[str] = []
        self.closed_log: list[int] = []
        self.created: list[str] = []

    def create_bookmark(self, path: str) -> str | None:
        if path in self.fail_bookmark or not os.path.exists(path):
            return None
        self.generation += 1
        self.created.append(path)
        return f"bm:{self.generation}:{path}"

    def open(self, bookmark: str) -> tuple[int, OpenedScope | None]:
        path = bookmark.split(":", 2)[2]
        if path in self.unresolvable:
            return sb.UNRESOLVABLE, None
        if path in self.denied:
            return sb.DENIED, None
        handle = self.next_handle
        self.next_handle += 1
        self.handles[handle] = path
        self.opened_log.append(path)
        stale = path in self.stale
        refreshed = None
        if stale and self.refresh:
            self.generation += 1
            refreshed = f"bm:{self.generation}:{path}"
            self.stale.discard(path)
        return sb.OK, OpenedScope(handle, path, stale, refreshed)

    def close(self, handle: int) -> bool:
        if handle not in self.handles:
            return False
        del self.handles[handle]
        self.closed_log.append(handle)
        return True

    def open_count(self) -> int:
        return len(self.handles)


def make_manager(tmp_path: Path, bridge: FakeScopeBridge | None = None) -> tuple[ScopeManager, FakeScopeBridge, BookmarkStore]:
    bridge = bridge or FakeScopeBridge()
    store = BookmarkStore(tmp_path / "container")
    return ScopeManager(bridge, store), bridge, store
