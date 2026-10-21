"""Supprimer un tracker ne réécrit jamais la liaison d'un suiveur verrouillé : même règle pour le modèle, l'interface et les scripts.

``tracking_ops.remove_tracker`` réécrit les liaisons des clips qui suivent le tracker (séquence active : une liaison ne vit que dans une séquence, voir plus bas). Le refus est dans le MODÈLE
(``core.tracking_locks.require_tracker_removal_unlocked``) : il passe AVANT la première écriture, ne vise que les clips dont la liaison serait
réellement différente, et vaut tout ou rien pour plusieurs trackers. L'interface l'attrape (message dans la barre d'état, pas de traceback,
pas d'entrée d'historique) ; le verbe de script n'a plus de copie de la règle.
"""

from __future__ import annotations

import copy
from dataclasses import replace

import pytest
from test_scopes import _window

from core import tracking_ops
from core.graphics import GraphicOverlay, GraphicType
from core.project_model import Clip, MediaAsset, Project, Sequence, Track
from core.scripting import Session, apply_script, parse_script
from core.timeline_operations import cut_clip, find_clip
from core.tracking_model import ClipTracking, TrackLink


def _project(lock: str | None = None) -> Project:
    """« a » (V1) porte t1 et t2 ; « f » (V2) les suit par la liaison « l1 » ; « free » (V3) suit t1 seul (libre)."""
    project = Project(
        "Démo", width=320, height=180, fps=30.0,
        media_assets=[MediaAsset("m", "/x/a.mp4", "a", 60.0, 320, 180, 30.0, "video", has_audio=True)],
        tracks=[
            Track("V1", "V1", "video", clips=[Clip("a", "m", "V1", 0.0, 0.0, 4.0)]),
            Track("V2", "V2", "video", locked=(lock == "track"), clips=[Clip("f", "m", "V2", 0.0, 0.0, 2.0)]),
            Track("V3", "V3", "video", clips=[Clip("free", "m", "V3", 0.0, 0.0, 2.0)]),
        ],
    )
    for tracker_id, x in (("t1", 100.0), ("t2", 200.0)):
        tracking_ops.add_tracker(project, "a", timeline_time=0.0, x=x, y=50.0, tracker_id=tracker_id)
        tracking_ops.set_tracker_position(project, "a", tracker_id, 3.0, x + 30.0, 60.0)
    tracking_ops.add_link(project, "f", "a", ["t1", "t2"], timeline_time=0.0, link_id="l1")
    if lock == "layer":
        follower = find_clip(project, "f")
        follower.graphic = replace(follower.graphic or GraphicOverlay(type=GraphicType.TEXT), locked=True)
    return project


def _links(project: Project, clip_id: str):
    return tracking_ops.tracking_of(find_clip(project, clip_id)).links


def _refused(project: Project, call) -> None:
    before = copy.deepcopy(project)
    with pytest.raises(ValueError) as error:
        call(project)
    assert "verrouillé" in str(error.value) and "'f'" in str(error.value) and "t1" in str(error.value)
    assert len(str(error.value)) < 400
    assert project == before, "refus avant la première écriture"


# --- Modèle ------------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("lock", ["layer", "track"])
def test_the_model_refuses_to_rewrite_a_locked_followers_link(lock):
    _refused(_project(lock), lambda p: tracking_ops.remove_tracker(p, "a", "t1"))
    _refused(_project(lock), lambda p: tracking_ops.remove_trackers(p, "a", ["t2", "t1"]))


def test_a_free_follower_is_rewritten_and_a_link_left_without_tracker_is_removed():
    project = _project()
    tracking_ops.remove_tracker(project, "a", "t1")
    assert _links(project, "f")[0].tracker_ids == ("t2",)
    tracking_ops.remove_tracker(project, "a", "t2")
    assert _links(project, "f") == ()


def test_only_what_the_operation_would_change_is_refused():
    project = _project("track")
    tracking_ops.add_tracker(project, "a", timeline_time=0.0, x=5.0, y=5.0, tracker_id="t3")
    tracking_ops.remove_tracker(project, "a", "t3")                         # le suiveur verrouillé n'utilise pas t3
    assert _links(project, "f")[0].tracker_ids == ("t1", "t2")
    tracking_ops.set_link_enabled(project, "f", "l1", False)
    _refused(project, lambda p: tracking_ops.remove_tracker(p, "a", "t1"))   # une liaison désactivée est réécrite aussi


# --- Autres séquences : les liaisons ne vivent que dans une séquence -------------------------------------------------------------
#
# Établi par ces tests (PR « suiveurs d'une autre séquence ») : l'identifiant d'un clip est unique au projet, la résolution d'une liaison
# (``TrackingContext``) ne cherche sa source que dans la séquence du suiveur, l'interface et les verbes n'en créent pas d'une séquence à
# l'autre, et dupliquer une séquence réécrit les sources. Une liaison qui désigne un clip d'une AUTRE séquence est donc déjà
# « source introuvable » (inerte) avant la suppression du tracker : la réécrire ne corrigerait rien. L'écriture (et la garde, qui en dépend)
# reste donc limitée à la séquence active.


def _second_sequence(project: Project, *, locked: bool = True) -> Clip:
    project.sequences.append(Sequence("seq-b", "B", width=320, height=180, fps=30.0, tracks=[
        Track("W1", "W1", "video", locked=locked, clips=[Clip("g", "m", "W1", 0.0, 0.0, 1.0)])]))
    return project.sequences[-1].tracks[0].clips[0]


def _issues(project: Project, clip: Clip):
    from core.tracking_bindings import TrackingContext, link_issues

    context = TrackingContext(project, project.sequences[-1])
    return link_issues(context, clip, tracking_ops.tracking_of(clip).links[0])


def test_a_link_to_a_clip_of_another_sequence_is_already_inert_and_removal_leaves_it_alone(tmp_path):
    """Seul le modèle (``add_link`` direct) ou un calque collé dans une autre séquence en crée un ; il est « source introuvable » avant comme après."""
    from core.project_io import load_project, save_project
    from core.render_plan import build_render_plan
    from core.scripting.api import describe

    project = _project()
    other = _second_sequence(project)
    tracking_ops.add_link(project, "g", "a", ["t1"], timeline_time=0.0, link_id="lx")
    assert _issues(project, other) == ("missing_source",)
    before = copy.deepcopy(other.tracking)
    tracking_ops.remove_tracker(project, "a", "t1")                    # le suiveur d'une autre séquence est verrouillé : ni refus ni écriture
    assert other.tracking == before and _issues(project, other) == ("missing_source",)
    assert _links(project, "f")[0].tracker_ids == ("t2",)               # la séquence active, elle, est bien réécrite
    build_render_plan(project, sequence_id="seq-b")                    # ni exception ni valeur aberrante
    describe(project)
    save_project(project, str(tmp_path / "p.kut"))
    assert load_project(str(tmp_path / "p.kut")).sequences[-1].tracks[0].clips[0].tracking == before


def test_a_layer_pasted_into_another_sequence_keeps_an_inert_link_that_removal_does_not_touch():
    from core import mograph_layers

    project = _project()
    layer = mograph_layers.add_layer(project, "text", at=0.0, duration=2.0)
    tracking_ops.add_link(project, layer.id, "a", ["t1"], timeline_time=0.0, link_id="lg")
    clipboard = mograph_layers.copy_layers(project, [layer.id])
    project.sequences.append(Sequence("seq-b", "B", width=320, height=180, fps=30.0, tracks=[Track("G", "G", "graphics", locked=True)]))
    project.active_sequence_id = "seq-b"
    pasted = mograph_layers.paste_layers(project, clipboard, at=0.0)[0]
    assert tracking_ops.tracking_of(pasted).links[0].source_clip_id == "a"
    assert _issues(project, pasted) == ("missing_source",)
    project.active_sequence_id = "seq-main"
    before = copy.deepcopy(pasted.tracking)
    tracking_ops.remove_tracker(project, "a", "t1")
    assert pasted.tracking == before


def test_the_verbs_cannot_create_a_link_across_sequences():
    project = _project()
    _second_sequence(project)
    report = apply_script(Session(project), parse_script(
        {"kut_script": 1, "ops": [{"op": "add_link", "clip": "g", "source": "a", "trackers": ["t1"], "at": 0.0}]}))
    assert not report.ok and "g" in str(report.error)


def test_a_duplicated_sequence_keeps_its_own_links_and_trackers_when_the_original_loses_one():
    """Indépendance des séquences : la copie a ses propres identifiants ; supprimer un tracker dans l'original ne réécrit pas la copie
    (même si le suiveur de la copie est verrouillé : aucun refus non plus)."""
    from core.sequences import duplicate_sequence

    project = _project()
    clone = duplicate_sequence(project, project.active_sequence_id)
    holders = [(t, c) for t in clone.tracks for c in t.clips if c.tracking and c.tracking.links]
    assert len(holders) == 1
    track, follower = holders[0]
    track.locked = True
    assert follower.id != "f" and tracking_ops.tracking_of(follower).links[0].source_clip_id != "a"
    before = copy.deepcopy(clone)
    tracking_ops.remove_tracker(project, "a", "t1")
    assert clone == before
    assert _links(project, "f")[0].tracker_ids == ("t2",)


def test_the_script_verb_and_the_model_agree_with_a_second_sequence():
    scripted, direct = _project(), _project()
    for project in (scripted, direct):
        _second_sequence(project)
    session, report = _script(scripted, "a", "t1")
    tracking_ops.remove_tracker(direct, "a", "t1")
    assert report.ok and session.project == direct


def test_a_cut_half_still_carrying_the_tracker_leaves_the_locked_follower_alone():
    project = _project()
    cut_clip(project, "a", 2.0, right_clip_id="a2")
    follower = find_clip(project, "f")
    follower.graphic = replace(follower.graphic or GraphicOverlay(type=GraphicType.TEXT), locked=True)
    assert _links(project, "f")[0].follows("a2")
    before = copy.deepcopy(_links(project, "f"))
    tracking_ops.remove_tracker(project, "a", "t1")
    assert _links(project, "f") == before


def test_removing_several_trackers_is_all_or_nothing():
    project = _project("layer")
    free = find_clip(project, "free")
    free.tracking = ClipTracking(links=(TrackLink(source_clip_id="a", tracker_ids=("t1",)),))
    _refused(project, lambda p: tracking_ops.remove_trackers(p, "a", ["t1", "t2"]))
    assert tracking_ops.tracking_of(find_clip(project, "a")).tracker("t1") is not None


def test_an_unknown_tracker_stops_a_multiple_removal_before_any_write():
    project = _project()
    before = copy.deepcopy(project)
    with pytest.raises(tracking_ops.TrackingError):
        tracking_ops.remove_trackers(project, "a", ["t1", "nope"])
    assert project == before


# --- Script ------------------------------------------------------------------------------------------------------------------


def _script(project: Project, clip: str, tracker: str):
    session = Session(project)
    return session, apply_script(session, parse_script({"kut_script": 1, "ops": [{"op": "remove_tracker", "clip": clip, "tracker": tracker}]}))


@pytest.mark.parametrize("lock", [None, "layer", "track"])
def test_the_script_verb_and_the_model_agree(lock):
    scripted, direct = _project(lock), _project(lock)
    session, report = _script(scripted, "a", "t1")
    try:
        tracking_ops.remove_tracker(direct, "a", "t1")
    except ValueError:
        assert not report.ok and "verrouillé" in str(report.error) and session.project == _project(lock)
    else:
        assert report.ok and session.project == direct and lock is None


def test_the_verb_has_no_guard_of_its_own_any_more():
    from core.scripting import tracking_rules

    assert not hasattr(tracking_rules, "require_link_owners_editable")


# --- Interface ---------------------------------------------------------------------------------------------------------------


def _open(qtbot, monkeypatch, lock):
    window = _window(qtbot, monkeypatch)
    window.project = _project(lock)
    window.history.reset(window.project)
    window.timeline_panel.set_project(window.project)
    clip = find_clip(window.project, "a")
    monkeypatch.setattr(window, "_tracking_selected_clip", lambda: (find_clip(window.project, clip.id), "video"))
    window.statusBar().clearMessage()
    return window


@pytest.mark.parametrize("lock", ["layer", "track"])
def test_the_window_refuses_with_a_message_and_writes_nothing(qtbot, monkeypatch, lock):
    window = _open(qtbot, monkeypatch, lock)
    before = copy.deepcopy(window.project)
    window.tracking_panel.remove_trackers_requested.emit(["t2", "t1"])      # le vrai signal du bouton « supprimer »
    assert "verrouillé" in window.statusBar().currentMessage()
    assert window.project == before and not window.history.can_undo


def test_a_free_follower_lets_the_window_remove_and_undo_redo_restore_it(qtbot, monkeypatch):
    window = _open(qtbot, monkeypatch, None)
    before = copy.deepcopy(window.project.tracks)
    window.tracking_panel.remove_trackers_requested.emit(["t1", "t2"])
    after = copy.deepcopy(window.project.tracks)
    assert after != before and window.history.can_undo and _links(window.project, "f") == ()
    window.undo_last()
    assert window.project.tracks == before
    window.redo_last()
    assert window.project.tracks == after


def test_a_locked_follower_of_another_sequence_does_not_block_the_window_and_undo_redo_keep_it_intact(qtbot, monkeypatch):
    window = _open(qtbot, monkeypatch, None)
    other = _second_sequence(window.project)
    tracking_ops.add_link(window.project, "g", "a", ["t1"], timeline_time=0.0, link_id="lx")
    window.history.reset(window.project)
    before = copy.deepcopy(other.tracking)
    window.tracking_panel.remove_trackers_requested.emit(["t1"])
    assert window.history.can_undo and _links(window.project, "f")[0].tracker_ids == ("t2",)
    assert window.project.sequences[-1].tracks[0].clips[0].tracking == before
    window.undo_last()
    assert _links(window.project, "f")[0].tracker_ids == ("t1", "t2") and window.project.sequences[-1].tracks[0].clips[0].tracking == before
    window.redo_last()
    assert _links(window.project, "f")[0].tracker_ids == ("t2",) and window.project.sequences[-1].tracks[0].clips[0].tracking == before
