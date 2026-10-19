"""Scènes et modèles 3D de test fabriqués par programme pour le rendu (aucune fixture binaire)."""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from kut3d import (
    AmbientLight,
    Camera,
    DirectionalLight,
    Instance,
    Scene,
)
from kut3d.mathutil import Array, translation_matrix
from kut3d.model import (
    AnimationChannel,
    AnimationClip,
    Material,
    Mesh,
    MorphTarget,
    Model,
    Node,
    Skeleton,
    TextureRef,
)
from kut3d.render.png import encode_png

WHITE_AMBIENT = AmbientLight(
    (1.0, 1.0, 1.0), 1.0
)  # ambiance seule : la couleur rendue = la couleur de base


def srgb_to_lin(v: float) -> float:
    c = v / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def lin_to_srgb255(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return 255.0 * (12.92 * x if x <= 0.0031308 else 1.055 * x ** (1 / 2.4) - 0.055)


def texture_ref(rgba: Any, name: str = "tex", **sampler: str) -> TextureRef:
    """Une texture PNG embarquée à partir d'un tableau (H, W, 4) uint8 (ou une couleur unie 1x1)."""
    arr = np.asarray(rgba, dtype=np.uint8)
    if arr.ndim == 1:
        arr = arr.reshape(1, 1, 4)
    return TextureRef(name=name, data=encode_png(arr), mime_type="image/png", **sampler)


def checker_rgba(
    size: int = 64,
    tiles: int = 8,
    a: tuple[int, int, int, int] = (235, 120, 40, 255),
    b: tuple[int, int, int, int] = (245, 235, 210, 255),
) -> Array:
    cell = size // tiles
    yy, xx = np.mgrid[0:size, 0:size]
    odd = ((xx // cell) + (yy // cell)) % 2 == 1
    out = np.empty((size, size, 4), np.uint8)
    out[...] = a
    out[odd] = b
    return out


def quad_mesh(
    half: float = 1.0,
    *,
    uvs: bool = True,
    material_index: int | None = 0,
    z: float = 0.0,
) -> Mesh:
    """Carré dans le plan XY, tourné vers +Z (sens trigonométrique), u vers +X, v vers le BAS (haut de l'image = +Y)."""
    pos = [[-half, -half, z], [half, -half, z], [half, half, z], [-half, half, z]]
    uv = [[0, 1], [1, 1], [1, 0], [0, 0]]
    return Mesh(
        positions=pos,
        indices=[[0, 1, 2], [0, 2, 3]],
        normals=[[0, 0, 1]] * 4,
        uvs=uv if uvs else None,
        material_index=material_index,
    )


def model_of(mesh: Mesh, material: Material | None = None) -> Model:
    return Model(
        nodes=(Node(mesh_index=0),),
        root_nodes=(0,),
        meshes=(mesh,),
        materials=() if material is None else (material,),
    )


def quad_model(
    material: Material | None = None,
    half: float = 1.0,
    z: float = 0.0,
    uvs: bool = True,
) -> Model:
    mesh = quad_mesh(half, uvs=uvs, material_index=None if material is None else 0, z=z)
    return model_of(mesh, material)


def flat(color: tuple[float, ...] = (1.0, 1.0, 1.0, 1.0), **kw: Any) -> Material:
    """Matériau mat (rugosité 1, non métallique) de couleur donnée."""
    kw.setdefault("roughness", 1.0)
    return Material(base_color=color, **kw)


def cube_model(material: Material | None = None) -> Model:
    """Cube de côté 2 (24 sommets, normales plates, UV 0..1 par face), sens trigonométrique vu de l'extérieur."""
    faces = (
        ((0, 0, 1), (1, 0, 0), (0, 1, 0)),
        ((0, 0, -1), (-1, 0, 0), (0, 1, 0)),
        ((1, 0, 0), (0, 0, -1), (0, 1, 0)),
        ((-1, 0, 0), (0, 0, 1), (0, 1, 0)),
        ((0, 1, 0), (1, 0, 0), (0, 0, -1)),
        ((0, -1, 0), (1, 0, 0), (0, 0, 1)),
    )
    pos: list[list[float]] = []
    uv: list[list[float]] = []
    nrm: list[list[float]] = []
    idx: list[list[int]] = []
    for f, (n, u, v) in enumerate(faces):
        n_a, u_a, v_a = (np.array(a, dtype=float) for a in (n, u, v))
        for su, sv in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
            pos.append((n_a + su * u_a + sv * v_a).tolist())
            uv.append([(su + 1) / 2, 1 - (sv + 1) / 2])
            nrm.append(n_a.tolist())
        b = 4 * f
        idx += [[b, b + 1, b + 2], [b, b + 2, b + 3]]
    mesh = Mesh(
        pos, idx, normals=nrm, uvs=uv, material_index=None if material is None else 0
    )
    return model_of(mesh, material)


def front_camera(z: float = 3.0, **kw: Any) -> Camera:
    """Caméra sur +Z qui regarde l'origine."""
    return Camera((0.0, 0.0, z), (0.0, 0.0, 0.0), **kw)


def scene(
    models_or_instances: Any,
    lights: Any = None,
    camera: Camera | None = None,
    background: Any = None,
) -> Scene:
    items = (
        models_or_instances
        if isinstance(models_or_instances, (list, tuple))
        else [models_or_instances]
    )
    insts = [i if isinstance(i, Instance) else Instance(i) for i in items]
    return Scene(
        insts,
        [WHITE_AMBIENT] if lights is None else lights,
        camera or front_camera(),
        background,
    )


def sun(
    direction: tuple[float, float, float] = (0, 0, -1), intensity: float = 1.0
) -> DirectionalLight:
    return DirectionalLight(direction, (1.0, 1.0, 1.0), intensity)


def rotation_z_quat(degrees: float) -> tuple[float, float, float, float]:
    h = math.radians(degrees) / 2
    return (0.0, 0.0, math.sin(h), math.cos(h))


# --- animation et peau -----------------------------------------------------------------------------------------


def animated_node_model(
    channels: list[AnimationChannel], duration: float = 2.0
) -> Model:
    """Un nœud sans maillage (pour tester les poses) et une clip « a »."""
    return Model(
        nodes=(Node(name="n"),),
        root_nodes=(0,),
        animations=(AnimationClip("a", duration, tuple(channels)),),
    )


def channel(
    path: str, times: Any, values: Any, interpolation: str = "linear", node: int = 0
) -> AnimationChannel:
    return AnimationChannel(
        node,
        path,
        np.array(times, np.float32),
        np.array(values, np.float32),
        interpolation,
    )


def skinned_model(clip_degrees: float = 90.0) -> Model:
    """Deux os en chaîne (os 0 à l'origine, os 1 en x=1) et 5 sommets :
    A(0,0,0) B(0,1,0) à 100 % de l'os 0 ; C(1,0,0) D(1,1,0) à 100 % de l'os 1 ; E(1,1,0) à 50 % / 50 %.
    La clip « bend » tourne l'os 1 de `clip_degrees` autour de Z entre t=0 et t=1. Le nœud du maillage est
    décalé de (5,0,0) : un maillage à peau doit l'ignorer."""
    pos = [[0, 0, 0], [0, 1, 0], [1, 0, 0], [1, 1, 0], [1, 1, 0]]
    joints = [[0, 0, 0, 0], [0, 0, 0, 0], [1, 0, 0, 0], [1, 0, 0, 0], [0, 1, 0, 0]]
    weights = [[1, 0, 0, 0], [1, 0, 0, 0], [1, 0, 0, 0], [1, 0, 0, 0], [0.5, 0.5, 0, 0]]
    mesh = Mesh(pos, [[0, 2, 3], [0, 3, 1], [3, 2, 4]], joints=joints, weights=weights)
    ibm = [np.eye(4), translation_matrix([-1, 0, 0])]
    skel = Skeleton(
        joint_names=("root", "tip"),
        parents=[-1, 0],
        inverse_bind_matrices=ibm,
        rest_translation=[[0, 0, 0], [1, 0, 0]],
        rest_rotation=[[0, 0, 0, 1]] * 2,
        rest_scale=[[1, 1, 1]] * 2,
        node_indices=[1, 2],
    )
    nodes = (
        Node(name="mesh", mesh_index=0, skin_index=0, translation=(5.0, 0.0, 0.0)),
        Node(name="root", children=(2,)),
        Node(name="tip", translation=(1.0, 0.0, 0.0)),
    )
    clip = AnimationClip(
        "bend",
        1.0,
        (
            channel(
                "rotation",
                [0, 1],
                [[0, 0, 0, 1], rotation_z_quat(clip_degrees)],
                node=2,
            ),
        ),
    )
    return Model(
        nodes=nodes,
        root_nodes=(0, 1),
        meshes=(mesh,),
        skeletons=(skel,),
        animations=(clip,),
    )


def morph_model(delta: tuple[float, float, float] = (0.0, 0.0, 1.0)) -> Model:
    """Un triangle avec une forme (écart `delta` sur chaque sommet) et une clip « m » qui fait passer son poids
    de 0 à 1 entre t=0 et t=2."""
    mesh = Mesh(
        [[0, 0, 0], [1, 0, 0], [0, 1, 0]],
        [[0, 1, 2]],
        morph_targets=(MorphTarget("s", [list(delta)] * 3),),
        morph_weights=(0.0,),
    )
    clip = AnimationClip("m", 2.0, (channel("weights", [0, 2], [[0.0], [1.0]]),))
    return Model(
        nodes=(Node(mesh_index=0),), root_nodes=(0,), meshes=(mesh,), animations=(clip,)
    )
