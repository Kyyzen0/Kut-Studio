"""Modes de fusion : liste unique pour l'aperçu, l'export et l'interface.

Chaque mode a **une** formule, appliquée par deux moteurs :

- le rastériseur motion graphics (Qt, :mod:`core.mograph_raster`) pour la
  fusion à l'intérieur d'un groupe isolé ;
- FFmpeg (:mod:`core.export_engine`) pour la fusion d'un calque avec tout
  ce qui est en dessous (vidéo comprise).

Les deux suivent les formules séparables du W3C (*Compositing and
Blending*), en RVB, puis mélangent le résultat avec le fond selon l'alpha
du calque : ``sortie = fond·(1−α) + f(fond, calque)·α``. Les tests
``tests/test_mograph_blend.py`` vérifient l'égalité pixel par pixel.

Ajouter un mode : l'ajouter ici **seulement** si Qt et FFmpeg possèdent la
même formule (voir :data:`_FFMPEG_MODES` et :func:`qt_composition_mode`).
"""

from __future__ import annotations

from enum import Enum


class BlendMode(str, Enum):
    """Mode de fusion d'un calque. La valeur est sérialisée dans le ``.kut``."""

    NORMAL = "normal"
    MULTIPLY = "multiply"
    SCREEN = "screen"
    OVERLAY = "overlay"
    DARKEN = "darken"
    LIGHTEN = "lighten"
    ADD = "addition"  # valeur historique (tâche 33)
    DIFFERENCE = "difference"


BLEND_MODES: tuple[BlendMode, ...] = tuple(BlendMode)
"""Ordre d'affichage dans l'interface."""

_ALIASES = {"add": BlendMode.ADD, "plus": BlendMode.ADD, "linear_dodge": BlendMode.ADD}

# Nom ``all_mode`` du filtre ``blend``. FFmpeg appelle ``A`` l'entrée du haut
# (premier flux) et ``B`` celle du bas. Son ``overlay`` teste ``A`` alors que
# l'overlay du W3C (et de Qt) teste le **fond** : c'est le ``hardlight`` de
# FFmpeg qui teste ``B``. Les autres modes sont symétriques.
_FFMPEG_MODES: dict[BlendMode, str] = {
    BlendMode.NORMAL: "normal",
    BlendMode.MULTIPLY: "multiply",
    BlendMode.SCREEN: "screen",
    BlendMode.OVERLAY: "hardlight",
    BlendMode.DARKEN: "darken",
    BlendMode.LIGHTEN: "lighten",
    BlendMode.ADD: "addition",
    BlendMode.DIFFERENCE: "difference",
}


def coerce_blend_mode(value: object) -> BlendMode:
    """Mode valide ; une valeur inconnue retombe sur ``normal`` (jamais d'erreur)."""
    if isinstance(value, BlendMode):
        return value
    text = str(getattr(value, "value", value) or "").strip().lower()
    if text in _ALIASES:
        return _ALIASES[text]
    try:
        return BlendMode(text)
    except ValueError:
        return BlendMode.NORMAL


def ffmpeg_blend_mode(mode: object) -> str:
    """Valeur ``all_mode`` du filtre ``blend`` (calque en premier flux)."""
    return _FFMPEG_MODES[coerce_blend_mode(mode)]


def qt_composition_mode(mode: object):
    """``QPainter.CompositionMode`` équivalent (import Qt paresseux)."""
    from PySide6.QtGui import QPainter

    return {
        BlendMode.NORMAL: QPainter.CompositionMode_SourceOver,
        BlendMode.MULTIPLY: QPainter.CompositionMode_Multiply,
        BlendMode.SCREEN: QPainter.CompositionMode_Screen,
        BlendMode.OVERLAY: QPainter.CompositionMode_Overlay,
        BlendMode.DARKEN: QPainter.CompositionMode_Darken,
        BlendMode.LIGHTEN: QPainter.CompositionMode_Lighten,
        BlendMode.ADD: QPainter.CompositionMode_Plus,
        BlendMode.DIFFERENCE: QPainter.CompositionMode_Difference,
    }[coerce_blend_mode(mode)]


def blend_label_key(mode: object) -> str:
    """Clé i18n du nom d'un mode."""
    return f"blend.{coerce_blend_mode(mode).name.lower()}"


__all__ = [
    "BLEND_MODES",
    "BlendMode",
    "blend_label_key",
    "coerce_blend_mode",
    "ffmpeg_blend_mode",
    "qt_composition_mode",
]
