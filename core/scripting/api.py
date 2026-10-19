"""La façade Python de Kut-Studio : **la seule surface publique** du mode script (ADR-0005, §5).

Les modules de ``core/`` restent internes et libres d'évoluer ; ce qui est exporté ici est ce qu'un script, un outil ou un LLM peut
appeler sans craindre une rupture silencieuse. Un test (``tests/test_scripting_api.py``) fige la liste des noms publics : en ajouter
ou en retirer est un choix conscient, annoncé dans le journal des modifications.

``API_VERSION`` est ``MAJEUR.MINEUR`` : le mineur monte quand la surface **grandit** ; le majeur monte pour une **rupture** (un nom
retiré ou changé de sens), annoncée dans ``CHANGELOG.md``. Tant que le majeur est ``0``, la surface est incomplète (la lecture
structurelle est là ; les vues typées, ``run`` et les aides viennent ensuite).

Lire un projet ne le modifie jamais : écrire passe par :class:`~core.scripting.session.Session` (``apply`` / ``transaction``).
"""

from __future__ import annotations

from typing import Any

from ..project_model import Project
from .describe import DESCRIBE_VERSION
from .describe import describe as _describe
from .describe import describe_json as _describe_json

API_VERSION = "0.1"
"""Version de la surface publique, ``MAJEUR.MINEUR`` (voir le texte du module)."""


def describe(project: Project, *, check_media: bool = False) -> dict[str, Any]:
    """Le projet comme un dictionnaire JSON versionné (clé ``describe_version``) ; lecture seule, sans accès disque par défaut.

    ``check_media=True`` teste sur le disque la présence de chaque média (``media[].present``) ; sinon elle vaut ``null``.
    """
    return _describe(project, check_media=check_media)


def describe_json(project: Project, *, check_media: bool = False, indent: int | None = 2) -> str:
    """:func:`describe` en texte JSON strict (pas de ``NaN``), mêmes octets pour le même projet."""
    return _describe_json(project, check_media=check_media, indent=indent)


__all__ = ["API_VERSION", "DESCRIBE_VERSION", "describe", "describe_json"]
