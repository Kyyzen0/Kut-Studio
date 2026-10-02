"""Imbriquer une sélection ne doit pas séparer un clip de celui dont il dépend (parent, groupe, tracking)."""

from __future__ import annotations

import copy
from dataclasses import replace

import pytest

from core.graphics import add_graphic_clip
from core.mograph_layers import group_layers, set_parent
from core.project_model import Clip, MediaAsset, Project, Track
from core.sequences import SequenceError, create_sequence_from_selection
from core.tracking_model import ClipTracking, TrackLink


def _project() -> Project:
    return Project(name="Dépendances", width=160, height=90, fps=10.0, tracks=[Track("V1", "V1", "video")])


def _two_layers(project: Project):
    parent = add_graphic_clip(project, "shape", timeline_start=0, duration=2)
    child = add_graphic_clip(project, "shape", timeline_start=0, duration=2)
    set_parent(project, child.id, parent.id)
    return parent, child


def _snapshot(project: Project):
    return copy.deepcopy((project.sequences, project.media_assets))


def test_a_selection_that_leaves_the_parent_behind_is_refused_and_changes_nothing():
    project = _project()
    _parent, child = _two_layers(project)
    before = _snapshot(project)
    with pytest.raises(SequenceError, match="Sélectionnez-les ensemble"):
        create_sequence_from_selection(project, [child.id])
    assert _snapshot(project) == before                      # aucune séquence créée, aucun clip déplacé


def test_a_selection_that_takes_the_parent_without_its_child_is_refused():
    project = _project()
    parent, _child = _two_layers(project)
    with pytest.raises(SequenceError):
        create_sequence_from_selection(project, [parent.id])


def test_a_parent_and_its_child_nest_together_and_keep_their_link():
    project = _project()
    parent, child = _two_layers(project)
    result = create_sequence_from_selection(project, [parent.id, child.id])
    inner = {clip.id: clip for track in result.sequence.tracks for clip in track.clips}
    assert set(inner) == {parent.id, child.id}
    assert inner[child.id].graphic.parent_id == parent.id


def test_a_group_cannot_be_split_from_its_members():
    project = _project()
    first = add_graphic_clip(project, "shape", timeline_start=0, duration=2)
    second = add_graphic_clip(project, "shape", timeline_start=0, duration=2)
    group = group_layers(project, [first.id, second.id])
    with pytest.raises(SequenceError):
        create_sequence_from_selection(project, [first.id])
    result = create_sequence_from_selection(project, [group.id, first.id, second.id])
    assert {clip.id for track in result.sequence.tracks for clip in track.clips} == {group.id, first.id, second.id}


def test_unrelated_layers_nest_freely():
    project = _project()
    first = add_graphic_clip(project, "shape", timeline_start=0, duration=2)
    add_graphic_clip(project, "shape", timeline_start=0, duration=2)
    result = create_sequence_from_selection(project, [first.id])
    assert [clip.id for track in result.sequence.tracks for clip in track.clips] == [first.id]


def _video_project_with_tracking():
    media = MediaAsset("m", "/tmp/m.mp4", "m", 4.0, 160, 90, 10.0, "video", False)
    source = Clip("source", "m", "V1", 0.0, 0.0, 3.0)
    target = Clip("target", "m", "V1", 0.0, 0.0, 3.0)
    target.tracking = ClipTracking(links=(TrackLink(id="link", source_clip_id="source", tracker_ids=("t1",)),))
    project = Project(
        name="Tracking", width=160, height=90, fps=10.0, media_assets=[media],
        tracks=[Track("V1", "V1", "video", clips=[source, target])],
    )
    return project, source, target


def test_a_tracking_link_cannot_be_separated_from_its_source_clip():
    project, source, target = _video_project_with_tracking()
    with pytest.raises(SequenceError, match="tracking"):
        create_sequence_from_selection(project, [target.id])
    with pytest.raises(SequenceError, match="tracking"):
        create_sequence_from_selection(project, [source.id])
    result = create_sequence_from_selection(project, [source.id, target.id])
    assert {clip.id for track in result.sequence.tracks for clip in track.clips} == {"source", "target"}


def test_a_link_to_the_clip_itself_or_to_a_missing_clip_does_not_block_nesting():
    project, source, target = _video_project_with_tracking()
    target.tracking = ClipTracking(links=(
        TrackLink(id="own", source_clip_id="", tracker_ids=("t1",)),            # le clip lui-même
        replace(TrackLink(id="gone", tracker_ids=("t1",)), source_clip_id="supprime"),   # source disparue
    ))
    result = create_sequence_from_selection(project, [target.id])
    assert [clip.id for track in result.sequence.tracks for clip in track.clips] == ["target"]
