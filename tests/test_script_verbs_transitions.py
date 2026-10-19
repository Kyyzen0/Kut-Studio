"""Scripts, transitions (lot 8 de l'extension 0.8.0) : `update_transition` et `apply_transition_preset`.

`update_transition` appelle `core.transitions.update_transition` (inspecteur de transition) ; `apply_transition_preset` pose le type et la durée par défaut
d'un préréglage **intégré** (`core.transition_presets.apply_transition_preset`). Les refus (verrou d'un des deux clips, durée trop longue, recalage qui
recouvrirait un autre clip, préréglage qui n'est pas intégré) laissent le projet strictement inchangé.
"""

from __future__ import annotations

import copy
import math
from dataclasses import replace

import pytest

from core.project_model import Clip, MediaAsset, Project, Track
from core.scripting import ScriptInvalid, Session, apply_script, parse_script
from core.scripting.schema import json_schema
from core.scripting.verbs import VERBS
from core.timeline_operations import find_clip
from core.transition_presets import builtin_transition_presets, make_user_transition_preset
from core.transition_presets import apply_transition_preset as direct_apply
from core.transitions import Transition, TransitionType, add_transition, update_transition
from ui import i18n


@pytest.fixture(autouse=True)
def _french(monkeypatch):
    monkeypatch.setenv("KUT_STUDIO_LANG", "fr")
    i18n.reset_for_tests()
    yield
    i18n.reset_for_tests()


def _bare() -> Project:
    return Project(
        "Démo", width=320, height=180, fps=30.0,
        media_assets=[MediaAsset("m", "/x/intro.mp4", "intro", 30.0, 320, 180, 30.0, "video", has_audio=True)],
        tracks=[
            Track("V1", "V1", "video", clips=[
                Clip("a", "m", "V1", 0.0, 0.0, 4.0), Clip("b", "m", "V1", 4.0, 0.0, 4.0), Clip("c", "m", "V1", 20.0, 0.0, 4.0),
            ]),
            Track("V2", "V2", "video", clips=[Clip("x", "m", "V2", 4.0, 0.0, 4.0)]),
            Track("V3", "V3", "video", locked=True, clips=[Clip("d", "m", "V3", 0.0, 0.0, 2.0), Clip("e", "m", "V3", 2.0, 0.0, 2.0)]),
            Track("A1", "A1", "audio"),
        ],
    )


def _joined() -> Project:
    """`a` -> `b` avec une transition de 0,5 s (`b` recalé à 3,5 s) ; `c` plus loin."""
    project = _bare()
    add_transition(project, "a", "b", TransitionType.CROSSFADE, 0.5, transition_id="t1")
    return project


def _op(verb: str, /, **fields) -> dict:
    return {"op": verb, **fields}


def _apply(project: Project, *ops: dict):
    session = Session(project)
    return session, apply_script(session, parse_script({"kut_script": 1, "ops": list(ops)}))


def _snapshot(project: Project):
    """Pistes et transitions ; l'identifiant d'une transition posée sans `id` est tiré au hasard : il ne compte pas."""
    return copy.deepcopy((project.tracks, [(t.from_clip_id, t.to_clip_id, t.type, t.duration) for t in project.transitions]))


def _parity(factory, op: dict, direct) -> Project:
    scripted = factory()
    session, report = _apply(scripted, op)
    assert report.ok, report.error
    reference = factory()
    direct(reference)
    assert _snapshot(session.project) == _snapshot(reference)
    return session.project


def _refused(project: Project, *ops: dict):
    before = _snapshot(project)
    session, report = _apply(project, *ops)
    assert not report.ok, "le script aurait dû être refusé"
    assert _snapshot(session.project) == before
    return report


def _invalid(*ops: dict) -> None:
    with pytest.raises(ScriptInvalid):
        parse_script({"kut_script": 1, "ops": list(ops)})


def test_the_verbs_are_in_the_table_with_the_right_shape():
    assert VERBS["update_transition"].creates is None and VERBS["apply_transition_preset"].creates == "transition"
    for name in ("update_transition", "apply_transition_preset"):
        assert VERBS[name].doc and VERBS[name].group == "edit"
    titles = {item["title"] for item in json_schema()["properties"]["ops"]["items"]["oneOf"]}
    assert {"update_transition", "apply_transition_preset"} <= titles


# --- update_transition -----------------------------------------------------------------------------------------------------


def test_update_transition_matches_the_model():
    project = _parity(_joined, _op("update_transition", transition="t1", type="wipe_left"), lambda p: update_transition(p, "t1", transition_type="wipe_left"))
    assert project.transitions[0].type is TransitionType.WIPE_LEFT and find_clip(project, "b").timeline_start == 3.5
    project = _parity(_joined, _op("update_transition", transition="t1", duration=1.0), lambda p: update_transition(p, "t1", duration=1.0))
    assert find_clip(project, "b").timeline_start == 3.0 and find_clip(project, "a").fade_out == 1.0 and find_clip(project, "b").fade_in == 1.0
    _parity(_joined, _op("update_transition", transition="t1", type="dissolve", duration=0.25),
            lambda p: update_transition(p, "t1", transition_type="dissolve", duration=0.25))


def test_update_transition_refuses_without_a_change_and_with_bad_values():
    assert "donnez" in _refused(_joined(), _op("update_transition", transition="t1")).error
    assert "moitié" in _refused(_joined(), _op("update_transition", transition="t1", duration=2.5)).error
    _refused(_joined(), _op("update_transition", transition="absente", duration=1.0))
    for bad in (0.049, 0.0, -1.0, math.nan, math.inf, 1e12, True):
        _invalid(_op("update_transition", transition="t1", duration=bad))
    _invalid(_op("update_transition", transition="t1", type="spirale"))
    _invalid(_op("update_transition", transition="t1", duration=1.0, extra=1))


def test_update_transition_never_overlaps_a_third_clip():
    """Raccourcir la transition recale le clip entrant vers la droite ; le modèle ne regarde pas ses voisins, un script reçoit le recalage refusé."""
    project = _joined()
    find_clip(project, "c").timeline_start = 7.5                      # c commence à la fin actuelle de `b` (3,5 + 4)
    report = _refused(project, _op("update_transition", transition="t1", duration=0.25))
    assert "recouvrir" in report.error and "'c'" in report.error
    _session, report = _apply(project, _op("update_transition", transition="t1", duration=1.0))      # il recule : aucun recouvrement
    assert report.ok, report.error


def test_update_transition_does_not_undo_the_junction_of_another_transition():
    project = _joined()
    project.transitions.append(Transition("t2", "b", "c", TransitionType.CROSSFADE, 0.5))
    find_clip(project, "c").timeline_start = 7.0
    assert "sortante" in _refused(project, _op("update_transition", transition="t1", duration=1.0)).error      # `b` reculerait : le recouvrement avec `c` changerait
    _session, report = _apply(project, _op("update_transition", transition="t1", type="dissolve"))     # le type seul ne recale rien
    assert report.ok, report.error


def test_update_transition_refuses_a_locked_clip_on_either_side():
    locked = _joined()
    locked.tracks[0].locked = True
    assert "verrouill" in _refused(locked, _op("update_transition", transition="t1", duration=1.0)).error
    assert "verrouill" in _refused(locked, _op("update_transition", transition="t1", type="dissolve")).error
    for clip in ("a", "b"):
        layered = _joined()
        target = find_clip(layered, clip)
        target.graphic = replace(_graphic(), locked=True)
        assert "verrouill" in _refused(layered, _op("update_transition", transition="t1", duration=1.0)).error


def _graphic():
    from core.graphics import GraphicOverlay, GraphicType

    return GraphicOverlay(type=GraphicType.TEXT)


# --- apply_transition_preset -----------------------------------------------------------------------------------------------


_PRESETS = {item.id: item for item in builtin_transition_presets()}


@pytest.mark.parametrize("preset_id", sorted(_PRESETS))
def test_apply_transition_preset_matches_the_model_for_every_builtin_preset(preset_id):
    preset = _PRESETS[preset_id]
    project = _parity(_bare, _op("apply_transition_preset", preset=preset_id, from_clip="a", to_clip="b"),
                      lambda p: direct_apply(p, "a", "b", preset))
    assert project.transitions[0].type is preset.transition_type and project.transitions[0].duration == preset.default_duration


def test_apply_transition_preset_with_a_duration_and_an_alias():
    preset = _PRESETS["wipe_left"]
    project = _parity(_bare, _op("apply_transition_preset", preset="wipe_left", from_clip="a", to_clip="b", duration=0.75),
                      lambda p: direct_apply(p, "a", "b", preset, 0.75))
    assert project.transitions[0].duration == 0.75
    session, report = _apply(_bare(), _op("apply_transition_preset", preset="crossfade", from_clip="a", to_clip="b", **{"as": "fondu"}),
                             _op("update_transition", transition={"ref": "fondu"}, duration=1.0))
    assert report.ok, report.error
    assert session.project.transitions[0].duration == 1.0
    _session, report = _apply(_bare(), _op("apply_transition_preset", preset="crossfade", from_clip="a", to_clip="b", id="mon_fondu"))
    assert report.ok and _session.project.transitions[0].id == "mon_fondu"


def test_apply_transition_preset_refuses_what_the_model_refuses_and_a_non_builtin_preset():
    assert "même piste" in _refused(_bare(), _op("apply_transition_preset", preset="crossfade", from_clip="a", to_clip="x")).error
    _refused(_bare(), _op("apply_transition_preset", preset="crossfade", from_clip="a", to_clip="c"))                 # ni adjacents ni chevauchants
    _refused(_bare(), _op("apply_transition_preset", preset="crossfade", from_clip="b", to_clip="a"))
    assert "moitié" in _refused(_bare(), _op("apply_transition_preset", preset="crossfade", from_clip="a", to_clip="b", duration=2.5)).error
    _refused(_joined(), _op("apply_transition_preset", preset="crossfade", from_clip="a", to_clip="b"))                # une transition existe déjà
    user = make_user_transition_preset("Mien", "", "crossfade", 1.0)
    for name in (user.id, "user-abc", "", "Crossfade", "crossfade "):
        _invalid(_op("apply_transition_preset", preset=name, from_clip="a", to_clip="b"))
    for bad in (0.01, math.nan, math.inf, 1e12):
        _invalid(_op("apply_transition_preset", preset="crossfade", from_clip="a", to_clip="b", duration=bad))
    assert tuple(VERBS["apply_transition_preset"].params[0].choices) == tuple(item.id for item in builtin_transition_presets())


def test_apply_transition_preset_never_overlaps_a_third_clip():
    project = _bare()
    project.tracks[0].clips.append(Clip("z", "m", "V1", 3.7, 0.0, 0.2))     # un clip posé dans la zone où `b` serait recalé (3,5 s à 7,5 s)
    assert "recouvrir" in _refused(project, _op("apply_transition_preset", preset="crossfade", from_clip="a", to_clip="b", duration=0.5)).error


def test_apply_transition_preset_refuses_a_locked_clip_other_kinds_and_audio():
    locked = _bare()
    locked.tracks[0].locked = True
    assert "verrouill" in _refused(locked, _op("apply_transition_preset", preset="crossfade", from_clip="a", to_clip="b")).error
    assert "verrouill" in _refused(_bare(), _op("apply_transition_preset", preset="crossfade", from_clip="d", to_clip="e")).error
    layered = _bare()
    find_clip(layered, "b").graphic = replace(_graphic(), locked=True)
    assert "verrouill" in _refused(layered, _op("apply_transition_preset", preset="crossfade", from_clip="a", to_clip="b")).error
    audio = _bare()
    audio.tracks[3].clips.extend([Clip("s1", "m", "A1", 0.0, 0.0, 2.0), Clip("s2", "m", "A1", 2.0, 0.0, 2.0)])
    assert "vise video" in _refused(audio, _op("apply_transition_preset", preset="crossfade", from_clip="s1", to_clip="s2")).error


def test_scripts_are_atomic_and_one_undo_step():
    session, report = _apply(
        _bare(),
        _op("apply_transition_preset", preset="wipe_left", from_clip="a", to_clip="b"),
        _op("update_transition", transition="t-introuvable", duration=1.0),
    )
    assert not report.ok and not session.project.transitions and find_clip(session.project, "b").timeline_start == 4.0
    session, report = _apply(_bare(), _op("apply_transition_preset", preset="wipe_left", from_clip="a", to_clip="b", **{"as": "t"}),
                             _op("update_transition", transition={"ref": "t"}, type="dissolve", duration=1.0))
    assert report.ok, report.error
    assert session.undo() and not session.project.transitions and find_clip(session.project, "b").timeline_start == 4.0


def test_huge_and_hostile_inputs_are_bounded_at_parse():
    with pytest.raises(ScriptInvalid) as caught:
        parse_script({"kut_script": 1, "ops": [_op("update_transition", transition="x" * 5_000_000, duration=1.0)]})
    assert len(str(caught.value)) < 10_000
    _invalid(_op("apply_transition_preset", preset="crossfade", from_clip="a", to_clip="x" * 5_000_000))
