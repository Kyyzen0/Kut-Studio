"""glTF : les poids de formes animés d'un nœud valent pour TOUTES les primitives du maillage."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from kut3d import Instance, Scene, load_model
from kut3d.animation import evaluate_pose
from kut3d.render.assemble import assemble
from tests.kut3d_samples import write_gltf
from tests.kut3d_scenes import front_camera


def _two_prims(doc: dict[str, Any]) -> None:
    prims = doc["meshes"][0]["primitives"]
    del prims[0]["material"]  # le PNG de l'échantillon n'est pas décodable
    prims.append(dict(prims[0]))
    prims[1]["targets"] = [dict(prims[0]["targets"][0])]
    doc["animations"][0]["samplers"][1]["interpolation"] = "LINEAR"


def test_animated_weights_reach_every_primitive(tmp_path: Path) -> None:
    model = load_model(write_gltf(tmp_path, mutate=_two_prims))
    assert len(model.meshes) == 2
    child = model.nodes[0].children[-1]
    assert model.nodes[child].name == "Quad#1"
    pose = evaluate_pose(model, "wave", 0.5)
    w0 = pose.morph_weights(0, model.meshes[0])
    w1 = pose.morph_weights(child, model.meshes[1])
    assert np.allclose(w0, [0.5]) and np.allclose(w1, [0.5])


def test_animated_weights_deform_every_primitive(tmp_path: Path) -> None:
    model = load_model(write_gltf(tmp_path, mutate=_two_prims))
    scene = Scene([Instance(model, clip="wave")], [], front_camera())
    pos = assemble(scene, 0.5).pos
    assert pos.shape[0] == 8
    assert np.allclose(pos[:4], pos[4:], atol=1e-6)  # même déformation sur les deux
    rest = assemble(scene, 0.0).pos
    assert not np.allclose(pos[4:, 2], rest[4:, 2])  # la seconde primitive bouge bien


def test_single_primitive_unchanged(tmp_path: Path) -> None:
    model = load_model(write_gltf(tmp_path))
    n_channels = len(model.animations[0].channels)
    assert n_channels == 2
