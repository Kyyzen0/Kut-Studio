"""Registre des importeurs et point d'entrée `load_model`."""

from __future__ import annotations

import struct
from collections.abc import Callable, Iterable
from pathlib import Path

from kut3d.errors import MAX_FILE_BYTES, ModelImportError, UnsupportedFormatError, short
from kut3d.importers import gltf, obj, ply, stl
from kut3d.model import Model
from kut3d.normalize import normalize_model

Importer = Callable[[Path], Model]

_REGISTRY: dict[str, Importer] = {}

# Erreurs que peut lever un fichier mal formé : toutes deviennent une `ModelImportError` propre.
_PARSE_ERRORS = (
    ValueError,
    KeyError,
    IndexError,
    TypeError,
    AttributeError,
    OverflowError,
    ArithmeticError,
    struct.error,
    RecursionError,
    MemoryError,
    OSError,
)


def register_importer(extensions: Iterable[str], importer: Importer) -> None:
    """Associe des extensions (avec ou sans point, insensibles à la casse) à un importeur `fn(path) -> Model`."""
    for ext in extensions:
        key = ext.lower() if ext.startswith(".") else "." + ext.lower()
        _REGISTRY[key] = importer


def supported_extensions() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))


def load_model(
    path: str | Path,
    *,
    up_axis: str = "y",
    unit_scale: float = 1.0,
    target_size: float | None = None,
    center: bool = True,
) -> Model:
    """Charge un modèle 3D (OBJ+MTL, PLY, STL, glTF/GLB) et le normalise (`Model.root_transform`).

    Lève `FileNotFoundError` (chemin absent), `UnsupportedFormatError` (extension inconnue) ou `ModelImportError`
    (fichier illisible, corrompu ou démesuré). Les problèmes bénins vont dans `Model.warnings`.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Fichier introuvable : {p.name}")
    if not p.is_file():
        raise ModelImportError(f"{p.name} : ce n'est pas un fichier.")
    ext = p.suffix.lower()
    importer = _REGISTRY.get(ext)
    if importer is None:
        raise UnsupportedFormatError(
            f"{p.name} : format {short(ext or '(aucune extension)')} non pris en charge ; "
            f"formats pris en charge : {', '.join(supported_extensions())}."
        )
    if p.stat().st_size > MAX_FILE_BYTES:
        raise ModelImportError(
            f"{p.name} : fichier trop volumineux (maximum {MAX_FILE_BYTES} octets)."
        )
    try:
        model = importer(p)
    except ModelImportError as exc:
        text = str(exc)
        if not text.startswith(p.name):
            raise ModelImportError(f"{p.name} : {text}") from exc
        raise
    except _PARSE_ERRORS as exc:
        raise ModelImportError(
            f"{p.name} : fichier illisible ou corrompu ({short(str(exc), 120)})."
        ) from exc
    return normalize_model(
        model,
        up_axis=up_axis,
        unit_scale=unit_scale,
        target_size=target_size,
        center=center,
    )


register_importer(obj.EXTENSIONS, obj.load)
register_importer(ply.EXTENSIONS, ply.load)
register_importer(stl.EXTENSIONS, stl.load)
register_importer(gltf.EXTENSIONS, gltf.load)
