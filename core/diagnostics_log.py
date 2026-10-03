"""Journal de diagnostic de l'application et crochets d'exceptions non rattrapées.

L'application empaquetée n'a pas de console (``--windowed``) : une exception levée dans un slot Qt, un
thread de travail ou un destructeur disparaissait sans laisser de trace. :func:`install_diagnostics`
écrit ces erreurs, avec leur pile, dans un fichier tournant du dossier de journaux de la plateforme.

Rien ici ne doit jamais empêcher le démarrage : un dossier illisible ou en lecture seule donne un
journal vers ``stderr`` seulement.
"""

from __future__ import annotations

import logging
import platform
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from .platform_paths import user_log_dir

LOG_FILE_NAME = "kut-studio.log"
MAX_BYTES = 1_000_000
BACKUP_COUNT = 3
_FORMAT = "%(asctime)s %(levelname)-8s %(threadName)s %(name)s : %(message)s"
_MARKER = "_kut_diagnostics_handler"
LOGGER = logging.getLogger("kut")
LOGGER_ROOTS = ("kut", "kut_studio", "core", "ui")
"""Racines des journaux de l'application : ``kut`` (ce module), ``kut_studio.*`` (encodage, décodage, GPU : choix
d'encodeur, repli de décodage, état du GPU), puis les modules qui journalisent sous leur nom (``core.…``, ``ui.…``)."""


class _Installed:
    """État à restaurer : crochets d'origine et gestionnaires ajoutés."""

    def __init__(self, handler: logging.Handler, log_path: Path | None) -> None:
        self.handler = handler
        self.log_path = log_path
        self.excepthook = sys.excepthook
        self.threading_hook = threading.excepthook
        self.unraisable_hook = sys.unraisablehook


_installed: _Installed | None = None


def log_file_path(log_dir: str | Path | None = None) -> Path:
    return user_log_dir(log_dir) / LOG_FILE_NAME


def _open_handler(log_dir: str | Path | None) -> tuple[logging.Handler, Path | None]:
    path = log_file_path(log_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler: logging.Handler = RotatingFileHandler(
            path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8", delay=True
        )
        return handler, path
    except OSError:
        # Application graphique sous Windows : PyInstaller met ``sys.stderr`` à ``None``.
        return (logging.StreamHandler(sys.stderr) if sys.stderr is not None else logging.NullHandler()), None


def install_diagnostics(log_dir: str | Path | None = None) -> Path | None:
    """Active le journal fichier et les crochets d'exceptions. Idempotent.

    Returns:
        Le fichier de journal, ou ``None`` s'il n'a pas pu être créé (le journal va alors sur ``stderr``).
    """
    global _installed
    if _installed is not None:
        return _installed.log_path
    handler, path = _open_handler(log_dir)
    handler.setFormatter(logging.Formatter(_FORMAT))
    setattr(handler, _MARKER, True)
    for name in LOGGER_ROOTS:
        logging.getLogger(name).addHandler(handler)
        logging.getLogger(name).setLevel(logging.INFO)
    state = _Installed(handler, path)
    _installed = state

    def excepthook(exc_type, exc, tb) -> None:
        LOGGER.critical("Exception non rattrapée", exc_info=(exc_type, exc, tb))
        state.excepthook(exc_type, exc, tb)

    def thread_hook(args: threading.ExceptHookArgs) -> None:
        name = args.thread.name if args.thread is not None else "?"
        exc_info: Any = (args.exc_type, args.exc_value, args.exc_traceback)   # ``exc_value`` peut être ``None``
        LOGGER.critical("Exception non rattrapée dans le thread %s", name, exc_info=exc_info)
        state.threading_hook(args)

    def unraisable_hook(args: object) -> None:
        LOGGER.error(
            "Exception ignorée (%s)",
            getattr(args, "err_msg", None) or "destructeur",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),  # type: ignore[attr-defined]
        )
        state.unraisable_hook(args)  # type: ignore[arg-type]

    sys.excepthook = excepthook
    threading.excepthook = thread_hook
    sys.unraisablehook = unraisable_hook
    LOGGER.info(
        "Démarrage — Python %s, %s, %s", platform.python_version(), platform.platform(), _qt_versions()
    )
    return path


def uninstall_diagnostics() -> None:
    """Restaure les crochets d'origine et retire le journal (tests, arrêt propre)."""
    global _installed
    state, _installed = _installed, None
    if state is None:
        return
    sys.excepthook = state.excepthook
    threading.excepthook = state.threading_hook
    sys.unraisablehook = state.unraisable_hook
    for name in LOGGER_ROOTS:
        logging.getLogger(name).removeHandler(state.handler)
    state.handler.close()


def _qt_versions() -> str:
    try:
        import PySide6

        return f"PySide6 {PySide6.__version__}"
    except Exception:  # noqa: BLE001 - le diagnostic ne doit jamais empêcher de démarrer
        return "PySide6 indisponible"


__all__ = ["install_diagnostics", "log_file_path", "uninstall_diagnostics"]
