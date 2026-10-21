"""Format déclaratif, lot 9 (ADR-0005 étape 4) : Multicam depuis des clips, aplatir un segment, supprimer un angle, couleur d'un angle.

`create_multicam_from_clips` transforme des clips de la séquence ACTIVE en source Multicam ; `flatten_multicam_segment` remplace un segment
par des clips ordinaires ; `remove_multicam_angle` supprime un angle et redirige les segments (de toutes les séquences) ;
`set_multicam_angle_color` règle le rang de palette. La synchronisation par analyse (`apply_sync`) n'a pas de verbe : un script n'analyse
rien. Chaque test vérifie un refus (du modèle, ou du verbe quand `core/` ne contrôle pas) ou la parité avec les fonctions de l'interface.
"""

from __future__ import annotations

from copy import deepcopy

import pytest

from core.audio_automation import DuckingSidechain
from core.mograph_layers import add_layer
from core.multicam_model import MAX_ANGLES, SyncMethod
from core.multicam_ops import (
    AngleSpec, create_multicam_from_clips, create_multicam_source, flatten_multicam_clip, insert_multicam_clip, remove_angle, set_angle_color,
)
from core.project_model import MAIN_SEQUENCE_ID, Clip, MediaAsset, Project, Track
from core.scripting import ScriptInvalid, Session, apply_script, created_ids, json_schema, parse_script
from core.scripting.multicam_verbs import ANGLE_PALETTE_SIZE
from core.scripting.verbs import VERBS
from core.sequences import create_sequence
from core.tracking_model import ClipTracking, Stabilization
from core.transitions import TransitionType, add_transition
from ui import i18n
from ui.theme import active_palette

NEW_VERBS = ("create_multicam_from_clips", "flatten_multicam_segment", "remove_multicam_angle", "set_multicam_angle_color")


@pytest.fixture(autouse=True)
def _french(monkeypatch):
    monkeypatch.setenv("KUT_STUDIO_LANG", "fr")
    i18n.reset_for_tests()
    yield
    i18n.reset_for_tests()


def _project() -> Project:
    project = Project(
        "Démo", width=1280, height=720, fps=30.0,
        media_assets=[
            MediaAsset("cam1", "/x/a.mp4", "Cam A", 10.0, 1280, 720, 30.0, "video", has_audio=True),
            MediaAsset("cam2", "/x/b.mp4", "Cam B", 10.0, 1280, 720, 30.0, "video", has_audio=True),
            MediaAsset("rec", "/x/r.wav", "Recorder", 10.0, 0, 0, 0.0, "audio", has_audio=True),
        ],
        tracks=[Track("V1", "V1", "video"), Track("V2", "V2", "video"), Track("A1", "A1", "audio"), Track("G1", "G1", "graphics")],
    )
    project.tracks[0].clips.append(Clip("c1", "cam1", "V1", 2.0, 0.0, 6.0, label="Large"))
    project.tracks[1].clips.append(Clip("c2", "cam2", "V2", 2.5, 0.0, 6.0, label="Serré"))
    project.tracks[2].clips.append(Clip("r1", "rec", "A1", 2.0, 0.0, 6.0, label="Son"))
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
    assert session.project.sequences == before.sequences, "un refus ne laisse rien derrière lui"
    return report


def _shape(sequence):
    """La séquence sans les identifiants de clips que le modèle génère au hasard."""
    tracks = [(t.id, t.name, t.type, [(c.asset_id, c.timeline_start, c.source_in, c.source_out, c.label) for c in t.clips]) for t in sequence.tracks]
    return sequence.id, sequence.name, sequence.width, sequence.height, sequence.fps, tracks, sequence.multicam


def _with_source() -> Project:
    project = _project()
    create_multicam_source(project, [
        AngleSpec(asset_id="cam1", name="Large", sync_method=SyncMethod.MANUAL),
        AngleSpec(asset_id="cam2", name="Serré", offset=0.5, sync_method=SyncMethod.MANUAL),
        AngleSpec(asset_id="rec", name="Son", sync_method=SyncMethod.MANUAL),
    ], name="Concert", sync_method=SyncMethod.MANUAL, sequence_id="show")
    return project


def _segment(project: Project) -> Clip:
    return next(c for t in project.tracks for c in t.clips if c.id == "seg")


def _with_segment(angle: str = "angle-1") -> Project:
    project = _with_source()
    insert_multicam_clip(project, "show", "V1", 0.0, angle_id=angle, clip_id="seg", source_out=4.0)
    return project


# --- Le catalogue ------------------------------------------------------------------------------------------------------


def test_the_new_verbs_are_in_the_table_the_schema_and_the_debt_says_why_apply_sync_has_none():
    for name in NEW_VERBS:
        assert VERBS[name].group == "multicam" and VERBS[name].doc, name
    assert VERBS["create_multicam_from_clips"].creates == "sequence"
    assert VERBS["flatten_multicam_segment"].creates is None and VERBS["remove_multicam_angle"].creates is None
    variants = {item["properties"]["op"]["const"]: item for item in json_schema()["properties"]["ops"]["items"]["oneOf"]}
    clips = variants["create_multicam_from_clips"]["properties"]["clips"]
    assert (clips["minItems"], clips["maxItems"]) == (2, MAX_ANGLES) and clips["items"]["required"] == ["clip"]
    from tools import scripting_debt

    assert scripting_debt.load_verb_debt()["core.multicam_ops.apply_sync"] == "interface seulement : analyse"
    assert "apply_sync" not in {name for verb in VERBS.values() for name in [verb.name]}


def test_the_angle_palette_size_is_the_one_the_interface_wraps_around():
    assert ANGLE_PALETTE_SIZE == len(active_palette().angle_colors)


# --- create_multicam_from_clips ----------------------------------------------------------------------------------------


def test_create_multicam_from_clips_matches_the_model_call_the_interface_makes():
    names = {"c1": "Large", "c2": "Serré", "r1": "Son"}
    session = _ok(_project(), {"op": "create_multicam_from_clips", "id": "show", "name": "Concert", "segment_id": "seg", "clips": [
        {"clip": "c1", "name": "Large"}, {"clip": "c2", "name": "Serré"}, {"clip": "r1", "name": "Son"}]})
    twin = _project()
    create_multicam_from_clips(twin, ["c1", "c2", "r1"], name="Concert", sync_method=SyncMethod.POSITIONS, names=names,
                               sequence_id="show", segment_id="seg")
    assert _shape(session.project.get_sequence("show")) == _shape(twin.get_sequence("show"))
    assert [(t.id, [(c.id, c.timeline_start, c.angle_id) for c in t.clips]) for t in session.project.tracks if t.clips] == [
        (t.id, [(c.id, c.timeline_start, c.angle_id) for c in t.clips]) for t in twin.tracks if t.clips
    ]
    source = session.project.get_sequence("show").multicam
    assert [a.name for a in source.angles] == ["Large", "Serré", "Son"] and source.sync_method is SyncMethod.POSITIONS
    assert [t.clips[0].timeline_start for t in session.project.get_sequence("show").tracks] == [0.0, 0.5, 0.0]
    assert session.project.tracks[0].clips[0].id == "seg" and session.project.tracks[0].clips[0].timeline_start == 2.0
    assert not any(c.id in {"c1", "c2", "r1"} for t in session.project.tracks for c in t.clips)


def test_create_multicam_from_clips_with_explicit_offsets_and_default_names_and_ids():
    script = parse_script(_script({"op": "create_multicam_from_clips", "as": "show", "clips": [
        {"clip": "c1", "offset": 0.0}, {"clip": "c2", "offset": 1.5}]}))
    session = Session(_project())
    report = apply_script(session, script)
    assert report.ok, report.error
    assert created_ids(script, report) == [{"op": "0:create_multicam_from_clips", "id": "show"}]
    source = session.project.get_sequence("show")
    assert [(a.name, a.sync_method) for a in source.multicam.angles] == [("Large", SyncMethod.MANUAL), ("Serré", SyncMethod.MANUAL)]
    assert [t.clips[0].timeline_start for t in source.tracks] == [0.0, 1.5]
    assert session.undo() and [c.id for c in session.project.tracks[0].clips] == ["c1"] and len(session.project.sequences) == 1


def test_create_multicam_from_clips_refuses_offsets_that_the_model_would_correct_silently():
    _refused(_project(), {"op": "create_multicam_from_clips", "clips": [{"clip": "c1", "offset": 1.0}, {"clip": "c2", "offset": 2.0}]},
             message="doit valoir 0")
    _refused(_project(), {"op": "create_multicam_from_clips", "clips": [{"clip": "c1", "offset": 0.0}, {"clip": "c2"}]}, message="à chaque clip")
    for bad in (-1.0, float("nan"), float("inf"), 1e12):
        with pytest.raises(ScriptInvalid):
            parse_script(_script({"op": "create_multicam_from_clips", "clips": [{"clip": "c1", "offset": bad}, {"clip": "c2", "offset": 0}]}))


def test_create_multicam_from_clips_refuses_names_the_model_would_suffix_or_that_pass_for_an_angle_id():
    _refused(_project(), {"op": "create_multicam_from_clips", "clips": [{"clip": "c1", "name": "Pareil"}, {"clip": "c2", "name": "Pareil"}]},
             message="s'appelle déjà")
    project = _project()
    project.tracks[1].clips[0].label = "Large"                                  # deux clips qui portent le même libellé, sans nom écrit
    _refused(project, {"op": "create_multicam_from_clips", "clips": [{"clip": "c1"}, {"clip": "c2"}]}, message="s'appelle déjà")
    # L'angle 2 est c2 (vidéos d'abord, par position) : « angle-2 » lui est permis, « angle-1 » ne l'est pas.
    _refused(_project(), {"op": "create_multicam_from_clips", "clips": [{"clip": "c1", "name": "angle-2"}, {"clip": "c2", "name": "Serré"}]},
             message="ressemble à un identifiant")
    _ok(_project(), {"op": "create_multicam_from_clips", "clips": [{"clip": "c1", "name": "Large"}, {"clip": "c2", "name": "angle-2"}]})
    _refused(_project(), {"op": "create_multicam_from_clips", "name": "Séquence principale", "clips": [{"clip": "c1"}, {"clip": "c2"}]},
             message="s'appelle déjà")


def test_create_multicam_from_clips_refuses_locks_duplicates_foreign_clips_and_graphics():
    project = _project()
    project.tracks[0].locked = True
    _refused(project, {"op": "create_multicam_from_clips", "clips": [{"clip": "c1"}, {"clip": "c2"}]}, message="verrouillée")
    _refused(_project(), {"op": "create_multicam_from_clips", "clips": [{"clip": "c1"}, {"clip": "c1"}]}, message="plus d'une fois")
    other = _project()
    create_sequence(other, "Autre", sequence_id="autre").tracks[0].clips.append(Clip("zz", "cam1", "V1", 0.0, 0.0, 2.0, label="zz"))
    _refused(other, {"op": "create_multicam_from_clips", "clips": [{"clip": "c1"}, {"clip": "zz"}]}, message="introuvable")
    with pytest.raises(ScriptInvalid):
        parse_script(_script({"op": "create_multicam_from_clips", "clips": [{"clip": "c1"}]}))
    with pytest.raises(ScriptInvalid):
        parse_script(_script({"op": "create_multicam_from_clips", "clips": [{"clip": f"c{i}"} for i in range(MAX_ANGLES + 1)]}))
    graphics = _project()
    add_layer(graphics, "text", at=0.0, duration=2.0, clip_id="g")
    _refused(graphics, {"op": "create_multicam_from_clips", "clips": [{"clip": "c1"}, {"clip": "g"}]}, message="piste 'graphics'")


def test_create_multicam_from_clips_refuses_what_the_model_would_leave_dangling():
    """`delete_clip` retire les transitions et délie le suivi ; `create_multicam_from_clips` ne le fait pas : un script reçoit un refus."""
    project = _project()
    project.tracks[0].clips[0] = Clip("c1", "cam1", "V1", 2.0, 0.0, 3.0, label="Large")
    project.tracks[0].clips.append(Clip("c4", "cam1", "V1", 5.0, 3.0, 6.0, label="suite"))
    add_transition(project, "c1", "c4", TransitionType.CROSSFADE, 0.1, transition_id="t1")
    _refused(project, {"op": "create_multicam_from_clips", "clips": [{"clip": "c1"}, {"clip": "c2"}]}, message="transition")
    tracked = _project()
    tracked.tracks[0].clips[0].tracking = ClipTracking(stabilization=Stabilization())
    _refused(tracked, {"op": "create_multicam_from_clips", "clips": [{"clip": "c1"}, {"clip": "c2"}]}, message="données de suivi")
    followed = _project()
    followed.tracks[0].clips.append(Clip("c5", "cam1", "V1", 8.5, 0.0, 1.0, label="suiveur"))
    from core.tracking_model import TrackLink

    followed.tracks[0].clips[1].tracking = ClipTracking(links=(TrackLink(id="l1", source_clip_id="c1"),))
    _refused(followed, {"op": "create_multicam_from_clips", "clips": [{"clip": "c1"}, {"clip": "c2"}]}, message="suit le clip")


def test_create_multicam_from_clips_bounds_ids_and_the_project_before_creating(monkeypatch):
    taken = _project()
    create_sequence(taken, "Pris", sequence_id="show")
    _refused(taken, {"op": "create_multicam_from_clips", "id": "show", "clips": [{"clip": "c1"}, {"clip": "c2"}]}, message="existe déjà")
    _refused(_project(), {"op": "create_multicam_from_clips", "segment_id": "c1", "clips": [{"clip": "c1"}, {"clip": "c2"}]}, message="existe déjà")
    monkeypatch.setattr("core.scripting.sequence_rules.MAX_SEQUENCES_IN_PROJECT", 1)
    _refused(_project(), {"op": "create_multicam_from_clips", "clips": [{"clip": "c1"}, {"clip": "c2"}]}, message="limite")


# --- flatten_multicam_segment ------------------------------------------------------------------------------------------


def test_flatten_matches_the_model_call_the_interface_makes():
    for angle in ("angle-1", "angle-2"):
        session = _ok(_with_segment(angle), {"op": "flatten_multicam_segment", "clip": "seg"})
        twin = _with_segment(angle)
        flatten_multicam_clip(twin, "seg")
        def tracks(project):
            return [(t.id, t.name, [(c.asset_id, c.timeline_start, c.source_in, c.source_out, c.gain_db) for c in t.clips]) for t in project.tracks]
        assert tracks(session.project) == tracks(twin), angle
        assert not any(c.id == "seg" for t in session.project.tracks for c in t.clips)
    assert session.undo() and any(c.id == "seg" for t in session.project.tracks for c in t.clips)


def test_flatten_refuses_locks_non_segments_and_what_the_model_cannot_merge():
    project = _with_segment()
    project.tracks[0].locked = True
    _refused(project, {"op": "flatten_multicam_segment", "clip": "seg"}, message="verrouillée")
    _refused(_project(), {"op": "flatten_multicam_segment", "clip": "c1"}, message="n'est pas un segment")
    project = _with_segment()
    _segment(project).gain_db = 3.0
    _refused(project, {"op": "flatten_multicam_segment", "clip": "seg"}, message="gain")


def test_flatten_refuses_tracking_a_transition_to_a_locked_track_and_the_clip_bound(monkeypatch):
    tracked = _with_segment()
    _segment(tracked).tracking = ClipTracking(stabilization=Stabilization())
    _refused(tracked, {"op": "flatten_multicam_segment", "clip": "seg"}, message="données de suivi")
    from core.transitions import Transition

    linked = _with_segment()
    linked.tracks[1].locked = True                                             # c2, sur V2, est le voisin de la transition
    linked.active_sequence.transitions.append(Transition(id="t1", type=TransitionType.CROSSFADE, from_clip_id="seg", to_clip_id="c2", duration=0.1))
    _refused(linked, {"op": "flatten_multicam_segment", "clip": "seg"}, message="piste verrouillée")
    monkeypatch.setattr("core.scripting.resource_limits.MAX_CLIPS_IN_PROJECT", 4)           # source : 3 clips d'angle + 1 segment + 3 clips
    _refused(_with_segment(), {"op": "flatten_multicam_segment", "clip": "seg"}, message="limite")


# --- remove_multicam_angle ---------------------------------------------------------------------------------------------


def test_remove_angle_matches_the_model_call_the_interface_makes():
    session = _ok(_with_segment("angle-2"), {"op": "remove_multicam_angle", "sequence": "show", "angle": "Serré", "replace_with": "Large"})
    twin = _with_segment("angle-2")
    assert remove_angle(twin, "show", "angle-2", replace_with="angle-1") == 1
    assert _shape(session.project.get_sequence("show")) == _shape(twin.get_sequence("show"))
    assert _segment(session.project).angle_id == _segment(twin).angle_id == "angle-1"
    unused = _ok(_with_segment("angle-1"), {"op": "remove_multicam_angle", "sequence": "show", "angle": "angle-3"})
    assert [a.id for a in unused.project.get_sequence("show").multicam.angles] == ["angle-1", "angle-2"]


def test_remove_angle_refusals_leave_everything_alone():
    _refused(_with_segment("angle-2"), {"op": "remove_multicam_angle", "sequence": "show", "angle": "angle-2"}, message="choisissez l'angle")
    _refused(_with_segment("angle-1"), {"op": "remove_multicam_angle", "sequence": "show", "angle": "angle-3", "replace_with": "angle-1"},
             message="n'a rien à remplacer")
    _refused(_with_segment("angle-2"), {"op": "remove_multicam_angle", "sequence": "show", "angle": "angle-2", "replace_with": "angle-2"},
             message="un autre angle")
    _refused(_project(), {"op": "remove_multicam_angle", "sequence": MAIN_SEQUENCE_ID, "angle": "angle-1"}, message="Multicam")
    only = _project()
    create_multicam_source(only, [AngleSpec(asset_id="cam1")], name="Seul", sequence_id="seul")
    _refused(only, {"op": "remove_multicam_angle", "sequence": "seul", "angle": "angle-1"}, message="au moins un angle")


def test_remove_angle_checks_every_lock_it_would_cross_before_changing_anything():
    project = _with_segment("angle-2")
    project.get_sequence("show").tracks[1].locked = True                       # la piste de l'angle, qui disparaîtrait
    _refused(project, {"op": "remove_multicam_angle", "sequence": "show", "angle": "angle-2", "replace_with": "angle-1"}, message="verrouillée")
    # Un segment de l'angle dans une AUTRE séquence que l'active, sur une piste verrouillée : il serait redirigé.
    project = _with_segment("angle-1")
    other = create_sequence(project, "Autre", sequence_id="autre")
    insert_multicam_clip(project, "show", other.tracks[0].id, 0.0, angle_id="angle-2", parent_sequence_id="autre", clip_id="seg2")
    other.tracks[0].locked = True
    _refused(project, {"op": "remove_multicam_angle", "sequence": "show", "angle": "angle-2", "replace_with": "angle-1"}, message="verrouillée")
    project.get_sequence("autre").tracks[0].locked = False
    session = _ok(project, {"op": "remove_multicam_angle", "sequence": "show", "angle": "angle-2", "replace_with": "angle-1"})
    assert session.project.get_sequence("autre").tracks[0].clips[0].angle_id == "angle-1"


def test_remove_angle_refuses_what_would_be_left_dangling_on_the_removed_track():
    project = _with_segment("angle-1")
    source = project.get_sequence("show")
    source.tracks[1].clips.append(Clip("x2", "cam2", "V2", 9.0, 0.0, 1.0, label="x2"))
    from core.transitions import Transition

    source.transitions.append(Transition(id="t1", type=TransitionType.CROSSFADE, from_clip_id=source.tracks[1].clips[0].id, to_clip_id="x2", duration=0.1))
    _refused(project, {"op": "remove_multicam_angle", "sequence": "show", "angle": "angle-2"}, message="transition")
    ducked = _with_segment("angle-1")
    show = ducked.get_sequence("show")
    show.tracks.append(Track("A9", "A9", "audio"))
    show.ducking_sidechains.append(DuckingSidechain(id="d1", music_track_id="A1", voice_track_id="A9"))
    _refused(ducked, {"op": "remove_multicam_angle", "sequence": "show", "angle": "angle-3"}, message="ducking")
    followed = _with_segment("angle-1")
    source = followed.get_sequence("show")
    source.tracks[0].clips[0].tracking = ClipTracking(links=(TrackLinkFor(source.tracks[1].clips[0].id),))
    _refused(followed, {"op": "remove_multicam_angle", "sequence": "show", "angle": "angle-2"}, message="suit un clip")


def TrackLinkFor(source_clip_id):
    from core.tracking_model import TrackLink

    return TrackLink(id="l1", source_clip_id=source_clip_id)


# --- set_multicam_angle_color ------------------------------------------------------------------------------------------


def test_set_angle_color_matches_the_model_and_is_bounded_by_the_palette():
    session = _ok(_with_source(), {"op": "set_multicam_angle_color", "sequence": "show", "angle": "Serré", "color_index": 7})
    twin = _with_source()
    set_angle_color(twin, "show", "angle-2", 7)
    assert session.project.get_sequence("show").multicam == twin.get_sequence("show").multicam
    for bad in (-1, ANGLE_PALETTE_SIZE, 10**9, 1.5, True, None, float("nan")):
        with pytest.raises(ScriptInvalid):
            parse_script(_script({"op": "set_multicam_angle_color", "sequence": "show", "angle": "angle-1", "color_index": bad}))
    _refused(_project(), {"op": "set_multicam_angle_color", "sequence": MAIN_SEQUENCE_ID, "angle": "angle-1", "color_index": 1}, message="Multicam")
    _refused(_with_source(), {"op": "set_multicam_angle_color", "sequence": "show", "angle": "inconnu", "color_index": 1}, message="introuvable")


# --- De bout en bout ---------------------------------------------------------------------------------------------------


def test_a_script_builds_a_multicam_from_clips_then_works_on_it():
    script = parse_script(_script(
        {"op": "create_multicam_from_clips", "as": "show", "segment_id": "seg", "clips": [{"clip": "c1", "name": "Large"}, {"clip": "c2", "name": "Serré"}]},
        {"op": "set_multicam_angle_color", "sequence": {"ref": "show"}, "angle": "Serré", "color_index": 4},
        {"op": "switch_multicam_angle", "at": 4, "angle": "Serré", "as": "plan2"},
        {"op": "remove_multicam_angle", "sequence": {"ref": "show"}, "angle": "Large", "replace_with": "Serré"},
    ))
    session = Session(_project())
    report = apply_script(session, script)
    assert report.ok, report.error
    source = session.project.get_sequence("show")
    assert [a.name for a in source.multicam.angles] == ["Serré"] and source.multicam.angles[0].color_index == 4
    assert {c.angle_id for t in session.project.tracks for c in t.clips if c.sequence_id == "show"} == {"angle-2"}
    assert created_ids(script, report)[0] == {"op": "0:create_multicam_from_clips", "id": "show"}
    assert session.undo() and len(session.project.sequences) == 1


def test_hostile_values_are_refused_without_echoing_them():
    huge = "x" * 5_000_000
    for op in (
        {"op": "create_multicam_from_clips", "clips": [{"clip": huge}, {"clip": "c2"}]}, {"op": "create_multicam_from_clips", "name": huge, "clips": [{"clip": "c1"}, {"clip": "c2"}]},
        {"op": "create_multicam_from_clips", "segment_id": huge, "clips": [{"clip": "c1"}, {"clip": "c2"}]},
        {"op": "remove_multicam_angle", "sequence": huge, "angle": "a"}, {"op": "flatten_multicam_segment", "clip": huge},
    ):
        with pytest.raises(ScriptInvalid) as caught:
            parse_script(_script(op))
        assert len(str(caught.value)) < 2_000
