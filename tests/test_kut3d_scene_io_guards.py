"""`.kut3d` : aller-retour exact des lumières, jeu de dépendances complet, écrivain aussi strict que le lecteur."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from kut3d import (
    AmbientLight,
    Camera,
    DirectionalLight,
    Instance,
    PointLight,
    Scene,
    SceneFileError,
    dependency_files,
    load_model,
    load_scene,
    save_scene,
)
from tests.kut3d_samples import write_cube_obj, write_gltf
from tests.kut3d_scene_samples import assert_scenes_equal, minimal_document, write_doc


@pytest.fixture
def cube_dir(tmp_path: Path) -> Path:
    write_cube_obj(tmp_path)
    return tmp_path


def _camera() -> Camera:
    return Camera(position=(0, 0, 5), target=(0, 0, 0))


def _listing(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir())


# --- 1. normalisation idempotente de DirectionalLight ---------------------------------------------------


@pytest.mark.parametrize(
    "direction",
    [(0.3, -0.7, 0.2), (1e-3, 2, 5), (0.3, -1, -0.2), (7, 7, 7), (1e-9, 0, -3e-9)],
)
def test_directional_light_round_trip_is_exact(
    cube_dir: Path, direction: tuple[float, ...]
) -> None:
    scene = Scene([], [DirectionalLight(direction=direction)], _camera())
    path = cube_dir / "s.kut3d"
    save_scene(scene, path)
    first = path.read_bytes()
    back = load_scene(path)
    assert_scenes_equal(scene, back)
    save_scene(back, path)
    assert path.read_bytes() == first


def test_directional_normalization_is_idempotent_random() -> None:
    rng = np.random.default_rng(20261019)
    for _ in range(200):
        raw = rng.normal(size=3) * 10.0 ** int(rng.integers(-3, 4))
        once = DirectionalLight(raw).direction
        again = DirectionalLight(once).direction
        assert np.array_equal(once, again), raw
        assert abs(float(np.linalg.norm(once)) - 1.0) <= 1e-12


def test_directional_still_normalizes_and_refuses() -> None:
    d = DirectionalLight((0, -3, 4)).direction
    assert np.allclose(d, (0, -0.6, 0.8))
    for bad in ((0, 0, 0), (1e-13, 0, 0), (float("nan"), 0, 1), (float("inf"), 0, 0)):
        with pytest.raises(ValueError):
            DirectionalLight(bad)
    # Un vecteur à 1e-9 de l'unité est renormalisé (au-delà de la tolérance).
    near = DirectionalLight((1.0 + 1e-9, 0, 0)).direction
    assert float(near[0]) == 1.0


# --- 2. dependency_files : modèles déclarés sans instance -------------------------------------------------


def _unused_model_document() -> dict[str, Any]:
    doc = minimal_document()
    doc["scene"]["instances"] = []
    return doc


def test_dependency_files_includes_declared_models_without_instance(
    cube_dir: Path,
) -> None:
    path = write_doc(cube_dir / "s.kut3d", _unused_model_document())
    names = [f.name for f in dependency_files(path)]
    assert names[0] == "s.kut3d"
    for expected in ("cube.obj", "cube.mtl", "tex.png", "normal map.png"):
        assert expected in names


def test_dependency_set_is_complete_in_a_blank_folder(cube_dir: Path) -> None:
    sub = cube_dir / "scenes"
    sub.mkdir()
    shutil.move(str(cube_dir / "cube.obj"), sub / "cube.obj")
    for extra in ("cube.mtl", "tex.png", "normal map.png"):
        shutil.move(str(cube_dir / extra), sub / extra)
    write_cube_obj(sub, name="inutile", mtl=False)
    doc = _unused_model_document()
    doc["scene"]["models"].append({"id": "autre", "path": "inutile.obj"})
    path = write_doc(sub / "s.kut3d", doc)
    files = dependency_files(path)
    fresh = cube_dir / "copie"
    fresh.mkdir()
    for f in files:
        target = fresh / f.relative_to(sub.resolve())
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(f, target)
    back = load_scene(fresh / "s.kut3d")
    assert back.instances == ()
    assert sorted(f.name for f in fresh.iterdir()) == sorted(f.name for f in files)


# --- 3. l'écrivain applique les bornes du lecteur AVANT de remplacer la destination --------------------------


def _scene_with(cube_dir: Path, **instance_kwargs: Any) -> Scene:
    model = load_model(cube_dir / "cube.obj")
    return Scene([Instance(model, **instance_kwargs)], [], _camera())


def test_name_at_limit_is_written_and_read(cube_dir: Path) -> None:
    scene = _scene_with(cube_dir, name="n" * 200)
    save_scene(scene, cube_dir / "s.kut3d")
    assert load_scene(cube_dir / "s.kut3d").instances[0].name == "n" * 200


def _refused_write_keeps_destination(
    folder: Path, scene: Scene, match: str, **kw: Any
) -> None:
    path = folder / "s.kut3d"
    save_scene(Scene([], [], _camera()), path)
    before = path.read_bytes()
    listing = _listing(folder)
    with pytest.raises(SceneFileError, match=match) as info:
        save_scene(scene, path, **kw)
    assert len(str(info.value)) < 400
    assert path.read_bytes() == before
    assert _listing(folder) == listing  # aucun temporaire résiduel


def test_name_over_limit_refused_at_write(cube_dir: Path) -> None:
    scene = _scene_with(cube_dir, name="n" * 201)
    _refused_write_keeps_destination(cube_dir, scene, "name")
    assert not (cube_dir / "absent.kut3d").exists()
    with pytest.raises(SceneFileError):
        save_scene(scene, cube_dir / "absent.kut3d")
    assert not (cube_dir / "absent.kut3d").exists()


def test_clip_name_limit(tmp_path: Path) -> None:
    write_gltf(tmp_path, "bin", name="quad")
    gltf = tmp_path / "quad.gltf"
    original = gltf.read_text(encoding="utf-8")
    for length, ok in ((200, True), (201, False)):
        gltf.write_text(
            original.replace('"wave"', f'"{"c" * length}"'), encoding="utf-8"
        )
        scene = Scene([Instance(load_model(gltf), clip="c" * length)], [], _camera())
        if ok:
            save_scene(scene, tmp_path / "ok.kut3d")
            back = load_scene(tmp_path / "ok.kut3d")
            assert back.instances[0].clip == "c" * 200
        else:
            _refused_write_keeps_destination(tmp_path, scene, "clip")


def test_instance_and_light_counts_at_limit(cube_dir: Path) -> None:
    model = load_model(cube_dir / "cube.obj")
    lights = [AmbientLight() for _ in range(16)]
    scene = Scene([Instance(model) for _ in range(2048)], lights, _camera())
    save_scene(scene, cube_dir / "s.kut3d")
    back = load_scene(cube_dir / "s.kut3d")
    assert len(back.instances) == 2048 and len(back.lights) == 16
    with pytest.raises(ValueError):  # Scene refuse déjà la limite + 1
        Scene([Instance(model) for _ in range(2049)], [], _camera())
    with pytest.raises(ValueError):
        Scene([], lights + [PointLight(position=(0, 0, 0))], _camera())


def test_model_count_limit(cube_dir: Path) -> None:
    from kut3d.scene_io_fields import MAX_MODELS

    def scene_of(count: int) -> Scene:
        return Scene(
            [Instance(load_model(cube_dir / "cube.obj")) for _ in range(count)],
            [],
            _camera(),
        )

    save_scene(scene_of(MAX_MODELS), cube_dir / "s.kut3d")
    assert len(load_scene(cube_dir / "s.kut3d").instances) == MAX_MODELS
    _refused_write_keeps_destination(cube_dir, scene_of(MAX_MODELS + 1), "trop de")


def test_path_length_limit(cube_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scene = _scene_with(cube_dir)
    limit = len("cube.obj")
    monkeypatch.setattr("kut3d.scene_io_paths.MAX_PATH_CHARS", limit)
    save_scene(scene, cube_dir / "ok.kut3d")
    load_scene(cube_dir / "ok.kut3d")
    monkeypatch.setattr("kut3d.scene_io_paths.MAX_PATH_CHARS", limit - 1)
    _refused_write_keeps_destination(cube_dir, scene, "trop long")


def test_size_limit_refused_before_replacing(
    cube_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scene = _scene_with(cube_dir)
    path = cube_dir / "s.kut3d"
    save_scene(Scene([], [], _camera()), path)
    before = path.read_bytes()
    listing = _listing(cube_dir)
    monkeypatch.setattr("kut3d.scene_io_write.MAX_SCENE_BYTES", 10)
    with pytest.raises(SceneFileError, match="trop volumineuse"):
        save_scene(scene, path)
    assert path.read_bytes() == before and _listing(cube_dir) == listing


def test_non_finite_value_injected_after_construction_is_refused(
    cube_dir: Path,
) -> None:
    cam = _camera()
    object.__setattr__(cam, "near", float("nan"))
    scene = Scene([], [], cam)
    _refused_write_keeps_destination(cube_dir, scene, "finis?|nombre|valeur")


def test_degenerate_root_transform_refused_at_write(cube_dir: Path) -> None:
    model = load_model(cube_dir / "cube.obj").with_root_transform(np.zeros((4, 4)))
    scene = Scene([Instance(model)], [], _camera())
    _refused_write_keeps_destination(cube_dir, scene, "dégénérée")


def test_non_text_name_refused_at_write(cube_dir: Path) -> None:
    model = load_model(cube_dir / "cube.obj")
    inst = Instance(model)
    object.__setattr__(inst, "name", 12)
    _refused_write_keeps_destination(cube_dir, Scene([inst], [], _camera()), "texte")


def test_valid_scene_still_written_identically_by_validation(cube_dir: Path) -> None:
    scene = _scene_with(cube_dir, name="ok")
    save_scene(scene, cube_dir / "a.kut3d")
    save_scene(load_scene(cube_dir / "a.kut3d"), cube_dir / "b.kut3d")
    assert (cube_dir / "a.kut3d").read_bytes() == (cube_dir / "b.kut3d").read_bytes()
