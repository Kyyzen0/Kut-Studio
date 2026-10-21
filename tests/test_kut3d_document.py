"""Document de scène éditable (ADR-0010) : commandes, historique annulable, identités stables, refus, plafonds."""

from __future__ import annotations

import dataclasses

import numpy as np
import pytest

import kut3d
from kut3d import (
    AmbientLight,
    Camera,
    Cube,
    DirectionalLight,
    DocumentError,
    Material,
    PointLight,
    SceneDocument,
    Sphere,
    pose_matrix,
    primitive_instance,
)
from kut3d import document_commands as cmd
from kut3d.document import MAX_UNDO
from kut3d.scene import MAX_INSTANCES, MAX_LIGHTS


def sphere(doc: SceneDocument, **kw: object) -> str:
    return doc.execute(cmd.AddPrimitive(Sphere(0.5, 16, 8), **kw))[0]  # type: ignore[arg-type]


def states_equal(doc: SceneDocument, other: kut3d.Scene) -> bool:
    mine = doc.scene()
    return (
        [(i.name, i.transform.tolist(), id(i.model)) for i in mine.instances]
        == [(i.name, i.transform.tolist(), id(i.model)) for i in other.instances]
        and len(mine.lights) == len(other.lights)
    )


# --- création, identités, noms -----------------------------------------------------------------------


def test_a_new_document_has_a_camera_and_lights_and_no_object() -> None:
    doc = SceneDocument()
    scene = doc.scene()
    assert scene.instances == () and len(scene.lights) == 2 and not doc.is_dirty and not doc.can_undo


def test_add_primitive_creates_a_stable_id_and_a_default_name_without_suffix() -> None:
    doc = SceneDocument()
    first = sphere(doc)
    second = sphere(doc)
    assert (first, second) == ("o3", "o4")  # l1 et l2 sont les deux lumières du document neuf : un seul compteur
    names = [o.name for o in doc.state.objects]
    assert names == ["sphere", "sphere"]  # jamais « sphere 2 » : l'identité est l'identifiant, pas le nom


def test_ids_of_new_documents_follow_the_file_order() -> None:
    scene = kut3d.Scene(
        instances=[primitive_instance("cube"), primitive_instance("sphere")],
        lights=[AmbientLight()],
        camera=Camera(position=(0, 0, 5), target=(0, 0, 0)),
    )
    doc = SceneDocument(scene)
    assert [o.id for o in doc.state.objects] == ["o1", "o2"]
    assert [e.id for e in doc.state.lights] == ["l3"]


def test_ids_are_never_reused_even_after_undo_then_a_new_edit() -> None:
    doc = SceneDocument()
    first = sphere(doc)
    doc.undo()
    second = sphere(doc)
    assert first != second


def test_redo_of_a_creation_gives_back_the_same_id() -> None:
    doc = SceneDocument()
    created = sphere(doc)
    doc.undo()
    assert doc.state.objects == ()
    doc.redo()
    assert [o.id for o in doc.state.objects] == [created]


def test_a_user_name_is_kept_as_written_and_the_same_rules_apply_to_defaults() -> None:
    doc = SceneDocument()
    oid = sphere(doc, name="Boule")
    assert doc.state.find_object(oid).name == "Boule"
    doc.execute(cmd.Rename(oid, "Boule"))  # un même nom deux fois : accepté, pas corrigé
    with pytest.raises(DocumentError):
        doc.execute(cmd.Rename(oid, "x" * 201))
    with pytest.raises(DocumentError):
        doc.execute(cmd.Rename(oid, 12))  # type: ignore[arg-type]
    with pytest.raises(DocumentError):
        sphere(doc, name="y" * 201)
    assert doc.state.find_object(oid).name == "Boule"


# --- annuler / rétablir --------------------------------------------------------------------------------


def test_undo_and_redo_restore_exact_states_and_share_models() -> None:
    doc = SceneDocument()
    oid = sphere(doc)
    model = doc.state.find_object(oid).model
    before = doc.scene()
    doc.execute(cmd.SetTransform(oid, pose_matrix(position=(1, 2, 3))))
    assert doc.undo()
    assert states_equal(doc, before)
    assert doc.state.find_object(oid).model is model  # un maillage n'est jamais copié par l'historique
    assert doc.redo()
    assert doc.state.find_object(oid).transform[:3, 3].tolist() == [1, 2, 3]
    assert doc.state.find_object(oid).model is model
    assert not doc.redo() and doc.undo() and doc.undo() and not doc.undo()


def test_labels_and_history_panel() -> None:
    doc = SceneDocument()
    oid = sphere(doc)
    doc.execute(cmd.Rename(oid, "A"))
    assert doc.undo_label == "Renommer" and doc.redo_label is None
    doc.undo()
    assert doc.redo_label == "Renommer" and doc.undo_label == "Ajouter sphere"
    assert doc.history() == ("État initial", "Ajouter sphere", "Renommer")


def test_a_new_edit_after_undo_drops_the_redo_branch() -> None:
    doc = SceneDocument()
    oid = sphere(doc)
    doc.undo()
    doc.execute(cmd.AddLight(PointLight((0, 1, 0))))
    assert not doc.can_redo
    with pytest.raises(DocumentError):
        doc.state.find_object(oid)


def test_a_refused_command_changes_nothing() -> None:
    doc = SceneDocument()
    oid = sphere(doc)
    revision, history = doc.revision, doc.history()
    bad = (
        cmd.SetTransform(oid, np.full((4, 4), np.nan)),
        cmd.SetTransform(oid, np.full((4, 4), np.inf)),
        cmd.SetTransform(oid, np.zeros((4, 4))),
        cmd.SetTransform("o999", np.eye(4)),
        cmd.RemoveObject("o999"),
        cmd.SetBackground((0, 0, 0, float("nan"))),
        cmd.SetBackground((2, 0, 0, 1)),
        cmd.SetAnimation(oid, clip="inconnu"),
        cmd.SetAnimation(oid, loop=1),  # type: ignore[arg-type]
        cmd.SetAnimation(oid, time_offset=float("inf")),
        cmd.RemoveLight("l999"),
        cmd.AddPrimitive(Sphere(0.5), transform=np.full((4, 4), np.nan)),
    )
    for command in bad:
        with pytest.raises(DocumentError):
            doc.execute(command)
    assert (doc.revision, doc.history()) == (revision, history)


def test_values_that_the_model_constructors_refuse_never_reach_the_document() -> None:
    for build in (
        lambda: Sphere(float("nan")),
        lambda: Sphere(radius=float("inf")),
        lambda: Cube(width=-1.0),
        lambda: Camera(position=(0, 0, float("nan")), target=(0, 0, 0)),
        lambda: PointLight((float("inf"), 0, 0)),
        lambda: pose_matrix(position=(float("nan"), 0, 0)),
        lambda: pose_matrix(scale=0.0),
        lambda: pose_matrix(scale=True),
        lambda: pose_matrix(rotation_deg=(0, float("inf"), 0)),
    ):
        with pytest.raises(ValueError):
            build()


def test_remove_then_undo_restores_the_same_id_at_the_same_place() -> None:
    doc = SceneDocument()
    a, b, c = sphere(doc), sphere(doc), sphere(doc)
    doc.execute(cmd.RemoveObject(b))
    assert [o.id for o in doc.state.objects] == [a, c]
    doc.undo()
    assert [o.id for o in doc.state.objects] == [a, b, c]


# --- gestes (fusion) ---------------------------------------------------------------------------------


def test_a_drag_is_one_history_entry_until_the_gesture_ends() -> None:
    doc = SceneDocument()
    oid = sphere(doc)
    for step in range(1, 11):
        doc.execute(cmd.SetTransform(oid, pose_matrix(position=(step, 0, 0)), gesture=True))
    assert doc.history() == ("État initial", "Ajouter sphere", "Déplacer")
    doc.end_gesture()
    doc.execute(cmd.SetTransform(oid, pose_matrix(position=(20, 0, 0)), gesture=True))
    assert len(doc.history()) == 4
    doc.undo()
    assert doc.state.find_object(oid).transform[0, 3] == 10.0


def test_gestures_on_two_objects_or_after_undo_do_not_merge() -> None:
    doc = SceneDocument()
    a, b = sphere(doc), sphere(doc)
    doc.execute(cmd.SetTransform(a, pose_matrix(position=(1, 0, 0)), gesture=True))
    doc.execute(cmd.SetTransform(b, pose_matrix(position=(1, 0, 0)), gesture=True))
    assert len(doc.history()) == 5
    doc.undo()
    doc.redo()
    doc.execute(cmd.SetTransform(b, pose_matrix(position=(2, 0, 0)), gesture=True))
    assert len(doc.history()) == 6  # undo / redo ont fermé le geste
    doc.execute(cmd.SetTransform(b, pose_matrix(position=(3, 0, 0))))  # sans geste : jamais fusionnée
    doc.execute(cmd.SetTransform(b, pose_matrix(position=(4, 0, 0))))
    assert len(doc.history()) == 8


def test_a_gesture_is_never_merged_into_the_saved_state(tmp_path) -> None:  # type: ignore[no-untyped-def]
    doc = SceneDocument()
    oid = sphere(doc)
    doc.execute(cmd.SetTransform(oid, pose_matrix(position=(1, 0, 0)), gesture=True))
    doc.save(tmp_path / "s.kut3d")
    assert not doc.is_dirty
    doc.execute(cmd.SetTransform(oid, pose_matrix(position=(2, 0, 0)), gesture=True))
    assert doc.is_dirty and len(doc.history()) == 4  # le geste a ouvert une entrée à lui
    doc.undo()
    assert not doc.is_dirty


def test_a_batch_is_atomic_and_one_entry() -> None:
    doc = SceneDocument()
    a, b = sphere(doc), sphere(doc)
    moves = [cmd.SetTransform(a, pose_matrix(position=(1, 0, 0))), cmd.SetTransform(b, pose_matrix(position=(2, 0, 0)))]
    doc.execute(cmd.Batch("Déplacer la sélection", moves))
    assert doc.undo_label == "Déplacer la sélection"
    doc.undo()
    assert doc.state.find_object(a).transform[0, 3] == 0.0 == doc.state.find_object(b).transform[0, 3]
    revision = doc.revision
    with pytest.raises(DocumentError):
        doc.execute(cmd.Batch("Mauvais", [moves[0], cmd.SetTransform("o999", np.eye(4))]))
    assert doc.revision == revision and doc.state.find_object(a).transform[0, 3] == 0.0


# --- modifié / enregistré / plafond d'historique ------------------------------------------------------------


def test_dirty_follows_the_saved_state(tmp_path) -> None:  # type: ignore[no-untyped-def]
    doc = SceneDocument()
    sphere(doc)
    assert doc.is_dirty
    doc.save(tmp_path / "s.kut3d")
    assert not doc.is_dirty
    sphere(doc)
    assert doc.is_dirty
    doc.undo()
    assert not doc.is_dirty
    doc.undo()
    assert doc.is_dirty
    doc.redo()
    assert not doc.is_dirty


def test_editing_after_undoing_past_the_saved_state_is_dirty(tmp_path) -> None:  # type: ignore[no-untyped-def]
    doc = SceneDocument()
    sphere(doc)
    doc.save(tmp_path / "s.kut3d")
    doc.undo()
    sphere(doc)  # l'état enregistré vivait dans la branche jetée
    assert doc.is_dirty
    doc.undo()
    assert doc.is_dirty


def test_the_history_is_capped_and_the_saved_mark_follows(tmp_path) -> None:  # type: ignore[no-untyped-def]
    doc = SceneDocument()
    oid = sphere(doc)
    doc.save(tmp_path / "s.kut3d")
    for i in range(MAX_UNDO + 20):
        doc.execute(cmd.Rename(oid, f"n{i}"))
    undone = 0
    while doc.undo():
        undone += 1
    assert undone == MAX_UNDO and len(doc.history()) == MAX_UNDO + 1
    assert doc.is_dirty  # l'état enregistré est sorti de la pile : plus jamais « propre »


# --- plafonds -------------------------------------------------------------------------------------------


def test_the_instance_cap_is_enforced_at_every_edit() -> None:
    shared = primitive_instance("cube")
    scene = kut3d.Scene(
        instances=[shared] * MAX_INSTANCES, lights=[], camera=Camera(position=(0, 0, 5), target=(0, 0, 0))
    )
    doc = SceneDocument(scene)
    with pytest.raises(DocumentError, match="trop d'objets"):
        doc.execute(cmd.DuplicateObject("o1"))
    with pytest.raises(DocumentError, match="trop d'objets"):
        doc.execute(cmd.AddPrimitive(Cube()))
    assert len(doc.state.objects) == MAX_INSTANCES and not doc.can_undo
    doc.execute(cmd.RemoveObject("o1"))
    doc.execute(cmd.DuplicateObject("o2"))


def test_the_light_cap_is_enforced() -> None:
    doc = SceneDocument()
    for _ in range(MAX_LIGHTS - 2):
        doc.execute(cmd.AddLight(PointLight((0, 1, 0))))
    with pytest.raises(DocumentError, match="trop de lumières"):
        doc.execute(cmd.AddLight(PointLight((0, 1, 0))))


def test_the_distinct_model_cap_counts_models_not_instances() -> None:
    doc = SceneDocument()
    for i in range(256):
        doc.execute(cmd.AddPrimitive(Cube(1.0 + i * 1e-3, 1.0, 1.0)))
    with pytest.raises(DocumentError, match="modèles distincts"):
        doc.execute(cmd.AddPrimitive(Cube(9.0)))
    doc.execute(cmd.DuplicateObject(doc.state.objects[0].id))  # une copie partage le modèle : pas un modèle de plus


def test_the_triangle_budget_is_checked_before_generating_anything(monkeypatch: pytest.MonkeyPatch) -> None:
    doc = SceneDocument()
    small = doc.execute(cmd.AddPrimitive(Cube()))[0]
    big = Sphere(0.5, 256, 128)  # 65 280 triangles
    for _ in range(7):  # 456 960 + 12 : sous les 500 000 du fichier
        doc.execute(cmd.AddPrimitive(big))
    calls: list[object] = []
    real = cmd.primitive_model
    monkeypatch.setattr(cmd, "primitive_model", lambda *a, **k: calls.append(a) or real(*a, **k))
    with pytest.raises(DocumentError, match="trop de triangles"):
        doc.execute(cmd.AddPrimitive(big))
    with pytest.raises(DocumentError, match="trop de triangles"):
        doc.execute(cmd.SetPrimitive(small, kut3d.Plane(1.0, 1.0, 181, 181)))  # 65 522 : tient seul, pas dans le fichier
    assert calls == []  # aucun maillage généré pour un refus
    doc.execute(cmd.SetPrimitive(small, kut3d.Plane(1.0, 1.0, 20, 20)))  # 800 : passe
    assert len(calls) == 1


def test_a_shared_model_counts_once_in_the_budget() -> None:
    doc = SceneDocument()
    oid = doc.execute(cmd.AddPrimitive(Sphere(0.5, 256, 128)))[0]
    doc.execute(cmd.Batch("Copies", [cmd.DuplicateObject(oid)] * 20))  # 21 x 65 280 > 500 000 si on comptait chaque copie
    assert len(doc.state.objects) == 21


# --- éditer une forme -------------------------------------------------------------------------------


def test_set_primitive_keeps_material_pose_and_root_transform_and_splits_a_shared_model() -> None:
    doc = SceneDocument()
    red = Material(base_color=(0.9, 0.1, 0.1, 1.0), roughness=0.3)
    a = doc.execute(cmd.AddPrimitive(Sphere(0.5, 16, 8), material=red, transform=pose_matrix(position=(1, 0, 0))))[0]
    b = doc.execute(cmd.DuplicateObject(a))[0]
    shared = doc.state.find_object(a).model
    assert doc.state.find_object(b).model is shared
    doc.execute(cmd.SetPrimitive(a, Sphere(0.9, 24, 12)))
    edited = doc.state.find_object(a)
    assert edited.model.primitive == Sphere(0.9, 24, 12)
    assert edited.model.materials[0] == red
    assert edited.transform[0, 3] == 1.0
    assert doc.state.find_object(b).model is shared  # l'autre objet ne bouge pas (copie à l'écriture)
    assert shared.primitive == Sphere(0.5, 16, 8)
    doc.undo()
    assert doc.state.find_object(a).model is shared


def test_set_material_and_a_shape_edited_by_hand_is_refused_not_rewritten() -> None:
    doc = SceneDocument()
    oid = sphere(doc)
    doc.execute(cmd.SetMaterial(oid, Material(base_color=(0, 1, 0, 1))))
    assert doc.state.find_object(oid).model.materials[0].base_color.tolist() == [0, 1, 0, 1]
    obj = doc.state.find_object(oid)
    hand = dataclasses.replace(obj.model, animations=obj.model.animations)
    mesh = dataclasses.replace(hand.meshes[0], positions=hand.meshes[0].positions * 2.0)
    hacked = dataclasses.replace(hand, meshes=(mesh,))
    scene = kut3d.Scene(
        instances=[kut3d.Instance(hacked)], lights=[], camera=Camera(position=(0, 0, 5), target=(0, 0, 0))
    )
    other = SceneDocument(scene)
    with pytest.raises(DocumentError, match="modifiée après sa création"):
        other.execute(cmd.SetPrimitive("o1", Sphere(0.4)))
    with pytest.raises(DocumentError, match="modifiée après sa création"):
        other.execute(cmd.SetMaterial("o1", Material()))
    assert other.state.objects[0].model is hacked


def test_textured_materials_are_refused_at_edit_time_not_at_save_time() -> None:
    from tests.kut3d_scenes import checker_rgba, texture_ref

    tex = Material(base_color_texture=texture_ref(checker_rgba(16, 4)))
    doc = SceneDocument()
    oid = sphere(doc)
    with pytest.raises(DocumentError, match="textures"):
        doc.execute(cmd.SetMaterial(oid, tex))
    with pytest.raises(DocumentError, match="textures"):
        doc.execute(cmd.AddPrimitive(Cube(), material=tex))


def test_camera_background_and_lights_are_editable_and_undoable() -> None:
    doc = SceneDocument()
    new_camera = Camera(position=(3, 3, 3), target=(0, 0, 0), fov_y_deg=60.0)
    doc.execute(cmd.SetCamera(new_camera))
    doc.execute(cmd.SetBackground((0.1, 0.2, 0.3, 1.0)))
    (light_id,) = doc.execute(cmd.AddLight(PointLight((0, 2, 0), intensity=3.0)))
    doc.execute(cmd.SetLight(light_id, DirectionalLight((0, -1, 0))))
    assert doc.scene().camera is new_camera and doc.scene().background.tolist() == [0.1, 0.2, 0.3, 1.0]
    assert isinstance(doc.scene().lights[-1], DirectionalLight)
    doc.execute(cmd.RemoveLight(light_id))
    assert len(doc.scene().lights) == 2
    doc.undo()
    assert len(doc.scene().lights) == 3
    doc.execute(cmd.SetBackground(None))
    assert doc.scene().background is None


def test_pose_matrix_matches_primitive_instance() -> None:
    expected = primitive_instance("cube", position=(1, 2, 3), rotation_deg=(10, 20, 30), scale=(1, 2, 3)).transform
    assert np.array_equal(pose_matrix((1, 2, 3), (10, 20, 30), (1, 2, 3)), expected)
    assert np.array_equal(pose_matrix(scale=2.0)[:3, :3], 2.0 * np.eye(3))


def test_the_scene_is_cached_by_revision() -> None:
    doc = SceneDocument()
    first = doc.scene()
    assert doc.scene() is first
    sphere(doc)
    assert doc.scene() is not first and len(doc.scene().instances) == 1


def test_import_kut3d_does_not_load_qt() -> None:
    import subprocess
    import sys

    code = "import sys, kut3d; kut3d.SceneDocument(); assert not any(m.startswith('PySide6') for m in sys.modules)"
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr
