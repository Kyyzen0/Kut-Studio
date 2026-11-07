"""Texte 3D dans le fichier `.kut3d` : format 3 conditionnel, aller-retour, lecture bornée avant toute génération, refus propres."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

import kut3d
from kut3d import (
    AmbientLight,
    Camera,
    Cube,
    Instance,
    Material,
    Scene,
    SceneFileError,
    SceneVersionError,
    Text3D,
    load_scene,
    primitive_instance,
    save_scene,
)
from kut3d import primitives as prim
from tests.kut3d_primitive_scenes import six_forms


def _camera() -> Camera:
    return Camera(position=(0, 0, 5), target=(0, 0, 0))


def _doc(*models: dict[str, Any], version: int = 3) -> dict[str, Any]:
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


def _text(
    params: dict[str, Any] | None = None, mid: str = "t", **extra: Any
) -> dict[str, Any]:
    return {
        "id": mid,
        "primitive": {"kind": "text", **(params or {"text": "Kut"})},
        **extra,
    }


def _forbid_generation(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    calls: list[int] = []

    def boom(spec: Any) -> Any:
        calls.append(1)
        raise AssertionError("un maillage a été généré avant la fin de la validation")

    monkeypatch.setattr("kut3d.primitive_mesh._GENERATORS", {Text3D: boom, Cube: boom})
    return calls


# --- format et aller-retour ------------------------------------------------------------------------------


def test_a_text_is_stored_by_its_parameters_in_a_version_3_file(tmp_path: Path) -> None:
    scene = Scene(
        [
            primitive_instance(
                "text",
                text="Kut 3D",
                size=2.0,
                depth=0.5,
                bevel=0.01,
                align="left",
                name="titre",
            )
        ],
        [],
        _camera(),
    )
    path = tmp_path / "t.kut3d"
    save_scene(scene, path)
    raw = path.read_bytes()
    doc = json.loads(raw)
    assert (
        doc["version"] == kut3d.TEXT_FORMAT_VERSION == 3
        and kut3d.PRIMITIVES_FORMAT_VERSION == 2
    )
    (entry,) = doc["scene"]["models"]
    assert entry["primitive"] == {
        "kind": "text",
        "text": "Kut 3D",
        "font": "Anton",
        "size": 2.0,
        "depth": 0.5,
        "bevel": 0.01,
        "align": "left",
    }
    assert set(entry) == {"id", "primitive", "root_transform"} and entry["id"] == "text"
    assert (
        len(raw) < 1500 and b"positions" not in raw and b"indices" not in raw
    )  # aucun sommet ni fichier de police
    assert b"\xc3" not in raw  # ASCII pur, comme le reste du format


def test_the_round_trip_gives_the_same_description_and_the_same_mesh(
    tmp_path: Path,
) -> None:
    mat = Material(base_color=(0.9, 0.5, 0.1, 1.0), metallic=0.8, roughness=0.3)
    inst = primitive_instance(
        "text",
        text="Été\nà Paris €",
        font="Saira ExtraCondensed Bold",
        bevel=0.005,
        align="right",
        material=mat,
        position=(1, 2, 3),
        rotation_deg=(10, 20, 30),
    )
    path = tmp_path / "t.kut3d"
    save_scene(Scene([inst], [], _camera()), path)
    back = load_scene(path)
    model = back.instances[0].model
    assert model.primitive == inst.model.primitive
    assert model.materials[0].metallic == pytest.approx(0.8) and np.allclose(
        model.materials[0].base_color, mat.base_color
    )
    assert np.allclose(back.instances[0].transform, inst.transform)
    mesh, original = model.meshes[0], inst.model.meshes[0]
    assert np.array_equal(mesh.positions, original.positions) and np.array_equal(
        mesh.indices, original.indices
    )
    second = tmp_path / "t2.kut3d"
    save_scene(back, second)
    assert second.read_bytes() == path.read_bytes()  # écriture déterministe


def test_text_and_shapes_and_files_share_a_scene_and_unicode_survives(
    tmp_path: Path,
) -> None:
    scene = Scene(
        [
            primitive_instance("cube"),
            primitive_instance("text", text="Ça va ? — « oui »"),
            primitive_instance("text", text="B"),
        ],
        [],
        _camera(),
    )
    path = tmp_path / "m.kut3d"
    save_scene(scene, path)
    doc = json.loads(path.read_bytes())
    assert [e["id"] for e in doc["scene"]["models"]] == ["cube", "text", "text-2"]
    assert doc["version"] == 3
    back = load_scene(path)
    assert [i.model.primitive for i in back.instances] == [
        Cube(),
        Text3D("Ça va ? — « oui »"),
        Text3D("B"),
    ]


def test_a_scene_without_text_keeps_version_2_and_its_exact_bytes(
    tmp_path: Path,
) -> None:
    """Empreinte relevée sur origin/main (avant le texte 3D) pour `six_forms` : elle ne doit jamais bouger."""
    path = tmp_path / "s.kut3d"
    save_scene(six_forms(), path)
    assert json.loads(path.read_bytes())["version"] == 2
    assert len(path.read_bytes()) == 6698
    assert (
        hashlib.sha256(path.read_bytes()).hexdigest()
        == "705fdf8d4caec079f04c6a8b446f572a64c4aeb5a0f308026955c79eb1de4256"
    )


def test_a_scene_without_any_shape_is_still_version_1(tmp_path: Path) -> None:
    save_scene(Scene([], [], _camera()), tmp_path / "e.kut3d")
    assert json.loads((tmp_path / "e.kut3d").read_bytes())["version"] == 1


def test_dependency_files_of_a_text_scene_is_the_file_itself(tmp_path: Path) -> None:
    path = tmp_path / "t.kut3d"
    save_scene(Scene([primitive_instance("text", text="A")], [], _camera()), path)
    assert kut3d.dependency_files(path) == (
        path.resolve(),
    )  # ni police ni modèle à copier


def test_a_text_model_altered_after_creation_is_refused_at_write_time(
    tmp_path: Path,
) -> None:
    import dataclasses

    inst = primitive_instance("text", text="A")
    mesh = inst.model.meshes[0]
    moved = dataclasses.replace(mesh, positions=mesh.positions + 1.0)
    changed = dataclasses.replace(inst.model, meshes=(moved,))
    with pytest.raises(SceneFileError, match="modifiée après sa création"):
        save_scene(Scene([Instance(changed)], [], _camera()), tmp_path / "x.kut3d")
    assert not (tmp_path / "x.kut3d").exists()


# --- versions ---------------------------------------------------------------------------------------------


def test_a_text_in_a_version_2_file_is_refused(tmp_path: Path) -> None:
    path = _write(tmp_path / "a.kut3d", _doc(_text(), version=2))
    with pytest.raises(SceneFileError, match="un texte 3D exige le format 3"):
        load_scene(path)
    ok = _write(
        tmp_path / "b.kut3d",
        _doc({"id": "c", "primitive": {"kind": "cube"}}, version=2),
    )
    assert (
        load_scene(ok).instances == ()
    )  # les autres formes restent lisibles en version 2


def test_a_version_4_file_is_refused_as_too_recent_and_a_reader_of_2_would_refuse_3(
    tmp_path: Path,
) -> None:
    with pytest.raises(SceneVersionError, match="format 4"):
        load_scene(_write(tmp_path / "a.kut3d", _doc(version=4)))
    from kut3d import scene_io_read

    assert scene_io_read.MAX_READ_VERSION == 3


# --- lecture bornée : rien n'est généré avant la fin de la validation -------------------------------------


_HOSTILE: dict[str, dict[str, Any]] = {
    "texte de 5000 caractères": {"text": "x" * 5000},
    "emoji": {"text": "\U0001f600"},
    "texte vide": {"text": ""},
    "accent combinant": {"text": "a\u0301"},
    "texte numérique": {"text": 3},
    "taille nulle": {"text": "A", "size": 0},
    "taille en texte": {"text": "A", "size": "1"},
    "profondeur négative": {"text": "A", "depth": -1},
    "biseau trop grand": {"text": "A", "bevel": 5.0},
    "alignement inconnu": {"text": "A", "align": "middle"},
    "alignement de 5000 caractères": {"text": "A", "align": "x" * 5000},
    "police de 5000 caractères": {"text": "A", "font": "x" * 5000},
    "police numérique": {"text": "A", "font": 4},
    "clé inconnue": {"text": "A", "color": "red"},
}


@pytest.mark.parametrize("name", list(_HOSTILE))
def test_hostile_text_entries_are_refused_without_generating_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    calls = _forbid_generation(monkeypatch)
    entry = _text(_HOSTILE[name])
    with pytest.raises(SceneFileError) as info:
        load_scene(_write(tmp_path / "h.kut3d", _doc(entry)))
    assert not calls and len(str(info.value)) < 400


def test_missing_fields_take_the_documented_defaults(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "d.kut3d",
        {
            **_doc(_text({"font": "Anton"})),
            "scene": {
                "camera": {"position": [0, 0, 5], "target": [0, 0, 0]},
                "models": [_text({"font": "Anton"})],
                "instances": [{"model": "t"}],
            },
        },
    )
    assert load_scene(path).instances[0].model.primitive == Text3D()


def test_a_text_cannot_carry_both_a_path_and_a_primitive(tmp_path: Path) -> None:
    with pytest.raises(SceneFileError):
        load_scene(_write(tmp_path / "h.kut3d", _doc({**_text(), "path": "x.obj"})))


def test_the_file_cap_counts_text_bounds_before_generating(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _forbid_generation(monkeypatch)
    one = Text3D("x" * 40, bevel=0.01)
    count = prim.MAX_SCENE_PRIMITIVE_TRIANGLES // one.triangle_count + 1
    models = [
        _text({"text": "x" * 40, "size": 1.0, "depth": 1.0, "bevel": 0.01}, mid=f"t{i}")
        for i in range(count)
    ]
    with pytest.raises(SceneFileError, match="trop de triangles"):
        load_scene(_write(tmp_path / "big.kut3d", _doc(*models)))
    assert not calls


def test_a_missing_font_and_a_glyphless_character_are_scene_errors(
    tmp_path: Path,
) -> None:
    with pytest.raises(SceneFileError, match=r"models\[0\].*introuvable"):
        load_scene(
            _write(
                tmp_path / "a.kut3d", _doc(_text({"text": "A", "font": "Nope Sans"}))
            )
        )
    with pytest.raises(SceneFileError, match=r"pas de glyphe pour U\+03B1"):
        load_scene(_write(tmp_path / "b.kut3d", _doc(_text({"text": "α"}))))


def test_save_scene_refuses_what_load_scene_would_refuse(tmp_path: Path) -> None:
    mat = Material(base_color=(1, 1, 1, 1))
    ok = primitive_instance("text", text="A", material=mat)
    save_scene(
        Scene([ok], [AmbientLight((1, 1, 1), 1.0)], _camera()), tmp_path / "ok.kut3d"
    )
    many = Scene(
        [
            primitive_instance(
                "text", text="x" * 40, bevel=0.01, size=1.0, depth=1.0, name=f"n{i}"
            )
            for i in range(1)
        ],
        [],
        _camera(),
    )
    save_scene(
        many, tmp_path / "one.kut3d"
    )  # un seul texte plein : sous le plafond de fichier
    assert (tmp_path / "one.kut3d").is_file()
