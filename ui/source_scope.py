"""Accès au fichier que le lecteur du moniteur lit (ADR-0013, étape 5).

``QMediaPlayer`` lit son média pendant toute la lecture, de façon asynchrone : l'accès par signet doit durer autant que la source
courante, pas seulement l'appel à ``setSource``. Un seul accès à la fois ; changer de source ou libérer referme le précédent. Hors Store
(``scoped_access`` rend un bloc vide), aucun effet.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Any

from core.security_scope import scoped_access


class SourceScope:
    def __init__(self) -> None:
        self._scope: AbstractContextManager[Any] | None = None

    def hold(self, path: str | None) -> None:
        """Referme l'accès précédent puis ouvre celui de ``path`` (vide : seulement refermer)."""
        self.release()
        if path:
            scope = scoped_access(path)
            scope.__enter__()
            self._scope = scope

    def release(self) -> None:
        scope, self._scope = self._scope, None
        if scope is not None:
            scope.__exit__(None, None, None)

    @property
    def active(self) -> bool:
        return self._scope is not None


__all__ = ["SourceScope"]
