"""Quels chemins de l'utilisateur une opération touche : arguments d'une commande, plan de rendu (ADR-0013, étape 5).

Deux sources, jamais un contournement : ce sont des CANDIDATS ; seuls ceux qui ont un signet ouvrent un accès
(:meth:`~core.security_scope.manager.ScopeManager.access`), les autres sont rapportés « à relier ».

* :func:`command_paths` : les arguments qui sont des chemins absolus, ou qui en contiennent un entre apostrophes / guillemets
  (``subtitles=filename='/x/y.srt'``). C'est le filet commun de tout sous-processus lancé par ``process_supervisor`` (ffprobe,
  FFmpeg des miniatures, des formes d'onde, des proxies…). Borné : 512 arguments, 4096 octets par chemin.
* :func:`plan_paths` : tout champ ``*path`` d'un plan de rendu, y compris des plans imbriqués et des compositions (les fichiers
  ``.ffconcat`` citent les médias SANS que les chemins figurent dans la commande : l'accès doit être ouvert par la tâche entière).
"""

from __future__ import annotations

import dataclasses
import os
import re
from collections.abc import Iterable
from typing import Any

MAX_ARGUMENTS = 512
MAX_PATH_BYTES = 4096
MAX_NODES = 200_000
MAX_DEPTH = 16

_QUOTED_PATH = re.compile(r"""['"](/[^'"\n\0]{1,4096})['"]""")


def _usable(text: str) -> bool:
    return 0 < len(text.encode("utf-8", "replace")) <= MAX_PATH_BYTES and "\n" not in text and "\0" not in text


def command_paths(command: Iterable[Any]) -> list[str]:
    """Chemins absolus POSIX présents dans ``command`` (sans doublon, dans l'ordre). Rien d'autre n'est lu."""
    found: dict[str, None] = {}
    for index, argument in enumerate(command):
        if index >= MAX_ARGUMENTS:
            break
        try:
            text = os.fspath(argument)
        except TypeError:
            continue
        if not isinstance(text, str) or len(text) > 8 * MAX_PATH_BYTES:
            continue
        if text.startswith("/") and _usable(text):
            found[text] = None
        elif "/" in text:
            for match in _QUOTED_PATH.findall(text):
                found[match] = None
    return list(found)


def plan_paths(plan: Any) -> list[str]:
    """Valeurs non vides des champs ``*path`` du plan (récursif dans les dataclasses, tuples, listes, dictionnaires), bornées."""
    found: dict[str, None] = {}
    budget = [MAX_NODES]

    def visit(node: Any, depth: int) -> None:
        if depth > MAX_DEPTH or budget[0] <= 0:
            return
        budget[0] -= 1
        if dataclasses.is_dataclass(node) and not isinstance(node, type):
            for item in dataclasses.fields(node):
                value = getattr(node, item.name, None)
                if isinstance(value, str):
                    if item.name.endswith("path") and value.startswith("/") and _usable(value):
                        found[value] = None
                elif value is not None and not isinstance(value, (int, float, bool, bytes)):
                    visit(value, depth + 1)
        elif isinstance(node, (tuple, list, set, frozenset)):
            for child in node:
                visit(child, depth + 1)
        elif isinstance(node, dict):
            for child in node.values():
                visit(child, depth + 1)

    visit(plan, 0)
    return list(found)


__all__ = ["MAX_ARGUMENTS", "MAX_DEPTH", "MAX_NODES", "MAX_PATH_BYTES", "command_paths", "plan_paths"]
