"""Chemins d'un fichier `.kut3d` : relatifs, séparateur `/`, confinés sous le dossier du fichier.

Les règles sont celles de Windows, appliquées partout : un fichier écrit sur macOS doit s'ouvrir sur Windows.
"""

from __future__ import annotations

import re
from pathlib import Path

from kut3d.errors import SceneFileError, short

MAX_PATH_CHARS = 1024
_DRIVE_RE = re.compile(r"[A-Za-z]:")
_RESERVED = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
)
_BAD_CHARS = frozenset('<>:"|?*')


def check_relative_path(raw: str, where: str, *, allow_outside: bool = False) -> str:
    """Valide le chemin tel qu'il est écrit dans le fichier ; le retourne inchangé (la casse est préservée)."""
    if not raw:
        raise SceneFileError(f"{where} : chemin vide.")
    if len(raw) > MAX_PATH_CHARS:
        raise SceneFileError(
            f"{where} : chemin trop long ({len(raw)} caractères, maximum {MAX_PATH_CHARS})."
        )
    if "\x00" in raw:
        raise SceneFileError(f"{where} : le chemin contient un caractère nul.")
    if "\\" in raw:
        raise SceneFileError(
            f"{where} : le séparateur doit être « / » et non une barre oblique inverse ({short(raw, 40)})."
        )
    if raw.startswith("/"):
        raise SceneFileError(f"{where} : chemin absolu refusé ({short(raw, 40)}).")
    if _DRIVE_RE.match(raw):
        raise SceneFileError(f"{where} : lettre de lecteur refusée ({short(raw, 40)}).")
    for part in raw.split("/"):
        if part == "..":
            if not allow_outside:
                raise SceneFileError(
                    f"{where} : « .. » refusé, le chemin doit rester sous le dossier du fichier ({short(raw, 40)})."
                )
            continue
        _check_segment(part, where, raw)
    return raw


def _check_segment(part: str, where: str, raw: str) -> None:
    if part in ("", "."):
        raise SceneFileError(
            f"{where} : segment vide ou « . » refusé ({short(raw, 40)})."
        )
    if any(ch in _BAD_CHARS or ord(ch) < 32 for ch in part):
        raise SceneFileError(
            f"{where} : caractère interdit sous Windows dans {short(raw, 40)}."
        )
    if part[-1] in " .":
        raise SceneFileError(
            f"{where} : un nom ne peut pas finir par un espace ou un point ({short(raw, 40)})."
        )
    if part.split(".", 1)[0].upper() in _RESERVED:
        raise SceneFileError(f"{where} : nom réservé sous Windows ({short(part, 40)}).")


def resolve_model_path(
    base_dir: Path, raw: str, where: str, *, allow_outside: bool = False
) -> Path:
    """Le fichier désigné par `raw` (relatif au dossier du `.kut3d`) ; un lien symbolique qui s'échappe est refusé."""
    check_relative_path(raw, where, allow_outside=allow_outside)
    base = base_dir.resolve()
    candidate = (base / raw).resolve(strict=False)
    if not allow_outside and not candidate.is_relative_to(base):
        raise SceneFileError(
            f"{where} : le fichier sort du dossier du .kut3d ({short(raw, 40)})."
        )
    if not candidate.is_file():
        raise SceneFileError(f"{where} : modèle introuvable ({short(raw, 40)}).")
    return candidate
