"""Du modèle assemblé aux tampons GPU (numpy pur, SANS Qt : testable partout).

`plan_frame` transforme une scène en : un tableau de sommets entrelacés, un tableau d'indices ordonné (triangles
opaques ou masqués regroupés par matériau et par dessin à peau, puis triangles `blend` triés du plus loin au plus
proche, comme le moteur numpy), la liste des `Run` (un appel de dessin chacun : bloc d'uniformes, textures,
articulations) et les blocs d'uniformes de l'image (caméra, lumières). La mise en page des blocs est celle `std140`
de `shaders/kut3d.vert` et `kut3d.frag`.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from kut3d.errors import RenderError
from kut3d.gpu.limits import MAX_GPU_JOINTS, MAX_GPU_LIGHTS
from kut3d.mathutil import Array
from kut3d.model import DEFAULT_SAMPLER, SamplerKey
from kut3d.render.assemble import Geometry, SkinDraw, assemble
from kut3d.render.limits import MAX_BLEND_TRIANGLES
from kut3d.render.materials import MODE_BLEND, PreparedMaterial
from kut3d.render.numpy_backend import tri_modes
from kut3d.scene import AmbientLight, DirectionalLight, PointLight, Scene

VERTEX_FLOATS = 24  # pos3 nrm3 uv2 col4 tan3 sign1 joints4 weights4
VERTEX_STRIDE = VERTEX_FLOATS * 4
DRAW_BLOCK_BYTES = 208
JOINT_BLOCK_BYTES = MAX_GPU_JOINTS * 64
FRAME_BLOCK_BYTES = 112 + 2 * 16 * 16
TEXTURE_SLOTS = ("base", "normal", "mr", "emissive", "occlusion")


@dataclass(frozen=True, eq=False)
class Run:
    """Un appel de dessin : `count` indices à partir de `first`, avec son matériau et son éventuelle peau."""

    first: int
    count: int
    blend: bool  # alpha-blending (mélange prémultiplié, sans écriture de profondeur)
    cull_back: bool  # faces arrière éliminées (matériau non double face)
    draw_block: bytes  # DRAW_BLOCK_BYTES
    textures: tuple[
        Array | None, ...
    ]  # (base, normal, mr, emissive, occlusion), tableaux RGBA 8 bits
    joints: bytes | None  # JOINT_BLOCK_BYTES (dessin à peau déformé par le GPU) ou None
    samplers: tuple[SamplerKey, ...] = (
        DEFAULT_SAMPLER,
    ) * 5  # réglage d'échantillonnage de chaque texture (wrap_s, wrap_t, mag, min)


@dataclass(frozen=True, eq=False)
class FramePlan:
    vertices: Array  # (V, 24) float32
    indices: Array  # (N,) uint32
    runs: tuple[Run, ...]
    view_proj: Array  # (4, 4) float64 : projection @ vue, SANS la correction d'espace de découpe de QRhi
    eye: Array
    ambient: Array
    light_a: Array  # (16, 4) float32
    light_b: Array  # (16, 4) float32
    light_count: int
    size: tuple[int, int]
    skinned_on_gpu: int  # nombre de dessins déformés dans le vertex shader (diagnostic)

    def frame_block(self, clip_correction: Array) -> bytes:
        """Bloc d'uniformes de l'image ; `clip_correction` = `QRhi.clipSpaceCorrMatrix()` (4x4)."""
        vp = np.asarray(clip_correction, dtype=np.float64) @ self.view_proj
        head = np.zeros((3, 4), dtype=np.float32)  # eye, ambient, counts
        head[0, :3] = self.eye
        head[1, :3] = self.ambient
        head[2, 0] = float(self.light_count)
        return (
            np.ascontiguousarray(vp.T, dtype=np.float32).tobytes()
            + head.tobytes()
            + np.ascontiguousarray(self.light_a, dtype=np.float32).tobytes()
            + np.ascontiguousarray(self.light_b, dtype=np.float32).tobytes()
        )


def _mat4_bytes(m: Array) -> bytes:
    return np.ascontiguousarray(
        np.asarray(m, dtype=np.float64).T, dtype=np.float32
    ).tobytes()


def _nmat4(skin: SkinDraw | None) -> Array:
    out = np.eye(4)
    if skin is not None:
        out[:3, :3] = skin.normal_matrix
    return out


def draw_block(mat: PreparedMaterial, skin: SkinDraw | None) -> bytes:
    """Bloc `DrawBlock` : modèle, matrice des normales, facteurs du matériau et présence des textures."""
    model = np.eye(4) if skin is None else skin.model
    scalars = np.zeros((5, 4), dtype=np.float32)
    scalars[0] = mat.base
    scalars[1, :3] = mat.emissive
    scalars[2] = (mat.metallic, mat.roughness, mat.cutoff, float(mat.mode))
    scalars[3] = [
        mat.base_tex is not None,
        mat.normal_tex is not None,
        mat.mr_tex is not None,
        mat.emissive_tex is not None,
    ]
    scalars[4] = (mat.occlusion_tex is not None, skin is not None, 0.0, 0.0)
    data = _mat4_bytes(model) + _mat4_bytes(_nmat4(skin)) + scalars.tobytes()
    assert len(data) == DRAW_BLOCK_BYTES
    return data


def joint_block(skin: SkinDraw) -> bytes:
    """Bloc `JointBlock` : les matrices d'articulation (+ identité), complétées par l'identité jusqu'à 128."""
    pal = np.broadcast_to(np.eye(4), (MAX_GPU_JOINTS, 4, 4)).copy()
    pal[: skin.joints.shape[0]] = skin.joints
    return np.ascontiguousarray(pal.transpose(0, 2, 1), dtype=np.float32).tobytes()


def _vertex_array(geo: Geometry) -> Array:
    v = geo.pos.shape[0]
    out = np.empty((v, VERTEX_FLOATS), dtype=np.float32)
    out[:, 0:3] = geo.pos
    out[:, 3:6] = geo.nrm
    out[:, 6:8] = geo.uv
    out[:, 8:12] = geo.col
    out[:, 12:15] = geo.tan
    out[:, 15] = geo.tan_sign
    assert geo.joints is not None and geo.joint_weights is not None
    out[:, 16:20] = geo.joints
    out[:, 20:24] = geo.joint_weights
    return out


def _lights(scene: Scene) -> tuple[Array, Array, Array, int]:
    ambient = np.zeros(3)
    a = np.zeros((MAX_GPU_LIGHTS, 4), dtype=np.float32)
    b = np.zeros((MAX_GPU_LIGHTS, 4), dtype=np.float32)
    n = 0
    directional = [x for x in scene.lights if isinstance(x, DirectionalLight)]
    points = [x for x in scene.lights if isinstance(x, PointLight)]
    for light in scene.lights:
        if isinstance(light, AmbientLight):
            ambient += np.asarray(light.color) * light.intensity
    for d in directional:
        a[n, :3] = -np.asarray(d.direction)
        b[n, :3] = np.asarray(d.color) * d.intensity
        b[n, 3] = -1.0
        n += 1
    for p in points:
        a[n, :3] = p.position
        a[n, 3] = 1.0
        b[n, :3] = np.asarray(p.color) * p.intensity
        b[n, 3] = -1.0 if p.range is None else p.range
        n += 1
    return ambient, a, b, n


def _runs(
    geo: Geometry, order: Array, per_draw: bool
) -> list[tuple[int, int, int, int]]:
    """Segments consécutifs de `order` (indices de triangles) de même matériau (et même dessin si `per_draw`).

    Retourne (début, fin, matériau, dessin) ; `dessin` vaut -1 hors dessin à peau déformé par le GPU.
    """
    if order.size == 0:
        return []
    mat = geo.draw_mat[geo.tri_draw[order]].astype(np.int64)
    draw = geo.tri_draw[order].astype(np.int64)
    skinned = np.array([s is not None for s in geo.draw_skin], dtype=bool)
    sk = skinned[draw] if per_draw else np.zeros(order.size, dtype=bool)
    key = mat * (len(geo.draw_skin) + 1) + np.where(sk, draw + 1, 0)
    cuts = np.flatnonzero(np.diff(key)) + 1
    starts = np.concatenate([[0], cuts])
    ends = np.concatenate([cuts, [order.size]])
    return [
        (int(s), int(e), int(mat[s]), int(draw[s]) if sk[s] else -1)
        for s, e in zip(starts, ends, strict=True)
    ]


def plan_frame(scene: Scene, t: float, size: tuple[int, int]) -> FramePlan:
    """Plan de dessin de `scene` à l'instant `t` pour une image de `size` pixels (déjà sur-échantillonnée)."""
    geo = assemble(scene, t, gpu_skin_limit=MAX_GPU_JOINTS)
    cam = scene.camera
    width, height = size
    view_proj = cam.projection_matrix(width / height) @ cam.view_matrix()
    ambient, light_a, light_b, n_lights = _lights(scene)
    runs: list[Run] = []
    if geo.triangle_count == 0:
        empty_v = np.zeros((0, VERTEX_FLOATS), dtype=np.float32)
        return FramePlan(
            empty_v, np.zeros(0, np.uint32), (), view_proj, np.asarray(cam.position),
            ambient, light_a, light_b, n_lights, size, 0,
        )  # fmt: skip
    mode, double = tri_modes(geo)
    is_blend = mode == MODE_BLEND
    if int(is_blend.sum()) > MAX_BLEND_TRIANGLES:
        raise RenderError(
            f"trop de triangles en alpha-blending (maximum {MAX_BLEND_TRIANGLES})."
        )
    solid = np.flatnonzero(~is_blend)
    if solid.size:
        draw_of = geo.tri_draw[solid].astype(np.int64)
        skinned = np.array([s is not None for s in geo.draw_skin], dtype=bool)
        mat = geo.draw_mat[geo.tri_draw[solid]].astype(np.int64)
        key = mat * (len(geo.draw_skin) + 1) + np.where(
            skinned[draw_of], draw_of + 1, 0
        )
        solid = solid[np.argsort(key, kind="stable")]
    blended = np.flatnonzero(is_blend)
    if blended.size:
        homo = np.concatenate([geo.pos, np.ones((geo.pos.shape[0], 1))], axis=1)
        w = (
            homo @ view_proj[3]
        )  # profondeur de vue de chaque sommet (blend : jamais déformé par le GPU)
        mean_w = w[geo.tri[blended]].mean(axis=1)
        blended = blended[np.argsort(-mean_w, kind="stable")]
    order = np.concatenate([solid, blended])
    indices = geo.tri[order].reshape(-1).astype(np.uint32)
    skin_count = 0
    for is_blend_run, sub, base in ((False, solid, 0), (True, blended, solid.size)):
        for start, end, mat_i, draw_i in _runs(geo, sub, not is_blend_run):
            material = geo.materials[mat_i]
            skin = geo.draw_skin[draw_i] if draw_i >= 0 else None
            if skin is not None:
                skin_count += 1
            runs.append(
                Run(
                    first=(base + start) * 3,
                    count=(end - start) * 3,
                    blend=is_blend_run,
                    cull_back=not material.double_sided,
                    draw_block=draw_block(material, skin),
                    textures=(
                        material.base_tex,
                        material.normal_tex,
                        material.mr_tex,
                        material.emissive_tex,
                        material.occlusion_tex,
                    ),
                    joints=None if skin is None else joint_block(skin),
                    samplers=material.samplers,
                )
            )
    return FramePlan(
        _vertex_array(geo), indices, tuple(runs), view_proj, np.asarray(cam.position),
        ambient, light_a, light_b, n_lights, size, skin_count,
    )  # fmt: skip


__all__ = ["FramePlan", "Run", "plan_frame"]
