"""Briques partagées par les importeurs : lecture bornée, chemins de textures confinés."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path, PurePosixPath

from kut3d.errors import MAX_FILE_BYTES, ModelImportError, short
from kut3d.model import Material, TextureRef


def read_limited(path: Path, limit: int = MAX_FILE_BYTES) -> bytes:
    """Lit un fichier entier après avoir vérifié sa taille (avant toute allocation)."""
    size = path.stat().st_size
    if size > limit:
        raise ModelImportError(
            f"{path.name} : fichier trop volumineux ({size} octets, maximum {limit})."
        )
    return path.read_bytes()


def safe_resolve(path: Path) -> Path | None:
    """`path.resolve()`, ou None si le chemin est inutilisable (nom trop long, octet nul, boucle de liens : sous
    Python < 3.13 elle lève `RuntimeError`). Un chemin écrit dans un fichier de modèle ne doit jamais faire échouer
    l'import par une exception système."""
    try:
        return path.resolve()
    except (OSError, RuntimeError, ValueError):
        return None


def safe_is_file(path: Path) -> bool:
    """`path.is_file()` sans exception : False aussi pour un nom trop long (ENAMETOOLONG ; `is_file` ne l'avale pas
    avant Python 3.14), une permission refusée ou un octet nul."""
    try:
        return path.is_file()
    except (OSError, RuntimeError, ValueError):
        return False


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
        candidate = safe_resolve(base / pure)
        if (
            candidate is None
        ):  # nom trop long, octet nul, boucle de liens : ce n'est pas un fichier lisible
            return None, f"texture introuvable : {short(text)}"
        if candidate.is_relative_to(base):
            if safe_is_file(candidate):
                return candidate, None
        else:
            fallback = safe_resolve(base / pure.name)
            if fallback is not None and safe_is_file(fallback):
                return (
                    fallback,
                    f"texture {short(text)} hors du dossier du modèle : {pure.name} utilisé à la place",
                )
            return None, f"texture {short(text)} hors du dossier du modèle : ignorée"
    fallback = safe_resolve(base / pure.name)
    if fallback is not None and safe_is_file(fallback):
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


def finalize_dependencies(paths: Iterable[Path]) -> tuple[Path, ...]:
    """Chemins absolus résolus, dédoublonnés, triés de façon déterministe (ordre de `as_posix()`)."""
    return tuple(sorted({p.resolve() for p in paths}, key=lambda p: p.as_posix()))


def material_texture_paths(materials: Iterable[Material]) -> list[Path]:
    """Les fichiers image référencés par les matériaux (les textures embarquées n'ont pas de chemin)."""
    out: list[Path] = []
    for mat in materials:
        for ref in (
            mat.base_color_texture,
            mat.normal_texture,
            mat.metallic_roughness_texture,
            mat.emissive_texture,
            mat.occlusion_texture,
        ):
            if ref is not None and ref.path is not None:
                out.append(ref.path)
    return out
