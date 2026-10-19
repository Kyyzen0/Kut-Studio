"""Les valeurs JSON de ``describe`` : nombres, textes, énumérations (ADR-0005, étape 5).

Brique commune des modules ``describe*.py`` : une seule règle pour « un nombre JSON déterministe » et « une chaîne que la sortie
sait écrire ». Aucune récursion : :func:`value` ne descend que d'un niveau (un couple ``(x, y)``).
"""

from __future__ import annotations

import math
from enum import Enum
from typing import Any

_DECIMALS = 6


def number(value: Any) -> Any:
    """Un nombre JSON déterministe : flottant arrondi à 6 décimales, ``-0.0`` ramené à ``0.0``, non fini -> ``None``."""
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        return value
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result):
        return None
    rounded = round(result, _DECIMALS)
    return rounded + 0.0 if rounded == 0.0 else rounded


def text(value: Any) -> str:
    """Une chaîne que le JSON et la sortie standard savent écrire (un demi-substitut isolé, possible dans un fichier abîmé, est remplacé)."""
    result = value if isinstance(value, str) else ("" if value is None else str(value))
    try:
        result.encode("utf-8")
    except UnicodeEncodeError:
        return result.encode("utf-8", "replace").decode("utf-8")
    return result


def optional_text(value: Any) -> str | None:
    """Une chaîne, ou ``None`` quand elle est vide (« pas de séquence », « pas d'angle »)."""
    result = text(value)
    return result or None


def enum(value: Any) -> Any:
    return text(value.value if isinstance(value, Enum) else value)


def value(item: Any) -> Any:
    """Une valeur d'image clé : un nombre, un booléen, ou un couple ``(x, y)`` (liste de deux nombres) ; ``None`` reste ``None``."""
    if isinstance(item, (tuple, list)):
        return [number(part) for part in item]
    return number(item)


def attr(owner: Any, name: str, default: Any = None) -> Any:
    """Le champ ``name`` d'un objet **ou** d'un dictionnaire (un réglage écrit en dictionnaire par un ancien fichier)."""
    if isinstance(owner, dict):
        return owner.get(name, default)
    return getattr(owner, name, default)
