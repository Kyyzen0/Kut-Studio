"""Le fichier ``.kut3d`` du parcours « motion design + 3D » : des formes de base et un texte 3D extrudé (format 3).

Le document est écrit en JSON déterministe (clés triées, nombres arrondis) à partir de paramètres : aucune police, aucun
modèle à télécharger (``Anton`` est livrée avec l'application). Voir ``docs/kut3d-scene.md`` pour le format.
"""

from __future__ import annotations

import math
from typing import Any

IDENTITY = (1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1)


def transform(position: tuple[float, float, float] = (0.0, 0.0, 0.0), yaw: float = 0.0, pitch: float = 0.0,
              scale: float = 1.0) -> list[float]:
    """Matrice 4 x 4 par lignes (translation en 4e colonne) : rotation autour de Y (``yaw``) puis de X (``pitch``), en degrés."""
    cy, sy = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
    cx, sx = math.cos(math.radians(pitch)), math.sin(math.radians(pitch))
    # R = Rx(pitch) * Ry(yaw)
    rows = [
        [cy, 0.0, sy],
        [sx * sy, cx, -sx * cy],
        [-cx * sy, sx, cx * cy],
    ]
    matrix: list[float] = []
    for index, row in enumerate(rows):
        matrix += [round(value * scale, 6) for value in row] + [round(position[index], 6)]
    return matrix + [0.0, 0.0, 0.0, 1.0]


def scene_document(params: dict[str, Any]) -> dict[str, Any]:
    """Scène d'un titre : un texte 3D entouré d'un cube, d'une sphère, d'un tore, d'un cône et d'un cylindre.

    ``params`` : ``title`` (texte 3D, 1 à 40 caractères), ``segments`` (finesse des formes, 8 à 64).
    """
    title = str(params.get("title", "KUT"))
    segments = int(params.get("segments", 32))
    return {
        "format": "kut3d",
        "version": 3,
        "scene": {
            "camera": {"position": [0.0, 1.4, 8.0], "target": [0.0, 0.3, 0.0], "up": [0.0, 1.0, 0.0], "fov_y_deg": 38.0,
                       "near": 0.1, "far": 60.0},
            "lights": [
                {"type": "ambient", "color": [1.0, 1.0, 1.0], "intensity": 0.35},
                {"type": "directional", "direction": [-0.4, -0.7, -0.6], "intensity": 2.6},
                {"type": "point", "position": [3.0, 3.0, 4.0], "intensity": 18.0, "range": None},
            ],
            "background": None,
            "models": [
                {"id": "titre", "primitive": {"kind": "text", "text": title, "font": "Anton", "size": 1.6, "depth": 0.5,
                                              "bevel": 0.0, "align": "center"},
                 "material": {"base_color": [0.95, 0.65, 0.12, 1.0], "metallic": 0.8, "roughness": 0.3}},
                {"id": "cube", "primitive": {"kind": "cube", "width": 1.0, "height": 1.0, "depth": 1.0},
                 "material": {"base_color": [0.15, 0.45, 0.95, 1.0], "metallic": 0.2, "roughness": 0.4}},
                {"id": "boule", "primitive": {"kind": "sphere", "radius": 0.6, "segments": segments, "rings": max(2, segments // 2)},
                 "material": {"base_color": [0.9, 0.2, 0.25, 1.0], "metallic": 0.1, "roughness": 0.35}},
                {"id": "tore", "primitive": {"kind": "torus", "major_radius": 0.6, "minor_radius": 0.2, "segments": segments,
                                             "tube_segments": max(3, segments // 2)},
                 "material": {"base_color": [0.2, 0.8, 0.55, 1.0], "metallic": 0.6, "roughness": 0.25}},
                {"id": "cone", "primitive": {"kind": "cone", "radius": 0.5, "height": 1.1, "segments": segments, "cap": True},
                 "material": {"base_color": [0.85, 0.85, 0.2, 1.0], "metallic": 0.0, "roughness": 0.6}},
                {"id": "colonne", "primitive": {"kind": "cylinder", "radius": 0.35, "height": 1.2, "segments": segments, "caps": True},
                 "material": {"base_color": [0.7, 0.3, 0.9, 1.0], "metallic": 0.3, "roughness": 0.5}},
            ],
            "instances": [
                {"model": "titre", "name": "titre", "transform": transform((0.0, 0.5, 0.0))},
                {"model": "cube", "name": "cube", "transform": transform((-3.2, -1.3, 0.5), yaw=30.0, pitch=20.0)},
                {"model": "boule", "name": "boule", "transform": transform((-1.1, -1.5, 1.0))},
                {"model": "tore", "name": "tore", "transform": transform((1.2, -1.2, 0.8), yaw=0.0, pitch=70.0)},
                {"model": "cone", "name": "cone", "transform": transform((2.8, -1.2, 0.2), yaw=0.0, pitch=0.0)},
                {"model": "colonne", "name": "colonne", "transform": transform((0.0, -1.3, -1.2))},
            ],
        },
    }
