"""Rendu de référence numpy : géométrie, z-buffer, culling, découpe du plan proche, perspective, profondeur,
fond, anti-crénelage, déterminisme, comparaison au spike de la phase 0 et performance."""

from __future__ import annotations

import math
import sys
import time
from pathlib import Path

import numpy as np
import pytest

from kut3d import Camera, Instance, Scene, render_frame
from kut3d.mathutil import rotation_x, scale_matrix, translation_matrix
from kut3d.model import Material, Mesh
from tests.kut3d_scenes import (
    WHITE_AMBIENT,
    channel,
    checker_rgba,
    cube_model,
    flat,
    front_camera,
    model_of,
    quad_model,
    scene,
    sun,
    texture_ref,
)
from kut3d.model import AnimationClip, Model, Node

ROOT = Path(__file__).resolve().parent.parent
RED = (1.0, 0.0, 0.0, 1.0)
BLUE = (0.0, 0.0, 1.0, 1.0)
FULL_FRAME_CAMERA = {
    "z": 1.0,
    "fov_y_deg": 90.0,
}  # à z=1, un carré de demi-côté 1 remplit exactement l'image


def rotate_y(deg: float) -> np.ndarray:
    a = math.radians(deg)
    m = np.eye(4)
    m[0, 0], m[0, 2], m[2, 0], m[2, 2] = (
        math.cos(a),
        math.sin(a),
        -math.sin(a),
        math.cos(a),
    )
    return m


def full_cam(**kw: float) -> Camera:
    return front_camera(**{**FULL_FRAME_CAMERA, **kw})


def test_full_frame_quad_covers_every_pixel_even_on_the_shared_diagonal() -> None:
    frame = render_frame(scene(quad_model(flat(RED)), camera=full_cam()), size=(16, 16))
    assert (frame.alpha == 1.0).all()
    assert (frame.color == [255, 0, 0, 255]).all()
    assert np.allclose(frame.depth, 1.0, atol=1e-5)


def test_frame_is_immutable_and_well_shaped() -> None:
    frame = render_frame(scene(quad_model(flat(RED))), size=(20, 10))
    assert frame.size == (20, 10)
    assert frame.color.shape == (10, 20, 4) and frame.color.dtype == np.uint8
    assert frame.alpha.shape == (10, 20) and frame.alpha.dtype == np.float32
    assert frame.depth.shape == (10, 20) and frame.depth.dtype == np.float32
    with pytest.raises(ValueError):
        frame.color[0, 0, 0] = 1
    with pytest.raises(AttributeError):
        frame.size = (1, 1)  # type: ignore[misc]


def test_z_buffer_nearer_object_hides_farther_one_in_any_order() -> None:
    near = Instance(quad_model(flat(RED), half=0.4, z=1.0))
    far = Instance(quad_model(flat(BLUE), half=1.0, z=0.0))
    for order in ([near, far], [far, near]):
        frame = render_frame(scene(order, camera=front_camera(z=4.0)), size=(32, 32))
        assert tuple(frame.color[16, 16]) == (
            255,
            0,
            0,
            255,
        )  # le proche gagne, même dessiné en premier
        assert tuple(frame.color[16, 8]) == (
            0,
            0,
            255,
            255,
        )  # hors du proche, on voit le lointain
        assert frame.depth[16, 16] == pytest.approx(
            3.0, abs=1e-4
        )  # z=1 : à 3 de la caméra
        assert frame.depth[16, 8] == pytest.approx(4.0, abs=1e-3)


def test_back_faces_are_culled_unless_double_sided() -> None:
    back = Instance(quad_model(flat(RED)), rotate_y(180.0))
    frame = render_frame(scene(back, camera=front_camera(z=3.0)), size=(16, 16))
    assert (frame.alpha == 0).all() and np.isinf(frame.depth).all()
    both = Instance(quad_model(flat(RED, double_sided=True)), rotate_y(180.0))
    frame = render_frame(scene(both, camera=front_camera(z=3.0)), size=(16, 16))
    assert frame.alpha[8, 8] == 1.0 and tuple(frame.color[8, 8]) == (255, 0, 0, 255)


def test_mirrored_instance_flips_winding_like_gltf() -> None:
    # Le miroir (déterminant négatif) inverse le sens des triangles : la face reste une face avant.
    mirrored = Instance(
        quad_model(flat(RED)), scale_matrix(1.0) @ np.diag([-1.0, 1.0, 1.0, 1.0])
    )
    frame = render_frame(scene(mirrored, camera=front_camera(z=3.0)), size=(16, 16))
    assert frame.alpha[8, 8] == 1.0


def _floor_model(material: Material) -> Model:
    pos = [[-8, -1, 5], [8, -1, 5], [8, -1, -40], [-8, -1, -40]]
    uv = [[0, 0], [1, 0], [1, 1], [0, 1]]  # u = (x + 8) / 16, v = (5 - z) / 45
    mesh = Mesh(
        pos, [[0, 1, 2], [0, 2, 3]], normals=[[0, 1, 0]] * 4, uvs=uv, material_index=0
    )
    return model_of(mesh, material)


def _floor_rays(
    size: int, fov: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Pour chaque pixel : (profondeur de vue, x, z du point du sol y=-1) ou NaN au-dessus de l'horizon."""
    tan = math.tan(math.radians(fov) / 2)
    j, i = np.mgrid[0:size, 0:size]
    ndc_x = (i + 0.5) / size * 2 - 1
    ndc_y = 1 - (j + 0.5) / size * 2
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.where(ndc_y < 0, -1.0 / (ndc_y * tan), np.nan)
    return t, ndc_x * tan * t, -t, ndc_y


def test_triangle_crossing_the_near_plane_is_clipped_not_dropped() -> None:
    # Le sol dépasse derrière la caméra (z = +5) : sans découpe, les deux triangles auraient un sommet derrière
    # la caméra. Profondeur attendue calculée à la main : pour un pixel sous l'horizon, t = 1 / (-ndc_y tan(fov/2)).
    cam = Camera((0, 0, 0), (0, 0, -1), fov_y_deg=60.0, near=0.1, far=100.0)
    frame = render_frame(scene(_floor_model(flat(RED)), camera=cam), size=(64, 64))
    t, x, z, _ = _floor_rays(64, 60.0)
    below = ~np.isnan(t) & (np.abs(x) < 7.9) & (z > -39.0)
    assert below.sum() > 1000
    assert (frame.alpha[below] == 1.0).all()
    assert np.allclose(frame.depth[below], t[below], rtol=2e-3)
    above = np.isnan(t)
    assert (frame.alpha[above] == 0.0).all()  # au-dessus de l'horizon : rien


def test_triangle_entirely_behind_the_camera_is_ignored() -> None:
    cam = Camera((0, 0, 0), (0, 0, -1))
    behind = Instance(quad_model(flat(RED), z=5.0))  # derrière la caméra
    frame = render_frame(scene(behind, camera=cam), size=(8, 8))
    assert (frame.alpha == 0).all()
    beyond = Instance(
        quad_model(flat(RED), z=-500.0)
    )  # au-delà du plan lointain (far = 1000 par défaut : dedans)
    far_cam = Camera((0, 0, 0), (0, 0, -1), far=100.0)
    frame = render_frame(scene(beyond, camera=far_cam), size=(8, 8))
    assert (frame.alpha == 0).all()


def test_camera_inside_a_huge_triangle_covers_the_frame() -> None:
    big = Mesh(
        [[-1e4, -1e4, -1.0], [1e4, -1e4, -1.0], [0, 1e4, -1.0]],
        [[0, 1, 2]],
        material_index=0,
    )
    cam = Camera((0, 0, 0), (0, 0, -1))
    frame = render_frame(scene(model_of(big, flat(RED)), camera=cam), size=(16, 16))
    assert (frame.alpha == 1.0).all() and np.allclose(frame.depth, 1.0, atol=1e-4)


def test_uv_are_perspective_correct_on_a_receding_floor() -> None:
    a, b = (235, 120, 40, 255), (245, 235, 210, 255)
    tex = texture_ref(checker_rgba(64, 8, a, b))
    material = flat((1, 1, 1, 1), base_color_texture=tex)
    cam = Camera((0, 0, 0), (0, 0, -1), fov_y_deg=60.0, near=0.1, far=100.0)
    size = 128
    frame = render_frame(scene(_floor_model(material), camera=cam), size=(size, size))
    t, x, z, _ = _floor_rays(size, 60.0)
    u, v = (x + 8) / 16, (5 - z) / 45
    fu, fv = (u * 8) % 1, (v * 8) % 1
    margin = np.minimum.reduce([fu, 1 - fu, fv, 1 - fv])
    ok = ~np.isnan(t) & (t < 15) & (np.abs(x) < 7.5) & (margin > 0.15)
    assert ok.sum() > 2000
    odd = (np.floor(u * 8) + np.floor(v * 8)) % 2 == 1
    expected = np.where(odd[..., None], np.array(b[:3]), np.array(a[:3]))
    close = (np.abs(frame.color[..., :3].astype(int) - expected) <= 2).all(axis=2)
    assert (
        close[ok].mean() > 0.995
    )  # une interpolation affine se tromperait de case sur une grande partie du sol


def test_depth_value_at_center_and_void_is_infinity() -> None:
    frame = render_frame(
        scene(quad_model(flat(RED), half=0.5), camera=front_camera(z=5.0)),
        size=(32, 32),
    )
    assert frame.depth[16, 16] == pytest.approx(
        5.0, abs=1e-4
    )  # distance de vue le long de l'axe, pas euclidienne
    assert np.isinf(frame.depth[0, 0]) and frame.alpha[0, 0] == 0
    # un point hors axe garde la profondeur de VUE (5), pas la distance au centre optique
    assert frame.depth[16, 17] == pytest.approx(5.0, abs=1e-4)
    assert np.isinf(frame.depth).sum() + np.isfinite(frame.depth).sum() == 32 * 32


def test_object_beyond_far_plane_is_not_drawn() -> None:
    cam = Camera((0, 0, 0), (0, 0, -1), far=10.0)
    frame = render_frame(
        scene(quad_model(flat(RED), half=50.0, z=-20.0), camera=cam), size=(8, 8)
    )
    assert (frame.alpha == 0).all()


def test_transparent_background_by_default_and_opaque_or_translucent_background() -> (
    None
):
    sc = scene(quad_model(flat(RED), half=0.4), camera=front_camera(z=4.0))
    frame = render_frame(sc, size=(16, 16))
    assert tuple(frame.color[0, 0]) == (0, 0, 0, 0) and frame.alpha[0, 0] == 0
    assert tuple(frame.color[8, 8]) == (255, 0, 0, 255)
    opaque = Scene(sc.instances, sc.lights, sc.camera, background=(0.0, 0.5, 1.0, 1.0))
    frame = render_frame(opaque, size=(16, 16))
    assert tuple(frame.color[0, 0]) == (0, 128, 255, 255)  # aller-retour sRGB exact
    assert frame.alpha[0, 0] == 1.0 and tuple(frame.color[8, 8]) == (255, 0, 0, 255)
    half = Scene(sc.instances, sc.lights, sc.camera, background=(0.0, 0.0, 1.0, 0.5))
    frame = render_frame(half, size=(16, 16))
    assert frame.color[0, 0, 3] == 128 and tuple(frame.color[0, 0, :3]) == (
        0,
        0,
        255,
    )  # alpha droit
    assert tuple(frame.color[8, 8]) == (255, 0, 0, 255)  # l'objet opaque couvre le fond


def test_unlit_scene_without_lights_is_black_but_covered() -> None:
    frame = render_frame(
        Scene([Instance(quad_model(flat(RED), half=0.4))], [], front_camera(z=4.0)),
        size=(16, 16),
    )
    assert tuple(frame.color[8, 8]) == (0, 0, 0, 255)


def test_mesh_without_material_is_default_grey() -> None:
    frame = render_frame(
        scene(quad_model(None, half=0.4), camera=front_camera(z=4.0)), size=(16, 16)
    )
    expected = round(255 * (1.055 * 0.8 ** (1 / 2.4) - 0.055))  # gris linéaire 0,8
    assert tuple(frame.color[8, 8]) == (expected, expected, expected, 255)


def _edge_scene(samples_edge_x: float) -> Scene:
    # fov 90°, caméra à 1 : l'abscisse écran du bord droit vaut (x * 0.5 + 0.5) * largeur ; x = 0.375 -> 5,5 px / 8
    mesh = Mesh(
        [[-2, -2, 0], [samples_edge_x, -2, 0], [samples_edge_x, 2, 0], [-2, 2, 0]],
        [[0, 1, 2], [0, 2, 3]],
        material_index=0,
    )
    return scene(model_of(mesh, flat(RED)), camera=full_cam())


@pytest.mark.parametrize("samples", [2, 4])
def test_supersampling_gives_intermediate_alpha_on_a_half_covered_pixel(
    samples: int,
) -> None:
    frame = render_frame(_edge_scene(0.375), size=(8, 8), samples=samples)
    assert (frame.alpha[:, :5] == 1.0).all() and (frame.alpha[:, 6:] == 0.0).all()
    assert np.allclose(frame.alpha[:, 5], 0.5)  # moitié du pixel couverte
    assert (frame.color[:, 5, 3] == 128).all()
    # alpha droit : la couleur du pixel de bord n'est PAS assombrie par la couverture
    assert (frame.color[:, 5, :3] == [255, 0, 0]).all()
    assert np.isfinite(
        frame.depth[:, 5]
    ).all()  # profondeur du plus proche des échantillons couverts


def test_without_supersampling_edges_are_binary() -> None:
    frame = render_frame(_edge_scene(0.31), size=(8, 8), samples=1)
    assert set(np.unique(frame.alpha)) <= {0.0, 1.0}


def test_supersampling_keeps_frame_size_and_averages_in_linear_space() -> None:
    # Moitié rouge (255,0,0), moitié noire : en linéaire, 0,5 -> sRGB 188 (et non 128 comme une moyenne sRGB)
    half = Mesh(
        [[-2, -2, 0], [0.375, -2, 0], [0.375, 2, 0], [-2, 2, 0]],
        [[0, 1, 2], [0, 2, 3]],
        material_index=0,
    )
    sc = scene(
        model_of(half, flat(RED)), camera=full_cam(), background=(0.0, 0.0, 0.0, 1.0)
    )
    frame = render_frame(sc, size=(8, 8), samples=2)
    assert frame.color.shape == (8, 8, 4)
    assert abs(int(frame.color[4, 5, 0]) - 188) <= 1


def test_render_is_deterministic_byte_for_byte() -> None:
    tex = texture_ref(checker_rgba(64, 8))
    sc = scene(
        [
            Instance(
                cube_model(flat((1, 1, 1, 1), base_color_texture=tex)),
                rotate_y(35.0) @ rotation_x(25.0),
            )
        ],
        [sun((-0.4, -0.7, -0.6)), WHITE_AMBIENT],
        front_camera(z=5.0),
    )
    a = render_frame(sc, 0.0, (48, 40), samples=2)
    b = render_frame(sc, 0.0, (48, 40), samples=2)
    assert a.color.tobytes() == b.color.tobytes()
    assert (
        a.alpha.tobytes() == b.alpha.tobytes()
        and a.depth.tobytes() == b.depth.tobytes()
    )


def test_animated_instance_moves_with_time() -> None:
    clip = AnimationClip(
        "slide", 2.0, (channel("translation", [0, 2], [[-1.0, 0, 0], [1.0, 0, 0]]),)
    )
    base = quad_model(flat(RED), half=0.25)
    model = Model(
        nodes=(Node(mesh_index=0),),
        root_nodes=(0,),
        meshes=base.meshes,
        materials=base.materials,
        animations=(clip,),
    )
    sc = scene(Instance(model, clip="slide"), camera=full_cam(z=1.0))
    cols = []
    for t in (0.0, 1.0, 2.0):
        frame = render_frame(sc, t, (32, 32))
        cols.append(float(np.nonzero(frame.alpha.any(axis=0))[0].mean()))
    assert cols[0] < cols[1] < cols[2]
    assert cols[1] == pytest.approx(15.5, abs=0.6)  # t = 1 : au centre
    shifted = scene(
        Instance(model, clip="slide", time_offset=1.0), camera=full_cam(z=1.0)
    )
    assert np.array_equal(
        render_frame(shifted, 0.0, (32, 32)).color,
        render_frame(sc, 1.0, (32, 32)).color,
    )


def test_instance_transform_places_the_model() -> None:
    sc = scene(
        Instance(quad_model(flat(RED), half=0.25), translation_matrix([0.5, 0, 0])),
        camera=full_cam(),
    )
    frame = render_frame(sc, size=(32, 32))
    xs = np.nonzero(frame.alpha.any(axis=0))[0]
    assert xs.min() >= 16 and xs.max() <= 28


# --- oracle : le rendu du spike de la phase 0 --------------------------------------------------------------------


@pytest.fixture
def spike():  # type: ignore[no-untyped-def]
    folder = ROOT / "experiments" / "kut3d-spike"
    if not folder.is_dir():
        pytest.skip("experiments/kut3d-spike absent")
    sys.path.insert(0, str(folder))
    try:
        import cube_math
        import render_numpy

        yield cube_math, render_numpy
    finally:
        sys.path.remove(str(folder))
        for name in ("cube_math", "render_numpy", "texture"):
            sys.modules.pop(name, None)


def test_matches_the_spike_oracle_within_documented_tolerance(spike) -> None:  # type: ignore[no-untyped-def]
    cm, rn = spike
    angle = 35.0
    # 1. mêmes matrices : projection x vue x modèle du spike = celles de la caméra et de l'instance
    cam = Camera(
        cm.CAMERA_POS, (0, 0, 0), fov_y_deg=cm.FOV_DEG, near=cm.NEAR, far=cm.FAR
    )
    mine = cam.projection_matrix(1.0) @ cam.view_matrix() @ cm.model_matrix(angle)
    theirs = cm.perspective_matrix(1.0) @ cm.view_matrix() @ cm.model_matrix(angle)
    assert np.allclose(mine, theirs, atol=1e-5)
    # 2. même image : silhouette et cases du damier
    tex = texture_ref(checker_rgba(256, 8))
    sc = scene(
        [
            Instance(
                cube_model(flat((1, 1, 1, 1), base_color_texture=tex)),
                cm.model_matrix(angle).astype(np.float64),
            )
        ],
        [WHITE_AMBIENT],
        cam,
    )
    mine_img = render_frame(sc, size=(512, 512))
    ref = rn.render_cube(512, 512, angle)
    ref_cover = (ref != np.array(cm.BACKGROUND, np.uint8)).any(axis=2)
    mine_cover = mine_img.alpha > 0.5
    assert (
        ref_cover == mine_cover
    ).mean() > 0.999  # silhouette : 99,9 % des pixels identiques
    both = ref_cover & mine_cover

    def is_orange(
        rgb: np.ndarray,
    ) -> (
        np.ndarray
    ):  # G/R ~ 0,51 (orange) contre ~ 0,96 (crème), insensible à l'ombrage
        return rgb[..., 1].astype(float) / np.maximum(rgb[..., 0], 1) < 0.75

    agree = is_orange(ref[both][:, :3]) == is_orange(mine_img.color[both][:, :3])
    assert (
        agree.mean() > 0.98
    )  # mesuré 99,5 % : bilinéaire contre nearest, écarts aux bords des cases


# --- performance (seuils généreux) -----------------------------------------------------------------------------------


def test_textured_cube_512_renders_in_under_2_seconds() -> None:
    tex = texture_ref(checker_rgba(256, 8))
    sc = scene(
        [
            Instance(
                cube_model(flat((1, 1, 1, 1), base_color_texture=tex)),
                rotate_y(35.0) @ rotation_x(25.0),
            )
        ],
        [sun((-0.4, -0.7, -0.6)), WHITE_AMBIENT],
        front_camera(z=5.0, fov_y_deg=45.0),
    )
    render_frame(sc, size=(64, 64))  # chauffe (décodage de la texture)
    start = time.perf_counter()
    frame = render_frame(sc, size=(512, 512))
    elapsed = time.perf_counter() - start
    print(f"\ncube texturé 512x512 : {elapsed:.3f} s")
    assert elapsed < 2.0 and frame.alpha.max() == 1.0


def test_20000_triangle_scene_renders_in_under_30_seconds() -> None:
    n = 100  # 100 x 100 carrés = 20 000 triangles
    gx, gy = np.meshgrid(np.linspace(-1, 1, n + 1), np.linspace(-1, 1, n + 1))
    height = 0.08 * np.sin(gx * 9) * np.cos(gy * 9)
    pos = np.stack([gx.ravel(), height.ravel(), gy.ravel()], axis=1)
    idx = np.arange((n + 1) * (n + 1)).reshape(n + 1, n + 1)
    a, b, c, d = (
        idx[:-1, :-1].ravel(),
        idx[:-1, 1:].ravel(),
        idx[1:, 1:].ravel(),
        idx[1:, :-1].ravel(),
    )
    tri = np.concatenate([np.stack([a, d, c], 1), np.stack([a, c, b], 1)])
    uv = np.stack([(gx.ravel() + 1) / 2, (gy.ravel() + 1) / 2], axis=1)
    mesh = Mesh(pos, tri, uvs=uv, material_index=0)
    assert mesh.triangle_count == 20_000
    material = flat((1, 1, 1, 1), base_color_texture=texture_ref(checker_rgba(128, 16)))
    cam = Camera((0, 1.2, 2.2), (0, 0, 0))
    sc = Scene(
        [Instance(model_of(mesh, material))],
        [sun((-0.3, -1, -0.5), 1.2), WHITE_AMBIENT],
        cam,
    )
    start = time.perf_counter()
    frame = render_frame(sc, size=(640, 360))
    elapsed = time.perf_counter() - start
    print(f"\nscène de 20 000 triangles 640x360 : {elapsed:.2f} s")
    assert elapsed < 30.0 and (frame.alpha > 0).mean() > 0.2
