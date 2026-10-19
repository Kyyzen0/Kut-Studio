"""Format déclaratif : audio avancé, première partie (lot 7 de l'extension 0.8.0) : rôle d'une piste, courbe de volume, ordre des inserts.

Chaque verbe fait ce que l'interface fait (menu ⋯ d'une piste audio, bande de courbe, boîtes d'inserts) ; le test de parité fait le même appel
direct. Les valeurs hors bornes, les instants déjà pris, les déplacements qui sortent de la chaîne sont refusés (jamais ramenés), et les verrous
valent pour toutes les entrées. Le ducking est dans ``test_script_verbs_audio_ducking.py``.
"""

from __future__ import annotations

import copy
import math

import pytest

from core.audio_automation import AudioAutomationService, TrackRole
from core.audio_buses import add_bus, add_bus_insert, move_bus_insert
from core.audio_effects_model import add_track_insert, move_track_insert
from core.project_model import Clip, MediaAsset, Project, Track
from core.scripting import ScriptInvalid, Session, apply_script, parse_script
from core.scripting import audio_rules
from core.scripting.schema import json_schema
from core.scripting.verbs import VERBS

AUTOMATION_VERBS = (
    "set_track_role", "add_automation_point", "update_automation_point", "move_automation_point", "remove_automation_point",
    "clear_automation", "set_automation_curve", "move_track_insert", "move_bus_insert",
)


def _project() -> Project:
    project = Project(
        "Démo", width=320, height=180, fps=30.0,
        media_assets=[MediaAsset("m", "/x/intro.mp4", "intro", 20.0, 320, 180, 30.0, "video", has_audio=True)],
        tracks=[
            Track("V1", "V1", "video", clips=[Clip("a", "m", "V1", 0.0, 0.0, 6.0)]),
            Track("A1", "Musique", "audio"),
            Track("A2", "Voix", "audio"),
            Track("A3", "Verrou", "audio", locked=True),
            Track("S1", "S1", "subtitle"),
            Track("G1", "G1", "graphics"),
        ],
    )
    return project


def _op(verb: str, /, **fields) -> dict:
    return {"op": verb, **fields}


def _apply(project: Project, *ops: dict):
    session = Session(project)
    return session, apply_script(session, parse_script({"kut_script": 1, "ops": list(ops)}))


def _state(project: Project):
    return copy.deepcopy(project.tracks), copy.deepcopy(project.buses)


def _refused(project: Project, *ops: dict):
    before = _state(project)
    session, report = _apply(project, *ops)
    assert not report.ok, "le script aurait dû être refusé"
    assert _state(session.project) == before
    return report


def _invalid(*ops: dict) -> None:
    with pytest.raises(ScriptInvalid):
        parse_script({"kut_script": 1, "ops": list(ops)})


def _points(project: Project, track: str = "A1") -> list[tuple[float, float, float]]:
    return [(p.time_seconds, p.gain_db, p.fade_seconds) for p in next(t for t in project.tracks if t.id == track).automation.points]


def test_the_verbs_are_in_the_table_with_the_right_shape():
    assert set(AUTOMATION_VERBS) <= set(VERBS)
    for name in AUTOMATION_VERBS:
        assert VERBS[name].creates is None and VERBS[name].doc and VERBS[name].group == "audio"
    assert set(AUTOMATION_VERBS) <= {item["title"] for item in json_schema()["properties"]["ops"]["items"]["oneOf"]}


# --- Rôle -----------------------------------------------------------------------------------------------------------------


def test_set_track_role_matches_the_track_menu_and_is_undoable():
    session, report = _apply(_project(), _op("set_track_role", track="A1", role="music"), _op("set_track_role", track="Voix", role="voice"))
    assert report.ok, report.error
    reference = _project()
    service = AudioAutomationService()
    service.set_track_role(reference, "A1", TrackRole.MUSIC)
    service.set_track_role(reference, "A2", TrackRole.VOICE)
    assert session.project.tracks == reference.tracks
    assert [t.audio_role for t in session.project.tracks if t.id in ("A1", "A2")] == ["music", "voice"]   # une chaîne, comme le modèle
    assert session.undo() and all(t.audio_role == "other" for t in session.project.tracks)


def test_set_track_role_refusals():
    _invalid(_op("set_track_role", track="A1", role="chef"))
    assert "audio" in str(_refused(_project(), _op("set_track_role", track="V1", role="music")).error)      # le menu n'existe que pour les pistes audio
    _refused(_project(), _op("set_track_role", track="S1", role="music"))
    _refused(_project(), _op("set_track_role", track="G1", role="music"))
    assert "verrouill" in str(_refused(_project(), _op("set_track_role", track="A3", role="music")).error)
    _refused(_project(), _op("set_track_role", track="inconnue", role="music"))


# --- Courbe de volume : parité ---------------------------------------------------------------------------------------------


def test_curve_verbs_match_the_service_calls_of_the_lane():
    session, report = _apply(
        _project(),
        _op("add_automation_point", track="A1", at=2.0, gain_db=-6.0, fade_seconds=0.5),
        _op("add_automation_point", track="A1", at=0.0, gain_db=0.0),
        _op("add_automation_point", track="A1", at=4.0, gain_db=-12.0),
        _op("update_automation_point", track="A1", at=2.0, gain_db=-9.0),
        _op("move_automation_point", track="A1", at=4.0, to=5.0, gain_db=-3.0),
        _op("remove_automation_point", track="A1", at=0.0),
    )
    assert report.ok, report.error
    reference = _project()
    service = AudioAutomationService()
    service.add_automation_point(reference, "A1", 2.0, -6.0, 0.5)
    service.add_automation_point(reference, "A1", 0.0, 0.0, 0.0)
    service.add_automation_point(reference, "A1", 4.0, -12.0, 0.0)
    service.update_automation_point(reference, "A1", 2.0, gain_db=-9.0, fade_seconds=None)
    service.move_automation_point(reference, "A1", 4.0, 5.0, -3.0)
    service.remove_automation_point(reference, "A1", 0.0)
    assert session.project.tracks == reference.tracks
    assert _points(session.project) == [(2.0, -9.0, 0.5), (5.0, -3.0, 0.0)]
    assert session.undo() and _points(session.project) == []


def test_clear_automation_matches_the_lane_menu():
    project = _project()
    AudioAutomationService().add_automation_point(project, "A1", 1.0, -3.0)
    session, report = _apply(project, _op("clear_automation", track="A1"))
    assert report.ok, report.error
    assert _points(session.project) == [] and session.undo() and _points(session.project) == [(1.0, -3.0, 0.0)]


def test_move_keeps_the_gain_when_none_is_given_and_may_stay_in_place():
    project = _project()
    AudioAutomationService().add_automation_point(project, "A1", 1.0, -3.0, 0.25)
    assert _apply(project, _op("move_automation_point", track="A1", at=1.0, to=2.0))[1].ok
    assert _points(project) == [(2.0, -3.0, 0.25)]                                   # gain et maintien conservés
    assert _apply(project, _op("move_automation_point", track="A1", at=2.0, to=2.0, gain_db=-4.0))[1].ok
    assert _points(project) == [(2.0, -4.0, 0.25)]


# --- Courbe de volume : refus ----------------------------------------------------------------------------------------------


def test_a_point_already_at_that_instant_is_refused_not_replaced_silently():
    project = _project()
    AudioAutomationService().add_automation_point(project, "A1", 1.0, -3.0)
    assert "déjà" in str(_refused(project, _op("add_automation_point", track="A1", at=1.0, gain_db=-9.0)).error)
    _refused(project, _op("add_automation_point", track="A1", at=1.0000003, gain_db=-9.0))        # trop proche pour qu'un script les distingue
    assert _points(project) == [(1.0, -3.0, 0.0)]


def test_move_to_an_instant_taken_by_another_point_is_refused():
    project = _project()
    service = AudioAutomationService()
    service.add_automation_point(project, "A1", 1.0, -3.0)
    service.add_automation_point(project, "A1", 2.0, -6.0)
    assert "déjà" in str(_refused(project, _op("move_automation_point", track="A1", at=1.0, to=2.0)).error)


def test_update_needs_something_and_a_point_must_exist():
    project = _project()
    AudioAutomationService().add_automation_point(project, "A1", 1.0, -3.0)
    assert "gain" in str(_refused(project, _op("update_automation_point", track="A1", at=1.0)).error)
    for verb, extra in (("update_automation_point", {"gain_db": -1.0}), ("move_automation_point", {"to": 3.0}), ("remove_automation_point", {})):
        report = _refused(project, _op(verb, track="A1", at=1.5, **extra))
        assert "point" in str(report.error)


def test_the_instant_read_from_describe_finds_the_point_even_when_rounded():
    project = _project()
    AudioAutomationService().add_automation_point(project, "A1", 1.0 / 3.0, -3.0)         # 0,333333… : décrit arrondi à 6 décimales
    assert _apply(project, _op("update_automation_point", track="A1", at=0.333333, gain_db=-5.0))[1].ok
    assert _apply(project, _op("remove_automation_point", track="A1", at=0.333333))[1].ok
    assert _points(project) == []


def test_two_points_too_close_to_be_told_apart_make_the_lookup_ambiguous():
    project = _project()
    service = AudioAutomationService()
    service.add_automation_point(project, "A1", 1.0, -3.0)
    service.add_automation_point(project, "A1", 1.0000004, -6.0)                          # le modèle les distingue (> 1e-9), la description non
    report = _refused(project, _op("update_automation_point", track="A1", at=1.0, gain_db=0.0))
    assert "Plusieurs" in str(report.error)
    _refused(project, _op("remove_automation_point", track="A1", at=1.0))


@pytest.mark.parametrize("verb,extra", [
    ("add_automation_point", {"at": 1.0, "gain_db": -3.0}), ("update_automation_point", {"at": 1.0, "gain_db": -1.0}),
    ("move_automation_point", {"at": 1.0, "to": 2.0}), ("remove_automation_point", {"at": 1.0}), ("clear_automation", {}),
    ("set_automation_curve", {"points": [{"time_seconds": 0.0, "gain_db": 0.0}]}), ("set_track_role", {"role": "voice"}),
])
def test_a_locked_track_and_a_non_audio_track_refuse_every_curve_verb(verb, extra):
    locked = _project()
    locked.tracks[3].automation.add_point(1.0, -3.0)                                  # le modèle le permettrait : le verrou est du verbe
    report = _refused(locked, _op(verb, track="A3", **extra))
    assert "verrouill" in str(report.error)
    for other in ("V1", "S1", "G1"):
        report = _refused(_project(), _op(verb, track=other, **extra))
        assert "audio" in str(report.error)


@pytest.mark.parametrize("field,value", [
    ("gain_db", 12.01), ("gain_db", -24.01), ("gain_db", math.nan), ("gain_db", math.inf), ("gain_db", True), ("gain_db", "3"),
    ("fade_seconds", 5.01), ("fade_seconds", -0.1), ("fade_seconds", math.nan), ("at", -0.001), ("at", 1_000_001.0), ("at", math.inf),
])
def test_out_of_range_values_are_refused_at_reading(field, value):
    fields = {"track": "A1", "at": 1.0, "gain_db": -3.0, "fade_seconds": 0.0}
    fields[field] = value
    _invalid(_op("add_automation_point", **fields))


def test_the_bounds_are_inclusive_and_those_of_the_lane():
    for value in (-24.0, 12.0):
        assert _apply(_project(), _op("add_automation_point", track="A1", at=1.0, gain_db=value))[1].ok
    assert _apply(_project(), _op("add_automation_point", track="A1", at=0.0, gain_db=0.0, fade_seconds=5.0))[1].ok


def test_a_curve_is_capped_before_a_point_is_created(monkeypatch):
    monkeypatch.setattr(audio_rules, "MAX_AUTOMATION_POINTS", 2)
    project = _project()
    service = AudioAutomationService()
    service.add_automation_point(project, "A1", 1.0, -3.0)
    service.add_automation_point(project, "A1", 2.0, -3.0)
    report = _refused(project, _op("add_automation_point", track="A1", at=3.0, gain_db=-1.0))
    assert "limite" in str(report.error)
    assert _apply(project, _op("update_automation_point", track="A1", at=2.0, gain_db=-1.0))[1].ok       # modifier ne crée rien
    assert _apply(project, _op("move_automation_point", track="A1", at=2.0, to=2.5))[1].ok


# --- Remplacer toute la courbe --------------------------------------------------------------------------------------------


def test_set_automation_curve_replaces_the_curve_like_clear_then_add():
    project = _project()
    AudioAutomationService().add_automation_point(project, "A1", 9.0, -20.0)
    points = [{"time_seconds": 0.0, "gain_db": 0.0}, {"time_seconds": 2.0, "gain_db": -6.0, "fade_seconds": 0.5}]
    session, report = _apply(project, _op("set_automation_curve", track="A1", points=points))
    assert report.ok, report.error
    reference = _project()
    service = AudioAutomationService()
    service.add_automation_point(reference, "A1", 0.0, 0.0, 0.0)
    service.add_automation_point(reference, "A1", 2.0, -6.0, 0.5)
    assert session.project.tracks == reference.tracks
    assert session.undo() and _points(session.project) == [(9.0, -20.0, 0.0)]
    assert _apply(project, _op("set_automation_curve", track="A1", points=[]))[1].ok and _points(project) == []


def test_set_automation_curve_refuses_unordered_or_duplicate_instants_and_keeps_the_old_curve():
    project = _project()
    AudioAutomationService().add_automation_point(project, "A1", 9.0, -20.0)
    for points in (
        [{"time_seconds": 2.0, "gain_db": 0.0}, {"time_seconds": 1.0, "gain_db": 0.0}],
        [{"time_seconds": 1.0, "gain_db": 0.0}, {"time_seconds": 1.0, "gain_db": -3.0}],
        [{"time_seconds": 1.0, "gain_db": 0.0}, {"time_seconds": 1.0000000005, "gain_db": -3.0}],
    ):
        assert "croissants" in str(_refused(project, _op("set_automation_curve", track="A1", points=points)).error)
    assert _points(project) == [(9.0, -20.0, 0.0)]


def test_set_automation_curve_reading_bounds():
    point = {"time_seconds": 0.0, "gain_db": 0.0}
    _invalid(_op("set_automation_curve", track="A1", points=[point] * 1001))                  # plafond annoncé
    _invalid(_op("set_automation_curve", track="A1", points=[{"time_seconds": 0.0, "gain_db": 13.0}]))
    _invalid(_op("set_automation_curve", track="A1", points=[{"time_seconds": 0.0}]))
    _invalid(_op("set_automation_curve", track="A1", points=[{"time_seconds": 0.0, "gain_db": 0.0, "extra": 1}]))
    _invalid(_op("set_automation_curve", track="A1", points="beaucoup"))
    ok = [{"time_seconds": float(i), "gain_db": 0.0} for i in range(1000)]
    assert _apply(_project(), _op("set_automation_curve", track="A1", points=ok))[1].ok


def test_set_automation_curve_is_atomic_with_the_rest_of_the_script():
    project = _project()
    before = _state(project)
    session, report = _apply(
        project, _op("set_automation_curve", track="A1", points=[{"time_seconds": 0.0, "gain_db": 0.0}]), _op("add_automation_point", track="A3", at=1.0, gain_db=0.0),
    )
    assert not report.ok and report.failed_index == 1
    assert _state(session.project) == before


# --- Bout en bout ---------------------------------------------------------------------------------------------------------


def test_a_script_by_names_and_undo_in_one_step():
    session, report = _apply(
        _project(),
        _op("set_track_role", track="Musique", role="music"),
        _op("add_automation_point", track="Musique", at=0.0, gain_db=0.0),
        _op("add_automation_point", track="Musique", at=1.0, gain_db=-12.0, fade_seconds=0.2),
        _op("move_automation_point", track="Musique", at=1.0, to=1.5),
    )
    assert report.ok, report.error
    assert _points(session.project) == [(0.0, 0.0, 0.0), (1.5, -12.0, 0.2)]
    assert session.undo() and _points(session.project) == [] and session.project.tracks[1].audio_role == "other"
    assert session.redo() and len(_points(session.project)) == 2


def test_hostile_inputs_are_bounded_at_parse():
    with pytest.raises(ScriptInvalid) as caught:
        parse_script({"kut_script": 1, "ops": [_op("set_track_role", track="x" * 5_000_000, role="music")]})
    assert len(str(caught.value)) < 10_000
    _invalid(_op("add_automation_point", track="A1", at=1.0, gain_db=0.0, extra=1))
    _invalid(_op("move_track_insert", track="A1", insert="i", delta=1001))
    _invalid(_op("move_bus_insert", bus="b", insert="i", delta=-1001))
    _invalid(_op("move_track_insert", track="A1", insert="i", delta=1.5))


# --- Ordre des inserts ----------------------------------------------------------------------------------------------------


def _with_inserts() -> Project:
    project = _project()
    for index, kind in enumerate(("compressor", "parametric_eq", "limiter")):
        add_track_insert(project, "A1", kind, effect_id=f"t{index}")
    add_bus(project, "Voix", bus_id="b1")
    for index, kind in enumerate(("compressor", "parametric_eq", "limiter")):
        add_bus_insert(project, "b1", kind, effect_id=f"u{index}")
    return project


def test_move_inserts_match_the_model_calls_of_the_dialogs():
    session, report = _apply(
        _with_inserts(), _op("move_track_insert", track="A1", insert="t2", delta=-2), _op("move_bus_insert", bus="b1", insert="u0", delta=1),
    )
    assert report.ok, report.error
    reference = _with_inserts()
    move_track_insert(reference, "A1", "t2", -2)
    move_bus_insert(reference, "b1", "u0", 1)
    assert [i.id for i in session.project.tracks[1].inserts] == ["t2", "t0", "t1"]
    assert [i.id for i in session.project.buses[0].inserts] == ["u1", "u0", "u2"]
    assert session.project.tracks == reference.tracks and session.project.buses == reference.buses
    assert session.undo() and [i.id for i in session.project.tracks[1].inserts] == ["t0", "t1", "t2"]


@pytest.mark.parametrize("verb,target,insert,delta", [
    ("move_track_insert", "track", "t0", -1), ("move_track_insert", "track", "t2", 1), ("move_track_insert", "track", "t1", 0),
    ("move_track_insert", "track", "t1", 5), ("move_bus_insert", "bus", "u0", -1), ("move_bus_insert", "bus", "u2", 1),
    ("move_bus_insert", "bus", "u1", 0), ("move_bus_insert", "bus", "u1", -7),
])
def test_a_move_that_would_leave_the_chain_is_refused_instead_of_clamped(verb, target, insert, delta):
    owner = {"track": "A1"} if target == "track" else {"bus": "b1"}
    report = _refused(_with_inserts(), _op(verb, **owner, insert=insert, delta=delta))
    assert "déplacement impossible" in str(report.error)


def test_move_inserts_refusals():
    locked = _with_inserts()
    add_track_insert(locked, "A2", "compressor", effect_id="x0")
    add_track_insert(locked, "A2", "limiter", effect_id="x1")
    locked.tracks[2].locked = True
    assert "verrouill" in str(_refused(locked, _op("move_track_insert", track="A2", insert="x0", delta=1)).error)
    _refused(_with_inserts(), _op("move_track_insert", track="S1", insert="t0", delta=1))
    _refused(_with_inserts(), _op("move_track_insert", track="A1", insert="inconnu", delta=1))
    _refused(_with_inserts(), _op("move_bus_insert", bus="b1", insert="inconnu", delta=1))
    _refused(_with_inserts(), _op("move_bus_insert", bus="b9", insert="u0", delta=1))
    graphics = _with_inserts()
    graphics.tracks[5].inserts = list(graphics.tracks[1].inserts)                     # un fichier abîmé : le rendu ne jouerait jamais ces inserts
    assert "audio et vidéo" in str(_refused(graphics, _op("move_track_insert", track="G1", insert="t0", delta=1)).error)


def test_move_track_insert_works_on_a_video_track():
    project = _with_inserts()
    add_track_insert(project, "V1", "compressor", effect_id="v0")
    add_track_insert(project, "V1", "limiter", effect_id="v1")
    assert _apply(project, _op("move_track_insert", track="V1", insert="v1", delta=-1))[1].ok
    assert [i.id for i in project.tracks[0].inserts] == ["v1", "v0"]
