"""Supprimer un clip par la timeline ne doit laisser aucune référence ni média technique orphelins.

Le panneau Calques passait par ``delete_layers`` (qui nettoie) ; la touche Suppr de la timeline passait par
``delete_clip`` / ``ripple_delete_clip``, qui laissaient le parent/groupe des enfants pointer dans le vide et
le média technique du calque dans chaque enregistrement.
"""

from __future__ import annotations

import pytest

from core.graphics import add_graphic_clip
from core.mograph_layers import group_layers, set_parent
from core.project_model import Clip, MediaAsset, Project, Track
from core.timeline_operations import delete_clip, ripple_delete_clip


def _project() -> Project:
    return Project(name="Calques", width=160, height=90, fps=10.0)


def _graphic_assets(project: Project) -> set[str]:
    return {asset.id for asset in project.media_assets if asset.media_type == "graphic"}


@pytest.mark.parametrize("delete", [delete_clip, ripple_delete_clip], ids=["delete", "ripple"])
def test_deleting_a_parent_layer_frees_its_children_and_its_technical_media(delete):
    project = _project()
    parent = add_graphic_clip(project, "shape", timeline_start=0, duration=2)
    child = add_graphic_clip(project, "shape", timeline_start=0, duration=2)
    set_parent(project, child.id, parent.id)
    assert child.graphic.parent_id == parent.id and parent.asset_id in _graphic_assets(project)

    delete(project, parent.id)

    assert child.graphic.parent_id == ""
    assert parent.asset_id not in _graphic_assets(project)
    assert child.asset_id in _graphic_assets(project)                      # l'autre calque garde son média


def test_deleting_a_group_releases_its_members_as_standalone_layers():
    project = _project()
    first = add_graphic_clip(project, "shape", timeline_start=0, duration=2)
    second = add_graphic_clip(project, "shape", timeline_start=0, duration=2)
    group = group_layers(project, [first.id, second.id])
    assert first.graphic.group_id == group.id

    delete_clip(project, group.id)

    assert first.graphic.group_id == "" and second.graphic.group_id == ""
    assert {first.asset_id, second.asset_id} <= _graphic_assets(project)    # les membres, eux, restent
    assert group.asset_id not in _graphic_assets(project)


def test_a_technical_media_still_used_by_another_clip_is_kept():
    project = _project()
    original = add_graphic_clip(project, "shape", timeline_start=0, duration=2)
    twin = Clip(
        id="twin", asset_id=original.asset_id, track_id=original.track_id, timeline_start=3.0,
        source_in=original.source_in, source_out=original.source_out, graphic=original.graphic,
    )
    next(track for track in project.tracks if track.id == original.track_id).clips.append(twin)

    delete_clip(project, original.id)

    assert original.asset_id in _graphic_assets(project)
    delete_clip(project, twin.id)
    assert original.asset_id not in _graphic_assets(project)


def test_library_media_is_never_removed_with_its_clip():
    video = MediaAsset("v", "/tmp/v.mp4", "v", 3.0, 160, 90, 10.0, "video", False)
    project = Project(
        name="Biblio", width=160, height=90, fps=10.0, media_assets=[video],
        tracks=[Track("V1", "V1", "video", clips=[Clip("c", "v", "V1", 0.0, 0.0, 3.0)])],
    )
    delete_clip(project, "c")
    assert [asset.id for asset in project.media_assets] == ["v"]
