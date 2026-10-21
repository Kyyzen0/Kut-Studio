"""Les verbes de DONNÉES d'un tracker n'écrivent jamais un suiveur : un suiveur verrouillé ne les bloque donc pas (parité verbe / interface).

Caractérisation (établie par lecture de ``core/tracking_ops.py`` et vérifiée ici, opération par opération et par sorte de verrou : calque,
piste, piste d'une AUTRE séquence) : ``set_tracker_position``, ``remove_correction``, ``reset_tracker``, ``set_stabilization`` et
``remove_stabilization`` ne remplacent que le suivi du clip qu'on vise (``_put_tracker`` / ``_store`` sur ce clip). La liaison d'un suiveur
(``TrackLink``, en temps source) n'est pas réécrite ; ses images clés dérivées, recalculées au rendu, suivent les nouvelles données : un
suiveur verrouillé est redessiné, jamais réécrit. Seul ``remove_tracker`` réécrit des liaisons (il garde sa garde, ``require_link_owners_editable``).
"""

from __future__ import annotations

import copy
from dataclasses import replace

import pytest
from test_script_verbs_tracking import _op, _project, _tracked
from test_scopes import _window

from core import tracking_ops
from core.project_model import Clip, Project, Sequence, Track
from core.scripting import Session, apply_script, parse_script
from core.timeline_operations import find_clip
from core.tracking_model import ClipTracking, Sample, SampleStatus, TrackLink

KINDS = ("layer", "track", "other")


def _source_ready(stabilized: bool) -> Project:
    """« v » porte t1 (images analysées de 0,5 s à 2 s + deux points à la main) et t2 ; optionnellement une stabilisation."""
    project = _tracked(_project())
    clip = find_clip(project, "v")
    tracker = tracking_ops.tracking_of(clip).tracker("t1")
    data = tracker.data.with_samples({i: Sample(10.0 + i, 10.0, 0.9, SampleStatus.TRACKED) for i in range(15, 61)})
    clip.tracking = replace(clip.tracking, trackers=(replace(tracker, data=data), clip.tracking.trackers[1]))
    if stabilized:
        tracking_ops.set_stabilization(project, "v", tracker_ids=("t1", "t2"))
    return project


def _with_follower(kind: str, *, stabilized: bool = False) -> Project:
    """Un suiveur de « v » (liaison sur t1 + t2), verrouillé par son calque, par sa piste, ou posé sur une piste verrouillée d'une autre séquence."""
    project = _source_ready(stabilized)
    if kind == "other":
        link = TrackLink(id="lo", source_clip_id="v", tracker_ids=("t1", "t2"))
        other = Clip("o", "m", "W1", 0.0, 0.0, 1.0)
        other.tracking = ClipTracking(links=(link,))
        project.sequences.append(Sequence("seq-b", "B", width=320, height=180, fps=30.0,
                                          tracks=[Track("W1", "W1", "video", locked=True, clips=[other])]))
        return project
    tracking_ops.add_link(project, "g", "v", ["t1", "t2"], timeline_time=0.0, link_id="lnk")
    if kind == "layer":
        follower = find_clip(project, "g")
        follower.graphic = replace(follower.graphic, locked=True)
    else:
        project.tracks[3].locked = True
    return project


def _followers(project: Project):
    return [copy.deepcopy(clip) for track in project.all_tracks() for clip in track.clips if clip.tracking is not None and clip.id != "v"]


def _source_tracking(project: Project):
    return copy.deepcopy(find_clip(project, "v").tracking)


OPERATIONS = {
    "set_tracker_position": (False, _op("set_tracker_position", clip="v", tracker="t1", at=2.0, x=40.0, y=50.0),
                             lambda p: tracking_ops.set_tracker_position(p, "v", "t1", 2.0, 40.0, 50.0)),
    "remove_correction": (False, _op("remove_correction", clip="v", tracker="t1", at=3.0),
                          lambda p: tracking_ops.remove_correction(p, "v", "t1", 3.0)),
    "reset_tracker": (False, _op("reset_tracker", clip="v", tracker="t1"), lambda p: tracking_ops.reset_tracker(p, "v", "t1")),
    "reset_tracker_after": (False, _op("reset_tracker", clip="v", tracker="t1", direction="after", at=1.0),
                            lambda p: tracking_ops.reset_tracker(p, "v", "t1", timeline_time=1.0, direction=1)),
    "set_stabilization": (False, _op("set_stabilization", clip="v", trackers=["t1", "t2"], smoothing="high"),
                          lambda p: tracking_ops.set_stabilization(p, "v", tracker_ids=("t1", "t2"), smoothing="high",
                                                                   reference_index=tracking_ops.stabilization_reference(p, "v", 0.0))),
    "remove_stabilization": (True, _op("remove_stabilization", clip="v"), lambda p: tracking_ops.remove_stabilization(p, "v")),
}


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("name", OPERATIONS)
def test_the_model_and_the_verb_change_the_tracker_data_without_touching_a_locked_follower(name, kind):
    stabilized, op, direct = OPERATIONS[name]
    scripted = _with_follower(kind, stabilized=stabilized)
    followers, source = _followers(scripted), _source_tracking(scripted)
    session = Session(scripted)
    report = apply_script(session, parse_script({"kut_script": 1, "ops": [op]}))
    assert report.ok, report.error                                                      # le verbe n'est pas plus strict que l'interface
    assert _followers(session.project) == followers, "la liaison d'un suiveur ne doit pas changer"
    assert _source_tracking(session.project) != source, "le verbe a bien écrit les données du tracker"

    reference = _with_follower(kind, stabilized=stabilized)
    direct(reference)                                                                    # ce que l'interface appelle : aucun refus
    assert _followers(reference) == followers
    assert _source_tracking(reference) == _source_tracking(session.project), "verbe et modèle donnent le même résultat"

    session.undo()
    assert _source_tracking(session.project) == source and _followers(session.project) == followers


def test_removing_a_tracker_still_refuses_a_locked_owner_of_a_link_it_would_rewrite():
    """Contre-épreuve : ce qui RÉÉCRIT une liaison reste protégé (c'est la seule des opérations de données qui le fait)."""
    project = _with_follower("layer")
    before = copy.deepcopy(project)
    session = Session(project)
    report = apply_script(session, parse_script({"kut_script": 1, "ops": [_op("remove_tracker", clip="v", tracker="t1")]}))
    assert not report.ok and "verrouillé" in str(report.error)
    assert session.project.tracks == before.tracks


@pytest.mark.parametrize("name", ["set_tracker_position", "reset_tracker", "set_stabilization"])
def test_the_locked_source_itself_is_still_refused_by_the_verbs(name):
    stabilized, op, _direct = OPERATIONS[name]
    project = _with_follower("layer", stabilized=stabilized)
    project.tracks[0].locked = True
    before = copy.deepcopy(project.tracks)
    session = Session(project)
    report = apply_script(session, parse_script({"kut_script": 1, "ops": [op]}))
    assert not report.ok and "verrouillée" in str(report.error)
    assert session.project.tracks == before


# --- Interface : les mêmes opérations, sans refus -------------------------------------------------------------------------------


def _open(qtbot, monkeypatch, kind, *, stabilized=False):
    window = _window(qtbot, monkeypatch)
    window.project = _with_follower(kind, stabilized=stabilized)
    window.history.reset(window.project)
    window.timeline_panel.set_project(window.project)
    window._restore_clip_selection("v")
    window.statusBar().clearMessage()
    return window


@pytest.mark.parametrize("kind", ["layer", "track"])
def test_the_window_edits_tracker_data_whose_follower_is_locked(qtbot, monkeypatch, kind):
    window = _open(qtbot, monkeypatch, kind)
    followers, source = _followers(window.project), _source_tracking(window.project)
    window.playhead_seconds = 2.0
    window._on_overlay_point_dragged("t1", 40.0, 50.0)
    window._on_overlay_drag_finished("t1", "point")
    window._reset_trackers("all")
    assert "verrouillé" not in window.statusBar().currentMessage()
    assert _source_tracking(window.project) != source and _followers(window.project) == followers
    window._toggle_stabilization(True)
    assert find_clip(window.project, "v").tracking.stabilization is not None and _followers(window.project) == followers
    window._toggle_stabilization(False)
    assert _followers(window.project) == followers
