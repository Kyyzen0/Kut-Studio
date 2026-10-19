"""Lecture stricte des champs d'un fichier `.kut3d` : types exacts, clés connues, bornes (entrée non fiable)."""

from __future__ import annotations

import math
import re
from typing import Any

from kut3d.errors import SceneFileError, short

MAX_SCENE_BYTES = 16 << 20  # taille du fichier `.kut3d`
MAX_MODELS = 256  # entrées de `models[]`
MAX_ID_CHARS = 64
MAX_NAME_CHARS = 200
_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")


def check_keys(
    obj: dict[str, Any],
    where: str,
    required: tuple[str, ...],
    optional: tuple[str, ...] = (),
) -> None:
    """Refuse toute clé inconnue et toute clé obligatoire absente."""
    allowed = set(required) | set(optional)
    unknown = sorted(str(k) for k in obj if k not in allowed)
    if unknown:
        listed = ", ".join(short(k, 30) for k in unknown[:5])
        raise SceneFileError(f"{where} : clé(s) inconnue(s) : {listed}.")
    missing = [k for k in required if k not in obj]
    if missing:
        raise SceneFileError(
            f"{where} : clé(s) obligatoire(s) absente(s) : {', '.join(missing)}."
        )


def as_object(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SceneFileError(f"{where} : un objet JSON est attendu.")
    return value


def as_list(value: Any, where: str, maximum: int) -> list[Any]:
    if not isinstance(value, list):
        raise SceneFileError(f"{where} : une liste est attendue.")
    if len(value) > maximum:
        raise SceneFileError(
            f"{where} : trop d'éléments ({len(value)}, maximum {maximum})."
        )
    return value


def number(value: Any, where: str) -> float:
    """Un nombre fini (un `bool` n'en est pas un)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SceneFileError(
            f"{where} : un nombre est attendu (reçu {short(value, 30)})."
        )
    try:
        out = float(value)
    except OverflowError:
        raise SceneFileError(f"{where} : nombre démesuré.") from None
    if not math.isfinite(out):
        raise SceneFileError(f"{where} : nombre non fini (NaN ou infini).")
    return out


def optional_number(value: Any, where: str) -> float | None:
    return None if value is None else number(value, where)


def boolean(value: Any, where: str) -> bool:
    if not isinstance(value, bool):
        raise SceneFileError(
            f"{where} : un booléen est attendu (reçu {short(value, 30)})."
        )
    return value


def text(value: Any, where: str, maximum: int = MAX_NAME_CHARS) -> str:
    if not isinstance(value, str):
        raise SceneFileError(
            f"{where} : un texte est attendu (reçu {short(value, 30)})."
        )
    if len(value) > maximum:
        raise SceneFileError(
            f"{where} : texte trop long ({len(value)} caractères, maximum {maximum})."
        )
    return value


def floats(value: Any, count: int, where: str) -> list[float]:
    """Exactement `count` nombres finis."""
    if not isinstance(value, list) or len(value) != count:
        got = len(value) if isinstance(value, list) else type(value).__name__
        raise SceneFileError(f"{where} : {count} nombres attendus (reçu {got}).")
    return [number(v, f"{where}[{i}]") for i, v in enumerate(value)]


def model_id(value: Any, where: str) -> str:
    """Identifiant de modèle : 1 à 64 caractères parmi lettres ASCII, chiffres, `_` et `-`."""
    s = text(value, where, MAX_ID_CHARS)
    if _ID_RE.fullmatch(s) is None:
        raise SceneFileError(
            f"{where} : identifiant invalide {short(s, 40)} (lettres ASCII, chiffres, « _ » et « - », 1 à {MAX_ID_CHARS})."
        )
    return s
