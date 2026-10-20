"""Scènes de comparaison qrhi / numpy (phase 2b) : fabriquées par programme, partagées par le test et le worker GPU.

`CASES[nom]` = `Case(build, t, size, samples)` ; `build()` retourne une `Scene`. Le worker (`kut3d_gpu_worker.py`)
rend chaque scène avec le moteur GPU dans un processus à plateforme Qt native ; le test la rend avec numpy et compare.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, replace

import numpy as np

from kut3d import (
    AmbientLight,
    Camera,
    DirectionalLight,
    Instance,
    PointLight,
    Scene,
)
from kut3d.mathutil import scale_matrix, translation_matrix


def diag(x: float, y: float, z: float) -> np.ndarray:
    return np.diag([x, y, z, 1.0])


from kut3d.model import (
    AnimationClip,
    Material,
    Mesh,
    Model,
    MorphTarget,
    Node,
    Skeleton,
)
from tests.kut3d_scenes import (
    WHITE_AMBIENT,
    channel,
    checker_rgba,
    cube_model,
    flat,
    front_camera,
    model_of,
    quad_mesh,
    quad_model,
    rotation_z_quat,
    scene,
    skinned_model,
    sun,
    texture_ref,
)

CAMERA = Camera((2.5, 2.0, 3.0), (0.0, 0.0, 0.0))
LIGHTS = [
    AmbientLight((1.0, 1.0, 1.0), 0.1),
    DirectionalLight((-0.4, -1.0, -0.6), intensity=2.0),
    PointLight((1.5, 1.5, 2.0), (1.0, 0.8, 0.6), 8.0),
]


@dataclass(frozen=True)
class Case:
    build: Callable[[], Scene]
    t: float = 0.0
    size: tuple[int, int] = (160, 120)
    samples: int = 1


def _bumpy_normal_map() -> np.ndarray:
    """Carte de normales à cases inclinées dans 4 directions (rouge = +X, vert = +Y vers le haut de l'image)."""
    tilts = [(0.6, 0.0), (-0.6, 0.0), (0.0, 0.6), (0.0, -0.6)]
    out = np.zeros((32, 32, 4), np.uint8)
    for j in range(4):
        for i in range(4):
            nx, ny = tilts[(i + 2 * j) % 4]
            nz = math.sqrt(1 - nx * nx - ny * ny)
            out[j * 8 : (j + 1) * 8, i * 8 : (i + 1) * 8] = [
                round((c * 0.5 + 0.5) * 255) for c in (nx, ny, nz)
            ] + [255]
    return out


def _gradient(channels: tuple[int, int, int]) -> np.ndarray:
    ramp = np.linspace(40, 255, 32).astype(np.uint8)
    img = np.zeros((32, 32, 4), np.uint8)
    img[..., 3] = 255
    for k, c in enumerate(channels):
        img[..., k] = ramp[None, :] if c == 0 else ramp[:, None] if c == 1 else 128
    return img


def checker() -> Scene:
    mat = Material(
        base_color=(1, 1, 1, 1),
        base_color_texture=texture_ref(checker_rgba()),
        roughness=1.0,
    )
    return scene(cube_model(mat), camera=CAMERA)


def pbr_lit() -> Scene:
    mat = Material(base_color=(0.9, 0.5, 0.2, 1), metallic=0.8, roughness=0.35)
    return scene(cube_model(mat), lights=LIGHTS, camera=CAMERA)


def pbr_dielectric() -> Scene:
    mat = Material(base_color=(0.2, 0.5, 0.9, 1), metallic=0.0, roughness=0.25)
    return scene(cube_model(mat), lights=LIGHTS, camera=CAMERA)


def normal_map() -> Scene:
    mat = flat((1, 1, 1, 1), normal_texture=texture_ref(_bumpy_normal_map()))
    return scene(
        quad_model(mat, half=1.5),
        lights=[AmbientLight((1, 1, 1), 0.05), sun((-1, -1, -1), 1.5)],
        camera=front_camera(3.0),
    )


def normal_map_cube() -> Scene:
    mat = flat(
        (0.8, 0.8, 0.8, 1),
        normal_texture=texture_ref(_bumpy_normal_map()),
        base_color_texture=texture_ref(checker_rgba()),
    )
    return scene(cube_model(mat), lights=LIGHTS, camera=CAMERA)


def maps_mr_emissive_occlusion() -> Scene:
    mat = Material(
        base_color=(1, 1, 1, 1),
        metallic=1.0,
        roughness=1.0,
        metallic_roughness_texture=texture_ref(_gradient((2, 0, 1))),
        emissive=(0.6, 0.3, 0.1),
        emissive_texture=texture_ref(_gradient((0, 2, 2))),
        occlusion_texture=texture_ref(_gradient((1, 2, 2))),
        base_color_texture=texture_ref(checker_rgba()),
    )
    return scene(
        quad_model(mat, half=1.5),
        lights=[AmbientLight((1, 1, 1), 0.5), sun((0.3, -0.4, -1), 1.0)],
        camera=front_camera(3.0),
    )


def skinned_with_normals(degrees: float = 90.0) -> Model:
    """`skinned_model` avec des normales (sans elles, le maillage à peau est déformé côté CPU : voir `_defer_skin`)."""
    model = skinned_model(degrees)
    return replace(model, meshes=(replace(model.meshes[0], normals=[[0, 0, 1]] * 5),))


def skin_ambient() -> Scene:
    return Scene(
        [
            Instance(
                skinned_with_normals(90.0),
                translation_matrix([-0.5, -0.5, 0.0]),
                clip="bend",
            )
        ],
        [WHITE_AMBIENT],
        front_camera(z=3.0, fov_y_deg=60.0),
    )


def skin_lit() -> Scene:
    model = skinned_with_normals(60.0)
    mat = Material(base_color=(0.9, 0.6, 0.3, 1), roughness=0.5, double_sided=True)
    mesh = replace(model.meshes[0], material_index=0)
    model = replace(model, meshes=(mesh,), materials=(mat,))
    inst = Instance(
        model, translation_matrix([-0.5, -0.5, 0.0]) @ scale_matrix(1.2), clip="bend"
    )
    return Scene(
        [inst],
        [AmbientLight((1, 1, 1), 0.1), sun((-0.3, -0.5, -1), 2.0)],
        front_camera(z=3.0, fov_y_deg=60.0),
    )


def skin_mirrored() -> Scene:
    inst = Instance(
        skinned_with_normals(90.0),
        diag(-1.0, 1.0, 1.0) @ translation_matrix([-0.5, -0.5, 0.0]),
        clip="bend",
    )
    return Scene([inst], [WHITE_AMBIENT], front_camera(z=3.0, fov_y_deg=60.0))


def skin_without_normals() -> Scene:
    """Maillage à peau sans normales : repli CPU (identique au moteur numpy), éclairé."""
    model = skinned_model(60.0)
    mat = Material(base_color=(0.9, 0.6, 0.3, 1), roughness=0.5, double_sided=True)
    model = replace(
        model, meshes=(replace(model.meshes[0], material_index=0),), materials=(mat,)
    )
    inst = Instance(model, translation_matrix([-0.5, -0.5, 0.0]), clip="bend")
    return Scene(
        [inst],
        [AmbientLight((1, 1, 1), 0.1), sun((-0.3, -0.5, -1), 2.0)],
        front_camera(z=3.0, fov_y_deg=60.0),
    )


def morph() -> Scene:
    mesh = Mesh(
        [[0, 0, 0], [1, 0, 0], [0, 1, 0]],
        [[0, 1, 2]],
        morph_targets=(
            MorphTarget("s", [[0.5, 0.2, 0.0], [0.0, 0.5, 0.0], [-0.3, 0.3, 0.0]]),
        ),
        morph_weights=(0.0,),
        material_index=0,
    )
    clip = AnimationClip("m", 2.0, (channel("weights", [0, 2], [[0.0], [1.0]]),))
    model = Model(
        nodes=(Node(mesh_index=0),),
        root_nodes=(0,),
        meshes=(mesh,),
        animations=(clip,),
        materials=(flat((1, 0.6, 0.2, 1), double_sided=True),),
    )
    return Scene(
        [Instance(model, translation_matrix([-0.4, -0.4, 0.0]), clip="m")],
        [WHITE_AMBIENT],
        front_camera(3.0),
    )


def morph_then_skin() -> Scene:
    mesh = Mesh(
        [[0, 0, 0], [1, 0, 0], [0, 1, 0]],
        [[0, 1, 2]],
        normals=[[0, 0, 1]] * 3,
        joints=[[0, 0, 0, 0]] * 3,
        weights=[[1, 0, 0, 0]] * 3,
        morph_targets=(MorphTarget("dx", [[1, 0, 0]] * 3),),
        morph_weights=(1.0,),
        material_index=0,
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
        materials=(flat((0.3, 0.8, 1, 1), double_sided=True),),
    )
    return Scene(
        [Instance(model, clip="turn")],
        [WHITE_AMBIENT],
        Camera((0, 0.5, 4), (0, 0.5, 0)),
    )


def _holes() -> np.ndarray:
    return checker_rgba(64, 8, (255, 0, 0, 255), (0, 255, 0, 0))


def mask() -> Scene:
    m = flat(
        (1, 1, 1, 1),
        base_color_texture=texture_ref(_holes()),
        alpha_mode="mask",
        alpha_cutoff=0.5,
    )
    behind = Instance(quad_model(flat((0, 0, 1, 1)), half=2.0, z=-1.0))
    return scene(
        [behind, Instance(model_of(quad_mesh(1.0), m))],
        camera=front_camera(z=1.0, fov_y_deg=90.0),
    )


def blend_sorted() -> Scene:
    """Verre rouge devant, vert derrière, instances dans l'ordre INVERSE du tri (le tri CPU doit les remettre)."""
    red = Instance(
        quad_model(flat((1, 0, 0, 0.5), alpha_mode="blend"), half=2.0, z=0.5)
    )
    green = Instance(
        quad_model(flat((0, 1, 0, 0.5), alpha_mode="blend"), half=2.0, z=0.0)
    )
    wall = Instance(quad_model(flat((0, 0, 1, 1)), half=4.0, z=-1.0))
    return scene([red, green, wall], camera=front_camera(z=1.0, fov_y_deg=90.0))


def blend_behind_opaque() -> Scene:
    glass = Instance(
        quad_model(flat((1, 0, 0, 0.5), alpha_mode="blend"), half=2.0, z=-0.5)
    )
    wall = Instance(quad_model(flat((0, 0, 1, 1)), half=1.0, z=0.5))
    return scene([glass, wall], camera=front_camera(z=1.0, fov_y_deg=90.0))


def background_opaque() -> Scene:
    return Scene(
        [Instance(cube_model(flat((0.9, 0.2, 0.2, 1))))],
        LIGHTS,
        CAMERA,
        (0.1, 0.3, 0.6, 1.0),
    )


def background_translucent() -> Scene:
    return Scene(
        [Instance(cube_model(flat((0.9, 0.2, 0.2, 1))))],
        LIGHTS,
        CAMERA,
        (0.9, 0.9, 0.2, 0.4),
    )


def double_sided_and_culled() -> Scene:
    one = Instance(
        quad_model(flat((1, 0, 0, 1)), half=0.8),
        translation_matrix([-1.0, 0, 0]) @ rotation_y(180.0),
    )
    two = Instance(
        quad_model(flat((0, 1, 0, 1), double_sided=True), half=0.8),
        translation_matrix([1.0, 0, 0]) @ rotation_y(180.0),
    )
    return scene(
        [one, two],
        lights=[sun((0, 0, -1), 1.0), WHITE_AMBIENT],
        camera=front_camera(4.0),
    )


def rotation_y(deg: float) -> np.ndarray:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    m = np.eye(4)
    m[0, 0], m[0, 2], m[2, 0], m[2, 2] = c, s, -s, c
    return m


def mirrored() -> Scene:
    inst = Instance(cube_model(flat((0.8, 0.4, 0.2, 1))), diag(-1.0, 1.0, 1.0))
    return Scene([inst], LIGHTS, CAMERA)


def vertex_colors() -> Scene:
    mesh = replace(
        quad_mesh(1.0, material_index=0),
        colors=[[1, 0, 0, 1], [0, 1, 0, 1], [0, 0, 1, 1], [1, 1, 0, 1]],
    )
    return scene(model_of(mesh, flat((1, 1, 1, 1))), camera=front_camera(3.0))


def no_normals() -> Scene:
    mesh = Mesh([[-1, -1, 0], [1, -1, 0], [0, 1, 0]], [[0, 1, 2]], material_index=0)
    return Scene(
        [Instance(model_of(mesh, flat((0.7, 0.7, 0.9, 1), double_sided=True)))],
        LIGHTS,
        front_camera(3.0),
    )


def supersampled_cube() -> Scene:
    return checker()


def supersampled_alpha() -> Scene:
    return Scene([Instance(cube_model(flat((0.9, 0.5, 0.2, 1))))], LIGHTS, CAMERA)


def depth_two_layers() -> Scene:
    a = Instance(quad_model(flat((1, 0, 0, 1)), half=1.0, z=0.0))
    b = Instance(quad_model(flat((0, 0, 1, 1)), half=2.0, z=-2.0))
    return scene([b, a], camera=front_camera(z=4.0, fov_y_deg=50.0))


def near_clipped() -> Scene:
    floor = Mesh(
        [[-3, -1, 2], [3, -1, 2], [3, -1, -8], [-3, -1, -8]],
        [[0, 1, 2], [0, 2, 3]],
        normals=[[0, 1, 0]] * 4,
        material_index=0,
    )
    return Scene(
        [Instance(model_of(floor, flat((0.6, 0.9, 0.6, 1), double_sided=True)))],
        [AmbientLight((1, 1, 1), 0.3), sun((0, -1, -0.3), 1.0)],
        Camera((0, 0.5, 1.0), (0, 0, -3), fov_y_deg=60.0),
    )


def terrain_20k() -> Scene:
    """Grille de 100 x 100 carrés (20 000 triangles) texturée et éclairée : la scène de mesure de vitesse."""
    n = 100
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
    material = flat((1, 1, 1, 1), base_color_texture=texture_ref(checker_rgba(128, 16)))
    lights = [sun((-0.3, -1, -0.5), 1.2), WHITE_AMBIENT]
    return Scene(
        [Instance(model_of(mesh, material))], lights, Camera((0, 1.2, 2.2), (0, 0, 0))
    )


def _sampler_plane(**sampler: str) -> Scene:
    """Plan dont les UV sortent de [0, 1] (de -0.5 à 1.5), texture 4 x 4 de couleurs franches, agrandie."""
    mesh = quad_mesh(1.0, material_index=0)
    mesh = replace(mesh, uvs=np.array(mesh.uvs) * 2.0 - 0.5)
    rng = np.random.default_rng(3)
    img = np.empty((4, 4, 4), np.uint8)
    img[..., :3] = rng.integers(20, 255, (4, 4, 3))
    img[..., 3] = 255
    mat = flat((1, 1, 1, 1), base_color_texture=texture_ref(img, **sampler))
    return scene(model_of(mesh, mat), camera=front_camera(3.0))


def sampler_repeat_nearest() -> Scene:
    return _sampler_plane(mag_filter="nearest", min_filter="nearest")


def sampler_clamp() -> Scene:
    return _sampler_plane(wrap_s="clamp", wrap_t="clamp")


def sampler_mirror() -> Scene:
    return _sampler_plane(wrap_s="mirror", wrap_t="mirror")


def sampler_mixed_nearest() -> Scene:
    return _sampler_plane(
        wrap_s="mirror", wrap_t="clamp", mag_filter="nearest", min_filter="nearest"
    )


def primitives() -> Scene:
    """Les six formes de base (damier, matériaux à une face sauf le plan) : normales, UV, sens des triangles, couture."""
    from tests.kut3d_primitive_scenes import six_forms

    return six_forms(checker=True)


CASES: dict[str, Case] = {
    "checker": Case(checker),
    "pbr_lit": Case(pbr_lit),
    "pbr_dielectric": Case(pbr_dielectric),
    "normal_map": Case(normal_map),
    "normal_map_cube": Case(normal_map_cube),
    "maps_mr_emissive_occlusion": Case(maps_mr_emissive_occlusion),
    "skin_ambient": Case(skin_ambient, t=1.0, size=(96, 96)),
    "skin_lit": Case(skin_lit, t=1.0, size=(96, 96)),
    "skin_without_normals": Case(skin_without_normals, t=1.0, size=(96, 96)),
    "skin_rest": Case(skin_ambient, t=0.0, size=(96, 96)),
    "skin_mirrored": Case(skin_mirrored, t=1.0, size=(96, 96)),
    "morph": Case(morph, t=1.0, size=(96, 96)),
    "morph_then_skin": Case(morph_then_skin, t=1.0, size=(96, 96)),
    "mask": Case(mask, size=(64, 64)),
    "blend_sorted": Case(blend_sorted, size=(64, 64)),
    "blend_behind_opaque": Case(blend_behind_opaque, size=(64, 64)),
    "background_opaque": Case(background_opaque),
    "background_translucent": Case(background_translucent),
    "double_sided_and_culled": Case(double_sided_and_culled, size=(128, 64)),
    "mirrored": Case(mirrored),
    "vertex_colors": Case(vertex_colors, size=(64, 64)),
    "no_normals": Case(no_normals, size=(64, 64)),
    "supersampled_cube": Case(supersampled_cube, samples=2),
    "supersampled_alpha4": Case(supersampled_alpha, size=(80, 60), samples=4),
    "depth_two_layers": Case(depth_two_layers, size=(64, 64)),
    "near_clipped": Case(near_clipped, size=(96, 72)),
    "terrain_20k": Case(terrain_20k, size=(1280, 720)),
    "sampler_repeat_nearest": Case(sampler_repeat_nearest, size=(96, 96)),
    "sampler_clamp": Case(sampler_clamp, size=(96, 96)),
    "sampler_mirror": Case(sampler_mirror, size=(96, 96)),
    "sampler_mixed_nearest": Case(sampler_mixed_nearest, size=(96, 96)),
    "primitives": Case(primitives, size=(240, 160)),
}
