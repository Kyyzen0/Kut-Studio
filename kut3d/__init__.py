"""Kut-3D : modèle de scène 3D immuable et importeurs (phase 1 d'ADR-0009).

Ce paquet n'importe ni Qt, ni `ui`, ni `core` : il s'utilise sans fenêtre.
"""

from __future__ import annotations

from kut3d.errors import ModelImportError, UnsupportedFormatError
from kut3d.importers import load_model, supported_extensions
from kut3d.model import Model

__all__ = [
    "Model",
    "ModelImportError",
    "UnsupportedFormatError",
    "load_model",
    "supported_extensions",
]
