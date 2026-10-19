"""Lecture des accessors glTF (bufferView, stride, `normalized`, `sparse`) en tableaux numpy."""

from __future__ import annotations

from typing import Any

import numpy as np

from kut3d.errors import MAX_ELEMENTS, ModelImportError
from kut3d.mathutil import Array

_COMPONENT = {
    5120: "i1",
    5121: "u1",
    5122: "<i2",
    5123: "<u2",
    5125: "<u4",
    5126: "<f4",
}
_TYPE_SIZE = {
    "SCALAR": 1,
    "VEC2": 2,
    "VEC3": 3,
    "VEC4": 4,
    "MAT2": 4,
    "MAT3": 9,
    "MAT4": 16,
}


def _int(d: dict[str, Any], key: str, default: int | None = None) -> int:
    v = d.get(key, default)
    if v is None:
        raise ModelImportError(f"champ « {key} » manquant.")
    if isinstance(v, bool) or not isinstance(v, int):
        raise ModelImportError(f"champ « {key} » invalide (entier attendu).")
    return v


def _read_view(
    gltf: dict[str, Any],
    buffers: list[bytes],
    view_index: int,
    byte_offset: int,
    count: int,
    ncomp: int,
    ctype: int,
    stride: int | None,
) -> Array:
    """Lit `count` éléments de `ncomp` composantes ; retourne (count, ncomp) contigu (copie)."""
    views = gltf.get("bufferViews", [])
    if not isinstance(views, list) or not 0 <= view_index < len(views):
        raise ModelImportError(f"bufferView {view_index} inexistant.")
    view = views[view_index]
    buf_i = _int(view, "buffer")
    if not 0 <= buf_i < len(buffers):
        raise ModelImportError(f"buffer {buf_i} inexistant.")
    buf = buffers[buf_i]
    base = _int(view, "byteOffset", 0)
    length = _int(view, "byteLength")
    if base < 0 or length < 0 or base + length > len(buf):
        raise ModelImportError(
            f"bufferView {view_index} dépasse son buffer (fichier tronqué ?)."
        )
    if ctype not in _COMPONENT:
        raise ModelImportError(f"componentType inconnu : {ctype}.")
    dt = np.dtype(_COMPONENT[ctype])
    elem = ncomp * dt.itemsize
    step = stride if stride else elem
    if step < elem:
        raise ModelImportError("byteStride plus petit qu'un élément.")
    if count == 0:
        return np.zeros((0, ncomp), dtype=dt)
    span = step * (count - 1) + elem
    start = base + byte_offset
    if byte_offset < 0 or byte_offset + span > length:
        raise ModelImportError("accessor hors de son bufferView (fichier tronqué ?).")
    raw = np.frombuffer(buf, dtype=np.uint8, count=span, offset=start)
    rows = np.lib.stride_tricks.as_strided(raw, shape=(count, elem), strides=(step, 1))
    return np.ascontiguousarray(rows).view(dt).reshape(count, ncomp)


def read_accessor(gltf: dict[str, Any], buffers: list[bytes], index: int) -> Array:
    """Un accessor en tableau : (count,) pour SCALAR, (count, n) pour VEC, (count, 4, 4) pour MAT4 (matrices
    « ligne » : translation dans `m[:3, 3]`). `normalized` -> float32 ; sinon dtype natif du composant."""
    accessors = gltf.get("accessors", [])
    if not isinstance(accessors, list) or not 0 <= index < len(accessors):
        raise ModelImportError(f"accessor {index} inexistant.")
    acc = accessors[index]
    count = _int(acc, "count")
    ctype = _int(acc, "componentType")
    kind = acc.get("type")
    if kind not in _TYPE_SIZE:
        raise ModelImportError(f"type d'accessor inconnu : {kind!r}.")
    ncomp = _TYPE_SIZE[kind]
    if not 0 <= count <= MAX_ELEMENTS:
        raise ModelImportError(
            f"accessor {index} : nombre d'éléments refusé ({count})."
        )
    if kind in ("MAT2", "MAT3") and ctype in (5120, 5121, 5122, 5123):
        raise ModelImportError(
            f"accessor {index} : {kind} de petits entiers non pris en charge."
        )
    if ctype not in _COMPONENT:
        raise ModelImportError(f"componentType inconnu : {ctype}.")
    if "bufferView" in acc:
        views = gltf.get("bufferViews", [])
        vi = _int(acc, "bufferView")
        stride = None
        if isinstance(views, list) and 0 <= vi < len(views):
            stride = views[vi].get("byteStride")
        arr = _read_view(
            gltf, buffers, vi, _int(acc, "byteOffset", 0), count, ncomp, ctype, stride
        )
    else:
        dt = np.dtype(_COMPONENT[ctype])
        if count * ncomp * dt.itemsize > 1 << 30:
            raise ModelImportError(f"accessor {index} : trop volumineux.")
        arr = np.zeros((count, ncomp), dtype=dt)
    sparse = acc.get("sparse")
    if sparse is not None:
        arr = _apply_sparse(gltf, buffers, arr.copy(), sparse, ncomp, ctype)
    if acc.get("normalized") and arr.dtype.kind in "iu":
        arr = denormalize(arr)
    if kind == "MAT4":
        arr = arr.reshape(count, 4, 4).transpose(0, 2, 1)
    elif kind == "SCALAR":
        arr = arr.reshape(count)
    return arr


def denormalize(arr: Array) -> Array:
    info = np.iinfo(arr.dtype)
    out = arr.astype(np.float32) / np.float32(info.max)
    return np.maximum(out, -1.0) if info.min < 0 else out


def _apply_sparse(
    gltf: dict[str, Any],
    buffers: list[bytes],
    base: Array,
    sparse: dict[str, Any],
    ncomp: int,
    ctype: int,
) -> Array:
    n = _int(sparse, "count")
    if not 0 <= n <= base.shape[0]:
        raise ModelImportError("sparse : nombre d'éléments invalide.")
    idx_d = sparse.get("indices")
    val_d = sparse.get("values")
    if not isinstance(idx_d, dict) or not isinstance(val_d, dict):
        raise ModelImportError("sparse : indices ou values absents.")
    itype = _int(idx_d, "componentType")
    if itype not in (5121, 5123, 5125):
        raise ModelImportError("sparse : type d'indices invalide.")
    idx = _read_view(
        gltf,
        buffers,
        _int(idx_d, "bufferView"),
        _int(idx_d, "byteOffset", 0),
        n,
        1,
        itype,
        None,
    ).reshape(n)
    vals = _read_view(
        gltf,
        buffers,
        _int(val_d, "bufferView"),
        _int(val_d, "byteOffset", 0),
        n,
        ncomp,
        ctype,
        None,
    )
    if n and int(idx.max()) >= base.shape[0]:
        raise ModelImportError("sparse : indice hors limites.")
    base[idx.astype(np.int64)] = vals
    return base
