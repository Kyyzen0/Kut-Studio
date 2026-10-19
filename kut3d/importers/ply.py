"""Importeur PLY : ascii, binary_little_endian, binary_big_endian.

Propriétés lues : `x y z`, `nx ny nz`, `u v` / `s t` (V converti : v = 1 - v), `red green blue [alpha]` et la liste
de faces `vertex_indices` / `vertex_index` (polygones triangulés en éventail). Les autres éléments sont ignorés.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from kut3d.errors import (
    MAX_ELEMENTS,
    MAX_HEADER_BYTES,
    MAX_LINE_BYTES,
    ModelImportError,
    short,
)
from kut3d.geometry import fan_triangulate_fixed, smooth_normals
from kut3d.importers._common import read_limited
from kut3d.mathutil import Array
from kut3d.model import Mesh, Model, Node

EXTENSIONS = (".ply",)

_TYPES = {
    "char": "i1",
    "int8": "i1",
    "uchar": "u1",
    "uint8": "u1",
    "short": "i2",
    "int16": "i2",
    "ushort": "u2",
    "uint16": "u2",
    "int": "i4",
    "int32": "i4",
    "uint": "u4",
    "uint32": "u4",
    "float": "f4",
    "float32": "f4",
    "double": "f8",
    "float64": "f8",
}
_MAX_ELEMENTS_DECLARED = 16
_MAX_PROPERTIES = 64


@dataclass
class _Prop:
    name: str
    dtype: str
    list_count: str | None = None  # type du compteur si c'est une liste


@dataclass
class _Element:
    name: str
    count: int
    props: list[_Prop] = field(default_factory=list)

    @property
    def has_list(self) -> bool:
        return any(p.list_count for p in self.props)


def _parse_header(data: bytes, what: str) -> tuple[str, list[_Element], int]:
    end = data.find(b"end_header", 0, MAX_HEADER_BYTES)
    if end < 0:
        raise ModelImportError(f"{what} : en-tête PLY incomplet ou trop long.")
    nl = data.find(b"\n", end)
    if nl < 0:
        raise ModelImportError(f"{what} : en-tête PLY non terminé.")
    lines = data[:nl].decode("ascii", errors="replace").replace("\r", "").split("\n")
    if lines[0].strip() != "ply":
        raise ModelImportError(f"{what} : ce n'est pas un fichier PLY.")
    fmt = ""
    elements: list[_Element] = []
    for line in lines[1:]:
        parts = line.split()
        if not parts or parts[0] in ("comment", "obj_info", "end_header"):
            continue
        if parts[0] == "format" and len(parts) >= 2:
            fmt = parts[1]
        elif parts[0] == "element" and len(parts) == 3:
            if len(elements) >= _MAX_ELEMENTS_DECLARED:
                raise ModelImportError(f"{what} : trop d'éléments déclarés.")
            count = int(parts[2])
            if not 0 <= count <= MAX_ELEMENTS:
                raise ModelImportError(f"{what} : nombre d'éléments refusé ({count}).")
            elements.append(_Element(parts[1], count))
        elif parts[0] == "property" and elements:
            if len(elements[-1].props) >= _MAX_PROPERTIES:
                raise ModelImportError(f"{what} : trop de propriétés.")
            if parts[1] == "list" and len(parts) == 5:
                if parts[2] not in _TYPES or parts[3] not in _TYPES:
                    raise ModelImportError(f"{what} : type de liste inconnu.")
                elements[-1].props.append(
                    _Prop(parts[4], _TYPES[parts[3]], _TYPES[parts[2]])
                )
            elif len(parts) == 3 and parts[1] in _TYPES:
                elements[-1].props.append(_Prop(parts[2], _TYPES[parts[1]]))
            else:
                raise ModelImportError(
                    f"{what} : propriété PLY illisible ({short(line)})."
                )
    if fmt not in ("ascii", "binary_little_endian", "binary_big_endian"):
        raise ModelImportError(f"{what} : format PLY inconnu ({short(fmt)}).")
    return fmt, elements, nl + 1


def _read_ascii(
    body: bytes, elements: list[_Element], what: str
) -> dict[str, tuple[Array | None, list[Array] | Array | None]]:
    """Retourne par élément : (tableau scalaire (count, nprops) ou None, listes ou None)."""
    lines = body.decode("ascii", errors="replace").split("\n")
    out: dict[str, tuple[Array | None, list[Array] | Array | None]] = {}
    pos = 0
    for el in elements:
        chunk = lines[pos : pos + el.count]
        if len(chunk) < el.count:
            raise ModelImportError(f"{what} : fichier tronqué (élément {el.name}).")
        pos += el.count
        if any(len(line) > MAX_LINE_BYTES for line in chunk):
            raise ModelImportError(f"{what} : ligne trop longue.")
        if not el.has_list:
            tokens = " ".join(chunk).split()
            n = len(el.props)
            if len(tokens) != el.count * n:
                raise ModelImportError(
                    f"{what} : élément {el.name} mal formé ({len(tokens)} valeurs)."
                )
            arr = np.array(tokens, dtype=np.float64).reshape(el.count, n)
            out[el.name] = (arr, None)
        else:
            lists: list[Array] = []
            for line in chunk:
                vals = line.split()
                i = 0
                for p in el.props:
                    if i >= len(vals):
                        raise ModelImportError(
                            f"{what} : ligne trop courte dans {el.name}."
                        )
                    if p.list_count is None:
                        i += 1
                        continue
                    n0 = int(vals[i])
                    if n0 < 0 or i + 1 + n0 > len(vals):
                        raise ModelImportError(
                            f"{what} : liste mal formée dans {el.name}."
                        )
                    if _keep(el, p):
                        lists.append(np.array(vals[i + 1 : i + 1 + n0], dtype=np.int64))
                    i += 1 + n0
            out[el.name] = (None, lists)
    return out


def _keep(el: _Element, prop: _Prop) -> bool:
    """Seule la liste d'indices de sommets nous intéresse (les autres listes sont lues puis jetées)."""
    if prop.name in ("vertex_indices", "vertex_index"):
        return True
    return sum(1 for p in el.props if p.list_count) == 1


def _struct_dtype(el: _Element, endian: str) -> np.dtype:
    return np.dtype([(p.name, endian + p.dtype) for p in el.props])


def _read_binary(
    data: bytes, offset: int, elements: list[_Element], endian: str, what: str
) -> dict[str, tuple[Array | None, list[Array] | Array | None]]:
    out: dict[str, tuple[Array | None, list[Array] | Array | None]] = {}
    for el in elements:
        if not el.has_list:
            dt = _struct_dtype(el, endian)
            size = el.count * dt.itemsize
            if offset + size > len(data):
                raise ModelImportError(f"{what} : fichier tronqué (élément {el.name}).")
            arr = np.frombuffer(data, dtype=dt, count=el.count, offset=offset)
            offset += size
            out[el.name] = (arr, None)
            continue
        fast = _try_fixed_list(data, offset, el, endian)
        if fast is not None:
            arr, offset = fast
            out[el.name] = (None, arr)
            continue
        lists: list[Array] = []
        for _ in range(el.count):
            for p in el.props:
                if p.list_count is None:
                    offset = _need(data, offset, np.dtype(p.dtype).itemsize, what)
                    continue
                cdt = np.dtype(endian + p.list_count)
                offset = _need(data, offset, cdt.itemsize, what)
                n = int(
                    np.frombuffer(
                        data, dtype=cdt, count=1, offset=offset - cdt.itemsize
                    )[0]
                )
                idt = np.dtype(endian + p.dtype)
                if n < 0 or offset + n * idt.itemsize > len(data):
                    raise ModelImportError(
                        f"{what} : fichier tronqué (liste de {el.name})."
                    )
                if _keep(el, p):
                    lists.append(
                        np.frombuffer(data, dtype=idt, count=n, offset=offset).astype(
                            np.int64
                        )
                    )
                offset += n * idt.itemsize
        out[el.name] = (None, lists)
    return out


def _need(data: bytes, offset: int, size: int, what: str) -> int:
    if offset + size > len(data):
        raise ModelImportError(f"{what} : fichier tronqué.")
    return offset + size


def _try_fixed_list(
    data: bytes, offset: int, el: _Element, endian: str
) -> tuple[Array, int] | None:
    """Chemin rapide : un élément réduit à une seule liste, de taille constante (maillage triangulaire)."""
    if len(el.props) != 1 or el.props[0].list_count is None or el.count == 0:
        return None
    p = el.props[0]
    cdt = np.dtype(endian + p.list_count if p.list_count else "u1")
    if offset + cdt.itemsize > len(data):
        return None
    n0 = int(np.frombuffer(data, dtype=cdt, count=1, offset=offset)[0])
    if not 3 <= n0 <= 1024:
        return None
    rec = np.dtype([("n", cdt), ("i", np.dtype(endian + p.dtype), (n0,))])
    if offset + el.count * rec.itemsize > len(data):
        return None
    arr = np.frombuffer(data, dtype=rec, count=el.count, offset=offset)
    if not (arr["n"] == n0).all():
        return None
    return arr["i"].astype(np.int64), offset + el.count * rec.itemsize


def _column(arr: Array, el: _Element, *names: str) -> Array | None:
    """Colonnes `names` d'un tableau scalaire (structuré ou (count, nprops)) en (count, len(names)) float64."""
    present = [p.name for p in el.props]
    if not all(n in present for n in names):
        return None
    if arr.dtype.names:
        return np.stack([arr[n].astype(np.float64) for n in names], axis=1)
    return np.stack([arr[:, present.index(n)] for n in names], axis=1)


def _color(arr: Array, el: _Element) -> Array | None:
    names = ["red", "green", "blue"]
    rgb = _column(arr, el, *names)
    if rgb is None:
        return None
    dtypes = {p.name: p.dtype for p in el.props}
    peak = {"u1": 255.0, "u2": 65535.0, "u4": 4294967295.0}.get(dtypes["red"])
    alpha = _column(arr, el, "alpha")
    rgba = np.ones((len(rgb), 4), dtype=np.float64)
    rgba[:, :3] = rgb
    if alpha is not None:
        rgba[:, 3] = alpha[:, 0]
    if peak is not None:
        rgba[:, :3] /= peak
        if alpha is not None:
            rgba[:, 3] /= peak
    return rgba.astype(np.float32)


def load(path: Path) -> Model:
    what = path.name
    data = read_limited(path)
    fmt, elements, body = _parse_header(data, what)
    if fmt == "ascii":
        tables = _read_ascii(data[body:], elements, what)
    else:
        tables = _read_binary(
            data, body, elements, "<" if fmt == "binary_little_endian" else ">", what
        )
    by_name = {e.name: e for e in elements}
    if "vertex" not in by_name or by_name["vertex"].count == 0:
        raise ModelImportError(f"{what} : aucun sommet.")
    vel = by_name["vertex"]
    varr = tables["vertex"][0]
    assert varr is not None
    pos = _column(varr, vel, "x", "y", "z")
    if pos is None:
        raise ModelImportError(f"{what} : propriétés x, y, z absentes.")
    nrm = _column(varr, vel, "nx", "ny", "nz")
    uv = _column(varr, vel, "u", "v")
    if uv is None:
        uv = _column(varr, vel, "s", "t")
    if uv is None:
        uv = _column(varr, vel, "texture_u", "texture_v")
    if uv is None:
        uv = _column(varr, vel, "texture_s", "texture_t")
    if uv is not None:
        uv = np.stack([uv[:, 0], 1.0 - uv[:, 1]], axis=1)
    colors = _color(varr, vel)
    indices = _faces(tables, by_name, len(pos), what)
    positions = pos.astype(np.float32)
    normals = (
        nrm.astype(np.float32)
        if nrm is not None
        else smooth_normals(positions, indices)
    )
    mesh = Mesh(
        positions=positions,
        indices=indices,
        normals=normals,
        uvs=uv,
        colors=colors,
        name=path.stem,
    )
    return Model(
        nodes=(Node(name=mesh.name, mesh_index=0),),
        root_nodes=(0,),
        meshes=(mesh,),
        source=path,
    )


def _faces(
    tables: dict[str, tuple[Array | None, list[Array] | Array | None]],
    by_name: dict[str, _Element],
    vertex_count: int,
    what: str,
) -> Array:
    fel = by_name.get("face")
    if fel is None or fel.count == 0:
        raise ModelImportError(
            f"{what} : aucune face (les nuages de points ne sont pas pris en charge)."
        )
    lists = tables["face"][1]
    if lists is None:
        raise ModelImportError(f"{what} : élément face sans liste d'indices.")
    if not any(p.list_count and _keep(fel, p) for p in fel.props):
        raise ModelImportError(f"{what} : liste vertex_indices absente.")
    if isinstance(lists, np.ndarray):
        tri = fan_triangulate_fixed(lists)
    else:
        parts = [fan_triangulate_fixed(f[None, :]) for f in lists if len(f) >= 3]
        if not parts:
            raise ModelImportError(f"{what} : aucune face de 3 sommets ou plus.")
        tri = np.concatenate(parts, axis=0)
    if tri.size and (tri.min() < 0 or tri.max() >= vertex_count):
        raise ModelImportError(f"{what} : indice de sommet hors limites.")
    return tri.astype(np.uint32)
