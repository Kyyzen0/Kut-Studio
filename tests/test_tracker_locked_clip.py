"""Un clip verrouillé (piste ou calque graphique) ne voit pas son suivi modifié : une règle du MODÈLE, partagée par l'interface et les verbes.

``core.tracking_locks.require_clip_editable`` est appelée par les fonctions de ``core.tracking_ops`` AVANT la première écriture, et ne vise que le
clip PORTEUR (celui que l'opération remplace : propriétaire d'un tracker, cible d'une liaison). La source d'une liaison, lue seulement, peut être
verrouillée. Une opération sans effet (même nom, même état) n'est pas refusée. Les verbes de script n'ont plus de copie de la règle.
"""

from __future__ import annotations

import copy
import inspect
from dataclasses import replace
from types import SimpleNamespace

import pytest
from test_script_verbs_tracking import _op, _project, _tracked

from core import tracking_ops
from core.graphics import GraphicOverlay, GraphicType
from core.project_model import Clip, Project, Sequence, Track
from core.scripting import Session, apply_script, parse_script, tracking_verbs
from core.timeline_operations import find_clip
from core.tracking_locks import require_clip_editable
from core.tracking_model import Sample, SampleStatus

KINDS = ("track", "layer")


def lock(project: Project, clip_id: str, kind: str) -> None:
    """Verrouille le clip : sa piste, ou son calque graphique (un clip vidéo peut en porter un, comme dans test_tracker_removal_lock)."""
    clip = find_clip(project, clip_id)
    if kind == "track":
        next(t for t in project.all_tracks() if clip in t.clips).locked = True
    else:
        clip.graphic = replace(clip.graphic or GraphicOverlay(type=GraphicType.TEXT), locked=True)


def build() -> Project:
    """« v » porte t1 et t2 (analysés, un point à la main à 3 s) et une stabilisation ; « w » (vidéo) et « g » (calque) les suivent."""
    project = _tracked(_project())
    clip = find_clip(project, "v")
    for index, tracker in enumerate(clip.tracking.trackers):
        data = tracker.data.with_samples({i: Sample(10.0 + i + 40 * index, 10.0, 0.9, SampleStatus.TRACKED) for i in range(15, 61)})
        clip.tracking = replace(clip.tracking, trackers=tuple(replace(t, data=data) if t.id == tracker.id else t for t in clip.tracking.trackers))
    tracking_ops.set_stabilization(project, "v", tracker_ids=("t1", "t2"))
    tracking_ops.add_link(project, "w", "v", ["t1", "t2"], timeline_time=0.0, link_id="lnk", rotation=True)
    return project


# (porteur, appel du modèle, opération de script ou None)
OPERATIONS = {
    "add_tracker": ("v", lambda p: tracking_ops.add_tracker(p, "v", timeline_time=1.0, x=10.0, y=10.0, tracker_id="t9"),
                    _op("add_tracker", clip="v", at=1.0, x=10.0, y=10.0, id="t9")),
    "update_tracker": ("v", lambda p: tracking_ops.update_tracker(p, "v", "t1", name="Autre"),
                       _op("update_tracker", clip="v", tracker="t1", name="Autre")),
    "set_tracker_settings": ("v", lambda p: tracking_ops.set_tracker_settings(p, "v", "t1", min_confidence=0.6),
                             _op("set_tracker_settings", clip="v", tracker="t1", min_confidence=0.6)),
    "set_tracker_position": ("v", lambda p: tracking_ops.set_tracker_position(p, "v", "t1", 2.0, 40.0, 50.0),
                             _op("set_tracker_position", clip="v", tracker="t1", at=2.0, x=40.0, y=50.0)),
    "remove_correction": ("v", lambda p: tracking_ops.remove_correction(p, "v", "t1", 3.0), _op("remove_correction", clip="v", tracker="t1", at=3.0)),
    "reset_tracker": ("v", lambda p: tracking_ops.reset_tracker(p, "v", "t1"), _op("reset_tracker", clip="v", tracker="t1")),
    "remove_tracker": ("v", lambda p: tracking_ops.remove_tracker(p, "v", "t2"), _op("remove_tracker", clip="v", tracker="t2")),
    "remove_trackers": ("v", lambda p: tracking_ops.remove_trackers(p, "v", ["t1", "t2"]), None),
    "set_stabilization": ("v", lambda p: tracking_ops.set_stabilization(p, "v", smoothing="high"),
                          _op("set_stabilization", clip="v", smoothing="high")),
    "remove_stabilization": ("v", lambda p: tracking_ops.remove_stabilization(p, "v"), _op("remove_stabilization", clip="v")),
    "analysis_request": ("v", lambda p: tracking_ops.analysis_request(p, "v", ["t1"], timeline_time=1.0, direction=1), None),
    "apply_tracking_result": ("v", lambda p: tracking_ops.apply_tracking_result(p, SimpleNamespace(
        request=SimpleNamespace(clip_id="v", rate=30.0),
        outcomes={"t1": SimpleNamespace(samples={70: Sample(5.0, 5.0, 0.9, SampleStatus.TRACKED)})})), None),
    "add_link": ("g", lambda p: tracking_ops.add_link(p, "g", "v", ["t1", "t2"], timeline_time=0.0, link_id="n"),
                 _op("add_link", clip="g", source="v", trackers=["t1", "t2"], at=0.0, **{"as": "n"})),
    "remove_link": ("w", lambda p: tracking_ops.remove_link(p, "w", "lnk"), _op("remove_link", clip="w", link="lnk")),
    "set_link_enabled": ("w", lambda p: tracking_ops.set_link_enabled(p, "w", "lnk", False), _op("set_link_enabled", clip="w", link="lnk", enabled=False)),
    "bake_link": ("w", lambda p: tracking_ops.bake_link(p, "w", "lnk"), _op("bake_link", clip="w", link="lnk")),
}


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("name", OPERATIONS)
def test_the_model_refuses_a_locked_carrier_before_writing(name, kind):
    carrier, call, _op_ = OPERATIONS[name]
    project = build()
    lock(project, carrier, kind)
    before = copy.deepcopy(project)
    with pytest.raises(ValueError) as error:
        call(project)
    assert "verrouillé" in str(error.value) and f"'{carrier}'" in str(error.value) and len(str(error.value)) < 300
    assert project == before, "aucune écriture avant le refus"


@pytest.mark.parametrize("name", OPERATIONS)
def test_a_free_clip_is_edited_and_only_the_carrier_changes(name):
    carrier, call, _op_ = OPERATIONS[name]
    project = build()
    before = copy.deepcopy(project)
    call(project)                                          # aucun refus
    assert (project != before) or name == "analysis_request"      # (une requête d'analyse n'écrit rien : elle refuse seulement)
    for track in project.all_tracks():
        for clip in track.clips:
            if clip.id not in (carrier, "v", "w"):             # « v » / « w » : les opérations qui réécrivent des liaisons
                assert clip == find_clip(before, clip.id)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("name", [n for n, (_c, _f, op) in OPERATIONS.items() if op is not None])
def test_the_script_verb_refuses_with_the_message_of_the_model_and_writes_nothing(name, kind):
    carrier, call, op = OPERATIONS[name]
    reference = build()
    lock(reference, carrier, kind)
    with pytest.raises(ValueError) as expected:
        call(copy.deepcopy(reference))
    session = Session(copy.deepcopy(reference))
    report = apply_script(session, parse_script({"kut_script": 1, "ops": [op]}))
    assert not report.ok and str(expected.value) in str(report.error)
    assert session.project == reference


def test_the_verbs_have_no_lock_check_of_their_own_but_follow_reframe_keeps_the_ui_rule():
    for helper in (tracking_verbs._video_clip, tracking_verbs._clip_on, tracking_verbs._link_of):
        assert "_editable_clip" not in inspect.getsource(helper) and "locked" not in inspect.getsource(helper).replace("verrou", "")
    assert "_editable_clip" in inspect.getsource(tracking_verbs._follow_reframe)      # apply_follow_reframe ne reçoit qu'un clip (social.py le garde)


@pytest.mark.parametrize("kind", KINDS)
def test_only_what_the_operation_would_modify_is_refused(kind):
    project = build()
    lock(project, "v", kind)
    lock(project, "w", kind)
    before = copy.deepcopy(project)
    tracking_ops.update_tracker(project, "v", "t1", name=tracking_ops.tracking_of(find_clip(project, "v")).tracker("t1").name)
    tracking_ops.set_tracker_settings(project, "v", "t1")                               # rien ne change
    tracking_ops.set_link_enabled(project, "w", "lnk", True)                            # déjà active
    tracking_ops.set_stabilization(project, "v", smoothing=tracking_ops.tracking_of(find_clip(project, "v")).stabilization.smoothing)
    assert project == before
    # la SOURCE d'une liaison n'est que lue : verrouillée, elle n'empêche pas de poser une liaison sur un calque libre
    tracking_ops.add_link(project, "g", "v", ["t1", "t2"], timeline_time=0.0, link_id="n")
    assert tracking_ops.tracking_of(find_clip(project, "g")).link("n") is not None


def test_a_clip_without_stabilization_is_not_refused_when_there_is_nothing_to_remove():
    project = build()
    tracking_ops.remove_stabilization(project, "v")
    lock(project, "v", "track")
    before = copy.deepcopy(project)
    tracking_ops.remove_stabilization(project, "v")
    assert project == before


def test_a_locked_track_of_another_sequence_is_found_and_refused():
    project = build()
    other = Clip("o", "m", "W1", 0.0, 0.0, 2.0)
    project.sequences.append(Sequence("seq-b", "B", width=320, height=180, fps=30.0, tracks=[Track("W1", "W1", "video", locked=True, clips=[other])]))
    before = copy.deepcopy(project)
    with pytest.raises(ValueError, match="verrouillé"):
        require_clip_editable(project, "o")
    result = SimpleNamespace(request=SimpleNamespace(clip_id="o", rate=30.0), outcomes={})
    assert tracking_ops.apply_tracking_result(project, result) == {} and project == before


def test_a_multiple_removal_is_all_or_nothing_on_a_locked_clip():
    project = build()
    lock(project, "v", "layer")
    before = copy.deepcopy(project)
    with pytest.raises(ValueError, match="verrouillé"):
        tracking_ops.remove_trackers(project, "v", ["t2", "t1"])
    assert project == before
