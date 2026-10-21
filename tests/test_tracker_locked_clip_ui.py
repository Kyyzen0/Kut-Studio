"""L'interface refuse d'éditer le suivi d'un clip verrouillé (piste ou calque), comme les verbes : message d'état, aucune écriture, aucune entrée d'historique.

Chaque geste de l'onglet Suivi et de la surimpression du viewer est exercé sur un clip verrouillé (le vrai signal ou la vraie méthode de la fenêtre),
puis sur un clip libre (qui s'édite, s'annule et se rétablit). Le refus est celui du modèle (``core.tracking_locks``) : le glisser d'un point est
refusé AVANT de commencer le geste, donc sans entrée d'historique à la relâche.
"""

from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest
from test_scopes import _window
from test_tracker_locked_clip import build, lock

from core import tracking_ops
from core.timeline_operations import find_clip
from core.tracking_model import Sample, SampleStatus

KINDS = ("track", "layer")
GESTURES = (
    "add", "remove", "rename", "visible", "reset_all", "reset_after", "settings", "stab_off", "stab_change", "drag_point", "drag_zone",
    "analysis", "auto_stabilize",
)


def _open(qtbot, monkeypatch, carrier: str, kind: str | None):
    window = _window(qtbot, monkeypatch)
    window.project = build()
    if kind is not None:
        lock(window.project, carrier, kind)
    window.history.reset(window.project)
    window.timeline_panel.set_project(window.project)
    monkeypatch.setattr(window, "_tracking_selected_clip", lambda: (find_clip(window.project, carrier), "video"))
    window._tracking_available = True
    window._tracking_selection[carrier] = ["t1", "t2"]
    window.playhead_seconds = 2.0
    window.statusBar().clearMessage()
    return window


def _gestures(window):
    """Les gestes qui écrivent sur le clip « v » (porteur) : (nom, appel)."""
    panel = window.tracking_panel
    return {
        "add": window.add_tracker_at_playhead,
        "remove": lambda: panel.remove_trackers_requested.emit(["t2", "t1"]),
        "rename": lambda: window._rename_tracker("t1", "Autre"),
        "visible": lambda: window._set_tracker_visible("t1", False),
        "reset_all": lambda: window._reset_trackers("all"),
        "reset_after": lambda: (setattr(window, "playhead_seconds", 1.0), window._reset_trackers("after")),     # des images analysées après : un vrai effacement
        "settings": lambda: window._on_tracker_settings({"min_confidence": 0.6}),
        "stab_off": lambda: window._toggle_stabilization(False),
        "stab_change": lambda: window._on_stabilization_changed({"smoothing": "high"}),
        "drag_point": lambda: (window._on_overlay_point_dragged("t1", 40.0, 50.0), window._on_overlay_drag_finished("t1", "point")),
        "drag_zone": lambda: (window._on_overlay_zone_dragged("t1", "pattern", 60.0, 60.0), window._on_overlay_drag_finished("t1", "pattern")),
        "analysis": lambda: window.track_selected(1),
        "auto_stabilize": lambda: window._auto_stabilize_with("v", [(10.0, 10.0), (50.0, 50.0)], ""),
    }


def test_every_listed_gesture_is_exercised(qtbot, monkeypatch):
    assert set(_gestures(_open(qtbot, monkeypatch, "v", None))) == set(GESTURES)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("name", GESTURES)
def test_a_locked_carrier_is_refused_with_a_message_and_nothing_is_written(qtbot, monkeypatch, name, kind):
    window = _open(qtbot, monkeypatch, "v", kind)
    before = copy.deepcopy(window.project)
    _gestures(window)[name]()
    assert "verrouillé" in window.statusBar().currentMessage()
    assert window.project == before and not window.history.can_undo
    assert not window._tracking_dragging and not window._tracking_jobs


@pytest.mark.parametrize("kind", KINDS)
def test_a_locked_follower_refuses_its_link_gestures(qtbot, monkeypatch, kind):
    window = _open(qtbot, monkeypatch, "w", kind)
    before = copy.deepcopy(window.project)
    for gesture in (lambda: window._set_link_enabled("lnk", False), lambda: window._bake_link("lnk"), lambda: window._remove_link("lnk")):
        window.statusBar().clearMessage()
        gesture()
        assert "verrouillé" in window.statusBar().currentMessage()
    assert window.project == before and not window.history.can_undo


@pytest.mark.parametrize("kind", KINDS)
def test_a_locked_target_refuses_a_new_link_but_a_locked_source_does_not(qtbot, monkeypatch, kind):
    window = _open(qtbot, monkeypatch, "v", kind)                                      # la SOURCE est verrouillée : elle n'est que lue
    window._link_selected_trackers({"clip_id": "g", "target": "transform"}, bake=False)
    assert "verrouillé" not in window.statusBar().currentMessage()
    assert tracking_ops.tracking_of(find_clip(window.project, "g")).links and window.history.can_undo
    other = _open(qtbot, monkeypatch, "v", None)
    lock(other.project, "g", kind)                                                     # la CIBLE est verrouillée : refus, tout ou rien
    before = copy.deepcopy(other.project)
    other._link_selected_trackers({"clip_id": "g", "target": "transform"}, bake=True)
    assert "verrouillé" in other.statusBar().currentMessage() and other.project == before and not other.history.can_undo


@pytest.mark.parametrize("kind", KINDS)
def test_an_analysis_finishing_on_a_clip_locked_meanwhile_writes_nothing(qtbot, monkeypatch, kind):
    window = _open(qtbot, monkeypatch, "v", kind)
    before = copy.deepcopy(window.project)
    outcome = SimpleNamespace(samples={70: Sample(5.0, 5.0, 0.9, SampleStatus.TRACKED)}, stop_index=None, last_index=70, reason="end")
    result = SimpleNamespace(state="finished", message="", request=SimpleNamespace(clip_id="v", rate=30.0, direction=1), outcomes={"t1": outcome})
    job = SimpleNamespace(request=result.request)
    window._finish_tracking_job("v", job, SimpleNamespace(result=result))
    assert "verrouillé" in window.statusBar().currentMessage()
    assert window.project == before and not window.history.can_undo


def test_a_free_clip_is_edited_and_undo_redo_restore_it(qtbot, monkeypatch):
    window = _open(qtbot, monkeypatch, "v", None)
    start = copy.deepcopy(window.project.tracks)
    window._on_overlay_point_dragged("t1", 40.0, 50.0)
    window._on_overlay_drag_finished("t1", "point")
    window._reset_trackers("all")
    window._on_tracker_settings({"min_confidence": 0.6})
    window._on_stabilization_changed({"smoothing": "high"})
    window.tracking_panel.remove_trackers_requested.emit(["t2", "t1"])
    assert "verrouillé" not in window.statusBar().currentMessage()
    done = copy.deepcopy(window.project.tracks)
    assert done != start and window.history.can_undo
    while window.history.can_undo:
        window.undo_last()
    assert window.project.tracks == start
    while window.history.can_redo:
        window.redo_last()
    assert window.project.tracks == done


def test_a_refused_drag_leaves_the_overlay_on_the_stored_point_and_a_later_free_drag_works(qtbot, monkeypatch):
    window = _open(qtbot, monkeypatch, "v", "track")
    window._on_overlay_point_dragged("t1", 40.0, 50.0)
    window._on_overlay_drag_finished("t1", "point")
    assert not window._tracking_dragging and not window.history.can_undo
    find_clip(window.project, "v")                                                     # le projet n'a pas bougé : la surimpression redessine l'état stocké
    window._refresh_tracking_overlay()
    stored = tracking_ops.tracking_of(find_clip(window.project, "v")).tracker("t1").data
    window.project.tracks[0].locked = False
    window._on_overlay_point_dragged("t1", 40.0, 50.0)
    window._on_overlay_drag_finished("t1", "point")
    assert window.history.can_undo and tracking_ops.tracking_of(find_clip(window.project, "v")).tracker("t1").data != stored


@pytest.mark.parametrize("kind", KINDS)
def test_a_lock_refusal_clears_every_follow_up_of_an_auto_stabilization(qtbot, monkeypatch, kind):
    window = _open(qtbot, monkeypatch, "v", None)
    monkeypatch.setattr(window.runtime, "schedule_analysis", lambda *_a, **_k: None)      # l'analyse reste en file : on la termine à la main
    window._auto_stabilize_with("v", [(10.0, 10.0), (50.0, 50.0)], "")
    job = window._tracking_jobs.pop("v")
    ids = [t.id for t in job.request.trackers]
    assert len(window._tracking_followups["v"]) == 2
    lock(window.project, "v", kind)                                                       # verrouillé avant la fin de l'analyse
    outcomes = {i: SimpleNamespace(samples={70: Sample(5.0, 5.0, 0.9, SampleStatus.TRACKED)}, stop_index=None, last_index=70, reason="end") for i in ids}
    result = SimpleNamespace(state="finished", message="", request=job.request, outcomes=outcomes)
    before = copy.deepcopy(window.project.tracks)
    window._finish_tracking_job("v", job, SimpleNamespace(result=result))
    assert "verrouillé" in window.statusBar().currentMessage()
    assert "v" not in window._tracking_followups and not window._tracking_jobs
    assert window.project.tracks == before
    window.project.tracks[0].locked = False                                               # déverrouillé : une analyse manuelle ne rejoue rien
    window.project.tracks[0].clips[0].graphic = None
    manual = SimpleNamespace(state="finished", message="", request=job.request, outcomes=outcomes)
    window._finish_tracking_job("v", SimpleNamespace(request=job.request), SimpleNamespace(result=manual))
    assert find_clip(window.project, "v").tracking.stabilization.tracker_ids == ("t1", "t2")   # la stabilisation périmée n'a pas été posée sur les nouveaux trackers
    assert not window._tracking_jobs


def test_a_follow_up_whose_analysis_does_not_start_leaves_nothing_queued(qtbot, monkeypatch):
    window = _open(qtbot, monkeypatch, "v", None)
    monkeypatch.setattr(window, "track_selected", lambda *a, **k: False)
    window._auto_stabilize_with("v", [(10.0, 10.0)], "")
    assert "v" not in window._tracking_followups
    window._tracking_followups["v"] = [("track", -1, ["t1"]), ("stabilize", ["t1"])]
    window._run_tracking_followup("v")
    assert "v" not in window._tracking_followups
