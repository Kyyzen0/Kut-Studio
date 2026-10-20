"""Scènes de test des formes de base : les six formes côte à côte, avec ou sans damier."""

from __future__ import annotations

from typing import Any

import numpy as np

from kut3d import (
    AmbientLight,
    Camera,
    Cone,
    Cube,
    Cylinder,
    DirectionalLight,
    Material,
    Plane,
    Scene,
    Sphere,
    Torus,
    primitive_instance,
)
from kut3d.primitives import PrimitiveSpec
from tests.kut3d_scenes import checker_rgba, texture_ref

SIX: tuple[PrimitiveSpec, ...] = (
    Cube(1.0, 1.0, 1.0),
    Sphere(0.6, 32, 16),
    Plane(1.4, 1.4, 4, 4),
    Cylinder(0.45, 1.2, 24),
    Cone(0.55, 1.2, 24),
    Torus(0.5, 0.2, 32, 16),
)
LIGHTS = [
    AmbientLight((1, 1, 1), 0.3),
    DirectionalLight((-0.4, -0.6, -0.7), (1, 1, 1), 2.5),
]


def checker_material(**kwargs: Any) -> Material:
    return Material(
        base_color=(1, 1, 1, 1),
        base_color_texture=texture_ref(checker_rgba(64, 8)),
        roughness=0.6,
        **kwargs,
    )


def six_forms(*, checker: bool = False, rotation: Any = (25.0, 30.0, 0.0)) -> Scene:
    """Les six formes sur deux rangées de trois (x = -1.8, 0, 1.8 ; y = 0.9, -0.9), fond opaque sombre."""
    instances = []
    for i, spec in enumerate(SIX):
        material = (
            checker_material(double_sided=isinstance(spec, Plane))
            if checker
            else Material(
                base_color=(0.9, 0.3 + 0.1 * i, 0.2, 1.0),
                roughness=0.5,
                double_sided=isinstance(spec, Plane),
            )
        )
        instances.append(
            primitive_instance(
                spec,
                material=material,
                position=((i % 3 - 1) * 1.8, (0.9 if i < 3 else -0.9), 0.0),
                rotation_deg=rotation,
                name=spec.kind,
            )
        )
    camera = Camera((0, 0, 7.5), (0, 0, 0), fov_y_deg=35)
    return Scene(instances, LIGHTS, camera, background=(0.1, 0.1, 0.14, 1.0))


def front_camera(distance: float = 4.0) -> Camera:
    return Camera((0, 0, distance), (0, 0, 0), fov_y_deg=40)


def single(
    spec: PrimitiveSpec,
    material: Material | None = None,
    *,
    camera: Camera | None = None,
    **pose: Any,
) -> Scene:
    inst = primitive_instance(spec, material=material, **pose)
    return Scene([inst], LIGHTS, camera or front_camera(), background=(0, 0, 0, 1))


def checker_cells(size: int = 8) -> np.ndarray:
    return checker_rgba(64, size)
