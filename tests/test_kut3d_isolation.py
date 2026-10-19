"""Propriété exclusive des tableaux : muter l'original après construction ne change jamais l'objet figé."""

from __future__ import annotations

import numpy as np

from kut3d.model import (
    AnimationChannel,
    Material,
    Mesh,
    Model,
    MorphTarget,
    Skeleton,
)
from kut3d.scene import Camera, DirectionalLight, Instance
from tests.kut3d_scenes import cube_model


def _same_after_mutation(source: np.ndarray, frozen: np.ndarray) -> None:
    before = frozen.copy()
    source[...] = 7
    assert np.array_equal(frozen, before)
    assert not frozen.flags.writeable
    assert not np.shares_memory(source, frozen)


def test_mesh_arrays_are_owned() -> None:
    pos = np.zeros((3, 3), np.float32)
    idx = np.array([[0, 1, 2]], np.uint32)
    nrm = np.ones((3, 3), np.float32)
    uvs = np.zeros((3, 2), np.float32)
    col = np.ones((3, 4), np.float32)
    joints = np.zeros((3, 4), np.uint16)
    weights = np.zeros((3, 4), np.float32)
    mesh = Mesh(pos, idx, nrm, uvs, col, joints=joints, weights=weights)
    for src, got in (
        (pos, mesh.positions),
        (idx, mesh.indices),
        (nrm, mesh.normals),
        (uvs, mesh.uvs),
        (col, mesh.colors),
        (joints, mesh.joints),
        (weights, mesh.weights),
    ):
        assert got is not None
        _same_after_mutation(src, got)


def test_morph_target_arrays_are_owned() -> None:
    d = np.zeros((3, 3), np.float32)
    n = np.ones((3, 3), np.float32)
    t = MorphTarget("m", d, n)
    assert t.normal_deltas is not None
    _same_after_mutation(d, t.position_deltas)
    _same_after_mutation(n, t.normal_deltas)


def test_material_arrays_are_owned() -> None:
    bc = np.ones(4, np.float32)
    em = np.zeros(3, np.float32)
    mat = Material(base_color=bc, emissive=em)
    _same_after_mutation(bc, mat.base_color)
    _same_after_mutation(em, mat.emissive)


def test_skeleton_arrays_are_owned() -> None:
    parents = np.array([-1], np.int64)
    ibm = np.eye(4, dtype=np.float32)[None].copy()
    t = np.zeros((1, 3), np.float32)
    r = np.array([[0, 0, 0, 1]], np.float32)
    s = np.ones((1, 3), np.float32)
    nodes = np.array([0], np.int64)
    sk = Skeleton(("j",), parents, ibm, t, r, s, nodes)
    for src, got in (
        (parents, sk.parents),
        (ibm, sk.inverse_bind_matrices),
        (t, sk.rest_translation),
        (r, sk.rest_rotation),
        (s, sk.rest_scale),
        (nodes, sk.node_indices),
    ):
        _same_after_mutation(src, got)


def test_animation_channel_arrays_are_owned() -> None:
    times = np.array([0.0, 1.0], np.float32)
    values = np.zeros((2, 3), np.float32)
    ch = AnimationChannel(0, "translation", times, values)
    _same_after_mutation(times, ch.times)
    _same_after_mutation(values, ch.values)


def test_model_root_transform_is_owned() -> None:
    root = np.eye(4, dtype=np.float64)
    model = Model(root_transform=root)
    _same_after_mutation(root, model.root_transform)


def test_scene_arrays_are_owned() -> None:
    pos = np.array([0.0, 0.0, 3.0])
    cam = Camera(position=pos, target=(0.0, 0.0, 0.0))
    pos[:] = 9
    assert cam.position[2] == 3.0
    d = np.array([0.0, -1.0, 0.0])
    light = DirectionalLight(d)
    d[:] = 5
    assert light.direction[1] == -1.0
    m = np.eye(4)
    inst = Instance(cube_model(), transform=m)
    m[:] = 3
    assert inst.transform[0, 0] == 1.0
