"""Importeur STL, ascii et binaire.

Détection fiable : un fichier est binaire si sa taille vaut exactement 84 + 50 * n (n lu à l'offset 80) ; on ne
se fie pas au mot `solid`, que beaucoup d'exporteurs binaires écrivent aussi. Les sommets sont dédupliqués par
position et les normales recalculées (lisses) : les normales de facette du fichier ne sont pas reprises.
"""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np

from kut3d.errors import MAX_ELEMENTS, MAX_LINE_BYTES, ModelImportError
from kut3d.geometry import smooth_normals
from kut3d.importers._common import read_limited
from kut3d.mathutil import Array
from kut3d.model import Mesh, Model, Node

EXTENSIONS = (".stl",)

_RECORD = np.dtype([("normal", "<f4", 3), ("v", "<f4", (3, 3)), ("attr", "<u2")])


def _binary(data: bytes) -> Array | None:
    """Triangles (T, 3, 3) si `data` a exactement la taille d'un STL binaire, sinon None."""
    if len(data) < 84:
        return None
    (n,) = struct.unpack_from("<I", data, 80)
    if len(data) != 84 + 50 * n:
        return None
    if n > MAX_ELEMENTS:
        raise ModelImportError(f"STL : trop de triangles ({n}).")
    if n == 0:
        raise ModelImportError("STL : aucun triangle.")
    return np.frombuffer(data, dtype=_RECORD, count=n, offset=84)["v"]


def _ascii(data: bytes, what: str) -> Array:
    text = data.decode("utf-8", errors="replace")
    verts: list[float] = []
    for line in text.splitlines():
        if len(line) > MAX_LINE_BYTES:
            raise ModelImportError(f"{what} : ligne trop longue.")
        parts = line.split()
        if parts and parts[0] == "vertex":
            if len(parts) != 4:
                raise ModelImportError(f"{what} : sommet mal formé.")
            verts.extend(float(p) for p in parts[1:])
    if not verts:
        raise ModelImportError(
            f"{what} : ni STL binaire valide (taille incohérente) ni STL ascii."
        )
    if len(verts) % 9:
        raise ModelImportError(
            f"{what} : nombre de sommets non multiple de 3 (fichier tronqué ?)."
        )
    return np.asarray(verts, dtype=np.float32).reshape(-1, 3, 3)


def load(path: Path) -> Model:
    data = read_limited(path)
    tris = _binary(data)
    if tris is None:
        tris = _ascii(data, path.name)
    corners = np.asarray(tris, dtype=np.float32).reshape(-1, 3)
    if not np.isfinite(corners).all():
        raise ModelImportError(f"{path.name} : positions non finies.")
    positions, inverse = np.unique(corners, axis=0, return_inverse=True)
    indices = inverse.reshape(-1, 3).astype(np.uint32)
    mesh = Mesh(
        positions=positions,
        indices=indices,
        normals=smooth_normals(positions, indices),
        name=path.stem,
    )
    return Model(
        nodes=(Node(name=mesh.name, mesh_index=0),),
        root_nodes=(0,),
        meshes=(mesh,),
        source=path,
    )
