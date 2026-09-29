"""Qualites de rendu d'apercu (tache 30) : Brouillon/Standard/Haute.

Distinct de core.preview_quality (diviseurs 1/2/4/8 du viewer temps
reel) : ici on decrit la fidelite d'un segment pre-rendu sur disque.
Les deux echelles coexistent : le viewer temps reel garde son
diviseur, le cache de segments utilise l'echelle ci-dessous.
"""

from __future__ import annotations


DRAFT = "draft"
STANDARD = "standard"
HIGH = "high"

VALID_RENDER_QUALITIES = (DRAFT, STANDARD, HIGH)

_NAMES = {DRAFT: "Brouillon", STANDARD: "Standard", HIGH: "Haute"}

_SCALES = {DRAFT: 0.25, STANDARD: 0.5, HIGH: 1.0}

_CRF = {DRAFT: 30, STANDARD: 23, HIGH: 18}

_PRESETS = {DRAFT: "ultrafast", STANDARD: "veryfast", HIGH: "medium"}


def coerce_render_quality(value):
    """Normalise une qualite ; repli sur standard si inconnue."""
    if isinstance(value, str) and value in VALID_RENDER_QUALITIES:
        return value
    return STANDARD


def preview_scale_factor(quality):
    """Facteur d'echelle (0.25 / 0.5 / 1.0)."""
    return _SCALES[coerce_render_quality(quality)]


def preview_crf(quality):
    """CRF x264 associe a la qualite."""
    return _CRF[coerce_render_quality(quality)]


def preview_preset(quality):
    """Preset x264 associe a la qualite."""
    return _PRESETS[coerce_render_quality(quality)]


def render_quality_label(quality, lang="fr"):
    """Libelle court : Brouillon / Standard / Haute."""
    code = coerce_render_quality(quality)
    if lang == "en":
        return {"draft": "Draft", "standard": "Standard", "high": "High"}[code]
    if lang == "es":
        return {"draft": "Borrador", "standard": "Estandar", "high": "Alta"}[code]
    return _NAMES[code]


__all__ = [
    "DRAFT",
    "HIGH",
    "STANDARD",
    "VALID_RENDER_QUALITIES",
    "coerce_render_quality",
    "preview_crf",
    "preview_preset",
    "preview_scale_factor",
    "render_quality_label",
]
