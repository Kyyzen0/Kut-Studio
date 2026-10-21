"""Format déclaratif, lot 9 (ADR-0005 étape 4) : `nest_clips` (séquence à partir d'une sélection), `create_format_version`, et le plafond
de séquences et de clips d'un projet (vérifié sur TOUS les chemins qui créent une séquence).

`nest_clips` déplace des clips de la séquence ACTIVE dans une nouvelle séquence imbriquée ; `create_format_version` décline une séquence
dans un format social. Chaque test vérifie un refus (du modèle, ou du verbe quand `core/` ne contrôle pas) ou la parité avec les
fonctions que l'interface appelle.
"""

from __future__ import annotations

from copy import deepcopy

import pytest

from core.format_versions import create_format_version
from core.graphics import update_graphic
from core.mograph_layers import add_layer, set_layer_locked, set_parent
from core.multicam_model import SyncMethod
from core.multicam_ops import AngleSpec, create_multicam_source
from core.project_model import MAIN_SEQUENCE_ID, Clip, MediaAsset, Project, Track
from core.scripting import (
    ScriptInvalid,
    Session,
    apply_script,
    created_ids,
    parse_script,
)
from core.scripting.verbs import VERBS
from core.sequences import (
    MAX_NESTING_DEPTH,
    create_sequence,
    create_sequence_from_selection,
    insert_sequence_clip,
)
from core.visual_effects import ClipTransform
from core.transitions import TransitionType, add_transition
from ui import i18n

NEW_VERBS = ("nest_clips", "create_format_version")


@pytest.fixture(autouse=True)
def _french(monkeypatch):
    monkeypatch.setenv("KUT_STUDIO_LANG", "fr")
    i18n.reset_for_tests()
    yield
    i18n.reset_for_tests()


def _project() -> Project:
    project = Project(
        "Démo",
        width=1280,
        height=720,
        fps=30.0,
        media_assets=[
            MediaAsset(
                "m",
                "/x/intro.mp4",
                "intro",
                20.0,
                1280,
                720,
                30.0,
                "video",
                has_audio=True,
            ),
            MediaAsset(
                "a", "/x/voix.wav", "voix", 20.0, 0, 0, 0.0, "audio", has_audio=True
            ),
        ],
        tracks=[
            Track("V1", "V1", "video"),
            Track("V2", "V2", "video"),
            Track("G1", "G1", "graphics"),
            Track("A1", "A1", "audio"),
        ],
    )
    project.tracks[0].clips.append(Clip("c1", "m", "V1", 0.0, 0.0, 4.0, label="un"))
    project.tracks[0].clips.append(Clip("c2", "m", "V1", 5.0, 0.0, 3.0, label="deux"))
    project.tracks[1].clips.append(Clip("c3", "m", "V2", 1.0, 0.0, 2.0, label="trois"))
    project.tracks[3].clips.append(Clip("a1", "a", "A1", 0.0, 0.0, 6.0, label="voix"))
    return project


def _script(*ops: dict) -> dict:
    return {"kut_script": 1, "ops": list(ops)}


def _apply(project: Project, *ops: dict):
    session = Session(project)
    return session, apply_script(session, parse_script(_script(*ops)))


def _ok(project: Project, *ops: dict) -> Session:
    session, report = _apply(project, *ops)
    assert report.ok, report.error
    return session


def _refused(project: Project, *ops: dict, message: str = ""):
    before = deepcopy(project)
    session, report = _apply(project, *ops)
    assert not report.ok, "le script aurait dû être refusé"
    if message:
        assert message in report.error, report.error
    assert session.project.sequences == before.sequences, (
        "un refus ne laisse rien derrière lui"
    )
    assert session.project.active_sequence_id == before.active_sequence_id
    return report


def _shape(sequence):
    """La séquence sans les identifiants que le modèle génère au hasard (clips, repères, transitions de la copie)."""
    tracks = [
        (
            t.id,
            t.type,
            t.locked,
            [
                (
                    c.asset_id,
                    c.timeline_start,
                    c.source_in,
                    c.source_out,
                    c.label,
                    c.transform,
                    c.graphic,
                )
                for c in t.clips
            ],
        )
        for t in sequence.tracks
    ]
    return (
        sequence.id,
        sequence.name,
        sequence.width,
        sequence.height,
        sequence.fps,
        sequence.format_source,
        tracks,
    )


# --- Le catalogue ------------------------------------------------------------------------------------------------------


def test_the_new_verbs_are_in_the_table_and_create_sequences():
    for name in NEW_VERBS:
        assert (
            VERBS[name].group == "sequences"
            and VERBS[name].creates == "sequence"
            and VERBS[name].doc
        ), name
    assert [p.name for p in VERBS["nest_clips"].params] == ["clips", "name", "clip_id"]
    assert VERBS["create_format_version"].params[1].choices == (
        "vertical",
        "portrait",
        "square",
        "landscape",
    )


# --- nest_clips --------------------------------------------------------------------------------------------------------


def test_nest_clips_matches_the_model_call_the_interface_makes():
    session = _ok(
        _project(),
        {
            "op": "nest_clips",
            "clips": ["c1", "c3", "a1"],
            "name": "Bloc",
            "id": "bloc",
            "clip_id": "bloc_clip",
        },
    )
    twin = _project()
    create_sequence_from_selection(
        twin, ["c1", "c3", "a1"], "Bloc", sequence_id="bloc", nested_clip_id="bloc_clip"
    )
    assert session.project.sequences == twin.sequences
    inner = session.project.get_sequence("bloc")
    assert {c.id for t in inner.tracks for c in t.clips} == {"c1", "c3", "a1"}
    nested = next(
        c for t in session.project.tracks for c in t.clips if c.id == "bloc_clip"
    )
    assert (
        nested.sequence_id == "bloc"
        and nested.timeline_start == 0.0
        and nested.duration == 6.0
    )


def test_nest_clips_end_to_end_with_aliases_and_undo():
    script = parse_script(
        _script(
            {
                "op": "nest_clips",
                "clips": ["c2"],
                "name": "Seul",
                "as": "seul",
                "clip_id": "seul_clip",
            },
            {"op": "create_sequence", "name": "Autre", "as": "autre"},
            {
                "op": "insert_sequence_clip",
                "sequence": {"ref": "seul"},
                "track": "V2",
                "at": 10,
                "as": "copie",
            },
        )
    )
    session = Session(_project())
    report = apply_script(session, script)
    assert report.ok, report.error
    assert created_ids(script, report) == [
        {"op": "0:nest_clips", "id": "seul"},
        {"op": "1:create_sequence", "id": "autre"},
        {"op": "2:insert_sequence_clip", "id": "copie"},
    ]
    assert (
        session.undo()
        and len(session.project.sequences) == 1
        and any(c.id == "c2" for c in session.project.tracks[0].clips)
    )


def test_nest_clips_default_name_is_checked_like_a_written_one():
    session = _ok(_project(), {"op": "nest_clips", "clips": ["c1"]})
    assert any(s.name == "Séquence imbriquée" for s in session.project.sequences)
    _refused(
        session.project, {"op": "nest_clips", "clips": ["c2"]}, message="s'appelle déjà"
    )  # le modèle numéroterait « 2 »
    _refused(
        _project(),
        {"op": "nest_clips", "clips": ["c2"], "name": "Séquence principale"},
        message="s'appelle déjà",
    )


def test_nest_clips_refuses_locked_tracks_locked_layers_duplicates_and_foreign_clips():
    project = _project()
    project.tracks[1].locked = True
    _refused(
        project,
        {"op": "nest_clips", "clips": ["c1", "c3"], "name": "X"},
        message="verrouillée",
    )
    project = _project()
    layer = add_layer(project, "text", at=0.0, duration=2.0, clip_id="titre")
    set_layer_locked(project, layer.id, True)
    _refused(
        project,
        {"op": "nest_clips", "clips": ["titre"], "name": "X"},
        message="verrouillé",
    )
    _refused(
        _project(),
        {"op": "nest_clips", "clips": ["c1", "c1"], "name": "X"},
        message="plusieurs fois",
    )
    other = _project()
    create_sequence(other, "Autre", sequence_id="autre").tracks[0].clips.append(
        Clip("zz", "m", "V1", 0.0, 0.0, 2.0, label="zz")
    )
    _refused(
        other, {"op": "nest_clips", "clips": ["zz"], "name": "X"}, message="introuvable"
    )  # un clip d'une autre séquence
    with pytest.raises(ScriptInvalid):
        parse_script(_script({"op": "nest_clips", "clips": [], "name": "X"}))
    with pytest.raises(ScriptInvalid):
        parse_script(
            _script(
                {
                    "op": "nest_clips",
                    "clips": [f"c{i}" for i in range(201)],
                    "name": "X",
                }
            )
        )


def test_nest_clips_refuses_a_transition_to_a_clip_left_outside_and_moves_a_whole_one():
    project = _project()
    project.tracks[0].clips[1].timeline_start = 4.0  # c1 [0,4] et c2 [4,7] bord à bord
    add_transition(
        project, "c1", "c2", TransitionType.CROSSFADE, 0.5, transition_id="t1"
    )
    _refused(
        project,
        {"op": "nest_clips", "clips": ["c1"], "name": "X"},
        message="relie un clip choisi à un clip resté dehors",
    )
    session = _ok(
        project, {"op": "nest_clips", "clips": ["c1", "c2"], "name": "X", "id": "x"}
    )
    assert [t.id for t in session.project.get_sequence("x").transitions] == [
        "t1"
    ] and session.project.transitions == []


def test_nest_clips_refuses_a_selection_that_splits_a_layer_from_its_parent():
    project = _project()
    add_layer(project, "text", at=0.0, duration=2.0, clip_id="parent")
    add_layer(project, "rectangle", at=0.0, duration=2.0, clip_id="enfant")
    set_parent(project, "enfant", "parent")
    _refused(
        project,
        {"op": "nest_clips", "clips": ["enfant"], "name": "X"},
        message="sépare des clips liés",
    )
    session = _ok(
        project,
        {"op": "nest_clips", "clips": ["enfant", "parent"], "name": "X", "id": "x"},
    )
    assert {
        c.id for t in session.project.get_sequence("x").tracks for c in t.clips
    } == {"parent", "enfant"}


def test_nest_clips_graphic_layers_only_use_the_first_free_video_track_as_host():
    project = _project()
    add_layer(project, "text", at=2.0, duration=2.0, clip_id="titre")
    twin = deepcopy(project)
    session = _ok(
        project,
        {
            "op": "nest_clips",
            "clips": ["titre"],
            "name": "Titre",
            "id": "t",
            "clip_id": "hote",
        },
    )
    create_sequence_from_selection(
        twin, ["titre"], "Titre", sequence_id="t", nested_clip_id="hote"
    )
    assert session.project.sequences == twin.sequences
    assert any(c.id == "hote" for c in session.project.tracks[0].clips)


def test_nest_clips_depth_is_computed_on_the_resulting_graph():
    project = _project()
    names = [f"p{i}" for i in range(1, MAX_NESTING_DEPTH)]
    for name in names:
        create_sequence(project, name, sequence_id=name)
    child = MAIN_SEQUENCE_ID
    for name in names:  # main < p1 < p2 < … : 31 niveaux au-dessus de main
        track_id = project.get_sequence(name).tracks[0].id
        insert_sequence_clip(project, child, track_id, 0.0, parent_sequence_id=name)
        child = name
    # Une sélection sans clip imbriqué : 31 + 1 + 0 = 32 niveaux, permis. Avec un clip imbriqué (un niveau de plus) : refusé.
    _ok(deepcopy(project), {"op": "nest_clips", "clips": ["c1"], "name": "X"})
    leaf = create_sequence(project, "feuille", sequence_id="feuille")
    leaf.tracks[0].clips.append(
        Clip("f1", "m", leaf.tracks[0].id, 0.0, 0.0, 2.0, label="f1")
    )
    project.tracks[0].clips.append(
        Clip("n1", "", "V1", 10.0, 0.0, 2.0, label="n1", sequence_id="feuille")
    )
    _refused(
        project,
        {"op": "nest_clips", "clips": ["n1"], "name": "X"},
        message="trop profonde",
    )


def test_nest_clips_refuses_when_the_active_sequence_is_a_multicam_source():
    project = _project()
    create_multicam_source(
        project,
        [
            AngleSpec(asset_id="m", sync_method=SyncMethod.MANUAL),
            AngleSpec(asset_id="a"),
        ],
        name="Show",
        sequence_id="show",
    )
    project.active_sequence_id = "show"
    clip_id = project.tracks[0].clips[0].id
    _refused(
        project,
        {"op": "nest_clips", "clips": [clip_id], "name": "X"},
        message="source Multicam",
    )


def test_a_locked_clip_left_outside_that_the_model_would_touch_makes_nest_clips_fail(
    monkeypatch,
):
    """Le modèle retire des liaisons de suivi de clips restés dehors (`_prune_crossing_sources`) : un clip verrouillé n'est jamais modifié."""
    import core.scripting.sequence_verbs as module

    real = module.create_sequence_from_selection

    def touching(project, ids, name, **kwargs):
        result = real(project, ids, name, **kwargs)
        project.tracks[3].clips[
            0
        ].gain_db = -3.0  # un clip resté dehors, sur une piste verrouillée
        return result

    monkeypatch.setattr(module, "create_sequence_from_selection", touching)
    project = _project()
    project.tracks[3].locked = True
    _refused(
        project,
        {"op": "nest_clips", "clips": ["c1"], "name": "X"},
        message="verrouillé",
    )
    project.tracks[3].locked = False
    _ok(
        project, {"op": "nest_clips", "clips": ["c1"], "name": "X"}
    )  # déverrouillé : permis


def test_nest_clips_bounds_the_project_before_creating(monkeypatch):
    monkeypatch.setattr("core.scripting.sequence_rules.MAX_SEQUENCES_IN_PROJECT", 1)
    _refused(
        _project(), {"op": "nest_clips", "clips": ["c1"], "name": "X"}, message="limite"
    )


def test_nest_clips_atomic_when_a_later_operation_fails():
    _refused(
        _project(),
        {"op": "nest_clips", "clips": ["c1"], "name": "X", "id": "x"},
        {"op": "delete_clip", "clip": "inconnu"},
    )


# --- create_format_version ---------------------------------------------------------------------------------------------


def _social_project() -> Project:
    """Une séquence 16:9 avec un plan plein cadre, un plan animé, un titre tourné, un fond et une piste verrouillée."""
    project = _project()
    project.tracks[0].clips[0].transform = ClipTransform(scale=1.0)
    project.tracks[1].locked = True
    title = add_layer(project, "text", at=0.0, duration=3.0, clip_id="titre")
    update_graphic(title, "text", "Bonjour")
    add_layer(project, "solid", at=0.0, duration=6.0, clip_id="fond")
    return project


def test_create_format_version_matches_the_model_call_the_interface_makes():
    session = _ok(
        _social_project(),
        {
            "op": "create_format_version",
            "sequence": MAIN_SEQUENCE_ID,
            "format": "vertical",
            "id": "v916",
        },
    )
    twin = _social_project()
    version, _changes = create_format_version(
        twin, MAIN_SEQUENCE_ID, "vertical", version_id="v916"
    )
    made = session.project.get_sequence("v916")
    assert _shape(made) == _shape(version)
    assert (made.width, made.height) == (
        1080,
        1920,
    ) and made.format_source == MAIN_SEQUENCE_ID
    assert made.tracks[0].clips[0].transform.fill, (
        "un plan pleine image est rempli, comme dans l'interface"
    )
    assert made.tracks[1].locked, (
        "la piste verrouillée de la source reste verrouillée dans la copie"
    )
    source = session.project.get_sequence(MAIN_SEQUENCE_ID)
    assert _shape(source) == _shape(twin.get_sequence(MAIN_SEQUENCE_ID)), (
        "la source n'est pas touchée"
    )
    assert session.project.active_sequence_id == MAIN_SEQUENCE_ID


def test_create_format_version_works_from_a_sequence_that_is_not_active_and_with_a_name():
    project = _social_project()
    create_sequence(project, "Autre", sequence_id="autre")
    project.active_sequence_id = "autre"
    session = _ok(
        project,
        {
            "op": "create_format_version",
            "sequence": MAIN_SEQUENCE_ID,
            "format": "square",
            "name": "Carré",
            "as": "carre",
        },
    )
    version = next(s for s in session.project.sequences if s.name == "Carré")
    assert (version.width, version.height) == (
        1080,
        1080,
    ) and version.format_source == MAIN_SEQUENCE_ID
    assert session.project.active_sequence_id == "autre"


def test_create_format_version_never_redoes_an_existing_version_or_versions_a_version():
    session = _ok(
        _social_project(),
        {
            "op": "create_format_version",
            "sequence": MAIN_SEQUENCE_ID,
            "format": "vertical",
            "id": "v916",
        },
    )
    twin = deepcopy(session.project)
    _refused(
        session.project,
        {
            "op": "create_format_version",
            "sequence": MAIN_SEQUENCE_ID,
            "format": "vertical",
        },
        message="a déjà une version",
    )
    _refused(
        session.project,
        {"op": "create_format_version", "sequence": "v916", "format": "square"},
        message="version de format",
    )
    create_sequence(
        session.project, "Déjà vertical", width=1080, height=1920, sequence_id="vert"
    )
    twin = deepcopy(session.project)
    _refused(
        session.project,
        {"op": "create_format_version", "sequence": "vert", "format": "vertical"},
        message="déjà au format",
    )
    assert session.project.sequences == twin.sequences
    _ok(
        session.project,
        {"op": "delete_sequence", "sequence": "v916"},
        {
            "op": "create_format_version",
            "sequence": MAIN_SEQUENCE_ID,
            "format": "vertical",
            "id": "v2",
        },
    )


def test_create_format_version_refuses_a_taken_name_default_included_and_a_taken_id():
    project = _social_project()
    create_sequence(project, "Séquence principale · 9:16", sequence_id="pris")
    _refused(
        project,
        {
            "op": "create_format_version",
            "sequence": MAIN_SEQUENCE_ID,
            "format": "vertical",
        },
        message="s'appelle déjà",
    )
    _refused(
        project,
        {
            "op": "create_format_version",
            "sequence": MAIN_SEQUENCE_ID,
            "format": "square",
            "name": "Séquence principale · 9:16",
        },
        message="s'appelle déjà",
    )
    _refused(
        project,
        {
            "op": "create_format_version",
            "sequence": MAIN_SEQUENCE_ID,
            "format": "square",
            "id": "pris",
        },
        message="existe déjà",
    )
    with pytest.raises(ScriptInvalid):
        parse_script(
            _script(
                {
                    "op": "create_format_version",
                    "sequence": MAIN_SEQUENCE_ID,
                    "format": "carré",
                }
            )
        )


def test_create_format_version_bounds_sequences_and_clips_before_copying(monkeypatch):
    monkeypatch.setattr("core.scripting.sequence_rules.MAX_SEQUENCES_IN_PROJECT", 1)
    _refused(
        _social_project(),
        {
            "op": "create_format_version",
            "sequence": MAIN_SEQUENCE_ID,
            "format": "vertical",
        },
        message="limite",
    )
    monkeypatch.undo()
    monkeypatch.setattr(
        "core.scripting.resource_limits.MAX_CLIPS_IN_PROJECT", 7
    )  # 7 clips déjà là : la copie en ajouterait 7
    _refused(
        _social_project(),
        {
            "op": "create_format_version",
            "sequence": MAIN_SEQUENCE_ID,
            "format": "vertical",
        },
        message="limite",
    )


# --- Le plafond vaut pour tous les chemins qui créent une séquence -----------------------------------------------------


def test_the_sequence_bound_applies_to_every_creating_verb(monkeypatch):
    monkeypatch.setattr("core.scripting.sequence_rules.MAX_SEQUENCES_IN_PROJECT", 1)
    for op in (
        {"op": "create_sequence", "name": "X"},
        {"op": "duplicate_sequence", "sequence": MAIN_SEQUENCE_ID},
        {"op": "create_multicam_source", "angles": [{"asset": "m"}]},
        {"op": "nest_clips", "clips": ["c1"], "name": "X"},
    ):
        _refused(_project(), op, message="limite")


def test_the_clip_bound_counts_every_sequence_for_duplicate_sequence(monkeypatch):
    project = _project()
    other = create_sequence(project, "Autre", sequence_id="autre")
    other.tracks[0].clips.append(
        Clip("z", "m", other.tracks[0].id, 0.0, 0.0, 2.0, label="z")
    )
    monkeypatch.setattr(
        "core.scripting.resource_limits.MAX_CLIPS_IN_PROJECT", 6
    )  # 5 clips en tout (4 + 1 dans l'autre séquence)
    _refused(
        project,
        {"op": "duplicate_sequence", "sequence": MAIN_SEQUENCE_ID},
        message="dépasseraient",
    )
    _ok(project, {"op": "duplicate_sequence", "sequence": "autre"})
