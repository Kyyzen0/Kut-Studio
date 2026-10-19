"""Erreurs et bornes de Kut-3D (un fichier 3D est une entrée non fiable)."""

from __future__ import annotations

# Bornes vérifiées AVANT toute allocation ou découpe (ADR-0009, §3).
MAX_FILE_BYTES = 1 << 30  # 1 Gio : taille d'un fichier de modèle
MAX_ELEMENTS = 50_000_000  # sommets, triangles ou éléments d'un accessor
MAX_LINE_BYTES = 1 << 20  # une ligne de texte (OBJ, MTL, PLY ascii)
MAX_HEADER_BYTES = 1 << 16  # en-tête PLY
MAX_MTL_BYTES = 1 << 24  # un fichier .mtl


class ModelImportError(ValueError):
    """Un modèle 3D n'a pas pu être lu (message : fichier + cause)."""


class UnsupportedFormatError(ModelImportError):
    """L'extension du fichier n'a pas d'importeur."""


def short(value: object, limit: int = 60) -> str:
    """Représentation bornée d'une valeur, pour qu'un message d'erreur ne recopie pas l'entrée entière."""
    text = repr(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


class RenderError(ValueError):
    """Une scène n'a pas pu être rendue (entrée invalide ou hors bornes ; message en français)."""


class ImageDecodeError(RenderError):
    """Une texture n'a pas pu être décodée (fichier corrompu, tronqué, démesuré ou format non pris en charge)."""
