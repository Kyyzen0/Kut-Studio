"""Scènes et documents `.kut3d` de test, fabriqués par programme dans un dossier temporaire."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import numpy as np

from kut3d import (
    AmbientLight,
    Camera,
    DirectionalLight,
    Instance,
    PointLight,
    Scene,
    load_model,
)
from tests.kut3d_samples import write_cube_obj, write_gltf


def translation(x: float, y: float, z: float) -> np.ndarray:
    m = np.eye(4)
    m[:3, 3] = (x, y, z)
    return m


def rich_scene(folder: Path) -> Scene:
    """Quatre instances sur trois modèles (OBJ normalisé, glTF + .bin, GLB), trois types de lumières, fond RGBA."""
    write_cube_obj(folder, name="cube")
    sub = folder / "assets"
    sub.mkdir(exist_ok=True)
    write_gltf(sub, "bin", name="quad")
    write_gltf(sub, "glb", name="rig")
    cube = load_model(folder / "cube.obj", target_size=2.0)
    quad = load_model(sub / "quad.gltf", up_axis="z", center=False)
    rig = load_model(sub / "rig.glb", unit_scale=0.5)
    rot = np.array([[0, -1, 0, 0.1], [1, 0, 0, 0.2], [0, 0, 2, 0.3], [0, 0, 0, 1.0]])
    return Scene(
        instances=[
            Instance(cube, transform=translation(1.5, 0, 0), name="a"),
            Instance(cube, transform=rot, name="b", time_offset=-0.125),
            Instance(quad, clip="wave", loop=True, time_offset=0.25, name="vague"),
            Instance(rig, clip=1, transform=translation(0, 0.1, -3)),
        ],
        lights=[
            AmbientLight(color=(0.9, 0.8, 0.7), intensity=0.15),
            DirectionalLight(
                direction=(0.3, -1, -0.2), color=(1, 0.9, 0.8), intensity=2.5
            ),
            PointLight(
                position=(2, 3, 4), color=(0.2, 0.4, 1), intensity=7.0, range=9.5
            ),
            PointLight(position=(-2, 1, 1)),
        ],
        camera=Camera(
            position=(0.1, 2, 6),
            target=(0, 0.5, 0),
            up=(0, 1, 0),
            fov_y_deg=33.3,
            near=0.05,
            far=250.0,
        ),
        background=(0.1, 0.2, 0.3, 0.4),
    )


def assert_scenes_equal(a: Scene, b: Scene) -> None:
    ca, cb = a.camera, b.camera
    for name in ("position", "target", "up"):
        assert np.array_equal(getattr(ca, name), getattr(cb, name)), name
    assert (ca.fov_y_deg, ca.near, ca.far) == (cb.fov_y_deg, cb.near, cb.far)
    assert len(a.lights) == len(b.lights)
    for la, lb in zip(a.lights, b.lights):
        assert type(la) is type(lb)
        for key, va in vars(la).items():
            vb = getattr(lb, key)
            assert np.array_equal(va, vb) if isinstance(va, np.ndarray) else va == vb, (
                key
            )
    if a.background is None:
        assert b.background is None
    else:
        assert np.array_equal(a.background, b.background)
    assert len(a.instances) == len(b.instances)
    for ia, ib in zip(a.instances, b.instances):
        assert np.array_equal(ia.transform, ib.transform)
        assert (ia.clip, ia.time_offset, ia.loop, ia.name) == (
            ib.clip,
            ib.time_offset,
            ib.loop,
            ib.name,
        )
        assert np.array_equal(ia.model.root_transform, ib.model.root_transform)
        assert ia.model.source.resolve() == ib.model.source.resolve()
        assert ia.model.triangle_count == ib.model.triangle_count
        assert [c.name for c in ia.model.animations] == [
            c.name for c in ib.model.animations
        ]


def minimal_document() -> dict[str, Any]:
    """Document valide minimal (cube.obj à côté du fichier), complet pour qu'on puisse y glisser une clé en trop."""
    return {
        "format": "kut3d",
        "version": 1,
        "scene": {
            "camera": {
                "position": [0, 0, 5],
                "target": [0, 0, 0],
                "up": [0, 1, 0],
                "fov_y_deg": 40,
                "near": 0.1,
                "far": 100,
            },
            "lights": [
                {"type": "ambient", "color": [1, 1, 1], "intensity": 0.2},
                {
                    "type": "directional",
                    "direction": [0, -1, 0],
                    "color": [1, 1, 1],
                    "intensity": 1,
                },
                {
                    "type": "point",
                    "position": [1, 2, 3],
                    "color": [1, 1, 1],
                    "intensity": 1,
                    "range": None,
                },
            ],
            "background": [0, 0, 0, 1],
            "models": [
                {
                    "id": "cube",
                    "path": "cube.obj",
                    "normalize": {
                        "up_axis": "y",
                        "unit_scale": 1.0,
                        "target_size": None,
                        "center": True,
                    },
                }
            ],
            "instances": [
                {
                    "model": "cube",
                    "transform": list(np.eye(4).reshape(-1)),
                    "clip": None,
                    "time_offset": 0,
                    "loop": False,
                    "name": "x",
                }
            ],
        },
    }


def mutated(path_in_doc: tuple[Any, ...], value: Any) -> dict[str, Any]:
    """Copie du document minimal où l'élément `path_in_doc` vaut `value`."""
    doc = copy.deepcopy(minimal_document())
    node: Any = doc
    for key in path_in_doc[:-1]:
        node = node[key]
    node[path_in_doc[-1]] = value
    return doc


def write_doc(path: Path, doc: dict[str, Any]) -> Path:
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path
