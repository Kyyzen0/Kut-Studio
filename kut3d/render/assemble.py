"""Assemblage de la scène : sommets déformés, en espace monde, concaténés pour toutes les instances.

Ordre des déformations, comme glTF : (1) formes de visage (morph targets) sur les sommets de repos, (2)
déformation par os (4 influences, interpolation linéaire des matrices d'articulation), (3) transformation
monde = `Instance.transform @ Model.root_transform @ monde du nœud`. Un maillage à peau ignore la matrice de son
propre nœud (spec glTF) : seuls les os le placent. Un déterminant négatif (miroir) inverse l'ordre des sommets
des triangles, comme glTF.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from kut3d.animation import Pose, evaluate_pose
from kut3d.errors import RenderError
from kut3d.geometry import smooth_normals
from kut3d.mathutil import Array
from kut3d.model import Mesh, Model
from kut3d.render.limits import MAX_TRIANGLES
from kut3d.render.materials import PreparedMaterial, prepare_material
from kut3d.scene import Instance, Scene

MAX_VERTICES = 6_000_000


@dataclass(frozen=True, eq=False)
class Geometry:
    """Toute la scène en espace monde. `tri` indexe `pos` ; `tri_draw` dit de quel dessin vient chaque triangle."""

    pos: Array  # (V, 3) float64
    nrm: Array  # (V, 3) float64 (unitaires, ou nuls si dégénérés)
    uv: Array  # (V, 2) float64
    col: Array  # (V, 4) float32
    tan: Array  # (V, 3) float64 (nuls si le matériau n'a pas de texture de normales)
    tan_sign: Array  # (V,) float64, ±1
    tri: Array  # (T, 3) int64
    tri_draw: Array  # (T,) int32
    draw_mat: Array  # (D,) int32 : indice dans `materials`
    materials: tuple[PreparedMaterial, ...]

    @property
    def triangle_count(self) -> int:
        return int(self.tri.shape[0])


def _normalize_rows(v: Array) -> Array:
    n = np.linalg.norm(v, axis=1, keepdims=True)
    return np.asarray(np.where(n > 1e-20, v / np.where(n > 1e-20, n, 1.0), 0.0))


def compute_tangents(
    pos: Array, nrm: Array, uv: Array, tri: Array
) -> tuple[Array, Array]:
    """Tangentes par sommet à partir des UV (glTF : V vers le bas ; +Y de la texture de normales = vers le haut
    de l'image). Retourne (tangente (V, 3), signe de la bitangente (V,))."""
    p = pos[tri]
    t = uv[tri]
    e1, e2 = p[:, 1] - p[:, 0], p[:, 2] - p[:, 0]
    du1, dv1 = t[:, 1, 0] - t[:, 0, 0], t[:, 1, 1] - t[:, 0, 1]
    du2, dv2 = t[:, 2, 0] - t[:, 0, 0], t[:, 2, 1] - t[:, 0, 1]
    det = du1 * dv2 - du2 * dv1
    r = np.where(np.abs(det) > 1e-20, 1.0 / np.where(det == 0, 1.0, det), 0.0)
    tan_f = (e1 * dv2[:, None] - e2 * dv1[:, None]) * r[:, None]
    up_f = -(
        (e2 * du1[:, None] - e1 * du2[:, None]) * r[:, None]
    )  # bitangente « vers le haut de l'image »
    n = pos.shape[0]
    acc_t = np.zeros((n, 3))
    acc_b = np.zeros((n, 3))
    for corner in range(3):
        for axis in range(3):
            acc_t[:, axis] += np.bincount(
                tri[:, corner], weights=tan_f[:, axis], minlength=n
            )
            acc_b[:, axis] += np.bincount(
                tri[:, corner], weights=up_f[:, axis], minlength=n
            )
    tt = acc_t - nrm * np.sum(nrm * acc_t, axis=1, keepdims=True)
    length = np.linalg.norm(tt, axis=1)
    weak = length < 1e-12
    if (
        weak.any()
    ):  # UV dégénérées : n'importe quelle tangente perpendiculaire à la normale
        axis_ = np.where(np.abs(nrm[:, [0]]) < 0.9, [[1.0, 0, 0]], [[0, 1.0, 0]])
        tt = np.where(weak[:, None], np.cross(nrm, axis_), tt)
        length = np.linalg.norm(tt, axis=1)
    tt = tt / np.where(length > 1e-20, length, 1.0)[:, None]
    sign = np.sign(np.sum(np.cross(nrm, tt) * acc_b, axis=1))
    return tt, np.where(sign == 0, 1.0, sign)


def _morph(
    mesh: Mesh, weights: Array, pos: Array, nrm: Array | None
) -> tuple[Array, Array | None]:
    for k, w in enumerate(weights):
        if w == 0.0:
            continue
        target = mesh.morph_targets[k]
        pos = pos + w * target.position_deltas
        if nrm is not None and target.normal_deltas is not None:
            nrm = nrm + w * target.normal_deltas
    return pos, nrm


def _skin(
    mesh: Mesh, joints_m: Array, pos: Array, nrm: Array | None
) -> tuple[Array, Array | None]:
    assert mesh.joints is not None and mesh.weights is not None
    jn = mesh.joints.astype(np.int64)
    if jn.shape[0] and int(np.max(jn)) >= joints_m.shape[0]:
        raise RenderError("maillage à peau : un sommet référence un os inexistant.")
    w = mesh.weights.astype(np.float64)
    wsum = w.sum(axis=1)
    good = wsum > 1e-8
    w = np.where(good[:, None], w / np.where(good, wsum, 1.0)[:, None], 0.0)
    m = np.zeros((jn.shape[0], 3, 4))
    for k in range(4):
        m += w[:, k, None, None] * joints_m[jn[:, k], :3, :]
    ident = np.hstack([np.eye(3), np.zeros((3, 1))])
    m[~good] = ident  # poids tous nuls : le sommet reste en pose de liaison
    out_pos = np.einsum("vij,vj->vi", m[:, :, :3], pos) + m[:, :, 3]
    out_nrm = None if nrm is None else np.einsum("vij,vj->vi", m[:, :, :3], nrm)
    return out_pos, out_nrm


def _jobs(scene: Scene, t: float) -> list[tuple[Instance, Model, Pose, list[int]]]:
    jobs = []
    triangles = vertices = 0
    rest: dict[int, Pose] = {}
    for inst in scene.instances:
        model = inst.model
        clip = inst.resolve_clip()
        if clip is None:
            pose = rest.get(id(model))
            if pose is None:
                pose = rest[id(model)] = evaluate_pose(model, None, 0.0)
        else:
            pose = evaluate_pose(model, clip, t + inst.time_offset, loop=inst.loop)
        nodes = [
            i
            for i, n in enumerate(model.nodes)
            if pose.reached[i]
            and n.mesh_index is not None
            and model.meshes[n.mesh_index].triangle_count > 0
        ]
        for i in nodes:
            mesh = model.meshes[model.nodes[i].mesh_index or 0]
            triangles += mesh.triangle_count
            vertices += mesh.vertex_count
            if triangles > MAX_TRIANGLES or vertices > MAX_VERTICES:
                raise RenderError(
                    f"scène trop lourde : plus de {MAX_TRIANGLES} triangles ou {MAX_VERTICES} sommets."
                )
        jobs.append((inst, model, pose, nodes))
    return jobs


def assemble(scene: Scene, t: float) -> Geometry:
    """Sommets déformés en espace monde pour toutes les instances, à l'instant `t`."""
    jobs = _jobs(scene, t)
    pos_l: list[Array] = []
    nrm_l: list[Array] = []
    uv_l: list[Array] = []
    col_l: list[Array] = []
    tan_l: list[Array] = []
    sign_l: list[Array] = []
    tri_l: list[Array] = []
    draw_l: list[Array] = []
    draw_mat: list[int] = []
    mats: list[PreparedMaterial] = []
    mat_index: dict[tuple[int, bool], int] = {}
    offset = 0
    for inst, model, pose, nodes in jobs:
        for i in nodes:
            node = model.nodes[i]
            mesh = model.meshes[node.mesh_index or 0]
            n = mesh.vertex_count
            pos = mesh.positions.astype(np.float64)
            nrm = None if mesh.normals is None else mesh.normals.astype(np.float64)
            weights = pose.morph_weights(i, mesh)
            if mesh.morph_targets and weights.any():
                pos, nrm = _morph(mesh, weights, pos, nrm)
            skinned = node.skin_index is not None and mesh.joints is not None
            if skinned:
                assert node.skin_index is not None
                pos, nrm = _skin(mesh, pose.joint_matrices(node.skin_index), pos, nrm)
            world = np.asarray(inst.transform) @ np.asarray(model.root_transform)
            if not skinned:
                world = world @ pose.world[i]
            lin = world[:3, :3]
            if abs(np.linalg.det(lin)) < 1e-18:
                continue  # échelle nulle : le dessin est invisible
            pos_w = pos @ lin.T + world[:3, 3]
            tri = mesh.indices.astype(np.int64)
            if np.linalg.det(lin) < 0:
                tri = tri[:, [0, 2, 1]]
            if nrm is None:
                nrm_w = smooth_normals(pos_w, tri).astype(np.float64)
            else:
                nrm_w = _normalize_rows(nrm @ np.linalg.inv(lin))
            has_uv = mesh.uvs is not None
            uv = (
                mesh.uvs.astype(np.float64)
                if mesh.uvs is not None
                else np.zeros((n, 2))
            )
            col = (
                mesh.colors.astype(np.float32)
                if mesh.colors is not None
                else np.ones((n, 4), np.float32)
            )
            material = (
                None
                if mesh.material_index is None
                else model.materials[mesh.material_index]
            )
            key = (id(material), has_uv)
            if key not in mat_index:
                mat_index[key] = len(mats)
                mats.append(prepare_material(material, has_uv))
            prepared = mats[mat_index[key]]
            if prepared.normal_tex is not None:
                tan, sign = compute_tangents(pos_w, nrm_w, uv, tri)
            else:
                tan, sign = np.zeros((n, 3)), np.ones(n)
            pos_l.append(pos_w)
            nrm_l.append(nrm_w)
            uv_l.append(uv)
            col_l.append(col)
            tan_l.append(tan)
            sign_l.append(sign)
            tri_l.append(tri + offset)
            draw_l.append(np.full(tri.shape[0], len(draw_mat), dtype=np.int32))
            draw_mat.append(mat_index[key])
            offset += n
    if not pos_l:
        empty = np.zeros((0, 3))
        return Geometry(
            empty, empty, np.zeros((0, 2)), np.zeros((0, 4), np.float32), empty,
            np.zeros(0), np.zeros((0, 3), np.int64), np.zeros(0, np.int32),
            np.zeros(0, np.int32), tuple(mats),
        )  # fmt: skip
    return Geometry(
        pos=np.concatenate(pos_l),
        nrm=np.concatenate(nrm_l),
        uv=np.concatenate(uv_l),
        col=np.concatenate(col_l),
        tan=np.concatenate(tan_l),
        tan_sign=np.concatenate(sign_l),
        tri=np.concatenate(tri_l),
        tri_draw=np.concatenate(draw_l),
        draw_mat=np.asarray(draw_mat, dtype=np.int32),
        materials=tuple(mats),
    )
