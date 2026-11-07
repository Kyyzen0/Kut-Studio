"""Déplacer, rogner, accélérer ou dupliquer la SOURCE d'un suivi ne réécrit jamais ses suiveurs, verrouillés ou non.

Caractérisation (établie par lecture : seuls ``follow_cut`` et ``release_source`` écrivent la liaison d'un AUTRE clip, et seules la coupe et la
suppression les appellent) : la liaison d'un suiveur est en temps SOURCE, le déplacement de sa source ne change que les images clés dérivées au
rendu. Donc aucune de ces opérations n'est refusée devant un suiveur verrouillé : ni par le modèle, ni par l'interface, ni par un verbe de
script (parité). Les verbes ne contiennent plus de garde propre : seul le modèle refuse (coupe, suppression : ``core/tracking_locks.py``).
"""

from __future__ import annotations

import copy
from dataclasses import replace

import pytest
from test_scopes import _window

from core.graphics import GraphicOverlay, GraphicType
from core.project_model import Clip, MediaAsset, Project, Sequence, Track
from core.scripting import Session, apply_script, parse_script
from core.timeline_editing import ClipPlacement, move_clips, relocate_clip, ripple_trim_left, slip_clip
from core.timeline_operations import (
    duplicate_clip, find_clip, move_clip, set_clip_speed, trim_clip_left, trim_clip_right,
)
from core.tracking_model import ClipTracking, TrackLink


def _project(kind: str | None, *, enabled: bool = True) -> Project:
    """« f » suit « a » (puis son prolongement « b »), verrouillé par calque, par piste ou sur une piste verrouillée d'une autre séquence."""
    project = Project(
        "Démo", width=320, height=180, fps=30.0,
        media_assets=[MediaAsset("m", "/x/a.mp4", "a", 60.0, 320, 180, 30.0, "video", has_audio=True)],
        tracks=[
            Track("V1", "V1", "video", clips=[Clip("a", "m", "V1", 0.0, 2.0, 4.0), Clip("b", "m", "V1", 4.0, 0.0, 3.0)]),
            Track("V2", "V2", "video", clips=[Clip("f", "m", "V2", 0.0, 0.0, 2.0)]),
            Track("V3", "V3", "video"),
        ],
    )
    link = TrackLink(id="l1", source_clip_id="a", tracker_ids=("t1",), continuation_ids=("b",), enabled=enabled)
    if kind == "other":
        project.sequences.append(Sequence("seq-b", "B", width=320, height=180, fps=30.0,
                                          tracks=[Track("W1", "W1", "video", locked=True, clips=[Clip("g", "m", "W1", 0.0, 0.0, 1.0)])]))
        project.sequences[-1].tracks[0].clips[0].tracking = ClipTracking(links=(link,))
        return project
    follower = find_clip(project, "f")
    follower.tracking = ClipTracking(links=(link,))
    if kind == "layer":
        follower.graphic = replace(GraphicOverlay(type=GraphicType.TEXT), locked=True)
    elif kind == "track":
        project.tracks[1].locked = True
    return project


KINDS = ("layer", "track", "other")


def _followers(project: Project):
    return [copy.deepcopy(clip) for track in project.all_tracks() for clip in track.clips if clip.tracking is not None]


MODEL_OPERATIONS = {
    "move_clip": lambda p: move_clip(p, "a", 20.0),
    "relocate_clip": lambda p: relocate_clip(p, "a", "V3", 20.0),
    "move_clips": lambda p: move_clips(p, [ClipPlacement("a", 20.0, "V3"), ClipPlacement("b", 30.0, "V1")]),
    "trim_left": lambda p: trim_clip_left(p, "a", 1.0),
    "trim_right": lambda p: trim_clip_right(p, "a", 3.0),
    "ripple_trim_left": lambda p: ripple_trim_left(p, "a", 1.0),
    "slip": lambda p: slip_clip(p, "a", 1.0),
    "speed": lambda p: set_clip_speed(p, "a", 2.0),
    "duplicate": lambda p: duplicate_clip(p, "a"),
}


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("name", MODEL_OPERATIONS)
def test_the_model_edits_a_tracking_source_without_touching_its_locked_follower(kind, name):
    project = _project(kind)
    before = _followers(project)
    MODEL_OPERATIONS[name](project)                                      # aucune exception : le modèle n'a pas de garde ici
    assert _followers(project) == before, "la liaison d'un suiveur ne doit pas changer"


SCRIPT_OPERATIONS = {
    "move_clip": {"op": "move_clip", "clip": "a", "at": 20.0},
    "move_clip_to_track": {"op": "move_clip_to_track", "clip": "a", "track": "V3", "at": 20.0},
    "move_clips": {"op": "move_clips", "placements": [{"clip": "a", "track": "V3", "at": 20.0}, {"clip": "b", "track": "V1", "at": 30.0}]},
}


def _apply(project: Project, op: dict):
    session = Session(project)
    report = apply_script(session, parse_script({"kut_script": 1, "ops": [op]}))
    return session, report


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("name", SCRIPT_OPERATIONS)
def test_the_script_verbs_move_a_tracking_source_like_the_window_does(name, kind, enabled):
    project = _project(kind, enabled=enabled)
    before = _followers(project)
    session, report = _apply(project, SCRIPT_OPERATIONS[name])
    assert report.ok, report.error
    assert _followers(session.project) == before
    moved = find_clip(session.project, "a")
    assert moved.timeline_start == 20.0
    session.undo()
    assert find_clip(session.project, "a").timeline_start == 0.0 and _followers(session.project) == before


def test_the_verbs_give_the_same_result_as_the_model_functions():
    for name, op in SCRIPT_OPERATIONS.items():
        by_script = _apply(_project("layer"), op)[0].project
        by_model = _project("layer")
        {"move_clip": MODEL_OPERATIONS["move_clip"], "move_clip_to_track": MODEL_OPERATIONS["relocate_clip"],
         "move_clips": MODEL_OPERATIONS["move_clips"]}[name](by_model)
        assert by_script.tracks == by_model.tracks, name


def test_a_locked_track_or_layer_on_the_source_itself_is_still_refused():
    """Seul le suiveur est assoupli : le clip déplacé lui-même reste protégé par son propre verrou."""
    project = _project(None)
    project.tracks[0].locked = True
    session, report = _apply(project, SCRIPT_OPERATIONS["move_clip"])
    assert not report.ok and "verrouillée" in report.error
    project = _project(None)
    source = find_clip(project, "a")
    source.graphic = replace(GraphicOverlay(type=GraphicType.TEXT), locked=True)
    session, report = _apply(project, SCRIPT_OPERATIONS["move_clip"])
    assert not report.ok and find_clip(project, "a").timeline_start == 0.0


def test_cutting_the_same_source_is_still_refused_by_the_model_for_the_locked_follower():
    from core.timeline_operations import cut_clip
    project = _project("layer")
    before = copy.deepcopy(project)
    with pytest.raises(ValueError, match="verrouillé"):
        cut_clip(project, "a", 1.0)
    assert project == before


# --- Interface : mêmes opérations, sans refus, annulables ---------------------------------------------------------------------


def _open(qtbot, monkeypatch, kind):
    window = _window(qtbot, monkeypatch)
    window.project = _project(kind)
    window.history.reset(window.project)
    window.timeline_panel.set_project(window.project)
    window.statusBar().clearMessage()
    return window


def _drag(window):
    window.on_move_clip_requested("a", 20.0)


def _drag_many(window):
    window.on_clips_move_requested([ClipPlacement("a", 20.0, "V3"), ClipPlacement("b", 30.0, "V1")])


def _trim(window):
    window.timeline_panel.ripple_enabled = False
    window.on_trim_left_requested("a", 1.0)


def _ripple_trim(window):
    window.timeline_panel.ripple_enabled = True
    window.on_trim_left_requested("a", 1.0)


ENTRIES = [_drag, _drag_many, _trim, _ripple_trim]


@pytest.mark.parametrize("kind", ["layer", "track"])
@pytest.mark.parametrize("entry", ENTRIES, ids=lambda f: f.__name__)
def test_the_window_moves_and_trims_a_source_with_a_locked_follower_and_undo_redo_restore_it(qtbot, monkeypatch, kind, entry):
    window = _open(qtbot, monkeypatch, kind)
    before = copy.deepcopy(window.project.tracks)
    followers = _followers(window.project)
    entry(window)
    after = copy.deepcopy(window.project.tracks)
    assert after != before and window.history.can_undo and "verrouillé" not in window.statusBar().currentMessage()
    assert _followers(window.project) == followers
    window.undo_last()
    assert window.project.tracks == before
    window.redo_last()
    assert window.project.tracks == after
