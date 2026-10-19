"""Rendu image par image de Kut-3D (phase 2a d'ADR-0009) : la référence numpy, sans Qt ni fenêtre.

Le moteur « numpy » est déterministe et sert d'oracle aux moteurs GPU de la phase 2b, qui s'enregistrent avec
`register_backend` sans changer cette API.
"""

from __future__ import annotations

from kut3d.render import numpy_backend
from kut3d.render.api import render_frame
from kut3d.render.frame import (
    Backend,
    Frame,
    available_backends,
    register_backend,
)
from kut3d.render.sequence import render_sequence

register_backend("numpy", numpy_backend.render_numpy)

__all__ = [
    "Backend",
    "Frame",
    "available_backends",
    "register_backend",
    "render_frame",
    "render_sequence",
]
