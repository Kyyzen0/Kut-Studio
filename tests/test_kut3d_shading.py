"""Ombrage PBR : lumières, métal / diélectrique, textures (normale, métal-rugosité, émission, occlusion), modes alpha."""

from __future__ import annotations

import math

import numpy as np
import pytest

from kut3d import (
    AmbientLight,
    Camera,
    Instance,
    PointLight,
    Scene,
    render_frame,
)
from kut3d.mathutil import translation_matrix
from kut3d.model import Material
from tests.kut3d_scenes import (
    WHITE_AMBIENT,
    checker_rgba,
    flat,
    front_camera,
    model_of,
    quad_mesh,
    quad_model,
    scene,
    srgb_to_lin,
    sun,
    texture_ref,
)

VIEW = np.array([0.0, 0.0, 1.0])  # la caméra voit la face +Z de face
NORMAL = np.array([0.0, 0.0, 1.0])


def ref_brdf(
    n: np.ndarray,
    to_light: np.ndarray,
    base: np.ndarray,
    metal: float,
    rough: float,
    intensity: float = 1.0,
) -> np.ndarray:
    """Cook-Torrance (glTF) écrit d'après la spécification, pour un point : rayonnement = intensité x π."""
    n = n / np.linalg.norm(n)
    l = to_light / np.linalg.norm(to_light)
    h = (l + VIEW) / np.linalg.norm(l + VIEW)
    nl, nv = max(float(n @ l), 0.0), max(float(n @ VIEW), 1e-4)
    nh, vh = max(float(n @ h), 0.0), max(float(VIEW @ h), 0.0)
    a2 = (rough * rough) ** 2
    f0 = 0.04 * (1 - metal) + base * metal
    f = f0 + (1 - f0) * (1 - vh) ** 5
    d = a2 / (math.pi * (nh * nh * (a2 - 1) + 1) ** 2)
    vis = 0.5 / (
        nl * math.sqrt(nv * nv * (1 - a2) + a2)
        + nv * math.sqrt(nl * nl * (1 - a2) + a2)
    )
    diffuse = (1 - f) * base * (1 - metal) / math.pi
    return (diffuse + f * d * vis) * nl * math.pi * intensity


def center_linear(sc: Scene, size: int = 8) -> np.ndarray:
    frame = render_frame(sc, size=(size, size))
    return np.array(
        [srgb_to_lin(float(v)) for v in frame.color[size // 2, size // 2, :3]]
    )


# Champ de 4° : le pixel central voit la surface presque exactement le long de la normale (le vecteur de vue de
# la référence scalaire est (0, 0, 1)).
NARROW = {"z": 1.0, "fov_y_deg": 4.0}


def lit(material: Material, lights: list, mesh=None) -> np.ndarray:  # type: ignore[no-untyped-def]
    model = quad_model(material, half=2.0) if mesh is None else model_of(mesh, material)
    return center_linear(scene(model, lights, front_camera(**NARROW)))


def test_head_on_white_diffuse_surface_is_hand_computed_0_97() -> None:
    # F0 = 0,04 ; D = 1/π (rugosité 1) ; V = 0,25 : spéculaire = 0,04 x 0,25 = 0,01 ; diffus = (1 - 0,04) = 0,96
    out = lit(flat((1, 1, 1, 1)), [sun((0, 0, -1))])
    assert out == pytest.approx([0.97] * 3, abs=0.004)


@pytest.mark.parametrize("angle", [0, 30, 60, 80])
@pytest.mark.parametrize(
    "metal, rough", [(0.0, 0.5), (0.0, 1.0), (1.0, 0.3), (0.5, 0.7)]
)
def test_directional_light_matches_scalar_reference(
    angle: int, metal: float, rough: float
) -> None:
    a = math.radians(angle)
    to_light = np.array([math.sin(a), 0.0, math.cos(a)])
    base = np.array([0.6, 0.3, 0.1])  # linéaire
    mat = Material(base_color=(*base, 1.0), metallic=metal, roughness=rough)
    out = lit(mat, [sun(tuple(-to_light), 0.5)])  # direction = sens de propagation
    expected = np.clip(ref_brdf(NORMAL, to_light, base, metal, rough, 0.5), 0, 1)
    assert out == pytest.approx(expected, abs=0.012)


def test_light_direction_changes_brightness_and_back_light_is_black() -> None:
    mat = flat((1, 1, 1, 1))
    head_on = lit(mat, [sun((0, 0, -1), 0.8)])[0]
    oblique = lit(mat, [sun((-1, 0, -1), 0.8)])[0]
    grazing = lit(mat, [sun((-5, 0, -0.5), 0.8)])[0]
    behind = lit(mat, [sun((0, 0, 1), 0.8)])[0]
    assert head_on > oblique > grazing > behind
    assert oblique == pytest.approx(head_on * math.cos(math.radians(45)), rel=0.05)
    assert behind == 0.0


def test_two_lights_add_up_in_linear_space() -> None:
    mat = flat((1, 1, 1, 1))
    one = lit(mat, [sun((0, 0, -1), 0.3)])[0]
    two = lit(mat, [sun((0, 0, -1), 0.3), sun((0, 0, -1), 0.3)])[0]
    assert two == pytest.approx(2 * one, rel=0.02)
    with_ambient = lit(mat, [sun((0, 0, -1), 0.3), AmbientLight((1, 1, 1), 0.2)])[0]
    assert with_ambient == pytest.approx(one + 0.2, abs=0.01)


def test_point_light_inverse_square_attenuation_and_range_window() -> None:
    mat = flat((1, 1, 1, 1))
    near = lit(mat, [PointLight((0, 0, 2), intensity=1.0)])[0]  # le quad est à z = 0
    far = lit(mat, [PointLight((0, 0, 4), intensity=1.0)])[0]
    assert near == pytest.approx(0.97 / 4, abs=0.006) and far == pytest.approx(
        0.97 / 16, abs=0.004
    )
    assert near / far == pytest.approx(4.0, rel=0.06)
    # fenêtre de portée (glTF) : (1 - (d/range)^4)^2 ; à d = 2 et range = 3 -> 0,644 ; au-delà de la portée -> 0
    windowed = lit(mat, [PointLight((0, 0, 2), intensity=1.0, range=3.0)])[0]
    assert windowed == pytest.approx(near * (1 - (2 / 3) ** 4) ** 2, rel=0.03)
    assert lit(mat, [PointLight((0, 0, 2), intensity=1.0, range=1.5)])[0] == 0.0
    # un point sur le côté éclaire moins le centre qu'un point de face à la même distance
    side = lit(mat, [PointLight((2, 0, 2), intensity=1.0)])[0]
    assert side < near


def test_metal_and_dielectric_differ_under_direct_light_and_metal_keeps_its_tint() -> (
    None
):
    base = (0.8, 0.2, 0.2, 1.0)
    light = [sun((0, 0, -1), 1.0)]
    dielectric = lit(Material(base_color=base, metallic=0.0, roughness=1.0), light)
    metal = lit(Material(base_color=base, metallic=1.0, roughness=1.0), light)
    # métal rugueux de face : F = base ; D = 1/π ; V = 0,25 -> F x 0,25 = 0,2 (rouge)
    assert metal[0] == pytest.approx(0.2, abs=0.01) and metal[1] == pytest.approx(
        0.05, abs=0.01
    )
    assert dielectric[0] == pytest.approx(0.8 * 0.96 + 0.01, abs=0.01)
    assert dielectric[0] > 3 * metal[0] and metal[0] > metal[1]
    # sous l'ambiance seule, métal et diélectrique éclairent la couleur de base de la même façon
    amb = lit(Material(base_color=base, metallic=1.0, roughness=1.0), [WHITE_AMBIENT])
    assert amb == pytest.approx([0.8, 0.2, 0.2], abs=0.004)


def test_glossy_metal_has_a_stronger_highlight_than_rough_metal() -> None:
    base = (0.9, 0.9, 0.9, 1.0)
    light = [sun((0, 0, -1), 0.05)]
    glossy = lit(Material(base_color=base, metallic=1.0, roughness=0.2), light)[0]
    rough = lit(Material(base_color=base, metallic=1.0, roughness=0.9), light)[0]
    assert glossy > 4 * rough


# --- textures --------------------------------------------------------------------------------------------------


def test_base_color_texture_is_read_as_srgb_and_multiplied_by_the_factor() -> None:
    tex = texture_ref([0, 128, 255, 255])
    frame = render_frame(
        scene(
            quad_model(flat((1, 1, 1, 1), base_color_texture=tex), half=2.0),
            camera=front_camera(z=1.0, fov_y_deg=90.0),
        ),
        size=(8, 8),
    )
    assert (
        abs(int(frame.color[4, 4, 1]) - 128) <= 1
    )  # aller-retour sRGB -> linéaire -> sRGB
    assert tuple(frame.color[4, 4, [0, 2]]) == (0, 255)
    half = render_frame(
        scene(
            quad_model(flat((0.5, 0.5, 0.5, 1), base_color_texture=tex), half=2.0),
            camera=front_camera(z=1.0, fov_y_deg=90.0),
        ),
        size=(8, 8),
    )
    assert half.color[4, 4, 2] == round(
        255 * (1.055 * 0.5 ** (1 / 2.4) - 0.055)
    )  # facteur 0,5 linéaire x texture 1,0


def test_vertex_colors_multiply_the_base_color() -> None:
    mesh = quad_mesh(2.0)
    from kut3d.model import Mesh

    tinted = Mesh(
        mesh.positions,
        mesh.indices,
        normals=mesh.normals,
        uvs=mesh.uvs,
        colors=[[1, 0, 0, 1]] * 4,
        material_index=0,
    )
    out = lit(flat((1, 1, 1, 1)), [WHITE_AMBIENT], mesh=tinted)
    assert out == pytest.approx([1.0, 0.0, 0.0], abs=0.004)


def test_emissive_factor_and_texture_add_light_without_any_lamp() -> None:
    cam = front_camera(z=1.0, fov_y_deg=90.0)
    out = center_linear(
        scene(
            quad_model(
                Material(base_color=(0, 0, 0, 1), emissive=(0.0, 0.5, 0.0)), half=2.0
            ),
            [],
            cam,
        )
    )
    assert out == pytest.approx([0.0, 0.5, 0.0], abs=0.004)
    tex = texture_ref([255, 0, 0, 255])
    mat = Material(
        base_color=(0, 0, 0, 1), emissive=(1.0, 1.0, 1.0), emissive_texture=tex
    )
    out = center_linear(scene(quad_model(mat, half=2.0), [], cam))
    assert out == pytest.approx([1.0, 0.0, 0.0], abs=0.004)
    dim = Material(
        base_color=(0, 0, 0, 1),
        emissive=(0.5, 0.5, 0.5),
        emissive_texture=texture_ref([128, 128, 128, 255]),
    )
    assert center_linear(scene(quad_model(dim, half=2.0), [], cam))[0] == pytest.approx(
        0.5 * srgb_to_lin(128), abs=0.004
    )


def test_occlusion_texture_darkens_ambient_but_not_direct_light() -> None:
    occ = texture_ref([128, 255, 255, 255])  # canal R = 128 : environ 0,5
    mat = flat((1, 1, 1, 1), occlusion_texture=occ)
    ambient = lit(mat, [WHITE_AMBIENT])[0]
    assert ambient == pytest.approx(128 / 255, abs=0.006)
    direct = lit(mat, [sun((0, 0, -1), 1.0)])[0]
    assert direct == pytest.approx(0.97, abs=0.006)


def test_metallic_roughness_texture_is_linear_and_uses_g_and_b() -> None:
    light = [sun((0, 0, -1), 1.0)]
    dielectric = lit(
        flat(
            (0.8, 0.2, 0.2, 1),
            metallic=1.0,
            metallic_roughness_texture=texture_ref([0, 255, 0, 255]),
        ),
        light,
    )
    metal = lit(
        flat(
            (0.8, 0.2, 0.2, 1),
            metallic=1.0,
            metallic_roughness_texture=texture_ref([0, 255, 255, 255]),
        ),
        light,
    )
    assert (
        dielectric[0] > 3 * metal[0]
    )  # B = 0 : métal x 0 = diélectrique ; B = 1 : métal
    mid = lit(
        flat(
            (1, 1, 1, 1),
            metallic=0.0,
            roughness=1.0,
            metallic_roughness_texture=texture_ref([0, 128, 0, 255]),
        ),
        light,
    )
    # G = 128/255 lu en LINÉAIRE (0,502), pas converti depuis sRGB (0,216) : rugosité 0,502 -> reflet plus vif que 1
    assert mid[0] > 0.97


def _tilted_normal_map(nx: float, ny: float) -> np.ndarray:
    nz = math.sqrt(max(0.0, 1 - nx * nx - ny * ny))
    px = [round((c * 0.5 + 0.5) * 255) for c in (nx, ny, nz)] + [255]
    return texture_ref(px)


@pytest.mark.parametrize(
    "tilt, toward, bright",
    [
        (
            (0.7071, 0.0),
            (1, 0, 1),
            True,
        ),  # normale inclinée vers +X, lumière côté +X : éclairé à fond
        ((-0.7071, 0.0), (1, 0, 1), False),  # inclinée à l'opposé : sombre
        (
            (0.0, 0.7071),
            (0, 1, 1),
            True,
        ),  # vert = +Y vers le HAUT de l'image (V vers le bas) : lumière côté +Y
        ((0.0, -0.7071), (0, 1, 1), False),
    ],
)
def test_normal_texture_changes_shading_with_the_right_handedness(
    tilt: tuple[float, float], toward: tuple[int, int, int], bright: bool
) -> None:
    mat = flat((1, 1, 1, 1), normal_texture=_tilted_normal_map(*tilt))
    to_light = np.array(toward, float)
    out = lit(mat, [sun(tuple(-to_light), 1.0)])[0]
    n_tilted = np.array([tilt[0], tilt[1], math.sqrt(1 - tilt[0] ** 2 - tilt[1] ** 2)])
    expected = float(ref_brdf(n_tilted, to_light, np.ones(3), 0.0, 1.0, 1.0)[0])
    assert out == pytest.approx(min(expected, 1.0), abs=0.02)
    flat_value = lit(flat((1, 1, 1, 1)), [sun(tuple(-to_light), 1.0)])[0]
    assert (out > flat_value + 0.05) if bright else (out < flat_value - 0.05)


def test_texture_without_uv_falls_back_to_factors() -> None:
    tex = texture_ref([255, 0, 0, 255])
    mat = flat((0.0, 1.0, 0.0, 1.0), base_color_texture=tex)
    out = lit(mat, [WHITE_AMBIENT], mesh=quad_mesh(2.0, uvs=False))
    assert out == pytest.approx(
        [0.0, 1.0, 0.0], abs=0.004
    )  # la texture est ignorée, pas échantillonnée en (0, 0)


def test_double_sided_back_face_is_lit_from_its_visible_side() -> None:
    cam = Camera(
        (0, 0, -1), (0, 0, 0), fov_y_deg=90.0
    )  # voit le quad (face +Z) par derrière
    both = quad_model(flat((1, 1, 1, 1), double_sided=True), half=2.0)
    toward_camera = [
        sun((0, 0, 1), 1.0)
    ]  # se propage vers +Z : éclaire la face arrière
    out = center_linear(scene(both, toward_camera, cam))
    assert out[0] == pytest.approx(
        0.97, abs=0.006
    )  # la normale est retournée vers l'observateur
    away = center_linear(scene(both, [sun((0, 0, -1), 1.0)], cam))
    assert away[0] == 0.0
    single = render_frame(
        scene(quad_model(flat((1, 1, 1, 1)), half=2.0), toward_camera, cam), size=(8, 8)
    )
    assert (single.alpha == 0).all()


# --- modes alpha -----------------------------------------------------------------------------------------------


def _holes_texture() -> np.ndarray:
    return checker_rgba(
        64, 8, (255, 0, 0, 255), (0, 255, 0, 0)
    )  # cases rouges opaques / vertes transparentes


def test_alpha_mask_discards_texels_under_the_cutoff_and_shows_what_is_behind() -> None:
    mask = flat(
        (1, 1, 1, 1),
        base_color_texture=texture_ref(_holes_texture()),
        alpha_mode="mask",
        alpha_cutoff=0.5,
    )
    behind = quad_model(flat((0, 0, 1, 1)), half=2.0, z=-1.0)
    front = Instance(model_of(quad_mesh(1.0), mask))
    sc = scene([Instance(behind), front], camera=front_camera(z=1.0, fov_y_deg=90.0))
    frame = render_frame(sc, size=(64, 64))
    # le quad de demi-côté 1 à z=0 (distance 1) remplit l'image ; cases de 8 px : (4, 4) rouge, (4, 12) trou
    assert tuple(frame.color[4, 4]) == (255, 0, 0, 255) and frame.depth[
        4, 4
    ] == pytest.approx(1.0, abs=1e-4)
    assert tuple(frame.color[4, 12]) == (
        0,
        0,
        255,
        255,
    )  # trou : on voit le quad bleu derrière
    assert frame.depth[4, 12] == pytest.approx(2.0, abs=1e-3)
    assert set(np.unique(frame.alpha)) == {1.0}
    only = render_frame(
        scene(front, camera=front_camera(z=1.0, fov_y_deg=90.0)), size=(64, 64)
    )
    assert only.alpha[4, 12] == 0.0 and np.isinf(
        only.depth[4, 12]
    )  # trou sans rien derrière : transparent
    assert 0.4 < only.alpha.mean() < 0.6


def test_alpha_mask_threshold_is_the_cutoff() -> None:
    tex = texture_ref([255, 255, 255, 120])  # alpha 0,47
    for cutoff, visible in ((0.4, True), (0.5, False)):
        mat = flat(
            (1, 1, 1, 1), base_color_texture=tex, alpha_mode="mask", alpha_cutoff=cutoff
        )
        frame = render_frame(
            scene(
                quad_model(mat, half=2.0), camera=front_camera(z=1.0, fov_y_deg=90.0)
            ),
            size=(8, 8),
        )
        assert bool(frame.alpha[4, 4]) is visible


def test_alpha_blend_composites_over_what_is_behind_and_over_transparency() -> None:
    cam = front_camera(z=1.0, fov_y_deg=90.0)
    glass = quad_model(flat((1, 0, 0, 0.5), alpha_mode="blend"), half=2.0, z=0.5)
    back = quad_model(flat((0, 0, 1, 1)), half=4.0, z=-1.0)
    frame = render_frame(scene([glass, back], camera=cam), size=(16, 16))
    # linéaire : 0,5 x rouge + 0,5 x bleu = (0,5, 0, 0,5) -> sRGB 188
    assert tuple(frame.color[8, 8]) == (188, 0, 188, 255)
    assert frame.depth[8, 8] == pytest.approx(
        2.0, abs=1e-3
    )  # la profondeur est celle de la surface opaque
    alone = render_frame(scene(glass, camera=cam), size=(16, 16))
    assert tuple(alone.color[8, 8]) == (
        255,
        0,
        0,
        128,
    )  # alpha droit : la couleur reste pleine, l'alpha vaut 0,5
    assert np.isinf(alone.depth[8, 8])  # une surface blend n'écrit pas la profondeur
    # couverture uniforme, y compris sur la diagonale partagée par les deux triangles (règle top-left)
    assert (alone.alpha == 0.5).all()


def test_alpha_blend_sorts_triangles_far_to_near_whatever_the_instance_order() -> None:
    cam = front_camera(z=1.0, fov_y_deg=90.0)
    red = Instance(
        quad_model(flat((1, 0, 0, 0.5), alpha_mode="blend"), half=2.0, z=0.5)
    )
    green = Instance(
        quad_model(flat((0, 1, 0, 0.5), alpha_mode="blend"), half=2.0, z=0.0)
    )
    a = render_frame(scene([red, green], camera=cam), size=(8, 8))
    b = render_frame(scene([green, red], camera=cam), size=(8, 8))
    assert a.color.tobytes() == b.color.tobytes()
    # le rouge est devant : rouge x 0,5 + (vert x 0,5) x 0,5 ; alpha = 0,5 + 0,5 x 0,5 = 0,75
    assert a.alpha[4, 4] == pytest.approx(0.75)
    lin = (srgb_to_lin(float(a.color[4, 4, 0])), srgb_to_lin(float(a.color[4, 4, 1])))
    assert lin[0] / lin[1] == pytest.approx(
        2.0, rel=0.05
    )  # (0,5 / 0,75) contre (0,25 / 0,75)


def test_blend_does_not_cover_opaque_objects_in_front() -> None:
    cam = front_camera(z=1.0, fov_y_deg=90.0)
    glass = quad_model(flat((1, 0, 0, 0.5), alpha_mode="blend"), half=2.0, z=-0.5)
    wall = quad_model(flat((0, 0, 1, 1)), half=2.0, z=0.5)  # devant le verre
    frame = render_frame(scene([glass, wall], camera=cam), size=(8, 8))
    assert tuple(frame.color[4, 4]) == (0, 0, 255, 255)


def test_translation_of_an_instance_moves_the_lit_point() -> None:
    inst = Instance(
        quad_model(flat((1, 1, 1, 1)), half=2.0), translation_matrix([0, 0, -1.0])
    )
    out = center_linear(
        scene(
            inst,
            [PointLight((0, 0, 1), intensity=1.0)],
            front_camera(z=2.0, fov_y_deg=4.0),
        )
    )
    assert out[0] == pytest.approx(
        0.97 / 4, abs=0.006
    )  # le quad est maintenant à 2 de la lampe
