"""Fichiers 3D de test fabriqués par programme (aucune fixture binaire commitée)."""

from __future__ import annotations

import base64
import json
import struct
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"fake-image-bytes"

# Cube de côté 2 : 8 sommets, 4 coordonnées de texture, 6 normales, 6 quads `v/vt/vn`.
_CUBE_V = [(x, y, z) for z in (-1, 1) for y in (-1, 1) for x in (-1, 1)]
_CUBE_VT = [(0, 0), (1, 0), (1, 1), (0, 1)]
_CUBE_VN = [(0, 0, 1), (0, 0, -1), (1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0)]
_CUBE_F = [
    ((5, 6, 8, 7), 1),
    ((1, 3, 4, 2), 2),
    ((2, 4, 8, 6), 3),
    ((1, 5, 7, 3), 4),
    ((3, 7, 8, 4), 5),
    ((1, 2, 6, 5), 6),
]


def write_cube_obj(
    folder: Path,
    *,
    mtl: bool = True,
    texture: bool = True,
    normals: bool = True,
    name: str = "cube",
    map_line: str = "map_Kd tex.png",
) -> Path:
    """Écrit `name.obj` (+ `name.mtl`, + `tex.png`) dans `folder` ; retourne le chemin de l'OBJ."""
    lines = ["# cube"]
    if mtl:
        lines.append(f"mtllib {name}.mtl")
    lines += [f"v {x} {y} {z}" for x, y, z in _CUBE_V]
    lines += [f"vt {u} {v}" for u, v in _CUBE_VT]
    if normals:
        lines += [f"vn {x} {y} {z}" for x, y, z in _CUBE_VN]
    lines.append("usemtl red")
    for (a, b, c, d), n in _CUBE_F:
        quad = [(a, 1), (b, 2), (c, 3), (d, 4)]
        if normals:
            lines.append("f " + " ".join(f"{v}/{t}/{n}" for v, t in quad))
        else:
            lines.append("f " + " ".join(f"{v}/{t}" for v, t in quad))
    obj = folder / f"{name}.obj"
    obj.write_text("\n".join(lines) + "\n")
    if mtl:
        body = ["newmtl red", "Kd 0.9 0.1 0.2", "Ke 0.1 0.2 0.3", "d 1.0"]
        if texture:
            body.append(map_line)
            body.append("map_Bump -bm 1.0 normal map.png")
        (folder / f"{name}.mtl").write_text("\n".join(body) + "\n")
    if texture:
        (folder / "tex.png").write_bytes(PNG_BYTES)
        (folder / "normal map.png").write_bytes(PNG_BYTES)
    return obj


# --- PLY / STL -------------------------------------------------------------------------------------------

PLY_POSITIONS = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], dtype=np.float32)
PLY_NORMALS = np.array([[0, 0, 1]] * 4, dtype=np.float32)
PLY_UV = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], dtype=np.float32)
PLY_COLORS = np.array(
    [[255, 0, 0, 255], [0, 255, 0, 128], [0, 0, 255, 255], [255, 255, 255, 0]],
    dtype=np.uint8,
)


def write_ply(
    path: Path, fmt: str, faces: list[list[int]] | None = None, *, comment: bool = True
) -> Path:
    """PLY d'un carré (4 sommets : x y z nx ny nz u v red green blue alpha) ; `fmt` : ascii, little ou big."""
    faces = faces if faces is not None else [[0, 1, 2], [0, 2, 3]]
    names = {
        "ascii": "ascii",
        "little": "binary_little_endian",
        "big": "binary_big_endian",
    }
    head = ["ply", f"format {names[fmt]} 1.0"]
    if comment:
        head += ["comment made by tests", "obj_info a comment"]
    head += ["element vertex 4"]
    head += [f"property float {n}" for n in ("x", "y", "z", "nx", "ny", "nz", "u", "v")]
    head += [f"property uchar {n}" for n in ("red", "green", "blue", "alpha")]
    head += [
        f"element face {len(faces)}",
        "property list uchar int vertex_indices",
        "end_header",
    ]
    header = ("\n".join(head) + "\n").encode("ascii")
    if fmt == "ascii":
        rows = []
        for i in range(4):
            vals = [*PLY_POSITIONS[i], *PLY_NORMALS[i], *PLY_UV[i], *PLY_COLORS[i]]
            rows.append(
                " ".join(
                    str(int(v)) if i2 >= 8 else repr(float(v))
                    for i2, v in enumerate(vals)
                )
            )
        rows += [f"{len(f)} " + " ".join(map(str, f)) for f in faces]
        path.write_bytes(header + ("\n".join(rows) + "\n").encode("ascii"))
        return path
    e = "<" if fmt == "little" else ">"
    body = b""
    for i in range(4):
        floats = [*PLY_POSITIONS[i], *PLY_NORMALS[i], *PLY_UV[i]]
        body += struct.pack(e + "8f", *floats) + bytes(PLY_COLORS[i].tolist())
    for f in faces:
        body += struct.pack(e + "B", len(f)) + struct.pack(e + f"{len(f)}i", *f)
    path.write_bytes(header + body)
    return path


def stl_triangles() -> np.ndarray:
    """Tétraèdre : 4 triangles, 4 sommets uniques."""
    p = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=np.float32)
    return p[[[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]]]


def write_stl(
    path: Path, binary: bool, *, header: bytes = b"solid binaire qui ment"
) -> Path:
    tris = stl_triangles()
    if binary:
        data = header.ljust(80, b" ")[:80] + struct.pack("<I", len(tris))
        for t in tris:
            data += (
                struct.pack("<3f", 0, 0, 0)
                + struct.pack("<9f", *t.reshape(-1))
                + b"\x00\x00"
            )
        path.write_bytes(data)
    else:
        out = ["solid tetra"]
        for t in tris:
            out += ["facet normal 0 0 0", " outer loop"]
            out += [f"  vertex {x} {y} {z}" for x, y, z in t]
            out += [" endloop", "endfacet"]
        out.append("endsolid tetra")
        path.write_text("\n".join(out) + "\n")
    return path


# --- glTF ------------------------------------------------------------------------------------------------

SQRT_HALF = float(np.sqrt(0.5))


def _pad(b: bytes) -> bytes:
    return b + b"\x00" * (-len(b) % 4)


def gltf_document() -> tuple[dict[str, Any], bytes]:
    """Quad à deux os : peau, poids, un morph target, deux animations, textures. Retourne (JSON, binaire)."""
    blob = b""
    views: list[dict[str, Any]] = []
    accessors: list[dict[str, Any]] = []

    def add(
        data: bytes,
        ctype: int,
        kind: str,
        count: int,
        *,
        target: int | None = None,
        stride: int | None = None,
        **extra: Any,
    ) -> int:
        nonlocal blob
        offset = len(blob)
        blob += _pad(data)
        view: dict[str, Any] = {
            "buffer": 0,
            "byteOffset": offset,
            "byteLength": len(data),
        }
        if stride:
            view["byteStride"] = stride
        if target:
            view["target"] = target
        views.append(view)
        accessors.append(
            {
                "bufferView": len(views) - 1,
                "componentType": ctype,
                "count": count,
                "type": kind,
                **extra,
            }
        )
        return len(accessors) - 1

    f32, u16, u8 = 5126, 5123, 5121
    pos = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], np.float32)
    a_pos = add(pos.tobytes(), f32, "VEC3", 4, min=[0, 0, 0], max=[1, 1, 0])
    a_nrm = add(np.tile(np.float32([0, 0, 1]), (4, 1)).tobytes(), f32, "VEC3", 4)
    uv = np.array([[0, 1], [1, 1], [1, 0], [0, 0]], np.float32)
    padded = np.concatenate(
        [uv, np.zeros((4, 1), np.float32)], axis=1
    )  # stride de 12 octets
    a_uv = add(padded.tobytes()[:-4], f32, "VEC2", 4, stride=12)
    a_col = add(
        np.array([[255, 0, 0, 255]] * 4, np.uint8).tobytes(),
        u8,
        "VEC4",
        4,
        normalized=True,
    )
    joints = np.array(
        [[0, 0, 0, 0], [0, 0, 0, 0], [1, 0, 0, 0], [1, 0, 0, 0]], np.uint8
    )
    a_joints = add(joints.tobytes(), u8, "VEC4", 4)
    a_weights = add(np.tile(np.float32([1, 0, 0, 0]), (4, 1)).tobytes(), f32, "VEC4", 4)
    a_idx = add(np.array([0, 1, 2, 0, 2, 3], np.uint16).tobytes(), u16, "SCALAR", 6)
    a_morph = add(np.tile(np.float32([0, 0, 0.5]), (4, 1)).tobytes(), f32, "VEC3", 4)
    ibm = np.stack([np.eye(4, dtype=np.float32), np.eye(4, dtype=np.float32)])
    ibm[1][1, 3] = -1.0  # translation (0, -1, 0) ; stockée en colonnes
    a_ibm = add(ibm.transpose(0, 2, 1).copy().tobytes(), f32, "MAT4", 2)
    a_times = add(np.float32([0, 1]).tobytes(), f32, "SCALAR", 2)
    q = np.float32([[0, 0, 0, 1], [0, 0, SQRT_HALF, SQRT_HALF]])
    a_rot = add(q.tobytes(), f32, "VEC4", 2)
    a_wtimes = add(np.float32([0, 1]).tobytes(), f32, "SCALAR", 2)
    a_wvals = add(np.float32([0, 1]).tobytes(), f32, "SCALAR", 2)
    cubic = np.float32(
        [[0, 0, 0], [1, 0, 0], [0.5, 0, 0], [0, 0, 0], [2, 0, 0], [0, 0, 0]]
    )
    a_cubic = add(cubic.tobytes(), f32, "VEC3", 6)
    image_view = len(views)
    views.append({"buffer": 0, "byteOffset": len(blob), "byteLength": len(PNG_BYTES)})
    blob += _pad(PNG_BYTES)

    doc: dict[str, Any] = {
        "asset": {"version": "2.0"},
        "scene": 0,
        "scenes": [{"nodes": [0, 1]}],
        "nodes": [
            {"name": "Quad", "mesh": 0, "skin": 0},
            {"name": "root", "children": [2]},
            {"name": "tip", "translation": [0, 1, 0]},
        ],
        "meshes": [
            {
                "name": "QuadMesh",
                "weights": [0.25],
                "extras": {"targetNames": ["smile"]},
                "primitives": [
                    {
                        "attributes": {
                            "POSITION": a_pos,
                            "NORMAL": a_nrm,
                            "TEXCOORD_0": a_uv,
                            "COLOR_0": a_col,
                            "JOINTS_0": a_joints,
                            "WEIGHTS_0": a_weights,
                        },
                        "indices": a_idx,
                        "material": 0,
                        "targets": [{"POSITION": a_morph}],
                    }
                ],
            }
        ],
        "skins": [{"name": "rig", "joints": [1, 2], "inverseBindMatrices": a_ibm}],
        "materials": [
            {
                "name": "skin",
                "pbrMetallicRoughness": {
                    "baseColorFactor": [1, 0.5, 0.25, 0.5],
                    "baseColorTexture": {"index": 0},
                    "metallicFactor": 0.2,
                    "roughnessFactor": 0.8,
                },
                "normalTexture": {"index": 1},
                "emissiveFactor": [0.1, 0.2, 0.3],
                "alphaMode": "BLEND",
                "doubleSided": True,
            }
        ],
        "textures": [{"source": 0}, {"source": 1}],
        "images": [
            {"name": "albedo", "bufferView": image_view, "mimeType": "image/png"},
            {"uri": "normal%20map.png"},
        ],
        "animations": [
            {
                "name": "wave",
                "channels": [
                    {"sampler": 0, "target": {"node": 2, "path": "rotation"}},
                    {"sampler": 1, "target": {"node": 0, "path": "weights"}},
                ],
                "samplers": [
                    {"input": a_times, "output": a_rot},
                    {"input": a_wtimes, "output": a_wvals, "interpolation": "STEP"},
                ],
            },
            {
                "name": "slide",
                "channels": [
                    {"sampler": 0, "target": {"node": 1, "path": "translation"}}
                ],
                "samplers": [
                    {
                        "input": a_times,
                        "output": a_cubic,
                        "interpolation": "CUBICSPLINE",
                    }
                ],
            },
        ],
        "bufferViews": views,
        "accessors": accessors,
        "buffers": [{"byteLength": len(blob)}],
    }
    return doc, blob


def write_gltf(
    folder: Path,
    kind: str = "glb",
    *,
    name: str = "quad",
    mutate: Callable[[dict[str, Any]], None] | None = None,
) -> Path:
    """Écrit le quad en `glb`, `bin` (.gltf + .bin externe) ou `data` (.gltf + data URI)."""
    doc, blob = gltf_document()
    if mutate:
        mutate(doc)
    (folder / "normal map.png").write_bytes(PNG_BYTES)
    if kind == "glb":
        js = json.dumps(doc).encode()
        js += b" " * (-len(js) % 4)
        bin_ = _pad(blob)
        total = 12 + 8 + len(js) + 8 + len(bin_)
        data = struct.pack("<4sII", b"glTF", 2, total)
        data += struct.pack("<II", len(js), 0x4E4F534A) + js
        data += struct.pack("<II", len(bin_), 0x004E4942) + bin_
        path = folder / f"{name}.glb"
        path.write_bytes(data)
        return path
    if kind == "bin":
        doc["buffers"][0]["uri"] = f"{name}.bin"
        (folder / f"{name}.bin").write_bytes(blob)
    else:
        b64 = base64.b64encode(blob).decode()
        doc["buffers"][0]["uri"] = "data:application/octet-stream;base64," + b64
    path = folder / f"{name}.gltf"
    path.write_text(json.dumps(doc))
    return path
