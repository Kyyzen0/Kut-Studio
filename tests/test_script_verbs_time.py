"""Format déclaratif : vitesse et remappage du temps (lot 2 de l'extension 0.8.0) : `set_clip_speed`, `add_speed_point`, `insert_hold`…

Chaque verbe appelle la fonction du modèle que l'interface appelle : le test de parité fait le même appel directement sur une copie et
compare les projets. Les refus (verrous, bornes que le modèle ramènerait, instants que le modèle aligne) sont ceux d'un script, sans écriture partielle.
"""

from __future__ import annotations

import copy
import math
from dataclasses import replace

import pytest

from core.animation import InterpolationType
from core.project_model import Clip, MediaAsset, Project, Track
from core.scripting import ScriptInvalid, Session, apply_script, parse_script
from core.scripting.schema import json_schema
from core.scripting.verbs import VERBS
from core.time_ops import (
    RippleMode, add_speed_point, clear_speed_curve, insert_hold, move_speed_points, remove_speed_point, set_clip_interpolation,
    set_clip_preserve_pitch, set_clip_remap_audio, set_speed_point_value, speed_points, speed_ref,
)
from core.time_remapping import FreezeFrameMode
from core.timeline_operations import (
    find_clip, remove_clip_freeze_frame, reset_clip_time_remapping, set_clip_freeze_duration, set_clip_freeze_frame, set_clip_reverse, set_clip_speed,
)
from core.visual_effects import TransformKeyframe
from ui import i18n

TIME_VERBS = (
    "set_clip_speed", "set_clip_reverse", "set_clip_freeze_frame", "remove_clip_freeze_frame", "set_clip_freeze_duration",
    "reset_clip_time_remapping", "add_speed_point", "set_speed_point_value", "remove_speed_point", "move_speed_points", "clear_speed_curve",
    "insert_hold", "set_clip_interpolation", "set_clip_preserve_pitch", "set_clip_remap_audio",
)


@pytest.fixture(autouse=True)
def _french(monkeypatch):
    monkeypatch.setenv("KUT_STUDIO_LANG", "fr")
    i18n.reset_for_tests()
    yield
    i18n.reset_for_tests()


def _project(*, locked_v1: bool = False) -> Project:
    return Project(
        "Démo", width=320, height=180, fps=30.0,
        media_assets=[
            MediaAsset("m", "/x/intro.mp4", "intro", 20.0, 320, 180, 30.0, "video", has_audio=True),
            MediaAsset("s", "/x/son.wav", "son", 20.0, 0, 0, 0.0, "audio", has_audio=True),
        ],
        tracks=[
            Track("V1", "V1", "video", locked=locked_v1, clips=[
                Clip("a", "m", "V1", 0.0, 0.0, 6.0),
                Clip("b", "m", "V1", 30.0, 2.0, 5.0),
            ]),
            Track("V2", "V2", "video", clips=[Clip("c", "m", "V2", 1.0, 0.0, 2.0)]),
            Track("V3", "V3", "video", locked=True, clips=[Clip("d", "m", "V3", 0.0, 0.0, 2.0)]),
            Track("A1", "A1", "audio", clips=[Clip("sound", "s", "A1", 0.0, 0.0, 4.0)]),
        ],
    )


def _op(verb: str, /, **fields) -> dict:
    return {"op": verb, **fields}


def _apply(project: Project, *ops: dict):
    session = Session(project)
    return session, apply_script(session, parse_script({"kut_script": 1, "ops": list(ops)}))


def _snapshot(project: Project):
    return copy.deepcopy(project.tracks)


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
    assert not report.ok, "le script aurait dû être refusé"
    assert _snapshot(session.project) == before
    return report


def _invalid(*ops: dict) -> None:
    with pytest.raises(ScriptInvalid):
        parse_script({"kut_script": 1, "ops": list(ops)})


def _curved() -> Project:
    """Le clip `a` (6 s de source) avec trois points : 1x à 0 s, 2x à 1 s, 0,5x à 2 s."""
    project = _project()
    for moment, speed in ((0.0, 1.0), (1.0, 2.0), (2.0, 0.5)):
        add_speed_point(project, "a", moment, speed, interpolation=InterpolationType.LINEAR)
    return project


def _frozen() -> Project:
    project = _project()
    set_clip_freeze_frame(project, "a", 2.0, 3.0)
    return project


def test_the_verbs_are_in_the_table_with_the_right_shape():
    assert set(TIME_VERBS) <= set(VERBS)
    for name in TIME_VERBS:
        assert VERBS[name].creates is None and VERBS[name].doc and VERBS[name].group == "time"
    titles = {item["title"] for item in json_schema()["properties"]["ops"]["items"]["oneOf"]}
    assert set(TIME_VERBS) <= titles


# --- Vitesse constante, sens ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("speed", [0.05, 0.25, 0.5, 2.0, 10.0])
def test_set_clip_speed_matches_the_model(speed):
    project = _parity(_project, _op("set_clip_speed", clip="a", speed=speed), lambda p: set_clip_speed(p, "a", speed))
    assert find_clip(project, "a").time_remapping.speed == speed


def test_set_clip_speed_on_an_audio_clip_and_with_a_curve_matches_the_model():
    _parity(_project, _op("set_clip_speed", clip="sound", speed=2.0), lambda p: set_clip_speed(p, "sound", 2.0))
    project = _parity(_curved, _op("set_clip_speed", clip="a", speed=2.0), lambda p: set_clip_speed(p, "a", 2.0))
    assert not speed_points(find_clip(project, "a"))                   # la vitesse constante remplace la courbe, comme dans l'inspecteur


@pytest.mark.parametrize("speed", [0.049, 0.0, -1.0, 10.001, 100.0, math.nan, math.inf, True, "2"])
def test_set_clip_speed_refuses_what_the_model_would_clamp(speed):
    _invalid(_op("set_clip_speed", clip="a", speed=speed))


def test_set_clip_reverse_matches_the_model():
    project = _parity(_project, _op("set_clip_reverse", clip="a", reverse=True), lambda p: set_clip_reverse(p, "a", True))
    assert find_clip(project, "a").is_reversed
    _parity(_project, _op("set_clip_reverse", clip="a", reverse=False), lambda p: set_clip_reverse(p, "a", False))
    _parity(_curved, _op("set_clip_reverse", clip="a", reverse=True), lambda p: set_clip_reverse(p, "a", True))


def test_speed_keeps_an_animated_clip_coherent_like_the_model():
    def animated() -> Project:
        project = _project()
        clip = find_clip(project, "a")
        clip.transform_keyframes = [TransformKeyframe("position_x", 0.0, 0.0), TransformKeyframe("position_x", 5.5, 1.0)]
        return project

    project = _parity(animated, _op("set_clip_speed", clip="a", speed=2.0), lambda p: set_clip_speed(p, "a", 2.0))
    assert find_clip(project, "a").duration == 3.0
    assert [k.time_seconds for k in find_clip(project, "a").transform_keyframes] != [0.0, 5.5]      # recadrées sur la nouvelle durée


# --- Arrêt sur image -----------------------------------------------------------------------------------------------------


def test_freeze_frame_verbs_match_the_model():
    project = _parity(_project, _op("set_clip_freeze_frame", clip="a", source_time=2.0, duration=3.0),
                      lambda p: set_clip_freeze_frame(p, "a", 2.0, 3.0))
    assert find_clip(project, "a").time_remapping.freeze_mode == FreezeFrameMode.FREEZE and find_clip(project, "a").duration == 3.0
    _parity(_frozen, _op("set_clip_freeze_duration", clip="a", duration=1.5), lambda p: set_clip_freeze_duration(p, "a", 1.5))
    _parity(_frozen, _op("remove_clip_freeze_frame", clip="a"), lambda p: remove_clip_freeze_frame(p, "a"))
    _parity(_curved, _op("set_clip_freeze_frame", clip="a", source_time=1.0, duration=2.0),
            lambda p: set_clip_freeze_frame(p, "a", 1.0, 2.0))                                  # la courbe de vitesse est retirée


def test_freeze_frame_errors():
    _refused(_project(), _op("set_clip_freeze_frame", clip="a", source_time=7.0, duration=1.0))      # hors de la fenêtre [0, 6]
    _refused(_project(), _op("set_clip_freeze_frame", clip="b", source_time=1.0, duration=1.0))      # avant le début de la fenêtre [2, 7]
    _refused(_project(), _op("set_clip_freeze_frame", clip="sound", source_time=1.0, duration=1.0))  # l'audio ne se fige pas
    for bad in (0.0, 0.009, -1.0, 3601.0, math.nan, math.inf):
        _invalid(_op("set_clip_freeze_frame", clip="a", source_time=1.0, duration=bad))
        _invalid(_op("set_clip_freeze_duration", clip="a", duration=bad))
    _invalid(_op("set_clip_freeze_frame", clip="a", source_time=-1.0, duration=1.0))


def test_freeze_duration_and_removal_refuse_a_clip_that_is_not_frozen():
    """Sans ce refus, le modèle écrirait une durée sans effet, ou remettrait vitesse et sens à zéro en silence."""
    project = _project()
    set_clip_speed(project, "a", 2.0)
    report = _refused(project, _op("set_clip_freeze_duration", clip="a", duration=2.0))
    assert "arrêt sur image" in str(report.error)
    report = _refused(project, _op("remove_clip_freeze_frame", clip="a"))
    assert "arrêt sur image" in str(report.error) and find_clip(project, "a").time_remapping.speed == 2.0


def test_reset_clip_time_remapping_matches_the_model():
    def busy() -> Project:
        project = _curved()
        set_clip_reverse(project, "a", True)
        return project

    project = _parity(busy, _op("reset_clip_time_remapping", clip="a"), lambda p: reset_clip_time_remapping(p, "a"))
    assert not speed_points(find_clip(project, "a")) and not find_clip(project, "a").is_reversed
    _parity(_frozen, _op("reset_clip_time_remapping", clip="a"), lambda p: reset_clip_time_remapping(p, "a"))


# --- Courbe de vitesse ---------------------------------------------------------------------------------------------------


def test_add_speed_point_matches_the_model():
    project = _parity(_project, _op("add_speed_point", clip="a", at=2.0, value=2.0),
                      lambda p: add_speed_point(p, "a", 2.0, 2.0))
    # Première courbe posée avec une valeur : le modèle ajoute un point d'amorce à 0 s (un palier) pour garder la vitesse d'avant.
    assert [(k.time_seconds, k.value) for k in speed_points(find_clip(project, "a"))] == [(0.0, 1.0), (2.0, 2.0)]
    _parity(_project, _op("add_speed_point", clip="a", at=2.0), lambda p: add_speed_point(p, "a", 2.0))      # sans valeur : la vitesse actuelle
    _parity(_curved, _op("add_speed_point", clip="a", at=1.5, value=-1.0, interpolation="hold"),
            lambda p: add_speed_point(p, "a", 1.5, -1.0, interpolation=InterpolationType.HOLD))
    _parity(_curved, _op("add_speed_point", clip="a", at=1.5, value=4.0, ripple="timeline"),
            lambda p: add_speed_point(p, "a", 1.5, 4.0, mode=RippleMode.TIMELINE))
    _parity(_curved, _op("add_speed_point", clip="a", at=1.5, value=4.0, ripple="source"),
            lambda p: add_speed_point(p, "a", 1.5, 4.0, mode=RippleMode.SOURCE))


def test_the_ripple_modes_differ_as_documented():
    source = _parity(_curved, _op("add_speed_point", clip="a", at=1.5, value=4.0), lambda p: add_speed_point(p, "a", 1.5, 4.0))
    timeline = _parity(_curved, _op("add_speed_point", clip="a", at=1.5, value=4.0, ripple="timeline"),
                       lambda p: add_speed_point(p, "a", 1.5, 4.0, mode=RippleMode.TIMELINE))
    assert find_clip(source, "a").duration != find_clip(timeline, "a").duration


def test_add_speed_point_errors():
    _refused(_project(), _op("add_speed_point", clip="a", at=99.0, value=2.0))                      # hors du clip : le modèle le refuse déjà
    _refused(_frozen(), _op("add_speed_point", clip="a", at=1.0, value=2.0))                        # un arrêt sur image n'a pas de vitesse
    for bad in (10.001, -10.001, math.nan, math.inf):
        _invalid(_op("add_speed_point", clip="a", at=1.0, value=bad))
    _invalid(_op("add_speed_point", clip="a", at=-0.1, value=1.0))
    _invalid(_op("add_speed_point", clip="a", at=1.0, value=1.0, ripple="les deux"))
    _invalid(_op("add_speed_point", clip="a", at=1.0, value=1.0, interpolation="saut"))
    for value in (10.0, -10.0, 0.0):
        _session, report = _apply(_project(), _op("add_speed_point", clip="a", at=1.0, value=value))
        assert report.ok, report.error


def test_set_and_remove_speed_point_match_the_model():
    def first_point(project: Project, at: float):
        return next(k for k in speed_points(find_clip(project, "a")) if abs(k.time_seconds - at) < 1e-6)

    _parity(_curved, _op("set_speed_point_value", clip="a", at=1.0, value=3.0),
            lambda p: set_speed_point_value(p, "a", first_point(p, 1.0).id, 3.0))
    _parity(_curved, _op("set_speed_point_value", clip="a", at=1.0, value=3.0, ripple="timeline"),
            lambda p: set_speed_point_value(p, "a", first_point(p, 1.0).id, 3.0, mode=RippleMode.TIMELINE))
    project = _parity(_curved, _op("remove_speed_point", clip="a", at=1.0), lambda p: remove_speed_point(p, "a", first_point(p, 1.0).id))
    assert [k.time_seconds for k in speed_points(find_clip(project, "a"))] == [0.0, 2.0]
    # Le dernier point retiré laisse la vitesse constante qu'il affichait.
    one = _project()
    add_speed_point(one, "a", 0.0, 2.0)
    project = _parity(lambda: copy.deepcopy(one), _op("remove_speed_point", clip="a", at=0.0), lambda p: remove_speed_point(p, "a", first_point(p, 0.0).id))
    assert not speed_points(find_clip(project, "a"))


def test_speed_point_errors():
    _refused(_curved(), _op("set_speed_point_value", clip="a", at=1.5, value=3.0))               # aucun point à cet instant
    _refused(_curved(), _op("remove_speed_point", clip="a", at=1.5))
    _refused(_project(), _op("set_speed_point_value", clip="a", at=0.0, value=3.0))              # pas de courbe
    _refused(_frozen(), _op("remove_speed_point", clip="a", at=0.0))
    for bad in (10.5, math.nan):
        _invalid(_op("set_speed_point_value", clip="a", at=1.0, value=bad))
    # Zéro est une vitesse (un arrêt) : le modèle l'accepte, le script aussi.
    _session, report = _apply(_curved(), _op("set_speed_point_value", clip="a", at=1.0, value=0.0))
    assert report.ok, report.error


def test_move_speed_points_matches_the_model():
    def refs(project: Project, *moments: float):
        clip = find_clip(project, "a")
        return [speed_ref(clip, k) for k in speed_points(clip) if any(abs(k.time_seconds - m) < 1e-6 for m in moments)]

    project = _parity(_curved, _op("move_speed_points", clip="a", points=[1.0], delta=0.5),
                      lambda p: move_speed_points(p, refs(p, 1.0), 0.5))
    assert [k.time_seconds for k in speed_points(find_clip(project, "a"))][1] > 1.0
    _parity(_curved, _op("move_speed_points", clip="a", points=[1.0, 2.0], delta=-0.5),
            lambda p: move_speed_points(p, refs(p, 1.0, 2.0), -0.5))
    _parity(_curved, _op("move_speed_points", clip="a", points=[1.0], delta=0.5, ripple="timeline"),
            lambda p: move_speed_points(p, refs(p, 1.0), 0.5, mode=RippleMode.TIMELINE))


def test_move_speed_points_refuses_what_the_model_would_clamp_or_overwrite():
    _refused(_curved(), _op("move_speed_points", clip="a", points=[1.0], delta=-3.0))            # avant 0 s : le modèle l'aurait ramené à 0
    _refused(_curved(), _op("move_speed_points", clip="a", points=[2.0], delta=500.0))            # au-delà de la fin
    _refused(_curved(), _op("move_speed_points", clip="a", points=[1.0, 2.0], delta=-1.5))        # un seul sortirait : tout le groupe est refusé
    report = _refused(_curved(), _op("move_speed_points", clip="a", points=[1.0], delta=1.0))     # arrive sur le point de 2 s, qu'il remplacerait
    assert "remplacerait" in str(report.error)
    _refused(_curved(), _op("move_speed_points", clip="a", points=[1.0], delta=0.0))
    _refused(_curved(), _op("move_speed_points", clip="a", points=[1.0, 1.0], delta=0.5))         # le même point deux fois
    _refused(_curved(), _op("move_speed_points", clip="a", points=[1.5], delta=0.5))              # aucun point à cet instant
    _invalid(_op("move_speed_points", clip="a", points=[], delta=0.5))
    _invalid(_op("move_speed_points", clip="a", points=[float(i) for i in range(65)], delta=0.1))   # liste démesurée
    _invalid(_op("move_speed_points", clip="a", points=[1.0], delta=math.nan))
    # Deux points qui se croisent sont permis quand ils vont ensemble : le décalage est le même.
    _session, report = _apply(_curved(), _op("move_speed_points", clip="a", points=[1.0, 2.0], delta=1.5))
    assert report.ok, report.error


def test_clear_speed_curve_matches_the_model():
    project = _parity(_curved, _op("clear_speed_curve", clip="a"), lambda p: clear_speed_curve(p, "a"))
    assert not speed_points(find_clip(project, "a"))
    _parity(_project, _op("clear_speed_curve", clip="a"), lambda p: clear_speed_curve(p, "a"))      # sans courbe : rien


def test_insert_hold_matches_the_model_with_the_project_frame_rate():
    project = _parity(_project, _op("insert_hold", clip="a", at=2.0), lambda p: insert_hold(p, "a", 2.0, 1.0, fps=30.0))
    assert find_clip(project, "a").duration == 7.0                                                # 6 s + 1 s d'arrêt
    _parity(_project, _op("insert_hold", clip="a", at=2.0, duration=0.5), lambda p: insert_hold(p, "a", 2.0, 0.5, fps=30.0))
    _parity(_curved, _op("insert_hold", clip="a", at=1.0, duration=2.0), lambda p: insert_hold(p, "a", 1.0, 2.0, fps=30.0))
    _parity(_curved, _op("insert_hold", clip="a", at=1.0, ripple="timeline"),
            lambda p: insert_hold(p, "a", 1.0, 1.0, fps=30.0, mode=RippleMode.TIMELINE))
    # Un clip qui ne commence pas sur une image : la grille est celle de la timeline.
    def off_grid() -> Project:
        project = _project()
        find_clip(project, "a").timeline_start = 0.01
        return project

    _session, report = _apply(off_grid(), _op("insert_hold", clip="a", at=1.0))
    assert not report.ok and "image" in str(report.error)


def test_insert_hold_refuses_an_instant_the_interface_would_align_or_bring_back():
    for moment in (2.01, 1.999, 0.017):                     # entre deux images à 30 i/s
        report = _refused(_project(), _op("insert_hold", clip="a", at=moment))
        assert "image" in str(report.error)
    _refused(_project(), _op("insert_hold", clip="a", at=6.5))              # au-delà du clip : l'interface le ramènerait à la fin
    _refused(_frozen(), _op("insert_hold", clip="a", at=1.0))
    for bad in (0.0, 0.009, 3601.0, math.nan, -1.0):
        _invalid(_op("insert_hold", clip="a", at=1.0, duration=bad))
    _session, report = _apply(_project(), _op("insert_hold", clip="a", at=6.0))                    # la fin du clip est une image : permis
    assert report.ok, report.error


# --- Images intermédiaires, audio ---------------------------------------------------------------------------------------


def test_interpolation_and_audio_options_match_the_model():
    project = _parity(_project, _op("set_clip_interpolation", clip="a", interpolation="blending"),
                      lambda p: set_clip_interpolation(p, "a", "blending"))
    assert find_clip(project, "a").time_remapping.interpolation.value == "blending"
    _parity(_project, _op("set_clip_interpolation", clip="a", interpolation="optical_flow", quality="best"),
            lambda p: set_clip_interpolation(p, "a", "optical_flow", "best"))

    def blending() -> Project:
        project = _project()
        set_clip_interpolation(project, "a", "blending")
        return project

    # Seule la qualité : le mode actuel est gardé (la commande « qualité » de l'interface).
    project = _parity(blending, _op("set_clip_interpolation", clip="a", quality="draft"),
                      lambda p: set_clip_interpolation(p, "a", find_clip(p, "a").time_remapping.interpolation, "draft"))
    assert find_clip(project, "a").time_remapping.interpolation.value == "blending"
    _parity(_project, _op("set_clip_preserve_pitch", clip="sound", enabled=False), lambda p: set_clip_preserve_pitch(p, "sound", False))
    _parity(_project, _op("set_clip_remap_audio", clip="a", enabled=False), lambda p: set_clip_remap_audio(p, "a", False))
    _parity(_project, _op("set_clip_remap_audio", clip="a", enabled=True), lambda p: set_clip_remap_audio(p, "a", True))


def test_interpolation_errors():
    _refused(_project(), _op("set_clip_interpolation", clip="a"))                                  # ni mode ni qualité
    _refused(_frozen(), _op("set_clip_interpolation", clip="a", interpolation="blending"))
    _invalid(_op("set_clip_interpolation", clip="a", interpolation="magie"))
    _invalid(_op("set_clip_interpolation", clip="a", quality="parfaite"))


# --- Verrous, genres de piste ---------------------------------------------------------------------------------------------

_SAMPLES = [
    _op("set_clip_speed", clip="CLIP", speed=2.0), _op("set_clip_reverse", clip="CLIP", reverse=True),
    _op("set_clip_freeze_frame", clip="CLIP", source_time=1.0, duration=1.0), _op("reset_clip_time_remapping", clip="CLIP"),
    _op("add_speed_point", clip="CLIP", at=1.0, value=2.0), _op("clear_speed_curve", clip="CLIP"),
    _op("insert_hold", clip="CLIP", at=1.0), _op("set_clip_interpolation", clip="CLIP", interpolation="blending"),
    _op("set_clip_preserve_pitch", clip="CLIP", enabled=False), _op("set_clip_remap_audio", clip="CLIP", enabled=False),
]


def _with_clip(operation: dict, clip: str) -> dict:
    return {key: (clip if value == "CLIP" else value) for key, value in operation.items()}


@pytest.mark.parametrize("operation", _SAMPLES, ids=lambda item: item["op"])
def test_a_locked_track_refuses_every_time_verb(operation):
    report = _refused(_project(locked_v1=True), _with_clip(operation, "a"))
    assert "verrouill" in str(report.error)
    _refused(_project(), _with_clip(operation, "d"))                                               # `d` est sur V3, verrouillée


def test_locked_track_refuses_the_verbs_that_need_an_existing_point_or_state():
    locked = _curved()
    locked.tracks[0].locked = True
    for operation in (
        _op("set_speed_point_value", clip="a", at=1.0, value=3.0), _op("remove_speed_point", clip="a", at=1.0),
        _op("move_speed_points", clip="a", points=[1.0], delta=0.5),
    ):
        report = _refused(locked, operation)
        assert "verrouill" in str(report.error)
    frozen = _frozen()
    frozen.tracks[0].locked = True
    for operation in (_op("remove_clip_freeze_frame", clip="a"), _op("set_clip_freeze_duration", clip="a", duration=2.0)):
        assert "verrouill" in str(_refused(frozen, operation).error)


def test_a_locked_layer_and_other_kinds_of_track_are_refused():
    project = _project()
    project.tracks.append(Track("G1", "G1", "graphics"))
    project.tracks.append(Track("S1", "S1", "subtitle"))
    session, report = _apply(project, _op("add_graphic", graphic="text", at=0, duration=3, id="t1", text="A", track="G1"),
                             _op("add_subtitle", text="x", at=0, duration=2, track="S1", id="sub"))
    assert report.ok, report.error
    for clip in ("t1", "sub"):
        for operation in _SAMPLES:
            _refused(session.project, _with_clip(operation, clip))
    layer = find_clip(session.project, "t1")
    layer.graphic = replace(layer.graphic, locked=True)
    assert "verrouill" in str(_refused(session.project, _op("set_clip_speed", clip="t1", speed=2.0)).error)


# --- Atomicité, annulation, de bout en bout ------------------------------------------------------------------------------


def test_scripts_are_atomic_and_one_undo_step():
    session, report = _apply(
        _project(), _op("set_clip_speed", clip="a", speed=2.0), _op("add_speed_point", clip="c", at=1.0, value=3.0),
        _op("set_clip_reverse", clip="sound", reverse=True),
    )
    assert report.ok, report.error
    assert find_clip(session.project, "a").duration == 3.0 and find_clip(session.project, "sound").is_reversed
    assert session.undo()
    assert find_clip(session.project, "a").duration == 6.0 and not find_clip(session.project, "sound").is_reversed
    assert not speed_points(find_clip(session.project, "c"))
    _refused(_project(), _op("set_clip_speed", clip="a", speed=2.0), _op("insert_hold", clip="a", at=99.0))     # la 2e échoue : la 1re est annulée


def test_speed_ramp_end_to_end_with_aliases():
    session, report = _apply(
        _project(),
        _op("cut_clip", clip="a", at=3.0, **{"as": "droite"}),
        _op("add_speed_point", clip={"ref": "droite"}, at=0.0, value=1.0),
        _op("add_speed_point", clip={"ref": "droite"}, at=1.0, value=4.0, interpolation="linear"),
        _op("set_speed_point_value", clip={"ref": "droite"}, at=1.0, value=2.0),
        _op("insert_hold", clip={"ref": "droite"}, at=0.5, duration=0.5),
        _op("set_clip_interpolation", clip={"ref": "droite"}, interpolation="blending"),
    )
    assert report.ok, report.error
    right = find_clip(session.project, "droite")
    assert len(speed_points(right)) >= 4 and right.time_remapping.interpolation.value == "blending"
    assert session.undo() and find_clip(session.project, "a").duration == 6.0


def test_huge_and_hostile_inputs_are_bounded_at_parse():
    with pytest.raises(ScriptInvalid) as caught:
        parse_script({"kut_script": 1, "ops": [_op("set_clip_speed", clip="x" * 5_000_000, speed=2.0)]})
    assert len(str(caught.value)) < 10_000
    _invalid(_op("add_speed_point", clip="a", at=float("nan")))
    _invalid(_op("add_speed_point", clip="a", at=float("inf")))
    _invalid(_op("add_speed_point", clip="a", at=1e12))
    _invalid(_op("move_speed_points", clip="a", points=["x"], delta=1))
    _invalid(_op("set_clip_speed", clip="a", speed=2.0, extra=1))


def test_a_slowed_clip_may_overlap_its_neighbour_like_in_the_interface():
    """Documenté dans docs/scripting.md : ni l'inspecteur ni les verbes ne repoussent les clips voisins."""
    project = _parity(_project, _op("set_clip_speed", clip="a", speed=0.1), lambda p: set_clip_speed(p, "a", 0.1))
    first, second = find_clip(project, "a"), find_clip(project, "b")
    assert first.timeline_start + first.duration > second.timeline_start


@pytest.mark.parametrize("operation", [
    _op("set_clip_speed", clip="a", speed=2.0), _op("set_clip_reverse", clip="a", reverse=True),
    _op("set_clip_preserve_pitch", clip="a", enabled=False), _op("set_clip_remap_audio", clip="a", enabled=False),
], ids=lambda item: item["op"])
def test_settings_the_interface_disables_are_refused_on_a_frozen_clip(operation):
    report = _refused(_frozen(), operation)
    assert "figé" in str(report.error)
    _refused(_frozen(), operation, _op("set_clip_speed", clip="c", speed=2.0))      # atomique : l'opération valide qui suit n'est pas gardée


def test_a_frozen_clip_can_be_frozen_again_like_in_the_interface():
    _parity(_frozen, _op("set_clip_freeze_frame", clip="a", source_time=1.0, duration=2.0), lambda p: set_clip_freeze_frame(p, "a", 1.0, 2.0))
