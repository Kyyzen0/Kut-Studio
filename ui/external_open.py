"""Ouverture d'un projet ``.kut`` confiée par le système.

Trois chemins mènent à la même action, ``MainWindow.open_external_project`` :

* macOS : le Finder envoie un ``QFileOpenEvent`` à l'application (double-clic, « Ouvrir avec ») ;
* Windows et Linux : le système lance l'application avec le chemin du fichier en argument ;
* un argument de ligne de commande, pour un démarrage depuis un terminal.

Le type ``.kut`` est déclaré à macOS dans ``build.py`` ; sous Windows et Linux, l'association du type à
l'application reste à faire (installeur ou entrée de registre), elle n'est pas dans ce module.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from PySide6.QtCore import QEvent, QObject

PROJECT_SUFFIX = ".kut"


def is_project_path(path: str) -> bool:
    """Vrai pour un chemin qui désigne un projet Kut-Studio, quelle que soit la casse de l'extension."""
    return path.lower().endswith(PROJECT_SUFFIX)


def project_path_from_arguments(arguments: Sequence[str]) -> str | None:
    """Premier argument qui désigne un projet ; les options (``--…``) et les autres fichiers sont ignorés."""
    for argument in arguments:
        if not argument.startswith("-") and is_project_path(argument):
            return argument
    return None


class _ExternalOpenFilter(QObject):
    """Filtre installé sur l'application : il intercepte les demandes d'ouverture de fichier."""

    def __init__(self, open_project: Callable[[str], None], parent: QObject) -> None:
        super().__init__(parent)
        self._open_project = open_project

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - API Qt
        if event.type() == QEvent.Type.FileOpen:
            path = event.file()  # seul type d'événement qui porte un chemin : un QFileOpenEvent
            if is_project_path(path):
                self._open_project(path)
                return True
        return False


def install_external_open(app: QObject, open_project: Callable[[str], None]) -> QObject:
    """Branche ``open_project`` sur les demandes d'ouverture de fichier de ``app``.

    Le filtre appartient à ``app`` : il vit aussi longtemps que l'application. Un fichier qui n'est pas un
    projet est laissé à Qt, comme avant.
    """
    handler = _ExternalOpenFilter(open_project, app)
    app.installEventFilter(handler)
    return handler
