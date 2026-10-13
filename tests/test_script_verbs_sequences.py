"""Format déclaratif, lot 6 (ADR-0005 étape 4) : séquences et séquences imbriquées.

Les verbes de séquence (`create_sequence`, `rename_sequence`, `set_sequence_format`, `duplicate_sequence`, `delete_sequence`) désignent
leur séquence par un sélecteur ; ceux qui touchent à la timeline (pistes, clips, `insert_sequence_clip`) agissent sur la séquence ACTIVE,
qu'un script change avec `set_active_sequence`. Chaque test vérifie un refus du modèle (cycle, profondeur, verrou, genre de piste, bornes)
ou la parité avec les fonctions que l'interface appelle.
"""

from __future__ import annotations

from copy import deepcopy

import pytest

from core.project_model import MAIN_SEQUENCE_ID, Clip, MediaAsset, Project, Track
from core.scripting import ScriptInvalid, Session, apply_script, created_ids, parse_script
from core.scripting.verbs import VERBS
from core.sequences import (
    MAX_NESTING_DEPTH, create_sequence, duplicate_sequence, insert_sequence_clip, rename_sequence, set_sequence_format,
)
from ui import i18n

SEQUENCE_VERBS = (
    "create_sequence", "rename_sequence", "set_sequence_format", "duplicate_sequence", "insert_sequence_clip",
    "set_active_sequence", "delete_sequence",
)


@pytest.fixture(autouse=True)
def _french(monkeypatch):
    monkeypatch.setenv("KUT_STUDIO_LANG", "fr")
    i18n.reset_for_tests()
    yield
    i18n.reset_for_tests()


def _project() -> Project:
    return Project(
        "Démo", width=1280, height=720, fps=30.0,
        media_assets=[MediaAsset("m", "/x/intro.mp4", "intro", 10.0, 1280, 720, 30.0, "video", has_audio=True)],
        tracks=[Track("V1", "V1", "video"), Track("A1", "A1", "audio")],
    )


def _script(*ops: dict) -> dict:
    return {"kut_script": 1, "ops": list(ops)}


def _apply(project: Project, *ops: dict):
    session = Session(project)
    return session, apply_script(session, parse_script(_script(*ops)))


def _intro_project(*, intro_clips: tuple[tuple[str, float, float], ...] = (("c1", 0.0, 4.0),)) -> Project:
    """``main`` (V1, A1, vide) et ``intro`` (4 s par défaut), construites avec les fonctions de ``core``."""
    project = _project()
    intro = create_sequence(project, "Intro", sequence_id="intro")
    for clip_id, start, length in intro_clips:
        intro.tracks[0].clips.append(Clip(clip_id, "m", intro.tracks[0].id, start, 0.0, length, label=clip_id))
    return project


def _nest(project: Project, parent: str, child: str, clip_id: str) -> None:
    sequence = project.get_sequence(parent)
    sequence.tracks[0].clips.append(
        Clip(clip_id, "", sequence.tracks[0].id, 0.0, 0.0, project.get_sequence(child).duration, label=clip_id, sequence_id=child)
    )


# --- Le catalogue ------------------------------------------------------------------------------------------------------


def test_the_sequence_verbs_are_in_the_table_and_create_ones_take_id_and_alias():
    for name in SEQUENCE_VERBS:
        assert name in VERBS and VERBS[name].group == "sequences", name
    assert VERBS["create_sequence"].creates == VERBS["duplicate_sequence"].creates == "sequence"
    assert VERBS["insert_sequence_clip"].creates == "clip"
    assert {"id", "as"} <= set(VERBS["create_sequence"].accepts)


# --- Nominal -----------------------------------------------------------------------------------------------------------


def test_create_a_sequence_fill_it_and_nest_it():
    script = parse_script(_script(
        {"op": "create_sequence", "name": "Intro", "as": "intro", "width": 1920, "height": 1080, "fps": 25},
        {"op": "set_active_sequence", "sequence": {"ref": "intro"}},
        {"op": "add_clip", "track": "V1", "asset": "m", "at": 0, "as": "plan"},
        {"op": "set_active_sequence", "sequence": MAIN_SEQUENCE_ID},
        {"op": "insert_sequence_clip", "sequence": {"ref": "intro"}, "track": "V1", "at": 2, "source_out": 4, "as": "nid"},
    ))
    session = Session(_project())
    report = apply_script(session, script)
    assert report.ok, report.error
    project = session.project
    intro = project.get_sequence("intro")
    assert (intro.name, intro.width, intro.height, intro.fps) == ("Intro", 1920, 1080, 25.0)
    assert project.active_sequence_id == MAIN_SEQUENCE_ID
    nested = next(clip for clip in project.active_sequence.tracks[0].clips if clip.id == "nid")
    assert (nested.sequence_id, nested.timeline_start, nested.source_in, nested.source_out, nested.label) == ("intro", 2.0, 0.0, 4.0, "Intro")
    assert created_ids(script, report) == [
        {"op": "0:create_sequence", "id": "intro"}, {"op": "2:add_clip", "id": "plan"}, {"op": "4:insert_sequence_clip", "id": "nid"},
    ]


def test_a_new_sequence_is_not_activated_and_inherits_the_frame_and_tracks():
    session, report = _apply(_project(), {"op": "create_sequence", "as": "autre"})
    assert report.ok, report.error
    project = session.project
    other = project.get_sequence("autre")
    assert project.active_sequence_id == MAIN_SEQUENCE_ID, "create_sequence n'active pas, comme create_sequence du modèle"
    assert (other.width, other.height, other.fps) == (1280, 720, 30.0)
    assert [track.id for track in other.tracks] == ["V1", "A1"] and not any(track.clips for track in other.tracks)


def test_explicit_id_alias_and_replay_give_the_same_ids():
    ops = (
        {"op": "create_sequence", "id": "seq_a", "name": "A"},
        {"op": "create_sequence", "as": "seq_b", "name": "B"},
        {"op": "duplicate_sequence", "sequence": {"ref": "seq_b"}, "as": "copie", "name": "B bis"},
    )
    first, report = _apply(_project(), *ops)
    assert report.ok, report.error
    second, _ = _apply(_project(), *ops)
    assert [s.id for s in first.project.sequences] == [s.id for s in second.project.sequences] == [MAIN_SEQUENCE_ID, "seq_a", "seq_b", "copie"]


def test_ids_already_taken_are_refused_not_overwritten():
    project = _intro_project()
    for op in ({"op": "create_sequence", "id": "intro", "name": "Autre"}, {"op": "duplicate_sequence", "sequence": "intro", "id": "intro"}):
        _session, report = _apply(project, op)
        assert not report.ok and "existe déjà" in report.error, op["op"]
    assert [s.name for s in project.sequences][-1] == "Intro" and len(project.sequences) == 2
    with pytest.raises(ScriptInvalid):
        parse_script(_script({"op": "create_sequence", "id": "bad id"}))
    with pytest.raises(ScriptInvalid):
        parse_script(_script({"op": "create_sequence", "id": "d"}, {"op": "create_sequence", "as": "d"}))


def test_an_alias_of_another_kind_is_not_a_sequence():
    with pytest.raises(ScriptInvalid) as caught:
        parse_script(_script({"op": "add_bus", "as": "b"}, {"op": "rename_sequence", "sequence": {"ref": "b"}, "name": "X"}))
    assert [issue.code for issue in caught.value.issues] == ["alias_kind"]
    with pytest.raises(ScriptInvalid):
        parse_script(_script({"op": "create_sequence", "as": "s"}, {"op": "route_track", "track": {"ref": "s"}}))


# --- Renommer, format, dupliquer, supprimer ----------------------------------------------------------------------------


def test_rename_by_name_and_by_id_and_a_taken_name_is_refused():
    project = _intro_project()
    session, report = _apply(project, {"op": "rename_sequence", "sequence": "Intro", "name": "Ouverture"})
    assert report.ok and project.get_sequence("intro").name == "Ouverture"
    _session, report = _apply(project, {"op": "rename_sequence", "sequence": "intro", "name": "Ouverture"})
    assert report.ok, "garder son propre nom n'est pas un conflit"
    before = [s.name for s in project.sequences]
    _session, report = _apply(project, {"op": "rename_sequence", "sequence": "intro", "name": project.sequences[0].name})
    assert not report.ok and "s'appelle déjà" in report.error
    assert [s.name for s in project.sequences] == before


def test_names_are_bounded_like_every_script_string():
    for bad in ("", "  x", "a\nb", "x" * 201, "\x00"):
        with pytest.raises(ScriptInvalid):
            parse_script(_script({"op": "create_sequence", "name": bad}))
        with pytest.raises(ScriptInvalid):
            parse_script(_script({"op": "rename_sequence", "sequence": "s", "name": bad}))


def test_create_refuses_a_taken_name_instead_of_numbering_it():
    project = _intro_project()
    _session, report = _apply(project, {"op": "create_sequence", "name": "Intro"})
    assert not report.ok and "s'appelle déjà" in report.error
    assert len(project.sequences) == 2


def test_formats_are_read_from_the_model_bounds():
    for field, value in (("width", 15), ("width", 8194), ("height", 15), ("fps", 0.5), ("fps", 241)):
        with pytest.raises(ScriptInvalid) as caught:
            parse_script(_script({"op": "create_sequence", field: value}))
        assert caught.value.issues[0].code == "out_of_range", (field, value)
    for field, value in (("width", 1921), ("height", 1081)):                      # impair : refusé par le modèle, pas arrondi
        project = _project()
        _session, report = _apply(project, {"op": "create_sequence", field: value})
        assert not report.ok and "Taille de cadre refusée" in report.error, field
        assert len(project.sequences) == 1
    project = _intro_project()
    _session, report = _apply(project, {"op": "set_sequence_format", "sequence": "intro", "width": 1001})
    assert not report.ok and "Taille de cadre refusée" in report.error
    _session, report = _apply(project, {"op": "set_sequence_format", "sequence": "intro"})
    assert not report.ok and "width, height" in report.error


def test_set_sequence_format_changes_only_what_is_given_and_matches_the_interface_function():
    project = _intro_project()
    expected = deepcopy(project)
    set_sequence_format(expected, "intro", 1920, 720, 30.0)
    session, report = _apply(project, {"op": "set_sequence_format", "sequence": "intro", "width": 1920})
    assert report.ok, report.error
    assert session.project.get_sequence("intro") == expected.get_sequence("intro")
    assert session.project.get_sequence(MAIN_SEQUENCE_ID).width == 1280, "la séquence active n'a pas bougé"


def test_duplicate_matches_the_interface_function_and_keeps_nested_references():
    project = _intro_project()
    _nest(project, MAIN_SEQUENCE_ID, "intro", "nid")
    expected = deepcopy(project)
    clone = duplicate_sequence(expected, "intro", "Intro bis", clone_id="intro2")
    session, report = _apply(project, {"op": "duplicate_sequence", "sequence": "intro", "name": "Intro bis", "id": "intro2"})
    assert report.ok, report.error
    got = session.project.get_sequence("intro2")
    assert (got.name, got.width, got.fps, len(got.tracks[0].clips)) == (clone.name, clone.width, clone.fps, 1)
    assert got.tracks[0].clips[0].id != "c1", "les clips de la copie ont des identifiants neufs"
    _session, report = _apply(project, {"op": "duplicate_sequence", "sequence": "intro", "name": "Intro"})
    assert not report.ok and "s'appelle déjà" in report.error
    _session, report = _apply(project, {"op": "duplicate_sequence", "sequence": "intro"})
    assert report.ok and project.sequences[-1].name == "Intro copie", "sans nom, le modèle nomme la copie"


def test_delete_an_unused_sequence_but_never_a_used_active_or_last_one():
    project = _intro_project()
    _nest(project, MAIN_SEQUENCE_ID, "intro", "nid")
    create_sequence(project, "Libre", sequence_id="libre")
    snapshot = deepcopy(project.sequences)
    _session, report = _apply(project, {"op": "delete_sequence", "sequence": "intro"})
    assert not report.ok and "est utilisée" in report.error
    _session, report = _apply(project, {"op": "delete_sequence", "sequence": MAIN_SEQUENCE_ID})
    assert not report.ok and "séquence active" in report.error
    assert project.sequences == snapshot, "un refus ne laisse rien"
    session, report = _apply(project, {"op": "delete_sequence", "sequence": "libre"})
    assert report.ok and project.get_sequence("libre") is None
    only = _project()
    _session, report = _apply(only, {"op": "delete_sequence", "sequence": MAIN_SEQUENCE_ID})
    assert not report.ok and len(only.sequences) == 1
    # une séquence qu'aucun clip n'utilise plus se supprime, une fois la séquence active changée
    session, report = _apply(_intro_project(), {"op": "set_active_sequence", "sequence": "intro"}, {"op": "delete_sequence", "sequence": MAIN_SEQUENCE_ID})
    assert report.ok and session.project.active_sequence_id == "intro"


# --- Insertion : les refus du modèle -----------------------------------------------------------------------------------


def test_insert_matches_the_interface_function():
    project = _intro_project()
    expected = deepcopy(project)
    clip = insert_sequence_clip(expected, "intro", "V1", 1.5, source_in=0.5, source_out=3.0, clip_id="nid")
    session, report = _apply(
        project, {"op": "insert_sequence_clip", "sequence": "Intro", "track": "V1", "at": 1.5, "source_in": 0.5, "source_out": 3, "id": "nid"},
    )
    assert report.ok, report.error
    assert session.project.active_sequence.tracks[0].clips == expected.active_sequence.tracks[0].clips
    assert report.values[0] == clip


def test_insert_on_an_audio_track_is_allowed_and_the_default_out_is_the_duration():
    session, report = _apply(_intro_project(), {"op": "insert_sequence_clip", "sequence": "intro", "track": "A1", "at": 0, "id": "son"})
    assert report.ok, report.error
    clip = next(item for item in session.project.tracks[1].clips if item.id == "son")
    assert clip.source_out == 4.0


def test_a_sequence_cannot_contain_itself_directly():
    project = _intro_project()
    _session, report = _apply(project, {"op": "set_active_sequence", "sequence": "intro"}, {"op": "insert_sequence_clip", "sequence": "intro", "track": "V1", "at": 4})
    assert not report.ok and report.failed_index == 1 and "se contenir elle-même" in report.error
    assert project.active_sequence_id == MAIN_SEQUENCE_ID, "tout est restauré, l'activation comprise"


def test_an_indirect_cycle_is_refused_with_a_clear_message():
    project = _intro_project()                                             # main ⊃ intro ⊃ (rien) ; on ajoute intro ⊃ autre
    other = create_sequence(project, "Autre", sequence_id="autre")
    other.tracks[0].clips.append(Clip("ca", "m", "V1", 0.0, 0.0, 2.0))
    _nest(project, "intro", "autre", "n1")                                  # intro ⊃ autre
    _nest(project, MAIN_SEQUENCE_ID, "intro", "n2")                         # main ⊃ intro ⊃ autre
    _session, report = _apply(project, {"op": "set_active_sequence", "sequence": "autre"}, {"op": "insert_sequence_clip", "sequence": MAIN_SEQUENCE_ID, "track": "V1", "at": 5})
    assert not report.ok and "Imbrication circulaire refusée" in report.error
    assert not any(clip.sequence_id for clip in project.get_sequence("autre").tracks[0].clips)


def test_the_maximum_nesting_depth_is_enforced():
    project = _project()
    for index in range(MAX_NESTING_DEPTH + 1):                              # s0 ⊃ s1 ⊃ … ⊃ s32 : profondeur 32
        create_sequence(project, f"S{index}", sequence_id=f"s{index}")
    project.get_sequence(f"s{MAX_NESTING_DEPTH}").tracks[0].clips.append(Clip("leaf", "m", "V1", 0.0, 0.0, 2.0))
    for index in reversed(range(MAX_NESTING_DEPTH)):
        _nest(project, f"s{index}", f"s{index + 1}", f"n{index}")
    _session, report = _apply(project, {"op": "insert_sequence_clip", "sequence": "s0", "track": "V1", "at": 0})
    assert not report.ok and "trop profonde" in report.error
    assert not project.active_sequence.tracks[0].clips


def test_locked_and_wrong_kind_tracks_are_refused():
    project = _intro_project()
    project.tracks.extend([Track("L", "L", "video", locked=True), Track("G", "G", "graphics"), Track("T", "T", "subtitle")])
    for track, fragment in (("L", "verrouillée"), ("G", "vidéo ou audio"), ("T", "vidéo ou audio")):
        _session, report = _apply(project, {"op": "insert_sequence_clip", "sequence": "intro", "track": track, "at": 0})
        assert not report.ok and fragment in report.error, track
    assert not any(clip.sequence_id for track in project.tracks for clip in track.clips)


def test_unknown_sequence_and_track_are_validation_errors_not_exceptions():
    project = _intro_project()
    for op, fragment in (
        ({"op": "insert_sequence_clip", "sequence": "nope", "track": "V1", "at": 0}, "Séquence introuvable"),
        ({"op": "insert_sequence_clip", "sequence": "intro", "track": "nope", "at": 0}, "Piste introuvable"),
        ({"op": "rename_sequence", "sequence": "nope", "name": "X"}, "Séquence introuvable"),
        ({"op": "set_active_sequence", "sequence": "nope"}, "Séquence introuvable"),
        ({"op": "duplicate_sequence", "sequence": "nope"}, "Séquence introuvable"),
        ({"op": "delete_sequence", "sequence": "nope"}, "Séquence introuvable"),
    ):
        _session, report = _apply(project, op)
        assert not report.ok and fragment in report.error, op["op"]


def test_an_ambiguous_sequence_name_is_an_error():
    project = _intro_project()
    rename_sequence(project, "intro", "Doublon")
    create_sequence(project, "Autre", sequence_id="autre")
    rename_sequence(project, "autre", "Doublon")                           # l'interface le permet
    _session, report = _apply(project, {"op": "set_active_sequence", "sequence": "Doublon"})
    assert not report.ok and "ambigu" in report.error
    _session, report = _apply(project, {"op": "set_active_sequence", "sequence": "autre"})
    assert report.ok


def test_source_bounds_come_from_the_source_sequence_and_are_never_clamped_silently():
    project = _intro_project()                                              # intro dure 4 s
    for extra, fragment in (
        ({"source_out": 4.5}, "dépasse la durée"),
        ({"source_in": 4}, "vide"),
        ({"source_in": 3, "source_out": 2}, "vide"),
    ):
        _session, report = _apply(project, {"op": "insert_sequence_clip", "sequence": "intro", "track": "V1", "at": 0, **extra})
        assert not report.ok and fragment in report.error, extra
    with pytest.raises(ScriptInvalid):
        parse_script(_script({"op": "insert_sequence_clip", "sequence": "intro", "track": "V1", "at": -1}))
    with pytest.raises(ScriptInvalid):
        parse_script(_script({"op": "insert_sequence_clip", "sequence": "intro", "track": "V1", "at": 0, "source_in": -0.5}))
    _session, report = _apply(project, {"op": "create_sequence", "name": "Vide", "as": "vide"}, {"op": "insert_sequence_clip", "sequence": "vide", "track": "V1", "at": 0})
    assert not report.ok and "est vide" in report.error


# --- Session : atomicité, historique, recadrage, dry-run ---------------------------------------------------------------


def test_one_failing_verb_cancels_the_previous_ones_of_the_same_script():
    project = _project()
    before = deepcopy(project)
    session, report = _apply(
        project,
        {"op": "create_sequence", "as": "a", "name": "A"},
        {"op": "set_active_sequence", "sequence": {"ref": "a"}},
        {"op": "rename_sequence", "sequence": "a", "name": "A2"},
        {"op": "insert_sequence_clip", "sequence": "a", "track": "V1", "at": 0},        # a s'insère dans elle-même
    )
    assert not report.ok and report.failed_index == 3
    assert [s.id for s in project.sequences] == [s.id for s in before.sequences] and project.active_sequence_id == MAIN_SEQUENCE_ID
    assert not session.history.can_undo, "aucune étape d'historique pour un script refusé"


def test_a_successful_script_is_one_history_step_and_undo_follows_the_history_rule_for_the_active_sequence():
    """``ProjectHistory.undo`` garde la séquence active de l'étape annulée si elle existe encore (règle de l'interface, inchangée)."""
    project = _intro_project()
    session = Session(project)
    report = apply_script(session, parse_script(_script(
        {"op": "create_sequence", "name": "B", "as": "b"},
        {"op": "set_active_sequence", "sequence": "b"},
        {"op": "add_clip", "track": "V1", "asset": "m", "at": 0},
        {"op": "set_active_sequence", "sequence": "intro"},
        {"op": "rename_sequence", "sequence": "intro", "name": "Intro 2"},
    )))
    assert report.ok, report.error
    assert project.active_sequence_id == "intro", "un script finit où il a laissé la séquence active"
    assert session.undo() and not session.can_undo
    assert [s.id for s in project.sequences] == [MAIN_SEQUENCE_ID, "intro"]
    assert project.get_sequence("intro").name == "Intro" and project.active_sequence_id == "intro"
    assert session.redo() and project.active_sequence_id == "intro" and project.get_sequence("b") is not None


def test_undoing_a_script_that_ended_in_a_sequence_it_created_falls_back_to_the_previous_one():
    project = _project()
    session = Session(project)
    assert apply_script(session, parse_script(_script({"op": "create_sequence", "as": "n"}, {"op": "set_active_sequence", "sequence": "n"}))).ok
    assert project.active_sequence_id == "n"
    assert session.undo()
    assert project.get_sequence("n") is None and project.active_sequence_id == MAIN_SEQUENCE_ID


def test_shortening_a_nested_sequence_crops_the_nested_clip_in_the_same_step():
    project = _intro_project(intro_clips=(("c1", 0.0, 2.0), ("c2", 2.0, 2.0)))
    _nest(project, MAIN_SEQUENCE_ID, "intro", "nid")
    session = Session(project)
    report = apply_script(session, parse_script(_script(
        {"op": "set_active_sequence", "sequence": "intro"}, {"op": "delete_clip", "clip": "c2"},
    )))
    assert report.ok, report.error
    assert [(a.clip_id, a.old_source_out, a.new_source_out) for a in report.adjustments] == [("nid", 4.0, 2.0)]
    assert project.get_sequence(MAIN_SEQUENCE_ID).tracks[0].clips[0].source_out == 2.0
    assert session.undo()
    assert project.get_sequence(MAIN_SEQUENCE_ID).tracks[0].clips[0].source_out == 4.0, "recadrage annulé avec le script"


def test_dry_run_leaves_the_original_project_and_its_active_sequence_untouched():
    """Comme ``core.cli`` : ``--dry-run`` applique à une copie."""
    project = _intro_project()
    session = Session(deepcopy(project))
    report = apply_script(session, parse_script(_script({"op": "set_active_sequence", "sequence": "intro"}, {"op": "create_sequence", "name": "Z"})))
    assert report.ok and session.project.active_sequence_id == "intro"
    assert project.active_sequence_id == MAIN_SEQUENCE_ID and len(project.sequences) == 2


def test_create_matches_the_interface_function():
    project = _project()
    expected = deepcopy(project)
    create_sequence(expected, "Scène", width=1920, height=1080, fps=24.0, sequence_id="scene")
    session, report = _apply(project, {"op": "create_sequence", "name": "Scène", "width": 1920, "height": 1080, "fps": 24, "id": "scene"})
    assert report.ok, report.error
    assert session.project.get_sequence("scene") == expected.get_sequence("scene")
