"""Document de scène éditable et fichier `.kut3d` : aller-retour octet-stable, modèles importés, conflit, modèles partagés."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import kut3d
from kut3d import (
    AmbientLight,
    Camera,
    Cube,
    DirectionalLight,
    DocumentConflict,
    DocumentError,
    Material,
    PointLight,
    SceneDocument,
    SceneFileError,
    Sphere,
    load_model,
    pose_matrix,
    primitive_instance,
    save_scene,
)
from kut3d import document_commands as cmd
from tests.kut3d_samples import write_cube_obj, write_gltf


def authored_scene() -> kut3d.Scene:
    return kut3d.Scene(
        instances=[
            primitive_instance(
                "sphere",
                radius=0.7,
                material=Material(base_color=(0.8, 0.2, 0.2, 1.0)),
                position=(1, 0, 0),
                name="rouge",
            ),
            primitive_instance(
                "cube", position=(-1, 0.5, 0), rotation_deg=(0, 30, 0), name="cube"
            ),
            primitive_instance("plane", width=8.0, depth=8.0, scale=1.0, name="sol"),
        ],
        lights=[
            AmbientLight((1, 1, 1), 0.2),
            DirectionalLight((0.3, -1, -0.2), intensity=2.0),
            PointLight((2, 3, 4), intensity=5.0, range=20.0),
        ],
        camera=Camera(position=(0, 2, 7), target=(0, 0.5, 0), fov_y_deg=45.0),
        background=(0.05, 0.05, 0.08, 1.0),
    )


@pytest.fixture
def saved(tmp_path: Path) -> Path:
    path = tmp_path / "scene.kut3d"
    save_scene(authored_scene(), path)
    return path


def test_load_then_save_gives_the_same_bytes(saved: Path, tmp_path: Path) -> None:
    doc = SceneDocument.load(saved)
    doc.save(tmp_path / "copie.kut3d")
    assert (tmp_path / "copie.kut3d").read_bytes() == saved.read_bytes()
    assert not doc.is_dirty


def test_edit_then_undo_everything_gives_the_original_bytes(
    saved: Path, tmp_path: Path
) -> None:
    doc = SceneDocument.load(saved)
    first = doc.state.objects[0].id
    doc.execute(cmd.SetTransform(first, pose_matrix(position=(5, 5, 5))))
    doc.execute(cmd.SetPrimitive(first, Sphere(1.2, 24, 12)))
    doc.execute(cmd.AddPrimitive(Cube(2, 2, 2)))
    doc.execute(cmd.SetCamera(Camera(position=(9, 9, 9), target=(0, 0, 0))))
    doc.save(tmp_path / "edite.kut3d")
    assert (tmp_path / "edite.kut3d").read_bytes() != saved.read_bytes()
    while doc.undo():
        pass
    doc.save(tmp_path / "annule.kut3d")
    assert (tmp_path / "annule.kut3d").read_bytes() == saved.read_bytes()


def test_a_saved_edit_loads_back_to_the_edited_scene(saved: Path) -> None:
    doc = SceneDocument.load(saved)
    first = doc.state.objects[0].id
    doc.execute(cmd.Rename(first, "Boule bleue"))
    doc.execute(
        cmd.SetMaterial(first, Material(base_color=(0.1, 0.2, 0.9, 1.0), metallic=0.5))
    )
    doc.execute(cmd.SetBackground(None))
    doc.save()
    again = SceneDocument.load(saved)
    inst = again.scene().instances[0]
    assert inst.name == "Boule bleue" and inst.model.materials[0].metallic == 0.5
    assert again.scene().background is None
    assert [o.name for o in again.state.objects] == ["Boule bleue", "cube", "sol"]


def test_the_format_version_follows_the_content(tmp_path: Path) -> None:
    doc = SceneDocument()
    doc.save(tmp_path / "vide.kut3d")
    assert (
        json.loads((tmp_path / "vide.kut3d").read_text())["version"] == 1
    )  # aucune forme : format 1, comme avant
    doc.execute(cmd.AddPrimitive(Cube()))
    doc.save(tmp_path / "forme.kut3d")
    assert json.loads((tmp_path / "forme.kut3d").read_text())["version"] == 2


def test_two_copies_share_one_model_entry_until_one_is_edited(tmp_path: Path) -> None:
    doc = SceneDocument()
    oid = doc.execute(cmd.AddPrimitive(Sphere(0.5, 16, 8)))[0]
    copy = doc.execute(cmd.DuplicateObject(oid))[0]
    doc.save(tmp_path / "a.kut3d")
    assert len(json.loads((tmp_path / "a.kut3d").read_text())["scene"]["models"]) == 1
    doc.execute(cmd.SetPrimitive(copy, Sphere(0.9, 16, 8)))
    doc.save(tmp_path / "b.kut3d")
    assert len(json.loads((tmp_path / "b.kut3d").read_text())["scene"]["models"]) == 2
    again = SceneDocument.load(tmp_path / "b.kut3d")
    assert [o.model.primitive for o in again.state.objects] == [
        Sphere(0.5, 16, 8),
        Sphere(0.9, 16, 8),
    ]


def test_imported_models_move_but_do_not_edit_by_parameters(tmp_path: Path) -> None:
    obj = write_cube_obj(tmp_path)
    doc = SceneDocument()
    oid = doc.execute(
        cmd.AddModel(load_model(obj), transform=pose_matrix(position=(0, 1, 0)))
    )[0]
    assert doc.state.find_object(oid).name == "cube"
    doc.execute(
        cmd.SetTransform(
            oid, pose_matrix(position=(0, 2, 0), rotation_deg=(0, 45, 0), scale=2.0)
        )
    )
    for refused in (cmd.SetPrimitive(oid, Sphere()), cmd.SetMaterial(oid, Material())):
        with pytest.raises(DocumentError, match="modèle importé"):
            doc.execute(refused)
    doc.save(tmp_path / "scene.kut3d")
    again = SceneDocument.load(tmp_path / "scene.kut3d")
    assert again.state.objects[0].transform[1, 3] == 2.0


def test_a_model_without_a_source_file_is_refused_when_added() -> None:
    from tests.kut3d_scenes import cube_model

    doc = SceneDocument()
    with pytest.raises(DocumentError, match="fichier source"):
        doc.execute(cmd.AddModel(cube_model()))
    assert not doc.is_dirty and doc.history() == ("État initial",)


def test_a_model_outside_the_folder_is_refused_at_save_and_the_document_is_untouched(
    tmp_path: Path,
) -> None:
    elsewhere = tmp_path / "ailleurs"
    elsewhere.mkdir()
    folder = tmp_path / "scene"
    folder.mkdir()
    doc = SceneDocument()
    doc.execute(cmd.AddModel(load_model(write_cube_obj(elsewhere))))
    with pytest.raises(SceneFileError, match="hors du dossier"):
        doc.save(folder / "s.kut3d")
    assert doc.is_dirty and doc.path is None and not (folder / "s.kut3d").exists()
    doc.save(folder / "s.kut3d", allow_outside=True)
    assert not doc.is_dirty


def test_an_animated_model_keeps_its_clip_across_edits_and_the_file(
    tmp_path: Path,
) -> None:
    doc = SceneDocument()
    gltf = write_gltf(tmp_path)
    model = load_model(gltf)
    names = [c.name for c in model.animations]
    assert names, "le modèle d'essai doit avoir des animations"
    oid = doc.execute(cmd.AddModel(model))[0]
    doc.execute(cmd.SetAnimation(oid, clip=names[0], time_offset=0.25, loop=True))
    doc.execute(
        cmd.SetTransform(oid, pose_matrix(position=(1, 0, 0)))
    )  # déplacer ne touche pas l'animation
    inst = doc.state.find_object(oid).instance
    assert (inst.clip, inst.time_offset, inst.loop) == (names[0], 0.25, True)
    with pytest.raises(DocumentError, match="introuvable"):
        doc.execute(cmd.SetAnimation(oid, clip="n'existe pas"))
    doc.save(tmp_path / "s.kut3d")
    inst = SceneDocument.load(tmp_path / "s.kut3d").state.objects[0].instance
    assert (inst.clip, inst.time_offset, inst.loop) == (names[0], 0.25, True)
    doc.execute(cmd.SetAnimation(oid, clip=None))
    assert doc.state.find_object(oid).instance.clip is None
    doc.undo()
    assert doc.state.find_object(oid).instance.clip == names[0]


def test_a_changed_file_is_not_overwritten_silently(saved: Path) -> None:
    doc = SceneDocument.load(saved)
    doc.execute(cmd.Rename(doc.state.objects[0].id, "A"))
    other = SceneDocument.load(
        saved
    )  # un autre clip (ou un autre programme) enregistre le même fichier
    other.execute(cmd.Rename(other.state.objects[1].id, "B"))
    other.save()
    before = saved.read_bytes()
    with pytest.raises(DocumentConflict):
        doc.save()
    assert saved.read_bytes() == before and doc.is_dirty
    doc.save(overwrite=True)
    assert not doc.is_dirty
    doc.execute(cmd.Rename(doc.state.objects[0].id, "C"))
    doc.save()  # il vient d'écrire le fichier : plus de conflit


def test_a_deleted_or_touched_file_is_a_conflict_only_when_its_content_changed(
    saved: Path,
) -> None:
    doc = SceneDocument.load(saved)
    stat = saved.stat()
    os.utime(saved, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
    with pytest.raises(DocumentConflict):  # l'horodatage a changé : prudence
        doc.save()
    doc = SceneDocument.load(saved)
    saved.unlink()
    with pytest.raises(DocumentConflict):
        doc.save()
    doc.save(overwrite=True)
    assert saved.exists()


def test_save_as_does_not_check_the_original_and_moves_the_document(
    saved: Path, tmp_path: Path
) -> None:
    doc = SceneDocument.load(saved)
    saved.write_bytes(saved.read_bytes() + b" ")
    doc.save(tmp_path / "autre.kut3d")
    assert doc.path == tmp_path / "autre.kut3d"
    doc.save()


def test_a_save_that_fails_does_not_mark_the_document_clean(tmp_path: Path) -> None:
    doc = SceneDocument()
    doc.execute(cmd.AddPrimitive(Cube()))
    with pytest.raises(SceneFileError):
        doc.save(tmp_path / "absent" / "s.kut3d")
    assert doc.is_dirty and doc.path is None


def test_a_hostile_file_is_refused_at_load(tmp_path: Path) -> None:
    path = tmp_path / "x.kut3d"
    path.write_text('{"format": "kut3d", "version": 99, "scene": {}}')
    with pytest.raises(SceneFileError):
        SceneDocument.load(path)
    path.write_text("{" * 10)
    with pytest.raises(SceneFileError):
        SceneDocument.load(path)


def test_the_gesture_cost_is_a_ratio_not_a_time() -> None:
    """Un geste de 200 déplacements sur une scène de 456 960 triangles ne copie aucun maillage : tous les états partagent les
    mêmes `Model` (propriété vérifiée par identité, pas par un chronomètre)."""
    doc = SceneDocument()
    oid = doc.execute(cmd.AddPrimitive(Sphere(0.5, 256, 128)))[0]
    model = doc.state.find_object(oid).model
    for i in range(200):
        doc.execute(
            cmd.SetTransform(oid, pose_matrix(position=(i * 0.01, 0, 0)), gesture=True)
        )
    assert doc.state.find_object(oid).model is model
    assert len(doc.history()) == 3
    while doc.undo():
        assert doc.state.objects == () or doc.state.find_object(oid).model is model


def test_dataclass_replace_of_the_state_keeps_ids(saved: Path) -> None:
    doc = SceneDocument.load(saved)
    ids = [o.id for o in doc.state.objects]
    doc.execute(cmd.RemoveObject(ids[1]))
    doc.execute(cmd.AddPrimitive(Cube()))
    new_ids = [o.id for o in doc.state.objects]
    assert new_ids[:2] == [ids[0], ids[2]] and new_ids[2] not in ids


def test_a_hand_written_file_is_reported_as_rewritten_by_save(tmp_path: Path) -> None:
    from tests.kut3d_scene_samples import minimal_document, write_doc

    write_cube_obj(tmp_path, name="cube")
    doc_json = minimal_document()
    old = doc_json["scene"]["models"][0]["id"]
    doc_json["scene"]["models"][0]["id"] = "ma-boite"
    for inst in doc_json["scene"]["instances"]:
        if inst["model"] == old:
            inst["model"] = "ma-boite"
    hand = write_doc(tmp_path / "main.kut3d", doc_json)
    doc = SceneDocument.load(hand)
    assert (
        not doc.canonical_on_disk
    )  # JSON compact, identifiant « ma-boite » : l'enregistrer le réécrit
    doc.save()
    assert doc.canonical_on_disk and SceneDocument.load(hand).canonical_on_disk
    assert b'"ma-boite"' not in hand.read_bytes()


def test_a_file_written_by_save_is_canonical(saved: Path) -> None:
    assert SceneDocument.load(saved).canonical_on_disk
