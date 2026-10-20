"""Verrou des suiveurs d'un suivi au niveau du MODÈLE : couper ou supprimer une source ne réécrit jamais un suiveur verrouillé.

``cut_clip`` (via ``follow_cut``) ajoute la moitié droite aux liaisons des suiveurs ; ``delete_clip`` / ``ripple_delete_clip`` /
``delete_clips`` (via ``release_source``) retirent la source supprimée. Le refus est dans le modèle, donc commun à l'interface, aux scripts
et aux menus ; il passe AVANT la première écriture (projet strictement inchangé) et ne vise que les suiveurs réellement modifiés.
"""

from __future__ import annotations

import copy
from dataclasses import replace

import pytest

from core.beat_edit import cut_clips_on_beats
from core.beat_grid import BeatGrid
from core.graphics import GraphicOverlay, GraphicType
from core.project_model import Clip, MediaAsset, Project, Sequence, Track
from core.timeline_editing import delete_clips
from core.timeline_operations import cut_clip, delete_clip, find_clip, ripple_delete_clip
from core.tracking_model import ClipTracking, TrackLink


def _project() -> Project:
    return Project(
        "Démo", width=320, height=180, fps=30.0,
        media_assets=[MediaAsset("m", "/x/a.mp4", "a", 60.0, 320, 180, 30.0, "video", has_audio=True)],
        tracks=[
            Track("V1", "V1", "video", clips=[Clip("a", "m", "V1", 0.0, 0.0, 4.0), Clip("b", "m", "V1", 4.0, 0.0, 3.0)]),
            Track("V2", "V2", "video", clips=[Clip("f", "m", "V2", 0.0, 0.0, 2.0)]),
        ],
    )


def _follow(project: Project, follower: str = "f", source: str = "a", *, continuation: tuple[str, ...] = (), enabled: bool = True) -> None:
    clip = next(c for t in project.all_tracks() for c in t.clips if c.id == follower)
    clip.tracking = ClipTracking(links=(
        TrackLink(source_clip_id=source, tracker_ids=("t1",), continuation_ids=continuation, enabled=enabled),))


def _locked(kind: str, **link) -> Project:
    project = _project()
    if kind == "other":
        project.sequences.append(Sequence("seq-b", "B", width=320, height=180, fps=30.0,
                                          tracks=[Track("W1", "W1", "video", locked=True, clips=[Clip("g", "m", "W1", 0.0, 0.0, 1.0)])]))
        _follow(project, "g", **link)
        return project
    _follow(project, **link)
    if kind == "layer":
        clip = find_clip(project, "f")
        clip.graphic = replace(clip.graphic or GraphicOverlay(type=GraphicType.TEXT), locked=True)
    else:
        project.tracks[1].locked = True
    return project


KINDS = ("layer", "track", "other")


def _refused(project: Project, call, fragment: str = "verrouillé") -> None:
    before = copy.deepcopy(project)
    with pytest.raises(ValueError) as error:
        call(project)
    assert fragment in str(error.value) and project == before, "refus avant la première écriture"


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("enabled", [True, False])
def test_cutting_a_source_with_a_locked_follower_is_refused_by_the_model(kind, enabled):
    _refused(_locked(kind, enabled=enabled), lambda p: cut_clip(p, "a", 2.0), "le clip verrouillé")


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("call", [
    lambda p: delete_clip(p, "a"), lambda p: ripple_delete_clip(p, "a"), lambda p: delete_clips(p, ["a"]),
    lambda p: delete_clips(p, ["a"], ripple=True),
], ids=["delete", "ripple", "delete_clips", "delete_clips_ripple"])
def test_deleting_a_source_whose_locked_follower_would_be_rewritten_is_refused(kind, call):
    _refused(_locked(kind, continuation=("b",)), call)


def test_a_guard_only_refuses_what_the_operation_would_really_change():
    plain = _locked("layer")                                           # aucune prolongation : la suppression ne réécrit pas la liaison
    before = copy.deepcopy(find_clip(plain, "f").tracking)
    delete_clip(plain, "a")
    assert find_clip(plain, "f").tracking == before
    other = _locked("layer", source="b")                                # suit une autre source : la coupe de « a » ne le touche pas
    cut_clip(other, "a", 2.0)
    assert find_clip(other, "f").tracking.links[0].source_clip_id == "b"
    free = _project()
    _follow(free)
    _left, right = cut_clip(free, "a", 2.0)
    assert find_clip(free, "f").tracking.links[0].continuation_ids == (right.id,)


def test_the_follower_deleted_in_the_same_operation_does_not_block():
    project = _locked("track", continuation=("b",))
    project.tracks[1].locked = False
    _follow(project, continuation=("b",))
    delete_clips(project, ["a", "f"])
    assert [c.id for c in project.tracks[0].clips] == ["b"] and not project.tracks[1].clips


def test_delete_clips_checks_everything_before_the_first_deletion():
    project = _locked("layer", continuation=("b",))
    _refused(project, lambda p: delete_clips(p, ["b", "a"]))            # « b » seul aurait pu partir avant « a »


def test_cutting_several_clips_on_beats_is_atomic():
    project = _project()
    project.active_sequence.beat_grid = BeatGrid(120.0)
    _follow(project, "f", "b")
    find_clip(project, "f").graphic = GraphicOverlay(type=GraphicType.TEXT, locked=True)
    _refused(project, lambda p: cut_clips_on_beats(p, ["a", "b"], p.active_sequence.beat_grid), "le clip verrouillé")


def test_a_locked_follower_of_the_right_half_is_still_covered_after_a_free_cut():
    project = _project()
    _follow(project)
    _left, right = cut_clip(project, "a", 2.0)
    find_clip(project, "f").graphic = GraphicOverlay(type=GraphicType.TEXT, locked=True)
    _refused(project, lambda p: delete_clip(p, right.id))


def test_updating_a_leaderboard_refuses_before_removing_anything_when_a_locked_follower_would_be_rewritten():
    from core.leaderboard import Leaderboard, LeaderboardRow, build_leaderboard, update_leaderboard

    project = _project()
    board = Leaderboard((LeaderboardRow("1", "A", "9"),), start=0.0, duration=3.0, id="lb1")
    clips = build_leaderboard(project, board)
    layers = [clip.id for clip in project.active_sequence.tracks[-1].clips] if clips else []
    assert layers
    # Un clip verrouillé suit le premier calque ET une moitié qui le prolonge : sa liaison serait réécrite.
    _follow(project, "f", layers[0], continuation=(layers[1],) if len(layers) > 1 else ("b",))
    find_clip(project, "f").graphic = GraphicOverlay(type=GraphicType.TEXT, locked=True)
    _refused(project, lambda p: update_leaderboard(p, board))
