"""Constantes partagées par l'inspecteur de propriétés."""

from __future__ import annotations




# Tolérance pour considérer une image-clé comme « sous » le losange.
_KEYFRAME_MATCH_TOLERANCE = 1e-3

_PROPERTY_RANGES = {
    "position_x": (-4.0, 4.0, 0.01),
    "position_y": (-4.0, 4.0, 0.01),
    "scale": (0.05, 10.0, 0.01),
    "rotation": (-3600.0, 3600.0, 0.1),
    "opacity": (0.0, 1.0, 0.01),
}
