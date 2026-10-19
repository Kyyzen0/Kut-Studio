"""Importeur OBJ + MTL.

Un `Mesh` par matériau (`usemtl`) ; `o`/`g` sont ignorés. Les sommets sont dédupliqués par triplet
(v, vt, vn). Les coordonnées de texture sont converties à la convention du modèle (V vers le bas : v = 1 - vt).
Normales absentes : calculées, lisses (soudées par position pour que les coutures d'UV ne se voient pas).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from kut3d.errors import (
    MAX_ELEMENTS,
    MAX_LINE_BYTES,
    MAX_MTL_BYTES,
    ModelImportError,
    short,
)
from kut3d.geometry import fan_triangulate_ragged, smooth_normals
from kut3d.importers._common import (
    finalize_dependencies,
    material_texture_paths,
    read_limited,
    safe_is_file,
    safe_resolve,
    texture_from_file,
)
from kut3d.mathutil import Array
from kut3d.model import Material, Mesh, Model, Node

EXTENSIONS = (".obj",)

# Nombre d'arguments des options d'une ligne `map_*` du MTL (le nom de fichier vient après).
_MAP_OPTIONS = {
    "-blendu": 1,
    "-blendv": 1,
    "-cc": 1,
    "-clamp": 1,
    "-imfchan": 1,
    "-mm": 2,
    "-o": 3,
    "-s": 3,
    "-t": 3,
    "-texres": 1,
    "-bm": 1,
    "-type": 1,
    "-boost": 1,
}
_GREY = (0.8, 0.8, 0.8, 1.0)


def _lines(data: bytes, what: str) -> list[str]:
    """Décode et découpe en lignes logiques (continuation par `\\`), en bornant la longueur d'une ligne."""
    out: list[str] = []
    pending = ""
    for raw in data.decode("utf-8", errors="replace").splitlines():
        if len(raw) > MAX_LINE_BYTES:
            raise ModelImportError(
                f"{what} : ligne trop longue ({len(raw)} caractères)."
            )
        if raw.endswith("\\"):
            pending += raw[:-1] + " "
            continue
        out.append(pending + raw)
        pending = ""
    if pending:
        out.append(pending)
    return out


def _floats(parts: list[str], count: int, line_no: int, what: str) -> list[float]:
    if len(parts) < count + 1:
        raise ModelImportError(f"{what} ligne {line_no} : {count} valeurs attendues.")
    return [float(p) for p in parts[1 : count + 1]]


def _map_name(parts: list[str]) -> str:
    """Nom de fichier d'une ligne `map_*` : ce qui suit les options (le nom peut contenir des espaces)."""
    i = 1
    while i < len(parts) and parts[i].startswith("-") and parts[i] in _MAP_OPTIONS:
        i += 1 + _MAP_OPTIONS[parts[i]]
    return " ".join(parts[i:])


def _parse_mtl(path: Path, warnings: list[str]) -> list[Material]:
    what = path.name
    materials: list[Material] = []
    cur: dict[str, object] | None = None

    def flush() -> None:
        nonlocal cur
        if cur is not None:
            materials.append(_build_material(cur, warnings))
        cur = None

    for no, line in enumerate(_lines(read_limited(path, MAX_MTL_BYTES), what), start=1):
        parts = line.split()
        if not parts or parts[0].startswith("#"):
            continue
        key = parts[0].lower()
        try:
            if key == "newmtl":
                flush()
                cur = {"name": line.strip()[6:].strip(), "dir": path.parent}
            elif cur is None:
                continue
            elif key == "kd":
                cur["kd"] = _floats(parts, 3, no, what)
            elif key == "ke":
                cur["ke"] = _floats(parts, 3, no, what)
            elif key == "d":
                cur["d"] = _floats(parts, 1, no, what)[0]
            elif key == "tr":
                cur["tr"] = _floats(parts, 1, no, what)[0]
            elif key == "pm":
                cur["pm"] = _floats(parts, 1, no, what)[0]
            elif key == "pr":
                cur["pr"] = _floats(parts, 1, no, what)[0]
            elif key in ("map_kd", "map_ke", "map_bump", "bump", "norm"):
                slot = {"map_kd": "map_kd", "map_ke": "map_ke"}.get(key, "normal")
                name = _map_name(parts)
                if name:
                    cur[slot] = name
        except ValueError as exc:
            warnings.append(
                f"{what} ligne {no} : valeur illisible ({short(str(exc))}), ligne ignorée"
            )
    flush()
    return materials


def _build_material(raw: dict[str, object], warnings: list[str]) -> Material:
    base_dir = raw["dir"]
    assert isinstance(base_dir, Path)
    name = str(raw["name"])
    kd = raw.get("kd", list(_GREY[:3]))
    assert isinstance(kd, list)
    alpha = 1.0
    if "d" in raw:
        alpha = float(raw["d"])  # type: ignore[arg-type]
    elif "tr" in raw:
        alpha = 1.0 - float(raw["tr"])  # type: ignore[arg-type]
    alpha = min(max(alpha, 0.0), 1.0)
    textures: dict[str, object] = {}
    for slot in ("map_kd", "normal", "map_ke"):
        if slot in raw:
            textures[slot] = texture_from_file(
                base_dir, str(raw[slot]), warnings, f"matériau {name!r}"
            )
    mat = Material(
        name=name,
        base_color=(*kd, alpha),
        base_color_texture=textures.get("map_kd"),  # type: ignore[arg-type]
        normal_texture=textures.get("normal"),  # type: ignore[arg-type]
        emissive_texture=textures.get("map_ke"),  # type: ignore[arg-type]
        emissive=raw.get("ke", (0.0, 0.0, 0.0)),
        metallic=float(raw.get("pm", 0.0)),  # type: ignore[arg-type]
        roughness=float(raw.get("pr", 1.0)),  # type: ignore[arg-type]
        alpha_mode="blend" if alpha < 1.0 else "opaque",
    )
    return mat


def _resolve_index(token: str, count: int, line_no: int) -> int:
    """Indice OBJ (base 1, négatif = relatif) -> base 0 ; chaîne vide ou 0 = absent (-1)."""
    if not token:
        return -1
    i = int(token)
    if i > 0:
        return i - 1
    if i < 0:
        return count + i if count + i >= 0 else -2
    raise ModelImportError(f"ligne {line_no} : indice 0 invalide dans une face.")


def _group_mesh(
    corners: Array,
    pos: Array,
    uv: Array,
    nrm: Array,
    name: str,
    material_index: int | None,
    warnings: list[str],
) -> Mesh:
    """Construit le `Mesh` d'un groupe de triangles : `corners` (T, 3, 3) = (v, vt, vn) par coin."""
    flat = corners.reshape(-1, 3)
    uniq, inverse = np.unique(flat, axis=0, return_inverse=True)
    indices = inverse.reshape(-1, 3)
    positions = pos[uniq[:, 0]]
    has_uv = uniq[:, 1] >= 0
    has_n = uniq[:, 2] >= 0
    uvs = None
    if has_uv.any():
        uvs = np.zeros((len(uniq), 2), dtype=np.float32)
        uvs[has_uv] = uv[uniq[has_uv, 1]]
        uvs[:, 1] = np.where(has_uv, 1.0 - uvs[:, 1], 0.0)
        if not has_uv.all():
            warnings.append(
                f"{name} : coordonnées de texture manquantes sur certains coins (mises à 0)"
            )
    if has_n.all():
        normals = nrm[uniq[:, 2]].astype(np.float64)
        length = np.linalg.norm(normals, axis=1, keepdims=True)
        normals = (normals / np.where(length < 1e-20, 1.0, length)).astype(np.float32)
    else:
        normals = smooth_normals(positions, indices, groups=uniq[:, 0])
    return Mesh(
        positions=positions,
        indices=indices,
        normals=normals,
        uvs=uvs,
        material_index=material_index,
        name=name,
    )


def load(path: Path) -> Model:
    warnings: list[str] = []
    what = path.name
    pos_l: list[float] = []
    uv_l: list[float] = []
    nrm_l: list[float] = []
    cv: list[int] = []
    ct: list[int] = []
    cn: list[int] = []
    face_counts: list[int] = []
    face_mat: list[int] = []
    mat_names: dict[str, int] = {}
    mtllibs: list[str] = []
    current = -1
    skipped = 0
    for no, line in enumerate(_lines(read_limited(path), what), start=1):
        parts = line.split()
        if not parts or parts[0][0] == "#":
            continue
        key = parts[0]
        try:
            if key == "v":
                pos_l.extend(_floats(parts, 3, no, what))
            elif key == "vt":
                uv_l.extend(_floats(parts, 1, no, what))
                uv_l.append(float(parts[2]) if len(parts) > 2 else 0.0)
            elif key == "vn":
                nrm_l.extend(_floats(parts, 3, no, what))
            elif key == "f":
                nv, nt, nn = len(pos_l) // 3, len(uv_l) // 2, len(nrm_l) // 3
                if len(parts) < 4:
                    skipped += 1
                    continue
                for tok in parts[1:]:
                    a = tok.split("/")
                    vi = _resolve_index(a[0], nv, no)
                    if vi == -1:
                        raise ModelImportError(
                            f"{what} ligne {no} : indice de sommet manquant."
                        )
                    cv.append(vi)
                    ct.append(_resolve_index(a[1], nt, no) if len(a) > 1 else -1)
                    cn.append(_resolve_index(a[2], nn, no) if len(a) > 2 else -1)
                face_counts.append(len(parts) - 1)
                face_mat.append(current)
            elif key == "usemtl":
                name = line.strip()[6:].strip()
                current = mat_names.setdefault(name, len(mat_names))
            elif key == "mtllib":
                mtllibs.append(line.strip()[6:].strip())
        except ValueError as exc:
            if isinstance(exc, ModelImportError):
                raise
            raise ModelImportError(
                f"{what} ligne {no} : nombre illisible ({short(str(exc))})."
            ) from exc
        if len(cv) > MAX_ELEMENTS or len(pos_l) > 3 * MAX_ELEMENTS:
            raise ModelImportError(
                f"{what} : trop d'éléments (maximum {MAX_ELEMENTS})."
            )
    if skipped:
        warnings.append(f"{skipped} face(s) de moins de 3 sommets ignorée(s)")
    if not face_counts:
        raise ModelImportError(f"{what} : aucune face.")
    positions = np.asarray(pos_l, dtype=np.float32).reshape(-1, 3)
    uvs = np.asarray(uv_l, dtype=np.float32).reshape(-1, 2)
    nrms = np.asarray(nrm_l, dtype=np.float32).reshape(-1, 3)
    c = np.stack([cv, ct, cn], axis=1).astype(np.int64)
    for col, size, label in (
        (0, len(positions), "v"),
        (1, len(uvs), "vt"),
        (2, len(nrms), "vn"),
    ):
        bad = (c[:, col] < -1) | (c[:, col] >= size)
        if bad.any():
            raise ModelImportError(
                f"{what} : indice {label} hors limites (fichier tronqué ?)."
            )
    counts = np.asarray(face_counts, dtype=np.int64)
    starts = np.cumsum(counts) - counts
    tri, face_of = fan_triangulate_ragged(starts, counts)
    tri_corners = c[tri]  # (T, 3, 3)
    tri_mat = np.asarray(face_mat, dtype=np.int64)[face_of]

    deps: list[Path] = []
    materials = _load_materials(path, mtllibs, mat_names, warnings, deps)
    stem = path.stem
    used = list(dict.fromkeys(tri_mat.tolist()))
    meshes: list[Mesh] = []
    for m in used:
        label = (
            stem
            if len(used) == 1
            else f"{stem}:{_name_of(mat_names, m) or 'sans matériau'}"
        )
        meshes.append(
            _group_mesh(
                tri_corners[tri_mat == m],
                positions,
                uvs,
                nrms,
                label,
                None if m < 0 else m,
                warnings,
            )
        )
    nodes = tuple(Node(name=m.name, mesh_index=i) for i, m in enumerate(meshes))
    return Model(
        nodes=nodes,
        root_nodes=tuple(range(len(nodes))),
        meshes=tuple(meshes),
        materials=tuple(materials),
        source=path,
        warnings=tuple(warnings),
        dependencies=finalize_dependencies([*deps, *material_texture_paths(materials)]),
    )


def _name_of(mat_names: dict[str, int], index: int) -> str:
    for name, i in mat_names.items():
        if i == index:
            return name
    return ""


def _load_materials(
    path: Path,
    mtllibs: list[str],
    mat_names: dict[str, int],
    warnings: list[str],
    deps: list[Path],
) -> list[Material]:
    """Matériaux dans l'ordre de `mat_names` (l'indice d'un `usemtl` = indice du matériau)."""
    parsed: dict[str, Material] = {}
    for lib in mtllibs:
        candidates = [lib] + [t for t in lib.split() if t != lib]
        for cand in candidates:
            target = safe_resolve(path.parent / cand.replace("\\", "/"))
            if (
                target is not None
                and target.is_relative_to(path.parent.resolve())
                and safe_is_file(target)
            ):
                deps.append(target)
                for mat in _parse_mtl(target, warnings):
                    parsed.setdefault(mat.name, mat)
                break
        else:
            warnings.append(
                f"fichier MTL introuvable : {short(lib)} (matériau gris par défaut)"
            )
    out: list[Material] = []
    for name in mat_names:
        found = parsed.get(name)
        if found is None:
            if parsed or not mtllibs:
                warnings.append(
                    f"matériau {short(name)} absent du MTL (matériau gris par défaut)"
                )
            found = Material(name=name, base_color=_GREY)
        out.append(found)
    return out
