"""Importeur glTF / GLB de Kut-3D : quad à deux os, peau, morph target, animations, textures."""

from __future__ import annotations

import base64
import json
import struct
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from kut3d import ModelImportError, load_model
from kut3d.importers.gltf_accessors import read_accessor
from tests.kut3d_samples import PNG_BYTES, SQRT_HALF, gltf_document, write_gltf

KINDS = ["glb", "bin", "data"]


@pytest.fixture(params=KINDS)
def quad(request: pytest.FixtureRequest, tmp_path: Path):  # type: ignore[no-untyped-def]
    return load_model(write_gltf(tmp_path, request.param), center=False)


def test_geometry_and_vertex_attributes(quad) -> None:  # type: ignore[no-untyped-def]
    assert len(quad.meshes) == 1
    mesh = quad.meshes[0]
    assert mesh.positions.dtype == np.float32 and mesh.indices.dtype == np.uint32
    assert mesh.positions.tolist() == [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]]
    assert mesh.indices.tolist() == [[0, 1, 2], [0, 2, 3]]
    assert mesh.normals is not None and np.allclose(mesh.normals, [0, 0, 1])
    # accessor entrelacé (stride de 12 octets pour 8 octets utiles)
    assert mesh.uvs is not None and mesh.uvs.tolist() == [
        [0, 1],
        [1, 1],
        [1, 0],
        [0, 0],
    ]
    # COLOR_0 : octets normalisés -> float32
    assert mesh.colors is not None and mesh.colors.dtype == np.float32
    assert np.allclose(mesh.colors, [1, 0, 0, 1])
    assert quad.warnings == ()


def test_skin_weights_and_skeleton(quad) -> None:  # type: ignore[no-untyped-def]
    mesh = quad.meshes[0]
    assert mesh.joints is not None and mesh.weights is not None
    assert mesh.joints.dtype == np.uint16 and mesh.weights.dtype == np.float32
    assert mesh.joints[:, 0].tolist() == [0, 0, 1, 1]
    assert np.allclose(mesh.weights, [1, 0, 0, 0])
    assert len(quad.skeletons) == 1
    sk = quad.skeletons[0]
    assert sk.name == "rig" and sk.joint_names == ("root", "tip")
    assert sk.parents.tolist() == [-1, 0]
    assert sk.node_indices.tolist() == [1, 2]
    assert sk.inverse_bind_matrices.shape == (2, 4, 4)
    assert np.allclose(sk.inverse_bind_matrices[0], np.eye(4))
    # colonnes -> lignes : la translation de l'os « tip » est dans la dernière colonne
    assert np.allclose(sk.inverse_bind_matrices[1][:3, 3], [0, -1, 0])
    assert np.allclose(sk.rest_translation, [[0, 0, 0], [0, 1, 0]])
    assert np.allclose(sk.rest_rotation, [[0, 0, 0, 1]] * 2)
    assert np.allclose(sk.rest_scale, 1.0)
    assert quad.nodes[0].skin_index == 0 and quad.nodes[0].mesh_index == 0


def test_morph_target(quad) -> None:  # type: ignore[no-untyped-def]
    mesh = quad.meshes[0]
    assert len(mesh.morph_targets) == 1
    t = mesh.morph_targets[0]
    assert t.name == "smile"
    assert t.position_deltas.shape == (4, 3)
    assert np.allclose(t.position_deltas, [0, 0, 0.5])
    assert t.normal_deltas is None
    assert mesh.morph_weights == (0.25,)


def test_material_and_textures(quad, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    mat = quad.materials[0]
    assert mat.name == "skin"
    assert np.allclose(mat.base_color, [1, 0.5, 0.25, 0.5])
    assert mat.metallic == pytest.approx(0.2) and mat.roughness == pytest.approx(0.8)
    assert np.allclose(mat.emissive, [0.1, 0.2, 0.3])
    assert mat.alpha_mode == "blend" and mat.double_sided is True
    assert mat.alpha_cutoff == 0.5
    tex = mat.base_color_texture
    assert tex is not None and tex.name == "albedo"
    assert tex.data == PNG_BYTES and tex.path is None and tex.mime_type == "image/png"
    n = mat.normal_texture
    assert n is not None and n.data is None  # uri percent-encodée « normal%20map.png »
    assert (
        n.path is not None
        and n.path.name == "normal map.png"
        and n.path.parent == tmp_path.resolve()
    )
    assert quad.meshes[0].material_index == 0


def test_animations(quad) -> None:  # type: ignore[no-untyped-def]
    assert [a.name for a in quad.animations] == ["wave", "slide"]
    wave, slide = quad.animations
    assert wave.duration == pytest.approx(1.0)
    rot, weights = wave.channels
    assert (rot.target_node, rot.path, rot.interpolation) == (2, "rotation", "linear")
    assert rot.times.tolist() == [0.0, 1.0]
    assert np.allclose(
        rot.values, [[0, 0, 0, 1], [0, 0, SQRT_HALF, SQRT_HALF]], atol=1e-6
    )
    assert (weights.target_node, weights.path, weights.interpolation) == (
        0,
        "weights",
        "step",
    )
    assert weights.values.shape == (2, 1) and weights.values[:, 0].tolist() == [
        0.0,
        1.0,
    ]
    (tr,) = slide.channels
    assert (tr.target_node, tr.path, tr.interpolation) == (
        1,
        "translation",
        "cubicspline",
    )
    # (K, 3, C) : tangente d'entrée, valeur, tangente de sortie
    assert tr.values.shape == (2, 3, 3)
    assert tr.values[:, 1, 0].tolist() == [1.0, 2.0]
    assert tr.values[0, 2, 0] == 0.5  # tangente de sortie de la première clé


def test_three_transports_give_identical_models(tmp_path: Path) -> None:
    models = []
    for kind in KINDS:
        sub = tmp_path / kind
        sub.mkdir()
        models.append(load_model(write_gltf(sub, kind)))
    first = models[0].meshes[0]
    for other in models[1:]:
        assert np.array_equal(first.positions, other.meshes[0].positions)
        assert np.array_equal(first.indices, other.meshes[0].indices)
    assert models[0].triangle_count == 2


def test_hierarchy_roots_and_bounds_at_rest(tmp_path: Path) -> None:
    model = load_model(write_gltf(tmp_path), center=False)
    assert model.root_nodes == (0, 1)
    assert model.nodes[1].children == (2,)
    lo, hi = model.bounds()
    assert np.allclose(lo, [0, 0, 0]) and np.allclose(hi, [1, 1, 0])


def test_node_matrix_is_decomposed(tmp_path: Path) -> None:
    def mutate(doc: dict[str, Any]) -> None:
        # rotation de 90° autour de Z, échelle 2, translation (3, 0, 0), en colonnes
        doc["nodes"][2] = {
            "name": "tip",
            "matrix": [0, 2, 0, 0, -2, 0, 0, 0, 0, 0, 2, 0, 3, 0, 0, 1],
        }

    nd = load_model(write_gltf(tmp_path, mutate=mutate)).nodes[2]
    assert np.allclose(nd.translation, [3, 0, 0])
    assert np.allclose(nd.scale, [2, 2, 2])
    assert np.allclose(nd.rotation, [0, 0, SQRT_HALF, SQRT_HALF], atol=1e-6)


def test_mesh_with_two_primitives_gets_child_nodes(tmp_path: Path) -> None:
    def mutate(doc: dict[str, Any]) -> None:
        prim = doc["meshes"][0]["primitives"][0]
        doc["meshes"][0]["primitives"].append(dict(prim))

    model = load_model(write_gltf(tmp_path, mutate=mutate))
    assert len(model.meshes) == 2
    assert model.nodes[0].mesh_index == 0
    extra = model.nodes[model.nodes[0].children[-1]]
    assert extra.name == "Quad#1" and extra.mesh_index == 1 and extra.skin_index == 0
    assert model.triangle_count == 4


def test_primitive_without_indices_and_without_normals(tmp_path: Path) -> None:
    def mutate(doc: dict[str, Any]) -> None:
        prim = doc["meshes"][0]["primitives"][0]
        del prim["indices"], prim["attributes"]["NORMAL"]
        # 4 sommets non indexés : pas un multiple de 3
        doc["accessors"][prim["attributes"]["POSITION"]]["count"] = 3
        for k in ("NORMAL",):
            prim["attributes"].pop(k, None)
        for k in ("TEXCOORD_0", "COLOR_0", "JOINTS_0", "WEIGHTS_0"):
            del prim["attributes"][k]
        del prim["targets"]
        del doc["skins"], doc["nodes"][0]["skin"], doc["animations"]
        doc.pop("scenes")
        doc.pop("scene")

    mesh = load_model(write_gltf(tmp_path, mutate=mutate)).meshes[0]
    assert mesh.indices.tolist() == [[0, 1, 2]]
    assert mesh.normals is not None and np.allclose(mesh.normals, [0, 0, 1])


def test_non_triangle_primitive_is_ignored_with_warning(tmp_path: Path) -> None:
    def mutate(doc: dict[str, Any]) -> None:
        doc["meshes"][0]["primitives"][0]["mode"] = 1

    model = load_model(write_gltf(tmp_path, mutate=mutate))
    assert model.meshes == () and model.nodes[0].mesh_index is None
    assert any("mode 1" in w for w in model.warnings)


def test_required_unknown_extension_is_an_error(tmp_path: Path) -> None:
    def mutate(doc: dict[str, Any]) -> None:
        doc["extensionsRequired"] = ["KHR_draco_mesh_compression"]
        doc["extensionsUsed"] = ["KHR_draco_mesh_compression"]

    with pytest.raises(ModelImportError, match="KHR_draco_mesh_compression") as err:
        load_model(write_gltf(tmp_path, mutate=mutate))
    assert "Draco" in str(err.value)


def test_optional_unknown_extension_warns(tmp_path: Path) -> None:
    def mutate(doc: dict[str, Any]) -> None:
        doc["extensionsUsed"] = [
            "KHR_lights_punctual",
            "KHR_materials_emissive_strength",
        ]
        doc["materials"][0]["extensions"] = {
            "KHR_materials_emissive_strength": {"emissiveStrength": 2.0}
        }

    model = load_model(write_gltf(tmp_path, mutate=mutate))
    assert any("KHR_lights_punctual" in w for w in model.warnings)
    assert not any("emissive_strength" in w for w in model.warnings)
    assert np.allclose(model.materials[0].emissive, [0.2, 0.4, 0.6])


def test_missing_external_image_is_a_warning(tmp_path: Path) -> None:
    path = write_gltf(tmp_path, "bin")
    (tmp_path / "normal map.png").unlink()
    model = load_model(path)
    assert model.materials[0].normal_texture is None
    assert any("texture introuvable" in w for w in model.warnings)


def test_missing_external_buffer(tmp_path: Path) -> None:
    path = write_gltf(tmp_path, "bin")
    (tmp_path / "quad.bin").unlink()
    with pytest.raises(ModelImportError, match="introuvable"):
        load_model(path)


def test_external_buffer_cannot_escape_the_model_folder(tmp_path: Path) -> None:
    folder = tmp_path / "m"
    folder.mkdir()
    path = write_gltf(folder, "bin")
    (tmp_path / "quad.bin").write_bytes((folder / "quad.bin").read_bytes())
    doc = json.loads(path.read_text())
    doc["buffers"][0]["uri"] = "../quad.bin"
    path.write_text(json.dumps(doc))
    with pytest.raises(ModelImportError, match="hors du dossier"):
        load_model(path)
    doc["buffers"][0]["uri"] = "http://example.com/quad.bin"
    path.write_text(json.dumps(doc))
    with pytest.raises(ModelImportError, match="refusée"):
        load_model(path)


def test_truncated_glb_is_a_clean_error(tmp_path: Path) -> None:
    path = write_gltf(tmp_path)
    data = path.read_bytes()
    for cut in (5, 19, 100, len(data) // 2, len(data) - 1):
        path.write_bytes(data[:cut])
        with pytest.raises(ModelImportError):
            load_model(path)


def test_truncated_buffer_is_a_clean_error(tmp_path: Path) -> None:
    path = write_gltf(tmp_path, "bin")
    blob = (tmp_path / "quad.bin").read_bytes()
    (tmp_path / "quad.bin").write_bytes(blob[:-40])
    with pytest.raises(ModelImportError, match="tronqué"):
        load_model(path)


@pytest.mark.parametrize(
    "garbage",
    [b"{not json", b"[]", b"{}", b'{"asset": {"version": "1.0"}}', b"\xff\xfe\x00"],
)
def test_corrupt_gltf_json(tmp_path: Path, garbage: bytes) -> None:
    path = tmp_path / "bad.gltf"
    path.write_bytes(garbage)
    with pytest.raises(ModelImportError):
        load_model(path)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d["accessors"][0].update(count=99999),  # dépasse le bufferView
        lambda d: d["bufferViews"][0].update(buffer=7),
        lambda d: d["meshes"][0]["primitives"][0].update(indices=500),
        lambda d: d["nodes"][1].update(
            children=[1]
        ),  # cycle (un nœud enfant de lui-même)
        lambda d: d["nodes"][2].update(children=[1]),  # cycle racine -> tip -> racine
        lambda d: d["nodes"][0].update(children=[2]),  # tip a deux parents
        lambda d: d["skins"][0].update(joints=[1, 1]),
        lambda d: d["skins"][0].update(joints=[1, 99]),
        lambda d: d["animations"][0]["channels"][0]["target"].update(node=99),
        lambda d: d["meshes"][0]["primitives"][0]["attributes"].update(POSITION="x"),
        lambda d: d["accessors"][0].update(componentType=1234),
        lambda d: d["accessors"][0].update(type="VEC9"),
        lambda d: d.update(scenes=[{"nodes": [42]}]),
        lambda d: d["accessors"][6].update(count=2**40),
    ],
)
def test_structural_errors_are_clean(tmp_path: Path, mutate) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ModelImportError):
        load_model(write_gltf(tmp_path, mutate=mutate))


def test_joint_index_beyond_skin_is_an_error(tmp_path: Path) -> None:
    def mutate(doc: dict[str, Any]) -> None:
        doc["skins"][0]["joints"] = [1]
        del doc["skins"][0]["inverseBindMatrices"]

    with pytest.raises(ModelImportError, match="JOINTS_0"):
        load_model(write_gltf(tmp_path, mutate=mutate))


def test_sparse_accessor() -> None:
    doc, blob = gltf_document()
    values = np.float32([[7, 8, 9]]).tobytes()
    indices = np.uint16([2]).tobytes()
    off = len(blob)
    blob += indices + b"\x00\x00" + values
    doc["bufferViews"] += [
        {"buffer": 0, "byteOffset": off, "byteLength": 2},
        {"buffer": 0, "byteOffset": off + 4, "byteLength": 12},
    ]
    doc["accessors"].append(
        {
            "componentType": 5126,
            "count": 4,
            "type": "VEC3",
            "sparse": {
                "count": 1,
                "indices": {
                    "bufferView": len(doc["bufferViews"]) - 2,
                    "componentType": 5123,
                },
                "values": {"bufferView": len(doc["bufferViews"]) - 1},
            },
        }
    )
    out = read_accessor(doc, [blob], len(doc["accessors"]) - 1)
    assert out.shape == (4, 3)
    assert out[2].tolist() == [7, 8, 9] and not out[[0, 1, 3]].any()
    # sparse par-dessus un accessor existant
    doc["accessors"][-1]["bufferView"] = 0
    base = read_accessor(doc, [blob], 0)  # POSITION : le quad
    patched = read_accessor(doc, [blob], len(doc["accessors"]) - 1)
    assert patched[2].tolist() == [7, 8, 9] and np.array_equal(
        patched[[0, 1, 3]], base[[0, 1, 3]]
    )
    # indice hors limites
    doc["accessors"][-1]["sparse"]["count"] = 1
    bad = blob[:off] + np.uint16([9]).tobytes() + blob[off + 2 :]
    with pytest.raises(ModelImportError, match="hors limites"):
        read_accessor(doc, [bad], len(doc["accessors"]) - 1)


def test_normalized_short_accessor_and_quantized_uv(tmp_path: Path) -> None:
    doc, blob = gltf_document()
    data = np.int16([-32768, 32767, 0, 16384]).tobytes()
    off = len(blob)
    blob += data
    doc["bufferViews"].append({"buffer": 0, "byteOffset": off, "byteLength": len(data)})
    doc["accessors"].append(
        {
            "bufferView": len(doc["bufferViews"]) - 1,
            "componentType": 5122,
            "count": 2,
            "type": "VEC2",
            "normalized": True,
        }
    )
    out = read_accessor(doc, [blob], len(doc["accessors"]) - 1)
    assert out.dtype == np.float32
    assert np.allclose(out, [[-1.0, 1.0], [0.0, 16384 / 32767]])


def test_data_uri_must_be_base64(tmp_path: Path) -> None:
    path = write_gltf(tmp_path, "data")
    doc = json.loads(path.read_text())
    doc["buffers"][0]["uri"] = "data:text/plain,hello"
    path.write_text(json.dumps(doc))
    with pytest.raises(ModelImportError, match="base64"):
        load_model(path)
    doc["buffers"][0]["uri"] = (
        "data:application/octet-stream;base64," + base64.b64encode(b"short").decode()
    )
    path.write_text(json.dumps(doc))
    with pytest.raises(ModelImportError, match="tronqué"):
        load_model(path)


def test_glb_chunk_longer_than_file_is_refused(tmp_path: Path) -> None:
    path = write_gltf(tmp_path)
    data = bytearray(path.read_bytes())
    struct.pack_into("<I", data, 12, 10**9)  # longueur du chunk JSON
    path.write_bytes(bytes(data))
    with pytest.raises(ModelImportError, match="chunk"):
        load_model(path)


def test_animation_interpolation_and_counts_validated(tmp_path: Path) -> None:
    def bad_interp(doc: dict[str, Any]) -> None:
        doc["animations"][0]["samplers"][0]["interpolation"] = "BEZIER"

    with pytest.raises(ModelImportError, match="interpolation"):
        load_model(write_gltf(tmp_path, mutate=bad_interp))

    def bad_count(doc: dict[str, Any]) -> None:
        doc["animations"][1]["samplers"][0]["interpolation"] = (
            "LINEAR"  # 6 valeurs pour 2 instants
        )

    with pytest.raises(ModelImportError, match="incompatible"):
        load_model(write_gltf(tmp_path, mutate=bad_count))


def test_unsupported_animation_target_is_skipped(tmp_path: Path) -> None:
    def mutate(doc: dict[str, Any]) -> None:
        doc["animations"][0]["channels"].append(
            {"sampler": 0, "target": {"path": "pointer", "extensions": {}}}
        )

    model = load_model(write_gltf(tmp_path, mutate=mutate))
    assert len(model.animations[0].channels) == 2
    assert any("cible non prise en charge" in w for w in model.warnings)
