"""Scène 3D : caméra (matrices, refus), lumières, instances, validation."""

from __future__ import annotations

import math

import numpy as np
import pytest

from kut3d import (
    AmbientLight,
    Camera,
    DirectionalLight,
    Instance,
    PointLight,
    Scene,
)
from kut3d.scene import MAX_INSTANCES, MAX_LIGHTS
from tests.kut3d_scenes import quad_model, skinned_model


def _project(
    cam: Camera, point: tuple[float, float, float], size: tuple[int, int]
) -> tuple[float, float, float]:
    w, h = size
    m = cam.projection_matrix(w / h) @ cam.view_matrix()
    c = m @ np.array([*point, 1.0])
    ndc = c[:3] / c[3]
    return (ndc[0] * 0.5 + 0.5) * w, (1 - (ndc[1] * 0.5 + 0.5)) * h, c[3]


def test_view_matrix_maps_target_on_minus_z_axis() -> None:
    cam = Camera((1.0, 2.0, 5.0), (1.0, 2.0, 0.0))
    v = cam.view_matrix() @ np.array([1.0, 2.0, 0.0, 1.0])
    assert np.allclose(v[:3], [0.0, 0.0, -5.0])  # devant la caméra : z de vue négatif
    assert np.allclose(cam.view_matrix() @ np.array([1.0, 2.0, 5.0, 1.0]), [0, 0, 0, 1])


def test_projection_of_known_points_gives_expected_pixels() -> None:
    cam = Camera((0, 0, 3), (0, 0, 0), fov_y_deg=90.0, near=0.1, far=50.0)
    # fov 90° : tan(45°) = 1 ; à la distance 3 le demi-champ vertical vaut 3 unités
    x, y, w = _project(cam, (0, 0, 0), (200, 100))
    assert (x, y) == pytest.approx((100.0, 50.0))
    assert w == pytest.approx(3.0)  # w de découpe = profondeur de vue
    x, y, _ = _project(cam, (3.0, 3.0, 0.0), (200, 100))  # haut du champ vertical
    assert y == pytest.approx(0.0)
    assert x == pytest.approx(
        100.0 + 50.0 * (3.0 / (3.0 * 2.0)) * 2
    )  # aspect 2 : le demi-champ horizontal vaut 6
    x, y, _ = _project(cam, (-6.0, -3.0, 0.0), (200, 100))
    assert (x, y) == pytest.approx((0.0, 100.0))


def test_near_far_map_to_ndc_minus_one_and_one() -> None:
    cam = Camera((0, 0, 0), (0, 0, -1), near=0.5, far=10.0)
    p = cam.projection_matrix(1.0)
    near = p @ np.array([0, 0, -0.5, 1.0])
    far = p @ np.array([0, 0, -10.0, 1.0])
    assert near[2] / near[3] == pytest.approx(-1.0)
    assert far[2] / far[3] == pytest.approx(1.0)


def test_camera_up_axis_is_respected() -> None:
    cam = Camera(
        (0, 0, 3), (0, 0, 0), up=(1, 0, 0)
    )  # caméra couchée : +X de la scène devient le haut de l'image
    _, y_up, _ = _project(cam, (1.0, 0.0, 0.0), (100, 100))
    assert y_up < 50.0


@pytest.mark.parametrize(
    "kw, message",
    [
        ({"fov_y_deg": 1.0}, "fov_y_deg"),
        ({"fov_y_deg": 170.0}, "fov_y_deg"),
        ({"fov_y_deg": float("nan")}, "fov_y_deg"),
        ({"near": 0.0}, "near"),
        ({"near": -1.0}, "near"),
        ({"far": 0.1}, "far"),
        ({"far": 0.05}, "far"),
        ({"target": (0, 0, 3)}, "confondues"),
        ({"up": (0, 0, -1)}, "colinéaire"),
        ({"up": (0, 0, 0)}, "nul"),
        ({"position": (0, 0, float("inf"))}, "non finies"),
        ({"position": (0, 0)}, "3 nombres"),
    ],
)
def test_camera_refuses_invalid_parameters(kw: dict, message: str) -> None:
    args = {"position": (0, 0, 3), "target": (0, 0, 0)}
    args.update(kw)
    with pytest.raises(ValueError, match=message):
        Camera(**args)


def test_camera_is_frozen_and_read_only() -> None:
    cam = Camera((0, 0, 3), (0, 0, 0))
    with pytest.raises(AttributeError):
        cam.near = 1.0  # type: ignore[misc]
    with pytest.raises(ValueError):
        cam.position[0] = 5.0
    with pytest.raises(ValueError, match="largeur"):
        cam.projection_matrix(0.0)


def test_lights_validate_and_normalize() -> None:
    d = DirectionalLight((0, -3, 0), (1, 0.5, 0.25), 2.0)
    assert np.allclose(d.direction, [0, -1, 0])
    with pytest.raises(ValueError, match="nulle"):
        DirectionalLight((0, 0, 0))
    with pytest.raises(ValueError, match="négatives"):
        AmbientLight((-1, 0, 0), 1.0)
    with pytest.raises(ValueError, match="intensité"):
        AmbientLight((1, 1, 1), float("nan"))
    with pytest.raises(ValueError, match="intensité"):
        PointLight((0, 0, 0), intensity=-1.0)
    with pytest.raises(ValueError, match="range"):
        PointLight((0, 0, 0), range=0.0)
    assert PointLight((0, 1, 0), range=None).range is None
    assert PointLight((0, 1, 0), range=5).range == 5.0


def test_instance_resolves_clips_by_name_and_index() -> None:
    model = skinned_model()
    assert Instance(model, clip="bend").resolve_clip() is model.animations[0]
    assert Instance(model, clip=0).resolve_clip() is model.animations[0]
    assert Instance(model).resolve_clip() is None
    with pytest.raises(ValueError, match="introuvable"):
        Instance(model, clip="marche")
    with pytest.raises(ValueError, match="hors limites"):
        Instance(model, clip=3)
    with pytest.raises(ValueError, match="nom ou un indice"):
        Instance(model, clip=True)
    with pytest.raises(ValueError, match="transform"):
        Instance(model, transform=np.zeros((4, 4)))
    with pytest.raises(ValueError, match="transform"):
        Instance(model, transform=np.eye(3))
    with pytest.raises(ValueError, match="time_offset"):
        Instance(model, time_offset=float("nan"))
    with pytest.raises(ValueError, match="Model"):
        Instance("pas un modèle")  # type: ignore[arg-type]


def test_instance_transform_is_read_only() -> None:
    inst = Instance(quad_model(), np.eye(4))
    with pytest.raises(ValueError):
        inst.transform[0, 0] = 2.0


def test_scene_validation_and_bounds() -> None:
    cam = Camera((0, 0, 3), (0, 0, 0))
    inst = Instance(quad_model())
    assert Scene([inst], [], cam).background is None  # fond transparent
    bg = Scene([inst], [], cam, background=(0.1, 0.2, 0.3, 1.0)).background
    assert np.allclose(bg, [0.1, 0.2, 0.3, 1.0])
    with pytest.raises(ValueError, match="fond"):
        Scene([inst], [], cam, background=(2.0, 0, 0, 1))
    with pytest.raises(ValueError, match="fond"):
        Scene([inst], [], cam, background=(0, 0, 0))
    with pytest.raises(ValueError, match="lumières"):
        Scene([inst], [AmbientLight()] * (MAX_LIGHTS + 1), cam)
    Scene([inst], [AmbientLight()] * MAX_LIGHTS, cam)
    with pytest.raises(ValueError, match="instances"):
        Scene([inst] * (MAX_INSTANCES + 1), [], cam)
    with pytest.raises(ValueError, match="Instance"):
        Scene(["x"], [], cam)
    with pytest.raises(ValueError, match="lumières"):
        Scene([inst], ["x"], cam)
    with pytest.raises(ValueError, match="Camera"):
        Scene([inst], [], "cam")  # type: ignore[arg-type]
    assert math.isclose(float(Scene([inst], [], cam).camera.fov_y_deg), 40.0)
