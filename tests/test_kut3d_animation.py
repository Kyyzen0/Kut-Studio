"""Animation : échantillonnage des canaux, pose, matrices d'articulation, poids de formes."""

from __future__ import annotations

import math

import numpy as np
import pytest

from kut3d.animation import clip_time, evaluate_pose, sample_channel, slerp
from kut3d.mathutil import quat_to_matrix
from tests.kut3d_scenes import (
    animated_node_model,
    channel,
    morph_model,
    rotation_z_quat,
    skinned_model,
)

Q0 = (0.0, 0.0, 0.0, 1.0)
Q90 = rotation_z_quat(90.0)


def _rot(model, t, **kw):  # type: ignore[no-untyped-def]
    return evaluate_pose(model, "a", t, **kw).rotation[0]


@pytest.mark.parametrize("interp", ["linear", "step", "cubicspline"])
def test_rotation_at_start_middle_and_end(interp: str) -> None:
    if interp == "cubicspline":
        zero = [0, 0, 0, 0]
        values = [[zero, Q0, zero], [zero, Q90, zero]]
    else:
        values = [Q0, Q90]
    model = animated_node_model([channel("rotation", [0, 2], values, interp)])
    start, mid, end = (_rot(model, t) for t in (0.0, 1.0, 2.0))
    assert np.allclose(start, Q0, atol=1e-6)
    assert np.allclose(end, Q90, atol=1e-6)
    if interp == "linear":  # slerp : 45° exactement
        assert np.allclose(mid, rotation_z_quat(45.0), atol=1e-6)
    elif interp == "step":  # tient la valeur de l'image clé précédente
        assert np.allclose(mid, Q0, atol=1e-6)
        assert np.allclose(_rot(model, 1.999), Q0, atol=1e-6)
    else:  # Hermite à tangentes nulles : 0,5 v0 + 0,5 v1, puis renormalisé
        expect = 0.5 * np.array(Q0) + 0.5 * np.array(Q90)
        assert np.allclose(mid, expect / np.linalg.norm(expect), atol=1e-6)
    assert abs(np.linalg.norm(mid) - 1.0) < 1e-9


def test_cubicspline_uses_file_tangents_by_hand() -> None:
    # v0 = 0, tangente de sortie 4 ; v1 = 2, tangente d'entrée 2 ; dt = 2 ; s = 0,5
    # p = 0,5*0 + 0,125*2*4 + 0,5*2 - 0,125*2*2 = 1 + 1 - 0,5 = 1,5
    values = [[[0, 0, 0], [0, 0, 0], [4, 0, 0]], [[2, 0, 0], [2, 0, 0], [0, 0, 0]]]
    model = animated_node_model([channel("translation", [0, 2], values, "cubicspline")])
    pose = evaluate_pose(model, "a", 1.0)
    assert np.allclose(pose.translation[0], [1.5, 0, 0])
    assert np.allclose(evaluate_pose(model, "a", 0.0).translation[0], [0, 0, 0])
    assert np.allclose(evaluate_pose(model, "a", 2.0).translation[0], [2, 0, 0])


def test_linear_translation_and_scale_and_world_matrix() -> None:
    model = animated_node_model(
        [
            channel("translation", [0, 2], [[0, 0, 0], [4, 0, 0]]),
            channel("scale", [0, 2], [[1, 1, 1], [3, 3, 3]]),
        ]
    )
    pose = evaluate_pose(model, 0, 0.5)
    assert np.allclose(pose.translation[0], [1, 0, 0])
    assert np.allclose(pose.scale[0], [1.5, 1.5, 1.5])
    expected = np.diag([1.5, 1.5, 1.5, 1.0])
    expected[0, 3] = 1.0
    assert np.allclose(pose.world[0], expected)
    assert pose.reached[0]


def test_time_clamps_and_loops() -> None:
    model = animated_node_model(
        [channel("translation", [0, 2], [[0, 0, 0], [4, 0, 0]])]
    )
    assert np.allclose(evaluate_pose(model, "a", -5.0).translation[0], [0, 0, 0])
    assert np.allclose(evaluate_pose(model, "a", 99.0).translation[0], [4, 0, 0])
    assert np.allclose(
        evaluate_pose(model, "a", 3.0, loop=True).translation[0], [2, 0, 0]
    )
    assert np.allclose(
        evaluate_pose(model, "a", -0.5, loop=True).translation[0], [3, 0, 0]
    )
    clip = model.animations[0]
    assert clip_time(clip, 5.0) == 2.0 and clip_time(
        clip, 5.0, loop=True
    ) == pytest.approx(1.0)
    with pytest.raises(ValueError, match="fini"):
        clip_time(clip, float("nan"))
    with pytest.raises(ValueError, match="fini"):
        evaluate_pose(model, None, float("inf"))


def test_no_clip_gives_rest_pose_and_unknown_clip_is_refused() -> None:
    model = skinned_model()
    rest = evaluate_pose(model)
    assert np.allclose(rest.rotation[2], Q0)
    assert np.allclose(rest.world[2][:3, 3], [1, 0, 0])
    with pytest.raises(ValueError, match="introuvable"):
        evaluate_pose(model, "absente", 0.0)
    with pytest.raises(ValueError, match="hors limites"):
        evaluate_pose(model, 4, 0.0)


def test_slerp_takes_the_short_way_and_handles_close_quaternions() -> None:
    q = slerp(
        np.array(Q0), -np.array(Q90), 0.5
    )  # -q est la même rotation : chemin le plus court
    assert np.allclose(np.abs(q), np.abs(rotation_z_quat(45.0)), atol=1e-6)
    near = slerp(np.array(Q0), np.array(rotation_z_quat(0.001)), 0.5)
    assert abs(np.linalg.norm(near) - 1.0) < 1e-12


def test_duplicate_keyframe_times_do_not_divide_by_zero() -> None:
    ch = channel(
        "translation", [0, 1, 1, 2], [[0, 0, 0], [1, 0, 0], [5, 0, 0], [6, 0, 0]]
    )
    assert np.isfinite(sample_channel(ch, 1.0)).all()


def test_joint_matrices_of_a_two_bone_chain() -> None:
    model = skinned_model(90.0)
    pose = evaluate_pose(model, "bend", 1.0)
    joints = pose.joint_matrices(0)
    assert np.allclose(joints[0], np.eye(4))  # os 0 : monde x inverse bind = identité
    # os 1 : T(1,0,0) R90 T(-1,0,0) ; le point (1,1,0) va en (0,0,0), le point (1,0,0) reste en place
    p = joints[1] @ np.array([1, 1, 0, 1.0])
    q = joints[1] @ np.array([1, 0, 0, 1.0])
    assert np.allclose(p[:3], [0, 0, 0], atol=1e-6)
    assert np.allclose(q[:3], [1, 0, 0], atol=1e-6)
    assert np.allclose(joints[1][:3, :3], quat_to_matrix(Q90), atol=1e-6)
    rest = evaluate_pose(model, None).joint_matrices(0)
    assert np.allclose(
        rest, np.tile(np.eye(4), (2, 1, 1)), atol=1e-6
    )  # au repos : pose de liaison


@pytest.mark.parametrize(
    "t, expected", [(0.0, 0.0), (1.0, 0.5), (2.0, 1.0), (7.0, 1.0)]
)
def test_morph_weights_follow_the_clip(t: float, expected: float) -> None:
    model = morph_model()
    pose = evaluate_pose(model, "m", t)
    w = pose.morph_weights(0, model.meshes[0])
    assert w.shape == (1,) and w[0] == pytest.approx(expected)


def test_morph_weights_default_to_mesh_and_node() -> None:
    model = morph_model()
    mesh = model.meshes[0]
    assert evaluate_pose(model).morph_weights(0, mesh)[0] == 0.0
    assert math.isclose(
        float(evaluate_pose(model, "m", 1.0).morph_weights(0, mesh).sum()), 0.5
    )
