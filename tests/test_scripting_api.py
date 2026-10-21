"""La façade ``core/scripting/api.py`` (ADR-0005, §5) : la **seule surface publique** du mode script.

Un test fige la liste des noms publics et leurs signatures : ajouter ou retirer un nom est un choix conscient (CHANGELOG, et
``API_VERSION`` : le mineur monte quand la surface grandit, le majeur pour une rupture).
"""

from __future__ import annotations

import inspect
import re

from core.scripting import api
from core.scripting import describe as describe_module

FROZEN_PUBLIC_NAMES = ["API_VERSION", "DESCRIBE_VERSION", "describe", "describe_json"]
"""Les noms publics, triés. Les modifier est une décision de façade : voir le texte du module ``api``."""


def test_the_public_names_are_frozen():
    assert sorted(api.__all__) == FROZEN_PUBLIC_NAMES, (
        "La surface publique de api.py a changé. Ajouter un nom : montez le mineur d'API_VERSION et mettez à jour cette liste ; "
        "retirer ou changer un nom : c'est une rupture (majeur, annoncée dans le CHANGELOG)."
    )
    assert all(hasattr(api, name) for name in api.__all__)


def test_versions_are_major_dot_minor_and_describe_version_is_an_integer():
    assert re.fullmatch(r"\d+\.\d+", api.API_VERSION)
    assert (
        isinstance(api.DESCRIBE_VERSION, int)
        and api.DESCRIBE_VERSION == describe_module.DESCRIBE_VERSION >= 1
    )


def test_the_signatures_are_frozen():
    assert (
        str(inspect.signature(api.describe))
        == "(project: 'Project', *, check_media: 'bool' = False) -> 'dict[str, Any]'"
    )
    assert str(inspect.signature(api.describe_json)) == (
        "(project: 'Project', *, check_media: 'bool' = False, indent: 'int | None' = 2) -> 'str'"
    )


def test_every_public_callable_is_documented():
    for name in api.__all__:
        value = getattr(api, name)
        if callable(value):
            assert (inspect.getdoc(value) or "").strip(), (
                f"api.{name} n'a pas de docstring"
            )


def test_the_session_exposes_describe_with_the_same_defaults():
    from core.scripting.session import Session

    parameters = inspect.signature(Session.describe).parameters
    assert (
        list(parameters) == ["self", "check_media"]
        and parameters["check_media"].default is False
    )
