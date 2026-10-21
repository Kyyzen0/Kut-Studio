"""Format déclaratif : montage sur plusieurs clips (lot 10 de l'extension 0.8.0) : `delete_clips`, `move_clips`.

Chaque verbe appelle la fonction que la sélection de la timeline appelle (``core.timeline_editing``) : le test de parité fait le même appel
directement sur une copie. Tout est vérifié avant la première modification : un clip cité, un clip que le ripple ferait reculer ou un tiers
touché (enfant d'un calque, liaison de suivi, y compris sur une autre séquence) dont la piste ou le calque est verrouillé fait tout refuser.
"""

from __future__ import annotations

import copy
from dataclasses import replace

import pytest

from core.graphics import GraphicOverlay, GraphicType, add_graphic_clip
from core.project_model import Clip, MediaAsset, Project, Sequence, Track
from core.scripting import ScriptInvalid, Session, apply_script, parse_script
from core.scripting.verbs import VERBS
from core.timeline_editing import ClipPlacement, delete_clips, move_clips
from core.timeline_operations import find_clip
from core.tracking_model import ClipTracking, TrackLink

NEW_VERBS = ("delete_clips", "move_clips")


def _project() -> Project:
    project = Project(
        "Démo",
        width=320,
        height=180,
        fps=30.0,
        media_assets=[
            MediaAsset(
                "m", "/x/a.mp4", "a", 20.0, 320, 180, 30.0, "video", has_audio=True
            ),
            MediaAsset("v", "/x/v.wav", "v", 20.0, 0, 0, 0.0, "audio", has_audio=True),
        ],
        tracks=[
            Track(
                "V1",
                "V1",
                "video",
                clips=[
                    Clip("a", "m", "V1", 0.0, 0.0, 3.0),
                    Clip("b", "m", "V1", 3.0, 0.0, 3.0),
                    Clip("c", "m", "V1", 6.0, 0.0, 3.0),
                ],
            ),
            Track(
                "V2",
                "V2",
                "video",
                clips=[
                    Clip("d", "m", "V2", 1.0, 0.0, 1.0),
                    Clip("e", "m", "V2", 7.0, 0.0, 1.0),
                ],
            ),
            Track("V3", "V3", "video"),
            Track("A1", "A1", "audio", clips=[Clip("au", "v", "A1", 4.0, 0.0, 2.0)]),
        ],
    )
    return project


def _op(verb: str, /, **fields) -> dict:
    return {"op": verb, **fields}


def _apply(project: Project, *ops: dict):
    session = Session(project)
    return session, apply_script(
        session, parse_script({"kut_script": 1, "ops": list(ops)})
    )


def _state(project: Project):
    return (copy.deepcopy(project.sequences), copy.deepcopy(project.media_assets))


def _refused(project: Project, *ops: dict):
    before = _state(project)
    session, report = _apply(project, *ops)
    assert not report.ok
    assert _state(session.project) == before and not session.can_undo
    return report


def _starts(project: Project) -> dict[str, float]:
    return {
        clip.id: clip.timeline_start for track in project.tracks for clip in track.clips
    }


# --- Forme -----------------------------------------------------------------------------------------------------------------


def test_the_verbs_are_in_the_table_with_the_right_shape():
    assert set(NEW_VERBS) <= set(VERBS)
    assert all(VERBS[name].creates is None and VERBS[name].doc for name in NEW_VERBS)


def test_the_json_schema_describes_both_verbs():
    from core.scripting.schema import json_schema

    titles = {
        item["title"] for item in json_schema()["properties"]["ops"]["items"]["oneOf"]
    }
    assert set(NEW_VERBS) <= titles


# --- delete_clips : parité -------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("ripple", [False, True])
def test_delete_clips_matches_the_model(ripple):
    scripted, reference = _project(), _project()
    _s, report = _apply(
        scripted, _op("delete_clips", clips=["a", "e", "au"], ripple=ripple)
    )
    assert report.ok, report.error
    delete_clips(reference, ["a", "e", "au"], ripple=ripple)
    assert scripted.sequences == reference.sequences
    assert {"a", "e", "au"}.isdisjoint(_starts(scripted))


def test_ripple_closes_the_gaps_on_every_free_track_but_not_on_a_locked_one():
    project = _project()
    project.tracks[2].clips.append(Clip("z", "m", "V3", 5.0, 0.0, 1.0))
    project.tracks[2].locked = True
    _s, report = _apply(project, _op("delete_clips", clips=["a"], ripple=True))
    assert report.ok, report.error
    starts = _starts(project)
    assert (
        starts["b"] == 0.0
        and starts["c"] == 3.0
        and starts["z"] == 5.0
        and starts["au"] == 1.0
    )


def test_the_default_is_not_ripple_and_undo_restores_everything_in_one_step():
    session, report = _apply(_project(), _op("delete_clips", clips=["a", "b"]))
    assert report.ok and _starts(session.project)["c"] == 6.0
    assert session.undo() and {"a", "b"} <= set(_starts(session.project))


def test_delete_a_clip_that_is_the_whole_track_and_a_clip_already_at_the_start():
    project = _project()
    _s, report = _apply(
        project,
        _op("delete_clips", clips=["au"]),
        _op("delete_clips", clips=["a"], ripple=True),
    )
    assert report.ok and not project.tracks[3].clips and _starts(project)["b"] == 0.0


def test_an_animated_clip_is_deleted_like_any_other():
    from core.keyframe_editing import add_keyframe

    project = _project()
    add_keyframe(project, "b", "scale", 0.0, 1.0)
    add_keyframe(project, "b", "scale", 1.0, 1.4)
    assert _apply(project, _op("delete_clips", clips=["b", "c"]))[1].ok


# --- delete_clips : refus atomiques ----------------------------------------------------------------------------------------


def test_a_locked_track_among_the_cited_clips_refuses_everything():
    project = _project()
    project.tracks[1].locked = True
    report = _refused(project, _op("delete_clips", clips=["a", "d"]))
    assert "verrouillée" in report.error


def test_a_locked_layer_among_the_cited_clips_refuses_everything():
    project = _project()
    find_clip(project, "b").graphic = GraphicOverlay(
        type=GraphicType.TEXT, locked=True
    )  # photo de template verrouillée
    assert (
        "verrouillé" in _refused(project, _op("delete_clips", clips=["a", "b"])).error
    )


def test_ripple_refuses_when_a_moved_third_party_is_a_locked_layer():
    project = _project()
    find_clip(project, "e").graphic = GraphicOverlay(
        type=GraphicType.TEXT, locked=True
    )  # sur V2, après le clip supprimé
    report = _refused(project, _op("delete_clips", clips=["a"], ripple=True))
    assert "verrouillé" in report.error
    assert _apply(project, _op("delete_clips", clips=["a"]))[1].ok, (
        "sans ripple, e ne bouge pas"
    )


def test_ripple_refuses_when_a_later_cited_clip_has_a_locked_layer_behind_it():
    project = _project()
    find_clip(project, "au").graphic = GraphicOverlay(
        type=GraphicType.TEXT, locked=True
    )
    assert (
        "verrouillé"
        in _refused(project, _op("delete_clips", clips=["a", "d"], ripple=True)).error
    )


def test_graphic_layers_are_refused_in_favour_of_delete_layers():
    project = _project()
    add_graphic_clip(
        project, GraphicType.TEXT, timeline_start=0.0, duration=2.0, clip_id="g"
    )
    report = _refused(project, _op("delete_clips", clips=["a", "g"]))
    assert "delete_layers" in report.error


def test_deleting_a_parent_refuses_when_a_child_layer_is_locked_and_detaches_free_children():
    def seeded(locked: bool) -> Project:
        project = _project()
        child = add_graphic_clip(
            project, GraphicType.TEXT, timeline_start=0.0, duration=2.0, clip_id="g"
        )
        child.graphic = replace(child.graphic, parent_id="a", locked=locked)
        return project

    assert (
        "verrouillé" in _refused(seeded(True), _op("delete_clips", clips=["a"])).error
    )
    free = seeded(False)
    assert _apply(free, _op("delete_clips", clips=["a"]))[1].ok
    assert find_clip(free, "g").graphic.parent_id == "", (
        "comme le modèle : l'enfant devient autonome"
    )


def test_deleting_a_tracking_source_refuses_when_a_follower_is_locked_even_in_another_sequence():
    def seeded() -> Project:
        project = _project()
        other = Sequence(
            "seq-b",
            "B",
            width=320,
            height=180,
            fps=30.0,
            tracks=[
                Track(
                    "V1",
                    "V1",
                    "video",
                    locked=True,
                    clips=[Clip("f", "m", "V1", 0.0, 0.0, 1.0)],
                )
            ],
        )
        other.tracks[0].clips[0].tracking = ClipTracking(
            links=(
                TrackLink(
                    source_clip_id="a", tracker_ids=("t1",), continuation_ids=("b",)
                ),
            )
        )
        project.sequences.append(other)
        return project

    assert "verrouillé" in _refused(seeded(), _op("delete_clips", clips=["a"])).error
    free = seeded()
    free.sequences[1].tracks[0].locked = False
    assert _apply(free, _op("delete_clips", clips=["a"]))[1].ok


def test_the_model_would_skip_what_the_script_refuses():
    """`delete_clips` du modèle saute en silence un clip introuvable ; un script le reçoit refusé, rien n'est supprimé."""
    reference = _project()
    delete_clips(reference, ["a", "nope"])
    assert "a" not in _starts(reference)
    assert (
        "introuvable"
        in _refused(_project(), _op("delete_clips", clips=["a", "nope"])).error
    )


def test_duplicates_and_bounds_of_the_list():
    report = _refused(_project(), _op("delete_clips", clips=["a", "a"]))
    assert "plusieurs fois" in report.error
    for clips in (
        [],
        ["a"] * 201,
        "a",
        [3],
        [None],
        [""],
        [{"ref": "x"}],
        ["x" * 5_000_000],
    ):
        with pytest.raises(ScriptInvalid) as caught:
            parse_script({"kut_script": 1, "ops": [_op("delete_clips", clips=clips)]})
        assert len(str(caught.value)) < 5_000
    with pytest.raises(ScriptInvalid):
        parse_script(
            {"kut_script": 1, "ops": [_op("delete_clips", clips=["a"], ripple="oui")]}
        )
    parse_script(
        {
            "kut_script": 1,
            "ops": [_op("delete_clips", clips=[f"x{index}" for index in range(200)])],
        }
    )


def test_two_hundred_clips_are_deleted_in_one_step():
    project = Project(
        "P",
        width=320,
        height=180,
        fps=30.0,
        media_assets=[MediaAsset("m", "/x/a.mp4", "a", 20.0, 320, 180, 30.0, "video")],
        tracks=[
            Track(
                "V1",
                "V1",
                "video",
                clips=[
                    Clip(f"k{i}", "m", "V1", float(i), 0.0, 1.0) for i in range(200)
                ],
            )
        ],
    )
    session, report = _apply(
        project, _op("delete_clips", clips=[f"k{i}" for i in range(200)], ripple=True)
    )
    assert (
        report.ok
        and not project.tracks[0].clips
        and session.undo()
        and len(project.tracks[0].clips) == 200
    )


# --- move_clips ------------------------------------------------------------------------------------------------------------


def _move(*items: tuple[str, str, float]) -> dict:
    return _op(
        "move_clips",
        placements=[
            {"clip": clip, "track": track, "at": at} for clip, track, at in items
        ],
    )


def test_move_clips_matches_the_model():
    scripted, reference = _project(), _project()
    _s, report = _apply(
        scripted, _move(("a", "V3", 10.0), ("d", "V2", 12.0), ("au", "A1", 0.5))
    )
    assert report.ok, report.error
    move_clips(
        reference,
        [
            ClipPlacement("a", 10.0, "V3"),
            ClipPlacement("d", 12.0, "V2"),
            ClipPlacement("au", 0.5, "A1"),
        ],
    )
    assert scripted.sequences == reference.sequences
    assert find_clip(scripted, "a").track_id == "V3"


def test_move_clips_swaps_in_one_step_and_undoes_in_one_step():
    session, report = _apply(_project(), _move(("a", "V1", 6.0), ("c", "V1", 0.0)))
    assert (
        report.ok
        and _starts(session.project)["a"] == 6.0
        and _starts(session.project)["c"] == 0.0
    )
    assert session.undo() and _starts(session.project)["a"] == 0.0


def test_move_clips_accepts_a_track_by_name_and_by_alias_end_to_end():
    session, report = _apply(
        _project(),
        _op("add_track", type="video", name="Haut", **{"as": "haut"}),
        _op(
            "move_clips",
            placements=[
                {"clip": "a", "track": {"ref": "haut"}, "at": 1.0},
                {"clip": "b", "track": "Haut", "at": 5.0},
            ],
        ),
    )
    assert report.ok, report.error
    assert (
        find_clip(session.project, "a").track_id == "haut"
        and find_clip(session.project, "b").track_id == "haut"
    )


def test_move_clips_refuses_without_writing_anything():
    project = _project()
    project.tracks[2].locked = True
    cases = [
        (_move(("a", "V3", 1.0)), "verrouillée"),  # destination verrouillée
        (_move(("a", "A1", 1.0)), ""),  # genre incompatible (modèle)
        (_move(("a", "V3x", 1.0)), "introuvable"),
        (_move(("zz", "V1", 1.0)), "introuvable"),
        (_move(("a", "V1", 1.0), ("a", "V1", 2.0)), "plusieurs fois"),
    ]
    for operation, message in cases:
        report = _refused(project, _move(("b", "V1", 9.0)), operation)
        assert message in report.error, (operation, report.error)


def test_move_clips_first_valid_placement_is_not_applied_when_a_later_one_fails():
    project = _project()
    project.tracks[1].locked = True
    report = _refused(project, _move(("a", "V1", 20.0), ("d", "V2", 5.0)))
    assert "verrouillée" in report.error and _starts(project)["a"] == 0.0


def test_move_clips_refuses_a_locked_layer_and_a_locked_source_track():
    project = _project()
    find_clip(project, "b").graphic = GraphicOverlay(type=GraphicType.TEXT, locked=True)
    assert (
        "verrouillé"
        in _refused(project, _move(("a", "V1", 20.0), ("b", "V1", 30.0))).error
    )
    locked = _project()
    locked.tracks[1].locked = True
    assert "verrouillée" in _refused(locked, _move(("d", "V3", 1.0))).error


@pytest.mark.parametrize(
    "placements",
    [
        [],
        [{"clip": "a", "track": "V1", "at": -1.0}],
        [{"clip": "a", "track": "V1", "at": float("nan")}],
        [{"clip": "a", "track": "V1", "at": float("inf")}],
        [{"clip": "a", "track": "V1", "at": 2e6}],
        [{"clip": "a", "track": "V1"}],
        [{"clip": "a", "at": 1.0}],
        [{"track": "V1", "at": 1.0}],
        [{"clip": "a", "track": "V1", "at": 1.0, "extra": 1}],
        ["a"],
        [3],
        "a",
        [{"clip": "a", "track": "V1", "at": 1.0}] * 201,
        [{"clip": "x" * 5_000_000, "track": "V1", "at": 1.0}],
    ],
)
def test_move_clips_hostile_lists_are_refused_at_reading(placements):
    with pytest.raises(ScriptInvalid) as caught:
        parse_script(
            {"kut_script": 1, "ops": [_op("move_clips", placements=placements)]}
        )
    assert len(str(caught.value)) < 5_000


def test_two_hundred_placements_are_read_and_applied_atomically():
    project = Project(
        "P",
        width=320,
        height=180,
        fps=30.0,
        media_assets=[MediaAsset("m", "/x/a.mp4", "a", 20.0, 320, 180, 30.0, "video")],
        tracks=[
            Track(
                "V1",
                "V1",
                "video",
                clips=[
                    Clip(f"k{i}", "m", "V1", float(i), 0.0, 1.0) for i in range(200)
                ],
            )
        ],
    )
    session, report = _apply(
        project, _move(*[(f"k{i}", "V1", float(i) + 1000.0) for i in range(200)])
    )
    assert (
        report.ok
        and min(_starts(project).values()) == 1000.0
        and session.undo()
        and _starts(project)["k0"] == 0.0
    )


def test_the_checks_come_before_the_first_modification_even_without_the_session_rollback():
    """Le verbe appelé seul (sans la restauration de la session) ne laisse rien derrière lui : le refus précède toute écriture."""
    project = _project()
    find_clip(project, "e").graphic = GraphicOverlay(
        type=GraphicType.TEXT, locked=True
    )  # calque verrouillé que le ripple ferait reculer
    before = _state(project)
    operation = VERBS["delete_clips"].build({"clips": ["a"], "ripple": True})
    with pytest.raises(ValueError, match="verrouillé"):
        operation(project)
    assert _state(project) == before
    locked = _project()
    locked.tracks[1].locked = True
    before = _state(locked)
    with pytest.raises(ValueError, match="verrouillée"):
        VERBS["delete_clips"].build({"clips": ["a", "d"]})(locked)
    with pytest.raises(ValueError, match="verrouillée"):
        VERBS["move_clips"].build(
            {
                "placements": [
                    {"clip": "a", "track": "V1", "at": 9.0},
                    {"clip": "d", "track": "V2", "at": 9.0},
                ]
            }
        )(locked)
    assert _state(locked) == before


def test_locked_dependants_are_detected_before_the_deletion_even_without_the_session():
    """Parent, groupe, source de suivi d'un clip verrouillé (même sur une autre séquence) : refus AVANT la suppression réelle."""

    def child(locked_track: bool, locked_layer: bool, key: str) -> Project:
        project = _project()
        layer = add_graphic_clip(
            project, GraphicType.TEXT, timeline_start=0.0, duration=2.0, clip_id="g"
        )
        layer.graphic = replace(layer.graphic, locked=locked_layer, **{key: "a"})
        next(t for t in project.tracks if t.type == "graphics").locked = locked_track
        return project

    for project in (
        child(False, True, "parent_id"),
        child(False, True, "group_id"),
        child(True, False, "parent_id"),
    ):
        before = _state(project)
        with pytest.raises(ValueError, match="dépend"):
            VERBS["delete_clips"].build({"clips": ["a", "b"]})(project)
        assert _state(project) == before, "la source n'est pas supprimée"

    other = _project()
    seq = Sequence(
        "seq-b",
        "B",
        width=320,
        height=180,
        fps=30.0,
        tracks=[
            Track(
                "V1",
                "V1",
                "video",
                locked=True,
                clips=[Clip("f", "m", "V1", 0.0, 0.0, 1.0)],
            )
        ],
    )
    seq.tracks[0].clips[0].tracking = ClipTracking(
        links=(
            TrackLink(source_clip_id="a", tracker_ids=("t1",), continuation_ids=("b",)),
        )
    )
    other.sequences.append(seq)
    before = _state(other)
    with pytest.raises(ValueError, match="dépend"):
        VERBS["delete_clips"].build({"clips": ["a"]})(other)
    assert _state(other) == before
