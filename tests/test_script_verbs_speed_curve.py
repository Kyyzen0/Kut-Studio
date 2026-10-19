"""Scripts, courbe de vitesse (lot 8 de l'extension 0.8.0) : interpolation et tangentes des points, édition et retrait groupés, préréglages.

Chaque verbe appelle la fonction de `core.time_ops` / `core.time_presets` que l'éditeur de courbes et le menu « Vitesse » appellent : la parité se
teste contre l'appel direct. Les refus (point inconnu ou cité deux fois, déplacement hors du clip ou sur un autre point, pente que le rendu ignorerait,
clip figé, verrous, préréglage qui n'est pas intégré) laissent le projet strictement inchangé.
"""

from __future__ import annotations

import copy
import math

import pytest

from core.animation import InterpolationType
from core.project_model import Clip, MediaAsset, Project, Track
from core.scripting import ScriptInvalid, Session, apply_script, parse_script
from core.scripting.schema import json_schema
from core.scripting.verbs import VERBS
from core.time_ops import (
    RippleMode, add_speed_point, edit_speed_points, remove_speed_points, set_speed_point_interpolation, set_speed_tangents, speed_points, speed_ref,
)
from core.time_presets import TimePreset, apply_preset
from core.timeline_operations import find_clip, set_clip_freeze_frame
from ui import i18n

SPEED_VERBS = ("set_speed_point_interpolation", "set_speed_tangents", "edit_speed_points", "remove_speed_points", "apply_time_preset")


@pytest.fixture(autouse=True)
def _french(monkeypatch):
    monkeypatch.setenv("KUT_STUDIO_LANG", "fr")
    i18n.reset_for_tests()
    yield
    i18n.reset_for_tests()


def _project() -> Project:
    return Project(
        "Démo", width=320, height=180, fps=30.0,
        media_assets=[
            MediaAsset("m", "/x/intro.mp4", "intro", 20.0, 320, 180, 30.0, "video", has_audio=True),
            MediaAsset("s", "/x/son.wav", "son", 20.0, 0, 0, 0.0, "audio", has_audio=True),
        ],
        tracks=[
            Track("V1", "V1", "video", clips=[Clip("a", "m", "V1", 0.0, 0.0, 6.0)]),
            Track("V3", "V3", "video", locked=True, clips=[Clip("d", "m", "V3", 0.0, 0.0, 2.0)]),
            Track("A1", "A1", "audio", clips=[Clip("sound", "s", "A1", 0.0, 0.0, 4.0)]),
            Track("G1", "G1", "graphics"),
        ],
    )


def _curved(interpolation: InterpolationType = InterpolationType.LINEAR) -> Project:
    """Le clip `a` (6 s de source) avec trois points : 1x à 0 s, 2x à 1 s, 0,5x à 2 s."""
    project = _project()
    for moment, speed in ((0.0, 1.0), (1.0, 2.0), (2.0, 0.5)):
        add_speed_point(project, "a", moment, speed, interpolation=interpolation)
    return project


def _bezier() -> Project:
    return _curved(InterpolationType.BEZIER)


def _op(verb: str, /, **fields) -> dict:
    return {"op": verb, **fields}


def _apply(project: Project, *ops: dict):
    session = Session(project)
    return session, apply_script(session, parse_script({"kut_script": 1, "ops": list(ops)}))


def _snapshot(project: Project):
    return copy.deepcopy(project.tracks)


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


def _refs(project: Project, *moments: float):
    clip = find_clip(project, "a")
    by_time = {round(point.time_seconds, 6): point for point in speed_points(clip)}
    return [speed_ref(clip, by_time[moment]) for moment in moments]


def test_the_verbs_are_in_the_table_with_the_right_shape():
    assert set(SPEED_VERBS) <= set(VERBS)
    for name in SPEED_VERBS:
        assert VERBS[name].creates is None and VERBS[name].doc and VERBS[name].group == "time"
    titles = {item["title"] for item in json_schema()["properties"]["ops"]["items"]["oneOf"]}
    assert set(SPEED_VERBS) <= titles


# --- Interpolation ---------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("interpolation", [item.value for item in InterpolationType])
@pytest.mark.parametrize("ripple", ["source", "timeline"])
def test_set_speed_point_interpolation_matches_the_model(interpolation, ripple):
    def direct(project: Project) -> None:
        ids = [ref.keyframe_id for ref in _refs(project, 0.0, 1.0)]
        set_speed_point_interpolation(project, "a", ids, interpolation, mode=RippleMode(ripple))

    _parity(_curved, _op("set_speed_point_interpolation", clip="a", points=[0.0, 1.0], interpolation=interpolation, ripple=ripple), direct)


def test_set_speed_point_interpolation_errors():
    assert "Aucun point" in _refused(_curved(), _op("set_speed_point_interpolation", clip="a", points=[0.5], interpolation="hold")).error
    assert "deux instants" in _refused(_curved(), _op("set_speed_point_interpolation", clip="a", points=[1.0, 1.0], interpolation="hold")).error
    _invalid(_op("set_speed_point_interpolation", clip="a", points=[], interpolation="hold"))
    _invalid(_op("set_speed_point_interpolation", clip="a", points=[float(i) for i in range(65)], interpolation="hold"))
    _invalid(_op("set_speed_point_interpolation", clip="a", points=[1.0], interpolation="sinus"))


# --- Tangentes -------------------------------------------------------------------------------------------------------------


def test_set_speed_tangents_matches_the_model():
    def direct(project: Project) -> None:
        set_speed_tangents(project, _refs(project, 1.0), in_slope=0.5, out_slope=-0.5, mode="broken")

    project = _parity(_bezier, _op("set_speed_tangents", clip="a", points=[1.0], in_slope=0.5, out_slope=-0.5, mode="broken"), direct)
    middle = next(point for point in speed_points(find_clip(project, "a")) if point.time_seconds == 1.0)
    assert (middle.in_slope, middle.out_slope) == (0.5, -0.5)
    _parity(_bezier, _op("set_speed_tangents", clip="a", points=[0.0, 1.0], mode="broken"),
            lambda p: set_speed_tangents(p, _refs(p, 0.0, 1.0), mode="broken"))
    _parity(_bezier, _op("set_speed_tangents", clip="a", points=[1.0], auto=True), lambda p: set_speed_tangents(p, _refs(p, 1.0), auto=True))
    _parity(_bezier, _op("set_speed_tangents", clip="a", points=[1.0], out_slope=0.25, ripple="timeline"),
            lambda p: set_speed_tangents(p, _refs(p, 1.0), out_slope=0.25, ripple=RippleMode.TIMELINE))


def test_set_speed_tangents_refuses_what_the_editor_does_not_offer_or_the_model_would_drop():
    assert "au moins" in _refused(_bezier(), _op("set_speed_tangents", clip="a", points=[1.0])).error
    assert "exclut" in _refused(_bezier(), _op("set_speed_tangents", clip="a", points=[1.0], auto=True, in_slope=1.0)).error
    assert "égales" in _refused(_bezier(), _op("set_speed_tangents", clip="a", points=[1.0], in_slope=1.0, out_slope=2.0)).error
    assert "suivi" in _refused(_bezier(), _op("set_speed_tangents", clip="a", points=[2.0], out_slope=1.0)).error            # le dernier point
    assert "précédent" in _refused(_bezier(), _op("set_speed_tangents", clip="a", points=[0.0], in_slope=1.0)).error        # le premier point
    assert "bezier" in _refused(_curved(), _op("set_speed_tangents", clip="a", points=[1.0], out_slope=1.0)).error           # des segments linéaires
    assert "deux instants" in _refused(_bezier(), _op("set_speed_tangents", clip="a", points=[1.0, 1.0], mode="broken")).error
    for bad in (math.nan, math.inf, 1e7, True):
        _invalid(_op("set_speed_tangents", clip="a", points=[1.0], out_slope=bad))


# --- Édition groupée -------------------------------------------------------------------------------------------------------


def test_edit_speed_points_matches_the_model_in_one_transaction():
    def direct(project: Project) -> None:
        refs = _refs(project, 1.0, 2.0)
        edit_speed_points(project, refs, delta_seconds=0.5, values={refs[0]: 3.0, refs[1]: 1.0}, fps=None, mode=RippleMode.SOURCE)

    project = _parity(_curved, _op("edit_speed_points", clip="a", delta=0.5, points=[{"at": 1.0, "value": 3.0}, {"at": 2.0, "value": 1.0}]), direct)
    assert [round(p.time_seconds, 6) for p in speed_points(find_clip(project, "a"))] == [0.0, 1.5, 2.5]
    _parity(_curved, _op("edit_speed_points", clip="a", points=[{"at": 1.0, "value": 3.0}]),                    # une valeur seule
            lambda p: edit_speed_points(p, _refs(p, 1.0), values={_refs(p, 1.0)[0]: 3.0}))
    _parity(_curved, _op("edit_speed_points", clip="a", delta=-0.5, points=[{"at": 1.0}], ripple="timeline"),     # un déplacement seul
            lambda p: edit_speed_points(p, _refs(p, 1.0), delta_seconds=-0.5, mode=RippleMode.TIMELINE))


def test_edit_speed_points_refuses_what_the_model_would_clamp_or_overwrite():
    assert "donnez" in _refused(_curved(), _op("edit_speed_points", clip="a", points=[{"at": 1.0}])).error
    assert "donnez" in _refused(_curved(), _op("edit_speed_points", clip="a", delta=0.0, points=[{"at": 1.0}])).error
    assert "sortirait" in _refused(_curved(), _op("edit_speed_points", clip="a", delta=-1.5, points=[{"at": 1.0}])).error
    assert "remplacerait" in _refused(_curved(), _op("edit_speed_points", clip="a", delta=1.0, points=[{"at": 1.0}])).error      # arriverait sur le point de 2 s
    assert "Aucun point" in _refused(_curved(), _op("edit_speed_points", clip="a", points=[{"at": 1.5, "value": 2.0}])).error
    assert "deux instants" in _refused(_curved(), _op("edit_speed_points", clip="a", points=[{"at": 1.0, "value": 2.0}, {"at": 1.0}])).error
    for bad in (10.001, -10.001, math.nan, math.inf, True):
        _invalid(_op("edit_speed_points", clip="a", points=[{"at": 1.0, "value": bad}]))
    _invalid(_op("edit_speed_points", clip="a", points=[{"at": 1.0, "speed": 2.0}]))
    _invalid(_op("edit_speed_points", clip="a", points=[{"at": float(i), "value": 1.0} for i in range(65)]))


# --- Retrait groupé --------------------------------------------------------------------------------------------------------


def test_remove_speed_points_matches_the_model():
    project = _parity(_curved, _op("remove_speed_points", clip="a", points=[1.0, 2.0]), lambda p: remove_speed_points(p, _refs(p, 1.0, 2.0)))
    assert len(speed_points(find_clip(project, "a"))) == 1
    project = _parity(_curved, _op("remove_speed_points", clip="a", points=[0.0, 1.0, 2.0]), lambda p: remove_speed_points(p, _refs(p, 0.0, 1.0, 2.0)))
    assert not speed_points(find_clip(project, "a"))                                       # plus de courbe : la vitesse constante qu'elle affichait
    _parity(_curved, _op("remove_speed_points", clip="a", points=[2.0], ripple="timeline"),
            lambda p: remove_speed_points(p, _refs(p, 2.0), mode=RippleMode.TIMELINE))


def test_remove_speed_points_errors():
    assert "Aucun point" in _refused(_curved(), _op("remove_speed_points", clip="a", points=[1.0, 1.7])).error
    assert "deux instants" in _refused(_curved(), _op("remove_speed_points", clip="a", points=[1.0, 1.0])).error
    _refused(_project(), _op("remove_speed_points", clip="a", points=[1.0]))                  # pas de courbe


# --- Préréglages -----------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("preset", [item.value for item in TimePreset if item is not TimePreset.FREEZE])
@pytest.mark.parametrize("factory", [_project, _curved])
def test_apply_time_preset_matches_the_model(preset, factory):
    _parity(factory, _op("apply_time_preset", clip="a", preset=preset), lambda p: apply_preset(p, "a", TimePreset(preset), fps=30.0))


def test_apply_time_preset_on_an_audio_clip_and_with_timeline_ripple_matches_the_model():
    _parity(_project, _op("apply_time_preset", clip="sound", preset="slow_50"), lambda p: apply_preset(p, "sound", TimePreset.SLOW_50, fps=30.0))
    _parity(_curved, _op("apply_time_preset", clip="a", preset="ramp_in", ripple="timeline"),
            lambda p: apply_preset(p, "a", TimePreset.RAMP_IN, fps=30.0, mode=RippleMode.TIMELINE))


def test_the_freeze_preset_needs_an_instant_on_a_frame_and_the_others_refuse_one():
    project = _parity(_project, _op("apply_time_preset", clip="a", preset="freeze", at=1.0),
                      lambda p: apply_preset(p, "a", TimePreset.FREEZE, local_time=1.0, fps=30.0))
    assert find_clip(project, "a").duration == 7.0
    assert "demande" in _refused(_project(), _op("apply_time_preset", clip="a", preset="freeze")).error
    assert "image de la timeline" in _refused(_project(), _op("apply_time_preset", clip="a", preset="freeze", at=1.01)).error
    assert "image de la timeline" in _refused(_project(), _op("apply_time_preset", clip="a", preset="freeze", at=6.5)).error
    assert "ne sert qu'au" in _refused(_project(), _op("apply_time_preset", clip="a", preset="slow_25", at=1.0)).error


def test_only_the_builtin_presets_are_accepted():
    for name in ("user-1", "slow_50 ", "SLOW_50", "slow-50", ""):
        _invalid(_op("apply_time_preset", clip="a", preset=name))
    assert tuple(VERBS["apply_time_preset"].params[1].choices) == tuple(item.value for item in TimePreset)


# --- Clip figé, verrous, genres de piste -----------------------------------------------------------------------------------


def _frozen() -> Project:
    project = _curved()
    set_clip_freeze_frame(project, "a", 2.0, 3.0)
    return project


_SAMPLES = [
    _op("set_speed_point_interpolation", clip="CLIP", points=[1.0], interpolation="hold"),
    _op("set_speed_tangents", clip="CLIP", points=[1.0], mode="broken"),
    _op("edit_speed_points", clip="CLIP", points=[{"at": 1.0, "value": 2.5}]),
    _op("remove_speed_points", clip="CLIP", points=[1.0]),
    _op("apply_time_preset", clip="CLIP", preset="slow_50"),
]


def _with_clip(operation: dict, clip: str) -> dict:
    return {key: (clip if value == "CLIP" else value) for key, value in operation.items()}


@pytest.mark.parametrize("operation", _SAMPLES, ids=lambda item: item["op"])
def test_a_locked_track_refuses_every_speed_curve_verb(operation):
    locked = _bezier()
    locked.tracks[0].locked = True
    assert "verrouill" in _refused(locked, _with_clip(operation, "a")).error
    _refused(_bezier(), _with_clip(operation, "d"))                                          # `d` est sur V3, verrouillée


@pytest.mark.parametrize("operation", _SAMPLES, ids=lambda item: item["op"])
def test_a_locked_layer_and_a_graphics_clip_are_refused(operation):
    project = _bezier()
    session, report = _apply(project, _op("add_graphic", graphic="text", at=0, duration=3, id="t1", text="A", track="G1"))
    assert report.ok, report.error
    _refused(session.project, _with_clip(operation, "t1"))
    from dataclasses import replace

    layer = find_clip(session.project, "t1")
    layer.graphic = replace(layer.graphic, locked=True)
    assert "verrouill" in _refused(session.project, _with_clip(operation, "t1")).error


@pytest.mark.parametrize("operation", _SAMPLES, ids=lambda item: item["op"])
def test_a_frozen_clip_has_no_speed_to_edit(operation):
    _refused(_frozen(), _with_clip(operation, "a"))


# --- Atomicité, annulation, de bout en bout ---------------------------------------------------------------------------------


def test_scripts_are_atomic_and_one_undo_step():
    session, report = _apply(
        _curved(),
        _op("edit_speed_points", clip="a", points=[{"at": 1.0, "value": 3.0}]),
        _op("set_speed_point_interpolation", clip="a", points=[0.0], interpolation="hold"),
        _op("apply_time_preset", clip="sound", preset="fast_2"),
    )
    assert report.ok, report.error
    assert find_clip(session.project, "sound").time_remapping.speed == 2.0
    assert session.undo()
    assert find_clip(session.project, "sound").time_remapping.speed == 1.0
    assert [p.value for p in speed_points(find_clip(session.project, "a"))] == [1.0, 2.0, 0.5]
    _refused(_curved(), _op("apply_time_preset", clip="a", preset="ramp_out"), _op("remove_speed_points", clip="a", points=[99.0]))   # la 2e échoue


def test_speed_ramp_end_to_end_with_aliases():
    session, report = _apply(
        _project(),
        _op("cut_clip", clip="a", at=3.0, **{"as": "droite"}),
        _op("apply_time_preset", clip={"ref": "droite"}, preset="ramp_in"),
        _op("set_speed_point_interpolation", clip={"ref": "droite"}, points=[0.0], interpolation="bezier"),
        _op("set_speed_tangents", clip={"ref": "droite"}, points=[0.0], out_slope=0.5),
        _op("edit_speed_points", clip={"ref": "droite"}, points=[{"at": 0.0, "value": 0.5}]),
    )
    assert report.ok, report.error
    points = speed_points(find_clip(session.project, "droite"))
    assert len(points) == 2 and points[0].value == 0.5 and points[0].out_slope == 0.5
    assert session.undo() and not speed_points(find_clip(session.project, "a"))


def test_huge_and_hostile_inputs_are_bounded_at_parse():
    with pytest.raises(ScriptInvalid) as caught:
        parse_script({"kut_script": 1, "ops": [_op("remove_speed_points", clip="x" * 5_000_000, points=[1.0])]})
    assert len(str(caught.value)) < 10_000
    _invalid(_op("remove_speed_points", clip="a", points=[math.nan]))
    _invalid(_op("remove_speed_points", clip="a", points=[1e12]))
    _invalid(_op("remove_speed_points", clip="a", points="all"))
    _invalid(_op("edit_speed_points", clip="a", delta=1e12, points=[{"at": 1.0}]))
    _invalid(_op("apply_time_preset", clip="a", preset="slow_50", extra=1))
