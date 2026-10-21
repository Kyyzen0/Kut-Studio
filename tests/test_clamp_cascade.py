"""Raccourcir une séquence raccourcit ses parents : un seul appel de ``clamp_nested_clips`` suffit."""

from __future__ import annotations

from core.project_model import Clip, MediaAsset, Project, Sequence, Track
from core.sequences import clamp_nested_clips, insert_sequence_clip


def _sequence(sequence_id: str, clips=()) -> Sequence:
    return Sequence(sequence_id, sequence_id, 160, 90, 10.0, tracks=[
        Track("V1", "V1", "video", clips=list(clips)), Track("V2", "V2", "video"),
    ])


def _three_levels():
    """``main`` ⊃ ``mid`` ⊃ ``leaf`` (4 s de média), chaque niveau imbriquant le suivant en entier."""
    media = MediaAsset("m", "/tmp/m.mp4", "m", 10.0, 160, 90, 10.0, "video", False)
    leaf = _sequence("leaf", [Clip("l", "m", "V1", 0.0, 0.0, 4.0)])
    mid, main = _sequence("mid"), _sequence("main")
    project = Project("p", media_assets=[media], sequences=[main, mid, leaf], active_sequence_id="main")
    in_mid = insert_sequence_clip(project, "leaf", "V2", 0.0, parent_sequence_id="mid")
    in_main = insert_sequence_clip(project, "mid", "V2", 0.0, parent_sequence_id="main")
    return project, leaf, in_mid, in_main


def test_one_call_clamps_every_level_and_the_next_call_changes_nothing():
    project, leaf, in_mid, in_main = _three_levels()
    leaf.tracks[0].clips[0].source_out = 2.0

    adjustments = clamp_nested_clips(project)

    assert [(a.sequence_id, a.new_source_out) for a in adjustments] == [("mid", 2.0), ("main", 2.0)]
    assert in_mid.source_out == 2.0 and in_main.source_out == 2.0
    assert clamp_nested_clips(project) == []                      # idempotent


def test_the_cascade_reaches_a_parent_outside_the_requested_sequences():
    project, leaf, _in_mid, in_main = _three_levels()
    leaf.tracks[0].clips[0].source_out = 2.0
    adjustments = clamp_nested_clips(project, {"leaf"})
    assert {a.sequence_id for a in adjustments} == {"mid", "main"}
    assert in_main.source_out == 2.0


def test_a_requested_sequence_that_did_not_shrink_leaves_the_others_alone():
    project, leaf, in_mid, in_main = _three_levels()
    leaf.tracks[0].clips[0].source_out = 2.0
    assert clamp_nested_clips(project, {"mid"}) == []             # « mid » n'a pas bougé : rien à propager
    assert in_mid.source_out == 4.0 and in_main.source_out == 4.0


def test_a_lengthened_source_changes_nothing_at_any_level():
    project, leaf, in_mid, in_main = _three_levels()
    leaf.tracks[0].clips[0].source_out = 8.0
    assert clamp_nested_clips(project) == []
    assert in_mid.source_out == 4.0 and in_main.source_out == 4.0


def test_a_hand_edited_cyclic_file_still_terminates():
    """Deux séquences qui s'imbriquent l'une l'autre (fichier retouché) : la propagation reste bornée."""
    media = MediaAsset("m", "/tmp/m.mp4", "m", 10.0, 160, 90, 10.0, "video", False)
    first = _sequence("a", [Clip("ca", "m", "V1", 0.0, 0.0, 4.0), Clip("na", "", "V2", 0.0, 0.0, 4.0, sequence_id="b")])
    second = _sequence("b", [Clip("cb", "m", "V1", 0.0, 0.0, 4.0), Clip("nb", "", "V2", 0.0, 0.0, 4.0, sequence_id="a")])
    project = Project("p", media_assets=[media], sequences=[first, second], active_sequence_id="a")
    first.tracks[0].clips[0].source_out = 1.0
    adjustments = clamp_nested_clips(project)
    assert len(adjustments) <= 2 * (len(project.sequences) + 1)
