"""Le gestionnaire d'accès : créer, résoudre, ouvrir et FERMER les accès (ADR-0013, étape 5). Sans Qt, pont injectable.

* :meth:`ScopeManager.remember` : après un choix de l'utilisateur dans un panneau (le système vient de lui donner un accès pour la
  session), crée et range le signet qui le rendra au prochain lancement. Un chemin déjà couvert par un signet de dossier n'en crée pas.
* :meth:`ScopeManager.access` : gestionnaire de contexte autour de CHAQUE lecture / écriture hors conteneur (et de tout sous-processus
  qui lit ou écrit ces chemins : FFmpeg hérite du bac à sable, l'accès doit rester ouvert jusqu'à sa fin). Il ferme TOUJOURS, y compris
  sur exception. Un accès par signet, partagé et compté si plusieurs lecteurs le demandent (:attr:`open_count`).
* :meth:`ScopeManager.resolve_all` : au lancement, résout chaque signet : périmé et recréable => remplacé en silence ; périmé non
  recréable ou introuvable => listé dans ``lost`` (l'interface le redemande).
* Sans pont (bibliothèque absente ou invalide) : AUCUN accès, jamais un contournement. Un chemin sans signet n'échoue pas ici : il est
  rapporté dans :attr:`AccessReport.missing` et l'appelant (ouverture d'un projet) le traite comme « hors ligne ».
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Protocol

from . import bridge as scope_bridge
from .bridge import OpenedScope
from .store import BookmarkEntry, BookmarkStore, canonical

LOGGER = logging.getLogger(__name__)


class BridgeLike(Protocol):
    """Ce que le gestionnaire attend du pont (le vrai :class:`ScopeBridge` ou un faux de test)."""

    def create_bookmark(self, path: str) -> str | None: ...
    def open(self, bookmark: str) -> tuple[int, OpenedScope | None]: ...
    def close(self, handle: int) -> bool: ...
    def open_count(self) -> int: ...


@dataclass(frozen=True)
class AccessReport:
    """Ce qu'un :meth:`ScopeManager.access` a obtenu. Les chemins sont canoniques ; ``requested`` n'est qu'un compteur d'affichage.

    * ``opened`` : couverts par un signet dont l'accès est ouvert (un signet de dossier couvre plusieurs chemins).
    * ``missing`` : aucun signet (jamais choisi, ou pont absent) : à relier par l'utilisateur.
    * ``lost`` : un signet existe mais n'a pu être ouvert (supprimé, volume absent, refusé) : à relier aussi.
    """

    opened: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    lost: tuple[str, ...] = ()

    @property
    def needs_relink(self) -> tuple[str, ...]:
        return self.missing + self.lost


EMPTY_REPORT = AccessReport()


@dataclass(frozen=True)
class LaunchReport:
    """Résultat de :meth:`ScopeManager.resolve_all`."""

    resolved: int = 0
    refreshed: int = 0
    lost: tuple[str, ...] = ()
    stale: tuple[str, ...] = ()


class ScopeManager:
    def __init__(self, bridge: BridgeLike | None, store: BookmarkStore) -> None:
        self._bridge = bridge
        self._store = store
        self._lock = threading.RLock()
        self._active: dict[str, list] = {}           # chemin de l'entrée -> [identifiant natif, nombre de lecteurs]
        self._stale: set[str] = set()
        self._broken: set[str] = set()                # signets qui n'ont pas pu être ouverts (refusés)

    @property
    def available(self) -> bool:
        return self._bridge is not None

    @property
    def open_count(self) -> int:
        """Accès actuellement ouverts par ce gestionnaire (un par signet, quel que soit le nombre de lecteurs)."""
        with self._lock:
            return len(self._active)

    def stale_paths(self) -> tuple[str, ...]:
        """Signets que le système juge périmés sans avoir pu les recréer : l'utilisateur doit rechoisir le fichier."""
        with self._lock:
            return tuple(sorted(self._stale))

    # -- création --------------------------------------------------------------------------------------------------------

    def remember(self, path: str) -> bool:
        """Crée et range le signet de ``path`` (à appeler juste après le choix de l'utilisateur). ``False`` si impossible."""
        if self._bridge is None:
            return False
        try:
            key = canonical(path)
        except ValueError:
            return False
        existing = self._store.find(key)
        # Un choix explicite refait le signet de ce chemin (l'ancien peut être périmé ou perdu). Seul un signet de DOSSIER sain qui
        # le couvre déjà dispense d'en créer un (sinon importer un dossier de milliers de fichiers remplirait la table).
        if existing is not None and existing.path != key and existing.path not in self._broken and existing.path not in self._stale:
            return True
        bookmark = self._bridge.create_bookmark(key)
        if bookmark is None or not self._store.put(key, bookmark):
            return False
        with self._lock:
            self._stale.discard(key)
            self._broken.discard(key)
        return True

    def forget(self, path: str) -> bool:
        return self._store.remove(path)

    # -- accès -----------------------------------------------------------------------------------------------------------

    def _acquire(self, entry: BookmarkEntry) -> bool:
        """Ouvre (ou partage) l'accès de ``entry`` ; ``False`` si le système le refuse."""
        bridge = self._bridge
        if bridge is None:
            return False
        with self._lock:
            active = self._active.get(entry.path)
            if active is not None:
                active[1] += 1
                return True
            status, opened = bridge.open(entry.bookmark)
            if opened is None:
                if status == scope_bridge.UNRESOLVABLE:
                    self._store.remove(entry.path)    # ne désigne plus rien : inutile de le garder, l'utilisateur doit rechoisir
                else:
                    self._broken.add(entry.path)
                return False
            self._broken.discard(entry.path)
            if opened.stale:
                if opened.refreshed and self._store.put(entry.path, opened.refreshed):
                    self._stale.discard(entry.path)
                else:
                    self._stale.add(entry.path)
            else:
                self._stale.discard(entry.path)
            self._active[entry.path] = [opened.handle, 1]
            return True

    def _release(self, key: str) -> None:
        with self._lock:
            active = self._active.get(key)
            if active is None:
                return
            active[1] -= 1
            if active[1] > 0:
                return
            del self._active[key]
            handle = active[0]
        bridge = self._bridge
        if bridge is None:
            return
        try:
            if not bridge.close(handle):
                LOGGER.warning("Accès à un fichier de l'utilisateur : fermeture refusée par le pont")
        except Exception:                                    # noqa: BLE001 - la fermeture ne doit jamais masquer l'exception du corps
            LOGGER.warning("Accès à un fichier de l'utilisateur : fermeture en erreur", exc_info=False)

    @contextmanager
    def access(self, *paths: str) -> Iterator[AccessReport]:
        """Ouvre l'accès aux ``paths`` pour la durée du bloc ; ferme TOUJOURS, y compris sur exception.

        Ne lève pas pour un signet manquant : voir :class:`AccessReport`. Les ``paths`` vides ou invalides sont ignorés."""
        keys: list[str] = []
        for path in paths:
            try:
                keys.append(canonical(path))
            except (ValueError, TypeError, OSError):
                continue
        if self._bridge is None:
            yield AccessReport(missing=tuple(dict.fromkeys(keys)))
            return
        held: list[str] = []
        opened: dict[str, None] = {}
        missing: dict[str, None] = {}
        lost: dict[str, None] = {}
        try:
            for key in dict.fromkeys(keys):
                entry = self._store.find(key)
                if entry is None:
                    missing[key] = None
                elif self._acquire(entry):
                    held.append(entry.path)
                    opened[key] = None
                else:
                    lost[key] = None
            yield AccessReport(tuple(opened), tuple(missing), tuple(lost))
        finally:
            for key in reversed(held):
                self._release(key)

    # -- lancement -------------------------------------------------------------------------------------------------------

    def resolve_all(self) -> LaunchReport:
        """Résout chaque signet rangé (au lancement), le rouvre puis le ferme aussitôt ; remplace les signets périmés recréables."""
        if self._bridge is None:
            return LaunchReport()
        resolved = refreshed = 0
        lost: list[str] = []
        stale: list[str] = []
        for entry in self._store.entries():
            status, opened = self._bridge.open(entry.bookmark)
            if opened is None:
                lost.append(entry.path)
                if status == scope_bridge.UNRESOLVABLE:
                    self._store.remove(entry.path)
                continue
            try:
                resolved += 1
                if opened.stale:
                    if opened.refreshed and self._store.put(entry.path, opened.refreshed):
                        refreshed += 1
                    else:
                        stale.append(entry.path)
            finally:
                self._bridge.close(opened.handle)
        with self._lock:
            self._stale = set(stale)
        return LaunchReport(resolved, refreshed, tuple(lost), tuple(stale))


def keys_of(paths: Sequence[str]) -> list[str]:
    """Chemins canoniques des ``paths`` valides (utilitaire d'affichage)."""
    result = []
    for path in paths:
        try:
            result.append(canonical(path))
        except (ValueError, TypeError, OSError):
            continue
    return result


__all__ = ["AccessReport", "BridgeLike", "EMPTY_REPORT", "LaunchReport", "ScopeManager", "keys_of"]
