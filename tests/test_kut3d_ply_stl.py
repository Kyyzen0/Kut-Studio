"""Importeurs PLY et STL de Kut-3D."""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
import pytest

from kut3d import ModelImportError, load_model
from tests.kut3d_samples import (
    PLY_COLORS,
    PLY_NORMALS,
    PLY_POSITIONS,
    PLY_UV,
    stl_triangles,
    write_ply,
    write_stl,
)


@pytest.mark.parametrize("fmt", ["ascii", "little", "big"])
def test_ply_all_formats_give_the_same_mesh(tmp_path: Path, fmt: str) -> None:
    model = load_model(write_ply(tmp_path / f"{fmt}.ply", fmt))
    mesh = model.meshes[0]
    assert mesh.positions.dtype == np.float32 and mesh.indices.dtype == np.uint32
    assert np.array_equal(mesh.positions, PLY_POSITIONS)
    assert mesh.indices.tolist() == [[0, 1, 2], [0, 2, 3]]
    assert mesh.normals is not None and np.array_equal(mesh.normals, PLY_NORMALS)
    assert mesh.uvs is not None
    assert np.allclose(mesh.uvs, np.stack([PLY_UV[:, 0], 1.0 - PLY_UV[:, 1]], axis=1))
    assert mesh.colors is not None and mesh.colors.shape == (4, 4)
    assert np.allclose(mesh.colors, PLY_COLORS / 255.0, atol=1e-6)
    assert model.triangle_count == 2


@pytest.mark.parametrize("fmt", ["ascii", "little", "big"])
def test_ply_polygons_are_fanned(tmp_path: Path, fmt: str) -> None:
    # un quad puis un triangle : tailles différentes (chemin lent), puis un quad seul (chemin rapide)
    mixed = load_model(
        write_ply(tmp_path / "m.ply", fmt, [[0, 1, 2, 3], [0, 1, 2]])
    ).meshes[0]
    assert mixed.indices.tolist() == [[0, 1, 2], [0, 2, 3], [0, 1, 2]]
    quads = load_model(
        write_ply(tmp_path / "q.ply", fmt, [[0, 1, 2, 3], [3, 2, 1, 0]])
    ).meshes[0]
    assert quads.indices.tolist() == [[0, 1, 2], [0, 2, 3], [3, 2, 1], [3, 1, 0]]


def test_ply_without_normals_computes_them(tmp_path: Path) -> None:
    path = tmp_path / "plain.ply"
    path.write_text(
        "ply\nformat ascii 1.0\nelement vertex 3\nproperty float x\nproperty float y\nproperty float z\n"
        "element face 1\nproperty list uchar int vertex_index\nend_header\n0 0 0\n1 0 0\n0 1 0\n3 0 1 2\n"
    )
    mesh = load_model(path).meshes[0]
    assert mesh.normals is not None and np.allclose(mesh.normals, [0, 0, 1])
    assert mesh.uvs is None and mesh.colors is None


def test_ply_float_colors_and_st_uvs(tmp_path: Path) -> None:
    path = tmp_path / "f.ply"
    path.write_text(
        "ply\nformat ascii 1.0\nelement vertex 3\nproperty float x\nproperty float y\nproperty float z\n"
        "property float s\nproperty float t\nproperty float red\nproperty float green\nproperty float blue\n"
        "element face 1\nproperty list uchar int vertex_indices\nend_header\n"
        "0 0 0 0.25 0.25 1 0 0\n1 0 0 0.5 0.5 0 1 0\n0 1 0 0.75 0.75 0 0 1\n3 0 1 2\n"
    )
    mesh = load_model(path).meshes[0]
    assert mesh.uvs is not None and np.allclose(mesh.uvs[0], [0.25, 0.75])
    assert mesh.colors is not None and np.allclose(mesh.colors[0], [1, 0, 0, 1])


def test_ply_extra_element_with_list_is_skipped(tmp_path: Path) -> None:
    path = tmp_path / "e.ply"
    path.write_text(
        "ply\nformat ascii 1.0\nelement vertex 3\nproperty float x\nproperty float y\nproperty float z\n"
        "element face 1\nproperty list uchar int vertex_indices\n"
        "element strip 1\nproperty int id\nproperty list uchar int verts\nend_header\n"
        "0 0 0\n1 0 0\n0 1 0\n3 0 1 2\n7 2 5 6\n"
    )
    assert load_model(path).triangle_count == 1


@pytest.mark.parametrize("fmt", ["little", "big"])
def test_ply_truncated_binary_is_a_clean_error(tmp_path: Path, fmt: str) -> None:
    path = write_ply(tmp_path / "t.ply", fmt)
    data = path.read_bytes()
    for cut in (len(data) - 5, len(data) - 60, data.index(b"end_header") + 15):
        path.write_bytes(data[:cut])
        with pytest.raises(ModelImportError):
            load_model(path)


@pytest.mark.parametrize(
    "text, fragment",
    [
        ("pla\nformat ascii 1.0\nend_header\n", "pas un fichier PLY"),
        ("ply\nformat ascii 1.0\nelement vertex 3\nproperty float x\n", "en-tête"),
        ("ply\nformat weird 1.0\nend_header\n", "format PLY inconnu"),
        (
            "ply\nformat ascii 1.0\nelement vertex 1\nproperty float a\nproperty float b\nproperty float c\n"
            "element face 1\nproperty list uchar int vertex_indices\nend_header\n0 0 0\n3 0 0 0\n",
            "x, y, z",
        ),
        (
            "ply\nformat ascii 1.0\nelement vertex 3\nproperty float x\nproperty float y\nproperty float z\n"
            "end_header\n0 0 0\n1 0 0\n0 1 0\n",
            "aucune face",
        ),
        (
            "ply\nformat ascii 1.0\nelement vertex 3\nproperty float x\nproperty float y\nproperty float z\n"
            "element face 1\nproperty list uchar int vertex_indices\nend_header\n0 0 0\n1 0 0\n0 1 0\n3 0 1 7\n",
            "hors limites",
        ),
        (
            "ply\nformat ascii 1.0\nelement vertex 99999999999\nproperty float x\nend_header\n",
            "refusé",
        ),
        (
            "ply\nformat ascii 1.0\nelement vertex 3\nproperty float x\nproperty float y\nproperty float z\n"
            "end_header\n0 0 0\n1 0\n",
            "tronqué|mal formé",
        ),
    ],
)
def test_ply_malformed(tmp_path: Path, text: str, fragment: str) -> None:
    path = tmp_path / "bad.ply"
    path.write_text(text)
    with pytest.raises(ModelImportError, match=fragment):
        load_model(path)


def test_ply_binary_huge_declared_count_is_refused_before_allocation(
    tmp_path: Path,
) -> None:
    path = tmp_path / "huge.ply"
    head = (
        "ply\nformat binary_little_endian 1.0\nelement vertex 40000000\nproperty float x\nproperty float y\n"
        "property float z\nelement face 0\nproperty list uchar int vertex_indices\nend_header\n"
    )
    path.write_bytes(head.encode() + b"\x00" * 64)
    with pytest.raises(ModelImportError, match="tronqué"):
        load_model(path)


@pytest.mark.parametrize("binary", [False, True])
def test_stl_ascii_and_binary(tmp_path: Path, binary: bool) -> None:
    model = load_model(write_stl(tmp_path / "t.stl", binary))
    mesh = model.meshes[0]
    assert mesh.positions.shape == (4, 3)  # sommets dédupliqués
    assert mesh.indices.shape == (4, 3) and mesh.indices.dtype == np.uint32
    assert mesh.normals is not None
    assert np.allclose(np.linalg.norm(mesh.normals, axis=1), 1.0, atol=1e-5)
    # les triangles reconstruits sont ceux du fichier
    got = {tuple(map(tuple, mesh.positions[t].tolist())) for t in mesh.indices}
    assert got == {tuple(map(tuple, t.tolist())) for t in stl_triangles()}


def test_stl_detection_does_not_trust_the_solid_keyword(tmp_path: Path) -> None:
    # un STL binaire dont l'en-tête commence par « solid » est lu en binaire
    path = write_stl(tmp_path / "b.stl", True, header=b"solid exported as binary")
    assert load_model(path).triangle_count == 4
    # et un STL ascii qui ne commence pas par « solid » est lu en ascii
    ascii_path = write_stl(tmp_path / "a.stl", False)
    ascii_path.write_text("  \n" + ascii_path.read_text().replace("solid", "objet"))
    assert load_model(ascii_path).triangle_count == 4


def test_stl_truncated_or_wrong_size_is_a_clean_error(tmp_path: Path) -> None:
    path = write_stl(tmp_path / "t.stl", True)
    data = path.read_bytes()
    path.write_bytes(data[:-10])
    with pytest.raises(ModelImportError):
        load_model(path)
    path.write_bytes(data[:80] + struct.pack("<I", 2**31) + data[84:])
    with pytest.raises(ModelImportError):
        load_model(path)
    path.write_bytes(b"")
    with pytest.raises(ModelImportError):
        load_model(path)


def test_stl_ascii_truncated_vertex_count(tmp_path: Path) -> None:
    path = write_stl(tmp_path / "a.stl", False)
    lines = path.read_text().splitlines()
    path.write_text("\n".join(lines[:11]))  # coupé au milieu du deuxième triangle
    with pytest.raises(ModelImportError, match="multiple de 3"):
        load_model(path)


def test_stl_non_finite_positions_are_refused(tmp_path: Path) -> None:
    path = tmp_path / "n.stl"
    path.write_text("solid x\nvertex 0 0 0\nvertex 1 0 0\nvertex nan 1 0\nendsolid\n")
    with pytest.raises(ModelImportError, match="non finies"):
        load_model(path)
