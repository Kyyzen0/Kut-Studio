"""Verrous des verbes historiques de montage : calque verrouillé sur une piste libre, suiveurs de suivi verrouillés.

Un verbe de script ne modifie jamais un calque ou un clip verrouillé, ni directement ni par un tiers (suiveur de suivi, enfant d'un calque).
Chaque refus est testé deux fois : en appelant l'opération DIRECTEMENT, sans session (le projet est alors strictement inchangé : la garde passe
avant la première écriture), et par la session (rapport en échec, projet inchangé, aucun pas d'historique).
"""

from __future__ import annotations

import copy
from dataclasses import replace

import pytest

from core.beat_grid import BeatGrid
from core.graphics import GraphicOverlay, GraphicType
from core.project_model import Clip, MediaAsset, Project, Sequence, Track
from core.scripting import Session, apply_script, parse_script
from core.scripting.verbs import VERBS
from core.timeline_operations import find_clip
from core.tracking_model import ClipTracking, TrackLink
from tests.test_script_verbs_multicam import _apply as _apply_multicam
from tests.test_script_verbs_multicam import _with_segment, _with_source
from ui import i18n


@pytest.fixture(autouse=True)
def _french(monkeypatch):
    monkeypatch.setenv("KUT_STUDIO_LANG", "fr")
    i18n.reset_for_tests()
    yield
    i18n.reset_for_tests()


def _project() -> Project:
    project = Project(
        "Démo", width=320, height=180, fps=30.0,
        media_assets=[MediaAsset("m", "/x/a.mp4", "a", 60.0, 320, 180, 30.0, "video", has_audio=True)],
        tracks=[
            Track("V1", "V1", "video", clips=[Clip("a", "m", "V1", 0.0, 0.0, 4.0), Clip("b", "m", "V1", 4.0, 0.0, 3.0)]),
            Track("V2", "V2", "video", clips=[Clip("f", "m", "V2", 0.0, 0.0, 2.0)]),
            Track("V3", "V3", "video"),
        ],
    )
    project.active_sequence.beat_grid = BeatGrid(120.0)
    return project


def _op(verb: str, /, **fields) -> dict:
    return {"op": verb, **fields}


def _lock_layer(project: Project, clip_id: str) -> None:
    clip = find_clip(project, clip_id)
    clip.graphic = replace(clip.graphic or GraphicOverlay(type=GraphicType.TEXT), locked=True)


def _follow(project: Project, follower: str = "f", source: str = "a", *, continuation: tuple[str, ...] = (), enabled: bool = True) -> None:
    clip = next(c for t in project.all_tracks() for c in t.clips if c.id == follower)
    clip.tracking = ClipTracking(links=(
        TrackLink(source_clip_id=source, tracker_ids=("t1",), continuation_ids=continuation, enabled=enabled),))


def _locked_follower(kind: str, **link) -> Project:
    """Un clip « f » qui suit « a », verrouillé de l'une des trois façons : calque, piste, ou piste d'une AUTRE séquence."""
    project = _project()
    if kind == "other":
        other = Sequence("seq-b", "B", width=320, height=180, fps=30.0,
                         tracks=[Track("W1", "W1", "video", locked=True, clips=[Clip("g", "m", "W1", 0.0, 0.0, 1.0)])])
        project.sequences.append(other)
        _follow(project, "g", **link)
        return project
    _follow(project, **link)
    if kind == "layer":
        _lock_layer(project, "f")
    else:
        project.tracks[1].locked = True
    return project


KINDS = ("layer", "track", "other")


def _refuse_both_ways(project: Project, op: dict, fragment: str) -> None:
    """Le verbe refuse sans session (projet inchangé) puis par la session (rapport en échec, projet inchangé, pas d'historique)."""
    before = copy.deepcopy(project)
    args = {key: value for key, value in op.items() if key != "op"}
    with pytest.raises(ValueError) as error:
        VERBS[op["op"]].build(args)(project)
    assert fragment in str(error.value) and project == before, "la garde doit passer avant la première écriture"
    session = Session(project)
    report = apply_script(session, parse_script({"kut_script": 1, "ops": [op]}))
    assert not report.ok and fragment in report.error
    assert project == before and not session.can_undo


def _applies(project: Project, op: dict) -> Project:
    session = Session(project)
    report = apply_script(session, parse_script({"kut_script": 1, "ops": [op]}))
    assert report.ok, report.error
    return session.project


# --- Un calque verrouillé sur une piste libre -------------------------------------------------------------------------------


@pytest.mark.parametrize("op", [
    _op("delete_clip", clip="a"), _op("move_clip", clip="a", at=20.0), _op("set_clip_enabled", clip="a", enabled=False),
], ids=lambda op: op["op"])
def test_a_locked_layer_on_a_free_track_is_refused_before_any_write(op):
    project = _project()
    _lock_layer(project, "a")
    _refuse_both_ways(project, op, "Le calque 'a' est verrouillé")


@pytest.mark.parametrize("op", [
    _op("delete_clip", clip="a"), _op("move_clip", clip="a", at=20.0), _op("set_clip_enabled", clip="a", enabled=False),
], ids=lambda op: op["op"])
def test_the_same_verbs_still_work_on_a_free_clip_and_refuse_a_locked_track(op):
    assert _applies(_project(), op)
    locked = _project()
    locked.tracks[0].locked = True
    _refuse_both_ways(locked, op, "verrouillée")


# --- Les suiveurs d'un suivi : coupe ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("op", [_op("cut_clip", clip="a", at=2.0), _op("cut_clips_on_beats", clips=["a"])], ids=lambda op: op["op"])
def test_cutting_a_source_refuses_when_a_follower_is_locked_whatever_its_link_state(op, enabled, kind):
    _refuse_both_ways(_locked_follower(kind, enabled=enabled), op, "le clip verrouillé")


def test_cutting_a_source_extends_the_links_of_free_followers_and_the_probe_now_refuses_the_locked_one():
    free = _project()
    _follow(free)
    cut = _applies(free, _op("cut_clip", clip="a", at=2.0, id="a2"))
    assert find_clip(cut, "f").tracking.links[0].continuation_ids == ("a2",)
    locked = _locked_follower("layer")
    before = copy.deepcopy(find_clip(locked, "f").tracking)
    _refuse_both_ways(locked, _op("cut_clip", clip="a", at=2.0, id="a2"), "le clip verrouillé 'f'")
    assert find_clip(locked, "f").tracking == before                    # la sonde : aucun prolongement ajouté


def test_cutting_at_scenes_refuses_a_locked_follower():
    project = _locked_follower("layer")
    _refuse_both_ways(project, _op("cut_clip_at_scenes", clip="a", times=[2.0]), "le clip verrouillé")


def test_a_locked_clip_that_follows_another_source_does_not_block_the_cut():
    project = _locked_follower("layer", source="b")
    assert _applies(project, _op("cut_clip", clip="a", at=2.0))


# --- Les suiveurs d'un suivi : suppression ---------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("op", [_op("delete_clip", clip="a"), _op("ripple_delete_clip", clip="a")], ids=lambda op: op["op"])
def test_deleting_a_source_refuses_when_a_locked_follower_would_have_its_links_rewritten(op, kind):
    project = _locked_follower(kind, continuation=("b",))             # « b » prolonge « a » (après une coupe)
    _refuse_both_ways(project, op, "verrouillé")


@pytest.mark.parametrize("op", [_op("delete_clip", clip="a"), _op("ripple_delete_clip", clip="a")], ids=lambda op: op["op"])
def test_after_a_cut_the_deletion_of_the_source_is_refused_too(op):
    free = _project()
    _follow(free)
    free = _applies(free, _op("cut_clip", clip="a", at=2.0, id="a2"))          # la coupe seule est libre : « f » suit « a » puis « a2 »
    assert find_clip(free, "f").tracking.links[0].continuation_ids == ("a2",)
    _lock_layer(free, "f")
    before = copy.deepcopy(find_clip(free, "f").tracking)
    _refuse_both_ways(free, op, "verrouillé")
    assert find_clip(free, "f").tracking == before


@pytest.mark.parametrize("op", [_op("delete_clip", clip="a"), _op("ripple_delete_clip", clip="a")], ids=lambda op: op["op"])
def test_deleting_a_source_works_when_the_follower_is_free_or_keeps_its_links_unchanged(op):
    free = _project()
    _follow(free, continuation=("b",))
    assert find_clip(_applies(free, op), "f").tracking.links[0].source_clip_id == "b"
    plain = _locked_follower("layer")                                  # aucune prolongation : la liaison n'est pas réécrite
    assert find_clip(_applies(plain, op), "f").tracking.links[0].source_clip_id == "a"


@pytest.mark.parametrize("op", [_op("delete_clip", clip="a"), _op("ripple_delete_clip", clip="a")], ids=lambda op: op["op"])
@pytest.mark.parametrize("key", ["parent_id", "group_id"])
def test_deleting_a_parent_or_group_refuses_when_a_child_layer_is_locked(op, key):
    project = _project()
    child = find_clip(project, "f")
    child.graphic = GraphicOverlay(type=GraphicType.TEXT, locked=True, **{key: "a"})
    _refuse_both_ways(project, op, "dépend")
    project.tracks[1].clips[0].graphic = replace(child.graphic, locked=False)
    assert getattr(find_clip(_applies(project, op), "f").graphic, key) == ""


# --- Les suiveurs d'un suivi : déplacement ---------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["layer", "track"])
@pytest.mark.parametrize("op", [
    _op("move_clip", clip="a", at=20.0), _op("move_clip_to_track", clip="a", track="V3", at=20.0),
    _op("move_clips", placements=[{"clip": "a", "track": "V3", "at": 20.0}]),
], ids=lambda op: op["op"])
def test_moving_a_source_refuses_when_an_active_follower_is_locked(op, kind):
    _refuse_both_ways(_locked_follower(kind), op, "le clip verrouillé 'f'")


def test_moving_a_source_in_another_sequence_is_also_refused_but_an_inactive_link_or_a_free_follower_does_not_block():
    _refuse_both_ways(_locked_follower("other"), _op("move_clip", clip="a", at=20.0), "le clip verrouillé 'g'")
    assert _applies(_locked_follower("layer", enabled=False), _op("move_clip", clip="a", at=20.0))
    free = _project()
    _follow(free)
    assert find_clip(_applies(free, _op("move_clip", clip="a", at=20.0)), "a").timeline_start == 20.0


# --- Multicam : calque verrouillé d'un segment, d'un clip d'angle -------------------------------------------------------------


def test_switching_an_angle_refuses_a_locked_layer_segment_and_a_locked_follower():
    for op in ({"op": "switch_multicam_angle", "at": 4, "angle": "angle-2"}, {"op": "switch_multicam_angle", "at": 0, "angle": "angle-2"}):
        project = _with_segment()
        _lock_layer(project, "seg")
        before = copy.deepcopy(project)
        _session, report = _apply_multicam(project, op)
        assert not report.ok and "verrouillé" in report.error and project == before, op
        with pytest.raises(ValueError, match="verrouillé"):
            VERBS["switch_multicam_angle"].build({key: value for key, value in op.items() if key != "op"} | {"angle": "angle-2"})(project)
        assert project == before


def test_switching_an_angle_refuses_when_the_segment_is_followed_by_a_locked_clip():
    project = _with_segment()
    project.tracks.append(Track("V9", "V9", "video", locked=True, clips=[Clip("fol", "cam1", "V9", 0.0, 0.0, 1.0)]))
    find_clip(project, "fol").tracking = ClipTracking(links=(TrackLink(source_clip_id="seg", tracker_ids=("t1",)),))
    before = copy.deepcopy(project)
    _session, report = _apply_multicam(project, {"op": "switch_multicam_angle", "at": 4, "angle": "angle-2"})
    assert not report.ok and "le clip verrouillé 'fol'" in report.error and project == before
    project.tracks[-1].locked = False
    assert _apply_multicam(project, {"op": "switch_multicam_angle", "at": 4, "angle": "angle-2"})[1].ok


def test_setting_an_angle_offset_refuses_a_locked_layer_on_the_angle_track():
    project = _with_source()
    clip = project.get_sequence("show").tracks[1].clips[0]
    clip.graphic = GraphicOverlay(type=GraphicType.TEXT, locked=True)
    before = copy.deepcopy(project)
    op = {"op": "set_multicam_angle_offset", "sequence": "show", "angle": "angle-2", "offset": 2}
    _session, report = _apply_multicam(project, op)
    assert not report.ok and "verrouillé" in report.error and project == before
    with pytest.raises(ValueError, match="verrouillé"):
        VERBS["set_multicam_angle_offset"].build({"sequence": "show", "angle": "angle-2", "offset": 2})(project)
    assert project == before
    project.get_sequence("show").tracks[1].clips[0].graphic = None
    assert _apply_multicam(project, op)[1].ok


# --- update_leaderboard : les calques remplacés sont supprimés ---------------------------------------------------------------


def test_updating_a_leaderboard_refuses_when_a_locked_clip_follows_one_of_its_layers():
    rows = [{"rank": "1", "name": "Alice", "value": "302"}]
    session = Session(_project())
    assert apply_script(session, parse_script({"kut_script": 1, "ops": [_op("add_leaderboard", rows=rows, start=0.0, duration=6.0, id="lb")]})).ok
    project = session.project
    member = project.active_sequence.generated_groups["lb"]["clips"][0]
    _follow(project, source=member, continuation=("b",))
    _lock_layer(project, "f")
    _refuse_both_ways(project, _op("update_leaderboard", leaderboard="lb", duration=9.0), "verrouillé")
    project.tracks[1].clips[0].graphic = None
    assert _applies(project, _op("update_leaderboard", leaderboard="lb", duration=9.0))


# --- Une garde ne refuse que ce que l'opération modifierait -------------------------------------------------------------------


def test_a_batch_cut_on_beats_leaves_an_uncut_clip_alone_even_when_a_locked_clip_follows_it():
    project = _project()
    project.tracks[2].clips.append(Clip("s", "m", "V3", 20.0, 0.0, 0.3))          # plus court qu'un temps : aucune coupe intérieure
    _follow(project, source="s")
    _lock_layer(project, "f")
    before = copy.deepcopy(find_clip(project, "f").tracking)
    cut = _applies(project, _op("cut_clips_on_beats", clips=["a", "s"]))
    assert find_clip(cut, "f").tracking == before and find_clip(cut, "a").duration < 4.0
    followed = _project()
    followed.tracks[2].clips.append(Clip("s", "m", "V3", 20.0, 0.0, 0.3))
    _follow(followed, source="a")
    _lock_layer(followed, "f")
    _refuse_both_ways(followed, _op("cut_clips_on_beats", clips=["a", "s"]), "le clip verrouillé 'f'")


def test_switching_to_the_angle_already_shown_or_at_the_start_of_the_segment_changes_no_link_so_it_is_not_refused():
    project = _with_segment()
    project.tracks.append(Track("V9", "V9", "video", locked=True, clips=[Clip("fol", "cam1", "V9", 0.0, 0.0, 1.0)]))
    find_clip(project, "fol").tracking = ClipTracking(links=(TrackLink(source_clip_id="seg", tracker_ids=("t1",)),))
    before = copy.deepcopy(project)
    session, report = _apply_multicam(project, {"op": "switch_multicam_angle", "at": 4, "angle": "angle-1"})      # déjà l'angle montré
    assert report.ok and session.project == before
    session, report = _apply_multicam(project, {"op": "switch_multicam_angle", "at": 0, "angle": "angle-2"})      # début : angle remplacé, sans coupe
    assert report.ok and find_clip(session.project, "fol").tracking == find_clip(before, "fol").tracking


def test_a_cut_outside_the_clip_keeps_the_models_own_refusal_and_a_move_to_the_same_instant_is_not_blocked():
    project = _locked_follower("layer")
    _refuse_both_ways(project, _op("cut_clip", clip="a", at=9.0), "strictement")
    for op in (_op("move_clip", clip="a", at=0.0), _op("move_clips", placements=[{"clip": "a", "track": "V1", "at": 0.0}]),
               _op("move_clip_to_track", clip="a", track="V3", at=0.0)):
        assert _applies(_locked_follower("layer"), op)
