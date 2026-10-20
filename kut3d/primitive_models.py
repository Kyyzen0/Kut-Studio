"""Un `Model` à partir d'une forme de base paramétrique, et l'`Instance` qui la pose (façade de script)."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

import numpy as np

from kut3d.mathutil import Array, rotation_x, trs_to_matrix
from kut3d.model import Material, Mesh, Model, Node
from kut3d.primitive_mesh import primitive_arrays
from kut3d.primitives import PRIMITIVE_KINDS, PrimitiveSpec
from kut3d.scene import Instance


def primitive_model(spec: PrimitiveSpec, material: Material | None = None) -> Model:
    """Le modèle généré par `spec` (un maillage, un nœud) ; `material` None = le gris par défaut du rendu.
    Le modèle porte `primitive=spec` : `save_scene` en écrit les paramètres, jamais les sommets."""
    if not isinstance(spec, PrimitiveSpec):
        raise ValueError(
            "spec doit être une forme de kut3d.primitives (Cube, Sphere, Plane, Cylinder, Cone, Torus, Text3D)."
        )
    if material is not None and not isinstance(material, Material):
        raise ValueError("material doit être un kut3d.model.Material ou None.")
    positions, normals, uvs, indices = primitive_arrays(spec)
    mesh = Mesh(
        positions,
        indices,
        normals=normals,
        uvs=uvs,
        material_index=None if material is None else 0,
        name=spec.kind,
    )
    return Model(
        nodes=(Node(name=spec.kind, mesh_index=0),),
        root_nodes=(0,),
        meshes=(mesh,),
        materials=() if material is None else (material,),
        primitive=spec,
    )


def _vector(value: Any, name: str) -> Sequence[float]:
    try:
        arr = np.array(value, dtype=np.float64)
    except (TypeError, ValueError):
        raise ValueError(f"{name} : 3 nombres attendus.") from None
    if arr.shape != (3,) or not np.isfinite(arr).all():
        raise ValueError(f"{name} : 3 nombres finis attendus.")
    return [float(x) for x in arr]


def primitive_instance(
    spec: PrimitiveSpec | str,
    *,
    material: Material | None = None,
    position: Any = (0.0, 0.0, 0.0),
    rotation_deg: Any = (0.0, 0.0, 0.0),
    scale: Any = 1.0,
    name: str = "",
    **params: Any,
) -> Instance:
    """Une forme de base posée : `spec` est une description (`Sphere(radius=1)`) ou un genre (`"sphere"`, avec `radius=1`...
    en paramètres nommés). `rotation_deg` = angles (X, Y, Z) en degrés appliqués dans l'ordre Z, puis Y, puis X ; `scale` un
    nombre ou trois nombres (> 0). Une erreur de paramètre est une `ValueError` en français."""
    if isinstance(spec, str):
        cls = PRIMITIVE_KINDS.get(spec)
        if cls is None:
            raise ValueError(
                f"genre de forme inconnu : {spec[:40]!r} (attendu : {', '.join(PRIMITIVE_KINDS)})."
            )
        try:
            spec = cls(**params)
        except TypeError:
            raise ValueError(
                f"paramètres invalides pour « {cls.kind} » : {', '.join(sorted(map(str, params)))[:80]}."
            ) from None
    elif params:
        raise ValueError(
            "des paramètres nommés ne s'ajoutent qu'à un genre de forme (« sphere »), pas à une description."
        )
    pos = _vector(position, "position")
    angles = _vector(rotation_deg, "rotation_deg")
    if isinstance(scale, bool):
        raise ValueError("scale : un nombre ou trois nombres sont attendus.")
    factors = (
        [*_vector([scale] * 3, "scale")]
        if np.ndim(scale) == 0
        else _vector(scale, "scale")
    )
    if not all(math.isfinite(f) and f > 0 for f in factors):
        raise ValueError("scale : des nombres finis > 0 sont attendus.")
    rot = _rotation(angles)
    matrix = trs_to_matrix(pos, (0.0, 0.0, 0.0, 1.0), factors)
    matrix[:3, :3] = rot @ matrix[:3, :3]
    return Instance(primitive_model(spec, material), transform=matrix, name=name)


def _rotation(angles: Sequence[float]) -> Array:
    rx = rotation_x(angles[0])[:3, :3]
    c, s = math.cos(math.radians(angles[1])), math.sin(math.radians(angles[1]))
    ry = np.array([[c, 0.0, s], [0.0, 1.0, 0.0], [-s, 0.0, c]])
    c, s = math.cos(math.radians(angles[2])), math.sin(math.radians(angles[2]))
    rz = np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])
    return np.asarray(rx @ ry @ rz)


def pristine_material(model: Model) -> Material | None:
    """Le matériau du modèle forme de base `model`, après avoir vérifié qu'il est EXACTEMENT ce que `primitive_model` génère :
    un modèle de forme dont on a changé la géométrie, les nœuds ou les animations (`dataclasses.replace`) ne s'enregistre pas
    par ses seuls paramètres, qui ne le décrivent plus : refusé (`ValueError`), jamais réécrit en silence."""
    spec = model.primitive
    assert spec is not None
    mesh = model.meshes[0] if len(model.meshes) == 1 else None
    positions, normals, uvs, indices = primitive_arrays(spec)
    intact = (
        mesh is not None
        and model.nodes == (Node(name=spec.kind, mesh_index=0),)
        and model.root_nodes == (0,)
        and not model.skeletons
        and not model.animations
        and len(model.materials) == (0 if mesh.material_index is None else 1)
        and mesh.material_index in (None, 0)
        and mesh.normals is not None
        and mesh.uvs is not None
        and mesh.colors is None
        and mesh.joints is None
        and not mesh.morph_targets
        and np.array_equal(mesh.positions, positions)
        and np.array_equal(mesh.normals, normals)
        and np.array_equal(mesh.uvs, uvs)
        and np.array_equal(mesh.indices, indices)
    )
    if not intact:
        raise ValueError(
            f"la forme « {spec.kind} » a été modifiée après sa création (géométrie, nœuds ou animations) : "
            "ses paramètres ne la décrivent plus."
        )
    return model.materials[0] if model.materials else None
