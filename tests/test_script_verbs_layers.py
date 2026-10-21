"""Format déclaratif : calques graphiques (lot 4 de l'extension 0.8.0) : hiérarchie, groupes, ordre, visibilité, verrou, doublons.

Chaque verbe appelle la fonction que le panneau Calques appelle : le test de parité fait le même appel directement sur une copie et
compare les projets. Les refus (verrous du calque, de la piste, du groupe ; tiers verrouillés ; boucles ; profondeur ; index hors pile ;
apparence qui sauterait) sont ceux d'un script : le projet reste strictement inchangé.
"""

from __future__ import annotations

import copy
import math

import pytest

from core import mograph_layers as layers
from core.graphics import GraphicOverlay
from core.mograph_layers import add_layer
from core.project_model import Clip, MediaAsset, Project, Sequence, Track
from core.scripting import ScriptInvalid, Session, apply_script, created_ids, parse_script
from core.scripting import layer_rules
from core.scripting.schema import json_schema
from core.scripting.verbs import VERBS
from core.timeline_operations import find_clip
from core.visual_effects import ClipTransform, TransformKeyframe
from ui import i18n

LAYER_VERBS = (
    "delete_layers", "duplicate_layers", "rename_layer", "set_layer_visible", "set_layer_locked", "set_layer_motion_blur",
    "reorder_layer", "move_layer", "group_layers", "ungroup_layers", "move_into_group", "set_parent", "clear_parent",
)


@pytest.fixture(autouse=True)
def _french(monkeypatch):
    monkeypatch.setenv("KUT_STUDIO_LANG", "fr")
    i18n.reset_for_tests()
    yield
    i18n.reset_for_tests()


def _project() -> Project:
    """V1 (vidéo) ; G1 (graphiques libre) : a (texte), b (forme), c (rectangle), d (contrôleur) ; G2 (graphiques verrouillée) : z."""
    project = Project(
        "Démo", width=320, height=180, fps=30.0,
        media_assets=[MediaAsset("m", "/x/a.mp4", "a", 10.0, 320, 180, 30.0, "video")],
        tracks=[
            Track("V1", "V1", "video", clips=[Clip("v", "m", "V1", 0.0, 0.0, 6.0)]),
            Track("G1", "G1", "graphics"),
            Track("G2", "G2", "graphics", locked=True),
        ],
    )
    for clip_id, kind in (("a", "text"), ("b", "shape"), ("c", "rectangle"), ("d", "null")):
        add_layer(project, kind, at=0.0, duration=4.0, clip_id=clip_id)
    add_layer(project, "shape", at=0.0, duration=2.0, track=project.tracks[2], clip_id="z")
    return project


def _op(verb: str, /, **fields) -> dict:
    return {"op": verb, **fields}


def _apply(project: Project, *ops: dict):
    session = Session(project)
    return session, apply_script(session, parse_script({"kut_script": 1, "ops": list(ops)}))


def _snapshot(project: Project):
    return (copy.deepcopy(project.tracks), copy.deepcopy(project.media_assets))


def _same(first: Project, second: Project) -> bool:
    return _snapshot(first) == _snapshot(second)


def _parity(op: dict, direct, factory=_project) -> Project:
    """Applique ``op`` à un projet et ``direct`` (l'appel du modèle) à un autre : ils doivent être identiques ; rend le projet du script."""
    scripted = factory()
    session, report = _apply(scripted, op)
    assert report.ok, report.error
    reference = factory()
    direct(reference)
    assert _same(session.project, reference)
    return session.project


def _refused(project: Project, *ops: dict, match: str | None = None):
    """Le script est refusé à l'application, et le projet est strictement inchangé."""
    before = _snapshot(project)
    session, report = _apply(project, *ops)
    assert not report.ok, "le script aurait dû être refusé"
    assert _snapshot(session.project) == before
    if match is not None:
        assert match in report.error, report.error
    return report


def _invalid(*ops: dict) -> None:
    with pytest.raises(ScriptInvalid):
        parse_script({"kut_script": 1, "ops": list(ops)})


def _layer(project: Project, clip_id: str) -> Clip:
    return find_clip(project, clip_id)


def _lock(project: Project, clip_id: str) -> None:
    layers.set_layer_locked(project, clip_id, True)


def _stack(project: Project) -> list[str]:
    return [clip.id for _i, _t, clip in layers.layer_clips(project)]


# --- Table, schéma -----------------------------------------------------------------------------------------------------


def test_the_verbs_are_in_the_table_with_the_right_shape():
    assert set(LAYER_VERBS) <= set(VERBS)
    for name in LAYER_VERBS:
        verb = VERBS[name]
        assert verb.doc and verb.group == "graphics", name
        assert (verb.creates == "clip") == (name in ("duplicate_layers", "group_layers")), name
        if verb.creates:
            assert "id" in verb.accepts and "as" in verb.accepts


def test_the_json_schema_describes_every_layer_verb():
    titles = {item["title"] for item in json_schema()["properties"]["ops"]["items"]["oneOf"]}
    assert set(LAYER_VERBS) <= titles


# --- add_graphic dans un groupe ----------------------------------------------------------------------------------------


def _with_group() -> Project:
    project = _project()
    layers.group_layers(project, ["a", "b"], name="G", group_id="g")
    return project


def test_add_graphic_can_drop_a_layer_in_a_group_like_add_layer():
    def direct(project: Project) -> None:
        add_layer(project, "text", at=1.0, duration=2.0, group_id="g", clip_id="n")

    project = _parity(_op("add_graphic", graphic="text", at=1, duration=2, group="g", id="n"), direct, _with_group)
    assert _layer(project, "n").graphic.group_id == "g"


def test_add_graphic_group_errors():
    _refused(_with_group(), _op("add_graphic", graphic="text", at=0, duration=1, group="c"), match="n'est pas un groupe")
    _refused(_with_group(), _op("add_graphic", graphic="adjustment", at=0, duration=1, group="g"), match="calque d'effets")
    _refused(_with_group(), _op("add_graphic", graphic="text", at=0, duration=1, group="fantome"))
    _refused(_with_group(), _op("add_graphic", graphic="text", at=0, duration=1, group="v"))        # un clip vidéo n'est pas un groupe
    locked = _with_group()
    _lock(locked, "g")
    _refused(locked, _op("add_graphic", graphic="text", at=0, duration=1, group="g"), match="verrouillé")


def test_add_graphic_without_group_is_unchanged():
    session, report = _apply(_project(), _op("add_graphic", graphic="text", at=0, duration=1, id="n"))
    assert report.ok and _layer(session.project, "n").graphic.group_id == ""


def test_a_group_of_another_sequence_is_not_a_target():
    project = _with_group()
    other = Sequence(id="seq2", name="Autre", width=320, height=180, fps=30.0, tracks=[Track("G9", "G9", "graphics")])
    project.sequences.append(other)
    add_layer_in_other = Project("x", 320, 180, 30.0)
    add_layer_in_other.sequences = [other]
    add_layer_in_other.active_sequence_id = "seq2"
    add_layer(add_layer_in_other, "shape", at=0.0, duration=1.0, clip_id="etranger")
    session, report = _apply(project, _op("set_parent", clip="c", parent="etranger"))
    assert not report.ok and "introuvable" in report.error
    _refused(project, _op("delete_layers", clips=["etranger"]))
    _refused(project, _op("move_into_group", clip="etranger", group="g"))
    _refused(project, _op("add_graphic", graphic="text", at=0, duration=1, group="etranger"))


# --- delete_layers -----------------------------------------------------------------------------------------------------


def test_delete_layers_matches_the_model_and_a_group_takes_its_content():
    project = _parity(_op("delete_layers", clips=["g", "c"]), lambda p: layers.delete_layers(p, ["g", "c"]), _with_group)
    assert _stack(project) == ["d", "z"]
    assert not any(asset.media_type == "graphic" and asset.id.endswith(("-a", "-b")) for asset in project.media_assets)


def test_delete_layers_detaches_the_children_of_a_deleted_parent():
    def factory() -> Project:
        project = _project()
        layers.set_parent(project, "b", "a", keep_visual=False)
        return project

    project = _parity(_op("delete_layers", clips=["a"]), lambda p: layers.delete_layers(p, ["a"]), factory)
    assert _layer(project, "b").graphic.parent_id == ""


def test_delete_layers_refuses_a_locked_layer_a_locked_track_and_a_locked_member():
    locked = _project()
    _lock(locked, "a")
    _refused(locked, _op("delete_layers", clips=["b", "a"]), match="verrouillé")          # rien n'est supprimé, pas même b
    _refused(_project(), _op("delete_layers", clips=["z"]))                                  # piste verrouillée
    member = _with_group()
    _lock(member, "b")
    _refused(member, _op("delete_layers", clips=["g"]), match="verrouillé")                 # le groupe emporterait un membre verrouillé


def test_delete_layers_refuses_when_a_locked_child_would_be_detached():
    """Détacher un enfant verrouillé le modifierait : refusé, même si l'enfant n'est pas dans la liste."""
    project = _project()
    layers.set_parent(project, "b", "a", keep_visual=False)
    _lock(project, "b")
    report = _refused(project, _op("delete_layers", clips=["a"]))
    assert "'b'" in report.error


def test_delete_layers_refuses_a_locked_group_that_loses_a_member():
    project = _with_group()
    _lock(project, "g")
    _refused(project, _op("delete_layers", clips=["a"]), match="Le groupe 'g' est verrouillé")
    ok = _with_group()                                                                       # supprimer le groupe lui-même ne regarde pas son verrou
    session, report = _apply(ok, _op("delete_layers", clips=["g"]))
    assert report.ok and "g" not in _stack(session.project)


def test_delete_layers_list_errors():
    _refused(_project(), _op("delete_layers", clips=["a", "a"]), match="plus d'une fois")
    _refused(_project(), _op("delete_layers", clips=["a", "fantome"]))
    _refused(_project(), _op("delete_layers", clips=["v"]))                                  # un clip vidéo n'est pas un calque
    _invalid(_op("delete_layers", clips=[]))
    _invalid(_op("delete_layers", clips=[f"c{i}" for i in range(layer_rules.MAX_LAYERS_PER_OPERATION + 1)]))
    _invalid(_op("delete_layers", clips="a"))
    _invalid(_op("delete_layers"))


def test_delete_then_undo_restores_the_project_in_one_step():
    project = _with_group()
    before = _snapshot(project)
    session, report = _apply(project, _op("delete_layers", clips=["g"]), _op("delete_layers", clips=["c"]))
    assert report.ok and _snapshot(session.project) != before
    assert session.undo() and _snapshot(session.project) == before


# --- duplicate_layers --------------------------------------------------------------------------------------------------


def test_duplicate_layers_matches_the_model_and_creates_the_copy_of_the_first():
    def direct(project: Project) -> None:
        layers.duplicate_layers(project, ["c"], new_ids={"c": "copie"})

    project = _parity(_op("duplicate_layers", clips=["c"], id="copie"), direct)
    assert _layer(project, "copie").label == _layer(project, "c").label
    assert _layer(project, "copie").asset_id == "graphic-asset-copie"


def test_duplicate_then_reference_the_copy_by_alias_end_to_end():
    project = _project()
    script = parse_script({"kut_script": 1, "ops": [
        _op("duplicate_layers", clips=["c"], **{"as": "copie"}),
        _op("rename_layer", clip={"ref": "copie"}, name="Copie de c"),
        _op("set_layer_visible", clip={"ref": "copie"}, visible=False),
    ]})
    session = Session(project)
    report = apply_script(session, script)
    assert report.ok, report.error
    assert created_ids(script, report) == [{"op": "0:duplicate_layers", "id": "copie"}]
    copy_ = _layer(session.project, "copie")
    assert copy_.label == "Copie de c" and not copy_.graphic.visible


def test_duplicate_without_id_returns_a_generated_copy():
    session, report = _apply(_project(), _op("duplicate_layers", clips=["c"]))
    assert report.ok
    copied = report.results[0].value
    assert copied.id.startswith("graphic-") and copied.id != "c" and _layer(session.project, copied.id).label == "Rectangle"


def test_duplicating_a_group_copies_its_content_with_the_links_between_copies():
    project = _with_group()
    session, report = _apply(project, _op("duplicate_layers", clips=["g"], id="g2"))
    assert report.ok, report.error
    members = [clip for _i, _t, clip in layers.layer_clips(session.project) if clip.graphic.group_id == "g2"]
    assert len(members) == 2 and {clip.label for clip in members} == {"Titre", "Rectangle"}
    assert {clip.id for clip in members}.isdisjoint({"a", "b"})
    assert [clip.graphic.group_id for clip in (_layer(session.project, "a"), _layer(session.project, "b"))] == ["g", "g"]


def test_duplicate_layers_errors():
    _refused(_project(), _op("duplicate_layers", clips=["c"], id="a"), match="existe déjà")             # identifiant pris par un clip
    _refused(_project(), _op("duplicate_layers", clips=["c"], id="v"), match="existe déjà")             # par un clip vidéo (son média technique est libre)
    taken = _project()
    taken.media_assets.append(MediaAsset("graphic-asset-copie", "", "x", 1.0, 1, 1, 30.0, "graphic"))
    _refused(taken, _op("duplicate_layers", clips=["c"], id="copie"), match="existe déjà")             # le média technique serait en double
    _refused(_project(), _op("duplicate_layers", clips=["z"]))                                         # piste verrouillée
    _refused(_project(), _op("duplicate_layers", clips=["c", "c"]), match="plus d'une fois")
    locked = _project()
    _lock(locked, "c")
    _refused(locked, _op("duplicate_layers", clips=["c"]), match="verrouillé")
    _invalid(_op("duplicate_layers", clips=["c"], id="a b"))


def test_a_copy_does_not_join_a_locked_group():
    project = _with_group()
    _lock(project, "g")
    _refused(project, _op("duplicate_layers", clips=["a"]), match="Le groupe 'g' est verrouillé")


def test_duplicating_stays_under_the_project_layer_limit(monkeypatch):
    """Doubler un groupe dans une boucle ferait grossir le projet sans fin : la limite arrête la copie avant d'allouer."""
    monkeypatch.setattr(layer_rules, "MAX_LAYERS_IN_PROJECT", 7)
    project = _with_group()                                            # a, b, c, d, z et le groupe : 6
    _refused(project, _op("duplicate_layers", clips=["g"]), match="limite")                  # le groupe et ses deux membres : 9
    session, report = _apply(project, _op("duplicate_layers", clips=["c"]))                  # 7 : permis
    assert report.ok
    _refused(session.project, _op("duplicate_layers", clips=["c"]), match="limite")


def test_duplicating_an_animated_rotated_layer_keeps_it_like_the_model():
    def factory() -> Project:
        project = _project()
        clip = _layer(project, "c")
        clip.transform = ClipTransform(rotation=30.0, position_x=0.1)
        clip.transform_keyframes = [TransformKeyframe("opacity", 0.0, 0.0), TransformKeyframe("opacity", 2.0, 1.0)]
        return project

    project = _parity(_op("duplicate_layers", clips=["c"], id="copie"), lambda p: layers.duplicate_layers(p, ["c"], new_ids={"c": "copie"}), factory)
    assert _layer(project, "copie").transform.rotation == 30.0 and len(_layer(project, "copie").transform_keyframes) == 2


# --- rename, visible, locked, flou -------------------------------------------------------------------------------------


def test_rename_visible_locked_and_motion_blur_match_the_model():
    _parity(_op("rename_layer", clip="c", name="Fond"), lambda p: layers.rename_layer(p, "c", "Fond"))
    _parity(_op("set_layer_visible", clip="c", visible=False), lambda p: layers.set_layer_visible(p, "c", False))
    _parity(_op("set_layer_locked", clip="c", locked=True), lambda p: layers.set_layer_locked(p, "c", True))
    _parity(_op("set_layer_motion_blur", clip="c", enabled=True), lambda p: layers.set_motion_blur(p, "c", True))


def test_rename_and_visibility_and_lock_work_on_a_locked_layer_like_the_panel():
    """Le panneau Calques laisse renommer, masquer et déverrouiller un calque verrouillé (il faut bien pouvoir le déverrouiller)."""
    project = _project()
    _lock(project, "c")
    session, report = _apply(
        project, _op("rename_layer", clip="c", name="Fond"), _op("set_layer_visible", clip="c", visible=False),
        _op("set_layer_locked", clip="c", locked=False), _op("set_layer_motion_blur", clip="c", enabled=True),
    )
    assert report.ok, report.error
    graphic = _layer(session.project, "c").graphic
    assert (_layer(session.project, "c").label, graphic.visible, graphic.locked, graphic.motion_blur) == ("Fond", False, False, True)


def test_the_motion_blur_of_a_locked_layer_or_track_is_refused():
    locked = _project()
    _lock(locked, "c")
    _refused(locked, _op("set_layer_motion_blur", clip="c", enabled=True), match="verrouillé")
    _refused(_project(), _op("set_layer_motion_blur", clip="z", enabled=True))


def test_rename_updates_the_technical_media_like_the_model():
    project = _parity(_op("rename_layer", clip="c", name="Fond"), lambda p: layers.rename_layer(p, "c", "Fond"))
    assert next(a for a in project.media_assets if a.id == "graphic-asset-c").name == "Fond"


def test_layer_verbs_refuse_a_clip_that_is_not_a_graphic_layer():
    for operation in (
        _op("rename_layer", clip="v", name="x"), _op("set_layer_visible", clip="v", visible=False),
        _op("set_layer_locked", clip="v", locked=True), _op("set_layer_motion_blur", clip="v", enabled=True),
        _op("reorder_layer", clip="v", index=0), _op("move_layer", clip="v", delta=1), _op("group_layers", clips=["v"]),
        _op("ungroup_layers", clip="v"), _op("move_into_group", clip="v"), _op("set_parent", clip="v", parent="a"),
        _op("clear_parent", clip="v"), _op("duplicate_layers", clips=["v"]),
    ):
        _refused(_project(), operation)


def test_rename_text_errors_are_refused_at_reading():
    for name in ("", " x", "x ", "a\x00b", "x" * 201):
        _invalid(_op("rename_layer", clip="c", name=name))
    _invalid(_op("rename_layer", clip="c", name=5))
    _invalid(_op("set_layer_visible", clip="c", visible="oui"))


# --- Ordre -------------------------------------------------------------------------------------------------------------


def test_reorder_and_move_match_the_model():
    assert _stack(_project())[:4] == ["a", "b", "c", "d"]
    project = _parity(_op("reorder_layer", clip="a", index=3), lambda p: layers.reorder_layer(p, "a", 3))
    assert _stack(project)[:4] == ["b", "c", "d", "a"]
    project = _parity(_op("move_layer", clip="d", delta=-2), lambda p: layers.move_layer(p, "d", -2))
    assert _stack(project)[:4] == ["a", "d", "b", "c"]
    _parity(_op("reorder_layer", clip="c", index=2), lambda p: layers.reorder_layer(p, "c", 2))          # déjà à sa place


def test_reorder_and_move_refuse_what_the_model_would_clamp():
    stack = len(_stack(_project())) - 1                           # z, sur une piste verrouillée, est aussi un frère
    _refused(_project(), _op("reorder_layer", clip="a", index=stack + 1), match="hors de la pile")
    _refused(_project(), _op("move_layer", clip="a", delta=-1), match="sortirait de la pile")
    _refused(_project(), _op("move_layer", clip="d", delta=stack), match="sortirait de la pile")
    _refused(_project(), _op("move_layer", clip="a", delta=0), match="non nul")
    _invalid(_op("reorder_layer", clip="a", index=-1))
    _invalid(_op("reorder_layer", clip="a", index=1.5))
    _invalid(_op("move_layer", clip="a", delta=True))
    _invalid(_op("reorder_layer", clip="a", index=10 ** 6))


def test_reorder_locks():
    locked = _project()
    _lock(locked, "c")
    _refused(locked, _op("reorder_layer", clip="c", index=0), match="verrouillé")
    _refused(_project(), _op("reorder_layer", clip="z", index=0))
    _refused(_project(), _op("move_layer", clip="z", delta=-1))


def test_reorder_refuses_to_renumber_a_locked_layer_but_not_when_nothing_of_it_changes():
    """Un changement d'ordre renumérote les frères : le calque verrouillé qui changerait de rang fait refuser le verbe."""
    project = _project()
    _lock(project, "b")
    report = _refused(project, _op("reorder_layer", clip="c", index=0))                       # b passerait du rang 1 au rang 2
    assert "'b'" in report.error
    top = _project()
    _lock(top, "d")                                                                          # le plus haut : les autres se permutent sans lui toucher
    session, done = _apply(top, _op("reorder_layer", clip="a", index=2))
    assert done.ok, done.error
    assert _stack(session.project)[:3] == ["b", "c", "a"] and _layer(session.project, "d").graphic.z_order == 3


def test_a_reorder_into_a_neighbour_on_a_locked_track_is_refused():
    project = _project()
    layers.set_layer_locked(project, "z", False)                  # z reste sur la piste G2, verrouillée
    _refused(project, _op("reorder_layer", clip="d", index=len(_stack(project)) - 1))


# --- Groupes -----------------------------------------------------------------------------------------------------------


def test_group_layers_matches_the_model_and_creates_the_group():
    def direct(project: Project) -> None:
        layers.group_layers(project, ["a", "b"], name="Titre+forme", group_id="g")

    project = _parity(_op("group_layers", clips=["a", "b"], name="Titre+forme", id="g"), direct)
    group = _layer(project, "g")
    assert group.label == "Titre+forme" and _layer(project, "a").graphic.group_id == "g"


def test_group_layers_default_name_is_the_models():
    session, report = _apply(_project(), _op("group_layers", clips=["a", "b"], id="g"))
    assert report.ok and _layer(session.project, "g").label == "Groupe"


def test_group_layers_errors():
    _refused(_project(), _op("group_layers", clips=["a", "a"]), match="plus d'une fois")
    _refused(_project(), _op("group_layers", clips=["a", "z"]))                                # piste verrouillée
    _refused(_project(), _op("group_layers", clips=["a", "b"], id="c"), match="existe déjà")
    nested = _with_group()
    _refused(nested, _op("group_layers", clips=["a", "c"]), match="même niveau")               # a est dans g, c non
    adjustment = _project()
    add_layer(adjustment, "adjustment", at=0.0, duration=2.0, clip_id="reglage")
    _refused(adjustment, _op("group_layers", clips=["a", "reglage"]), match="calque d'effets")
    locked = _project()
    _lock(locked, "b")
    _refused(locked, _op("group_layers", clips=["a", "b"]), match="verrouillé")
    _invalid(_op("group_layers", clips=[]))
    _invalid(_op("group_layers", clips=["a"], name=""))


def test_group_layers_inside_a_locked_group_is_refused():
    project = _with_group()
    layers.group_layers(project, ["c", "d"], name="H", group_id="h")
    layers.move_into_group(project, "h", "g")
    _lock(project, "g")
    _refused(project, _op("group_layers", clips=["h"]), match="Le groupe 'g' est verrouillé")


def test_group_layers_refuses_to_renumber_a_locked_layer():
    """Le nouveau groupe se glisse dans la pile de la piste : un calque verrouillé au-dessus changerait de rang."""
    project = _project()
    _lock(project, "d")
    _refused(project, _op("group_layers", clips=["a", "b"]), match="'d'")


def test_ungroup_matches_the_model_for_a_neutral_group_and_for_a_transformed_one():
    project = _parity(_op("ungroup_layers", clip="g"), lambda p: layers.ungroup(p, "g"), _with_group)
    assert "g" not in _stack(project) and _layer(project, "a").graphic.group_id == ""

    def transformed() -> Project:
        built = _with_group()
        _layer(built, "g").transform = ClipTransform(position_x=0.2)
        return built

    project = _parity(_op("ungroup_layers", clip="g"), lambda p: layers.ungroup(p, "g"), transformed)
    assert _layer(project, "g").graphic.type.value == "null" and _layer(project, "a").graphic.parent_id == "g"


def test_ungroup_inside_a_locked_group_is_refused():
    project = _with_group()
    layers.group_layers(project, ["c", "d"], name="H", group_id="h")
    layers.move_into_group(project, "h", "g")
    _lock(project, "g")
    _refused(project, _op("ungroup_layers", clip="h"), match="Le groupe 'g' est verrouillé")


def test_ungroup_errors_and_locks():
    _refused(_project(), _op("ungroup_layers", clip="a"), match="n'est pas un groupe")
    locked = _with_group()
    _lock(locked, "g")
    _refused(locked, _op("ungroup_layers", clip="g"), match="verrouillé")
    member = _with_group()
    _lock(member, "b")
    _refused(member, _op("ungroup_layers", clip="g"), match="'b'")                              # b changerait de groupe


# --- move_into_group ---------------------------------------------------------------------------------------------------


def test_move_into_group_matches_the_model_in_both_directions():
    project = _parity(_op("move_into_group", clip="c", group="g", at=1.5), lambda p: layers.move_into_group(p, "c", "g", at_time=1.5), _with_group)
    assert _layer(project, "c").graphic.group_id == "g"
    project = _parity(_op("move_into_group", clip="a"), lambda p: layers.move_into_group(p, "a", ""), _with_group)
    assert _layer(project, "a").graphic.group_id == ""


def test_move_into_group_keeps_the_appearance_of_a_rotated_layer_in_a_transformed_group():
    project = _with_group()
    _layer(project, "g").transform = ClipTransform(position_x=0.2, scale=1.5, rotation=20.0)
    _layer(project, "c").transform = ClipTransform(position_x=-0.1, rotation=35.0)
    before = layer_rules.appearance(project, "c", 1.0)
    session, report = _apply(project, _op("move_into_group", clip="c", group="g", at=1.0))
    assert report.ok, report.error
    after = layer_rules.appearance(session.project, "c", 1.0)
    assert all(abs(x - y) < 0.01 for pair, other in zip(before, after) for x, y in zip(pair, other))


def test_move_into_group_refuses_an_appearance_it_cannot_keep():
    project = _with_group()
    _layer(project, "g").transform = ClipTransform(scale_x=0.0, scale_y=0.0)
    _refused(project, _op("move_into_group", clip="c", group="g"), match="apparence")


def test_move_into_group_errors():
    _refused(_with_group(), _op("move_into_group", clip="c", group="d"), match="n'est pas un groupe")
    _refused(_with_group(), _op("move_into_group", clip="g", group="g"), match="contenir")           # un groupe dans lui-même
    nested = _with_group()
    layers.group_layers(nested, ["c", "d"], name="H", group_id="h")
    layers.move_into_group(nested, "h", "g")
    _refused(nested, _op("move_into_group", clip="g", group="h"), match="contenir")                  # dans son propre descendant
    adjustment = _with_group()
    add_layer(adjustment, "adjustment", at=0.0, duration=2.0, clip_id="reglage")
    _refused(adjustment, _op("move_into_group", clip="reglage", group="g"), match="calque d'effets")
    _refused(_with_group(), _op("move_into_group", clip="z", group="g"))
    _refused(_with_group(), _op("move_into_group", clip="c", group="v"))


def test_move_into_group_locked_groups_are_neither_entered_nor_left():
    target = _with_group()
    layers.group_layers(target, ["c", "d"], name="H", group_id="h")
    _lock(target, "h")
    _refused(target, _op("move_into_group", clip="a", group="h"), match="Le groupe 'h' est verrouillé")
    source = _with_group()
    _lock(source, "g")
    _refused(source, _op("move_into_group", clip="a"), match="Le groupe 'g' est verrouillé")
    _refused(source, _op("move_into_group", clip="a", group="c"))


# --- Parentage ---------------------------------------------------------------------------------------------------------


def test_set_and_clear_parent_match_the_model():
    project = _parity(_op("set_parent", clip="c", parent="a", at=2.0), lambda p: layers.set_parent(p, "c", "a", at_time=2.0))
    assert _layer(project, "c").graphic.parent_id == "a"

    def factory() -> Project:
        built = _project()
        layers.set_parent(built, "c", "a", keep_visual=False)
        return built

    project = _parity(_op("clear_parent", clip="c", at=2.0), lambda p: layers.clear_parent(p, "c", at_time=2.0), factory)
    assert _layer(project, "c").graphic.parent_id == ""


def test_clearing_a_missing_parent_is_a_no_op():
    project = _project()
    session, report = _apply(project, _op("clear_parent", clip="c"))
    assert report.ok and _same(session.project, project)


def test_a_video_clip_can_be_a_parent_like_in_the_panel():
    project = _parity(_op("set_parent", clip="c", parent="v"), lambda p: layers.set_parent(p, "c", "v"))
    assert _layer(project, "c").graphic.parent_id == "v"


def test_set_parent_keeps_the_appearance_of_a_rotated_layer_with_animated_opacity():
    project = _project()
    _layer(project, "a").transform = ClipTransform(position_x=0.2, scale=1.3, rotation=25.0)
    _layer(project, "c").transform = ClipTransform(position_y=0.1, rotation=40.0)
    _layer(project, "c").transform_keyframes = [TransformKeyframe("opacity", 0.0, 0.0), TransformKeyframe("opacity", 3.0, 1.0)]
    before = layer_rules.appearance(project, "c", 1.0)
    session, report = _apply(project, _op("set_parent", clip="c", parent="a", at=1.0))
    assert report.ok, report.error
    after = layer_rules.appearance(session.project, "c", 1.0)
    assert all(abs(x - y) < 0.01 for pair, other in zip(before, after) for x, y in zip(pair, other))
    assert len(_layer(session.project, "c").transform_keyframes) == 2


def test_set_parent_refuses_an_animated_position_the_model_would_shift_wrongly():
    """Le modèle décale les images clés de position du calque d'un écart calculé sur la valeur **animée** : sous un parent tourné, la
    position à l'instant de référence serait comptée deux fois et le calque sauterait. Un script le reçoit refusé."""
    project = _project()
    _layer(project, "a").transform = ClipTransform(position_x=0.2, scale=1.3, rotation=25.0)
    _layer(project, "c").transform = ClipTransform(position_y=0.1, rotation=40.0)
    _layer(project, "c").transform_keyframes = [TransformKeyframe("position_x", 0.0, -0.2), TransformKeyframe("position_x", 3.0, 0.2)]
    _refused(project, _op("set_parent", clip="c", parent="a", at=1.0), match="apparence")


def test_set_parent_refuses_an_appearance_it_cannot_keep():
    """Un parent à échelle nulle : le modèle laisserait le calque sauter (il « garde ses valeurs ») ; un script le reçoit refusé."""
    project = _project()
    _layer(project, "a").transform = ClipTransform(scale_x=0.0, scale_y=0.0, position_x=0.3)
    _refused(project, _op("set_parent", clip="c", parent="a"), match="apparence")


def test_set_parent_refuses_a_layer_whose_position_is_driven_by_a_tracker():
    """Un suivi pilote la position : sous un parent transformé, le recalcul du modèle la compterait deux fois (le calque sauterait)."""
    from core import tracking_ops

    def tracked() -> Project:
        project = _project()
        tracker = tracking_ops.add_tracker(project, "v", timeline_time=0.0, x=100.0, y=60.0)
        for i in range(1, 6):
            tracking_ops.set_tracker_position(project, "v", tracker.id, i * 0.5, 100.0 + 20 * i, 60.0 + 5 * i)
        tracking_ops.add_link(project, "c", "v", [tracker.id], timeline_time=0.0)
        return project

    moved = tracked()
    _layer(moved, "a").transform = ClipTransform(position_x=0.2, scale=1.3, rotation=25.0)
    _refused(moved, _op("set_parent", clip="c", parent="a", at=1.0), match="apparence")
    session, report = _apply(tracked(), _op("set_parent", clip="c", parent="a", at=1.0))        # parent sans transform : rien ne bouge
    assert report.ok, report.error


def test_set_parent_on_a_full_frame_background_and_on_the_same_parent_twice():
    project = _project()
    add_layer(project, "solid", at=0.0, duration=4.0, clip_id="fond")
    _layer(project, "d").transform = ClipTransform(scale=0.5, position_x=0.1)
    before = layer_rules.appearance(project, "fond", 0.0)
    session, report = _apply(project, _op("set_parent", clip="fond", parent="d"), _op("set_parent", clip="fond", parent="d"))
    assert report.ok, report.error
    after = layer_rules.appearance(session.project, "fond", 0.0)
    assert all(abs(x - y) < 0.01 for pair, other in zip(before, after) for x, y in zip(pair, other))
    assert _layer(session.project, "fond").graphic.parent_id == "d"


def test_set_parent_cycles_are_refused():
    _refused(_project(), _op("set_parent", clip="a", parent="a"), match="propre parent")
    chain = _project()
    layers.set_parent(chain, "b", "a", keep_visual=False)
    layers.set_parent(chain, "c", "b", keep_visual=False)
    _refused(chain, _op("set_parent", clip="a", parent="b"), match="boucle")                         # A suit B qui suit A
    _refused(chain, _op("set_parent", clip="a", parent="c"), match="boucle")                         # A suit C qui suit B qui suit A
    through_group = _with_group()
    _refused(through_group, _op("set_parent", clip="g", parent="a"), match="boucle")                 # le groupe suivrait son propre membre
    ancestors = _with_group()
    layers.set_parent(ancestors, "c", "g", keep_visual=False)                                         # c suit g
    _refused(ancestors, _op("set_parent", clip="g", parent="c"), match="boucle")


def test_set_parent_errors_and_locks():
    _refused(_project(), _op("set_parent", clip="c", parent="fantome"))
    locked = _project()
    _lock(locked, "c")
    _refused(locked, _op("set_parent", clip="c", parent="a"), match="verrouillé")
    _refused(_project(), _op("set_parent", clip="z", parent="a"))
    _refused(_project(), _op("clear_parent", clip="z"))
    nested = _project()
    nested.tracks.append(Track("V2", "V2", "video", clips=[Clip("imbrique", "m", "V2", 0.0, 0.0, 2.0, sequence_id="seq9")]))
    _refused(nested, _op("set_parent", clip="c", parent="imbrique"), match="parent")
    audio = _project()
    audio.tracks.append(Track("A1", "A1", "audio", clips=[Clip("son", "m", "A1", 0.0, 0.0, 2.0)]))
    _refused(audio, _op("set_parent", clip="c", parent="son"), match="parent")


def test_a_locked_parent_stays_untouched_and_may_be_followed():
    project = _project()
    _lock(project, "a")
    session, report = _apply(project, _op("set_parent", clip="c", parent="a"))
    assert report.ok, report.error
    assert _layer(session.project, "a") == _layer(project, "a")


def test_parent_chains_and_nested_groups_are_bounded_on_the_resulting_graph():
    depth = layer_rules.MAX_HIERARCHY_DEPTH
    project = _project()
    ops = [_op("add_graphic", graphic="null", at=0, duration=1, id=f"n{i}") for i in range(depth + 1)]
    ops += [_op("set_parent", clip=f"n{i}", parent=f"n{i - 1}") for i in range(1, depth)]       # n0 <- n1 <- ... : `depth` niveaux
    session, report = _apply(project, *ops)
    assert report.ok, report.error
    last = _op("set_parent", clip=f"n{depth}", parent=f"n{depth - 1}")
    _refused(session.project, last, match=f"{depth} niveaux")
    _refused(session.project, _op("set_parent", clip="n0", parent=f"n{depth - 1}"), match="boucle")    # n31 suit n0 : une boucle
    # Reparenter la racine sous un nouveau parent allonge la chaîne par le haut : refusé aussi.
    _refused(session.project, _op("set_parent", clip="n0", parent="c"), match=f"{depth} niveaux")


def test_add_graphic_into_the_deepest_group_is_bounded_too():
    depth = layer_rules.MAX_HIERARCHY_DEPTH
    project = _project()
    layers.group_layers(project, ["a"], group_id="k0")
    for index in range(1, depth):
        layers.group_layers(project, [f"k{index - 1}"], group_id=f"k{index}")
    layers.delete_layers(project, ["a"])                                       # k0 est vide : la chaîne k0 … k31 a `depth` niveaux
    _refused(project, _op("add_graphic", graphic="text", at=0, duration=1, group="k0"), match="niveaux")
    shallow = _project()
    layers.group_layers(shallow, ["a"], group_id="k0")
    session, report = _apply(shallow, _op("add_graphic", graphic="text", at=0, duration=1, group="k0", id="n"))
    assert report.ok, report.error


def test_a_loop_already_in_a_file_is_refused_instead_of_followed():
    """Un fichier retouché à la main peut contenir une boucle de parentage : la profondeur la voit et refuse au lieu de tourner."""
    project = _project()
    layers._set_graphic(_layer(project, "a"), parent_id="b")
    layers._set_graphic(_layer(project, "b"), parent_id="a")
    _refused(project, _op("set_parent", clip="c", parent="a"), match="boucle")


def test_nested_groups_are_bounded_on_the_resulting_graph():
    depth = layer_rules.MAX_HIERARCHY_DEPTH
    ops = [_op("group_layers", clips=["a"], id="k0")]
    ops += [_op("group_layers", clips=[f"k{i - 1}"], id=f"k{i}") for i in range(1, depth - 1)]
    ops += [_op("group_layers", clips=["c", "d"], id="h")]
    session, report = _apply(_project(), *ops)
    assert report.ok, report.error                                                 # a + k0 … k{depth-2} : `depth` niveaux
    top = f"k{depth - 2}"
    _refused(session.project, _op("group_layers", clips=[top], id="trop"), match="niveaux")
    # Enfouir toute la chaîne sous un autre groupe l'allongerait par le haut : refusé sur le graphe résultant.
    _refused(session.project, _op("move_into_group", clip=top, group="h"), match="niveaux")
    # Un calque seul peut en revanche rejoindre le groupe du haut.
    done, moved = _apply(session.project, _op("move_into_group", clip="b", group=top))
    assert moved.ok, moved.error


# --- Entrées démesurées, atomicité -------------------------------------------------------------------------------------


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf, 1e300, -1.0, True, "1"])
def test_hostile_instants_are_refused_at_reading(value):
    _invalid(_op("set_parent", clip="c", parent="a", at=value))
    _invalid(_op("move_into_group", clip="c", at=value))
    _invalid(_op("clear_parent", clip="c", at=value))


def test_huge_inputs_are_bounded_at_reading():
    _invalid(_op("rename_layer", clip="c", name="x" * 5_000_000))
    _invalid(_op("delete_layers", clips=["a"] * 100_000))
    _invalid(_op("group_layers", clips=[str(i) for i in range(10_000)]))
    _invalid(_op("duplicate_layers", clips=["a"] * 201))


def test_a_failing_layer_verb_rolls_back_the_whole_script_and_one_step_undoes_a_good_one():
    project = _with_group()
    before = _snapshot(project)
    session, report = _apply(
        project, _op("rename_layer", clip="c", name="Nouveau"), _op("move_layer", clip="c", delta=1),
        _op("set_parent", clip="a", parent="a"),
    )
    assert not report.ok and report.failed_index == 2 and _snapshot(session.project) == before
    session, report = _apply(project, _op("rename_layer", clip="c", name="Nouveau"), _op("move_layer", clip="c", delta=1))
    assert report.ok and session.undo() and _snapshot(session.project) == before


def test_the_frozen_witness_is_what_protects_third_parties():
    """La photographie des calques verrouillés voit un rang, un parent, un groupe ou une piste qui changent."""
    project = _project()
    _lock(project, "b")
    frozen = layer_rules.frozen_layers(project)
    assert set(frozen) == {"b", "z"}
    layer_rules.require_untouched(project, frozen)
    layers._set_graphic(_layer(project, "b"), parent_id="a")
    with pytest.raises(ValueError, match="'b'"):
        layer_rules.require_untouched(project, frozen)
    project.tracks[1].clips = [clip for clip in project.tracks[1].clips if clip.id != "b"]
    with pytest.raises(ValueError, match="'b'"):
        layer_rules.require_untouched(project, frozen)


def test_graphic_overlay_class_is_what_the_layer_selectors_resolve():
    assert isinstance(_layer(_project(), "a").graphic, GraphicOverlay)


# --- Plafond de calques du projet (toutes séquences) --------------------------------------------------------------------


def _two_sequences() -> Project:
    """5 calques dans la séquence active, 4 dans une autre : 9 en tout."""
    project = _with_group()                                            # a, b, c, d, z et le groupe g : 6
    other = Project("x", 320, 180, 30.0)
    other.sequences[0].id = "seq2"
    other.active_sequence_id = "seq2"
    for index in range(3):
        add_layer(other, "shape", at=0.0, duration=1.0, clip_id=f"autre{index}")
    project.sequences.append(other.sequences[0])
    return project


_CREATING = (
    _op("add_graphic", graphic="text", at=0, duration=1),
    _op("add_graphic", graphic="text", at=0, duration=1, group="g"),
    _op("group_layers", clips=["c", "d"]),
    _op("duplicate_layers", clips=["c"]),
    _op("duplicate_layers", clips=["g"]),
    _op("apply_layer_preset", preset="Titre simple", at=0),
    _op("apply_layer_preset", preset="Lower third", at=0),
    _op("duplicate_clip", clip="c"),
    _op("cut_clip", clip="c", at=1.0),
)


def test_the_layer_cap_counts_every_sequence(monkeypatch):
    """Basculer sur une séquence peu peuplée ne contourne pas le plafond : il compte les calques de toutes les séquences."""
    project = _two_sequences()
    assert layer_rules.total_layers(project) == 9 and len(layers.layer_clips(project)) == 6
    monkeypatch.setattr(layer_rules, "MAX_LAYERS_IN_PROJECT", 9)
    for operation in _CREATING:
        report = _refused(copy.deepcopy(project), operation, match="limite")
        assert report is not None


def test_every_creating_path_counts_exactly_what_it_creates(monkeypatch):
    project = _two_sequences()
    monkeypatch.setattr(layer_rules, "MAX_LAYERS_IN_PROJECT", 10)
    for operation in (_CREATING[0], _CREATING[1], _CREATING[2], _CREATING[3], _CREATING[7], _CREATING[8], _CREATING[5]):
        session, report = _apply(copy.deepcopy(project), operation)
        assert report.ok, (operation, report.error)
    _refused(copy.deepcopy(project), _CREATING[4], match="limite")            # un groupe et ses deux membres : 3
    _refused(copy.deepcopy(project), _CREATING[6], match="limite")            # le bandeau : 4 calques
    monkeypatch.setattr(layer_rules, "MAX_LAYERS_IN_PROJECT", 12)
    _session, done = _apply(copy.deepcopy(project), _CREATING[4])
    assert done.ok
    _refused(copy.deepcopy(project), _CREATING[6], match="limite")            # 9 + 4 = 13
