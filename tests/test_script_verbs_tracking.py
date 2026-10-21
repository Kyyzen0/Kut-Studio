"""Format déclaratif : suivi, liaisons et stabilisation (lot 6 de l'extension 0.8.0) : `add_tracker`, `set_tracker_position`, `add_link`,
`bake_link`, `set_stabilization`, `follow_reframe`…

Chaque verbe appelle la fonction du modèle que l'interface appelle (`core/tracking_ops.py`, `core/follow_reframe.py`) : le test de parité fait
le même appel directement sur une copie et compare les projets. Les refus (verrous du clip, du calque, de la piste et des clips qui
suivent ; bornes que le modèle ramènerait ; plages démesurées ; données absentes) sont ceux d'un script : le projet reste inchangé.
Aucun verbe ne lance d'analyse : `apply_tracking_result` reste à l'interface.
"""

from __future__ import annotations

import copy
import json
import math
from dataclasses import replace

import pytest

from core import follow_reframe, tracking_ops
from core.compositing import Compositing, Mask, MaskShape
from core.mograph_layers import add_layer
from core.project_model import Clip, MediaAsset, Project, Track
from core.scripting import (
    ScriptInvalid,
    Session,
    apply_script,
    created_ids,
    parse_script,
)
from core.scripting import tracking_rules
from core.scripting.schema import json_schema
from core.scripting.verbs import VERBS
from core.timeline_operations import find_clip
from core.tracking_model import Sample, SampleStatus, StabilizationMode, TrackData
from core.visual_effects import TransformKeyframe
from ui import i18n

NEW_VERBS = (
    "add_tracker",
    "update_tracker",
    "set_tracker_settings",
    "set_tracker_position",
    "remove_correction",
    "reset_tracker",
    "remove_tracker",
    "add_link",
    "remove_link",
    "set_link_enabled",
    "bake_link",
    "set_stabilization",
    "remove_stabilization",
    "follow_reframe",
)


@pytest.fixture(autouse=True)
def _french(monkeypatch):
    monkeypatch.setenv("KUT_STUDIO_LANG", "fr")
    i18n.reset_for_tests()
    yield
    i18n.reset_for_tests()


def _project(*, width: int = 320, height: int = 180) -> Project:
    """V1 : v (0 à 6 s) ; V2 : w ; V3 (verrouillée) : lk ; G1 : g (calque libre, 4 s) ; G2 (verrouillée) : z ; A1 : un clip audio."""
    project = Project(
        "Démo",
        width=width,
        height=height,
        fps=30.0,
        media_assets=[
            MediaAsset(
                "m", "/x/a.mp4", "a", 10.0, 320, 180, 30.0, "video", has_audio=True
            ),
            MediaAsset(
                "snd", "/x/s.wav", "s", 10.0, 0, 0, 0.0, "audio", has_audio=True
            ),
        ],
        tracks=[
            Track("V1", "V1", "video", clips=[Clip("v", "m", "V1", 0.0, 0.0, 6.0)]),
            Track("V2", "V2", "video", clips=[Clip("w", "m", "V2", 0.0, 0.0, 6.0)]),
            Track(
                "V3",
                "V3",
                "video",
                locked=True,
                clips=[Clip("lk", "m", "V3", 0.0, 0.0, 6.0)],
            ),
            Track("G1", "G1", "graphics"),
            Track("G2", "G2", "graphics", locked=True),
            Track("A1", "A1", "audio", clips=[Clip("au", "snd", "A1", 0.0, 0.0, 5.0)]),
        ],
    )
    add_layer(
        project, "shape", at=0.0, duration=4.0, track=project.tracks[3], clip_id="g"
    )
    add_layer(
        project, "shape", at=0.0, duration=4.0, track=project.tracks[3], clip_id="h"
    )
    add_layer(
        project, "shape", at=0.0, duration=2.0, track=project.tracks[4], clip_id="z"
    )
    return project


def _tracked(project: Project | None = None, clip_id: str = "v") -> Project:
    """Deux trackers (t1, t2) de ``clip_id``, chacun avec deux images posées (0 s et 3 s) : ils peuvent être liés."""
    project = project or _project()
    for tracker_id, x, y in (("t1", 100.0, 50.0), ("t2", 200.0, 120.0)):
        tracking_ops.add_tracker(
            project, clip_id, timeline_time=0.0, x=x, y=y, tracker_id=tracker_id
        )
        tracking_ops.set_tracker_position(
            project, clip_id, tracker_id, 3.0, x + 30.0, y + 10.0
        )
    return project


def _op(verb: str, /, **fields) -> dict:
    return {"op": verb, **fields}


def _apply(project: Project, *ops: dict):
    session = Session(project)
    return session, apply_script(
        session, parse_script({"kut_script": 1, "ops": list(ops)})
    )


def _snapshot(project: Project):
    return (copy.deepcopy(project.sequences), copy.deepcopy(project.media_assets))


def _same(first: Project, second: Project) -> bool:
    return _snapshot(first) == _snapshot(second)


def _parity(factory, op: dict, direct) -> Project:
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
    assert not report.ok
    assert _snapshot(session.project) == before
    if match is not None:
        assert match in str(report.error), report.error
    return report


def _tracking(project: Project, clip_id: str):
    return tracking_ops.tracking_of(find_clip(project, clip_id))


def _link_project() -> Project:
    """``_tracked`` + une liaison de position de ``g`` (calque libre) sur t1 + t2, dont l'identifiant est ``lnk``."""
    project = _tracked()
    tracking_ops.add_link(
        project, "g", "v", ["t1", "t2"], timeline_time=0.0, link_id="lnk"
    )
    return project


def _lock_layer(project: Project, clip_id: str) -> None:
    clip = find_clip(project, clip_id)
    clip.graphic = replace(clip.graphic, locked=True)


# --- Forme -------------------------------------------------------------------------------------------------------------------


def test_the_verbs_are_in_the_table_with_the_right_shape():
    assert set(NEW_VERBS) <= set(VERBS)
    for name in NEW_VERBS:
        assert VERBS[name].doc and VERBS[name].group == "tracking", name
        assert (VERBS[name].creates is not None) == (
            name in ("add_tracker", "add_link")
        ), name
    assert (
        VERBS["add_tracker"].creates == "tracker"
        and "id" in VERBS["add_tracker"].accepts
        and "as" in VERBS["add_tracker"].accepts
    )
    assert VERBS["add_link"].creates == "link" and "as" in VERBS["add_link"].accepts


def test_the_json_schema_describes_every_new_verb():
    titles = {
        item["title"] for item in json_schema()["properties"]["ops"]["items"]["oneOf"]
    }
    assert set(NEW_VERBS) <= titles


def test_no_verb_launches_an_analysis():
    """Aucun verbe n'importe le moteur d'analyse (lecture des images, corrélation) ni ne l'applique : un script est une donnée (ADR-0005)."""
    import ast
    import pathlib

    folder = pathlib.Path(tracking_rules.__file__).parent
    forbidden = {
        "tracking_engine",
        "tracking_frames",
        "tracking_match",
        "analysis_request",
        "apply_tracking_result",
        "subprocess",
    }
    for name in ("tracking_verbs.py", "tracking_rules.py"):
        imported: set[str] = set()
        for node in ast.walk(ast.parse((folder / name).read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom):
                imported.update(
                    [node.module or "", *(alias.name for alias in node.names)]
                )
            elif isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
        assert not any(item.split(".")[-1] in forbidden for item in imported), (
            name,
            imported & forbidden,
        )


# --- add_tracker -------------------------------------------------------------------------------------------------------------


def test_add_tracker_matches_the_model():
    _parity(
        _project,
        _op("add_tracker", clip="v", at=1.0, x=120.0, y=60.0, id="t1", name="Œil"),
        lambda p: tracking_ops.add_tracker(
            p, "v", timeline_time=1.0, x=120.0, y=60.0, name="Œil", tracker_id="t1"
        ),
    )
    _parity(
        _project,
        _op("add_tracker", clip="v", at=0.0, id="t1"),  # le centre, le nom par défaut
        lambda p: tracking_ops.add_tracker(p, "v", timeline_time=0.0, tracker_id="t1"),
    )


def test_add_tracker_creates_an_object_the_script_can_cite():
    session, report = _apply(
        _project(),
        _op("add_tracker", clip="v", at=0.0, x=10.0, y=10.0, **{"as": "eye"}),
        _op(
            "set_tracker_position",
            clip="v",
            tracker={"ref": "eye"},
            at=2.0,
            x=20.0,
            y=20.0,
        ),
    )
    assert report.ok, report.error
    script = parse_script(
        {
            "kut_script": 1,
            "ops": [_op("add_tracker", clip="v", at=0.0, **{"as": "eye"})],
        }
    )
    assert created_ids(script, apply_script(Session(_project()), script)) == [
        {"op": "0:add_tracker", "id": "eye"}
    ]
    assert _tracking(session.project, "v").tracker("eye") is not None
    assert (
        _tracking(session.project, "v").tracker("eye").data.count == 61
    )  # 0 s à 2 s à 30 i/s


def test_add_tracker_generates_an_id_without_one():
    session, report = _apply(_project(), _op("add_tracker", clip="v", at=0.0))
    assert report.ok and len(_tracking(session.project, "v").trackers) == 1
    assert report.results[0].value.id


def test_add_tracker_refuses_a_taken_or_malformed_id():
    project = _tracked()
    _refused(
        project, _op("add_tracker", clip="v", at=0.0, id="t1"), match="existe déjà"
    )
    _refused(
        project, _op("add_tracker", clip="w", at=0.0, id="t2"), match="existe déjà"
    )  # unique dans tout le projet
    with pytest.raises(ScriptInvalid):
        parse_script(
            {"kut_script": 1, "ops": [_op("add_tracker", clip="v", at=0.0, id="a b")]}
        )


def test_the_default_name_is_the_model_default_and_a_long_name_is_refused():
    session, report = _apply(
        _project(),
        _op("add_tracker", clip="v", at=0.0),
        _op("add_tracker", clip="v", at=0.0),
    )
    assert [t.name for t in _tracking(session.project, "v").trackers] == [
        "Tracker 1",
        "Tracker 2",
    ]
    with pytest.raises(ScriptInvalid):
        parse_script(
            {
                "kut_script": 1,
                "ops": [_op("add_tracker", clip="v", at=0.0, name="x" * 81)],
            }
        )


@pytest.mark.parametrize(
    "fields",
    [
        {"at": 7.0},
        {"at": 0.0, "x": 321.0},
        {"at": 0.0, "y": 181.0},
    ],
)
def test_add_tracker_refuses_what_the_model_would_clamp(fields):
    _refused(_project(), _op("add_tracker", clip="v", **fields))


def test_add_tracker_refuses_a_locked_track_a_locked_layer_and_the_wrong_kind_of_track():
    _refused(_project(), _op("add_tracker", clip="lk", at=0.0), match="verrouillée")
    _refused(_project(), _op("add_tracker", clip="g", at=0.0), match="piste")
    _refused(_project(), _op("add_tracker", clip="au", at=0.0), match="piste")
    project = _project()
    clip = find_clip(project, "v")
    clip.graphic = (
        None  # un clip vidéo n'a pas de calque ; le verrou de calque ne le concerne pas
    )
    session, report = _apply(project, _op("add_tracker", clip="v", at=0.0))
    assert report.ok


def test_add_tracker_refuses_a_nested_sequence_clip():
    project = _project()
    find_clip(project, "v").sequence_id = "other"
    _refused(project, _op("add_tracker", clip="v", at=0.0))


def test_a_clip_cannot_hold_more_trackers_than_the_limit(monkeypatch):
    monkeypatch.setattr(tracking_rules, "MAX_TRACKERS_PER_CLIP", 2)
    project = _tracked()
    _refused(project, _op("add_tracker", clip="v", at=0.0), match="maximum")
    session, report = _apply(
        project, _op("add_tracker", clip="w", at=0.0)
    )  # un autre clip : de la place
    assert report.ok


def test_the_project_limit_counts_every_sequence_not_only_the_active_one(monkeypatch):
    """``Project.tracks`` n'est que la séquence active : les trackers d'une autre séquence comptent aussi."""
    from core.project_model import Sequence

    monkeypatch.setattr(tracking_rules, "MAX_TRACKERS_IN_PROJECT", 2)
    project = _project()
    other = Sequence(
        id="seq2",
        name="Autre",
        tracks=[
            Track("W1", "W1", "video", clips=[Clip("far", "m", "W1", 0.0, 0.0, 6.0)])
        ],
    )
    project.sequences.append(other)
    tracking_ops.add_tracker(project, "far", timeline_time=0.0, tracker_id="x1")
    tracking_ops.add_tracker(project, "far", timeline_time=0.0, tracker_id="x2")
    assert len(project.tracks) == 6 and project.active_sequence is not other
    _refused(project, _op("add_tracker", clip="v", at=0.0), match="limite")


def test_add_tracker_counts_its_first_image_against_the_project_frame_cap_in_every_sequence(
    monkeypatch,
):
    """Un projet déjà au plafond d'images suivies (ici dans une autre séquence) n'accepte pas un tracker de plus, qui porte déjà une image."""
    from core.project_model import Sequence

    project = _project()
    project.sequences.append(
        Sequence(
            id="seq2",
            name="Autre",
            tracks=[
                Track(
                    "W1", "W1", "video", clips=[Clip("far", "m", "W1", 0.0, 0.0, 6.0)]
                )
            ],
        )
    )
    tracking_ops.add_tracker(project, "far", timeline_time=0.0, tracker_id="x1")
    assert tracking_rules.tracked_frames(project) == 1 and not any(
        t.clips and t.clips[0].tracking for t in project.tracks
    )
    monkeypatch.setattr(tracking_rules, "MAX_PROJECT_TRACK_FRAMES", 1)
    _refused(project, _op("add_tracker", clip="v", at=0.0), match="trop d'images")
    monkeypatch.setattr(tracking_rules, "MAX_PROJECT_TRACK_FRAMES", 2)
    session, report = _apply(project, _op("add_tracker", clip="v", at=0.0))
    assert report.ok


# --- update_tracker ----------------------------------------------------------------------------------------------------------


def test_update_tracker_matches_the_model():
    _parity(
        _tracked,
        _op(
            "update_tracker",
            clip="v",
            tracker="t1",
            name="Coin",
            color="#112233",
            visible=False,
            show_path=False,
        ),
        lambda p: tracking_ops.update_tracker(
            p, "v", "t1", name="Coin", color="#112233", visible=False, show_path=False
        ),
    )
    _parity(
        _tracked,
        _op("update_tracker", clip="v", tracker="t1", name="Coin"),
        lambda p: tracking_ops.update_tracker(p, "v", "t1", name="Coin"),
    )


def test_update_tracker_refuses_an_empty_change_an_alpha_colour_and_a_locked_clip():
    _refused(
        _tracked(),
        _op("update_tracker", clip="v", tracker="t1"),
        match="au moins un champ",
    )
    _refused(
        _tracked(),
        _op("update_tracker", clip="v", tracker="t1", color="#11223344"),
        match="sans transparence",
    )
    _refused(
        _tracked(),
        _op("update_tracker", clip="v", tracker="nope", name="x"),
        match="Tracker",
    )
    project = _tracked(None, "v")
    project.tracks[0].locked = True
    _refused(
        project,
        _op("update_tracker", clip="v", tracker="t1", name="x"),
        match="verrouillée",
    )


# --- set_tracker_settings ----------------------------------------------------------------------------------------------------


def test_set_tracker_settings_matches_the_model():
    changes = {
        "pattern_width": 60.0,
        "pattern_height": 50.0,
        "search_width": 200.0,
        "search_height": 180.0,
        "min_confidence": 0.5,
        "good_confidence": 0.9,
        "adapt": "fixed",
        "precision": "full",
        "stop_on_loss": False,
    }
    _parity(
        _tracked,
        _op("set_tracker_settings", clip="v", tracker="t1", **changes),
        lambda p: tracking_ops.set_tracker_settings(p, "v", "t1", **changes),
    )


@pytest.mark.parametrize(
    "fields, text",
    [
        (
            {"search_width": 26.0},
            "search_width",
        ),  # zone cible par défaut 24 : la recherche doit la dépasser de 4 px
        (
            {"pattern_width": 200.0},
            "search_width",
        ),  # la zone de recherche (144) serait agrandie par le modèle
        (
            {"min_confidence": 0.9},
            "good_confidence",
        ),  # la bonne confiance (0,8) serait remontée par le modèle
        ({"good_confidence": 0.5}, "good_confidence"),  # sous la minimale (0,55)
    ],
)
def test_set_tracker_settings_refuses_what_the_model_would_adjust(fields, text):
    _refused(
        _tracked(),
        _op("set_tracker_settings", clip="v", tracker="t1", **fields),
        match=text,
    )


def test_set_tracker_settings_needs_a_setting_and_refuses_out_of_range_numbers():
    _refused(
        _tracked(),
        _op("set_tracker_settings", clip="v", tracker="t1"),
        match="au moins un réglage",
    )
    for fields in (
        {"pattern_width": 7.0},
        {"search_width": 5000.0},
        {"min_confidence": 0.99},
        {"adapt": "magic"},
    ):
        with pytest.raises(ScriptInvalid):
            parse_script(
                {
                    "kut_script": 1,
                    "ops": [
                        _op("set_tracker_settings", clip="v", tracker="t1", **fields)
                    ],
                }
            )


def test_set_tracker_settings_refuses_a_locked_clip():
    project = _tracked()
    project.tracks[0].locked = True
    _refused(
        project, _op("set_tracker_settings", clip="v", tracker="t1", adapt="fixed")
    )


# --- set_tracker_position ----------------------------------------------------------------------------------------------------


def test_set_tracker_position_matches_the_model():
    _parity(
        _tracked,
        _op("set_tracker_position", clip="v", tracker="t1", at=2.0, x=150.0, y=70.0),
        lambda p: tracking_ops.set_tracker_position(p, "v", "t1", 2.0, 150.0, 70.0),
    )
    _parity(
        _tracked,
        _op(
            "set_tracker_position", clip="v", tracker="t1", at=5.0, x=-100.0, y=300.0
        ),  # dans [−1, 2] × taille
        lambda p: tracking_ops.set_tracker_position(p, "v", "t1", 5.0, -100.0, 300.0),
    )


def test_set_tracker_position_on_a_trimmed_and_sped_up_clip_uses_the_source_index():
    def factory() -> Project:
        project = _project()
        clip = find_clip(project, "v")
        clip.source_in, clip.source_out = 2.0, 8.0
        tracking_ops.add_tracker(project, "v", timeline_time=0.0, tracker_id="t1")
        return project

    _parity(
        factory,
        _op("set_tracker_position", clip="v", tracker="t1", at=1.0, x=10.0, y=10.0),
        lambda p: tracking_ops.set_tracker_position(p, "v", "t1", 1.0, 10.0, 10.0),
    )


@pytest.mark.parametrize(
    "fields",
    [
        {"at": 7.0, "x": 10.0, "y": 10.0},
        {"at": 1.0, "x": 641.0, "y": 10.0},
        {"at": 1.0, "x": 10.0, "y": -181.0},
    ],
)
def test_set_tracker_position_refuses_what_the_model_would_clamp(fields):
    _refused(_tracked(), _op("set_tracker_position", clip="v", tracker="t1", **fields))


def test_a_far_point_cannot_stretch_the_dense_data(monkeypatch):
    """Les données sont une grille dense (10 octets par image) : la plage d'un tracker est bornée avant d'écrire."""
    monkeypatch.setattr(tracking_rules, "MAX_TRACK_SPAN", 100)
    _refused(
        _tracked(),
        _op("set_tracker_position", clip="v", tracker="t1", at=5.5, x=10.0, y=10.0),
        match="plage",
    )


def test_the_total_of_tracked_frames_of_the_project_is_bounded(monkeypatch):
    monkeypatch.setattr(tracking_rules, "MAX_PROJECT_TRACK_FRAMES", 200)
    project = _tracked()  # 2 trackers de 91 images
    _refused(
        project,
        _op("set_tracker_position", clip="v", tracker="t1", at=5.0, x=10.0, y=10.0),
        match="trop d'images",
    )
    session, report = _apply(
        project,
        _op("set_tracker_position", clip="v", tracker="t1", at=2.0, x=10.0, y=10.0),
    )  # dans la plage : aucune croissance
    assert report.ok


def test_set_tracker_position_refuses_while_a_follower_is_locked():
    """Un clip qui suit ce tracker changerait d'apparence : verrouillé, il bloque l'écriture."""
    project = _link_project()
    _lock_layer(project, "g")
    _refused(
        project,
        _op("set_tracker_position", clip="v", tracker="t1", at=2.0, x=10.0, y=10.0),
        match="verrouillé",
    )
    project = _link_project()
    session, report = _apply(
        project,
        _op("set_tracker_position", clip="v", tracker="t1", at=2.0, x=10.0, y=10.0),
    )
    assert report.ok
    project = _link_project()
    project.tracks[4].locked = True  # un suiveur sur une piste verrouillée
    follower = project.tracks[4].clips[0]
    tracking_ops.add_link(
        project, "z", "v", ["t1", "t2"], timeline_time=0.0, link_id="lz"
    )
    _refused(
        project,
        _op("set_tracker_position", clip="v", tracker="t2", at=2.0, x=10.0, y=10.0),
        match="verrouillée",
    )
    assert follower is not None


def test_a_disabled_link_does_not_block_the_tracker_data():
    project = _link_project()
    tracking_ops.set_link_enabled(project, "g", "lnk", False)
    _lock_layer(project, "g")
    session, report = _apply(
        project,
        _op("set_tracker_position", clip="v", tracker="t1", at=2.0, x=10.0, y=10.0),
    )
    assert report.ok


def test_a_follower_of_another_tracker_does_not_block():
    project = _tracked()
    tracking_ops.add_tracker(project, "v", timeline_time=0.0, tracker_id="t3")
    tracking_ops.set_tracker_position(project, "v", "t3", 3.0, 5.0, 5.0)
    tracking_ops.add_link(project, "g", "v", ["t3"], timeline_time=0.0, link_id="l3")
    _lock_layer(project, "g")
    session, report = _apply(
        project,
        _op("set_tracker_position", clip="v", tracker="t1", at=2.0, x=10.0, y=10.0),
    )
    assert report.ok


# --- remove_correction -------------------------------------------------------------------------------------------------------


def test_remove_correction_matches_the_model():
    _parity(
        _tracked,
        _op("remove_correction", clip="v", tracker="t1", at=3.0),
        lambda p: tracking_ops.remove_correction(p, "v", "t1", 3.0),
    )


def test_remove_correction_refuses_an_image_that_is_not_a_correction_and_the_last_point():
    project = _project()
    tracking_ops.add_tracker(project, "v", timeline_time=0.0, tracker_id="t1")
    clip = find_clip(project, "v")
    tracker = tracking_ops.tracking_of(clip).tracker("t1")
    data = tracker.data.with_samples(
        {i: Sample(10.0 + i, 10.0, 0.9, SampleStatus.TRACKED) for i in range(1, 30)}
    )
    clip.tracking = replace(clip.tracking, trackers=(replace(tracker, data=data),))
    _refused(
        project,
        _op("remove_correction", clip="v", tracker="t1", at=0.5),
        match="pas de point posé",
    )
    solo = _project()
    tracking_ops.add_tracker(solo, "v", timeline_time=0.0, tracker_id="t1")
    _refused(
        solo,
        _op("remove_correction", clip="v", tracker="t1", at=0.0),
        match="dernier point",
    )


def test_remove_correction_refuses_a_locked_follower_and_an_instant_outside_the_clip():
    project = _link_project()
    _lock_layer(project, "g")
    _refused(
        project,
        _op("remove_correction", clip="v", tracker="t1", at=3.0),
        match="verrouillé",
    )
    _refused(_tracked(), _op("remove_correction", clip="v", tracker="t1", at=7.0))
    edge = _tracked()
    tracking_ops.set_tracker_position(
        edge, "v", "t1", 6.0, 5.0, 5.0
    )  # le modèle ramènerait 7 s à la dernière image du clip
    _refused(
        edge,
        _op("remove_correction", clip="v", tracker="t1", at=7.0),
        match="hors du clip",
    )


# --- reset_tracker -----------------------------------------------------------------------------------------------------------


def _analysed() -> Project:
    """t1 avec des images analysées de 0,5 s à 2 s et une correction à la main à 1 s."""
    project = _project()
    tracking_ops.add_tracker(
        project, "v", timeline_time=0.0, x=10.0, y=10.0, tracker_id="t1"
    )
    clip = find_clip(project, "v")
    tracker = tracking_ops.tracking_of(clip).tracker("t1")
    data = tracker.data.with_samples(
        {i: Sample(10.0 + i, 10.0, 0.9, SampleStatus.TRACKED) for i in range(15, 61)}
    )
    data = data.with_samples({30: Sample(0.0, 0.0, 1.0, SampleStatus.MANUAL)})
    clip.tracking = replace(clip.tracking, trackers=(replace(tracker, data=data),))
    return project


@pytest.mark.parametrize(
    "direction, at", [("all", None), ("after", 1.0), ("before", 1.5)]
)
def test_reset_tracker_matches_the_model(direction, at):
    op = _op(
        "reset_tracker",
        clip="v",
        tracker="t1",
        direction=direction,
        **({} if at is None else {"at": at}),
    )
    code = {"all": 0, "after": 1, "before": -1}[direction]
    _parity(
        _analysed,
        op,
        lambda p: tracking_ops.reset_tracker(
            p, "v", "t1", timeline_time=at, direction=code
        ),
    )


def test_reset_tracker_keeps_the_manual_points_and_defaults_to_all():
    session, report = _apply(_analysed(), _op("reset_tracker", clip="v", tracker="t1"))
    data = _tracking(session.project, "v").tracker("t1").data
    assert (
        report.ok
        and {s for _i, s in data.samples() if s.status is not SampleStatus.EMPTY}
        and data.counts()[SampleStatus.TRACKED] == 0
    )


def test_reset_tracker_refuses_inconsistent_instants_and_a_locked_follower():
    _refused(
        _analysed(),
        _op("reset_tracker", clip="v", tracker="t1", direction="all", at=1.0),
        match="« at »",
    )
    _refused(
        _analysed(),
        _op("reset_tracker", clip="v", tracker="t1", direction="after"),
        match="demande",
    )
    _refused(
        _analysed(),
        _op("reset_tracker", clip="v", tracker="t1", direction="after", at=9.0),
    )
    project = _link_project()
    _lock_layer(project, "g")
    _refused(project, _op("reset_tracker", clip="v", tracker="t1"), match="verrouillé")


# --- remove_tracker ----------------------------------------------------------------------------------------------------------


def test_remove_tracker_matches_the_model_and_drops_the_links_that_depended_on_it():
    project = _parity(
        _link_project,
        _op("remove_tracker", clip="v", tracker="t1"),
        lambda p: tracking_ops.remove_tracker(p, "v", "t1"),
    )
    link = _tracking(project, "g").link("lnk")
    assert link is not None and link.tracker_ids == (
        "t2",
    )  # la liaison garde les autres trackers
    project = _parity(
        _link_project,
        _op("remove_tracker", clip="v", tracker="t1"),
        lambda p: tracking_ops.remove_tracker(p, "v", "t1"),
    )


def test_remove_tracker_removes_a_link_left_without_tracker_and_the_stabilization_use():
    def factory() -> Project:
        project = _tracked()
        tracking_ops.add_link(
            project, "g", "v", ["t1"], timeline_time=0.0, link_id="lnk"
        )
        tracking_ops.set_stabilization(project, "v", tracker_ids=("t1",))
        return project

    project = _parity(
        factory,
        _op("remove_tracker", clip="v", tracker="t1"),
        lambda p: tracking_ops.remove_tracker(p, "v", "t1"),
    )
    assert (
        _tracking(project, "g").links == ()
        and _tracking(project, "v").stabilization is None
    )


def test_remove_tracker_refuses_before_changing_anything_when_a_dependent_is_locked():
    project = _link_project()
    _lock_layer(project, "g")
    _refused(project, _op("remove_tracker", clip="v", tracker="t1"), match="verrouillé")
    project = _link_project()
    project.tracks[3].locked = True
    _refused(
        project, _op("remove_tracker", clip="v", tracker="t1"), match="verrouillée"
    )


def test_remove_tracker_leaves_a_locked_clip_alone_when_its_link_does_not_use_the_tracker():
    project = _tracked()
    tracking_ops.add_tracker(project, "v", timeline_time=0.0, tracker_id="t3")
    tracking_ops.set_tracker_position(project, "v", "t3", 3.0, 5.0, 5.0)
    tracking_ops.add_link(project, "g", "v", ["t3"], timeline_time=0.0, link_id="l3")
    _lock_layer(project, "g")
    session, report = _apply(project, _op("remove_tracker", clip="v", tracker="t1"))
    assert report.ok and _tracking(session.project, "g").link("l3") is not None


def test_remove_tracker_on_one_half_of_a_cut_clip_leaves_the_locked_follower_alone():
    """Les moitiés d'un clip coupé partagent les trackers : la liaison suit encore l'autre moitié, elle n'est pas réécrite."""
    from core.timeline_operations import cut_clip

    project = _link_project()
    _left, right = cut_clip(project, "v", 3.0, right_clip_id="v2")
    assert tracking_ops.tracking_of(find_clip(project, "g")).link("lnk").follows("v2")
    _lock_layer(project, "g")
    session, report = _apply(project, _op("remove_tracker", clip="v", tracker="t1"))
    assert report.ok, report.error
    assert _tracking(session.project, "g").link("lnk").tracker_ids == ("t1", "t2")
    assert right is not None


# --- add_link ----------------------------------------------------------------------------------------------------------------


def test_add_link_matches_the_model():
    _parity(
        _tracked,
        _op(
            "add_link",
            clip="g",
            source="v",
            trackers=["t1", "t2"],
            at=1.0,
            id="lnk",
            rotation=True,
            scale=True,
        ),
        lambda p: tracking_ops.add_link(
            p,
            "g",
            "v",
            ["t1", "t2"],
            rotation=True,
            scale=True,
            timeline_time=1.0,
            link_id="lnk",
        ),
    )
    _parity(
        _tracked,
        _op("add_link", clip="w", source="v", trackers=["t1"], at=0.0, id="lnk"),
        lambda p: tracking_ops.add_link(
            p, "w", "v", ["t1"], timeline_time=0.0, link_id="lnk"
        ),
    )
    _parity(
        _tracked,
        _op(
            "add_link",
            clip="v",
            source="v",
            trackers=["t1"],
            at=0.0,
            target="anchor",
            id="lnk",
        ),
        lambda p: tracking_ops.add_link(
            p, "v", "v", ["t1"], target="anchor", timeline_time=0.0, link_id="lnk"
        ),
    )


def test_add_link_to_a_mask_and_to_a_colour_window():
    def factory() -> Project:
        project = _tracked()
        clip = find_clip(project, "g")
        clip.compositing = Compositing(masks=(Mask(id="mk", shape=MaskShape.ELLIPSE),))
        return project

    _parity(
        factory,
        _op(
            "add_link",
            clip="g",
            source="v",
            trackers=["t1"],
            at=0.0,
            target="mask",
            mask="mk",
            id="lnk",
        ),
        lambda p: tracking_ops.add_link(
            p,
            "g",
            "v",
            ["t1"],
            target="mask",
            mask_id="mk",
            timeline_time=0.0,
            link_id="lnk",
        ),
    )
    _refused(
        factory(),
        _op(
            "add_link",
            clip="g",
            source="v",
            trackers=["t1"],
            at=0.0,
            target="mask",
            mask="ghost",
        ),
        match="Masque",
    )


def test_add_link_returns_a_citable_link_and_the_alias_works_end_to_end():
    script = parse_script(
        {
            "kut_script": 1,
            "ops": [
                _op(
                    "add_link",
                    clip="g",
                    source="v",
                    trackers=["t1", "t2"],
                    at=0.0,
                    **{"as": "follow"},
                ),
                _op(
                    "set_link_enabled", clip="g", link={"ref": "follow"}, enabled=False
                ),
            ],
        }
    )
    session = Session(_tracked())
    report = apply_script(session, script)
    assert report.ok, report.error
    assert created_ids(script, report) == [{"op": "0:add_link", "id": "follow"}]
    assert _tracking(session.project, "g").link("follow").enabled is False


def test_add_link_refuses_a_second_link_on_the_same_property_instead_of_replacing_the_first():
    project = _link_project()
    _refused(
        project,
        _op("add_link", clip="g", source="v", trackers=["t1"], at=0.0),
        match="remove_link",
    )
    session, report = _apply(
        project,
        _op("remove_link", clip="g", link="lnk"),
        _op("add_link", clip="g", source="v", trackers=["t1"], at=0.0),
    )
    assert report.ok


def test_add_link_refuses_unanalysed_trackers_and_the_model_rules():
    project = _project()
    tracking_ops.add_tracker(project, "v", timeline_time=0.0, tracker_id="t1")
    _refused(
        project,
        _op("add_link", clip="g", source="v", trackers=["t1"], at=0.0),
        match="au moins deux images",
    )
    project = _tracked()
    _refused(
        project,
        _op("add_link", clip="g", source="v", trackers=["t1"], at=0.0, rotation=True),
        match="deux trackers",
    )
    _refused(
        project,
        _op("add_link", clip="v", source="v", trackers=["t1"], at=0.0),
        match="stabilisation",
    )  # transform de soi-même
    _refused(
        project,
        _op("add_link", clip="g", source="v", trackers=["t1"], at=0.0, target="anchor"),
        match="point d'ancrage",
    )
    _refused(
        project,
        _op("add_link", clip="g", source="v", trackers=["t1", "t1"], at=0.0),
        match="deux fois",
    )
    _refused(
        project,
        _op("add_link", clip="g", source="v", trackers=["ghost"], at=0.0),
        match="Tracker",
    )
    _refused(
        project, _op("add_link", clip="g", source="g", trackers=["t1"], at=0.0)
    )  # un calque ne porte pas de trackers


def test_add_link_refuses_inconsistent_components_and_a_reference_outside_the_source():
    project = _tracked()
    _refused(
        project,
        _op("add_link", clip="g", source="v", trackers=["t1"], at=0.0, position=False),
        match="au moins",
    )
    _refused(
        project,
        _op(
            "add_link",
            clip="v",
            source="v",
            trackers=["t1"],
            at=0.0,
            target="anchor",
            position=False,
        ),
        match="toujours",
    )
    _refused(
        project,
        _op("add_link", clip="g", source="v", trackers=["t1"], at=0.0, mask="mk"),
        match="mask",
    )
    _refused(
        project,
        _op("add_link", clip="g", source="v", trackers=["t1"], at=0.0, target="mask"),
        match="mask",
    )
    _refused(
        project,
        _op("add_link", clip="g", source="v", trackers=["t1"], at=6.5),
        match="hors du clip",
    )


def test_add_link_refuses_a_locked_target_but_accepts_a_locked_source():
    project = _tracked()
    _refused(
        project,
        _op("add_link", clip="z", source="v", trackers=["t1"], at=0.0),
        match="verrouillée",
    )
    project = _tracked()
    _lock_layer(project, "g")
    _refused(
        project,
        _op("add_link", clip="g", source="v", trackers=["t1"], at=0.0),
        match="verrouillé",
    )
    project = _tracked()
    project.tracks[
        0
    ].locked = True  # la source n'est que lue : elle peut être verrouillée
    session, report = _apply(
        project, _op("add_link", clip="g", source="v", trackers=["t1"], at=0.0)
    )
    assert report.ok
    _refused(
        _tracked(),
        _op("add_link", clip="au", source="v", trackers=["t1"], at=0.0),
        match="piste",
    )


def test_a_link_chain_between_two_clips_has_no_cycle_to_evaluate():
    """v suit w et w suit v : l'évaluation ne lit jamais les liaisons du clip source (TrackingContext.layer_matrix)."""
    from core.render_plan import build_render_plan

    project = _project()
    for clip_id in ("v", "w"):
        for tracker_id, x, y in (("t1", 100.0, 50.0), ("t2", 200.0, 120.0)):
            tracking_ops.add_tracker(
                project,
                clip_id,
                timeline_time=0.0,
                x=x,
                y=y,
                tracker_id=f"{clip_id}{tracker_id}",
            )
            tracking_ops.set_tracker_position(
                project, clip_id, f"{clip_id}{tracker_id}", 3.0, x + 30.0, y + 10.0
            )
    session, report = _apply(
        project,
        _op("add_link", clip="v", source="w", trackers=["wt1"], at=0.0),
        _op("add_link", clip="w", source="v", trackers=["vt1"], at=0.0),
    )
    assert report.ok, report.error
    assert build_render_plan(session.project) is not None


def test_add_link_keeps_working_for_an_animated_target():
    def factory() -> Project:
        project = _tracked()
        find_clip(project, "g").transform_keyframes = [
            TransformKeyframe("position_x", 0.0, 0.1),
            TransformKeyframe("position_x", 3.0, 0.9),
        ]
        return project

    _parity(
        factory,
        _op("add_link", clip="g", source="v", trackers=["t1"], at=0.0, id="lnk"),
        lambda p: tracking_ops.add_link(
            p, "g", "v", ["t1"], timeline_time=0.0, link_id="lnk"
        ),
    )


# --- remove_link, set_link_enabled, bake_link --------------------------------------------------------------------------------


def test_remove_link_and_set_link_enabled_match_the_model():
    _parity(
        _link_project,
        _op("remove_link", clip="g", link="lnk"),
        lambda p: tracking_ops.remove_link(p, "g", "lnk"),
    )
    _parity(
        _link_project,
        _op("set_link_enabled", clip="g", link="lnk", enabled=False),
        lambda p: tracking_ops.set_link_enabled(p, "g", "lnk", False),
    )


def test_link_verbs_refuse_a_locked_target_and_an_unknown_link():
    for op in (
        _op("remove_link", clip="g", link="lnk"),
        _op("set_link_enabled", clip="g", link="lnk", enabled=False),
        _op("bake_link", clip="g", link="lnk"),
    ):
        project = _link_project()
        _lock_layer(project, "g")
        _refused(project, op, match="verrouillé")
        project = _link_project()
        project.tracks[3].locked = True
        _refused(project, op, match="verrouillée")
        _refused(_link_project(), {**op, "link": "ghost"}, match="Liaison")


def test_bake_link_matches_the_model_for_a_plain_and_an_animated_target():
    _parity(
        _link_project,
        _op("bake_link", clip="g", link="lnk"),
        lambda p: tracking_ops.bake_link(p, "g", "lnk"),
    )

    def animated() -> Project:
        project = _link_project()
        find_clip(project, "g").transform_keyframes = [
            TransformKeyframe("position_x", 0.0, 0.1),
            TransformKeyframe("position_x", 3.0, 0.9),
        ]
        return project

    baked = _parity(
        animated,
        _op("bake_link", clip="g", link="lnk"),
        lambda p: tracking_ops.bake_link(p, "g", "lnk"),
    )
    assert (
        _tracking(baked, "g").links == ()
        and baked.tracks[3].clips[0].transform_keyframes
    )


def test_bake_link_refuses_a_clip_too_long_to_derive_one_keyframe_per_frame(
    monkeypatch,
):
    monkeypatch.setattr(tracking_rules, "MAX_DERIVED_FRAMES", 100)
    _refused(_link_project(), _op("bake_link", clip="g", link="lnk"), match="images")


# --- set_stabilization, remove_stabilization --------------------------------------------------------------------------------


def test_set_stabilization_creates_with_documented_defaults_and_matches_the_model():
    def direct(p: Project) -> None:
        tracking_ops.set_stabilization(
            p,
            "v",
            tracker_ids=("t1", "t2"),
            reference_index=tracking_ops.stabilization_reference(p, "v", 0.0),
        )

    _parity(_tracked, _op("set_stabilization", clip="v", trackers=["t1", "t2"]), direct)
    _parity(
        _tracked,
        _op(
            "set_stabilization",
            clip="v",
            trackers=["t1", "t2"],
            mode="position_rotation_scale",
            smoothing="custom",
            smoothing_frames=20.0,
            borders="crop",
            at=2.0,
        ),
        lambda p: tracking_ops.set_stabilization(
            p,
            "v",
            tracker_ids=("t1", "t2"),
            mode=StabilizationMode.POSITION_ROTATION_SCALE,
            smoothing="custom",
            smoothing_frames=20.0,
            borders="crop",
            reference_index=tracking_ops.stabilization_reference(p, "v", 2.0),
        ),
    )


def test_set_stabilization_modifies_an_existing_one_and_can_disable_it():
    def factory() -> Project:
        project = _tracked()
        tracking_ops.set_stabilization(project, "v", tracker_ids=("t1", "t2"))
        return project

    _parity(
        factory,
        _op("set_stabilization", clip="v", borders="black"),
        lambda p: tracking_ops.set_stabilization(p, "v", borders="black"),
    )
    _parity(
        factory,
        _op("set_stabilization", clip="v", enabled=False),
        lambda p: tracking_ops.set_stabilization(p, "v", enabled=False),
    )


def test_set_stabilization_refuses_what_the_model_would_default_or_ignore():
    project = _tracked()
    _refused(
        project, _op("set_stabilization", clip="v"), match="trackers"
    )  # le modèle choisirait les deux premiers
    _refused(
        project,
        _op("set_stabilization", clip="v", trackers=["t1"], enabled=False),
        match="trackers",
    )
    _refused(
        project,
        _op("set_stabilization", clip="v", trackers=["t1"], smoothing_frames=20.0),
        match="custom",
    )
    _refused(
        project,
        _op("set_stabilization", clip="v", trackers=["t1"], mode="position_rotation"),
        match="deux trackers",
    )
    _refused(
        project,
        _op("set_stabilization", clip="v", trackers=["t1", "ghost"]),
        match="Tracker",
    )
    _refused(
        project,
        _op("set_stabilization", clip="v", trackers=["t1", "t1"]),
        match="deux fois",
    )
    _refused(
        project,
        _op("set_stabilization", clip="v", trackers=["t1"], at=7.0),
        match="hors du clip",
    )
    for fields in (
        {"smoothing": "huge"},
        {"borders": "wrap"},
        {"mode": "spin"},
        {"smoothing": "custom", "smoothing_frames": 700.0},
    ):
        with pytest.raises(ScriptInvalid):
            parse_script(
                {
                    "kut_script": 1,
                    "ops": [
                        _op("set_stabilization", clip="v", trackers=["t1"], **fields)
                    ],
                }
            )


def test_set_stabilization_refuses_an_unanalysed_tracker():
    project = _tracked()
    tracking_ops.add_tracker(project, "v", timeline_time=0.0, tracker_id="t3")
    _refused(
        project,
        _op("set_stabilization", clip="v", trackers=["t3"]),
        match="au moins deux images",
    )


def test_set_stabilization_refuses_a_locked_clip_and_a_locked_follower():
    project = _tracked()
    project.tracks[0].locked = True
    _refused(
        project,
        _op("set_stabilization", clip="v", trackers=["t1"]),
        match="verrouillée",
    )
    project = _link_project()
    _lock_layer(project, "g")
    _refused(
        project, _op("set_stabilization", clip="v", trackers=["t1"]), match="verrouillé"
    )
    _refused(
        _tracked(),
        _op("set_stabilization", clip="lk", trackers=["t1"]),
        match="verrouillée",
    )


def test_remove_stabilization_matches_the_model_and_refuses_without_one():
    def factory() -> Project:
        project = _tracked()
        tracking_ops.set_stabilization(project, "v", tracker_ids=("t1", "t2"))
        return project

    _parity(
        factory,
        _op("remove_stabilization", clip="v"),
        lambda p: tracking_ops.remove_stabilization(p, "v"),
    )
    _refused(_tracked(), _op("remove_stabilization", clip="v"), match="pas stabilisé")
    project = factory()
    project.tracks[0].locked = True
    _refused(project, _op("remove_stabilization", clip="v"), match="verrouillée")
    project = factory()
    tracking_ops.add_link(project, "g", "v", ["t1"], timeline_time=0.0, link_id="lnk")
    _lock_layer(project, "g")
    _refused(project, _op("remove_stabilization", clip="v"), match="verrouillé")


# --- follow_reframe ----------------------------------------------------------------------------------------------------------


def _portrait() -> Project:
    """Séquence 180 × 320 : le plan horizontal 320 × 180 déborde du cadre, il y a de la marge où déplacer la fenêtre."""
    return _tracked(_project(width=180, height=320))


def test_follow_reframe_matches_the_model():
    def direct(p: Project) -> None:
        clip = find_clip(p, "v")
        trackers = [tracking_ops.tracking_of(clip).tracker("t1")]
        follow_reframe.apply_follow_reframe(
            clip, trackers, media_size=(320, 180), canvas_size=(180, 320), fps=30.0
        )

    project = _parity(
        _portrait, _op("follow_reframe", clip="v", trackers=["t1"]), direct
    )
    clip = find_clip(project, "v")
    assert clip.transform.fill and any(
        k.property_name == "pan_x" for k in clip.transform_keyframes
    )


def test_follow_reframe_keeps_the_other_animated_properties():
    def factory() -> Project:
        project = _portrait()
        find_clip(project, "v").transform_keyframes = [
            TransformKeyframe("opacity", 0.0, 0.2),
            TransformKeyframe("opacity", 3.0, 1.0),
        ]
        return project

    session, report = _apply(
        factory(), _op("follow_reframe", clip="v", trackers=["t1"])
    )
    assert report.ok and {
        k.property_name for k in find_clip(session.project, "v").transform_keyframes
    } >= {"opacity", "pan_x"}


def test_follow_reframe_refuses_a_shot_that_already_has_the_frame_shape():
    _refused(
        _tracked(),
        _op("follow_reframe", clip="v", trackers=["t1"]),
        match="forme du cadre",
    )


def test_follow_reframe_refuses_empty_trackers_nested_clips_locks_and_huge_clips(
    monkeypatch,
):
    project = _portrait()
    tracking_ops.add_tracker(project, "v", timeline_time=0.0, tracker_id="t3")
    clip = find_clip(project, "v")
    tracker = tracking_ops.tracking_of(clip).tracker("t3")
    clip.tracking = replace(
        clip.tracking,
        trackers=(*clip.tracking.trackers[:2], replace(tracker, data=TrackData())),
    )
    _refused(
        project,
        _op("follow_reframe", clip="v", trackers=["t3"]),
        match="aucune position",
    )
    _refused(
        project,
        _op("follow_reframe", clip="v", trackers=["t1", "t3"]),
        match="aucune position",
    )  # le modèle l'ignorerait
    _refused(
        _portrait(),
        _op("follow_reframe", clip="v", trackers=["t1", "t1"]),
        match="deux fois",
    )
    nested = _portrait()
    find_clip(nested, "v").sequence_id = "other"
    _refused(nested, _op("follow_reframe", clip="v", trackers=["t1"]))
    locked = _portrait()
    locked.tracks[0].locked = True
    _refused(
        locked, _op("follow_reframe", clip="v", trackers=["t1"]), match="verrouillée"
    )
    monkeypatch.setattr(tracking_rules, "MAX_DERIVED_FRAMES", 100)
    _refused(
        _portrait(), _op("follow_reframe", clip="v", trackers=["t1"]), match="images"
    )


# --- Entrées hostiles --------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fields",
    [
        {"x": float("nan")},
        {"x": float("inf")},
        {"x": 1e300},
        {"y": -1.0},
        {"at": float("nan")},
        {"at": -1.0},
        {"name": "x" * 5_000_000},
        {"name": ""},
        {"name": " padded "},
        {"x": True},
    ],
)
def test_hostile_values_are_rejected_by_the_decoder(fields):
    op = {"clip": "v", "at": 0.0, **fields}
    with pytest.raises(ScriptInvalid):
        parse_script({"kut_script": 1, "ops": [_op("add_tracker", **op)]})


def test_hostile_lists_are_rejected_by_the_decoder():
    for trackers in ([], ["t1"] * 33, "t1", [1], [{"ref": "nobody"}], [None]):
        with pytest.raises(ScriptInvalid):
            parse_script(
                {
                    "kut_script": 1,
                    "ops": [
                        _op("add_link", clip="g", source="v", trackers=trackers, at=0.0)
                    ],
                }
            )
    with pytest.raises(ScriptInvalid):
        parse_script(
            {
                "kut_script": 1,
                "ops": [_op("follow_reframe", clip="v", trackers=["t"] * 10_000)],
            }
        )


def test_a_huge_far_point_is_not_allocated():
    """Un point à l'autre bout d'un très long plan : refusé avant d'allouer la grille dense (le test échoue vite, sans gigaoctet)."""
    project = _project()
    clip = find_clip(project, "v")
    clip.source_in, clip.source_out = 0.0, 900_000.0
    project.media_assets[0].duration = 1_000_000.0
    tracking_ops.add_tracker(project, "v", timeline_time=0.0, tracker_id="t1")
    _refused(
        project,
        _op("set_tracker_position", clip="v", tracker="t1", at=800_000.0, x=1.0, y=1.0),
        match="plage",
    )


# --- Atomicité, annulation, bout en bout ---------------------------------------------------------------------------------------


def test_a_failing_script_leaves_the_project_unchanged_and_a_good_one_is_one_undo_step():
    project = _tracked()
    _refused(
        project,
        _op("add_link", clip="g", source="v", trackers=["t1", "t2"], at=0.0),
        _op("add_link", clip="g", source="v", trackers=["t1"], at=0.0),
    )
    session = Session(_tracked())
    before = _snapshot(session.project)
    report = apply_script(
        session,
        parse_script(
            {
                "kut_script": 1,
                "ops": [
                    _op(
                        "add_link",
                        clip="g",
                        source="v",
                        trackers=["t1", "t2"],
                        at=0.0,
                        **{"as": "lk1"},
                    ),
                    _op("bake_link", clip="g", link={"ref": "lk1"}),
                    _op("set_stabilization", clip="v", trackers=["t1", "t2"]),
                ],
            }
        ),
    )
    assert report.ok, report.error
    assert (
        _tracking(session.project, "v").stabilization is not None
        and _tracking(session.project, "g").links == ()
    )
    assert session.undo()
    assert _snapshot(session.project) == before
    assert session.redo() and _tracking(session.project, "v").stabilization is not None


def test_a_whole_script_builds_tracking_from_explicit_points_only():
    """Sans aucune analyse : un point posé, un autre, une liaison, de la stabilisation, puis une conversion en images clés."""
    ops = [
        _op("add_tracker", clip="v", at=0.0, x=100.0, y=50.0, **{"as": "a"}),
        _op("add_tracker", clip="v", at=0.0, x=200.0, y=120.0, **{"as": "b"}),
        _op(
            "set_tracker_position",
            clip="v",
            tracker={"ref": "a"},
            at=3.0,
            x=130.0,
            y=60.0,
        ),
        _op(
            "set_tracker_position",
            clip="v",
            tracker={"ref": "b"},
            at=3.0,
            x=230.0,
            y=130.0,
        ),
        _op(
            "update_tracker",
            clip="v",
            tracker={"ref": "a"},
            name="Coin haut",
            color="#FFAA00",
        ),
        _op(
            "add_link",
            clip="g",
            source="v",
            trackers=[{"ref": "a"}, {"ref": "b"}],
            at=0.0,
            rotation=True,
            **{"as": "lk"},
        ),
        _op(
            "set_stabilization",
            clip="v",
            trackers=[{"ref": "a"}, {"ref": "b"}],
            mode="position_rotation",
        ),
        _op("bake_link", clip="g", link={"ref": "lk"}),
        _op("remove_tracker", clip="v", tracker={"ref": "b"}),
    ]
    session = Session(_project())
    report = apply_script(session, parse_script({"kut_script": 1, "ops": ops}))
    assert report.ok, report.error
    tracking = _tracking(session.project, "v")
    assert (
        [t.id for t in tracking.trackers] == ["a"]
        and tracking.trackers[0].name == "Coin haut"
        and tracking.stabilization.tracker_ids == ("a",)
    )


def test_the_json_form_round_trips_through_the_decoder():
    text = json.dumps(
        {
            "kut_script": 1,
            "ops": [_op("add_tracker", clip="v", at=0.5, x=1.0, y=2.0, id="t1")],
        }
    )
    session = Session(_project())
    assert apply_script(session, parse_script(json.loads(text))).ok
    assert math.isclose(
        _tracking(session.project, "v").tracker("t1").data.sample(15).x, 1.0
    )
