"""Format ``.kut`` 22 : écrit seulement si le projet contient un calque « scène 3D » (lot B2a).

Les projets sans calque 3D restent écrits octet pour octet comme avant : leur empreinte SHA-256 a été calculée avec le code de
``main`` AVANT la création de ``GraphicType.SCENE3D`` (les projets de référence sont construits ici, de façon déterministe).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace

import pytest

from core import project_io
from core.composition import GraphicNode
from core.composition_ops import convert_to_composition
from core.graphics import GraphicOverlay, GraphicType, ShapeKind, add_graphic_clip
from core.mograph_layers import (
    copy_layers,
    delete_layers,
    duplicate_layers,
    group_layers,
    paste_layers,
)
from core.project_io import (
    CURRENT_VERSION,
    SCENE3D_VERSION,
    SUPPORTED_VERSIONS,
    TRANSITION_CATALOG_VERSION,
    load_project,
    payload_uses_scene3d,
    project_payload,
    save_project,
)
from core.project_model import Clip, MediaAsset, Project, Track
from core.sequences import create_sequence, create_sequence_from_selection
from core.timeline_operations import cut_clip, duplicate_clip, trim_clip_left
from core.transitions import TransitionType, add_transition
from tests.scene3d_samples import base_project, project_with_scene, write_scene


def reference_projects() -> dict[str, Project]:
    asset = MediaAsset("m1", "/tmp/a.mp4", "a", 20.0, 64, 36, 25.0, "video")

    def base() -> Project:
        clips = [
            Clip("a", "m1", "V1", 0.0, 0.0, 3.0),
            Clip("b", "m1", "V1", 3.0, 0.0, 3.0),
        ]
        return Project(
            "p",
            width=64,
            height=36,
            fps=25.0,
            media_assets=[asset],
            tracks=[Track("V1", "V1", "video", clips=clips)],
        )

    video = base()
    graphics = base()
    for index, kind in enumerate(
        (
            GraphicType.TEXT,
            GraphicType.RECTANGLE,
            GraphicType.SOLID,
            GraphicType.ADJUSTMENT,
            GraphicType.NULL,
        )
    ):
        add_graphic_clip(
            graphics,
            kind,
            timeline_start=float(index),
            duration=2.0,
            clip_id=f"g{index}",
        )
    add_graphic_clip(
        graphics,
        GraphicType.SHAPE,
        timeline_start=1.0,
        shape=ShapeKind.ELLIPSE,
        clip_id="shape",
    )
    add_graphic_clip(graphics, GraphicType.LIGHT, timeline_start=0.0, clip_id="light")
    rich = base()
    add_transition(rich, "a", "b", TransitionType.SLIDE_LEFT, 0.5)
    add_graphic_clip(rich, GraphicType.TEXT, timeline_start=0.0, clip_id="title")
    create_sequence(
        rich,
        "Deux",
        sequence_id="seqtwo",
        tracks=[
            Track("V1", "V1", "video", clips=[Clip("c", "m1", "V1", 0.0, 0.0, 2.0)])
        ],
    )
    return {"video": video, "graphics": graphics, "rich": rich}


def _digest(project: Project) -> str:
    payload = project_payload(project)
    text = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    text = re.sub(
        r"transition-[0-9a-f]{12}", "transition-ID", text
    )  # identifiant tiré au hasard à la création
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


EXPECTED_DIGESTS = {
    "video": "4633858d6d83c6557417fcb922f8e321d4f5c6d801b76f4d240afe14b8c3fe7c",
    "graphics": "f8cc47f366d80635a2efe30a6951680a8b5094249bd79a40e5c1766c7e4b78ea",
    "rich": "1b6d3f8dbe539dcc2b2638a366f07518d7135aa606fde65d409ef1673bee258c",
}
"""Calculées avec le code d'avant ``GraphicType.SCENE3D`` ; un changement d'un octet d'un projet sans 3D les casse."""


@pytest.mark.parametrize("name", sorted(EXPECTED_DIGESTS))
def test_projects_without_a_3d_layer_are_written_byte_for_byte_as_before(name):
    project = reference_projects()[name]
    assert _digest(project) == EXPECTED_DIGESTS[name]
    assert project_payload(project)["version"] == CURRENT_VERSION == 17
    assert not payload_uses_scene3d(project_payload(project))


def test_the_saved_file_of_a_project_without_3d_has_no_trace_of_it(tmp_path):
    path = tmp_path / "p.kut"
    save_project(reference_projects()["graphics"], path)
    assert b"scene3d" not in path.read_bytes()
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 17


def test_constants():
    assert (CURRENT_VERSION, TRANSITION_CATALOG_VERSION, SCENE3D_VERSION) == (
        17,
        21,
        22,
    )
    assert SCENE3D_VERSION in SUPPORTED_VERSIONS


def test_a_3d_layer_writes_version_22_and_round_trips_byte_for_byte(tmp_path):
    project, clip, scene = project_with_scene(tmp_path)
    path = tmp_path / "t.kut"
    save_project(project, path)
    assert (
        json.loads(path.read_text(encoding="utf-8"))["version"] == SCENE3D_VERSION == 22
    )
    loaded = load_project(path)
    layer = next(c for t in loaded.tracks for c in t.clips if c.id == clip.id)
    assert layer.graphic == clip.graphic and layer.graphic.type is GraphicType.SCENE3D
    assert layer.graphic.source_path == str(scene.resolve())
    assert (layer.timeline_start, layer.source_in, layer.source_out) == (1.0, 0.0, 5.0)
    again = tmp_path / "again.kut"
    save_project(loaded, again)
    assert again.read_bytes() == path.read_bytes()


def test_the_version_follows_the_content_when_the_last_3d_layer_is_deleted(tmp_path):
    project, clip, _scene = project_with_scene(tmp_path)
    assert project_payload(project)["version"] == 22
    delete_layers(project, [clip.id])
    assert project_payload(project)["version"] == 17


def test_the_version_never_goes_below_what_another_content_requires(tmp_path):
    project, _clip, _scene = project_with_scene(tmp_path)
    add_transition(project, "a", "b", TransitionType.COVER_LEFT, 0.5)  # exige 21
    assert project_payload(project)["version"] == 22
    project.transitions.clear()
    assert project_payload(project)["version"] == 22  # la scène exige toujours 22


def test_a_catalog_transition_alone_still_writes_21(tmp_path):
    project = base_project()
    add_transition(project, "a", "b", TransitionType.COVER_LEFT, 0.5)
    assert project_payload(project)["version"] == 21


def test_a_build_that_does_not_know_version_22_refuses_the_file(tmp_path, monkeypatch):
    project, _clip, _scene = project_with_scene(tmp_path)
    path = tmp_path / "t.kut"
    save_project(project, path)
    monkeypatch.setattr(
        project_io, "SUPPORTED_VERSIONS", SUPPORTED_VERSIONS - {SCENE3D_VERSION}
    )
    with pytest.raises(ValueError, match="Version de fichier Kut-Studio non supportée"):
        load_project(path)


def test_a_3d_layer_in_a_secondary_sequence_writes_version_22(tmp_path):
    project = base_project()
    scene = write_scene(tmp_path)
    create_sequence(project, "Deux", sequence_id="seqtwo", activate=True)
    add_graphic_clip(
        project,
        GraphicType.SCENE3D,
        timeline_start=0.0,
        source_path=str(scene),
        clip_id="s3d",
    )
    assert not any(c.graphic for t in project.sequences[0].tracks for c in t.clips)
    assert len(project.sequences) == 2
    assert project_payload(project)["version"] == 22


def test_a_3d_layer_inside_a_nested_sequence_writes_version_22(tmp_path):
    project, clip, _scene = project_with_scene(tmp_path, start=0.0, duration=3.0)
    result = create_sequence_from_selection(project, [clip.id], "Imbriquée")
    assert result.clip.sequence_id
    assert all(
        c.graphic is None for t in project.tracks for c in t.clips
    )  # plus aucun calque sur la séquence active
    assert project_payload(project)["version"] == 22


def test_a_3d_layer_in_a_group_writes_version_22(tmp_path):
    project, clip, _scene = project_with_scene(tmp_path)
    group = group_layers(project, [clip.id])
    assert clip.graphic.group_id == group.id
    assert project_payload(project)["version"] == 22


def test_a_3d_layer_in_a_serialized_composition_is_seen():
    scene = {"graphic": {"type": "scene3d", "source_path": "/x/s.kut3d"}}
    composition_payload = {
        "clips": [{"composition": {"graph": {"nodes": [{"kind": "graphic", **scene}]}}}]
    }
    assert payload_uses_scene3d(composition_payload)
    assert not payload_uses_scene3d(
        {
            "clips": [
                {
                    "composition": {
                        "graph": {
                            "nodes": [
                                {
                                    "kind": "graphic",
                                    "graphic": {"type": "text", "source_path": ""},
                                }
                            ]
                        }
                    }
                }
            ]
        }
    )
    assert not payload_uses_scene3d(
        {"type": "scene3d"}
    )  # une autre chose qui porte ce mot : pas un calque


def test_a_real_composition_node_holding_a_3d_layer_writes_version_22(tmp_path):
    project = base_project()
    add_graphic_clip(
        project, GraphicType.TEXT, timeline_start=0.0, duration=3.0, clip_id="title"
    )
    converted = convert_to_composition(project, ["a", "title"])
    composition = converted.clip.composition
    scene = GraphicOverlay(
        type=GraphicType.SCENE3D, text="", source_path=str(write_scene(tmp_path))
    )
    nodes = tuple(
        replace(n, graphic=scene) if isinstance(n, GraphicNode) else n
        for n in composition.graph.nodes
    )
    converted.clip.composition = replace(
        composition, graph=replace(composition.graph, nodes=nodes)
    )
    assert project_payload(project)["version"] == 22


def test_the_reader_reads_the_same_way_a_17_file_and_a_22_file(tmp_path):
    plain = tmp_path / "plain.kut"
    save_project(reference_projects()["graphics"], plain)
    assert [c.id for t in load_project(plain).tracks for c in t.clips if c.graphic] == [
        c.id
        for t in reference_projects()["graphics"].tracks
        for c in t.clips
        if c.graphic
    ]


def test_a_cut_clip_keeps_the_scene_and_advances_source_in(tmp_path):
    project, clip, scene = project_with_scene(tmp_path, start=1.0, duration=5.0)
    left, right = cut_clip(project, clip.id, 3.0)
    assert (left.source_in, left.source_out) == (0.0, 2.0)
    assert (right.source_in, right.source_out) == (
        2.0,
        5.0,
    ) and right.timeline_start == 3.0
    for half in (left, right):
        assert (
            half.graphic.type is GraphicType.SCENE3D
            and half.graphic.source_path == str(scene.resolve())
        )
    path = tmp_path / "cut.kut"
    save_project(project, path)
    loaded = load_project(path)
    halves = {c.id: c for t in loaded.tracks for c in t.clips if c.graphic}
    assert halves[right.id].source_in == 2.0 and halves[
        right.id
    ].graphic.source_path == str(scene.resolve())


def test_a_duplicated_clip_keeps_the_scene_and_the_source_in(tmp_path):
    project, clip, scene = project_with_scene(tmp_path)
    cut = trim_clip_left(project, clip.id, 2.0)
    assert cut.source_in == 1.0
    copy = duplicate_clip(project, clip.id)
    assert copy.id != clip.id and copy.source_in == 1.0
    assert copy.graphic == clip.graphic and copy.graphic.source_path == str(
        scene.resolve()
    )


def test_a_pasted_layer_keeps_the_scene_and_the_source_in(tmp_path):
    project, clip, scene = project_with_scene(tmp_path)
    trim_clip_left(project, clip.id, 2.0)
    clipboard = copy_layers(project, [clip.id])
    pasted = paste_layers(project, clipboard, at=10.0)[0]
    assert (
        pasted.id != clip.id
        and pasted.source_in == 1.0
        and pasted.timeline_start == 10.0
    )
    assert (
        pasted.graphic.type is GraphicType.SCENE3D
        and pasted.graphic.source_path == str(scene.resolve())
    )
    dup = duplicate_layers(project, [clip.id])[0]
    assert dup.graphic.source_path == str(scene.resolve()) and dup.source_in == 1.0
