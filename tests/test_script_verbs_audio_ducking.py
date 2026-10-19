"""Format déclaratif : audio avancé, seconde partie (lot 7 de l'extension 0.8.0) : associations de ducking.

``add_ducking_sidechain`` fait ce que le menu « Baisser sous… » de la piste fait (deux pistes audio déverrouillées, réglage par défaut du modèle), avec un
identifiant ``id`` / ``as`` comme les autres créations. Une boucle, un doublon de paire, un identifiant pris, la limite du projet (toutes séquences),
la piste « musique » verrouillée sont refusés ; un réglage hors bornes est refusé à la lecture, jamais écrêté.
"""

from __future__ import annotations

import copy
import math

import pytest

from core.audio_automation import DUCKING_PRESETS, AudioAutomationService, DuckingConfig, DuckingSidechain
from core.project_model import Clip, MediaAsset, Project, Sequence, Track
from core.scripting import ScriptInvalid, Session, apply_script, created_ids, parse_script
from core.scripting import audio_rules
from core.scripting.describe import describe
from core.scripting.schema import json_schema
from core.scripting.verbs import VERBS

DUCKING_VERBS = ("add_ducking_sidechain", "remove_ducking_sidechain", "set_ducking_enabled", "update_ducking_config")


def _project() -> Project:
    voice = Track("A2", "Voix", "audio", audio_role="voice", clips=[Clip("voix", "s", "A2", 0.0, 0.0, 4.0)])
    music = Track("A1", "Musique", "audio", audio_role="music", clips=[Clip("son", "s", "A1", 0.0, 0.0, 4.0)])
    return Project(
        "Démo", width=320, height=180, fps=30.0,
        media_assets=[MediaAsset("s", "/x/son.wav", "son", 20.0, 0, 0, 0.0, "audio", has_audio=True)],
        tracks=[
            Track("V1", "V1", "video"), music, voice, Track("A3", "Autre", "audio"), Track("A4", "Verrou", "audio", locked=True),
            Track("S1", "S1", "subtitle"), Track("G1", "G1", "graphics"),
        ],
    )


def _op(verb: str, /, **fields) -> dict:
    return {"op": verb, **fields}


def _apply(project: Project, *ops: dict):
    session = Session(project)
    return session, apply_script(session, parse_script({"kut_script": 1, "ops": list(ops)}))


def _state(project: Project):
    return copy.deepcopy(project.tracks), copy.deepcopy(project.ducking_sidechains)


def _refused(project: Project, *ops: dict):
    before = _state(project)
    session, report = _apply(project, *ops)
    assert not report.ok, "le script aurait dû être refusé"
    assert _state(session.project) == before
    return report


def _invalid(*ops: dict) -> None:
    with pytest.raises(ScriptInvalid):
        parse_script({"kut_script": 1, "ops": list(ops)})


def _pairs(project: Project) -> list[tuple[str, str]]:
    return [(item.music_track_id, item.voice_track_id) for item in project.ducking_sidechains]


def test_the_verbs_are_in_the_table_with_the_right_shape():
    assert set(DUCKING_VERBS) <= set(VERBS)
    assert VERBS["add_ducking_sidechain"].creates == "ducking" and VERBS["add_ducking_sidechain"].creates_scope is None
    assert "id" in VERBS["add_ducking_sidechain"].accepts and "as" in VERBS["add_ducking_sidechain"].accepts
    for name in set(DUCKING_VERBS) - {"add_ducking_sidechain"}:
        assert VERBS[name].creates is None
    for name in DUCKING_VERBS:
        assert VERBS[name].doc and VERBS[name].group == "audio"
    assert set(DUCKING_VERBS) <= {item["title"] for item in json_schema()["properties"]["ops"]["items"]["oneOf"]}


# --- Création -------------------------------------------------------------------------------------------------------------


def test_add_ducking_sidechain_is_the_menu_with_the_default_model_config():
    session, report = _apply(_project(), _op("add_ducking_sidechain", music="A1", voice="A2", id="dk1"))
    assert report.ok, report.error
    reference = _project()
    expected = AudioAutomationService().add_ducking_sidechain(reference, "A1", "A2", config=DuckingConfig(), sidechain_id="dk1")
    assert session.project.ducking_sidechains == [expected]
    assert created_ids(parse_script({"kut_script": 1, "ops": [_op("add_ducking_sidechain", music="A1", voice="A2", id="dk1")]}), report) == [
        {"op": "0:add_ducking_sidechain", "id": "dk1"}]
    assert session.undo() and session.project.ducking_sidechains == []


def test_without_id_the_model_names_it_like_before():
    session, report = _apply(_project(), _op("add_ducking_sidechain", music="Musique", voice="Voix"))
    assert report.ok, report.error
    assert session.project.ducking_sidechains[0].id.startswith("duck-")


def test_preset_and_fields_set_the_config_and_fields_win_over_the_preset():
    session, report = _apply(
        _project(),
        _op("add_ducking_sidechain", music="A1", voice="A2", preset="gentle", reduction_db=20.0),
        _op("add_ducking_sidechain", music="A3", voice="A2", threshold_db=-40.0, release_seconds=2.0),
    )
    assert report.ok, report.error
    first, second = session.project.ducking_sidechains
    assert first.config == DuckingConfig(threshold_db=-30.0, reduction_db=20.0, attack_seconds=0.08, release_seconds=0.6)
    assert first.config.reduction_db != DUCKING_PRESETS["gentle"].reduction_db
    assert second.config == DuckingConfig(threshold_db=-40.0, release_seconds=2.0)


def test_roles_are_not_required_like_the_menu_which_ignores_them():
    project = _project()
    project.tracks[1].audio_role = "other"
    assert _apply(project, _op("add_ducking_sidechain", music="A1", voice="A3"))[1].ok            # ni musique ni voix : l'interface et l'export le permettent


def test_aliases_end_to_end_with_the_other_ducking_verbs():
    session, report = _apply(
        _project(),
        _op("add_ducking_sidechain", music="A1", voice="A2", **{"as": "d"}),
        _op("update_ducking_config", ducking={"ref": "d"}, reduction_db=15.0),
        _op("set_ducking_enabled", ducking={"ref": "d"}, enabled=False),
    )
    assert report.ok, report.error
    item = session.project.ducking_sidechains[0]
    assert item.id == "d" and item.config.reduction_db == 15.0 and item.enabled is False
    assert _apply(session.project, _op("remove_ducking_sidechain", ducking="d"))[1].ok and session.project.ducking_sidechains == []
    again = _apply(_project(), _op("add_ducking_sidechain", music="A1", voice="A2", id="k9"))[0].project
    assert describe(again)["sequences"][0]["ducking_sidechains"][0]["id"] == "k9"
    # Un alias de piste n'est pas un alias d'association.
    _invalid(_op("add_track_insert", track="A1", effect="compressor", **{"as": "ins"}), _op("remove_ducking_sidechain", ducking={"ref": "ins"}))


# --- Création : refus -----------------------------------------------------------------------------------------------------


def test_a_track_cannot_duck_itself_and_a_pair_cannot_be_doubled():
    _refused(_project(), _op("add_ducking_sidechain", music="A1", voice="A1"))
    project = _project()
    assert _apply(project, _op("add_ducking_sidechain", music="A1", voice="A2"))[1].ok
    assert "existe déjà" in str(_refused(project, _op("add_ducking_sidechain", music="A1", voice="A2")).error)


def test_a_loop_is_refused_on_the_resulting_graph_not_only_for_two_tracks():
    project = _project()
    assert _apply(project, _op("add_ducking_sidechain", music="A1", voice="A2"))[1].ok
    assert "boucle" in str(_refused(project, _op("add_ducking_sidechain", music="A2", voice="A1")).error)           # A sous B, B sous A
    assert _apply(project, _op("add_ducking_sidechain", music="A2", voice="A3"))[1].ok                                # A1 <- A2 <- A3
    assert "boucle" in str(_refused(project, _op("add_ducking_sidechain", music="A3", voice="A1")).error)           # A3 <- A1 ferme A1 <- A2 <- A3 <- A1
    ok = _project()
    assert _apply(ok, _op("add_ducking_sidechain", music="A1", voice="A2"), _op("add_ducking_sidechain", music="A3", voice="A2"))[1].ok   # deux musiques, une voix


def test_a_disabled_association_still_counts_for_the_loop():
    session, report = _apply(_project(), _op("add_ducking_sidechain", music="A1", voice="A2", id="duck_off"), _op("set_ducking_enabled", ducking="duck_off", enabled=False))
    assert report.ok, report.error
    assert "boucle" in str(_refused(session.project, _op("add_ducking_sidechain", music="A2", voice="A1")).error)


@pytest.mark.parametrize("music,voice", [("V1", "A2"), ("A1", "V1"), ("S1", "A2"), ("A1", "G1")])
def test_only_audio_tracks_can_be_paired(music, voice):
    assert "audio" in str(_refused(_project(), _op("add_ducking_sidechain", music=music, voice=voice)).error)


def test_both_tracks_must_be_unlocked_for_a_new_association_like_the_menu():
    assert "verrouill" in str(_refused(_project(), _op("add_ducking_sidechain", music="A4", voice="A2")).error)
    assert "verrouill" in str(_refused(_project(), _op("add_ducking_sidechain", music="A1", voice="A4")).error)


def test_an_imposed_id_must_be_free_in_every_sequence():
    project = _project()
    other = Sequence("seq2", "Autre", tracks=[Track("B1", "B1", "audio"), Track("B2", "B2", "audio")],
                     ducking_sidechains=[DuckingSidechain("taken", "B1", "B2")])
    project.sequences.append(other)
    assert "existe déjà" in str(_refused(project, _op("add_ducking_sidechain", music="A1", voice="A2", id="taken")).error)
    assert _apply(project, _op("add_ducking_sidechain", music="A1", voice="A2", id="free"))[1].ok
    again = _refused(project, _op("add_ducking_sidechain", music="A3", voice="A2", id="free"))
    assert "existe déjà" in str(again.error)
    _invalid(_op("add_ducking_sidechain", music="A1", voice="A2", id="a-b"))                    # forme interdite


def test_the_limit_counts_every_sequence_and_is_checked_before_creating(monkeypatch):
    monkeypatch.setattr(audio_rules, "MAX_SIDECHAINS", 2)
    project = _project()
    project.sequences.append(Sequence("seq2", "Autre", tracks=[Track("B1", "B1", "audio"), Track("B2", "B2", "audio")],
                                      ducking_sidechains=[DuckingSidechain("o1", "B1", "B2")]))
    assert _apply(project, _op("add_ducking_sidechain", music="A1", voice="A2"))[1].ok                       # 1 + 1 = 2 : la limite
    assert "limite" in str(_refused(project, _op("add_ducking_sidechain", music="A3", voice="A2")).error)


def test_duck_music_under_voice_obeys_the_same_limit(monkeypatch):
    monkeypatch.setattr(audio_rules, "MAX_SIDECHAINS", 0)
    assert "limite" in str(_refused(_project(), _op("duck_music_under_voice")).error)
    monkeypatch.setattr(audio_rules, "MAX_SIDECHAINS", 1)
    project = _project()
    assert _apply(project, _op("duck_music_under_voice"))[1].ok                                              # une paire neuve : 1
    assert _apply(project, _op("duck_music_under_voice", preset="gentle"))[1].ok                             # la paire existe : rien de neuf, la limite n'y change rien
    assert len(project.ducking_sidechains) == 1


def test_duck_music_under_voice_refuses_a_pair_that_would_close_a_loop_and_changes_nothing():
    project = _project()
    project.ducking_sidechains.append(DuckingSidechain("manuel", "A2", "A1"))            # la voix baisse déjà sous la musique
    report = _refused(project, _op("duck_music_under_voice"))
    assert "boucle" in str(report.error)
    project.ducking_sidechains[0] = DuckingSidechain("manuel", "A2", "A1", enabled=False)  # désactivée : elle compte encore
    assert "boucle" in str(_refused(project, _op("duck_music_under_voice")).error)


def test_duck_music_under_voice_ignores_existing_pairs_and_checks_new_pairs_together():
    project = _project()
    assert _apply(project, _op("duck_music_under_voice"))[1].ok
    assert _apply(project, _op("duck_music_under_voice", preset="gentle"))[1].ok         # la paire existe : pas de nouvelle boucle à craindre
    chain = _project()
    chain.tracks[3].audio_role = "music"
    chain.ducking_sidechains.append(DuckingSidechain("m", "A2", "A3"))                    # A2 (voix) baisse sous A3 (musique)
    assert "boucle" in str(_refused(chain, _op("duck_music_under_voice")).error)         # A3 sous A2 fermerait la boucle


@pytest.mark.parametrize("field,value", [
    ("threshold_db", 0.01), ("threshold_db", -60.01), ("reduction_db", 36.01), ("reduction_db", -0.01), ("attack_seconds", 0.004),
    ("attack_seconds", 5.01), ("release_seconds", 0.009), ("release_seconds", 10.01), ("threshold_db", math.nan), ("reduction_db", math.inf),
    ("attack_seconds", True), ("release_seconds", "1"),
])
def test_a_config_out_of_bounds_is_refused_at_reading_never_clamped(field, value):
    _invalid(_op("add_ducking_sidechain", music="A1", voice="A2", **{field: value}))
    _invalid(_op("update_ducking_config", ducking="d", **{field: value}))


def test_the_bounds_are_inclusive():
    edge = dict(threshold_db=-60.0, reduction_db=36.0, attack_seconds=0.005, release_seconds=10.0)
    session, report = _apply(_project(), _op("add_ducking_sidechain", music="A1", voice="A2", **edge))
    assert report.ok, report.error
    assert session.project.ducking_sidechains[0].config == DuckingConfig(**edge)
    high = dict(threshold_db=0.0, reduction_db=0.0, attack_seconds=5.0, release_seconds=0.01)
    assert _apply(_project(), _op("add_ducking_sidechain", music="A1", voice="A2", **high))[1].ok
    _invalid(_op("add_ducking_sidechain", music="A1", voice="A2", preset="enorme"))


# --- Retirer, activer, régler ---------------------------------------------------------------------------------------------


def _with_pair(*, music_locked: bool = False, voice_locked: bool = False) -> Project:
    project = _project()
    project.tracks[1].locked = music_locked
    project.tracks[2].locked = voice_locked
    project.ducking_sidechains.append(DuckingSidechain("d1", "A1", "A2", DuckingConfig(reduction_db=9.0)))
    return project


def test_update_changes_only_the_given_fields_and_matches_the_model_call():
    session, report = _apply(_with_pair(), _op("update_ducking_config", ducking="d1", threshold_db=-40.0))
    assert report.ok, report.error
    reference = _with_pair()
    AudioAutomationService().update_ducking_config(reference, "d1", DuckingConfig(threshold_db=-40.0, reduction_db=9.0))
    assert session.project.ducking_sidechains == reference.ducking_sidechains
    assert session.undo() and session.project.ducking_sidechains[0].config.threshold_db == -20.0


def test_remove_and_enable_match_the_model_calls():
    session, report = _apply(_with_pair(), _op("set_ducking_enabled", ducking="d1", enabled=False))
    assert report.ok and session.project.ducking_sidechains[0].enabled is False
    assert session.undo() and session.project.ducking_sidechains[0].enabled is True
    assert _apply(session.project, _op("remove_ducking_sidechain", ducking="d1"))[1].ok and session.project.ducking_sidechains == []


def test_edits_need_something_and_an_existing_association():
    assert "réglage" in str(_refused(_with_pair(), _op("update_ducking_config", ducking="d1")).error)
    for verb, extra in (("remove_ducking_sidechain", {}), ("set_ducking_enabled", {"enabled": True}), ("update_ducking_config", {"reduction_db": 3.0})):
        _refused(_with_pair(), _op(verb, ducking="inconnu", **extra))
        _refused(_with_pair(), _op(verb, ducking="Musique", **extra))                       # une association se désigne par son identifiant, pas par un nom


@pytest.mark.parametrize("verb,extra", [
    ("remove_ducking_sidechain", {}), ("set_ducking_enabled", {"enabled": False}), ("update_ducking_config", {"reduction_db": 3.0}),
])
def test_the_ducked_track_being_locked_refuses_every_edit_but_a_locked_voice_does_not(verb, extra):
    assert "verrouill" in str(_refused(_with_pair(music_locked=True), _op(verb, ducking="d1", **extra)).error)
    assert _apply(_with_pair(voice_locked=True), _op(verb, ducking="d1", **extra))[1].ok     # la voix n'est pas modifiée : seule la piste baissée l'est


def test_an_orphan_association_after_a_track_removal_can_still_be_removed():
    project = _with_pair()
    project.tracks = [t for t in project.tracks if t.id != "A1"]
    assert _apply(project, _op("remove_ducking_sidechain", ducking="d1"))[1].ok and project.ducking_sidechains == []


def test_the_script_is_atomic_when_a_later_operation_fails():
    project = _project()
    before = _state(project)
    session, report = _apply(
        project, _op("add_ducking_sidechain", music="A1", voice="A2", id="d1"), _op("add_ducking_sidechain", music="A1", voice="A2", id="d2"),
    )
    assert not report.ok and report.failed_index == 1
    assert _state(session.project) == before


def test_hostile_inputs_are_bounded_at_parse():
    with pytest.raises(ScriptInvalid) as caught:
        parse_script({"kut_script": 1, "ops": [_op("add_ducking_sidechain", music="x" * 5_000_000, voice="A2")]})
    assert len(str(caught.value)) < 10_000
    _invalid(_op("add_ducking_sidechain", music="A1", voice="A2", extra=1))
    _invalid(_op("add_ducking_sidechain", music="A1"))
    _invalid(_op("add_ducking_sidechain", music="A1", voice="A2", id="x" * 1000))
    _invalid(_op("add_ducking_sidechain", music="A1", voice="A2", **{"as": "d"}), _op("add_ducking_sidechain", music="A3", voice="A2", **{"as": "d"}))
