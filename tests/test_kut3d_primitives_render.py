"""Formes de base : rendu de référence (numpy). Silhouette, profondeur, sens des triangles, lumière, UV d'une texture."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pytest

from kut3d import (
    AmbientLight,
    Camera,
    Cone,
    Cube,
    Cylinder,
    DirectionalLight,
    Frame,
    Material,
    Plane,
    Scene,
    Sphere,
    Torus,
    primitive_instance,
    render_frame,
)
from kut3d.primitives import PrimitiveSpec
from tests.kut3d_primitive_scenes import front_camera, six_forms
from tests.kut3d_scenes import texture_ref

SIZE = 96
DISTANCE = 4.0
FOCAL = (SIZE / 2) / math.tan(math.radians(20.0))  # fov_y 40 degrés


def _render(scene: Scene, size: int = SIZE) -> Frame:
    return render_frame(scene, 0.0, size=(size, size), backend="numpy")


def _scene(
    spec: PrimitiveSpec,
    material: Material | None = None,
    lights: Any = None,
    camera: Camera | None = None,
    **pose: Any,
) -> Scene:
    inst = primitive_instance(
        spec,
        material=material or Material(base_color=(0.8, 0.5, 0.3, 1), roughness=0.7),
        **pose,
    )
    return Scene(
        [inst],
        [
            AmbientLight((1, 1, 1), 0.3),
            DirectionalLight((-0.3, -0.5, -1), (1, 1, 1), 2.5),
        ]
        if lights is None
        else lights,
        camera or front_camera(),
    )


def _mask(frame: Frame) -> np.ndarray:
    return np.asarray(frame.alpha) > 0.5


def _runs(row: np.ndarray) -> int:
    return int(
        np.count_nonzero(np.diff(np.concatenate([[0], row.astype(np.int8), [0]])) == 1)
    )


# --- silhouette ----------------------------------------------------------------------------------------------


def test_sphere_silhouette_has_the_analytic_area() -> None:
    frame = _render(_scene(Sphere(0.6, 32, 16)))
    radius_px = FOCAL * math.tan(math.asin(0.6 / DISTANCE))
    area = float(_mask(frame).sum())
    assert area == pytest.approx(math.pi * radius_px**2, rel=0.04)


@pytest.mark.parametrize(
    "spec",
    [Cube(1, 1, 1), Sphere(0.6, 32, 16), Cylinder(0.45, 1.2, 24), Cone(0.55, 1.2, 24)],
    ids=lambda s: s.kind,
)
def test_convex_shapes_have_no_hole_in_their_silhouette(spec: PrimitiveSpec) -> None:
    mask = _mask(_render(_scene(spec, rotation_deg=(25, 30, 0))))
    assert mask.sum() > 400
    for row in mask:
        assert _runs(row) <= 1  # une seule bande par rangée : aucun trou
    for column in mask.T:
        assert _runs(column) <= 1


def test_a_torus_facing_the_camera_has_a_hole_and_a_ring() -> None:
    frame = _render(_scene(Torus(0.5, 0.2, 32, 16), rotation_deg=(90, 0, 0)))
    mask = _mask(frame)
    assert (
        not mask[SIZE // 2, SIZE // 2] and _runs(mask[SIZE // 2]) == 2
    )  # trou central, deux traversées du tube
    ring_x = int(SIZE / 2 + FOCAL * 0.5 / 3.8)
    assert mask[SIZE // 2, ring_x]
    outer = FOCAL * 0.7 / DISTANCE  # bord extérieur approximatif
    assert mask.sum() > math.pi * (outer**2 - (FOCAL * 0.3 / DISTANCE) ** 2) * 0.6


def test_depth_of_the_nearest_surface_is_right_for_each_shape() -> None:
    centre = (SIZE // 2, SIZE // 2)
    cases = [
        (Cube(1, 1, 1), 3.5),
        (Sphere(0.6, 32, 16), 3.4),
        (Cylinder(0.45, 1.2, 24), 3.55),
        (Cone(0.55, 1.2, 24), 3.725),
    ]
    for spec, expected in cases:
        depth = float(_render(_scene(spec)).depth[centre])
        assert depth == pytest.approx(expected, rel=2e-3), spec.kind
    frame = _render(_scene(Torus(0.5, 0.2, 32, 16), rotation_deg=(90, 0, 0)))
    assert float(
        frame.depth[SIZE // 2, int(SIZE / 2 + FOCAL * 0.5 / 3.8)]
    ) == pytest.approx(3.8, rel=5e-3)


@pytest.mark.parametrize(
    "spec",
    [
        Cube(1, 1, 1),
        Sphere(0.6, 32, 16),
        Cylinder(0.45, 1.2, 24),
        Cone(0.55, 1.2, 24),
        Torus(0.5, 0.2, 32, 16),
    ],
    ids=lambda s: s.kind,
)
def test_a_closed_shape_renders_the_same_one_sided_and_two_sided(
    spec: PrimitiveSpec,
) -> None:
    """Le sens des triangles est bon : un matériau à une face (faces arrière éliminées) donne l'image d'un matériau à deux faces."""
    pose = {"rotation_deg": (25, 30, 0)}
    one = _render(
        _scene(
            spec, Material(base_color=(0.8, 0.5, 0.3, 1), double_sided=False), **pose
        )
    )
    two = _render(
        _scene(spec, Material(base_color=(0.8, 0.5, 0.3, 1), double_sided=True), **pose)
    )
    assert np.array_equal(one.color, two.color) and np.array_equal(one.depth, two.depth)
    assert _mask(one).sum() > 400


def test_open_shapes_show_only_their_outer_side_when_single_sided() -> None:
    above = Camera((0, 3, 1), (0, 0, 0))
    below = Camera((0, -3, 1), (0, 0, 0))
    lit = [AmbientLight((1, 1, 1), 1.0)]
    plane = Plane(2, 2, 2, 2)
    assert _mask(_render(_scene(plane, lights=lit, camera=above)))[
        SIZE // 2, SIZE // 2
    ]  # normale +Y : visible d'en haut
    assert not _mask(
        _render(_scene(plane, lights=lit, camera=below))
    ).any()  # invisible d'en bas (une face)
    two = Material(double_sided=True)
    assert _mask(_render(_scene(plane, two, lights=lit, camera=below))).any()
    tube = Cylinder(
        0.5, 1.0, 16, caps=False
    )  # on regarde par le dessus : l'intérieur n'est pas dessiné d'une seule face
    one_sided = _mask(
        _render(
            _scene(
                tube,
                Material(base_color=(1, 1, 1, 1)),
                lights=lit,
                camera=Camera((0, 3, 0.01), (0, 0, 0)),
            )
        )
    )
    both = _mask(
        _render(
            _scene(
                tube,
                Material(double_sided=True),
                lights=lit,
                camera=Camera((0, 3, 0.01), (0, 0, 0)),
            )
        )
    )
    assert (
        one_sided.sum() < both.sum()
    )  # le côté éloigné du tube (face intérieure vue d'en haut) n'apparaît qu'à deux faces


# --- ombrage : les normales ----------------------------------------------------------------------------------


def _luma(frame: Frame, x: int, y: int) -> float:
    return float(np.asarray(frame.color)[y, x, :3].astype(np.float64).mean())


def test_sphere_shading_follows_the_normals() -> None:
    white = Material(base_color=(1, 1, 1, 1), roughness=1.0)
    front = _render(
        _scene(
            Sphere(0.6, 48, 24),
            white,
            lights=[DirectionalLight((0, 0, -1), (1, 1, 1), 0.5)],
        )
    )
    c = SIZE // 2
    radius_px = int(FOCAL * math.tan(math.asin(0.6 / DISTANCE)))
    assert (
        _luma(front, c, c) > _luma(front, c + radius_px - 1, c) + 25
    )  # plein face : lumineux ; limbe : sombre
    assert abs(_luma(front, c - 12, c) - _luma(front, c + 12, c)) < 6  # symétrique
    top = _render(
        _scene(
            Sphere(0.6, 48, 24),
            white,
            lights=[DirectionalLight((0, -1, 0), (1, 1, 1), 0.5)],
        )
    )
    assert (
        _luma(top, c, c - radius_px + 6) > _luma(top, c, c + radius_px - 6) + 25
    )  # éclairée par le haut
    side = _render(
        _scene(
            Sphere(0.6, 48, 24),
            white,
            lights=[DirectionalLight((-1, 0, 0), (1, 1, 1), 0.5)],
        )
    )
    assert (
        _luma(side, c + radius_px - 8, c) > _luma(side, c - radius_px + 8, c) + 25
    )  # la lumière se propage vers -X : côté +X éclairé


def test_cube_faces_are_flat_shaded_and_cone_cylinder_caps_follow_their_normals() -> (
    None
):
    white = Material(base_color=(1, 1, 1, 1), roughness=1.0)
    frame = _render(
        _scene(
            Cube(1, 1, 1), white, lights=[DirectionalLight((0, 0, -1), (1, 1, 1), 0.5)]
        )
    )
    c = SIZE // 2
    assert (
        abs(_luma(frame, c - 10, c - 10) - _luma(frame, c + 10, c + 10)) < 2
    )  # face plate : éclairage uniforme
    above = Camera((0, 4, 0.01), (0, 0, 0))
    for spec in (Cylinder(0.5, 1, 24), Cone(0.5, 1, 24, cap=True)):
        lit = _render(
            _scene(
                spec,
                white,
                lights=[DirectionalLight((0, -1, 0), (1, 1, 1), 0.5)],
                camera=above,
            )
        )
        dark = _render(
            _scene(
                spec,
                white,
                lights=[DirectionalLight((0, 1, 0), (1, 1, 1), 0.5)],
                camera=above,
            )
        )
        assert _luma(lit, c, c) > 60 and _luma(dark, c, c) < 10, (
            spec.kind
        )  # le dessus regarde vers le haut


# --- UV : une texture à quatre quadrants -------------------------------------------------------------------


def _quadrants() -> Material:
    pixels = np.array(
        [[[255, 0, 0, 255], [0, 255, 0, 255]], [[0, 0, 255, 255], [255, 255, 0, 255]]],
        dtype=np.uint8,
    )  # haut-gauche rouge, haut-droit vert, bas-gauche bleu, bas-droit jaune
    tex = texture_ref(
        pixels,
        mag_filter="nearest",
        min_filter="nearest",
        wrap_s="clamp",
        wrap_t="clamp",
    )
    return Material(base_color=(1, 1, 1, 1), base_color_texture=tex)


def _sample(frame: Frame, dx: int, dy: int) -> tuple[int, ...]:
    return tuple(
        int(v) for v in np.asarray(frame.color)[SIZE // 2 + dy, SIZE // 2 + dx, :3]
    )


EXPECTED_QUADRANTS = {
    (-1, -1): (255, 0, 0),
    (1, -1): (0, 255, 0),
    (-1, 1): (0, 0, 255),
    (1, 1): (255, 255, 0),
}


def _check_quadrants(frame: Frame, label: str, step: int = 8) -> None:
    for (sx, sy), color in EXPECTED_QUADRANTS.items():
        got = _sample(frame, sx * step, sy * step)
        assert all(abs(a - b) <= 6 for a, b in zip(got, color)), (
            label,
            (sx, sy),
            got,
            color,
        )


@pytest.mark.parametrize(
    ("normal", "up"),
    [
        ((0, 0, 1), (0, 1, 0)),
        ((0, 0, -1), (0, 1, 0)),
        ((1, 0, 0), (0, 1, 0)),
        ((-1, 0, 0), (0, 1, 0)),
        ((0, 1, 0), (0, 0, -1)),
        ((0, -1, 0), (0, 0, 1)),
    ],
)
def test_every_cube_face_shows_an_upright_unmirrored_texture(
    normal: tuple[int, int, int], up: tuple[int, int, int]
) -> None:
    camera = Camera(tuple(DISTANCE * n for n in normal), (0, 0, 0), up=up, fov_y_deg=40)
    frame = _render(
        _scene(
            Cube(1, 1, 1),
            _quadrants(),
            lights=[AmbientLight((1, 1, 1), 1.0)],
            camera=camera,
        )
    )
    _check_quadrants(frame, str(normal))


def test_plane_and_caps_show_an_upright_unmirrored_texture() -> None:
    lit = [AmbientLight((1, 1, 1), 1.0)]
    above = Camera((0, DISTANCE, 0), (0, 0, 0), up=(0, 0, -1))
    below = Camera((0, -DISTANCE, 0), (0, 0, 0), up=(0, 0, 1))
    _check_quadrants(
        _render(_scene(Plane(1, 1, 3, 3), _quadrants(), lights=lit, camera=above)),
        "plane",
    )
    _check_quadrants(
        _render(_scene(Cylinder(0.5, 1, 32), _quadrants(), lights=lit, camera=above)),
        "cylinder haut",
    )
    _check_quadrants(
        _render(_scene(Cylinder(0.5, 1, 32), _quadrants(), lights=lit, camera=below)),
        "cylinder bas",
    )
    _check_quadrants(
        _render(_scene(Cone(0.5, 1, 32), _quadrants(), lights=lit, camera=below)),
        "cône base",
    )


def test_a_checker_texture_is_clean_on_a_subdivided_plane() -> None:
    """Un damier 8 x 8 sur un plan subdivisé : chaque case est unie (aucune distorsion de UV entre les subdivisions)."""
    from tests.kut3d_scenes import checker_rgba

    tex = texture_ref(checker_rgba(64, 8), mag_filter="nearest", min_filter="nearest")
    material = Material(base_color=(1, 1, 1, 1), base_color_texture=tex)
    camera = Camera((0, DISTANCE, 0), (0, 0, 0), up=(0, 0, -1))
    frame = _render(
        _scene(
            Plane(2, 2, 5, 3),
            material,
            lights=[AmbientLight((1, 1, 1), 1.0)],
            camera=camera,
        ),
        size=128,
    )
    half = (
        FOCAL * (128 / SIZE) * 1.0 / DISTANCE
    )  # demi-côté du plan en pixels (côté 2 à distance 4)
    cell = 2 * half / 8
    colors = np.asarray(frame.color)[..., :3].astype(int)
    for row in range(8):
        for col in range(8):
            cx = int(64 - half + (col + 0.5) * cell)
            cy = int(64 - half + (row + 0.5) * cell)
            expected = (235, 120, 40) if (row + col) % 2 == 0 else (245, 235, 210)
            assert all(abs(a - b) <= 6 for a, b in zip(colors[cy, cx], expected)), (
                row,
                col,
                colors[cy, cx],
            )


# --- les six ensemble ----------------------------------------------------------------------------------------


def test_six_forms_scene_covers_six_separate_regions() -> None:
    width, height = 180, 120
    frame = render_frame(six_forms(), 0.0, size=(width, height), backend="numpy")
    shape = np.isfinite(
        np.asarray(frame.depth)
    )  # le fond est opaque : la profondeur dit où sont les formes
    for row in range(2):  # 3 colonnes x 2 rangées : chaque case contient une forme
        for col in range(3):
            cell = shape[
                row * height // 2 : (row + 1) * height // 2,
                col * width // 3 : (col + 1) * width // 3,
            ]
            assert cell.sum() > 200, (row, col)
    assert 0.1 < shape.mean() < 0.5
    colors = np.asarray(frame.color)
    assert tuple(int(v) for v in colors[1, 1]) == (
        26,
        26,
        36,
        255,
    )  # fond (0.1, 0.1, 0.14) opaque


def test_the_scene_is_deterministic() -> None:
    a = _render(six_forms(checker=True))
    b = _render(six_forms(checker=True))
    assert np.array_equal(a.color, b.color) and np.array_equal(a.depth, b.depth)
