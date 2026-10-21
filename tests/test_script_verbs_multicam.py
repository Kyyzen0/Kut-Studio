"""Format déclaratif, lot 7 (ADR-0005 étape 4) : Multicam.

Une source Multicam est une séquence portant `Sequence.multicam` ; un segment est un clip imbriqué qui porte `Clip.angle_id`. Un script
désigne la source par son sélecteur de séquence, un angle par son identifiant (`angle-N`) ou son nom exact (unique dans la source), et
agit sur les segments de la séquence ACTIVE. Chaque test vérifie un refus du modèle (ou du verbe quand `core/` ne contrôle pas) ou la
parité avec les fonctions que l'interface appelle.
"""

from __future__ import annotations

from copy import deepcopy

import pytest

from core.multicam_model import AudioMode, MAX_ANGLES, SyncMethod
from core.multicam_ops import (
    AngleSpec, MulticamAudio, create_multicam_source, insert_multicam_clip, rename_angle, replace_angle, set_angle_offset,
    set_audio_policy, switch_angle, add_angle,
)
from core.project_model import MAIN_SEQUENCE_ID, Clip, MediaAsset, Project, Track
from core.scripting import ScriptInvalid, Session, apply_script, created_ids, json_schema, parse_script
from core.scripting.verbs import VERBS
from core.sequences import create_sequence
from ui import i18n

MULTICAM_VERBS = (
    "create_multicam_source", "insert_multicam_clip", "switch_multicam_angle", "replace_multicam_angle", "set_multicam_audio",
    "rename_multicam_angle", "add_multicam_angle", "set_multicam_angle_offset",
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
        media_assets=[
            MediaAsset("cam1", "/x/a.mp4", "Cam A", 10.0, 1280, 720, 30.0, "video", has_audio=True),
            MediaAsset("cam2", "/x/b.mp4", "Cam B", 8.0, 1280, 720, 30.0, "video", has_audio=True),
            MediaAsset("mute", "/x/c.mp4", "Cam muette", 8.0, 1280, 720, 30.0, "video", has_audio=False),
            MediaAsset("rec", "/x/r.wav", "Recorder", 10.0, 0, 0, 0.0, "audio", has_audio=True),
            MediaAsset("photo", "/x/p.png", "Photo", 5.0, 100, 100, 30.0, "image"),
            MediaAsset("nodur", "/x/n.mp4", "Sans durée", 0.0, 1280, 720, 30.0, "video", has_audio=True),
        ],
        tracks=[Track("V1", "V1", "video"), Track("V2", "V2", "video"), Track("A1", "A1", "audio")],
    )


def _script(*ops: dict) -> dict:
    return {"kut_script": 1, "ops": list(ops)}


def _apply(project: Project, *ops: dict):
    session = Session(project)
    return session, apply_script(session, parse_script(_script(*ops)))


CREATE = {"op": "create_multicam_source", "id": "show", "name": "Concert", "angles": [
    {"asset": "cam1", "name": "Large"}, {"asset": "cam2", "name": "Serré", "offset": 0.5}, {"asset": "rec", "name": "Son"},
]}


def _with_source() -> Project:
    """Projet de départ construit avec les fonctions de ``core`` : la source `show` (angle-1 « Large », angle-2 « Serré », angle-3 « Son »)."""
    project = _project()
    create_multicam_source(project, [
        AngleSpec(asset_id="cam1", name="Large", sync_method=SyncMethod.MANUAL),
        AngleSpec(asset_id="cam2", name="Serré", offset=0.5, sync_method=SyncMethod.MANUAL),
        AngleSpec(asset_id="rec", name="Son", sync_method=SyncMethod.MANUAL),
    ], name="Concert", sync_method=SyncMethod.MANUAL, sequence_id="show")
    return project


def _with_segment() -> Project:
    project = _with_source()
    insert_multicam_clip(project, "show", "V1", 0.0, angle_id="angle-1", clip_id="seg")
    return project


def _structure(sequence):
    """La séquence sans les identifiants de clips (générés au hasard par ``new_clip_id``)."""
    tracks = [(t.id, t.name, t.type, [(c.asset_id, c.timeline_start, c.source_in, c.source_out, c.label) for c in t.clips]) for t in sequence.tracks]
    return sequence.id, sequence.name, sequence.width, sequence.height, sequence.fps, tracks, sequence.multicam


# --- Le catalogue ------------------------------------------------------------------------------------------------------


def test_the_multicam_verbs_are_in_the_table_and_the_schema():
    for name in MULTICAM_VERBS:
        assert name in VERBS and VERBS[name].group == "multicam", name
    assert VERBS["create_multicam_source"].creates == "sequence"
    assert VERBS["insert_multicam_clip"].creates == VERBS["switch_multicam_angle"].creates == "clip"
    variants = {item["properties"]["op"]["const"]: item for item in json_schema()["properties"]["ops"]["items"]["oneOf"]}
    angles = variants["create_multicam_source"]["properties"]["angles"]
    assert angles["type"] == "array" and (angles["minItems"], angles["maxItems"]) == (1, MAX_ANGLES)
    assert angles["items"]["required"] == ["asset"] and angles["items"]["additionalProperties"] is False
    assert variants["set_multicam_audio"]["properties"]["angles"]["items"]["anyOf"], "une liste de sélecteurs"


# --- Création ----------------------------------------------------------------------------------------------------------


def test_create_insert_and_switch_a_multicam_in_one_script():
    script = parse_script(_script(
        CREATE,
        {"op": "insert_multicam_clip", "sequence": "show", "track": "V1", "at": 1, "angle": "Large", "id": "seg"},
        {"op": "switch_multicam_angle", "at": 3, "angle": "angle-2", "id": "seg_b"},
        {"op": "switch_multicam_angle", "at": 5, "angle": "Large", "as": "seg_c"},
    ))
    session = Session(_project())
    report = apply_script(session, script)
    assert report.ok, report.error
    project = session.project
    source = project.get_sequence("show")
    assert [(a.id, a.name, a.track_id) for a in source.multicam.angles] == [
        ("angle-1", "Large", "V1"), ("angle-2", "Serré", "V2"), ("angle-3", "Son", "A1"),
    ]
    assert source.name == "Concert" and (source.width, source.fps) == (1280, 30.0)
    assert project.active_sequence_id == MAIN_SEQUENCE_ID, "create_multicam_source n'active pas la source"
    assert [t.clips[0].timeline_start for t in source.tracks] == [0.0, 0.5, 0.0]
    segments = {c.id: c for c in project.get_sequence(MAIN_SEQUENCE_ID).tracks[0].clips}
    assert set(segments) == {"seg", "seg_b", "seg_c"}
    assert [(c.timeline_start, c.angle_id) for c in sorted(segments.values(), key=lambda c: c.timeline_start)] == [
        (1.0, "angle-1"), (3.0, "angle-2"), (5.0, "angle-1"),
    ]
    assert created_ids(script, report) == [
        {"op": "0:create_multicam_source", "id": "show"}, {"op": "1:insert_multicam_clip", "id": "seg"},
        {"op": "2:switch_multicam_angle", "id": "seg_b"}, {"op": "3:switch_multicam_angle", "id": "seg_c"},
    ]


def test_explicit_id_alias_and_replay_give_the_same_ids():
    ops = (
        {"op": "create_multicam_source", "as": "concert", "angles": [{"asset": "cam1"}, {"asset": "cam2"}]},
        {"op": "insert_multicam_clip", "sequence": {"ref": "concert"}, "track": "V1", "at": 0, "as": "seg"},
    )
    first, report = _apply(_project(), *ops)
    assert report.ok, report.error
    second, _ = _apply(_project(), *ops)
    assert [s.id for s in first.project.sequences] == [s.id for s in second.project.sequences] == [MAIN_SEQUENCE_ID, "concert"]
    assert first.project.get_sequence(MAIN_SEQUENCE_ID).tracks[0].clips[0].id == "seg"
    with pytest.raises(ScriptInvalid):                                      # un alias de clip n'est pas une séquence
        parse_script(_script(ops[0], {"op": "insert_multicam_clip", "sequence": {"ref": "seg"}, "track": "V1", "at": 0, "as": "seg"}))


def test_default_names_come_from_the_media_and_audio_policy_is_the_models_proposal():
    session, report = _apply(_project(), {"op": "create_multicam_source", "id": "s", "angles": [{"asset": "cam1"}, {"asset": "rec"}]})
    assert report.ok, report.error
    source = session.project.get_sequence("s")
    assert source.name == "Multicam" and [a.name for a in source.multicam.angles] == ["Cam A", "Recorder"]
    assert source.multicam.audio == MulticamAudio(AudioMode.FIXED, ("angle-2",)), "enregistreur sans mesure incertaine : son fixe"
    assert all(a.sync_method is SyncMethod.MANUAL for a in source.multicam.angles)


def test_create_matches_the_interface_function():
    project = _project()
    expected = deepcopy(project)
    create_multicam_source(expected, [
        AngleSpec(asset_id="cam1", name="Large", sync_method=SyncMethod.MANUAL),
        AngleSpec(asset_id="cam2", name="Serré", offset=0.5, sync_method=SyncMethod.MANUAL),
        AngleSpec(asset_id="rec", name="Son", sync_method=SyncMethod.MANUAL),
    ], name="Concert", sync_method=SyncMethod.MANUAL, sequence_id="show")
    session, report = _apply(project, CREATE)
    assert report.ok, report.error
    assert _structure(session.project.get_sequence("show")) == _structure(expected.get_sequence("show"))


def test_the_interface_still_generates_the_sequence_id_without_sequence_id():
    project = _project()
    sequence = create_multicam_source(project, [AngleSpec(asset_id="cam1")])
    assert sequence.id.startswith("seq-") and len(sequence.id) == len("seq-") + 12
    create_multicam_source(project, [AngleSpec(asset_id="cam1")], sequence_id="mine")
    with pytest.raises(ValueError, match="existe déjà"):
        create_multicam_source(project, [AngleSpec(asset_id="cam1")], sequence_id="mine")
    with pytest.raises(ValueError, match="invalide"):
        create_multicam_source(project, [AngleSpec(asset_id="cam1")], sequence_id="bad id")
    assert len(project.sequences) == 3


def test_create_bounds_are_read_from_the_model():
    base = {"op": "create_multicam_source"}
    for bad_angles in ([], [{"asset": "cam1"}] * (MAX_ANGLES + 1)):
        with pytest.raises(ScriptInvalid) as caught:
            parse_script(_script({**base, "angles": bad_angles}))
        assert caught.value.issues[0].code == "out_of_range"
    for bad in ({"asset": "cam1", "offset": -1}, {"asset": "cam1", "offset": 1e9}, {"asset": "cam1", "name": " x"},
                {"asset": "cam1", "extra": 1}, {"offset": 0}, "cam1"):
        with pytest.raises(ScriptInvalid):
            parse_script(_script({**base, "angles": [bad]}))
    with pytest.raises(ScriptInvalid):
        parse_script(_script({**base, "angles": {"asset": "cam1"}}))
    with pytest.raises(ScriptInvalid):
        parse_script(_script({**base}))
    issues = []
    try:
        parse_script(_script({**base, "angles": [{"asset": "cam1", "offset": -1}, {"offset": 0}]}))
    except ScriptInvalid as error:
        issues = [(i.code, i.path) for i in error.issues]
    assert ("out_of_range", "ops[0].angles[0].offset") in issues and ("missing_field", "ops[0].angles[1].asset") in issues


def test_offsets_are_never_silently_shifted():
    project = _project()
    _session, report = _apply(project, {"op": "create_multicam_source", "angles": [{"asset": "cam1", "offset": 2}, {"asset": "cam2", "offset": 3}]})
    assert not report.ok and "plus petit décalage" in report.error
    assert len(project.sequences) == 1


def test_unknown_or_wrong_type_media_and_undurable_media_are_refused():
    for angles, text in (
        ([{"asset": "inconnu"}], "introuvable"),
        ([{"asset": "photo"}], "ni une vidéo ni un son"),
        ([{"asset": "nodur"}], "durée"),
    ):
        project = _project()
        _session, report = _apply(project, {"op": "create_multicam_source", "angles": angles})
        assert not report.ok and text in report.error, angles
        assert len(project.sequences) == 1


def test_a_taken_sequence_name_or_id_is_refused():
    project = _with_source()
    for op, text in (
        ({"op": "create_multicam_source", "name": "Concert", "angles": [{"asset": "cam1"}]}, "s'appelle déjà"),
        ({"op": "create_multicam_source", "id": "show", "name": "Autre", "angles": [{"asset": "cam1"}]}, "existe déjà"),
    ):
        _session, report = _apply(project, op)
        assert not report.ok and text in report.error
    assert len(project.sequences) == 2


def test_angle_names_stay_unique_and_cannot_pose_as_ids():
    for angles, text in (
        ([{"asset": "cam1", "name": "X"}, {"asset": "cam2", "name": "X"}], "doit rester unique"),
        ([{"asset": "cam1", "name": "angle-2"}, {"asset": "cam2"}], "ressemble à un identifiant"),
        ([{"asset": "cam1"}, {"asset": "cam2", "name": "Cam A"}], "doit rester unique"),       # le nom par défaut du premier angle (celui du média) est pris
    ):
        project = _project()
        _session, report = _apply(project, {"op": "create_multicam_source", "angles": angles})
        assert not report.ok and text in report.error, angles
        assert len(project.sequences) == 1
    session, report = _apply(_project(), {"op": "create_multicam_source", "id": "s", "angles": [{"asset": "cam1", "name": "angle-1"}]})
    assert report.ok, "le nom de l'angle ressemble à SON identifiant : sans ambiguïté"


def test_the_default_source_name_is_checked_too():
    """Revue de #142 : sans `name`, le défaut « Multicam » était suffixé en silence quand il était pris."""
    project = _project()
    create_multicam_source(project, [AngleSpec(asset_id="cam1", sync_method=SyncMethod.MANUAL)], sync_method=SyncMethod.MANUAL)
    assert [sequence.name for sequence in project.sequences][-1] == "Multicam"
    _session, report = _apply(project, {"op": "create_multicam_source", "angles": [{"asset": "cam2"}]})
    assert not report.ok and "s'appelle déjà" in report.error
    assert len(project.sequences) == 2


def test_media_derived_angle_names_follow_the_same_rules():
    """Revue de #142 : le nom par défaut (celui du média) échappait au contrôle des noms d'angle."""
    project = _project()
    project.media_assets.append(MediaAsset("trap", "/x/t.mp4", "angle-2", 8.0, 1280, 720, 30.0, "video", has_audio=True))
    _session, report = _apply(project, {"op": "create_multicam_source", "angles": [{"asset": "trap"}, {"asset": "cam1"}]})
    assert not report.ok and "ressemble à un identifiant" in report.error
    _session, report = _apply(_project(), {"op": "create_multicam_source", "angles": [{"asset": "cam1"}, {"asset": "cam1"}]})
    assert not report.ok and "doit rester unique" in report.error                 # deux angles du même média, sans noms écrits
    project = _with_source()
    project.media_assets.append(MediaAsset("trap2", "/x/t2.mp4", "angle-2", 8.0, 1280, 720, 30.0, "video", has_audio=True))
    _session, report = _apply(project, {"op": "add_multicam_angle", "sequence": "show", "asset": "trap2"})
    assert not report.ok and ("ressemble à un identifiant" in report.error or "déjà" in report.error)
    _session, report = _apply(project, {"op": "add_multicam_angle", "sequence": "show", "asset": "cam1"})
    assert report.ok, report.error                                                # « Cam A » n'est pas pris (l'angle 1 s'appelle « Large »)
    _session, report = _apply(project, {"op": "add_multicam_angle", "sequence": "show", "asset": "cam1"})
    assert not report.ok and "déjà" in report.error                               # le second « Cam A » serait suffixé : refusé


# --- Désigner les angles et les sources --------------------------------------------------------------------------------


def test_angles_are_picked_by_id_or_exact_unique_name_and_refusals_are_explicit():
    project = _with_source()
    for selector in ("angle-2", "Serré"):
        _session, report = _apply(deepcopy(project), {"op": "rename_multicam_angle", "sequence": "show", "angle": selector, "name": "Gros plan"})
        assert report.ok, (selector, report.error)
    for selector, text in (("angle-9", "introuvable"), ("serré", "introuvable"), ("Serre", "introuvable")):
        _session, report = _apply(deepcopy(project), {"op": "rename_multicam_angle", "sequence": "show", "angle": selector, "name": "Z"})
        assert not report.ok and text in report.error, selector
    # Deux angles de même nom (fichier chargé tel quel) : ambiguïté = erreur, jamais un choix ; l'identifiant reste possible.
    project.get_sequence("show").multicam.angles[1].name = "Large"
    _session, report = _apply(deepcopy(project), {"op": "replace_multicam_angle", "clip": "x", "angle": "Large"})
    assert not report.ok
    insert = {"op": "insert_multicam_clip", "sequence": "show", "track": "V1", "at": 0}
    _session, report = _apply(deepcopy(project), {**insert, "angle": "Large"})
    assert not report.ok and "ambigu" in report.error
    _session, report = _apply(deepcopy(project), {**insert, "angle": "angle-2"})
    assert report.ok, report.error


def test_the_source_can_be_designated_by_name_and_must_be_multicam():
    project = _with_source()
    create_sequence(project, "Ordinaire", sequence_id="plain")
    _session, report = _apply(project, {"op": "rename_multicam_angle", "sequence": "Concert", "angle": "angle-1", "name": "Plan large"})
    assert report.ok and project.get_sequence("show").multicam.angles[0].name == "Plan large"
    for op in (
        {"op": "rename_multicam_angle", "sequence": "plain", "angle": "angle-1", "name": "X"},
        {"op": "set_multicam_audio", "sequence": "plain", "mode": "follow"},
        {"op": "add_multicam_angle", "sequence": "plain", "asset": "cam1"},
        {"op": "set_multicam_angle_offset", "sequence": "plain", "angle": "angle-1", "offset": 1},
        {"op": "insert_multicam_clip", "sequence": "plain", "track": "V1", "at": 0},
    ):
        _session, report = _apply(project, op)
        assert not report.ok and "n'est pas une source Multicam" in report.error, op["op"]
    _session, report = _apply(project, {"op": "rename_multicam_angle", "sequence": "absente", "angle": "angle-1", "name": "X"})
    assert not report.ok and "introuvable" in report.error


# --- Insérer -----------------------------------------------------------------------------------------------------------


def test_insert_defaults_to_the_first_angle_and_matches_the_interface_function():
    project = _with_source()
    expected = deepcopy(project)
    insert_multicam_clip(expected, "show", "V1", 2.0, angle_id="", clip_id="seg")
    session, report = _apply(project, {"op": "insert_multicam_clip", "sequence": "show", "track": "V1", "at": 2, "id": "seg"})
    assert report.ok, report.error
    clip = session.project.get_sequence(MAIN_SEQUENCE_ID).tracks[0].clips[0]
    assert clip.angle_id == "angle-1" and clip == expected.get_sequence(MAIN_SEQUENCE_ID).tracks[0].clips[0]
    assert session.project == expected


def test_insert_takes_source_bounds_and_refuses_beyond_the_source():
    project = _with_source()                      # durée de la source : 10 s (le son et la caméra A)
    session, report = _apply(project, {"op": "insert_multicam_clip", "sequence": "show", "track": "V1", "at": 0, "source_in": 1, "source_out": 4, "angle": "Son"})
    assert report.ok, report.error
    clip = session.project.get_sequence(MAIN_SEQUENCE_ID).tracks[0].clips[0]
    assert (clip.source_in, clip.source_out, clip.angle_id) == (1.0, 4.0, "angle-3")
    for bound in ({"source_out": 11}, {"source_in": 6, "source_out": 5}):
        project = _with_source()
        _session, report = _apply(project, {"op": "insert_multicam_clip", "sequence": "show", "track": "V1", "at": 0, **bound})
        assert not report.ok and not project.get_sequence(MAIN_SEQUENCE_ID).tracks[0].clips, bound


def test_insert_refuses_locked_or_wrong_kind_tracks_and_unknown_things():
    for track, setup, text in (
        ("V1", lambda p: setattr(p.tracks[0], "locked", True), "verrouillée"),
        ("T1", lambda p: p.tracks.append(Track("T1", "T1", "subtitle")), "vidéo ou audio"),
        ("V9", lambda p: None, "introuvable"),
    ):
        project = _with_source()
        setup(project.active_sequence)
        _session, report = _apply(project, {"op": "insert_multicam_clip", "sequence": "show", "track": track, "at": 0})
        assert not report.ok and text in report.error, track
        assert not any(t.clips for t in project.active_sequence.tracks)
    _session, report = _apply(_with_source(), {"op": "insert_multicam_clip", "sequence": "show", "track": "V1", "at": 0, "angle": "inconnu"})
    assert not report.ok and "introuvable" in report.error


def test_a_multicam_cannot_go_inside_a_multicam_nor_cycle():
    project = _with_source()
    session = Session(project)
    report = apply_script(session, parse_script(_script(
        {"op": "set_active_sequence", "sequence": "show"},
        {"op": "insert_multicam_clip", "sequence": "show", "track": "V1", "at": 0},
    )))
    assert not report.ok and report.failed_index == 1 and "elle-même" in report.error
    other = create_multicam_source(project, [AngleSpec(asset_id="cam1")], name="Autre", sequence_id="other")
    report = apply_script(session, parse_script(_script(
        {"op": "set_active_sequence", "sequence": other.id},
        {"op": "insert_multicam_clip", "sequence": "show", "track": "V1", "at": 0},
    )))
    assert not report.ok and "ne peut pas contenir une autre source Multicam" in report.error
    assert project.active_sequence_id == MAIN_SEQUENCE_ID


def test_nesting_depth_and_cycles_are_still_checked_through_an_ordinary_sequence():
    project = _with_source()
    create_sequence(project, "Boîte", sequence_id="box")
    session = Session(project)
    assert apply_script(session, parse_script(_script(
        {"op": "set_active_sequence", "sequence": "box"},
        {"op": "insert_multicam_clip", "sequence": "show", "track": "V1", "at": 0},
        {"op": "set_active_sequence", "sequence": MAIN_SEQUENCE_ID},
        {"op": "insert_sequence_clip", "sequence": "box", "track": "V1", "at": 0},
    ))).ok
    report = apply_script(session, parse_script(_script(
        {"op": "set_active_sequence", "sequence": "show"},
        {"op": "insert_sequence_clip", "sequence": "box", "track": "V2", "at": 0},
    )))
    assert not report.ok, "show contiendrait box qui contient show"


# --- Basculer et remplacer ---------------------------------------------------------------------------------------------


def test_switch_cuts_the_segment_and_matches_the_interface_function():
    project = _with_segment()
    expected = deepcopy(project)
    result = switch_angle(expected, 4.0, "angle-2", clip_id="seg", segment_id="seg_b")
    assert result is not None and result.cut
    session, report = _apply(project, {"op": "switch_multicam_angle", "at": 4, "angle": "Serré", "id": "seg_b"})
    assert report.ok, report.error
    assert session.project == expected
    clips = {c.id: c for c in session.project.active_sequence.tracks[0].clips}
    assert (clips["seg"].angle_id, clips["seg_b"].angle_id) == ("angle-1", "angle-2")
    assert clips["seg"].timeline_start + clips["seg"].duration == clips["seg_b"].timeline_start == 4.0
    assert report.results[0].value is clips["seg_b"] or report.results[0].value == clips["seg_b"]


def test_switch_without_id_keeps_the_models_generated_id_and_aliases_name_the_right_half():
    session, report = _apply(_with_segment(), {"op": "switch_multicam_angle", "at": 4, "angle": "angle-2"})
    assert report.ok, report.error
    assert {c.id for c in session.project.active_sequence.tracks[0].clips} == {"seg", "seg-split-2"}


def test_switch_at_the_start_of_a_segment_replaces_without_cutting_and_refuses_an_id():
    project = _with_segment()
    session, report = _apply(project, {"op": "switch_multicam_angle", "at": 0, "angle": "angle-2"})
    assert report.ok and [c.angle_id for c in project.active_sequence.tracks[0].clips] == ["angle-2"]
    project = _with_segment()
    _session, report = _apply(project, {"op": "switch_multicam_angle", "at": 0, "angle": "angle-2", "id": "x"})
    assert not report.ok and "aucune coupe" in report.error
    assert [c.angle_id for c in project.active_sequence.tracks[0].clips] == ["angle-1"]


def test_switching_to_the_angle_already_shown_is_a_no_op_that_returns_the_segment():
    project = _with_segment()
    expected = deepcopy(project)
    session, report = _apply(project, {"op": "switch_multicam_angle", "at": 4, "angle": "angle-1"})
    assert report.ok and session.project == expected and report.results[0].value.id == "seg"
    _session, report = _apply(project, {"op": "switch_multicam_angle", "at": 4, "angle": "angle-1", "id": "x"})
    assert not report.ok and "déjà cet angle" in report.error


def test_switch_refusals():
    for op, text in (
        ({"op": "switch_multicam_angle", "at": 20, "angle": "angle-2"}, "Aucun segment"),
        ({"op": "switch_multicam_angle", "at": 4, "angle": "angle-9"}, "introuvable"),
        ({"op": "switch_multicam_angle", "at": 4, "angle": "angle-2", "clip": "absent"}, "introuvable"),
        ({"op": "switch_multicam_angle", "at": 4, "angle": "angle-2", "track": "V2"}, "Aucun segment"),
        ({"op": "switch_multicam_angle", "at": 9.99, "angle": "angle-2"}, "fin du segment"),
        ({"op": "switch_multicam_angle", "at": 4, "angle": "angle-2", "id": "seg"}, "existe déjà"),
        ({"op": "switch_multicam_angle", "at": 4, "angle": "angle-2", "id": "bad id"}, None),
    ):
        project = _with_segment()
        expected = deepcopy(project)
        if text is None:
            with pytest.raises(ScriptInvalid):
                parse_script(_script(op))
            continue
        _session, report = _apply(project, op)
        assert not report.ok and text in report.error, op
        assert project == expected, "refus : projet inchangé"


def test_switch_picks_the_segment_by_clip_or_by_track_and_never_touches_an_ordinary_clip():
    project = _with_segment()
    insert_multicam_clip(project, "show", "V2", 0.0, angle_id="angle-1", clip_id="seg2")
    session, report = _apply(project, {"op": "switch_multicam_angle", "at": 4, "angle": "angle-2", "clip": "seg", "id": "low"})
    assert report.ok, report.error
    by_id = {c.id: c for t in session.project.active_sequence.tracks for c in t.clips}
    assert (by_id["low"].angle_id, by_id["seg2"].angle_id, by_id["low"].track_id) == ("angle-2", "angle-1", "V1")
    project = _with_segment()
    insert_multicam_clip(project, "show", "V2", 0.0, angle_id="angle-1", clip_id="seg2")
    session, report = _apply(project, {"op": "switch_multicam_angle", "at": 4, "angle": "angle-2"})        # piste la plus haute : V2
    assert report.ok and {c.id: c.angle_id for c in session.project.active_sequence.tracks[0].clips} == {"seg": "angle-1"}


def test_switch_and_replace_respect_a_locked_track_even_where_the_model_does_not_check():
    for op in (
        {"op": "switch_multicam_angle", "at": 4, "angle": "angle-2"},
        {"op": "switch_multicam_angle", "at": 0, "angle": "angle-2"},                  # début de segment : ``switch_angle`` ne coupe pas, donc ne contrôle rien
        {"op": "replace_multicam_angle", "clip": "seg", "angle": "angle-2"},
    ):
        project = _with_segment()
        project.active_sequence.tracks[0].locked = True
        expected = deepcopy(project)
        _session, report = _apply(project, op)
        assert not report.ok and "verrouillée" in report.error, op
        assert project == expected


def test_replace_changes_the_angle_without_a_cut_and_matches_the_interface_function():
    project = _with_segment()
    expected = deepcopy(project)
    replace_angle(expected, "seg", "angle-3")
    session, report = _apply(project, {"op": "replace_multicam_angle", "clip": "seg", "angle": "Son"})
    assert report.ok, report.error
    assert session.project == expected and len(session.project.active_sequence.tracks[0].clips) == 1


def test_replace_refusals():
    project = _with_segment()
    project.active_sequence.tracks[2].clips.append(Clip("plain", "cam1", "A1", 0.0, 0.0, 2.0, label="plain"))
    for op, text in (
        ({"op": "replace_multicam_angle", "clip": "plain", "angle": "angle-1"}, "pas un segment Multicam"),
        ({"op": "replace_multicam_angle", "clip": "seg", "angle": "angle-9"}, "introuvable"),
        ({"op": "replace_multicam_angle", "clip": "absent", "angle": "angle-1"}, "introuvable"),
    ):
        expected = deepcopy(project)
        _session, report = _apply(project, op)
        assert not report.ok and text in report.error, op
        assert project == expected


# --- Politique audio, noms, angles, décalages --------------------------------------------------------------------------


def test_audio_policy_matches_the_interface_function():
    for mode, angles, args in (
        ("fixed", ["Son"], (AudioMode.FIXED, ("angle-3",))),
        ("mix", ["angle-1", "Son"], (AudioMode.MIX, ("angle-1", "angle-3"))),
        ("follow", None, (AudioMode.FOLLOW_VIDEO, ())),
    ):
        project = _with_source()
        expected = deepcopy(project)
        set_audio_policy(expected, "show", *args)
        op = {"op": "set_multicam_audio", "sequence": "show", "mode": mode, **({"angles": angles} if angles else {})}
        session, report = _apply(project, op)
        assert report.ok, (mode, report.error)
        assert session.project == expected


def test_audio_policy_refusals_never_correct_silently():
    for op, text in (
        ({"mode": "fixed", "angles": ["angle-1", "angle-2"]}, "exactement une"),
        ({"mode": "fixed"}, "exactement une"),
        ({"mode": "mix"}, "au moins une"),
        ({"mode": "follow", "angles": ["angle-1"]}, "ne prend aucun angle"),
        ({"mode": "mix", "angles": ["angle-1", "Large"]}, "cité deux fois"),
        ({"mode": "fixed", "angles": ["angle-9"]}, "introuvable"),
    ):
        project = _with_source()
        expected = deepcopy(project)
        _session, report = _apply(project, {"op": "set_multicam_audio", "sequence": "show", **op})
        assert not report.ok and text in report.error, op
        assert project == expected
    project = _with_source()
    add_angle(project, "show", AngleSpec(asset_id="mute", name="Muette"))
    _session, report = _apply(project, {"op": "set_multicam_audio", "sequence": "show", "mode": "fixed", "angles": ["Muette"]})
    assert not report.ok and "pas de son" in report.error
    for bad in ("loud", 3, None):
        with pytest.raises(ScriptInvalid):
            parse_script(_script({"op": "set_multicam_audio", "sequence": "show", "mode": bad}))
    with pytest.raises(ScriptInvalid):
        parse_script(_script({"op": "set_multicam_audio", "sequence": "show", "mode": "mix", "angles": []}))


def test_rename_matches_the_interface_function_and_keeps_names_unique():
    project = _with_source()
    expected = deepcopy(project)
    rename_angle(expected, "show", "angle-2", "Gros plan")
    session, report = _apply(project, {"op": "rename_multicam_angle", "sequence": "show", "angle": "Serré", "name": "Gros plan"})
    assert report.ok, report.error
    assert session.project == expected and session.project.get_sequence("show").tracks[1].name == "Gros plan"
    # le nom suffit ensuite à viser l'angle, l'identifiant ne change pas
    _session, report = _apply(project, {"op": "replace_multicam_angle", "clip": "x", "angle": "Gros plan"})
    assert not report.ok and "introuvable" in report.error and "Clip" in report.error
    for name, text in (("Large", "doit rester unique"), ("angle-3", "ressemble à un identifiant")):
        before = deepcopy(project)
        _session, report = _apply(project, {"op": "rename_multicam_angle", "sequence": "show", "angle": "angle-2", "name": name})
        assert not report.ok and text in report.error and project == before
    _session, report = _apply(project, {"op": "rename_multicam_angle", "sequence": "show", "angle": "angle-2", "name": "Gros plan"})
    assert report.ok, "garder son propre nom n'est pas un conflit"
    for bad in ("", " x", "a\nb", "x" * 201):
        with pytest.raises(ScriptInvalid):
            parse_script(_script({"op": "rename_multicam_angle", "sequence": "show", "angle": "angle-1", "name": bad}))


def test_add_angle_matches_the_interface_function_and_numbers_after_the_largest():
    project = _with_source()
    expected = deepcopy(project)
    add_angle(expected, "show", AngleSpec(asset_id="cam2", offset=1.5, name="Drone", sync_method=SyncMethod.MANUAL))
    session, report = _apply(project, {"op": "add_multicam_angle", "sequence": "show", "asset": "cam2", "offset": 1.5, "name": "Drone"})
    assert report.ok, report.error
    assert _structure(session.project.get_sequence("show")) == _structure(expected.get_sequence("show"))
    assert session.project.get_sequence("show").multicam.angles[-1].id == "angle-4"
    _session, report = _apply(session.project, {"op": "rename_multicam_angle", "sequence": "show", "angle": "Drone", "name": "Plongée"})
    assert report.ok


def test_add_angle_refusals():
    for op, text in (
        ({"asset": "photo"}, "ni une vidéo ni un son"),
        ({"asset": "inconnu"}, "introuvable"),
        ({"asset": "nodur"}, "durée"),
        ({"asset": "cam2", "name": "Large"}, "doit rester unique"),
        ({"asset": "cam2", "name": "angle-1"}, "ressemble à un identifiant"),
        ({"asset": "cam1", "name": "Large"}, "doit rester unique"),
    ):
        project = _with_source()
        expected = deepcopy(project)
        _session, report = _apply(project, {"op": "add_multicam_angle", "sequence": "show", **op})
        assert not report.ok and text in report.error, op
        assert project == expected
    project = _with_source()
    project.get_sequence("show").multicam.angles.extend(deepcopy(project.get_sequence("show").multicam.angles[0]) for _ in range(MAX_ANGLES))
    for number, angle in enumerate(project.get_sequence("show").multicam.angles):
        angle.id = f"angle-{number + 1}"
    _session, report = _apply(project, {"op": "add_multicam_angle", "sequence": "show", "asset": "cam1"})
    assert not report.ok and str(MAX_ANGLES) in report.error
    # Un nom par défaut (nom du média) déjà pris est suffixé par le modèle, comme dans l'interface.
    session, report = _apply(_with_source(), {"op": "add_multicam_angle", "sequence": "show", "asset": "cam1"})
    assert report.ok and session.project.get_sequence("show").multicam.angles[-1].name == "Cam A"


def test_set_angle_offset_matches_the_interface_function_and_refuses_locked_or_negative():
    project = _with_source()
    expected = deepcopy(project)
    set_angle_offset(expected, "show", "angle-2", 2.0)
    session, report = _apply(project, {"op": "set_multicam_angle_offset", "sequence": "show", "angle": "Serré", "offset": 2})
    assert report.ok, report.error
    assert session.project == expected and session.project.get_sequence("show").tracks[1].clips[0].timeline_start == 2.0
    assert session.project.get_sequence("show").multicam.angles[1].sync_method is SyncMethod.MANUAL
    with pytest.raises(ScriptInvalid):
        parse_script(_script({"op": "set_multicam_angle_offset", "sequence": "show", "angle": "angle-1", "offset": -1}))
    project = _with_source()
    project.get_sequence("show").tracks[1].locked = True
    before = deepcopy(project)
    _session, report = _apply(project, {"op": "set_multicam_angle_offset", "sequence": "show", "angle": "angle-2", "offset": 2})
    assert not report.ok and "verrouillée" in report.error and project == before


# --- Atomicité, historique ---------------------------------------------------------------------------------------------


def test_one_failing_verb_cancels_the_previous_multicam_ones():
    project = _project()
    before = deepcopy(project)
    session, report = _apply(
        project, CREATE,
        {"op": "insert_multicam_clip", "sequence": "show", "track": "V1", "at": 0, "id": "seg"},
        {"op": "switch_multicam_angle", "at": 4, "angle": "angle-2", "id": "b"},
        {"op": "switch_multicam_angle", "at": 6, "angle": "angle-9"},
    )
    assert not report.ok and report.failed_index == 3
    assert project == before and not session.history.can_undo


def test_a_whole_multicam_script_is_one_history_step():
    project = _project()
    session = Session(project)
    before = deepcopy(project)
    report = apply_script(session, parse_script(_script(
        CREATE,
        {"op": "insert_multicam_clip", "sequence": "show", "track": "V1", "at": 0, "id": "seg"},
        {"op": "switch_multicam_angle", "at": 4, "angle": "Serré", "id": "seg_b"},
        {"op": "replace_multicam_angle", "clip": "seg", "angle": "Son"},
        {"op": "set_multicam_audio", "sequence": "show", "mode": "mix", "angles": ["Large", "Son"]},
        {"op": "rename_multicam_angle", "sequence": "show", "angle": "Son", "name": "Console"},
        {"op": "add_multicam_angle", "sequence": "show", "asset": "cam2", "name": "Drone"},
        {"op": "set_multicam_angle_offset", "sequence": "show", "angle": "Drone", "offset": 1},
    )))
    assert report.ok, report.error
    assert session.undo() and not session.can_undo
    assert project == before, "une seule annulation défait toute la source Multicam et son montage"
    assert session.redo() and project.get_sequence("show") is not None


def test_nested_clip_cropping_still_applies_when_an_angle_is_moved():
    """Recaler le seul angle long raccourcit la source ; le segment imbriqué est recadré dans la même étape, comme dans l'interface."""
    project = _project()
    create_multicam_source(project, [AngleSpec(asset_id="cam2", name="Seule")], name="Solo", sequence_id="solo")     # 8 s
    insert_multicam_clip(project, "solo", "V1", 0.0, clip_id="seg")
    session = Session(project)
    report = apply_script(session, parse_script(_script({"op": "set_multicam_angle_offset", "sequence": "solo", "angle": "Seule", "offset": 0})))
    assert report.ok, report.error
    assert session.project.active_sequence.tracks[0].clips[0].source_out == 8.0


def test_multicam_values_are_data_not_code():
    sneaky = "__import__('os').system('x')"
    session, report = _apply(_project(), {"op": "create_multicam_source", "id": "s", "name": sneaky, "angles": [{"asset": "cam1", "name": "${HOME}; rm -rf /"}]})
    assert report.ok, report.error
    source = session.project.get_sequence("s")
    assert source.name == sneaky and source.multicam.angles[0].name == "${HOME}; rm -rf /"
    deepcopy(session.project)


def test_cut_clip_keeps_its_generated_right_id_by_default_and_takes_an_imposed_one():
    from core.timeline_operations import cut_clip

    project = _with_segment()
    _left, right = cut_clip(project, "seg", 2.0)
    assert right.id == "seg-split-2", "comportement de l'interface inchangé"
    for bad in ("seg", "bad id"):
        with pytest.raises(ValueError):
            cut_clip(project, "seg", 1.0, right_clip_id=bad)
    assert [c.id for c in project.active_sequence.tracks[0].clips] == ["seg", "seg-split-2"], "refus : rien n'est coupé"
    _left, right = cut_clip(project, "seg", 1.0, right_clip_id="mine")
    assert right.id == "mine"
