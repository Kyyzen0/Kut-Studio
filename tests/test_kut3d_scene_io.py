"""Fichier de scène `.kut3d` : aller-retour exact, lecture stricte, confinement des chemins, écriture atomique."""

from __future__ import annotations

import copy
import dataclasses
import json
import os
from pathlib import Path
from typing import Any

import numpy as np
import pytest

import kut3d
from kut3d import (
    Camera,
    Instance,
    Model,
    Scene,
    SceneFileError,
    SceneVersionError,
    dependency_files,
    load_model,
    load_scene,
    save_scene,
)
from kut3d.model import Mesh
from kut3d.scene_io_fields import MAX_MODELS, MAX_SCENE_BYTES
from tests.kut3d_samples import write_cube_obj, write_gltf
from tests.kut3d_scene_samples import (
    assert_scenes_equal,
    minimal_document,
    mutated,
    rich_scene,
    translation,
    write_doc,
)


@pytest.fixture
def cube_dir(tmp_path: Path) -> Path:
    write_cube_obj(tmp_path)
    return tmp_path


def _load_doc(folder: Path, doc: dict[str, Any], **kw: Any) -> Scene:
    return load_scene(write_doc(folder / "s.kut3d", doc), **kw)


def _camera() -> Camera:
    return Camera(position=(0, 0, 5), target=(0, 0, 0))


# --- aller-retour ----------------------------------------------------------------------------------------


def test_round_trip_rich_scene(tmp_path: Path) -> None:
    scene = rich_scene(tmp_path)
    path = tmp_path / "scene.kut3d"
    save_scene(scene, path)
    back = load_scene(path)
    assert_scenes_equal(scene, back)
    # Idempotent : relire puis réécrire donne les mêmes octets.
    first = path.read_bytes()
    save_scene(back, path)
    assert path.read_bytes() == first


def test_background_none_round_trip(cube_dir: Path) -> None:
    model = load_model(cube_dir / "cube.obj")
    scene = Scene([Instance(model)], [], _camera(), background=None)
    save_scene(scene, cube_dir / "s.kut3d")
    assert load_scene(cube_dir / "s.kut3d").background is None


def test_shared_model_has_a_single_entry(tmp_path: Path) -> None:
    scene = rich_scene(tmp_path)
    save_scene(scene, tmp_path / "s.kut3d")
    doc = json.loads((tmp_path / "s.kut3d").read_text())
    assert len(doc["scene"]["models"]) == 3  # cube (2 instances), quad, rig
    assert len(doc["scene"]["instances"]) == 4
    back = load_scene(tmp_path / "s.kut3d")
    assert back.instances[0].model is back.instances[1].model


def test_written_bytes_are_stable_ascii_utf8_sorted(tmp_path: Path) -> None:
    scene = rich_scene(tmp_path)
    a, b = tmp_path / "a.kut3d", tmp_path / "b.kut3d"
    save_scene(scene, a)
    save_scene(scene, b)
    data = a.read_bytes()
    assert data == b.read_bytes()
    assert data.endswith(b"\n") and b"\r" not in data
    data.decode("ascii")
    doc = json.loads(data)
    assert list(doc) == sorted(doc) and list(doc["scene"]) == sorted(doc["scene"])
    assert (
        doc["format"] == "kut3d" and doc["version"] == kut3d.SCENE_FORMAT_VERSION == 1
    )
    assert all(len(m["root_transform"]) == 16 for m in doc["scene"]["models"])


def test_accented_paths_and_names(tmp_path: Path) -> None:
    folder = tmp_path / "dossier é"
    folder.mkdir()
    write_cube_obj(folder, name="modèle é")
    model = load_model(folder / "modèle é.obj")
    scene = Scene([Instance(model, name="caméra à l'œuf")], [], _camera())
    save_scene(scene, folder / "scène.kut3d")
    raw = (folder / "scène.kut3d").read_bytes()
    raw.decode("ascii")  # ensure_ascii : accents écrits en \uXXXX
    assert b"\\u00e8" in raw
    back = load_scene(folder / "scène.kut3d")
    assert back.instances[0].name == "caméra à l'œuf"
    assert back.instances[0].model.source.name.startswith("mod")
    assert_scenes_equal(scene, back)


def test_model_id_collisions_are_deduplicated(tmp_path: Path) -> None:
    write_cube_obj(tmp_path, name="a")
    write_cube_obj(tmp_path, name="a-2")
    a = load_model(tmp_path / "a.obj")
    a2 = load_model(tmp_path / "a-2.obj")
    a_copy = dataclasses.replace(a)  # autre objet, même fichier : autre entrée
    save_scene(
        Scene([Instance(a), Instance(a2), Instance(a_copy)], [], _camera()),
        tmp_path / "s.kut3d",
    )
    ids = [
        m["id"]
        for m in json.loads((tmp_path / "s.kut3d").read_text())["scene"]["models"]
    ]
    assert len(set(ids)) == 3 and ids[:2] == ["a", "a-2"]
    assert len(load_scene(tmp_path / "s.kut3d").instances) == 3


def test_save_refuses_too_many_distinct_models(cube_dir: Path) -> None:
    base = load_model(cube_dir / "cube.obj")
    instances = [Instance(dataclasses.replace(base)) for _ in range(MAX_MODELS + 1)]
    with pytest.raises(SceneFileError, match="trop de modèles"):
        save_scene(Scene(instances, [], _camera()), cube_dir / "s.kut3d")


# --- normalize / root_transform --------------------------------------------------------------------------


def test_normalize_alone_is_applied_through_load_model(cube_dir: Path) -> None:
    options = {"up_axis": "z", "unit_scale": 2.0, "target_size": 1.0, "center": True}
    doc = mutated(
        ("scene", "models"), [{"id": "cube", "path": "cube.obj", "normalize": options}]
    )
    model = _load_doc(cube_dir, doc).instances[0].model
    expected = load_model(cube_dir / "cube.obj", **options).root_transform
    assert np.array_equal(model.root_transform, expected)
    assert not np.array_equal(model.root_transform, np.eye(4))


def test_root_transform_wins_over_normalize(cube_dir: Path) -> None:
    given = translation(3, 4, 5)
    given[0, 0] = 0.5
    entry = {
        "id": "cube",
        "path": "cube.obj",
        "normalize": {"target_size": 10.0},
        "root_transform": [float(x) for x in given.reshape(-1)],
    }
    model = (
        _load_doc(cube_dir, mutated(("scene", "models"), [entry])).instances[0].model
    )
    assert np.array_equal(model.root_transform, given)


def test_neither_option_gives_default_normalization(cube_dir: Path) -> None:
    doc = mutated(("scene", "models"), [{"id": "cube", "path": "cube.obj"}])
    model = _load_doc(cube_dir, doc).instances[0].model
    assert np.array_equal(
        model.root_transform, load_model(cube_dir / "cube.obj").root_transform
    )


def test_minimal_document_loads(cube_dir: Path) -> None:
    scene = _load_doc(cube_dir, minimal_document())
    assert len(scene.lights) == 3 and scene.background is not None


def test_optional_sections_default(cube_dir: Path) -> None:
    doc = {
        "format": "kut3d",
        "version": 1,
        "scene": {"camera": {"position": [0, 0, 5], "target": [0, 0, 0]}},
    }
    scene = _load_doc(cube_dir, doc)
    assert scene.instances == () and scene.lights == () and scene.background is None


# --- refus de lecture ------------------------------------------------------------------------------------


def test_newer_version_is_refused(cube_dir: Path) -> None:
    doc = mutated(("version",), 2)
    doc["scene"]["clef_du_futur"] = (
        1  # un format futur peut avoir d'autres clés : c'est la version qui est dite
    )
    with pytest.raises(SceneVersionError, match="plus récent"):
        _load_doc(cube_dir, doc)
    assert issubclass(SceneVersionError, SceneFileError) and issubclass(
        SceneFileError, ValueError
    )


@pytest.mark.parametrize("version", [0, -1, "1", 1.0, True, None])
def test_invalid_version(cube_dir: Path, version: Any) -> None:
    with pytest.raises(SceneFileError) as info:
        _load_doc(cube_dir, mutated(("version",), version))
    assert not isinstance(info.value, SceneVersionError)


def test_wrong_format_name(cube_dir: Path) -> None:
    with pytest.raises(SceneFileError, match="format inconnu"):
        _load_doc(cube_dir, mutated(("format",), "autre"))


@pytest.mark.parametrize(
    "where",
    [
        (),
        ("scene",),
        ("scene", "camera"),
        ("scene", "lights", 0),
        ("scene", "lights", 1),
        ("scene", "lights", 2),
        ("scene", "models", 0),
        ("scene", "models", 0, "normalize"),
        ("scene", "instances", 0),
    ],
)
def test_unknown_key_refused_at_every_level(
    cube_dir: Path, where: tuple[Any, ...]
) -> None:
    doc = copy.deepcopy(minimal_document())
    node: Any = doc
    for key in where:
        node = node[key]
    node["bogus"] = 1
    with pytest.raises(SceneFileError, match="inconnue"):
        _load_doc(cube_dir, doc)


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity", "1e999"])
def test_non_finite_numbers_refused(cube_dir: Path, token: str) -> None:
    text = json.dumps(minimal_document()).replace('"far": 100', f'"far": {token}')
    path = cube_dir / "s.kut3d"
    path.write_text(text)
    with pytest.raises(SceneFileError):
        load_scene(path)


def test_duplicate_json_key_refused(cube_dir: Path) -> None:
    path = cube_dir / "s.kut3d"
    path.write_text('{"format": "kut3d", "format": "kut3d", "version": 1, "scene": {}}')
    with pytest.raises(SceneFileError, match="en double"):
        load_scene(path)


def test_huge_file_refused_before_reading(cube_dir: Path) -> None:
    path = cube_dir / "big.kut3d"
    path.write_bytes(b" " * (MAX_SCENE_BYTES + 1))
    with pytest.raises(SceneFileError, match="trop volumineux"):
        load_scene(path)


def test_deeply_nested_json_refused(cube_dir: Path) -> None:
    path = cube_dir / "deep.kut3d"
    path.write_text(
        '{"format": "kut3d", "version": 1, "scene": '
        + "[" * 200_000
        + "]" * 200_000
        + "}"
    )
    with pytest.raises(SceneFileError, match="profondément"):
        load_scene(path)


@pytest.mark.parametrize("payload", [b"", b"\xff\xfe\x00", b"not json", b"[]", b"{"])
def test_garbage_files(cube_dir: Path, payload: bytes) -> None:
    path = cube_dir / "g.kut3d"
    path.write_bytes(payload)
    with pytest.raises(SceneFileError):
        load_scene(path)


def test_missing_scene_file_and_directory(cube_dir: Path) -> None:
    with pytest.raises(SceneFileError, match="introuvable"):
        load_scene(cube_dir / "absent.kut3d")
    with pytest.raises(SceneFileError, match="pas un fichier"):
        load_scene(cube_dir)


def test_too_many_lights_instances_models(cube_dir: Path) -> None:
    light = {"type": "ambient"}
    with pytest.raises(SceneFileError, match="trop d'éléments"):
        _load_doc(
            cube_dir,
            mutated(("scene", "lights"), [light] * (kut3d.scene.MAX_LIGHTS + 1)),
        )
    with pytest.raises(SceneFileError, match="trop d'éléments"):
        _load_doc(
            cube_dir,
            mutated(
                ("scene", "instances"),
                [{"model": "cube"}] * (kut3d.scene.MAX_INSTANCES + 1),
            ),
        )
    with pytest.raises(SceneFileError, match="trop d'éléments"):
        _load_doc(
            cube_dir,
            mutated(
                ("scene", "models"),
                [{"id": "m", "path": "cube.obj"}] * (MAX_MODELS + 1),
            ),
        )


@pytest.mark.parametrize("count", [0, 3, 15, 17])
def test_arrays_need_exact_length(cube_dir: Path, count: int) -> None:
    with pytest.raises(SceneFileError, match="nombres attendus"):
        _load_doc(
            cube_dir, mutated(("scene", "instances", 0, "transform"), [1.0] * count)
        )
    with pytest.raises(SceneFileError, match="nombres attendus"):
        entry = {"id": "cube", "path": "cube.obj", "root_transform": [1.0] * count}
        _load_doc(cube_dir, mutated(("scene", "models"), [entry]))


@pytest.mark.parametrize(
    "where",
    [
        ("scene", "camera", "fov_y_deg"),
        ("scene", "camera", "position", 0),
        ("scene", "lights", 0, "intensity"),
        ("scene", "lights", 2, "range"),
        ("scene", "background", 3),
        ("scene", "instances", 0, "time_offset"),
        ("scene", "instances", 0, "transform", 5),
        ("scene", "models", 0, "normalize", "unit_scale"),
    ],
)
def test_bool_is_not_a_number(cube_dir: Path, where: tuple[Any, ...]) -> None:
    with pytest.raises(SceneFileError, match="nombre"):
        _load_doc(cube_dir, mutated(where, True))


@pytest.mark.parametrize(
    ("where", "value"),
    [
        (("scene", "instances", 0, "loop"), "oui"),
        (("scene", "instances", 0, "loop"), 1),
        (("scene", "models", 0, "normalize", "center"), 1),
        (("scene", "instances", 0, "clip"), True),
        (("scene", "instances", 0, "clip"), 1.5),
        (("scene", "instances", 0, "name"), 3),
        (("scene", "camera", "near"), "0.1"),
        (("scene", "background"), [0, 0, 0]),
        (("scene", "lights", 0, "type"), "spot"),
        (("scene", "lights", 0), 5),
        (("scene", "camera"), []),
        (("scene", "models"), {}),
        (("scene", "models", 0, "id"), "a b"),
        (("scene", "models", 0, "id"), ""),
        (("scene", "models", 0, "id"), "x" * 65),
        (("scene", "instances", 0, "model"), "autre"),
        (("scene", "instances", 0, "name"), "n" * 201),
        (("scene", "models", 0, "normalize", "up_axis"), "x"),
        (("scene", "camera", "fov_y_deg"), 500),
        (
            ("scene", "camera", "up"),
            [0, 0, 5],
        ),  # colinéaire à la visée : refus du constructeur de Camera
        (("scene", "lights", 0, "intensity"), -1),
        (("scene", "instances", 0, "transform"), [0.0] * 16),
        (("scene", "models", 0, "root_transform"), [0.0] * 16),
    ],
)
def test_invalid_values_are_refused(
    cube_dir: Path, where: tuple[Any, ...], value: Any
) -> None:
    with pytest.raises(SceneFileError):
        _load_doc(cube_dir, mutated(where, value))


def test_duplicate_model_id_and_missing_required_key(cube_dir: Path) -> None:
    entries = [{"id": "m", "path": "cube.obj"}, {"id": "m", "path": "cube.obj"}]
    with pytest.raises(SceneFileError, match="en double"):
        _load_doc(cube_dir, mutated(("scene", "models"), entries))
    doc = minimal_document()
    del doc["scene"]["camera"]
    with pytest.raises(SceneFileError, match="obligatoire"):
        _load_doc(cube_dir, doc)


def test_unknown_clip_name_and_index_refused(tmp_path: Path) -> None:
    write_gltf(tmp_path, "glb", name="quad")
    entry = [{"id": "q", "path": "quad.glb"}]
    for clip, match in (("nope", "introuvable"), (7, "hors limites")):
        doc = mutated(("scene", "models"), entry)
        doc["scene"]["instances"] = [{"model": "q", "clip": clip}]
        with pytest.raises(SceneFileError, match=match):
            _load_doc(tmp_path, doc)
    doc = mutated(("scene", "models"), entry)
    doc["scene"]["instances"] = [{"model": "q", "clip": "slide"}]
    assert _load_doc(tmp_path, doc).instances[0].clip == "slide"


def test_long_input_is_not_copied_in_messages(cube_dir: Path) -> None:
    long_path = "a" * 5000
    with pytest.raises(SceneFileError) as info:
        _load_doc(
            cube_dir, mutated(("scene", "models"), [{"id": "m", "path": long_path}])
        )
    assert len(str(info.value)) < 400
    with pytest.raises(SceneFileError) as info:
        _load_doc(
            cube_dir, mutated(("scene", "models"), [{"id": "m", "path": "z" * 900}])
        )
    assert len(str(info.value)) < 400


def test_missing_corrupt_and_unsupported_models(cube_dir: Path) -> None:
    def one(path: str) -> dict[str, Any]:
        return mutated(("scene", "models"), [{"id": "cube", "path": path}])

    with pytest.raises(SceneFileError, match="introuvable"):
        _load_doc(cube_dir, one("absent.obj"))
    (cube_dir / "bad.obj").write_text("f 1 2 3\n")
    with pytest.raises(SceneFileError, match="bad.obj"):
        _load_doc(cube_dir, one("bad.obj"))
    (cube_dir / "m.xyz").write_text("x")
    with pytest.raises(SceneFileError, match="non pris en charge"):
        _load_doc(cube_dir, one("m.xyz"))
    (cube_dir / "sub.obj").mkdir()
    with pytest.raises(SceneFileError, match="introuvable"):
        _load_doc(cube_dir, one("sub.obj"))


# --- chemins ---------------------------------------------------------------------------------------------

BAD_PATHS = [
    "",
    "/etc/cube.obj",
    "C:/models/cube.obj",
    "c:cube.obj",
    "..\\cube.obj",
    "sub\\cube.obj",
    "../cube.obj",
    "sub/../cube.obj",
    "a\x00b.obj",
    "a//b.obj",
    "./cube.obj",
    "sub/",
    "CON.obj",
    "dir/nul",
    "com1.txt",
    "cube.obj.",
    "cube.obj ",
    "a<b.obj",
    "a?.obj",
    "dossier:alt.obj",
    "tab\t.obj",
]


@pytest.mark.parametrize("raw", BAD_PATHS)
def test_unsafe_model_paths_are_refused(cube_dir: Path, raw: str) -> None:
    doc = mutated(("scene", "models"), [{"id": "cube", "path": raw}])
    with pytest.raises(SceneFileError):
        _load_doc(cube_dir, doc)


def test_absolute_and_drive_paths_refused_even_with_allow_outside(
    cube_dir: Path,
) -> None:
    for raw in (
        str((cube_dir / "cube.obj").resolve().as_posix()),
        "C:/x.obj",
        "\\\\srv\\x.obj",
    ):
        doc = mutated(("scene", "models"), [{"id": "cube", "path": raw}])
        with pytest.raises(SceneFileError):
            _load_doc(cube_dir, doc, allow_outside=True)


def test_allow_outside_permits_parent_folder(tmp_path: Path) -> None:
    write_cube_obj(tmp_path)
    inner = tmp_path / "scenes"
    inner.mkdir()
    doc = mutated(("scene", "models"), [{"id": "cube", "path": "../cube.obj"}])
    with pytest.raises(SceneFileError, match=r"\.\."):
        _load_doc(inner, doc)
    scene = _load_doc(inner, doc, allow_outside=True)
    assert (
        scene.instances[0].model.source.resolve() == (tmp_path / "cube.obj").resolve()
    )
    # ...et l'aller-retour reste cohérent : save refuse sans allow_outside, accepte avec.
    with pytest.raises(SceneFileError, match="hors du dossier"):
        save_scene(scene, inner / "o.kut3d")
    save_scene(scene, inner / "o.kut3d", allow_outside=True)
    assert_scenes_equal(scene, load_scene(inner / "o.kut3d", allow_outside=True))
    with pytest.raises(SceneFileError):
        load_scene(inner / "o.kut3d")


def test_symlink_escaping_the_folder_is_refused(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    write_cube_obj(outside)
    inside = tmp_path / "proj"
    inside.mkdir()
    link = inside / "evil.obj"
    try:
        link.symlink_to(outside / "cube.obj")
    except (OSError, NotImplementedError):
        pytest.skip("liens symboliques non permis sur ce système")
    doc = mutated(("scene", "models"), [{"id": "evil", "path": "evil.obj"}])
    doc["scene"]["instances"][0]["model"] = "evil"
    with pytest.raises(SceneFileError, match="sort du dossier"):
        _load_doc(inside, doc)
    assert len(_load_doc(inside, doc, allow_outside=True).instances) == 1
    # Un lien qui reste dans le dossier est accepté.
    write_cube_obj(inside, name="real")
    (inside / "alias.obj").symlink_to(inside / "real.obj")
    ok = mutated(("scene", "models"), [{"id": "alias", "path": "alias.obj"}])
    ok["scene"]["instances"][0]["model"] = "alias"
    assert len(_load_doc(inside, ok).instances) == 1


def test_case_is_preserved(cube_dir: Path) -> None:
    folder = cube_dir / "Assets"
    folder.mkdir()
    write_cube_obj(folder, name="Cube")
    scene = Scene([Instance(load_model(folder / "Cube.obj"))], [], _camera())
    save_scene(scene, cube_dir / "s.kut3d")
    entry = json.loads((cube_dir / "s.kut3d").read_text())["scene"]["models"][0]
    assert entry["path"] == "Assets/Cube.obj" and "\\" not in entry["path"]


# --- écriture --------------------------------------------------------------------------------------------


def _programmatic_model() -> Model:
    mesh = Mesh(positions=[[0, 0, 0], [1, 0, 0], [0, 1, 0]], indices=[[0, 1, 2]])
    return Model(meshes=(mesh,))


def test_save_refuses_programmatic_model(tmp_path: Path) -> None:
    scene = Scene([Instance(_programmatic_model())], [], _camera())
    with pytest.raises(SceneFileError, match="pas de fichier source"):
        save_scene(scene, tmp_path / "s.kut3d")
    assert not (tmp_path / "s.kut3d").exists()


def test_save_applies_the_same_path_rules(tmp_path: Path) -> None:
    fake = Model(source=tmp_path / "CON.obj")
    with pytest.raises(SceneFileError, match="réservé"):
        save_scene(Scene([Instance(fake)], [], _camera()), tmp_path / "s.kut3d")
    ghost = Model(source=tmp_path / "ghost.obj")
    with pytest.raises(SceneFileError, match="introuvable"):
        save_scene(Scene([Instance(ghost)], [], _camera()), tmp_path / "s.kut3d")
    with pytest.raises(SceneFileError, match="dossier de destination"):
        save_scene(Scene([], [], _camera()), tmp_path / "nope" / "s.kut3d")


def test_empty_scene_round_trip(tmp_path: Path) -> None:
    save_scene(Scene([], [], _camera()), tmp_path / "s.kut3d")
    back = load_scene(tmp_path / "s.kut3d")
    assert back.instances == () and back.lights == ()


@pytest.mark.parametrize("failing", ["replace", "fsync"])
def test_atomic_write_leaves_no_partial_file(
    cube_dir: Path, monkeypatch: pytest.MonkeyPatch, failing: str
) -> None:
    scene = Scene([Instance(load_model(cube_dir / "cube.obj"))], [], _camera())
    path = cube_dir / "s.kut3d"
    save_scene(scene, path)
    before = path.read_bytes()
    listing = sorted(p.name for p in cube_dir.iterdir())

    def boom(*_a: Any, **_k: Any) -> None:
        raise OSError("disque plein simulé")

    monkeypatch.setattr(os, failing, boom)
    changed = Scene(
        [Instance(load_model(cube_dir / "cube.obj"), name="autre")], [], _camera()
    )
    with pytest.raises(SceneFileError, match="écriture impossible"):
        save_scene(changed, path)
    monkeypatch.undo()
    assert path.read_bytes() == before  # l'ancien fichier est intact
    assert (
        sorted(p.name for p in cube_dir.iterdir()) == listing
    )  # et aucun temporaire ne traîne
    load_scene(path)


def test_atomic_write_does_not_create_file_on_failure(
    cube_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        os, "replace", lambda *_a, **_k: (_ for _ in ()).throw(OSError("x"))
    )
    with pytest.raises(SceneFileError):
        save_scene(Scene([], [], _camera()), cube_dir / "new.kut3d")
    assert not (cube_dir / "new.kut3d").exists()
    assert not [p for p in cube_dir.iterdir() if p.suffix == ".tmp"]


# --- dépendances -----------------------------------------------------------------------------------------


def test_model_dependencies_obj_and_gltf(tmp_path: Path) -> None:
    write_cube_obj(tmp_path)
    obj = load_model(tmp_path / "cube.obj")
    assert [p.name for p in obj.dependencies] == [
        "cube.mtl",
        "normal map.png",
        "tex.png",
    ]
    assert all(p.is_absolute() and p == p.resolve() for p in obj.dependencies)
    write_gltf(tmp_path, "bin", name="quad")
    gltf = load_model(tmp_path / "quad.gltf")
    assert "quad.bin" in [p.name for p in gltf.dependencies]
    write_gltf(tmp_path, "glb", name="rig")
    # Un GLB n'a pas de .bin externe ; sa seule dépendance est la texture référencée par fichier.
    assert [p.name for p in load_model(tmp_path / "rig.glb").dependencies] == [
        "normal map.png"
    ]
    write_cube_obj(tmp_path, mtl=False, name="bare")
    assert load_model(tmp_path / "bare.obj").dependencies == ()
    assert Model().dependencies == ()


def test_dependency_files(tmp_path: Path) -> None:
    scene = rich_scene(tmp_path)
    path = tmp_path / "scene.kut3d"
    save_scene(scene, path)
    files = dependency_files(path)
    assert files[0] == path.resolve()
    assert len(set(files)) == len(files)
    assert all(f.is_absolute() and f.is_file() for f in files)
    names = [f.name for f in files]
    for expected in (
        "cube.obj",
        "cube.mtl",
        "tex.png",
        "normal map.png",
        "quad.gltf",
        "quad.bin",
        "rig.glb",
    ):
        assert expected in names
    assert [f.as_posix() for f in files[1:]] == sorted(f.as_posix() for f in files[1:])
    assert dependency_files(path) == files


def test_dependency_files_obeys_confinement(cube_dir: Path) -> None:
    doc = mutated(("scene", "models"), [{"id": "cube", "path": "../cube.obj"}])
    path = write_doc(cube_dir / "s.kut3d", doc)
    with pytest.raises(SceneFileError):
        dependency_files(path)


# --- défenses rares --------------------------------------------------------------------------------------


def test_oversized_integer_and_long_path_refused(cube_dir: Path) -> None:
    path = cube_dir / "s.kut3d"
    path.write_text(
        json.dumps(minimal_document()).replace('"far": 100', '"far": 1' + "0" * 400)
    )
    with pytest.raises(SceneFileError, match="nombre"):
        load_scene(path)
    with pytest.raises(SceneFileError, match="trop long"):
        _load_doc(
            cube_dir, mutated(("scene", "models"), [{"id": "m", "path": "a/" * 600}])
        )


def test_unreadable_file_and_scene_constructor_error(
    cube_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_doc(cube_dir / "s.kut3d", minimal_document())

    def deny(*_a: Any, **_k: Any) -> None:
        raise OSError("refusé")

    with monkeypatch.context() as ctx:
        ctx.setattr(Path, "open", deny)
        with pytest.raises(SceneFileError, match="lecture impossible"):
            load_scene(path)
    with monkeypatch.context() as ctx:
        ctx.setattr(
            "kut3d.scene_io_read.Scene",
            lambda **_k: (_ for _ in ()).throw(ValueError("x")),
        )
        with pytest.raises(SceneFileError, match="scene"):
            load_scene(path)


def test_save_defenses(cube_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scene = Scene([Instance(load_model(cube_dir / "cube.obj"))], [], _camera())
    with monkeypatch.context() as ctx:  # Windows : modèle sur un autre lecteur
        ctx.setattr(
            os.path,
            "relpath",
            lambda *_a, **_k: (_ for _ in ()).throw(ValueError("drive")),
        )
        with pytest.raises(SceneFileError, match="autre lecteur"):
            save_scene(scene, cube_dir / "s.kut3d")
    with monkeypatch.context() as ctx:
        ctx.setattr("kut3d.scene_io_write.MAX_SCENE_BYTES", 10)
        with pytest.raises(SceneFileError, match="trop volumineuse"):
            save_scene(scene, cube_dir / "s.kut3d")
    with (
        monkeypatch.context() as ctx
    ):  # le nettoyage du temporaire échoue lui-même : l'erreur d'origine reste
        ctx.setattr(
            os, "replace", lambda *_a, **_k: (_ for _ in ()).throw(OSError("x"))
        )
        ctx.setattr(os, "unlink", lambda *_a, **_k: (_ for _ in ()).throw(OSError("y")))
        with pytest.raises(SceneFileError, match="écriture impossible"):
            save_scene(scene, cube_dir / "s.kut3d")
    assert not (cube_dir / "s.kut3d").exists()
    with monkeypatch.context() as ctx:  # mkstemp échoue avant tout temporaire
        ctx.setattr(
            "tempfile.mkstemp", lambda *_a, **_k: (_ for _ in ()).throw(OSError("z"))
        )
        with pytest.raises(SceneFileError, match="écriture impossible"):
            save_scene(scene, cube_dir / "s.kut3d")
