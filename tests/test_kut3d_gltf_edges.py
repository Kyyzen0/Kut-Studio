"""glTF : cas limites (avertissements, champs optionnels, erreurs de structure)."""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pytest

from kut3d import ModelImportError, load_model
from tests.kut3d_samples import gltf_document, write_gltf

Mutation = Callable[[dict[str, Any]], None]


def _load(tmp_path: Path, mutate: Mutation, kind: str = "glb"):  # type: ignore[no-untyped-def]
    return load_model(write_gltf(tmp_path, kind, mutate=mutate))


def test_warnings_for_unsupported_features(tmp_path: Path) -> None:
    def mutate(doc: dict[str, Any]) -> None:
        attrs = doc["meshes"][0]["primitives"][0]["attributes"]
        attrs["TEXCOORD_1"] = attrs["TEXCOORD_0"]
        attrs["JOINTS_1"] = attrs["JOINTS_0"]
        doc["cameras"] = [{"type": "perspective"}]
        doc["textures"].append({})  # texture sans source
        doc["images"].append({"name": "vide"})  # image sans uri ni bufferView
        doc["textures"].append({"source": 2})
        doc["materials"][0]["occlusionTexture"] = {"index": 2}
        doc["materials"][0]["emissiveTexture"] = {"index": 3}
        doc["materials"][0]["alphaMode"] = "WEIRD"

    model = _load(tmp_path, mutate)
    text = "\n".join(model.warnings)
    for fragment in (
        "TEXCOORD_1",
        "JOINTS_1",
        "caméras",
        "sans source",
        "sans uri ni bufferView",
        "alphaMode",
    ):
        assert fragment in text
    assert model.materials[0].alpha_mode == "opaque"
    assert model.materials[0].occlusion_texture is None


def test_primitive_without_position_is_skipped(tmp_path: Path) -> None:
    def mutate(doc: dict[str, Any]) -> None:
        del doc["meshes"][0]["primitives"][0]["attributes"]["POSITION"]

    model = _load(tmp_path, mutate)
    assert model.meshes == () and any("sans POSITION" in w for w in model.warnings)


def test_vec3_colors_node_weights_and_scene_selection(tmp_path: Path) -> None:
    def mutate(doc: dict[str, Any]) -> None:
        doc["accessors"][3].update(
            type="VEC3"
        )  # COLOR_0 en RGB (12 octets sur 16 : lecture partielle)
        doc["nodes"][0]["weights"] = [0.75]
        doc["meshes"][0]["weights"] = [
            0.1,
            0.2,
        ]  # incohérent avec 1 forme : remis à zéro
        doc["scenes"] = [{"nodes": [1]}, {"nodes": [0, 1]}]
        doc["scene"] = 1

    model = _load(tmp_path, mutate)
    colors = model.meshes[0].colors
    assert colors is not None and np.allclose(colors[:, 3], 1.0)  # alpha complété
    assert model.nodes[0].weights == (0.75,)
    assert model.meshes[0].morph_weights == (0.0,)
    assert model.root_nodes == (0, 1)


def test_scene_index_out_of_range(tmp_path: Path) -> None:
    with pytest.raises(ModelImportError, match="scène"):
        _load(tmp_path, lambda d: d.update(scene=5))


def test_skin_without_ibm_defaults_to_identity(tmp_path: Path) -> None:
    def mutate(doc: dict[str, Any]) -> None:
        del doc["skins"][0]["inverseBindMatrices"]

    sk = _load(tmp_path, mutate).skeletons[0]
    assert np.allclose(sk.inverse_bind_matrices, np.eye(4))


def test_skin_joint_parent_skips_non_joint_nodes(tmp_path: Path) -> None:
    def mutate(doc: dict[str, Any]) -> None:
        # root -> mid (pas un os) -> tip : le parent de tip dans le squelette reste root
        doc["nodes"].append({"name": "mid", "children": [2]})
        doc["nodes"][1]["children"] = [3]

    sk = _load(tmp_path, mutate).skeletons[0]
    assert sk.parents.tolist() == [-1, 0]


def test_skin_needs_enough_matrices_and_joints(tmp_path: Path) -> None:
    def few(doc: dict[str, Any]) -> None:
        doc["accessors"][doc["skins"][0]["inverseBindMatrices"]]["count"] = 1

    with pytest.raises(ModelImportError, match="inverseBindMatrices"):
        _load(tmp_path, few)

    def empty(doc: dict[str, Any]) -> None:
        doc["skins"][0]["joints"] = []

    with pytest.raises(ModelImportError, match="sans os"):
        _load(tmp_path, empty)


def test_mesh_skin_index_out_of_range(tmp_path: Path) -> None:
    with pytest.raises(ModelImportError, match="skin"):
        _load(tmp_path, lambda d: d["nodes"][0].update(skin=4))


@pytest.mark.parametrize(
    "mutate, fragment",
    [
        (lambda d: d["animations"][0]["samplers"][0].update(input=99), "accessor"),
        (lambda d: d["animations"][0]["channels"][0].update(sampler=9), "sampler"),
        (
            lambda d: d["accessors"][d["animations"][0]["samplers"][0]["input"]].update(
                count=0
            ),
            "instants",
        ),
        (lambda d: d["animations"][0].update(channels=["x"]), "invalide"),
        (
            lambda d: d["animations"][0]["channels"][0]["target"].update(node=True),
            "nœud cible",
        ),
        (lambda d: d.update(nodes=["x"]), "invalide"),
        (lambda d: d["nodes"][0].update(mesh=9), "maillage"),
        (lambda d: d["nodes"][0].update(translation=[1, 2]), "translation"),
        (lambda d: d["meshes"][0]["primitives"][0].update(material=7), "matériau"),
        (lambda d: d["meshes"][0]["primitives"][0]["targets"].append("x"), "forme"),
        (lambda d: d["meshes"][0].update(primitives=["x"]), "primitive"),
        (lambda d: d.update(meshes=["x"]), "maillage"),
        (lambda d: d.update(buffers=["x"]), "buffer"),
        (lambda d: d["buffers"][0].update(byteLength=-4), "byteLength"),
        (lambda d: d.update(nodes={"a": 1}), "liste"),
        (lambda d: d["textures"][0].update(source=9), "image"),
        (
            lambda d: d["materials"][0]["pbrMetallicRoughness"].update(
                baseColorFactor=[1]
            ),
            "baseColorFactor",
        ),
        (lambda d: d["accessors"][4].update(type="MAT2"), "MAT2"),
    ],
)
def test_malformed_documents(tmp_path: Path, mutate: Mutation, fragment: str) -> None:
    with pytest.raises(ModelImportError, match=fragment):
        _load(tmp_path, mutate)


def test_animation_times_must_not_decrease(tmp_path: Path) -> None:
    path = write_gltf(tmp_path, "bin")
    doc, blob = gltf_document()
    times_acc = doc["animations"][0]["samplers"][0]["input"]
    view = doc["bufferViews"][doc["accessors"][times_acc]["bufferView"]]
    patched = bytearray((tmp_path / "quad.bin").read_bytes())
    struct.pack_into("<2f", patched, view["byteOffset"], 1.0, 0.5)
    (tmp_path / "quad.bin").write_bytes(bytes(patched))
    with pytest.raises(ModelImportError, match="non croissants"):
        load_model(path)


def test_not_a_glb_or_json_object(tmp_path: Path) -> None:
    path = write_gltf(tmp_path)
    data = bytearray(path.read_bytes())
    struct.pack_into("<I", data, 4, 1)  # version GLB 1
    path.write_bytes(bytes(data))
    with pytest.raises(ModelImportError, match="version GLB"):
        load_model(path)
    short = tmp_path / "s.glb"
    short.write_bytes(b"glTF\x02\x00\x00\x00")
    with pytest.raises(ModelImportError, match="tronqué"):
        load_model(short)


def test_glb_without_json_chunk(tmp_path: Path) -> None:
    path = tmp_path / "nojson.glb"
    body = struct.pack("<II", 4, 0x004E4942) + b"\x00" * 4
    path.write_bytes(struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body)
    with pytest.raises(ModelImportError, match="JSON"):
        load_model(path)


def test_buffer_without_uri_outside_glb(tmp_path: Path) -> None:
    def mutate(doc: dict[str, Any]) -> None:
        pass

    path = write_gltf(tmp_path, "bin", mutate=mutate)
    import json

    doc = json.loads(path.read_text())
    del doc["buffers"][0]["uri"]
    path.write_text(json.dumps(doc))
    with pytest.raises(ModelImportError, match="sans uri"):
        load_model(path)


def test_empty_gltf_is_an_empty_model(tmp_path: Path) -> None:
    path = tmp_path / "empty.gltf"
    path.write_text('{"asset": {"version": "2.0"}}')
    model = load_model(path)
    assert model.meshes == () and model.nodes == () and model.triangle_count == 0
    assert model.bounds()[0].tolist() == [0, 0, 0]


def test_morph_target_normals_and_weights_animation_width(tmp_path: Path) -> None:
    def mutate(doc: dict[str, Any]) -> None:
        target = doc["meshes"][0]["primitives"][0]["targets"][0]
        target["NORMAL"] = target["POSITION"]

    model = _load(tmp_path, mutate)
    nd = model.meshes[0].morph_targets[0].normal_deltas
    assert nd is not None and nd.shape == (4, 3)
