"""Importeur OBJ + MTL de Kut-3D."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from kut3d import ModelImportError, load_model
from tests.kut3d_samples import PNG_BYTES, write_cube_obj


def test_cube_shapes_dtypes_and_dedup(tmp_path: Path) -> None:
    model = load_model(write_cube_obj(tmp_path))
    assert len(model.meshes) == 1
    mesh = model.meshes[0]
    # 8 positions, 4 uv, 6 normales : 24 triplets (v, vt, vn) distincts, 6 quads -> 12 triangles
    assert mesh.positions.shape == (24, 3) and mesh.positions.dtype == np.float32
    assert mesh.indices.shape == (12, 3) and mesh.indices.dtype == np.uint32
    assert mesh.normals is not None and mesh.normals.shape == (24, 3)
    assert mesh.uvs is not None and mesh.uvs.shape == (24, 2)
    assert model.triangle_count == 12
    assert model.warnings == ()
    lo, hi = model.bounds()
    assert np.allclose(lo, -1) and np.allclose(hi, 1)


def test_uv_v_axis_is_flipped_to_model_convention(tmp_path: Path) -> None:
    mesh = load_model(write_cube_obj(tmp_path)).meshes[0]
    assert mesh.uvs is not None and mesh.normals is not None
    # face +z, coin (-1, -1, 1) : vt 1 = (0, 0) dans le fichier, donc (0, 1) dans le modèle (V vers le bas)
    corner = (np.abs(mesh.positions - [-1, -1, 1]).sum(axis=1) == 0) & (
        mesh.normals[:, 2] == 1
    )
    assert mesh.uvs[corner].tolist() == [[0.0, 1.0]]
    # coin (-1, 1, 1) de la même face : vt 4 = (0, 1) -> (0, 0)
    corner = (np.abs(mesh.positions - [-1, 1, 1]).sum(axis=1) == 0) & (
        mesh.normals[:, 2] == 1
    )
    assert mesh.uvs[corner].tolist() == [[0.0, 0.0]]


def test_mtl_material_and_textures(tmp_path: Path) -> None:
    model = load_model(write_cube_obj(tmp_path))
    assert len(model.materials) == 1
    mat = model.materials[0]
    assert mat.name == "red"
    assert np.allclose(mat.base_color, [0.9, 0.1, 0.2, 1.0])
    assert np.allclose(mat.emissive, [0.1, 0.2, 0.3])
    assert mat.alpha_mode == "opaque"
    assert (
        mat.base_color_texture is not None
        and mat.base_color_texture.path == (tmp_path / "tex.png").resolve()
    )
    # option -bm ignorée, nom de fichier avec espace conservé
    assert mat.normal_texture is not None and mat.normal_texture.path is not None
    assert mat.normal_texture.path.name == "normal map.png"
    assert model.meshes[0].material_index == 0
    assert (
        mat.base_color_texture.data is None
    )  # jamais de décodage ni de lecture d'image


def test_mtl_transparency(tmp_path: Path) -> None:
    obj = write_cube_obj(tmp_path)
    mtl = obj.with_suffix(".mtl")
    mtl.write_text("newmtl red\nKd 1 1 1\nTr 0.75\n")
    mat = load_model(obj).materials[0]
    assert mat.base_color[3] == pytest.approx(0.25)
    assert mat.alpha_mode == "blend"


def test_missing_mtl_gives_grey_material_and_warning(tmp_path: Path) -> None:
    obj = write_cube_obj(tmp_path, mtl=False, texture=False)
    obj.write_text("mtllib absent.mtl\n" + obj.read_text())
    model = load_model(obj)
    assert any("MTL introuvable" in w for w in model.warnings)
    assert model.materials[0].name == "red"
    assert np.allclose(model.materials[0].base_color, [0.8, 0.8, 0.8, 1.0])


def test_usemtl_without_any_mtllib_warns(tmp_path: Path) -> None:
    model = load_model(write_cube_obj(tmp_path, mtl=False, texture=False))
    assert any("absent du MTL" in w for w in model.warnings)
    assert len(model.materials) == 1


def test_missing_texture_is_a_warning_not_an_error(tmp_path: Path) -> None:
    obj = write_cube_obj(tmp_path, texture=False, map_line="map_Kd")
    obj.with_suffix(".mtl").write_text("newmtl red\nKd 1 0 0\nmap_Kd nowhere.png\n")
    model = load_model(obj)
    assert model.materials[0].base_color_texture is None
    assert any("texture introuvable" in w for w in model.warnings)


def test_texture_path_is_confined_to_model_folder(tmp_path: Path) -> None:
    outside = tmp_path / "secret.png"
    outside.write_bytes(PNG_BYTES)
    folder = tmp_path / "model"
    folder.mkdir()
    obj = write_cube_obj(folder, texture=False)
    obj.with_suffix(".mtl").write_text("newmtl red\nmap_Kd ../secret.png\n")
    model = load_model(obj)
    assert model.materials[0].base_color_texture is None
    assert any("hors du dossier" in w for w in model.warnings)


def test_windows_absolute_texture_path_falls_back_to_basename(tmp_path: Path) -> None:
    obj = write_cube_obj(tmp_path, texture=False)
    (tmp_path / "tex.png").write_bytes(PNG_BYTES)
    obj.with_suffix(".mtl").write_text(
        "newmtl red\nmap_Kd C:\\Users\\moi\\textures\\tex.png\n"
    )
    model = load_model(obj)
    ref = model.materials[0].base_color_texture
    assert ref is not None and ref.path == (tmp_path / "tex.png").resolve()
    assert model.warnings == ()


def test_normals_are_computed_when_absent(tmp_path: Path) -> None:
    mesh = load_model(write_cube_obj(tmp_path, normals=False, texture=False)).meshes[0]
    assert mesh.normals is not None
    assert np.allclose(np.linalg.norm(mesh.normals, axis=1), 1.0, atol=1e-5)
    # lisses et sortantes : sur un cube soudé par position, la normale d'un coin pointe vers le coin
    assert (np.sum(mesh.normals * mesh.positions, axis=1) > 0.5).all()
    # soudées par position : une position a une seule normale malgré les coutures d'UV
    keyed = {
        tuple(np.round(p, 4)): tuple(np.round(n, 4))
        for p, n in zip(mesh.positions, mesh.normals)
    }
    assert len(keyed) == 8


def test_negative_indices_and_polygon_fan(tmp_path: Path) -> None:
    obj = tmp_path / "poly.obj"
    obj.write_text(
        "v 0 0 0\nv 1 0 0\nv 2 1 0\nv 1 2 0\nv 0 1 0\n"
        "f -5 -4 -3 -2 -1\n"  # pentagone : 3 triangles en éventail
        "v 5 5 5\nf -1 1 2\n"  # indice relatif après un nouveau sommet
        "f 1 2 3\n"
    )
    mesh = load_model(obj).meshes[0]
    assert mesh.triangle_count == 5
    assert mesh.positions.shape[0] == 6
    pts = [(0, 0, 0), (1, 0, 0), (2, 1, 0), (1, 2, 0), (0, 1, 0)]
    expected = [(pts[0], pts[k], pts[k + 1]) for k in (1, 2, 3)]
    got = [
        tuple(tuple(mesh.positions[i].astype(int).tolist()) for i in tri)
        for tri in mesh.indices[:3]
    ]
    assert got == expected
    # `f -1 1 2` après `v 5 5 5` : -1 désigne ce dernier sommet
    last = [tuple(mesh.positions[i].tolist()) for i in mesh.indices[3]]
    assert last[0] == (5.0, 5.0, 5.0)


def test_v_slash_slash_vn_form(tmp_path: Path) -> None:
    obj = tmp_path / "t.obj"
    obj.write_text("v 0 0 0\nv 1 0 0\nv 0 1 0\nvn 0 0 1\nf 1//1 2//1 3//1\n")
    mesh = load_model(obj).meshes[0]
    assert mesh.uvs is None
    assert mesh.normals is not None and np.allclose(mesh.normals, [0, 0, 1])


def test_split_by_material(tmp_path: Path) -> None:
    obj = tmp_path / "two.obj"
    obj.write_text(
        "mtllib two.mtl\nv 0 0 0\nv 1 0 0\nv 0 1 0\nv 1 1 0\n"
        "usemtl a\nf 1 2 3\nusemtl b\nf 2 4 3\nusemtl a\nf 1 2 4\n"
    )
    (tmp_path / "two.mtl").write_text("newmtl a\nKd 1 0 0\nnewmtl b\nKd 0 0 1\n")
    model = load_model(obj)
    assert [m.material_index for m in model.meshes] == [0, 1]
    assert [m.triangle_count for m in model.meshes] == [2, 1]
    assert [m.name for m in model.meshes] == ["two:a", "two:b"]
    assert [m.name for m in model.materials] == ["a", "b"]
    assert len(model.nodes) == 2 and model.root_nodes == (0, 1)


def test_no_material_gives_none_index(tmp_path: Path) -> None:
    obj = tmp_path / "p.obj"
    obj.write_text("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n")
    model = load_model(obj)
    assert model.meshes[0].material_index is None and model.materials == ()


@pytest.mark.parametrize(
    "text, fragment",
    [
        ("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 9\n", "hors limites"),
        ("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 0 1 2\n", "indice 0"),
        ("v 0 0 0\nv 1 0 0\nv 0 1 0\nf -9 1 2\n", "hors limites"),
        ("v 0 0 zzz\nf 1 1 1\n", "illisible"),
        ("v 0 0 0\n", "aucune face"),
        ("v 0 0\nf 1 1 1\n", "valeurs attendues"),
        ("v nan 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n", "non finies"),
    ],
)
def test_malformed_obj_raises_clean_error(
    tmp_path: Path, text: str, fragment: str
) -> None:
    obj = tmp_path / "bad.obj"
    obj.write_text(text)
    with pytest.raises(ModelImportError, match=fragment) as err:
        load_model(obj)
    assert str(err.value).startswith("bad.obj")


def test_degenerate_faces_are_skipped_with_warning(tmp_path: Path) -> None:
    obj = tmp_path / "d.obj"
    obj.write_text("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2\nf 1 2 3\n")
    model = load_model(obj)
    assert model.triangle_count == 1
    assert any("moins de 3" in w for w in model.warnings)


def test_oversized_line_is_refused_without_echoing_it(tmp_path: Path) -> None:
    obj = tmp_path / "big.obj"
    obj.write_text("v 0 0 0\nf " + "1 " * 600_000 + "\n")
    with pytest.raises(ModelImportError, match="ligne trop longue") as err:
        load_model(obj)
    assert len(str(err.value)) < 300


def test_binary_garbage_with_obj_extension(tmp_path: Path) -> None:
    obj = tmp_path / "junk.obj"
    obj.write_bytes(bytes(range(256)) * 50)
    with pytest.raises(ModelImportError):
        load_model(obj)


def test_line_continuation_and_comments(tmp_path: Path) -> None:
    obj = tmp_path / "c.obj"
    obj.write_text("# commentaire\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 \\\n 2 \\\n 3\n")
    assert load_model(obj).triangle_count == 1
