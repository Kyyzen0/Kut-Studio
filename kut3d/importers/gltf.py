"""Importeur glTF 2.0 / GLB.

Pris en charge : JSON + buffers (data URI, `.bin` externe, chunk GLB), accessors (stride, `normalized`, `sparse`),
primitives TRIANGLES, matériaux métal / rugosité, hiérarchie de nœuds (TRS ou `matrix`), skins, JOINTS_0 /
WEIGHTS_0, morph targets (POSITION, NORMAL), animations (translation, rotation, scale, weights ; LINEAR, STEP,
CUBICSPLINE). Non pris en charge : autres modes de primitive (ignorés avec avertissement), TEXCOORD_1 et plus,
tangentes, caméras, lumières, extensions (une extension requise inconnue, dont Draco, est une erreur).
Un maillage glTF à plusieurs primitives devient plusieurs `Mesh` : le nœud porte la première, des nœuds enfants
`nom#k` (transformation identité, même peau) portent les suivantes.
"""

from __future__ import annotations

import base64
import json
import struct
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import numpy as np

from kut3d.errors import ModelImportError, short
from kut3d.geometry import smooth_normals
from kut3d.importers._common import (
    finalize_dependencies,
    material_texture_paths,
    read_limited,
    safe_is_file,
    safe_resolve,
    texture_from_file,
)
from kut3d.importers.gltf_accessors import denormalize, read_accessor
from kut3d.importers.gltf_sampler import sampler_fields
from kut3d.mathutil import Array, matrix_to_trs, normalize_quaternion
from kut3d.model import (
    AnimationChannel,
    AnimationClip,
    Material,
    Mesh,
    Model,
    MorphTarget,
    Node,
    Skeleton,
    TextureRef,
)

EXTENSIONS = (".gltf", ".glb")
SUPPORTED_EXTENSIONS = frozenset(
    {"KHR_mesh_quantization", "KHR_materials_emissive_strength"}
)
_GLB_MAGIC = b"glTF"
_CHUNK_JSON = 0x4E4F534A
_CHUNK_BIN = 0x004E4942
_PATHS = ("translation", "rotation", "scale", "weights")
_PATH_WIDTH = {"translation": 3, "rotation": 4, "scale": 3}


def _list(gltf: dict[str, Any], key: str) -> list[Any]:
    value = gltf.get(key, [])
    if not isinstance(value, list):
        raise ModelImportError(f"« {key} » doit être une liste.")
    return value


def _obj(items: list[Any], index: Any, what: str) -> dict[str, Any]:
    if (
        isinstance(index, bool)
        or not isinstance(index, int)
        or not 0 <= index < len(items)
    ):
        raise ModelImportError(f"{what} {short(index)} inexistant.")
    item = items[index]
    if not isinstance(item, dict):
        raise ModelImportError(f"{what} {index} invalide.")
    return item


def _vec(
    node: dict[str, Any], key: str, size: int, default: tuple[float, ...]
) -> tuple[float, ...]:
    value = node.get(key)
    if value is None:
        return default
    if not isinstance(value, list) or len(value) != size:
        raise ModelImportError(f"« {key} » doit contenir {size} nombres.")
    return tuple(float(v) for v in value)


def _parse_glb(data: bytes) -> tuple[dict[str, Any], bytes | None]:
    if len(data) < 20:
        raise ModelImportError("GLB tronqué (en-tête).")
    version, total = struct.unpack_from("<II", data, 4)
    if version != 2:
        raise ModelImportError(f"version GLB non prise en charge : {version}.")
    if total > len(data):
        raise ModelImportError("GLB tronqué (longueur déclarée supérieure au fichier).")
    offset = 12
    doc: dict[str, Any] | None = None
    binary: bytes | None = None
    while offset + 8 <= total:
        length, kind = struct.unpack_from("<II", data, offset)
        start = offset + 8
        if start + length > total:
            raise ModelImportError("GLB tronqué (chunk plus long que le fichier).")
        if kind == _CHUNK_JSON and doc is None:
            doc = json.loads(data[start : start + length].decode("utf-8"))
        elif kind == _CHUNK_BIN and binary is None:
            binary = data[start : start + length]
        offset = start + ((length + 3) & ~3)
    if not isinstance(doc, dict):
        raise ModelImportError("GLB sans chunk JSON valide.")
    return doc, binary


def _decode_data_uri(uri: str) -> tuple[bytes, str | None]:
    head, sep, payload = uri.partition(",")
    if not sep or ";base64" not in head:
        raise ModelImportError("data URI non base64.")
    mime = head[5:].split(";")[0] or None
    return base64.b64decode(payload), mime


class _Loader:
    def __init__(self, path: Path, gltf: dict[str, Any], glb_bin: bytes | None) -> None:
        self.path = path
        self.base = path.parent
        self.gltf = gltf
        self.warnings: list[str] = []
        self._textures: dict[int, TextureRef | None] = {}
        self.deps: list[Path] = []
        self.buffers = self._load_buffers(glb_bin)

    # -- buffers -------------------------------------------------------------------------------------------
    def _load_buffers(self, glb_bin: bytes | None) -> list[bytes]:
        out: list[bytes] = []
        for i, buf in enumerate(_list(self.gltf, "buffers")):
            if not isinstance(buf, dict):
                raise ModelImportError(f"buffer {i} invalide.")
            uri = buf.get("uri")
            if uri is None:
                if i != 0 or glb_bin is None:
                    raise ModelImportError(f"buffer {i} sans uri ni chunk GLB.")
                data = glb_bin
            elif not isinstance(uri, str):
                raise ModelImportError(f"buffer {i} : uri invalide.")
            elif uri.startswith("data:"):
                data, _ = _decode_data_uri(uri)
            else:
                data = self._read_external(uri, f"buffer {i}")
            length = buf.get("byteLength")
            if isinstance(length, bool) or not isinstance(length, int) or length < 0:
                raise ModelImportError(f"buffer {i} : byteLength invalide.")
            if len(data) < length:
                raise ModelImportError(
                    f"buffer {i} tronqué ({len(data)} octets sur {length})."
                )
            out.append(data)
        return out

    def _read_external(self, uri: str, what: str) -> bytes:
        text = unquote(uri)
        base = self.base.resolve()
        if "://" in text or text.startswith(("/", "\\")):
            raise ModelImportError(f"{what} : uri externe refusée ({short(uri)}).")
        target = safe_resolve(base / text.replace("\\", "/"))
        if target is not None and not target.is_relative_to(base):
            raise ModelImportError(
                f"{what} : fichier hors du dossier du modèle ({short(uri)})."
            )
        if target is None or not safe_is_file(target):
            raise ModelImportError(f"{what} : fichier introuvable ({short(uri)}).")
        self.deps.append(target)
        return read_limited(target)

    def acc(self, index: Any) -> Array:
        if isinstance(index, bool) or not isinstance(index, int):
            raise ModelImportError(f"accessor invalide : {short(index)}.")
        return read_accessor(self.gltf, self.buffers, index)

    # -- extensions ----------------------------------------------------------------------------------------
    def check_extensions(self) -> None:
        asset = self.gltf.get("asset")
        version = str(asset.get("version", "")) if isinstance(asset, dict) else ""
        if not version.startswith("2."):
            raise ModelImportError(
                f"version glTF non prise en charge : {short(version)}."
            )
        required = [str(e) for e in _list(self.gltf, "extensionsRequired")]
        bad = [e for e in required if e not in SUPPORTED_EXTENSIONS]
        if bad:
            hint = (
                " (compression Draco non prise en charge)"
                if "KHR_draco_mesh_compression" in bad
                else ""
            )
            raise ModelImportError(
                f"extension requise non prise en charge : {', '.join(map(short, bad))}{hint}."
            )
        for ext in _list(self.gltf, "extensionsUsed"):
            if str(ext) not in SUPPORTED_EXTENSIONS and str(ext) not in required:
                self.warnings.append(f"extension ignorée : {short(ext)}")
        if self.gltf.get("cameras"):
            self.warnings.append("caméras glTF ignorées")

    # -- matériaux et textures ---------------------------------------------------------------------------
    def texture(self, info: Any) -> TextureRef | None:
        if not isinstance(info, dict):
            return None
        index = info.get("index")
        if isinstance(index, int) and index in self._textures:
            return self._textures[index]
        tex = _obj(_list(self.gltf, "textures"), index, "texture")
        assert isinstance(index, int)  # garanti par _obj
        ref = self._texture_ref(tex, index)
        if ref is not None:
            fields: dict[str, Any] = dict(
                sampler_fields(self.gltf, tex, index, self.warnings)
            )
            ref = replace(ref, **fields)
        self._textures[index] = ref
        return ref

    def _texture_ref(self, tex: dict[str, Any], index: int) -> TextureRef | None:
        source = tex.get("source")
        if source is None:
            self.warnings.append(
                f"texture {index} sans source (extension d'image non prise en charge)"
            )
            return None
        img = _obj(_list(self.gltf, "images"), source, "image")
        name = str(img.get("name") or tex.get("name") or f"texture{index}")
        uri = img.get("uri")
        if isinstance(uri, str):
            if uri.startswith("data:"):
                data, mime = _decode_data_uri(uri)
                return TextureRef(name=name, data=data, mime_type=mime)
            ref = texture_from_file(
                self.base, unquote(uri), self.warnings, f"image {source}"
            )
            return None if ref is None else replace(ref, name=name)
        if "bufferView" in img:
            view = _obj(
                _list(self.gltf, "bufferViews"), img["bufferView"], "bufferView"
            )
            buf = (
                self.buffers[int(view["buffer"])]
                if 0 <= int(view["buffer"]) < len(self.buffers)
                else b""
            )
            start = int(view.get("byteOffset", 0))
            end = start + int(view["byteLength"])
            if start < 0 or end > len(buf):
                raise ModelImportError(
                    f"image {source} hors de son buffer (fichier tronqué ?)."
                )
            mime = img.get("mimeType")
            return TextureRef(
                name=name,
                data=bytes(buf[start:end]),
                mime_type=str(mime) if mime else None,
            )
        self.warnings.append(f"image {source} sans uri ni bufferView")
        return None

    def material(self, index: int, mat: dict[str, Any]) -> Material:
        pbr = mat.get("pbrMetallicRoughness") or {}
        emissive = _vec(mat, "emissiveFactor", 3, (0.0, 0.0, 0.0))
        strength = (mat.get("extensions") or {}).get("KHR_materials_emissive_strength")
        if isinstance(strength, dict):
            emissive = tuple(
                c * float(strength.get("emissiveStrength", 1.0)) for c in emissive
            )
        mode = str(mat.get("alphaMode", "OPAQUE")).lower()
        if mode not in ("opaque", "mask", "blend"):
            self.warnings.append(
                f"matériau {index} : alphaMode inconnu {short(mode)} (opaque)"
            )
            mode = "opaque"
        return Material(
            name=str(mat.get("name") or f"material{index}"),
            base_color=_vec(pbr, "baseColorFactor", 4, (1.0, 1.0, 1.0, 1.0)),
            base_color_texture=self.texture(pbr.get("baseColorTexture")),
            normal_texture=self.texture(mat.get("normalTexture")),
            metallic=float(pbr.get("metallicFactor", 1.0)),
            roughness=float(pbr.get("roughnessFactor", 1.0)),
            metallic_roughness_texture=self.texture(
                pbr.get("metallicRoughnessTexture")
            ),
            emissive=emissive,
            emissive_texture=self.texture(mat.get("emissiveTexture")),
            occlusion_texture=self.texture(mat.get("occlusionTexture")),
            alpha_mode=mode,
            alpha_cutoff=float(mat.get("alphaCutoff", 0.5)),
            double_sided=bool(mat.get("doubleSided", False)),
        )

    # -- maillages -----------------------------------------------------------------------------------------
    def _columns(self, arr: Array, width: tuple[int, ...], what: str) -> Array:
        if arr.ndim != 2 or arr.shape[1] not in width:
            raise ModelImportError(f"{what} : {arr.shape} invalide.")
        return arr

    def primitive(
        self, prim: dict[str, Any], name: str, mesh: dict[str, Any]
    ) -> Mesh | None:
        mode = prim.get("mode", 4)
        if mode != 4:
            self.warnings.append(
                f"{name} : primitive de mode {short(mode)} ignorée (seul TRIANGLES est pris en charge)"
            )
            return None
        attrs = prim.get("attributes")
        if not isinstance(attrs, dict) or "POSITION" not in attrs:
            self.warnings.append(f"{name} : primitive sans POSITION ignorée")
            return None
        pos = self._columns(
            self.acc(attrs["POSITION"]), (3,), f"{name} POSITION"
        ).astype(np.float32)
        n = len(pos)
        if "indices" in prim:
            flat = self.acc(prim["indices"]).reshape(-1)
        else:
            flat = np.arange(n, dtype=np.uint32)
        if len(flat) % 3:
            raise ModelImportError(
                f"{name} : {len(flat)} indices, pas un multiple de 3."
            )
        indices = flat.astype(np.uint32).reshape(-1, 3)
        normals = None
        if "NORMAL" in attrs:
            normals = self._columns(
                self.acc(attrs["NORMAL"]), (3,), f"{name} NORMAL"
            ).astype(np.float32)
        else:
            normals = smooth_normals(pos, indices)
        uvs = None
        if "TEXCOORD_0" in attrs:
            uvs = self._columns(
                self.acc(attrs["TEXCOORD_0"]), (2,), f"{name} TEXCOORD_0"
            ).astype(np.float32)
        if "TEXCOORD_1" in attrs:
            self.warnings.append(f"{name} : TEXCOORD_1 ignoré")
        colors = None
        if "COLOR_0" in attrs:
            raw = self._columns(self.acc(attrs["COLOR_0"]), (3, 4), f"{name} COLOR_0")
            if raw.dtype.kind in "iu":
                raw = denormalize(raw)
            colors = np.ones((n, 4), dtype=np.float32)
            colors[:, : raw.shape[1]] = raw
        joints = weights = None
        if "JOINTS_0" in attrs and "WEIGHTS_0" in attrs:
            joints = self._columns(
                self.acc(attrs["JOINTS_0"]), (4,), f"{name} JOINTS_0"
            )
            weights = self._columns(
                self.acc(attrs["WEIGHTS_0"]), (4,), f"{name} WEIGHTS_0"
            )
            if weights.dtype.kind in "iu":
                weights = denormalize(weights)
            if "JOINTS_1" in attrs:
                self.warnings.append(
                    f"{name} : plus de 4 influences par sommet, JOINTS_1 ignoré"
                )
        material = prim.get("material")
        if material is not None:
            _obj(_list(self.gltf, "materials"), material, "matériau")
        targets = self._morph_targets(prim, mesh, name, n)
        weights_default = mesh.get("weights")
        morph_weights: tuple[float, ...] = ()
        if targets:
            if isinstance(weights_default, list) and len(weights_default) == len(
                targets
            ):
                morph_weights = tuple(float(w) for w in weights_default)
            else:
                morph_weights = (0.0,) * len(targets)
        return Mesh(
            positions=pos,
            indices=indices,
            normals=normals,
            uvs=uvs,
            colors=colors,
            material_index=material,
            joints=joints,
            weights=weights,
            morph_targets=targets,
            morph_weights=morph_weights,
            name=name,
        )

    def _morph_targets(
        self, prim: dict[str, Any], mesh: dict[str, Any], name: str, n: int
    ) -> tuple[MorphTarget, ...]:
        raw = prim.get("targets")
        if not raw:
            return ()
        if not isinstance(raw, list):
            raise ModelImportError(f"{name} : targets invalide.")
        extras = mesh.get("extras")
        names = extras.get("targetNames") if isinstance(extras, dict) else None
        out: list[MorphTarget] = []
        for i, t in enumerate(raw):
            if not isinstance(t, dict):
                raise ModelImportError(f"{name} : forme {i} invalide.")
            label = (
                str(names[i])
                if isinstance(names, list) and i < len(names)
                else f"target{i}"
            )
            dp = (
                self.acc(t["POSITION"]).astype(np.float32)
                if "POSITION" in t
                else np.zeros((n, 3), np.float32)
            )
            dn = self.acc(t["NORMAL"]).astype(np.float32) if "NORMAL" in t else None
            out.append(MorphTarget(name=label, position_deltas=dp, normal_deltas=dn))
        return tuple(out)

    # -- squelettes ----------------------------------------------------------------------------------------
    def skeleton(
        self, index: int, skin: dict[str, Any], nodes: list[Node], parent_of: list[int]
    ) -> Skeleton:
        joints = skin.get("joints")
        if not isinstance(joints, list) or not joints:
            raise ModelImportError(f"skin {index} sans os.")
        node_ids = [int(j) for j in joints]
        if len(set(node_ids)) != len(node_ids):
            raise ModelImportError(f"skin {index} : os en double.")
        for j in node_ids:
            if not 0 <= j < len(nodes):
                raise ModelImportError(f"skin {index} : nœud {j} inexistant.")
        count = len(node_ids)
        if "inverseBindMatrices" in skin:
            ibm = self.acc(skin["inverseBindMatrices"])
            if ibm.shape[0] < count or ibm.ndim != 3:
                raise ModelImportError(
                    f"skin {index} : inverseBindMatrices insuffisantes."
                )
            ibm = ibm[:count]
        else:
            ibm = np.broadcast_to(np.eye(4, dtype=np.float32), (count, 4, 4))
        jmap = {nid: j for j, nid in enumerate(node_ids)}
        parents = []
        for nid in node_ids:
            p = parent_of[nid]
            while p != -1 and p not in jmap:
                p = parent_of[p]
            parents.append(jmap[p] if p != -1 else -1)
        picked = [nodes[nid] for nid in node_ids]
        return Skeleton(
            joint_names=tuple(nd.name for nd in picked),
            parents=parents,
            inverse_bind_matrices=ibm,
            rest_translation=[nd.translation for nd in picked],
            rest_rotation=[nd.rotation for nd in picked],
            rest_scale=[nd.scale for nd in picked],
            node_indices=node_ids,
            name=str(skin.get("name") or f"skin{index}"),
        )

    # -- animations ----------------------------------------------------------------------------------------
    def animation(
        self,
        index: int,
        anim: dict[str, Any],
        node_count: int,
        clones: dict[int, list[int]] | None = None,
    ) -> AnimationClip:
        samplers = _list(anim, "samplers")
        channels: list[AnimationChannel] = []
        for ci, ch in enumerate(_list(anim, "channels")):
            if not isinstance(ch, dict):
                raise ModelImportError(f"animation {index} : canal {ci} invalide.")
            target = ch.get("target")
            if (
                not isinstance(target, dict)
                or "node" not in target
                or target.get("path") not in _PATHS
            ):
                self.warnings.append(
                    f"animation {index} canal {ci} : cible non prise en charge, ignoré"
                )
                continue
            node = target["node"]
            if (
                isinstance(node, bool)
                or not isinstance(node, int)
                or not 0 <= node < node_count
            ):
                raise ModelImportError(
                    f"animation {index} canal {ci} : nœud cible invalide."
                )
            sampler = _obj(samplers, ch.get("sampler"), "sampler")
            built = self._channel(index, ci, node, str(target["path"]), sampler)
            channels.append(built)
            if (
                built.path == "weights"
            ):  # les nœuds `nom#k` portent les autres primitives du même maillage
                channels.extend(
                    replace(built, target_node=c) for c in (clones or {}).get(node, ())
                )
        duration = max(
            (float(c.times[-1]) for c in channels if len(c.times)), default=0.0
        )
        return AnimationClip(
            name=str(anim.get("name") or f"animation{index}"),
            duration=duration,
            channels=tuple(channels),
        )

    def _channel(
        self, ai: int, ci: int, node: int, path: str, sampler: dict[str, Any]
    ) -> AnimationChannel:
        what = f"animation {ai} canal {ci}"
        interp = str(sampler.get("interpolation", "LINEAR")).lower()
        if interp not in ("linear", "step", "cubicspline"):
            raise ModelImportError(f"{what} : interpolation inconnue {short(interp)}.")
        times = self.acc(sampler.get("input")).astype(np.float32)
        out = self.acc(sampler.get("output")).astype(np.float32)
        if times.ndim != 1 or len(times) == 0:
            raise ModelImportError(f"{what} : instants invalides.")
        if len(times) > 1 and (np.diff(times) < 0).any():
            raise ModelImportError(f"{what} : instants non croissants.")
        k = len(times)
        factor = 3 if interp == "cubicspline" else 1
        if path == "weights":
            total = out.reshape(-1).size
            if total == 0 or total % (k * factor):
                raise ModelImportError(f"{what} : {total} valeurs pour {k} instants.")
            width = total // (k * factor)
            flat = out.reshape(-1, width)
        else:
            width = _PATH_WIDTH[path]
            if out.ndim != 2 or out.shape[1] != width or out.shape[0] != k * factor:
                raise ModelImportError(
                    f"{what} : sortie {out.shape} incompatible avec {k} instants."
                )
            flat = out
        values = flat.reshape(k, 3, width) if factor == 3 else flat
        if path == "rotation" and factor == 1:
            values = np.array(
                [normalize_quaternion(q) for q in values], dtype=np.float32
            )
        return AnimationChannel(node, path, times, values, interp)


def _build_nodes(
    ld: _Loader, mesh_prims: list[list[int]]
) -> tuple[list[Node], list[int], list[int], dict[int, list[int]]]:
    """Retourne (nœuds, parent de chaque nœud glTF, racines, nœuds générés par nœud d'origine) ; les nœuds ajoutés
    pour les primitives en plus de la première viennent après les nœuds du fichier (les indices cibles des
    animations ne bougent pas)."""
    raw_nodes = _list(ld.gltf, "nodes")
    count = len(raw_nodes)
    parent_of = [-1] * count
    for i, nd in enumerate(raw_nodes):
        if not isinstance(nd, dict):
            raise ModelImportError(f"nœud {i} invalide.")
        for c in _list(nd, "children"):
            if isinstance(c, bool) or not isinstance(c, int) or not 0 <= c < count:
                raise ModelImportError(f"nœud {i} : enfant {short(c)} inexistant.")
            if parent_of[c] != -1:
                raise ModelImportError(f"nœud {c} : deux parents.")
            parent_of[c] = i
    nodes: list[Node] = []
    extra: list[Node] = []
    clones: dict[int, list[int]] = {}
    for i, nd in enumerate(raw_nodes):
        t, r, s = (
            _vec(nd, "translation", 3, (0.0, 0.0, 0.0)),
            _vec(nd, "rotation", 4, (0.0, 0.0, 0.0, 1.0)),
            _vec(nd, "scale", 3, (1.0, 1.0, 1.0)),
        )
        if "matrix" in nd:
            m = np.asarray(_vec(nd, "matrix", 16, ()), dtype=np.float64).reshape(4, 4).T
            t, r, s = matrix_to_trs(m)
        mesh_i = nd.get("mesh")
        prims = (
            mesh_prims[mesh_i]
            if mesh_i is not None and 0 <= int(mesh_i) < len(mesh_prims)
            else []
        )
        if mesh_i is not None and not 0 <= int(mesh_i) < len(mesh_prims):
            raise ModelImportError(f"nœud {i} : maillage {short(mesh_i)} inexistant.")
        name = str(nd.get("name") or f"node{i}")
        skin = nd.get("skin")
        weights = (
            tuple(float(w) for w in nd["weights"])
            if isinstance(nd.get("weights"), list)
            else None
        )
        children = [int(c) for c in nd.get("children", [])]
        for k, extra_mesh in enumerate(prims[1:], start=1):
            children.append(count + len(extra))
            clones.setdefault(i, []).append(count + len(extra))
            extra.append(
                Node(
                    name=f"{name}#{k}",
                    mesh_index=extra_mesh,
                    skin_index=skin,
                    weights=weights,
                )
            )
        nodes.append(
            Node(
                name=name,
                mesh_index=prims[0] if prims else None,
                skin_index=skin,
                children=tuple(children),
                translation=(t[0], t[1], t[2]),
                rotation=normalize_quaternion(r),
                scale=(s[0], s[1], s[2]),
                weights=weights,
            )
        )
    scenes = _list(ld.gltf, "scenes")
    if scenes:
        scene = _obj(scenes, ld.gltf.get("scene", 0), "scène")
        roots = [int(x) for x in _list(scene, "nodes")]
    else:
        roots = [i for i in range(count) if parent_of[i] == -1]
    return nodes + extra, parent_of, roots, clones


def load(path: Path) -> Model:
    data = read_limited(path)
    if data[:4] == _GLB_MAGIC:
        gltf, glb_bin = _parse_glb(data)
    else:
        gltf = json.loads(data.decode("utf-8-sig"))
        glb_bin = None
        if not isinstance(gltf, dict):
            raise ModelImportError("document glTF invalide (objet JSON attendu).")
    ld = _Loader(path, gltf, glb_bin)
    ld.check_extensions()
    materials = [
        ld.material(i, _obj(_list(gltf, "materials"), i, "matériau"))
        for i in range(len(_list(gltf, "materials")))
    ]
    meshes: list[Mesh] = []
    mesh_prims: list[list[int]] = []
    for mi, mesh in enumerate(_list(gltf, "meshes")):
        if not isinstance(mesh, dict):
            raise ModelImportError(f"maillage {mi} invalide.")
        mname = str(mesh.get("name") or f"mesh{mi}")
        prims = _list(mesh, "primitives")
        ids: list[int] = []
        for pi, prim in enumerate(prims):
            if not isinstance(prim, dict):
                raise ModelImportError(f"{mname} : primitive {pi} invalide.")
            built = ld.primitive(
                prim, mname if len(prims) == 1 else f"{mname}.{pi}", mesh
            )
            if built is not None:
                ids.append(len(meshes))
                meshes.append(built)
        mesh_prims.append(ids)
    nodes, parent_of, roots, clones = _build_nodes(ld, mesh_prims)
    skeletons = [
        ld.skeleton(i, _obj(_list(gltf, "skins"), i, "skin"), nodes, parent_of)
        for i in range(len(_list(gltf, "skins")))
    ]
    for nd in nodes:
        if nd.skin_index is not None and nd.mesh_index is not None:
            joints = meshes[nd.mesh_index].joints
            if not 0 <= nd.skin_index < len(skeletons):
                raise ModelImportError(
                    f"nœud {nd.name!r} : skin {nd.skin_index} inexistant."
                )
            if (
                joints is not None
                and joints.size
                and int(joints.max()) >= skeletons[nd.skin_index].joint_count
            ):
                raise ModelImportError(
                    f"{meshes[nd.mesh_index].name} : JOINTS_0 dépasse le nombre d'os du skin."
                )
    animations = [
        ld.animation(
            i, _obj(_list(gltf, "animations"), i, "animation"), len(nodes), clones
        )
        for i in range(len(_list(gltf, "animations")))
    ]
    return Model(
        nodes=tuple(nodes),
        root_nodes=tuple(roots),
        meshes=tuple(meshes),
        materials=tuple(materials),
        skeletons=tuple(skeletons),
        animations=tuple(animations),
        source=path,
        warnings=tuple(ld.warnings),
        dependencies=finalize_dependencies(
            [*ld.deps, *material_texture_paths(materials)]
        ),
    )
