"""Normalisation d'un modèle importé : axe haut, unités, taille, centrage.

Elle ne touche JAMAIS aux sommets : elle remplit `Model.root_transform`, ce qui garde squelette, poids de peau et
animations valides (tout vit dans l'espace d'origine du fichier ; seule la racine change).
Ordre : rotation d'axe haut, puis `unit_scale`, puis mise à la taille `target_size`, puis centrage.
"""

from __future__ import annotations

import math
from dataclasses import replace

import numpy as np

from kut3d.mathutil import Array, rotation_x, scale_matrix, translation_matrix
from kut3d.model import Model

UP_AXES = ("y", "z")


def normalization_matrix(
    model: Model,
    *,
    up_axis: str = "y",
    unit_scale: float = 1.0,
    target_size: float | None = None,
    center: bool = True,
) -> tuple[Array, list[str]]:
    """Matrice 4x4 de normalisation (sans l'appliquer) et avertissements éventuels."""
    if up_axis not in UP_AXES:
        raise ValueError(f"up_axis doit valoir 'y' ou 'z' (reçu {up_axis!r}).")
    if not math.isfinite(unit_scale) or unit_scale <= 0:
        raise ValueError(
            f"unit_scale doit être un nombre fini strictement positif (reçu {unit_scale!r})."
        )
    if target_size is not None and (not math.isfinite(target_size) or target_size <= 0):
        raise ValueError(
            f"target_size doit être un nombre fini strictement positif (reçu {target_size!r})."
        )
    warnings: list[str] = []
    m = scale_matrix(unit_scale)
    if up_axis == "z":
        m = m @ rotation_x(-90.0)
    lo, hi = replace(model, root_transform=m).bounds()
    extent = float((hi - lo).max())
    k = 1.0
    if target_size is not None:
        if extent > 1e-12:
            k = target_size / extent
        else:
            warnings.append("target_size ignorée : modèle sans étendue")
    m = scale_matrix(k) @ m
    if center:
        m = translation_matrix(-k * (lo + hi) / 2.0) @ m
    return m, warnings


def normalize_model(
    model: Model,
    *,
    up_axis: str = "y",
    unit_scale: float = 1.0,
    target_size: float | None = None,
    center: bool = True,
) -> Model:
    """Retourne le modèle avec `root_transform` = normalisation (par-dessus celle qu'il portait déjà)."""
    m, warnings = normalization_matrix(
        model,
        up_axis=up_axis,
        unit_scale=unit_scale,
        target_size=target_size,
        center=center,
    )
    out = replace(model, root_transform=m @ np.asarray(model.root_transform))
    return out.with_warnings(*warnings) if warnings else out
