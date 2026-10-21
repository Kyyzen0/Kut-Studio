"""``core.scene3d`` : validation, dépendances et signature d'un fichier ``.kut3d`` (lot B2a), sans rien rendre."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from core import scene3d
from core.scene3d import (
    INVALID_PREFIX,
    MAX_PATH_CHARS,
    MISSING_SIGNATURE,
    Scene3DError,
    dependencies,
    has_scene_suffix,
    scene_signature,
    validate,
)
from tests.kut3d_scene_samples import minimal_document, mutated, write_doc
from tests.scene3d_samples import write_scene

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _fresh_cache():
    scene3d.clear_signature_cache()
    yield
    scene3d.clear_signature_cache()


def test_importing_core_scene3d_does_not_import_kut3d_nor_numpy():
    code = (
        "import sys; sys.path.insert(0, %r); import core.scene3d, core.graphics; "
        "bad = [m for m in ('kut3d', 'numpy', 'PySide6') if m in sys.modules]; assert not bad, bad"
        % str(ROOT)
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_the_suffix_is_checked_without_the_disk():
    assert has_scene_suffix("/x/a.kut3d") and has_scene_suffix("A.KUT3D")
    assert (
        not has_scene_suffix("/x/a.png")
        and not has_scene_suffix("")
        and not has_scene_suffix(None)
    )  # type: ignore[arg-type]


def test_a_valid_scene_is_accepted(tmp_path):
    assert validate(write_scene(tmp_path)) is None
    assert validate(str(write_scene(tmp_path, "UPPER.KUT3D"))) is None


def _refused(path) -> str:
    with pytest.raises(Scene3DError) as caught:
        validate(path)
    return str(caught.value)


def test_a_scene_error_is_a_value_error_in_french(tmp_path):
    assert issubclass(Scene3DError, ValueError)
    assert "introuvable" in _refused(tmp_path / "absent.kut3d")


def test_wrong_paths_are_refused_without_copying_them_whole(tmp_path):
    assert ".kut3d" in _refused(tmp_path / "photo.png")
    assert ".kut3d" in _refused("")
    assert ".kut3d" in _refused("   ")
    huge = "x" * (MAX_PATH_CHARS + 10) + ".kut3d"
    message = _refused(huge)
    assert len(message) < 200 and huge not in message
    assert len(_refused("a\x00b.kut3d")) < 200
    long_name = tmp_path / ("y" * 5000 + ".png")
    assert len(_refused(long_name)) < 200
    directory = tmp_path / "folder.kut3d"
    directory.mkdir()
    assert _refused(directory)


def test_a_broken_scene_is_refused(tmp_path):
    bad = tmp_path / "bad.kut3d"
    bad.write_text("{ pas du json", encoding="utf-8")
    assert _refused(bad)
    newer = write_doc(tmp_path / "newer.kut3d", {**minimal_document(), "version": 99})
    assert "plus récent" in _refused(newer)
    write_scene(tmp_path)
    no_model = write_doc(tmp_path / "nomodel.kut3d", minimal_document())
    (tmp_path / "cube.obj").unlink()
    assert _refused(no_model)


def test_a_model_outside_the_folder_is_refused(tmp_path):
    inner = tmp_path / "inner"
    inner.mkdir()
    (tmp_path / "cube.obj").write_text("v 0 0 0\n", encoding="utf-8")
    doc = mutated(("scene", "models", 0, "path"), "../cube.obj")
    path = write_doc(inner / "s.kut3d", doc)
    assert _refused(path)
    with pytest.raises(Scene3DError):
        dependencies(path)
    assert scene_signature(path).startswith(INVALID_PREFIX)


def test_dependencies_lists_the_scene_then_its_files(tmp_path):
    scene = write_scene(tmp_path)
    found = dependencies(scene)
    assert found[0] == scene.resolve()
    names = [item.name for item in found]
    assert "cube.obj" in names and names == [scene.name, *sorted(names[1:])]
    assert all(item.is_absolute() for item in found) and len(set(found)) == len(found)


def test_the_signature_is_stable_and_follows_every_dependency(tmp_path):
    scene = write_scene(tmp_path)
    first = scene_signature(scene)
    assert (
        first == scene_signature(scene)
        and not first.startswith(INVALID_PREFIX)
        and first != MISSING_SIGNATURE
    )
    scene3d.clear_signature_cache()
    assert scene_signature(scene) == first  # même contenu : même empreinte, sans mémo
    obj = tmp_path / "cube.obj"
    obj.write_text(obj.read_text(encoding="utf-8") + "# changé\n", encoding="utf-8")
    changed = scene_signature(scene)
    assert changed != first
    texture = tmp_path / "tex.png"
    texture.write_bytes(texture.read_bytes() + b"0")
    assert scene_signature(scene) not in (first, changed)


def test_a_touch_without_content_change_changes_the_signature(tmp_path):
    scene = write_scene(tmp_path)
    first = scene_signature(scene)
    info = scene.stat()
    os.utime(scene, ns=(info.st_atime_ns, info.st_mtime_ns + 5_000_000_000))
    assert scene_signature(scene) != first


def test_a_content_change_with_the_same_size_is_seen_by_the_fingerprint(tmp_path):
    scene = write_scene(tmp_path)
    first = scene_signature(scene)
    info = scene.stat()
    data = bytearray(scene.read_bytes())
    data[-3] = (
        ord("X") if data[-3] != ord("X") else ord("Y")
    )  # même taille, début/fin différents
    scene.write_bytes(bytes(data))
    os.utime(
        scene, ns=(info.st_atime_ns, info.st_mtime_ns)
    )  # même date : seul le contenu a changé
    scene3d.clear_signature_cache()
    assert scene_signature(scene) != first


def test_the_signature_is_memoized_and_revalidated_by_stat(tmp_path, monkeypatch):
    scene = write_scene(tmp_path)
    calls = []
    real = scene3d.dependencies
    monkeypatch.setattr(
        scene3d, "dependencies", lambda path: calls.append(path) or real(path)
    )
    first = scene_signature(scene)
    for _ in range(5):
        assert scene_signature(scene) == first
    assert len(calls) == 1  # un seul passage par l'analyse de la scène
    (tmp_path / "cube.obj").write_text("v 1 1 1\n", encoding="utf-8")
    scene_signature(scene)
    assert len(calls) == 2  # un fichier a bougé : on recalcule


def test_missing_and_broken_scenes_give_a_signature_never_an_exception(tmp_path):
    assert scene_signature(tmp_path / "absent.kut3d") == MISSING_SIGNATURE
    assert (
        scene_signature("") == MISSING_SIGNATURE
        and scene_signature(tmp_path / "x.png") == MISSING_SIGNATURE
    )
    assert scene_signature("a\x00.kut3d") == MISSING_SIGNATURE
    scene = write_scene(tmp_path)
    good = scene_signature(scene)
    (tmp_path / "cube.obj").unlink()
    broken = scene_signature(scene)
    assert broken.startswith(INVALID_PREFIX) and broken != good
    (tmp_path / "cube.obj").write_text(
        "v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n", encoding="utf-8"
    )
    assert not scene_signature(scene).startswith(
        INVALID_PREFIX
    )  # un échec n'est pas mémorisé : le fichier revenu se voit
    bad = tmp_path / "bad.kut3d"
    bad.write_text("{", encoding="utf-8")
    first_bad = scene_signature(bad)
    bad.write_text("{{", encoding="utf-8")
    assert first_bad.startswith(INVALID_PREFIX) and scene_signature(bad) != first_bad


def test_a_deleted_scene_gives_the_sentinel_even_when_memoized(tmp_path):
    scene = write_scene(tmp_path)
    scene_signature(scene)
    scene.unlink()
    assert scene_signature(scene) == MISSING_SIGNATURE
    assert json.dumps(scene_signature(scene))  # sérialisable : une chaîne


def test_the_memo_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(scene3d, "_MEMO_LIMIT", 2)
    scenes = []
    for index in range(4):
        folder = tmp_path / f"s{index}"
        folder.mkdir()
        scenes.append(write_scene(folder))
        scene_signature(scenes[-1])
    assert len(scene3d._MEMO) == 2
