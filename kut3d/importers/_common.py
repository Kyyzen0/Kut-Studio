"""Briques partagées par les importeurs : lecture bornée, chemins de textures confinés."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from kut3d.errors import MAX_FILE_BYTES, ModelImportError, short
from kut3d.model import TextureRef


def read_limited(path: Path, limit: int = MAX_FILE_BYTES) -> bytes:
    """Lit un fichier entier après avoir vérifié sa taille (avant toute allocation)."""
    size = path.stat().st_size
    if size > limit:
        raise ModelImportError(
            f"{path.name} : fichier trop volumineux ({size} octets, maximum {limit})."
        )
    return path.read_bytes()


def resolve_texture_path(base_dir: Path, raw: str) -> tuple[Path | None, str | None]:
    """Résout un chemin de texture écrit dans un fichier de modèle.

    Confinement (ADR-0009) : le chemin doit rester dans le dossier du modèle. Les chemins absolus (souvent
    ceux de la machine d'origine, écrits avec des `\\`) et les `..` qui sortent du dossier sont ramenés au
    nom du fichier cherché dans ce dossier. Retourne (chemin, avertissement) ; chemin None si introuvable.
    """
    text = raw.strip().strip('"').replace("\\", "/")
    if not text:
        return None, "chemin de texture vide"
    base = base_dir.resolve()
    pure = PurePosixPath(text)
    absolute = pure.is_absolute() or (len(text) > 1 and text[1] == ":")
    if not absolute:
        candidate = (base / pure).resolve()
        if candidate.is_relative_to(base):
            if candidate.is_file():
                return candidate, None
        else:
            fallback = (base / pure.name).resolve()
            if fallback.is_file():
                return (
                    fallback,
                    f"texture {short(text)} hors du dossier du modèle : {pure.name} utilisé à la place",
                )
            return None, f"texture {short(text)} hors du dossier du modèle : ignorée"
    fallback = (base / pure.name).resolve()
    if fallback.is_file():
        return fallback, None
    return None, f"texture introuvable : {short(text)}"


def texture_from_file(
    base_dir: Path, raw: str, warnings: list[str], owner: str
) -> TextureRef | None:
    """Fabrique un `TextureRef` pointant sur un fichier ; texture absente : avertissement, pas d'exception."""
    path, warn = resolve_texture_path(base_dir, raw)
    if warn:
        warnings.append(f"{owner} : {warn}")
    if path is None:
        return None
    return TextureRef(name=path.name, path=path)
