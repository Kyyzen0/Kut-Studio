"""Format déclaratif : montage et pistes (lot 1 de l'extension 0.8.0) : `trim_clip_start`, `cut_clip`, `slip_clip`, `move_track`…

Chaque verbe appelle la fonction du modèle que l'interface appelle : le test de parité fait le même appel directement sur une copie et
compare les projets. Les refus (verrous, bornes que le modèle ramènerait) sont ceux d'un script, sans écriture partielle.
"""

from __future__ import annotations

import copy
import math
from dataclasses import replace

import pytest

from core.project_model import Clip, MediaAsset, Project, Track
from core.scripting import (
    ScriptInvalid,
    Session,
    apply_script,
    created_ids,
    parse_script,
)
from core.scripting.verbs import VERBS
from core.timeline_editing import (
    relocate_clip,
    ripple_trim_left,
    roll_edit,
    set_cover_marker,
    slide_clip,
    slip_clip,
)
from core.timeline_operations import (
    cut_clip,
    duplicate_clip,
    find_clip,
    ripple_delete_clip,
    trim_clip_left,
    trim_clip_right,
)
from core.track_operations import move_track, remove_track, rename_track
from core.visual_effects import TransformKeyframe
from ui import i18n

NEW_VERBS = (
    "trim_clip_start",
    "trim_clip_end",
    "ripple_trim_clip_start",
    "cut_clip",
    "duplicate_clip",
    "ripple_delete_clip",
    "slip_clip",
    "slide_clip",
    "roll_edit",
    "move_clip_to_track",
    "remove_track",
    "rename_track",
    "move_track",
    "set_track_volume",
    "set_track_pan",
    "set_cover_marker",
    "remove_marker",
)


@pytest.fixture(autouse=True)
def _french(monkeypatch):
    monkeypatch.setenv("KUT_STUDIO_LANG", "fr")
    i18n.reset_for_tests()
    yield
    i18n.reset_for_tests()


def _project(*, locked_v1: bool = False) -> Project:
    return Project(
        "Démo",
        width=320,
        height=180,
        fps=30.0,
        media_assets=[
            MediaAsset(
                "m",
                "/x/intro.mp4",
                "intro",
                10.0,
                320,
                180,
                30.0,
                "video",
                has_audio=True,
            ),
            MediaAsset(
                "n", "/x/b.mp4", "b", 10.0, 320, 180, 30.0, "video", has_audio=True
            ),
        ],
        tracks=[
            Track(
                "V1",
                "V1",
                "video",
                locked=locked_v1,
                clips=[
                    Clip("a", "m", "V1", 0.0, 0.0, 5.0),
                    Clip("b", "m", "V1", 5.0, 2.0, 5.0),
                ],
            ),
            Track("V2", "V2", "video", clips=[Clip("c", "n", "V2", 1.0, 0.0, 2.0)]),
            Track("V3", "V3", "video", locked=True),
            Track("A1", "A1", "audio"),
            Track("A2", "A2", "audio"),
        ],
    )


def _op(verb: str, /, **fields) -> dict:
    return {"op": verb, **fields}


def _apply(project: Project, *ops: dict):
    session = Session(project)
    return session, apply_script(
        session, parse_script({"kut_script": 1, "ops": list(ops)})
    )


def _snapshot(project: Project):
    return (copy.deepcopy(project.tracks), copy.deepcopy(project.markers))


def _same(first: Project, second: Project) -> bool:
    return _snapshot(first) == _snapshot(second)


def _parity(project_factory, op: dict, direct) -> Project:
    """Applique ``op`` à un projet et ``direct`` (l'appel du modèle) à un autre : ils doivent être identiques ; rend le projet du script."""
    scripted = project_factory()
    session, report = _apply(scripted, op)
    assert report.ok, report.error
    reference = project_factory()
    direct(reference)
    assert _same(session.project, reference)
    return session.project


def _refused(project: Project, *ops: dict):
    """Le script est refusé à l'application, et le projet est strictement inchangé."""
    before = _snapshot(project)
    session, report = _apply(project, *ops)
    assert not report.ok
    assert _snapshot(session.project) == before
    return report


def test_the_verbs_are_in_the_table_with_the_right_shape():
    assert set(NEW_VERBS) <= set(VERBS)
    for name in ("cut_clip", "duplicate_clip", "set_cover_marker"):
        assert VERBS[name].creates in ("clip", "marker")
        assert "id" in VERBS[name].accepts and "as" in VERBS[name].accepts
    for name in set(NEW_VERBS) - {"cut_clip", "duplicate_clip", "set_cover_marker"}:
        assert VERBS[name].creates is None and VERBS[name].doc


def test_the_json_schema_describes_every_new_verb():
    from core.scripting.schema import json_schema

    titles = {
        item["title"] for item in json_schema()["properties"]["ops"]["items"]["oneOf"]
    }
    assert set(NEW_VERBS) <= titles


# --- Rogner ------------------------------------------------------------------------------------------------------------


def test_trim_clip_start_and_end_match_the_model():
    _parity(
        _project,
        _op("trim_clip_start", clip="a", at=1.5),
        lambda p: trim_clip_left(p, "a", 1.5),
    )
    _parity(
        _project,
        _op("trim_clip_end", clip="a", at=3.0),
        lambda p: trim_clip_right(p, "a", 3.0),
    )
    _parity(
        _project,
        _op("trim_clip_end", clip="a", at=8.0),
        lambda p: trim_clip_right(p, "a", 8.0),
    )  # étendre, dans la source
    _parity(
        _project,
        _op("ripple_trim_clip_start", clip="a", at=2.0),
        lambda p: ripple_trim_left(p, "a", 2.0),
    )


def test_ripple_trim_closes_the_gap_after_the_clip():
    project = _parity(
        _project,
        _op("ripple_trim_clip_start", clip="a", at=2.0),
        lambda p: ripple_trim_left(p, "a", 2.0),
    )
    assert (
        find_clip(project, "a").timeline_start == 0.0
        and find_clip(project, "b").timeline_start == 3.0
    )


def test_trim_keeps_an_animated_clip_coherent_like_the_model():
    def animated() -> Project:
        project = _project()
        clip = find_clip(project, "a")
        clip.transform_keyframes = [
            TransformKeyframe("position_x", 0.0, 0.0),
            TransformKeyframe("position_x", 4.0, 1.0),
        ]
        return project

    _parity(
        animated,
        _op("trim_clip_start", clip="a", at=2.0),
        lambda p: trim_clip_left(p, "a", 2.0),
    )
    _parity(
        animated,
        _op("trim_clip_end", clip="a", at=3.0),
        lambda p: trim_clip_right(p, "a", 3.0),
    )


@pytest.mark.parametrize(
    "operation",
    [
        _op(
            "trim_clip_start", clip="a", at=-1
        ),  # refusé à la lecture : borne du paramètre
        _op("trim_clip_start", clip="a", at=0.5 + 5),  # au-delà de la fin du clip
        _op("trim_clip_end", clip="a", at=0.0),  # durée nulle
        _op("trim_clip_end", clip="a", at=50.0),  # dépasse le média (10 s)
        _op("trim_clip_end", clip="inconnu", at=3.0),
        _op("trim_clip_start", clip="a", at=1e12),
    ],
)
def test_trim_errors_are_clean_and_leave_nothing(operation):
    try:
        script = parse_script({"kut_script": 1, "ops": [operation]})
    except ScriptInvalid:
        return
    before = _snapshot(_project())
    session = Session(_project())
    report = apply_script(session, script)
    assert not report.ok and _snapshot(session.project) == before


def test_trim_trims_back_toward_the_start_is_refused():
    report = _refused(_project(), _op("trim_clip_start", clip="b", at=4.0))
    assert "trim" in str(report.error).lower() or "gauche" in str(report.error).lower()


# --- Couper ------------------------------------------------------------------------------------------------------------


def test_cut_clip_matches_the_model_and_creates_the_right_half():
    def direct(p):
        cut_clip(p, "a", 2.0, right_clip_id="droite")

    project = _parity(_project, _op("cut_clip", clip="a", at=2.0, id="droite"), direct)
    assert (
        find_clip(project, "a").duration == 2.0
        and find_clip(project, "droite").timeline_start == 2.0
    )
    session, report = _apply(_project(), _op("cut_clip", clip="a", at=2.0, id="droite"))
    assert created_ids(
        parse_script(
            {"kut_script": 1, "ops": [_op("cut_clip", clip="a", at=2.0, id="droite")]}
        ),
        report,
    ) == [{"op": report.results[0].name, "id": "droite"}]


def test_cut_clip_without_id_keeps_the_models_generated_name():
    session, report = _apply(_project(), _op("cut_clip", clip="a", at=2.0))
    assert report.ok, report.error
    assert report.results[0].value.id == "a-split-2"


def test_cut_then_reference_the_right_clip_by_alias_end_to_end():
    session, report = _apply(
        _project(),
        _op("cut_clip", clip="a", at=2.0, **{"as": "droite"}),
        _op("trim_clip_end", clip={"ref": "droite"}, at=4.0),
        _op("set_clip_enabled", clip={"ref": "droite"}, enabled=False),
        _op("move_clip", clip={"ref": "droite"}, at=20.0),
    )
    assert report.ok, report.error
    right = find_clip(session.project, "droite")
    assert right.timeline_start == 20.0 and not right.enabled and right.duration == 2.0
    assert find_clip(session.project, "a").duration == 2.0
    assert (
        session.undo() and find_clip(session.project, "a").duration == 5.0
    )  # une seule étape d'historique, tout revient
    with pytest.raises(KeyError):
        find_clip(session.project, "droite")


def test_cut_errors_and_atomicity():
    for position in (0.0, 5.0, 7.0):  # le bord et au-delà : strictement à l'intérieur
        _refused(_project(), _op("cut_clip", clip="a", at=position))
    _refused(
        _project(), _op("cut_clip", clip="a", at=2.0, id="b")
    )  # identifiant déjà pris
    _refused(
        _project(),
        _op("cut_clip", clip="a", at=2.0, id="x"),
        _op("cut_clip", clip="a", at=99.0),
    )  # la 2e échoue : la 1re est annulée
    _refused(_project(locked_v1=True), _op("cut_clip", clip="a", at=2.0))
    with pytest.raises(ScriptInvalid):
        parse_script(
            {"kut_script": 1, "ops": [_op("cut_clip", clip="a", at=float("nan"))]}
        )
    with pytest.raises(ScriptInvalid):
        parse_script(
            {"kut_script": 1, "ops": [_op("cut_clip", clip="a", at=float("inf"))]}
        )
    with pytest.raises(ScriptInvalid):
        parse_script(
            {
                "kut_script": 1,
                "ops": [_op("cut_clip", clip="a", at=1, id="angle-1 ; drawtext")],
            }
        )


# --- Dupliquer, ripple delete ------------------------------------------------------------------------------------------


def test_duplicate_clip_matches_the_model():
    _parity(
        _project,
        _op("duplicate_clip", clip="a", id="copie"),
        lambda p: duplicate_clip(p, "a", new_clip_id="copie"),
    )
    project = _parity(
        _project,
        _op("duplicate_clip", clip="a", at=12.0, id="copie"),
        lambda p: duplicate_clip(p, "a", 12.0, new_clip_id="copie"),
    )
    assert find_clip(project, "copie").timeline_start == 12.0
    session, report = _apply(
        _project(),
        _op("duplicate_clip", clip="a", **{"as": "copie"}),
        _op("move_clip", clip={"ref": "copie"}, at=30.0),
    )
    assert report.ok, report.error
    assert find_clip(session.project, "copie").timeline_start == 30.0


def test_duplicate_clip_errors():
    _refused(_project(), _op("duplicate_clip", clip="a", id="b"))
    _refused(_project(locked_v1=True), _op("duplicate_clip", clip="a"))
    _refused(_project(), _op("duplicate_clip", clip="nope"))
    with pytest.raises(ScriptInvalid):
        parse_script({"kut_script": 1, "ops": [_op("duplicate_clip", clip="a", at=-1)]})


def test_ripple_delete_clip_matches_the_model_and_respects_locked_tracks():
    def factory() -> Project:
        project = _project()
        project.tracks[2].clips.append(Clip("v3", "m", "V3", 6.0, 0.0, 1.0))
        return project

    project = _parity(
        factory,
        _op("ripple_delete_clip", clip="a"),
        lambda p: ripple_delete_clip(p, "a"),
    )
    assert find_clip(project, "b").timeline_start == 0.0
    assert (
        find_clip(project, "v3").timeline_start == 6.0
    )  # la piste verrouillée ne bouge pas
    _refused(_project(locked_v1=True), _op("ripple_delete_clip", clip="a"))
    _refused(_project(), _op("ripple_delete_clip", clip="nope"))


# --- Slip, slide, roll ------------------------------------------------------------------------------------------------


def test_slip_clip_matches_the_model_and_refuses_what_the_model_would_clamp():
    _parity(
        _project,
        _op("slip_clip", clip="a", delta=3.0),
        lambda p: slip_clip(p, "a", 3.0),
    )  # 5 s -> 8 s, dans les 10 s
    _parity(
        _project,
        _op("slip_clip", clip="b", delta=-2.0),
        lambda p: slip_clip(p, "b", -2.0),
    )  # source_in 2 -> 0
    _refused(
        _project(), _op("slip_clip", clip="a", delta=6.0)
    )  # 5 + 6 > 10 : le modèle l'aurait ramené à 5
    _refused(
        _project(), _op("slip_clip", clip="b", delta=-2.5)
    )  # avant le début du média
    _refused(_project(), _op("slip_clip", clip="a", delta=-0.1))
    _refused(_project(locked_v1=True), _op("slip_clip", clip="a", delta=1.0))
    with pytest.raises(ScriptInvalid):
        parse_script(
            {"kut_script": 1, "ops": [_op("slip_clip", clip="a", delta=float("nan"))]}
        )


def test_slide_clip_matches_the_model():
    def adjacent() -> Project:
        project = _project()
        find_clip(project, "b").source_in = 1.0  # de la marge de source côté droit
        return project

    _parity(
        adjacent, _op("slide_clip", clip="b", at=5.0), lambda p: slide_clip(p, "b", 5.0)
    )
    _parity(
        _project, _op("slide_clip", clip="c", at=4.0), lambda p: slide_clip(p, "c", 4.0)
    )  # sans voisin : un déplacement
    _refused(_project(locked_v1=True), _op("slide_clip", clip="a", at=1.0))
    _refused(_project(), _op("slide_clip", clip="nope", at=1.0))


def test_roll_edit_matches_the_model_in_both_directions():
    _parity(
        _project,
        _op("roll_edit", clip="a", edge="right", at=5.5),
        lambda p: roll_edit(p, "a", "right", 5.5),
    )
    _parity(
        _project,
        _op("roll_edit", clip="b", edge="left", at=4.5),
        lambda p: roll_edit(p, "b", "left", 4.5),
    )
    _parity(
        _project,
        _op("roll_edit", clip="b", edge="right", at=7.0),
        lambda p: roll_edit(p, "b", "right", 7.0),
    )  # sans voisin : un trim
    _refused(
        _project(), _op("roll_edit", clip="a", edge="right", at=50.0)
    )  # la source du voisin n'y suffit pas
    _refused(_project(locked_v1=True), _op("roll_edit", clip="a", edge="right", at=5.5))
    with pytest.raises(ScriptInvalid):
        parse_script(
            {"kut_script": 1, "ops": [_op("roll_edit", clip="a", edge="up", at=5)]}
        )


def _graphics_project(*, lock_second: bool) -> Project:
    project = _project()
    project.tracks.append(Track("G1", "G1", "graphics"))
    session, report = _apply(
        project,
        _op(
            "add_graphic",
            graphic="text",
            at=0,
            duration=3,
            id="t1",
            text="A",
            track="G1",
        ),
        _op(
            "add_graphic",
            graphic="text",
            at=3,
            duration=3,
            id="t2",
            text="B",
            track="G1",
        ),
    )
    assert report.ok, report.error
    second = find_clip(session.project, "t2")
    second.graphic = replace(second.graphic, locked=lock_second)
    return session.project


def test_a_locked_layer_blocks_the_clip_and_its_neighbours_in_slide_and_roll():
    free = _graphics_project(lock_second=False)
    _session, report = _apply(free, _op("trim_clip_end", clip="t2", at=5.0))
    assert report.ok, report.error
    for operation in (
        _op("trim_clip_end", clip="t2", at=5.0),
        _op("cut_clip", clip="t2", at=4.0),
        _op("duplicate_clip", clip="t2"),
        _op("slide_clip", clip="t1", at=1.0),
        _op("roll_edit", clip="t1", edge="right", at=3.5),
        _op("slip_clip", clip="t2", delta=0.0),
        _op("ripple_delete_clip", clip="t2"),
    ):
        report = _refused(_graphics_project(lock_second=True), operation)
        assert "verrouill" in str(
            report.error
        )  # refusé pour le verrou, pas par hasard par une borne du modèle


# --- Changer de piste --------------------------------------------------------------------------------------------------


def test_move_clip_to_track_matches_the_model():
    project = _parity(
        _project,
        _op("move_clip_to_track", clip="a", track="V2", at=6.0),
        lambda p: relocate_clip(p, "a", "V2", 6.0),
    )
    assert find_clip(project, "a").track_id == "V2"
    _parity(
        _project,
        _op("move_clip_to_track", clip="a", track="V1", at=9.0),
        lambda p: relocate_clip(p, "a", "V1", 9.0),
    )
    _parity(
        _project,
        _op("move_clip_to_track", clip="a", track="V2", at=6.0),
        lambda p: relocate_clip(p, "a", "V2", 6.0),
    )


def test_move_clip_to_track_refuses_locks_and_wrong_kinds():
    _refused(
        _project(), _op("move_clip_to_track", clip="a", track="V3", at=0.0)
    )  # destination verrouillée
    _refused(
        _project(locked_v1=True),
        _op("move_clip_to_track", clip="a", track="V2", at=0.0),
    )  # source verrouillée
    _refused(
        _project(), _op("move_clip_to_track", clip="a", track="A1", at=0.0)
    )  # piste audio : genre incompatible
    _refused(_project(), _op("move_clip_to_track", clip="a", track="nope", at=0.0))
    with pytest.raises(ScriptInvalid):
        parse_script(
            {
                "kut_script": 1,
                "ops": [_op("move_clip_to_track", clip="a", track="V2", at=-1)],
            }
        )


# --- Repères -----------------------------------------------------------------------------------------------------------


def test_set_cover_marker_replaces_the_cover_and_creates_the_marker():
    session, report = _apply(
        _project(),
        _op("add_marker", at=1.0, name="x", id="m1"),
        _op("set_cover_marker", at=2.0, name="Une", id="couv"),
        _op("set_cover_marker", at=3.0, id="autre"),
    )
    assert report.ok, report.error
    assert [(m.id, m.category) for m in session.project.markers] == [
        ("m1", "standard"),
        ("autre", "cover"),
    ]
    # Un second script peut reprendre l'identifiant de la couverture qu'il remplace.
    again = apply_script(
        session,
        parse_script(
            {"kut_script": 1, "ops": [_op("set_cover_marker", at=5.0, id="autre")]}
        ),
    )
    assert again.ok, again.error
    assert [
        (m.id, m.time_seconds) for m in session.project.markers if m.category == "cover"
    ] == [("autre", 5.0)]


def test_set_cover_marker_defaults_and_refused_id_keep_the_existing_cover():
    project = _project()
    set_cover_marker(project, 1.0, "avant")  # l'appel de l'interface, sans identifiant
    cover = [m for m in project.markers if m.category == "cover"]
    assert len(cover) == 1 and cover[0].id.startswith("marker-")
    _apply(project, _op("add_marker", at=0.0, id="pris"))
    report = _refused(project, _op("set_cover_marker", at=4.0, id="pris"))
    assert not report.ok and [
        m.name for m in project.markers if m.category == "cover"
    ] == ["avant"]
    with pytest.raises(Exception):
        set_cover_marker(
            project, 5.0, "z", marker_id="pris"
        )  # le modèle seul : refus, la couverture reste
    assert [m.name for m in project.markers if m.category == "cover"] == ["avant"]


def test_remove_marker():
    project = _project()
    _apply(project, _op("add_marker", at=1.0, id="m1"))
    session, report = _apply(project, _op("remove_marker", marker="m1"))
    assert report.ok, report.error
    assert session.project.markers == []
    _refused(project, _op("remove_marker", marker="m1"))  # il n'existe plus
    session, report = _apply(
        _project(),
        _op("add_marker", at=1.0, **{"as": "jalon"}),
        _op("remove_marker", marker={"ref": "jalon"}),
    )
    assert report.ok, report.error and session.project.markers == []


# --- Pistes ------------------------------------------------------------------------------------------------------------


def test_remove_rename_move_track_match_the_model():
    _parity(_project, _op("remove_track", track="A2"), lambda p: remove_track(p, "A2"))
    _parity(
        _project,
        _op("rename_track", track="A2", name="Musique"),
        lambda p: rename_track(p, "A2", "Musique"),
    )
    _parity(
        _project,
        _op("move_track", track="A2", index=0),
        lambda p: move_track(p, "A2", 0),
    )
    project = _parity(
        _project,
        _op("move_track", track="V1", index=4),
        lambda p: move_track(p, "V1", 4),
    )
    assert [t.id for t in project.tracks][-1] == "V1"


def test_track_errors_and_locks():
    _refused(_project(), _op("remove_track", track="V1"))  # porte des clips
    _refused(_project(), _op("remove_track", track="nope"))
    _refused(_project(), _op("rename_track", track="A2", name="A1"))  # nom déjà pris
    _refused(_project(), _op("rename_track", track="A2", name="V1"))
    _refused(
        _project(), _op("move_track", track="V3", index=0)
    )  # verrouillée : ne bouge pas
    for index in (5, 99, 10_000):  # le modèle les ramènerait au bord
        _refused(_project(), _op("move_track", track="A2", index=index))
    for bad in (-1, 10_001, 1.5, True):
        with pytest.raises(ScriptInvalid):
            parse_script(
                {"kut_script": 1, "ops": [_op("move_track", track="A2", index=bad)]}
            )
    for bad in ("", "  x", "x" * 1000):
        with pytest.raises(ScriptInvalid):
            parse_script(
                {"kut_script": 1, "ops": [_op("rename_track", track="A2", name=bad)]}
            )


def test_track_ops_are_atomic_and_undoable():
    session, report = _apply(
        _project(),
        _op("rename_track", track="A2", name="Musique"),
        _op("move_track", track="A2", index=0),
    )
    assert report.ok and session.project.tracks[0].name == "Musique"
    assert (
        session.undo()
        and [t.id for t in session.project.tracks][0] == "V1"
        and session.project.tracks[4].name == "A2"
    )
    _refused(
        _project(),
        _op("rename_track", track="A2", name="Musique"),
        _op("remove_track", track="V1"),
    )


def test_set_track_volume_and_pan():
    session, report = _apply(
        _project(),
        _op("set_track_volume", track="A1", volume_db=-6.5),
        _op("set_track_pan", track="A1", pan=-0.25),
    )
    assert report.ok, report.error
    track = session.project.tracks[3]
    assert track.volume_db == -6.5 and track.pan == -0.25
    reference = _project().tracks[3]
    reference.set_volume_db(-6.5)
    reference.set_pan(-0.25)
    assert (reference.volume_db, reference.pan) == (track.volume_db, track.pan)
    assert (
        session.undo()
        and session.project.tracks[3].volume_db == 0.0
        and session.project.tracks[3].pan == 0.0
    )


def test_track_volume_and_pan_refuse_bounds_wrong_kind_and_locks():
    for name, field, bad in (
        ("set_track_volume", "volume_db", 12.5),
        ("set_track_volume", "volume_db", -61),
        ("set_track_pan", "pan", 1.01),
        ("set_track_pan", "pan", -1.01),
        ("set_track_pan", "pan", float("nan")),
        ("set_track_volume", "volume_db", float("inf")),
        ("set_track_volume", "volume_db", math.nan),
    ):
        with pytest.raises(ScriptInvalid):
            parse_script(
                {"kut_script": 1, "ops": [_op(name, track="A1", **{field: bad})]}
            )
    for edge, field, value in (
        ("set_track_volume", "volume_db", 12.0),
        ("set_track_volume", "volume_db", -60.0),
        ("set_track_pan", "pan", 1.0),
    ):
        _session, report = _apply(_project(), _op(edge, track="A1", **{field: value}))
        assert report.ok, report.error
    _refused(
        _project(), _op("set_track_volume", track="V1", volume_db=0.0)
    )  # pas de fader sur une piste vidéo
    _refused(_project(), _op("set_track_pan", track="V1", pan=0.0))
    locked = _project()
    locked.tracks[3].locked = True
    _refused(locked, _op("set_track_volume", track="A1", volume_db=-3.0))
    _refused(locked, _op("set_track_pan", track="A1", pan=0.5))


def test_huge_inputs_are_bounded_at_parse():
    with pytest.raises(ScriptInvalid) as caught:
        parse_script(
            {"kut_script": 1, "ops": [_op("cut_clip", clip="x" * 5_000_000, at=1)]}
        )
    assert len(str(caught.value)) < 10_000


def test_rename_and_remove_do_not_look_at_the_lock_like_the_interface():
    """Documenté dans docs/scripting.md : l'interface renomme et supprime une piste (vide) verrouillée ; seul `move_track` la refuse."""
    _parity(
        _project,
        _op("rename_track", track="V3", name="Fond"),
        lambda p: rename_track(p, "V3", "Fond"),
    )
    _parity(_project, _op("remove_track", track="V3"), lambda p: remove_track(p, "V3"))


def _followers_project() -> Project:
    """G1 : t1 (0-3), t2 (3-6, calque verrouillé) ; V1 inchangée ; le suiveur verrouillé est sur une piste libre."""
    project = _graphics_project(lock_second=True)
    project.tracks[0].clips.append(Clip("tard", "m", "V1", 20.0, 0.0, 1.0))
    return project


def test_ripple_verbs_refuse_when_a_locked_layer_would_be_shifted():
    for operation in (
        _op("ripple_trim_clip_start", clip="t1", at=1.0),
        _op("ripple_delete_clip", clip="t1"),
    ):
        project = _followers_project()
        report = _refused(project, operation)  # rien ne bouge : t2 est resté à 3
        assert (
            "verrouill" in str(report.error)
            and find_clip(project, "t2").timeline_start == 3.0
        )


def test_ripple_verbs_still_work_when_no_locked_layer_follows():
    project = _graphics_project(lock_second=False)
    _session, report = _apply(project, _op("ripple_trim_clip_start", clip="t1", at=1.0))
    assert report.ok, report.error
    assert find_clip(project, "t2").timeline_start == 2.0
    # Un calque verrouillé AVANT le clip n'est pas décalé : pas de refus.
    before = _graphics_project(lock_second=False)
    first = find_clip(before, "t1")
    first.graphic = replace(first.graphic, locked=True)
    _session, report = _apply(before, _op("ripple_delete_clip", clip="t2"))
    assert report.ok, report.error
    # Rogner à la position actuelle ne retire rien : rien ne recule, donc pas de refus.
    _session, report = _apply(
        _followers_project(), _op("ripple_trim_clip_start", clip="t1", at=0.0)
    )
    assert report.ok, report.error
