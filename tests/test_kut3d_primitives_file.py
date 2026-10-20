"""Formes de base dans le fichier `.kut3d` : format 2 conditionnel, aller-retour exact, lecture bornée, refus propres."""

from __future__ import annotations

import copy
import dataclasses
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

import kut3d
from kut3d import (
    Camera,
    Cone,
    Cube,
    Cylinder,
    Instance,
    Material,
    Model,
    Plane,
    Scene,
    SceneFileError,
    SceneVersionError,
    Sphere,
    Torus,
    dependency_files,
    load_model,
    load_scene,
    primitive_instance,
    primitive_model,
    save_scene,
)
from kut3d import primitives as prim
from kut3d import scene_io_read
from kut3d.model import Mesh, TextureRef
from tests.kut3d_primitive_scenes import SIX, six_forms
from tests.kut3d_samples import write_cube_obj
from tests.kut3d_scene_samples import rich_scene


def _camera() -> Camera:
    return Camera(position=(0, 0, 5), target=(0, 0, 0))


def _doc(*models: dict[str, Any], version: int = 2) -> dict[str, Any]:
    return {
        "format": "kut3d",
        "version": version,
        "scene": {
            "camera": {"position": [0, 0, 5], "target": [0, 0, 0]},
            "models": list(models),
        },
    }


def _write(path: Path, doc: dict[str, Any]) -> Path:
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def _entry(primitive: dict[str, Any], mid: str = "p", **extra: Any) -> dict[str, Any]:
    return {"id": mid, "primitive": primitive, **extra}


# --- aller-retour ------------------------------------------------------------------------------------------


def test_round_trip_of_the_six_forms_is_exact(tmp_path: Path) -> None:
    scene = six_forms(checker=False)
    a, b = tmp_path / "a.kut3d", tmp_path / "b.kut3d"
    save_scene(scene, a)
    back = load_scene(a)
    save_scene(back, b)
    assert a.read_bytes() == b.read_bytes()  # écrire, relire, réécrire : mêmes octets
    assert [i.model.primitive for i in back.instances] == list(SIX)
    for before, after in zip(scene.instances, back.instances):
        assert (
            np.array_equal(before.transform, after.transform)
            and before.name == after.name
        )
        mb, ma = before.model.meshes[0], after.model.meshes[0]
        assert np.array_equal(mb.positions, ma.positions) and np.array_equal(
            mb.indices, ma.indices
        )
        assert np.array_equal(
            before.model.materials[0].base_color, after.model.materials[0].base_color
        )
        assert (
            before.model.materials[0].double_sided
            == after.model.materials[0].double_sided
        )


def test_the_file_is_version_2_and_holds_parameters_never_vertices(
    tmp_path: Path,
) -> None:
    scene = Scene([primitive_instance(Sphere(1.5, 8, 4), name="s")], [], _camera())
    path = tmp_path / "s.kut3d"
    save_scene(scene, path)
    raw = path.read_bytes()
    doc = json.loads(raw)
    assert (
        doc["version"] == kut3d.PRIMITIVES_FORMAT_VERSION == 2
        and kut3d.SCENE_FORMAT_VERSION == 1
    )
    (entry,) = doc["scene"]["models"]
    assert entry["primitive"] == {
        "kind": "sphere",
        "radius": 1.5,
        "segments": 8,
        "rings": 4,
    }
    assert (
        set(entry) == {"id", "primitive", "root_transform"} and entry["id"] == "sphere"
    )
    assert len(raw) < 2000  # quelques lignes de paramètres, pas des milliers de sommets
    assert b"positions" not in raw and b"indices" not in raw


def test_a_scene_without_a_primitive_stays_in_version_1_with_no_new_key(
    tmp_path: Path,
) -> None:
    scene = rich_scene(tmp_path)
    path = tmp_path / "scene.kut3d"
    save_scene(scene, path)
    doc = json.loads(path.read_bytes())
    assert doc["version"] == 1 and set(doc) == {"format", "version", "scene"}
    assert all(
        set(m) == {"id", "path", "root_transform"} for m in doc["scene"]["models"]
    )
    assert (
        b"primitive" not in path.read_bytes() and b"material" not in path.read_bytes()
    )
    empty = Scene([], [], _camera())
    save_scene(empty, tmp_path / "e.kut3d")
    assert json.loads((tmp_path / "e.kut3d").read_bytes())["version"] == 1


def test_a_scene_without_a_primitive_is_written_byte_for_byte_as_before(tmp_path: Path) -> None:
    """Empreinte relevée sur le code d'avant les formes de base (origin/main 52575b5) pour `rich_scene` : elle ne doit jamais bouger."""
    import hashlib

    path = tmp_path / "scene.kut3d"
    save_scene(rich_scene(tmp_path), path)
    assert len(path.read_bytes()) == 2504
    assert hashlib.sha256(path.read_bytes()).hexdigest() == "89c6402b842cf89ae84599527a3e1dff620194c20b7efce84746b59eff9a90f8"


def test_a_file_model_and_a_primitive_can_share_a_scene(tmp_path: Path) -> None:
    write_cube_obj(tmp_path, name="cube")
    mesh = load_model(tmp_path / "cube.obj")
    scene = Scene(
        [Instance(mesh, name="fichier"), primitive_instance("cone", name="forme")],
        [],
        _camera(),
    )
    path = tmp_path / "mix.kut3d"
    save_scene(scene, path)
    assert json.loads(path.read_bytes())["version"] == 2
    back = load_scene(path)
    assert (
        back.instances[0].model.primitive is None
        and back.instances[1].model.primitive == Cone()
    )
    files = dependency_files(path)
    assert files[0] == path.resolve() and (tmp_path / "cube.obj").resolve() in files
    assert Path("").resolve() not in files and all(
        f.is_absolute() for f in files
    )  # une forme n'a aucun fichier


def test_dependency_files_of_a_primitive_only_scene_is_the_file_itself(
    tmp_path: Path,
) -> None:
    path = tmp_path / "p.kut3d"
    save_scene(six_forms(), path)
    assert dependency_files(path) == (path.resolve(),)


def test_two_instances_of_one_primitive_share_one_entry_and_ids_are_unique(
    tmp_path: Path,
) -> None:
    model = primitive_model(Cube(2, 2, 2))
    twin = primitive_model(Cube(3, 3, 3))
    scene = Scene([Instance(model), Instance(model), Instance(twin)], [], _camera())
    path = tmp_path / "s.kut3d"
    save_scene(scene, path)
    entries = json.loads(path.read_bytes())["scene"]["models"]
    assert [e["id"] for e in entries] == ["cube", "cube-2"]
    back = load_scene(path)
    assert back.instances[0].model is back.instances[1].model and back.instances[
        2
    ].model.primitive == Cube(3, 3, 3)


def test_root_transform_of_a_primitive_round_trips(tmp_path: Path) -> None:
    m = np.eye(4)
    m[:3, 3] = (1.5, -2.0, 0.25)
    m[0, 0] = 2.0
    model = primitive_model(Torus(1.0, 0.25, 12, 6)).with_root_transform(m)
    path = tmp_path / "t.kut3d"
    save_scene(Scene([Instance(model)], [], _camera()), path)
    back = load_scene(path).instances[0].model
    assert np.array_equal(back.root_transform, m) and back.primitive == Torus(
        1.0, 0.25, 12, 6
    )


def test_materials_round_trip_every_representable_field(tmp_path: Path) -> None:
    material = Material(
        name="émaillé",
        base_color=(0.25, 0.5, 0.75, 0.5),
        metallic=0.3,
        roughness=0.6,
        emissive=(0.1, 2.0, 0.0),
        alpha_mode="mask",
        alpha_cutoff=0.4,
        double_sided=True,
    )
    path = tmp_path / "m.kut3d"
    save_scene(
        Scene([primitive_instance("cube", material=material)], [], _camera()), path
    )
    back = load_scene(path).instances[0].model.materials[0]
    assert (
        back.name == "émaillé"
        and back.alpha_mode == "mask"
        and back.double_sided is True
    )
    assert np.array_equal(back.base_color, material.base_color) and np.array_equal(
        back.emissive, material.emissive
    )
    assert (back.metallic, back.roughness, back.alpha_cutoff) == (0.3, 0.6, 0.4)
    save_scene(load_scene(path), tmp_path / "m2.kut3d")
    assert path.read_bytes() == (tmp_path / "m2.kut3d").read_bytes()


# --- ce que save_scene refuse -----------------------------------------------------------------------------


def test_hand_built_geometry_without_a_file_is_still_refused_with_the_same_message(
    tmp_path: Path,
) -> None:
    mesh = Mesh([[0, 0, 0], [1, 0, 0], [0, 1, 0]], [[0, 1, 2]])
    model = Model(
        nodes=(kut3d.model.Node(mesh_index=0),), root_nodes=(0,), meshes=(mesh,)
    )
    with pytest.raises(
        SceneFileError,
        match="n'a pas de fichier source \\(géométrie construite en code\\)",
    ):
        save_scene(Scene([Instance(model)], [], _camera()), tmp_path / "x.kut3d")
    assert not (tmp_path / "x.kut3d").exists()


def test_a_textured_material_is_refused_not_dropped(tmp_path: Path) -> None:
    texture = TextureRef(name="t", data=b"x", mime_type="image/png")
    with pytest.raises(SceneFileError, match="texture"):
        save_scene(
            Scene(
                [
                    primitive_instance(
                        "cube", material=Material(base_color_texture=texture)
                    )
                ],
                [],
                _camera(),
            ),
            tmp_path / "x.kut3d",
        )
    assert not (tmp_path / "x.kut3d").exists()


def test_a_primitive_modified_after_creation_is_refused_not_rewritten(
    tmp_path: Path,
) -> None:
    model = primitive_model(Cube(1, 1, 1))
    stretched = model.meshes[0].positions * 2.0
    changed = dataclasses.replace(
        model, meshes=(dataclasses.replace(model.meshes[0], positions=stretched),)
    )
    with pytest.raises(SceneFileError, match="modifiée"):
        save_scene(Scene([Instance(changed)], [], _camera()), tmp_path / "x.kut3d")
    swapped = dataclasses.replace(
        model, primitive=Cube(5, 5, 5)
    )  # description qui ne décrit plus le maillage
    with pytest.raises(SceneFileError, match="modifiée"):
        save_scene(Scene([Instance(swapped)], [], _camera()), tmp_path / "x.kut3d")
    assert not (tmp_path / "x.kut3d").exists()


@pytest.mark.parametrize(
    "material",
    [
        Material(base_color=(2.0, 0, 0, 1)),
        Material(metallic=1.5),
        Material(roughness=-0.1),
        Material(emissive=(100.0, 0, 0)),
        Material(alpha_cutoff=2.0),
        Material(metallic=float("nan")),
    ],
)
def test_a_material_value_the_reader_would_refuse_is_refused_at_write(
    tmp_path: Path, material: Material
) -> None:
    with pytest.raises(SceneFileError):
        save_scene(
            Scene([primitive_instance("cube", material=material)], [], _camera()),
            tmp_path / "x.kut3d",
        )
    assert not (tmp_path / "x.kut3d").exists()


def test_the_scene_cap_is_checked_when_writing_too(tmp_path: Path) -> None:
    big = Plane(1, 1, 181, 181)  # 65 522 triangles
    instances = [
        Instance(primitive_model(dataclasses.replace(big, width=1.0 + i)))
        for i in range(8)
    ]
    with pytest.raises(SceneFileError, match="triangles"):
        save_scene(Scene(instances, [], _camera()), tmp_path / "x.kut3d")
    assert not (tmp_path / "x.kut3d").exists()


# --- lecture : refus --------------------------------------------------------------------------------------


def test_a_reader_of_the_previous_format_refuses_a_file_with_a_primitive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "p.kut3d"
    save_scene(six_forms(), path)
    monkeypatch.setattr(
        scene_io_read, "MAX_READ_VERSION", 1
    )  # ce que sait lire la version précédente
    with pytest.raises(SceneVersionError, match="plus récent"):
        load_scene(path)


@pytest.mark.parametrize("version", [3, 99, 10**9])
def test_an_unknown_newer_version_is_refused(tmp_path: Path, version: int) -> None:
    with pytest.raises(SceneVersionError):
        load_scene(_write(tmp_path / "s.kut3d", _doc(version=version)))


def test_a_primitive_in_a_version_1_file_is_refused(tmp_path: Path) -> None:
    doc = _doc(_entry({"kind": "cube"}), version=1)
    with pytest.raises(SceneFileError, match="exige le format 2"):
        load_scene(_write(tmp_path / "s.kut3d", doc))


def test_minimal_primitive_entries_take_the_defaults(tmp_path: Path) -> None:
    doc = _doc(
        *[
            _entry({"kind": k}, mid=k)
            for k in ("cube", "sphere", "plane", "cylinder", "cone", "torus")
        ]
    )
    doc["scene"]["instances"] = [{"model": "sphere"}]
    scene = load_scene(_write(tmp_path / "s.kut3d", doc))
    assert scene.instances[0].model.primitive == Sphere()


HOSTILE: list[tuple[str, dict[str, Any]]] = [
    ("kind inconnu", {"kind": "pyramide"}),
    ("kind absent", {"width": 1.0}),
    ("kind pas un texte", {"kind": ["cube"]}),
    ("kind objet", {"kind": {"a": 1}}),
    ("clé inconnue", {"kind": "cube", "rayon": 1.0}),
    ("clé d'un autre genre", {"kind": "cube", "radius": 1.0}),
    ("dimension énorme", {"kind": "cube", "width": 1e30}),
    ("dimension infinie", {"kind": "cube", "width": 1e999}),
    ("dimension nulle", {"kind": "cube", "width": 0}),
    ("dimension négative", {"kind": "sphere", "radius": -1}),
    ("dimension texte", {"kind": "cube", "width": "1"}),
    ("dimension booléenne", {"kind": "cube", "depth": True}),
    ("dimension nulle (null)", {"kind": "cube", "height": None}),
    ("segments énormes", {"kind": "sphere", "segments": 10**9}),
    ("segments flottants", {"kind": "sphere", "segments": 32.0}),
    ("segments booléens", {"kind": "sphere", "segments": True}),
    ("segments négatifs", {"kind": "cylinder", "segments": -3}),
    ("anneaux trop peu", {"kind": "sphere", "rings": 1}),
    (
        "subdivisions énormes",
        {"kind": "plane", "width_segments": 100000, "depth_segments": 100000},
    ),
    ("bouchon pas booléen", {"kind": "cylinder", "caps": 1}),
    ("tore recoupé", {"kind": "torus", "major_radius": 1.0, "minor_radius": 2.0}),
    (
        "trop de triangles",
        {"kind": "plane", "width_segments": 256, "depth_segments": 256},
    ),
    ("énorme entier", {"kind": "sphere", "segments": 10**400}),
]


@pytest.mark.parametrize(("label", "primitive"), HOSTILE, ids=[h[0] for h in HOSTILE])
def test_hostile_primitive_parameters_are_refused_without_generating(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    label: str,
    primitive: dict[str, Any],
) -> None:
    monkeypatch.setattr(
        scene_io_read,
        "primitive_model",
        lambda *a: pytest.fail("géométrie générée pour une entrée refusée"),
    )
    raw = json.dumps(_doc(_entry(primitive))).replace("Infinity", "1e999")
    path = tmp_path / "s.kut3d"
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(SceneFileError) as info:
        load_scene(path)
    assert len(str(info.value)) < 400


def test_a_file_with_a_huge_sum_of_triangles_is_refused_before_any_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        scene_io_read, "primitive_model", lambda *a: pytest.fail("généré")
    )
    one = {"kind": "plane", "width_segments": 181, "depth_segments": 181}
    models = [
        _entry({**one, "width": 1.0 + i}, mid=f"p{i}") for i in range(256)
    ]  # 256 x 65 522 triangles
    with pytest.raises(SceneFileError, match="trop de triangles"):
        load_scene(_write(tmp_path / "s.kut3d", _doc(*models)))


def test_the_cumulative_cap_accepts_exactly_the_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(scene_io_read, "MAX_SCENE_PRIMITIVE_TRIANGLES", 12 * 3)
    models = [_entry({"kind": "cube"}, mid=f"c{i}") for i in range(3)]
    load_scene(_write(tmp_path / "ok.kut3d", _doc(*models)))
    models.append(_entry({"kind": "cube"}, mid="c3"))
    with pytest.raises(SceneFileError, match="trop de triangles"):
        load_scene(_write(tmp_path / "ko.kut3d", _doc(*models)))


@pytest.mark.parametrize(
    "entry",
    [
        {"id": "p", "primitive": {"kind": "cube"}, "path": "cube.obj"},
        {"id": "p", "primitive": {"kind": "cube"}, "normalize": {"center": True}},
        {"id": "p", "primitive": {"kind": "cube"}, "clef": 1},
        {"primitive": {"kind": "cube"}},
        {"id": "p", "primitive": ["cube"]},
        {"id": "p", "primitive": "cube"},
        {"id": "p", "primitive": {"kind": "cube"}, "material": "rouge"},
        {
            "id": "p",
            "primitive": {"kind": "cube"},
            "material": {"couleur": [1, 0, 0, 1]},
        },
        {
            "id": "p",
            "primitive": {"kind": "cube"},
            "material": {"base_color": [1, 0, 0]},
        },
        {
            "id": "p",
            "primitive": {"kind": "cube"},
            "material": {"base_color": [2, 0, 0, 1]},
        },
        {"id": "p", "primitive": {"kind": "cube"}, "material": {"roughness": -1}},
        {
            "id": "p",
            "primitive": {"kind": "cube"},
            "material": {"emissive": [1e9, 0, 0]},
        },
        {"id": "p", "primitive": {"kind": "cube"}, "material": {"alpha_mode": "verre"}},
        {"id": "p", "primitive": {"kind": "cube"}, "material": {"double_sided": 1}},
        {"id": "p", "primitive": {"kind": "cube"}, "material": {"name": "x" * 5000}},
        {
            "id": "p",
            "primitive": {"kind": "cube"},
            "material": {"base_color_texture": "a.png"},
        },
        {"id": "p", "primitive": {"kind": "cube"}, "root_transform": [0] * 16},
        {"id": "p", "primitive": {"kind": "cube"}, "root_transform": [1.0] * 15},
        {"id": "é/../..", "primitive": {"kind": "cube"}},
    ],
    ids=lambda e: json.dumps(e)[:60],
)
def test_malformed_entries_are_refused(tmp_path: Path, entry: dict[str, Any]) -> None:
    with pytest.raises(SceneFileError):
        load_scene(_write(tmp_path / "s.kut3d", _doc(entry)))


def test_a_duplicate_id_between_a_primitive_and_a_primitive_is_refused(
    tmp_path: Path,
) -> None:
    doc = _doc(_entry({"kind": "cube"}), _entry({"kind": "cone"}))
    with pytest.raises(SceneFileError, match="en double"):
        load_scene(_write(tmp_path / "s.kut3d", doc))


def test_an_instance_may_reference_a_primitive_but_an_unknown_id_is_refused(
    tmp_path: Path,
) -> None:
    doc = _doc(_entry({"kind": "cube"}))
    doc["scene"]["instances"] = [{"model": "p"}, {"model": "ailleurs"}]
    with pytest.raises(SceneFileError, match="modèle inconnu"):
        load_scene(_write(tmp_path / "s.kut3d", doc))


def test_json_constants_and_duplicate_keys_are_still_refused_in_a_primitive(
    tmp_path: Path,
) -> None:
    for raw in (
        '{"format":"kut3d","version":2,"scene":{"camera":{"position":[0,0,5],"target":[0,0,0]},'
        '"models":[{"id":"p","primitive":{"kind":"cube","width":NaN}}]}}',
        '{"format":"kut3d","version":2,"scene":{"camera":{"position":[0,0,5],"target":[0,0,0]},'
        '"models":[{"id":"p","primitive":{"kind":"cube","width":1,"width":2}}]}}',
    ):
        path = tmp_path / "s.kut3d"
        path.write_text(raw, encoding="utf-8")
        with pytest.raises(SceneFileError):
            load_scene(path)


def test_a_copy_of_the_document_with_every_value_valid_loads(tmp_path: Path) -> None:
    doc = copy.deepcopy(
        _doc(
            _entry(
                {
                    "kind": "torus",
                    "major_radius": 1.0,
                    "minor_radius": 0.25,
                    "segments": 8,
                    "tube_segments": 4,
                }
            )
        )
    )
    scene = load_scene(_write(tmp_path / "s.kut3d", doc))
    assert scene.instances == () and prim.Torus(1.0, 0.25, 8, 4).triangle_count == 64


def test_cylinder_flags_round_trip(tmp_path: Path) -> None:
    scene = Scene(
        [
            primitive_instance(Cylinder(0.5, 1, 6, caps=False)),
            primitive_instance(Cone(0.5, 1, 6, cap=False)),
        ],
        [],
        _camera(),
    )
    save_scene(scene, tmp_path / "f.kut3d")
    back = load_scene(tmp_path / "f.kut3d")
    assert back.instances[0].model.primitive == Cylinder(0.5, 1.0, 6, False)
    assert back.instances[1].model.primitive == Cone(0.5, 1.0, 6, False)
