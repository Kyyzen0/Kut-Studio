"""Déformation par os (4 influences), formes de visage (morph targets) et leur ordre (glTF : formes puis os)."""

from __future__ import annotations

import numpy as np
import pytest

from kut3d import Instance, RenderError, Scene, render_frame
from kut3d.mathutil import scale_matrix, translation_matrix
from kut3d.model import AnimationClip, Mesh, Model, MorphTarget, Node, Skeleton
from kut3d.render.assemble import assemble, compute_tangents
from tests.kut3d_scenes import (
    WHITE_AMBIENT,
    channel,
    front_camera,
    morph_model,
    rotation_z_quat,
    skinned_model,
)


def world_positions(
    model: Model, t: float, clip: str | None = None, transform: np.ndarray | None = None
) -> np.ndarray:
    inst = Instance(model, np.eye(4) if transform is None else transform, clip=clip)
    return assemble(Scene([inst], [], front_camera()), t).pos


@pytest.mark.parametrize(
    "t, expected",
    [
        # os 1 tourné de 90° autour de Z autour du point (1, 0, 0) ; E est mélangé à 50 % / 50 % entre les os
        (0.0, [[0, 0, 0], [0, 1, 0], [1, 0, 0], [1, 1, 0], [1, 1, 0]]),
        (
            0.5,
            [
                [0, 0, 0],
                [0, 1, 0],
                [1, 0, 0],
                [1 - 0.70710678, 0.70710678, 0],
                [0.5 + 0.5 * (1 - 0.70710678), 0.5 + 0.5 * 0.70710678, 0],
            ],
        ),
        (1.0, [[0, 0, 0], [0, 1, 0], [1, 0, 0], [0, 0, 0], [0.5, 0.5, 0]]),
        (
            9.0,
            [[0, 0, 0], [0, 1, 0], [1, 0, 0], [0, 0, 0], [0.5, 0.5, 0]],
        ),  # au-delà de la clip : dernière pose
    ],
)
def test_two_bone_quad_positions_match_hand_computation(
    t: float, expected: list
) -> None:
    pos = world_positions(skinned_model(90.0), t, clip="bend")
    assert np.allclose(
        pos, expected, atol=1e-6
    )  # le décalage (5, 0, 0) du nœud du maillage est ignoré (glTF)


def test_skinned_mesh_follows_instance_and_model_root_transforms() -> None:
    model = skinned_model(90.0)
    from dataclasses import replace

    moved = replace(model, root_transform=scale_matrix(2.0))
    pos = world_positions(
        moved, 1.0, clip="bend", transform=translation_matrix([0, 0, 3.0])
    )
    expected = np.array(
        [[0, 0, 0], [0, 1, 0], [1, 0, 0], [0, 0, 0], [0.5, 0.5, 0]]
    ) * 2.0 + [0, 0, 3.0]
    assert np.allclose(pos, expected, atol=1e-6)


def test_rest_pose_of_a_skinned_mesh_is_the_bind_pose() -> None:
    assert np.allclose(
        world_positions(skinned_model(), 0.0),
        [[0, 0, 0], [0, 1, 0], [1, 0, 0], [1, 1, 0], [1, 1, 0]],
        atol=1e-6,
    )


def test_zero_weight_vertices_stay_put_and_unknown_joint_is_refused() -> None:
    model = skinned_model()
    mesh = model.meshes[0]
    zero = Mesh(
        mesh.positions,
        mesh.indices,
        joints=mesh.joints,
        weights=np.zeros_like(mesh.weights),
    )
    from dataclasses import replace

    pos = world_positions(replace(model, meshes=(zero,)), 1.0, clip="bend")
    assert np.allclose(pos, mesh.positions, atol=1e-6)
    bad = Mesh(
        mesh.positions, mesh.indices, joints=np.full((5, 4), 7), weights=mesh.weights
    )
    with pytest.raises(RenderError, match="os inexistant"):
        world_positions(replace(model, meshes=(bad,)), 0.0)


@pytest.mark.parametrize("t, weight", [(0.0, 0.0), (1.0, 0.5), (2.0, 1.0)])
def test_morph_target_displaces_vertices_by_weight(t: float, weight: float) -> None:
    pos = world_positions(morph_model((0, 0, 1)), t, clip="m")
    assert np.allclose(pos[:, 2], weight, atol=1e-6)
    assert np.allclose(pos[:, :2], [[0, 0], [1, 0], [0, 1]], atol=1e-6)


def test_mesh_default_weights_and_node_weights_apply_without_a_clip() -> None:
    model = morph_model((0, 0, 1))
    from dataclasses import replace

    mesh = replace(model.meshes[0], morph_weights=(0.5,))
    assert np.allclose(world_positions(replace(model, meshes=(mesh,)), 0.0)[:, 2], 0.5)
    node = replace(
        model.nodes[0], weights=(0.25,)
    )  # un nœud remplace le poids du maillage
    assert np.allclose(
        world_positions(replace(model, meshes=(mesh,), nodes=(node,)), 0.0)[:, 2], 0.25
    )


def test_morph_is_applied_before_skinning_like_gltf() -> None:
    mesh = Mesh(
        [[0, 0, 0], [1, 0, 0], [0, 1, 0]],
        [[0, 1, 2]],
        joints=[[0, 0, 0, 0]] * 3,
        weights=[[1, 0, 0, 0]] * 3,
        morph_targets=(MorphTarget("dx", [[1, 0, 0]] * 3),),
        morph_weights=(1.0,),
    )
    skel = Skeleton(
        ("j",), [-1], [np.eye(4)], [[0, 0, 0]], [[0, 0, 0, 1]], [[1, 1, 1]], [1]
    )
    clip = AnimationClip(
        "turn",
        1.0,
        (channel("rotation", [0, 1], [[0, 0, 0, 1], rotation_z_quat(90.0)], node=1),),
    )
    model = Model(
        nodes=(Node(mesh_index=0, skin_index=0), Node(name="j")),
        root_nodes=(0, 1),
        meshes=(mesh,),
        skeletons=(skel,),
        animations=(clip,),
    )
    pos = world_positions(model, 1.0, clip="turn")
    # forme d'abord : (1,0,0), (2,0,0), (1,1,0) ; puis os (rotation de 90°) : (0,1,0), (0,2,0), (-1,1,0).
    # (os d'abord, puis forme, donnerait (1,0,0), (1,1,0), (0,1,0)... plus l'écart (1,0,0) : un autre résultat)
    assert np.allclose(pos, [[0, 1, 0], [0, 2, 0], [-1, 1, 0]], atol=1e-6)


def test_deformed_model_renders_and_changes_with_time() -> None:
    model = skinned_model(90.0)
    sc = Scene(
        [Instance(model, translation_matrix([-0.5, -0.5, 0.0]), clip="bend")],
        [WHITE_AMBIENT],
        front_camera(z=3.0, fov_y_deg=60.0),
    )
    a = render_frame(sc, 0.0, (48, 48))
    b = render_frame(sc, 1.0, (48, 48))
    assert (
        (a.alpha > 0).sum() > (b.alpha > 0).sum() > 0
    )  # l'os replié réduit la surface visible
    assert render_frame(sc, 1.0, (48, 48)).color.tobytes() == b.color.tobytes()


def test_tangents_follow_uv_direction_with_v_down() -> None:
    pos = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], float)
    nrm = np.tile([0.0, 0.0, 1.0], (4, 1))
    uv = np.array(
        [[0, 1], [1, 1], [1, 0], [0, 0]], float
    )  # u vers +X, v vers le bas (haut de l'image = +Y)
    tri = np.array([[0, 1, 2], [0, 2, 3]])
    tan, sign = compute_tangents(pos, nrm, uv, tri)
    assert np.allclose(tan, [[1, 0, 0]] * 4, atol=1e-9)
    assert (
        sign == 1.0
    ).all()  # bitangente = N x T = +Y = « vers le haut de l'image » : main droite
    flipped = uv.copy()
    flipped[:, 0] = (
        1 - flipped[:, 0]
    )  # u inversé : la texture est en miroir -> signe négatif
    tan2, sign2 = compute_tangents(pos, nrm, flipped, tri)
    assert np.allclose(tan2, [[-1, 0, 0]] * 4, atol=1e-9) and (sign2 == -1.0).all()
