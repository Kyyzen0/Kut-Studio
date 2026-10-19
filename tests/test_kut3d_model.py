"""Modèle de scène, normalisation, registre d'importeurs, architecture et performance de Kut-3D."""

from __future__ import annotations

import ast
import time
from pathlib import Path

import numpy as np
import pytest

import kut3d
from kut3d import (
    ModelImportError,
    UnsupportedFormatError,
    load_model,
    supported_extensions,
)
from kut3d.importers import register_importer
from kut3d.model import AnimationChannel, Material, Mesh, Model, Node, Skeleton
from tests.kut3d_samples import write_cube_obj, write_gltf

ROOT = Path(__file__).resolve().parent.parent


def _tri(**kw) -> Mesh:  # type: ignore[no-untyped-def]
    return Mesh(positions=[[0, 0, 0], [1, 0, 0], [0, 1, 0]], indices=[[0, 1, 2]], **kw)


def _tall_obj(tmp_path: Path) -> Path:
    """Boîte 2 x 4 x 6 centrée en (10, 0, 0), longue selon Z."""
    obj = tmp_path / "tall.obj"
    verts = [(x, y, z) for z in (-3, 3) for y in (-2, 2) for x in (9, 11)]
    faces = [
        "f 1 3 4 2",
        "f 5 6 8 7",
        "f 1 2 6 5",
        "f 3 7 8 4",
        "f 1 5 7 3",
        "f 2 4 8 6",
    ]
    obj.write_text("\n".join([f"v {x} {y} {z}" for x, y, z in verts] + faces) + "\n")
    return obj


# --- immuabilité et validation ------------------------------------------------------------------------


def test_arrays_are_read_only(tmp_path: Path) -> None:
    model = load_model(write_cube_obj(tmp_path))
    mesh = model.meshes[0]
    assert mesh.normals is not None and mesh.uvs is not None
    for arr in (
        mesh.positions,
        mesh.indices,
        mesh.normals,
        mesh.uvs,
        model.root_transform,
        model.materials[0].base_color,
    ):
        assert not arr.flags.writeable
        with pytest.raises(ValueError, match="read-only"):
            arr[(0,) * arr.ndim] = 1


def test_gltf_arrays_are_read_only(tmp_path: Path) -> None:
    model = load_model(write_gltf(tmp_path))
    mesh = model.meshes[0]
    arrays = [
        mesh.joints,
        mesh.weights,
        mesh.colors,
        mesh.morph_targets[0].position_deltas,
    ]
    arrays += [model.skeletons[0].inverse_bind_matrices, model.skeletons[0].parents]
    arrays += [
        model.animations[0].channels[0].times,
        model.animations[0].channels[0].values,
    ]
    for arr in arrays:
        assert arr is not None and not arr.flags.writeable


def test_dataclasses_are_frozen(tmp_path: Path) -> None:
    model = load_model(write_cube_obj(tmp_path))
    with pytest.raises(AttributeError):
        model.source = Path("x")  # type: ignore[misc]
    with pytest.raises(AttributeError):
        model.nodes[0].name = "x"  # type: ignore[misc]
    with pytest.raises(AttributeError):
        model.meshes[0].material_index = 3  # type: ignore[misc]


def test_mesh_validation() -> None:
    with pytest.raises(ValueError, match="hors limites"):
        Mesh(positions=[[0, 0, 0]], indices=[[0, 0, 5]])
    with pytest.raises(ValueError, match="forme"):
        Mesh(positions=[[0, 0]], indices=[[0, 0, 0]])
    with pytest.raises(ValueError, match="non finies"):
        Mesh(positions=[[0, 0, float("inf")]], indices=[[0, 0, 0]])
    with pytest.raises(ValueError, match="forme"):
        _tri(uvs=[[0, 0]])
    with pytest.raises(ValueError, match="joints et weights"):
        _tri(joints=np.zeros((3, 4)))
    with pytest.raises(ValueError):
        Material(alpha_mode="glass")
    with pytest.raises(ValueError):
        AnimationChannel(0, "scale", [0.0, 1.0], np.zeros((3, 3)))
    with pytest.raises(ValueError):
        AnimationChannel(0, "wobble", [0.0], np.zeros((1, 3)))


def test_model_graph_validation() -> None:
    mesh = _tri()
    ok = Model(nodes=(Node(children=(1,)), Node()), root_nodes=(0,), meshes=(mesh,))
    assert ok.triangle_count == 1 and ok.vertex_count == 3
    with pytest.raises(ValueError, match="cycle"):
        Model(nodes=(Node(children=(1,)), Node(children=(0,))), root_nodes=())
    with pytest.raises(ValueError, match="cycle|parent"):
        Model(nodes=(Node(children=(0,)),), root_nodes=())
    with pytest.raises(ValueError, match="deux parents"):
        Model(
            nodes=(Node(children=(2,)), Node(children=(2,)), Node()), root_nodes=(0, 1)
        )
    with pytest.raises(ValueError, match="racine qui a un parent"):
        Model(nodes=(Node(children=(1,)), Node()), root_nodes=(0, 1))
    with pytest.raises(ValueError, match="hors limites"):
        Model(nodes=(Node(mesh_index=3),))
    with pytest.raises(ValueError, match="hors limites"):
        Model(nodes=(Node(skin_index=0),), meshes=(mesh,))


def test_cycle_through_ancestors_of_the_parent_is_detected() -> None:
    # 0 -> 1 -> 2 -> 3 -> 1 : le cycle ne passe pas par 0, mais par un ancêtre du parent de 3
    with pytest.raises(ValueError, match="cycle|deux parents"):
        Model(
            nodes=(
                Node(children=(1,)),
                Node(children=(2,)),
                Node(children=(3,)),
                Node(children=(1,)),
            ),
            root_nodes=(0,),
        )


def test_deep_hierarchy_has_no_recursion_limit() -> None:
    depth = 20000
    nodes = tuple(
        Node(
            children=(i + 1,) if i < depth - 1 else (),
            translation=(1.0, 0, 0),
            mesh_index=0 if i == depth - 1 else None,
        )
        for i in range(depth)
    )
    model = Model(nodes=nodes, root_nodes=(0,), meshes=(_tri(),))
    lo, hi = model.bounds()
    assert hi[0] == pytest.approx(depth + 1)


def test_skeleton_validation() -> None:
    with pytest.raises(ValueError, match="parents"):
        Skeleton(
            joint_names=("a",),
            parents=[3],
            inverse_bind_matrices=np.eye(4)[None],
            rest_translation=[[0, 0, 0]],
            rest_rotation=[[0, 0, 0, 1]],
            rest_scale=[[1, 1, 1]],
            node_indices=[0],
        )


# --- bornes et normalisation --------------------------------------------------------------------------


def test_bounds_include_node_transforms_and_root_transform() -> None:
    mesh = _tri()
    nodes = (
        Node(children=(1,), translation=(5.0, 0, 0)),
        Node(mesh_index=0, scale=(2.0, 2.0, 2.0)),
    )
    model = Model(nodes=nodes, root_nodes=(0,), meshes=(mesh,))
    lo, hi = model.bounds()
    assert np.allclose(lo, [5, 0, 0]) and np.allclose(hi, [7, 2, 0])
    shifted = model.with_root_transform(
        np.diag([1.0, 1.0, 1.0, 1.0])
        + np.array([[0, 0, 0, 10], [0] * 4, [0] * 4, [0] * 4])
    )
    lo2, hi2 = shifted.bounds()
    assert np.allclose(lo2, [15, 0, 0]) and np.allclose(hi2, [17, 2, 0])
    assert Model().bounds()[0].tolist() == [0, 0, 0]


def test_unreachable_nodes_do_not_count_in_bounds() -> None:
    model = Model(
        nodes=(Node(), Node(mesh_index=0, translation=(100.0, 0, 0))),
        root_nodes=(0,),
        meshes=(_tri(),),
    )
    assert np.allclose(model.bounds()[1], 0)


def test_default_normalization_centers_and_never_edits_vertices(tmp_path: Path) -> None:
    obj = _tall_obj(tmp_path)
    model = load_model(obj)
    mesh = model.meshes[0]
    assert (
        mesh.positions[:, 0].min() == 9 and mesh.positions[:, 0].max() == 11
    )  # sommets intacts
    lo, hi = model.bounds()
    assert np.allclose((lo + hi) / 2, 0, atol=1e-6)
    assert np.allclose(hi - lo, [2, 4, 6], atol=1e-6)
    assert np.allclose(model.root_transform[:3, 3], [-10, 0, 0])
    raw = load_model(obj, center=False)
    assert np.allclose(raw.root_transform, np.eye(4))
    assert np.allclose(raw.bounds()[0], [9, -2, -3])


def test_up_axis_z_rotates_minus_90_about_x(tmp_path: Path) -> None:
    model = load_model(_tall_obj(tmp_path), up_axis="z")
    lo, hi = model.bounds()
    # la plus grande dimension (6, selon Z) devient la hauteur (Y) ; la profondeur (Y, 4) devient Z
    assert np.allclose(hi - lo, [2, 6, 4], atol=1e-6)
    expected = np.array(
        [[1, 0, 0, 0], [0, 0, 1, 0], [0, -1, 0, 0], [0, 0, 0, 1]], dtype=float
    )
    assert np.allclose(model.root_transform[:3, :3], expected[:3, :3])


def test_target_size_unit_scale_and_combination(tmp_path: Path) -> None:
    obj = _tall_obj(tmp_path)
    lo, hi = load_model(obj, target_size=1.0).bounds()
    assert float((hi - lo).max()) == pytest.approx(1.0)
    assert np.allclose((lo + hi) / 2, 0, atol=1e-6)
    lo, hi = load_model(obj, unit_scale=0.01, center=False).bounds()
    assert np.allclose(hi - lo, [0.02, 0.04, 0.06])
    lo, hi = load_model(obj, up_axis="z", target_size=3.0, center=False).bounds()
    assert float((hi - lo).max()) == pytest.approx(3.0)
    assert hi[0] * 1.0 == pytest.approx(11 * 0.5)  # 3 / 6 = échelle 0.5, sans centrage


def test_normalization_keeps_skeleton_and_animation_untouched(tmp_path: Path) -> None:
    path = write_gltf(tmp_path)
    plain = load_model(path, center=False)
    scaled = load_model(path, up_axis="z", target_size=10.0)
    assert not np.allclose(scaled.root_transform, np.eye(4))
    assert np.array_equal(
        plain.skeletons[0].inverse_bind_matrices,
        scaled.skeletons[0].inverse_bind_matrices,
    )
    assert np.array_equal(
        plain.skeletons[0].rest_translation, scaled.skeletons[0].rest_translation
    )
    assert np.array_equal(
        plain.animations[0].channels[0].values, scaled.animations[0].channels[0].values
    )
    assert np.array_equal(plain.meshes[0].positions, scaled.meshes[0].positions)


def test_target_size_on_flat_degenerate_model_warns(tmp_path: Path) -> None:
    obj = tmp_path / "pt.obj"
    obj.write_text("v 1 1 1\nv 1 1 1\nv 1 1 1\nf 1 2 3\n")
    model = load_model(obj, target_size=2.0)
    assert any("target_size ignorée" in w for w in model.warnings)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"up_axis": "x"},
        {"unit_scale": 0},
        {"unit_scale": float("nan")},
        {"target_size": -1.0},
        {"target_size": float("inf")},
    ],
)
def test_invalid_normalization_arguments(tmp_path: Path, kwargs: dict) -> None:  # type: ignore[type-arg]
    with pytest.raises(ValueError):
        load_model(write_cube_obj(tmp_path), **kwargs)


# --- registre et erreurs --------------------------------------------------------------------------------


def test_supported_extensions_and_public_api() -> None:
    assert supported_extensions() == (".glb", ".gltf", ".obj", ".ply", ".stl")
    for name in (
        "load_model",
        "Model",
        "ModelImportError",
        "UnsupportedFormatError",
        "supported_extensions",
    ):
        assert hasattr(kut3d, name)


def test_unknown_extension_lists_supported_ones(tmp_path: Path) -> None:
    path = tmp_path / "thing.fbx"
    path.write_bytes(b"x")
    with pytest.raises(UnsupportedFormatError) as err:
        load_model(path)
    assert isinstance(err.value, ModelImportError)
    for ext in supported_extensions():
        assert ext in str(err.value)
    no_ext = tmp_path / "plain"
    no_ext.write_bytes(b"x")
    with pytest.raises(UnsupportedFormatError):
        load_model(no_ext)


def test_missing_file_and_directory(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_model(tmp_path / "nope.obj")
    folder = tmp_path / "dir.obj"
    folder.mkdir()
    with pytest.raises(ModelImportError, match="pas un fichier"):
        load_model(folder)


def test_extension_is_case_insensitive_and_path_may_be_str(tmp_path: Path) -> None:
    obj = write_cube_obj(tmp_path, mtl=False, texture=False)
    upper = obj.rename(tmp_path / "CUBE.OBJ")
    assert load_model(str(upper)).triangle_count == 12


def test_register_custom_importer_and_unexpected_errors_are_wrapped(
    tmp_path: Path,
) -> None:
    path = tmp_path / "x.zzz"
    path.write_bytes(b"x")

    def boom(_: Path) -> Model:
        raise KeyError("clé interne")

    register_importer(["zzz"], boom)
    try:
        assert ".zzz" in supported_extensions()
        with pytest.raises(ModelImportError, match="x.zzz") as err:
            load_model(path)
        assert isinstance(err.value.__cause__, KeyError)
    finally:
        from kut3d.importers import _REGISTRY

        _REGISTRY.pop(".zzz", None)


def test_oversized_file_is_refused_before_reading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import kut3d.importers as importers

    obj = write_cube_obj(tmp_path, mtl=False, texture=False)
    monkeypatch.setattr(importers, "MAX_FILE_BYTES", 10)
    with pytest.raises(ModelImportError, match="trop volumineux"):
        load_model(obj)


def test_error_message_does_not_echo_huge_input(tmp_path: Path) -> None:
    obj = tmp_path / "x.obj"
    obj.write_text("v " + "9" * 5000 + " 0 0\nv 0 0 q\nf 1 1 1\n")
    with pytest.raises(ModelImportError) as err:
        load_model(obj)
    assert len(str(err.value)) < 400


# --- architecture ---------------------------------------------------------------------------------------


def test_kut3d_does_not_import_qt_ui_or_core() -> None:
    forbidden = ("PySide6", "PyQt5", "PyQt6", "ui", "core")
    offenders = []
    files = sorted((ROOT / "kut3d").rglob("*.py"))
    assert len(files) >= 10
    for file in files:
        tree = ast.parse(file.read_text())
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = (
                    [node.module or ""]
                    if node.level == 0
                    else [f"<relatif>{node.module}"]
                )
            for name in names:
                if name.startswith("<relatif>") or name.split(".")[0] in forbidden:
                    offenders.append(f"{file.relative_to(ROOT)} : {name}")
    assert not offenders, offenders


def test_importing_kut3d_loads_no_qt() -> None:
    import subprocess
    import sys

    code = "import sys, kut3d; assert not [m for m in sys.modules if m.startswith('PySide6')], 'Qt chargé'"
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr


# --- performance ----------------------------------------------------------------------------------------


def test_100k_triangle_obj_loads_in_under_5_seconds(tmp_path: Path) -> None:
    n = 224  # grille de 224 x 224 quads = 100 352 triangles
    gx, gy = np.meshgrid(np.arange(n + 1), np.arange(n + 1))
    lines = [f"v {x} {y} {(x * y) % 7}" for x, y in zip(gx.ravel(), gy.ravel())]
    lines += [f"vt {x / n} {y / n}" for x, y in zip(gx.ravel(), gy.ravel())]
    idx = np.arange((n + 1) * (n + 1)).reshape(n + 1, n + 1) + 1
    a, b, c, d = (
        idx[:-1, :-1].ravel(),
        idx[:-1, 1:].ravel(),
        idx[1:, 1:].ravel(),
        idx[1:, :-1].ravel(),
    )
    lines += [f"f {p}/{p} {q}/{q} {r}/{r} {s}/{s}" for p, q, r, s in zip(a, b, c, d)]
    obj = tmp_path / "grid.obj"
    obj.write_text("\n".join(lines) + "\n")
    start = time.perf_counter()
    model = load_model(obj)
    elapsed = time.perf_counter() - start
    assert model.triangle_count == 2 * n * n >= 100_000
    assert model.meshes[0].normals is not None
    assert elapsed < 5.0, f"{elapsed:.2f} s"
    print(f"\nchargement de {model.triangle_count} triangles : {elapsed:.2f} s")
