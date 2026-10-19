"""Erreurs du système de fichiers : jamais une `OSError` brute, toujours une erreur du paquet, bornée, en français.

Les noms trop longs lèvent `OSError` (ENAMETOOLONG) sous Python 3.11 et 3.13 mais pas sous 3.14 (`is_file` l'avale) :
la règle des 255 octets est donc vérifiée sans accès disque, et chaque conversion est aussi testée en forçant l'erreur."""

from __future__ import annotations

import errno
import json
import os
import shutil
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from kut3d import (
    Camera,
    Instance,
    ModelImportError,
    Scene,
    SceneFileError,
    dependency_files,
    load_model,
    load_scene,
    save_scene,
)
from kut3d.scene_io_paths import MAX_SEGMENT_BYTES, check_relative_path
from tests.kut3d_samples import write_cube_obj, write_gltf
from tests.kut3d_scene_samples import mutated, write_doc

CODES = [errno.ENAMETOOLONG, errno.EACCES, errno.ELOOP]
needs_symlink = pytest.mark.skipif(
    sys.platform == "win32",
    reason="liens symboliques : droits particuliers sous Windows",
)


@pytest.fixture
def cube_dir(tmp_path: Path) -> Path:
    write_cube_obj(tmp_path)
    return tmp_path


def _load(folder: Path, path: str, **kw: Any) -> Scene:
    doc = mutated(("scene", "models"), [{"id": "cube", "path": path}])
    return load_scene(write_doc(folder / "s.kut3d", doc), **kw)


def _scene(folder: Path) -> Scene:
    model = load_model(folder / "cube.obj")
    return Scene([Instance(model)], [], Camera(position=(0, 0, 5), target=(0, 0, 0)))


def _raise_for(cls: type, name: str, exc: BaseException) -> Callable[..., Any]:
    """Remplace `cls.<méthode>` : lève `exc` pour le chemin nommé `name`, sinon appelle l'original."""

    def make(real: Callable[..., Any]) -> Callable[..., Any]:
        def fake(self: Path, *args: Any, **kwargs: Any) -> Any:
            if self.name == name:
                raise exc
            return real(self, *args, **kwargs)

        return fake

    return make


def _patch(
    monkeypatch: pytest.MonkeyPatch, method: str, name: str, exc: BaseException
) -> None:
    monkeypatch.setattr(
        Path, method, _raise_for(Path, name, exc)(getattr(Path, method))
    )


# --- règle des 255 octets par composant -------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    ["a" * 255, "é" * 127 + "a", "d/" + "b" * 255, "/".join(["c" * 200] * 5)],
)
def test_segment_up_to_255_bytes_is_accepted(raw: str) -> None:
    assert check_relative_path(raw, "p") == raw


@pytest.mark.parametrize("raw", ["a" * 256, "é" * 128, "d/" + "b" * 256, "😀" * 64])
def test_segment_over_255_bytes_is_refused_without_copying_input(raw: str) -> None:
    with pytest.raises(SceneFileError, match=f"{MAX_SEGMENT_BYTES} octets") as info:
        check_relative_path(raw, "p")
    assert len(str(info.value)) < 400


def test_lone_surrogate_is_refused() -> None:
    with pytest.raises(SceneFileError, match="caractère invalide"):
        check_relative_path("a\ud800b.obj", "p")


def test_long_component_is_refused_by_load_scene(cube_dir: Path) -> None:
    for raw in ("z" * 256, "é" * 128, "ok/" + "y" * 900):
        with pytest.raises(SceneFileError, match="octets") as info:
            _load(cube_dir, raw)
        assert len(str(info.value)) < 400


def test_255_byte_file_name_loads(cube_dir: Path) -> None:
    name = "x" * 251 + ".obj"
    assert len(name.encode()) == 255
    shutil.copy(cube_dir / "cube.obj", cube_dir / name)
    assert len(_load(cube_dir, name).instances) == 1


def test_multibyte_file_name_loads(cube_dir: Path) -> None:
    name = "é" * 125 + ".obj"  # 254 octets
    shutil.copy(cube_dir / "cube.obj", cube_dir / name)
    assert len(_load(cube_dir, name).instances) == 1


# --- conversions OSError / RuntimeError / ValueError -> SceneFileError ------------------------------------


@pytest.mark.parametrize("code", CODES)
def test_resolve_model_path_converts_os_errors(
    cube_dir: Path, monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    _patch(monkeypatch, "is_file", "cube.obj", OSError(code, "x" * 2000))
    with pytest.raises(SceneFileError) as info:
        _load(cube_dir, "cube.obj")
    assert len(str(info.value)) < 400 and "xxx" not in str(info.value)


def test_resolve_model_path_converts_symlink_loop_runtime_error(
    cube_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch(monkeypatch, "resolve", "cube.obj", RuntimeError("Symlink loop from 'x'"))
    with pytest.raises(SceneFileError, match="boucle de liens"):
        _load(cube_dir, "cube.obj")


@needs_symlink
def test_real_symlink_loop_in_model_path(cube_dir: Path) -> None:
    (cube_dir / "loop.obj").symlink_to("loop.obj")
    with pytest.raises(SceneFileError):
        _load(cube_dir, "loop.obj")


def test_resolve_model_path_converts_value_error(
    cube_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch(monkeypatch, "is_file", "cube.obj", ValueError("embedded null byte"))
    with pytest.raises(SceneFileError, match="chemin invalide"):
        _load(cube_dir, "cube.obj")


def test_read_document_null_byte_in_path(tmp_path: Path) -> None:
    with pytest.raises(SceneFileError, match="introuvable ou illisible"):
        load_scene(str(tmp_path / "a\x00b.kut3d"))


def test_read_document_name_too_long(tmp_path: Path) -> None:
    with pytest.raises(SceneFileError, match="illisible"):
        load_scene(tmp_path / ("n" * 300 + ".kut3d"))


@pytest.mark.parametrize("code", CODES)
def test_save_scene_converts_is_dir_error(
    cube_dir: Path, monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    scene = _scene(cube_dir)
    sub = cube_dir / "sub"
    sub.mkdir()
    _patch(monkeypatch, "is_dir", "sub", OSError(code, "boom"))
    with pytest.raises(SceneFileError):
        save_scene(scene, sub / "s.kut3d")


def test_save_scene_long_parent_and_long_file_name(cube_dir: Path) -> None:
    scene = _scene(cube_dir)
    with pytest.raises(SceneFileError):
        save_scene(scene, cube_dir / ("d" * 300) / "s.kut3d")
    with pytest.raises(SceneFileError):
        save_scene(scene, cube_dir / ("n" * 300 + ".kut3d"))
    assert sorted(p.name for p in cube_dir.iterdir()) == [
        "cube.mtl",
        "cube.obj",
        "normal map.png",
        "tex.png",
    ]


def test_save_scene_missing_folder(cube_dir: Path) -> None:
    with pytest.raises(SceneFileError, match="n'existe pas"):
        save_scene(_scene(cube_dir), cube_dir / "absent" / "s.kut3d")


@pytest.mark.skipif(
    sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="droits POSIX, hors superutilisateur",
)
def test_save_scene_read_only_folder(cube_dir: Path) -> None:
    scene = _scene(cube_dir)
    ro = cube_dir / "ro"
    ro.mkdir()
    shutil.copy(cube_dir / "cube.obj", ro / "cube.obj")
    ro.chmod(0o500)
    try:
        with pytest.raises(SceneFileError, match="écriture impossible"):
            save_scene(scene, ro / "s.kut3d", allow_outside=True)
        assert [p.name for p in ro.iterdir()] == ["cube.obj"]
    finally:
        ro.chmod(0o700)


@pytest.mark.parametrize("code", CODES)
def test_write_atomic_replace_error(
    cube_dir: Path, monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    scene = _scene(cube_dir)

    def boom(src: Any, dst: Any) -> None:
        raise OSError(code, "boom")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(SceneFileError, match="écriture impossible"):
        save_scene(scene, cube_dir / "s.kut3d")
    assert not any(p.suffix == ".tmp" for p in cube_dir.iterdir())
    assert not (cube_dir / "s.kut3d").exists()


@pytest.mark.parametrize(
    "exc",
    [
        OSError(errno.ELOOP, "x"),
        OSError(errno.EACCES, "x"),
        RuntimeError("Symlink loop"),
    ],
)
def test_save_scene_source_resolution_errors(
    cube_dir: Path, monkeypatch: pytest.MonkeyPatch, exc: BaseException
) -> None:
    scene = _scene(cube_dir)
    _patch(monkeypatch, "resolve", "cube.obj", exc)
    with pytest.raises(SceneFileError):
        save_scene(scene, cube_dir / "s.kut3d")
    assert not (cube_dir / "s.kut3d").exists()


@pytest.mark.parametrize("code", CODES)
def test_save_scene_source_is_file_error(
    cube_dir: Path, monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    scene = _scene(cube_dir)
    _patch(monkeypatch, "is_file", "cube.obj", OSError(code, "x"))
    with pytest.raises(SceneFileError):
        save_scene(scene, cube_dir / "s.kut3d")


def test_dependency_files_resolve_error(
    cube_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    save_scene(_scene(cube_dir), cube_dir / "s.kut3d")
    _patch(monkeypatch, "resolve", "s.kut3d", OSError(errno.ELOOP, "x"))
    with pytest.raises(SceneFileError, match="boucle de liens"):
        dependency_files(cube_dir / "s.kut3d")


# --- importeurs : un chemin tiré d'un fichier de modèle ne fuit pas d'OSError -----------------------------


@pytest.mark.parametrize("code", CODES)
def test_load_model_converts_exists_and_stat_errors(
    cube_dir: Path, monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    _patch(monkeypatch, "exists", "cube.obj", OSError(code, "x"))
    with pytest.raises(ModelImportError, match="chemin inutilisable"):
        load_model(cube_dir / "cube.obj")
    monkeypatch.undo()
    _patch(monkeypatch, "stat", "cube.obj", OSError(code, "x"))
    # ELOOP : `exists` l'interprète comme « absent » avant Python 3.14 ; les autres codes doivent être convertis
    expected = (
        (ModelImportError, FileNotFoundError)
        if code == errno.ELOOP
        else ModelImportError
    )
    with pytest.raises(expected):
        load_model(cube_dir / "cube.obj")


def test_load_model_long_name_is_not_a_raw_oserror(tmp_path: Path) -> None:
    with pytest.raises((ModelImportError, FileNotFoundError)):
        load_model(tmp_path / ("n" * 300 + ".obj"))
    with pytest.raises((ModelImportError, FileNotFoundError)):
        load_model(str(tmp_path / "a\x00b.obj"))


def test_obj_texture_with_too_long_name_is_a_warning(tmp_path: Path) -> None:
    write_cube_obj(tmp_path, map_line="map_Kd " + "t" * 300 + ".png")
    model = load_model(tmp_path / "cube.obj")
    assert any("texture introuvable" in w for w in model.warnings)


@needs_symlink
def test_obj_texture_symlink_loop_is_a_warning(tmp_path: Path) -> None:
    write_cube_obj(tmp_path, map_line="map_Kd loop.png")
    (tmp_path / "loop.png").symlink_to("loop.png")
    model = load_model(tmp_path / "cube.obj")
    assert any("texture introuvable" in w for w in model.warnings)


def test_obj_mtllib_with_too_long_name_is_a_warning(tmp_path: Path) -> None:
    obj = write_cube_obj(tmp_path, texture=False)
    obj.write_text(
        obj.read_text().replace("mtllib cube.mtl", "mtllib " + "m" * 300 + ".mtl")
    )
    model = load_model(obj)
    assert any("MTL introuvable" in w for w in model.warnings)


def _gltf_with_uri(folder: Path, uri: str, name: str = "quad") -> Path:
    path = write_gltf(folder, "bin", name=name)
    doc = json.loads(path.read_text())
    doc["buffers"][0]["uri"] = uri
    path.write_text(json.dumps(doc))
    return path


@pytest.mark.parametrize("uri", ["q" * 300 + ".bin", "a%00b.bin"])
def test_gltf_external_buffer_errors_are_model_import_errors(
    tmp_path: Path, uri: str
) -> None:
    with pytest.raises(ModelImportError, match="introuvable"):
        load_model(_gltf_with_uri(tmp_path, uri))


@needs_symlink
def test_gltf_external_buffer_symlink_loop(tmp_path: Path) -> None:
    path = _gltf_with_uri(tmp_path, "loop.bin")
    (tmp_path / "loop.bin").symlink_to("loop.bin")
    with pytest.raises(ModelImportError, match="introuvable"):
        load_model(path)


def test_save_scene_null_byte_in_destination(cube_dir: Path) -> None:
    with pytest.raises(SceneFileError):
        save_scene(_scene(cube_dir), cube_dir / "a\x00b.kut3d")


@pytest.mark.parametrize(
    "exc", [OSError(errno.EACCES, "x"), OSError(errno.ENAMETOOLONG, "x")]
)
def test_obj_texture_is_file_error_is_a_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exc: OSError
) -> None:
    obj = write_cube_obj(tmp_path)
    _patch(monkeypatch, "is_file", "tex.png", exc)
    model = load_model(obj)
    assert any("texture introuvable" in w for w in model.warnings)


def test_runtime_error_from_resolve_is_a_model_import_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    obj = write_cube_obj(tmp_path)
    # `resolve()` du dossier du modèle (MTL, textures) : Python < 3.13 signale une boucle de liens par RuntimeError
    _patch(monkeypatch, "resolve", tmp_path.name, RuntimeError("Symlink loop from 'x'"))
    with pytest.raises(ModelImportError):
        load_model(obj)
