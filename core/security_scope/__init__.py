"""Accès aux fichiers de l'utilisateur dans le bac à sable du Mac App Store (ADR-0013, étape 5) : signets à portée de sécurité.

Façade utilisée par le reste de l'application. **Hors Store (``core.build_flavor.is_store()`` faux) rien ne change** : 
:func:`remember_user_path` ne fait rien, :func:`scoped_access` rend un bloc vide (``nullcontext``), :func:`start_for_store` n'est
pas appelé. En Store, un seul gestionnaire (:class:`~core.security_scope.manager.ScopeManager`) est installé au lancement ; sans
lui (ou sans pont) aucun accès n'est ouvert, jamais un contournement.

Usage :

* après un choix dans un panneau : ``remember_user_path(chemin)`` ;
* autour d'une lecture / écriture hors conteneur, ou d'un sous-processus qui lit ou écrit ces chemins :
  ``with scoped_access(chemin, ...) as acces:`` ; ``acces.needs_relink`` liste les chemins à relier (média « hors ligne »).
"""

from __future__ import annotations

import functools
import os
from collections.abc import Callable, Iterable
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path
from typing import Any, TypeVar, cast

from .. import build_flavor
from .bridge import ScopeBridge, ScopeBridgeError, bundled_dylib_path
from .paths import command_paths, plan_paths
from .manager import EMPTY_REPORT, AccessReport, LaunchReport, ScopeManager
from .store import BookmarkStore

_F = TypeVar("_F", bound=Callable[..., Any])
_manager: ScopeManager | None = None


def install_manager(manager: ScopeManager | None) -> None:
    """Installe (ou retire, avec ``None``) le gestionnaire. Les tests l'installent avec un faux pont."""
    global _manager
    _manager = manager


def current_manager() -> ScopeManager | None:
    """Le gestionnaire installé, ``None`` hors Store ou avant :func:`start_for_store`."""
    return _manager if build_flavor.is_store() else None


def bundled_bridge_loader() -> ScopeBridge:
    """Charge la bibliothèque du bundle (jamais par variable d'environnement) ; ``ScopeBridgeError`` si absente ou invalide."""
    path = bundled_dylib_path()
    if path is None:
        raise ScopeBridgeError("pont des signets hors d'un bundle")  # i18n-ignore: message technique, jamais affiché
    return ScopeBridge.load(path)


def start_for_store(
    directory: str | Path | None = None, bridge_loader: Callable[[], ScopeBridge] = bundled_bridge_loader,
) -> LaunchReport:
    """Installe le gestionnaire du build Store et résout les signets rangés. Hors Store : ne fait rien.

    Pont absent ou invalide : le gestionnaire est installé SANS pont (aucun accès ouvert, tout chemin est « à relier »)."""
    if not build_flavor.is_store():
        return LaunchReport()
    from ..platform_paths import user_config_dir

    try:
        bridge: ScopeBridge | None = bridge_loader()
    except ScopeBridgeError:
        bridge = None
    manager = ScopeManager(bridge, BookmarkStore(Path(directory) if directory is not None else user_config_dir()))
    install_manager(manager)
    return manager.resolve_all()


def remember_user_path(path: str | Path | None) -> bool:
    """Range le signet d'un chemin que l'utilisateur vient de choisir. Hors Store : ``False``, rien n'est fait."""
    manager = current_manager()
    if manager is None or not path:
        return False
    return manager.remember(str(path))


def scoped_access(*paths: str | Path | None) -> AbstractContextManager[AccessReport]:
    """Ouvre l'accès aux chemins pour la durée du bloc ; hors Store (ou sans gestionnaire) : bloc vide, aucun effet."""
    manager = current_manager()
    if manager is None:
        return nullcontext(EMPTY_REPORT)
    return manager.access(*[str(path) for path in paths if path])


def scoped_argument(index: int = 0) -> Callable[[_F], _F]:
    """Décorateur : la fonction s'exécute avec l'accès au chemin de son argument positionnel ``index`` (ou ``file_path`` / ``path``
    par mot-clé). Hors Store : aucun effet (le bloc est vide). Garde la signature et la docstring."""

    def decorate(function: _F) -> _F:
        @functools.wraps(function)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            target = args[index] if len(args) > index else kwargs.get("file_path", kwargs.get("path"))
            with scoped_access(os.fspath(target) if isinstance(target, (str, os.PathLike)) else None):
                return function(*args, **kwargs)

        return cast(_F, wrapper)

    return decorate


def command_scope(command: Iterable[Any]) -> AbstractContextManager[AccessReport]:
    """Accès aux chemins absolus d'une commande (filet commun des sous-processus). Hors Store : bloc vide, la commande n'est pas lue."""
    manager = current_manager()
    if manager is None:
        return nullcontext(EMPTY_REPORT)
    return manager.access(*command_paths(command))


def plan_scope(plan: Any, *extra: str | Path | None) -> AbstractContextManager[AccessReport]:
    """Accès aux médias d'un plan de rendu et à ``extra`` (sortie…) pour toute la durée d'un export. Hors Store : bloc vide."""
    manager = current_manager()
    if manager is None:
        return nullcontext(EMPTY_REPORT)
    return manager.access(*plan_paths(plan), *[str(path) for path in extra if path])


__all__ = [
    "command_scope", "plan_scope", "scoped_argument",
    "EMPTY_REPORT", "AccessReport", "BookmarkStore", "LaunchReport", "ScopeBridge", "ScopeBridgeError", "ScopeManager",
    "bundled_bridge_loader", "current_manager", "install_manager", "remember_user_path", "scoped_access", "start_for_store",
]
