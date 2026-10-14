"""ADR-0006 lot 3b : un projet n'est écrit en version 18 que s'il contient un masque courbe (version minimale nécessaire)."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from test_composition import _graph

from core import project_io
from core.color_grading import ColorGradingService
from core.compositing import Compositing, Mask, MaskShape
from core.composition import OUTPUT_ID, Composition, MaskNode, SolidNode
from core.project_io import (
    CURRENT_VERSION, PATH_MASK_VERSION, SUPPORTED_VERSIONS, load_project, project_payload, project_uses_path_masks, save_project,
)
from core.project_model import Clip, MediaAsset, Project, Track
from core.sequences import create_sequence

CURVE = Mask(shape=MaskShape.PATH, id="curve001")
PLAIN = Compositing(masks=(Mask(id="rect0001"), Mask(shape=MaskShape.POLYGON, id="poly0001")))


def _project(compositing: Compositing | None = None) -> Project:
    project = Project(name="Version", width=160, height=90, fps=10.0)
    project.media_assets.append(MediaAsset("a", "/tmp/a.mp4", "a", 4.0, 160, 90, 10.0, "video"))
    clip = Clip("c", "a", "V1", 0.0, 0.0, 2.0, compositing=compositing or Compositing())
    project.tracks.append(Track("V1", "V1", "video", clips=[clip]))
    return project


def _version(project: Project, tmp_path) -> int:
    path = tmp_path / "p.kut"
    save_project(project, str(path))
    return json.loads(path.read_text(encoding="utf-8"))["version"]


def test_the_two_versions_and_what_this_build_reads():
    assert (CURRENT_VERSION, PATH_MASK_VERSION) == (17, 18) and {17, 18} <= SUPPORTED_VERSIONS


def test_a_project_without_a_curve_is_still_version_17_and_byte_identical(tmp_path):
    project = _project(PLAIN)
    path = tmp_path / "plain.kut"
    save_project(project, str(path))
    text = path.read_text(encoding="utf-8")
    raw = json.loads(text)
    assert raw["version"] == 17 and not project_uses_path_masks(project)
    # Le fichier complet, en-tête compris : le début est exactement celui d'avant (format, version 17, puis le projet).
    assert text.startswith('{\n  "format": "kut-studio-project",\n  "version": 17,\n  "project": {')
    assert '"handles"' not in text and '"shape": "path"' not in text
    assert list(raw) == ["format", "version", "project"]


def test_a_curve_in_the_active_sequence_makes_the_file_version_18_and_it_round_trips(tmp_path):
    project = _project(Compositing(masks=(CURVE,)))
    assert project_uses_path_masks(project) and project_payload(project)["version"] == 18
    path = tmp_path / "curve.kut"
    save_project(project, str(path))
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 18
    restored = load_project(str(path))
    assert restored.tracks[0].clips[0].compositing.masks == (CURVE,)
    again = tmp_path / "again.kut"
    save_project(restored, str(again))
    assert again.read_text(encoding="utf-8") == path.read_text(encoding="utf-8")        # 18 → 18, idempotent


def test_a_curve_in_another_sequence_counts(tmp_path):
    project = _project(PLAIN)
    other = create_sequence(project, "Autre")
    other.tracks[0].clips.append(Clip("d", "a", other.tracks[0].id, 0.0, 0.0, 1.0, compositing=Compositing(masks=(CURVE,))))
    assert project.active_sequence is not other and _version(project, tmp_path) == 18


def test_a_curve_in_a_composition_node_counts(tmp_path):
    graph = _graph(SolidNode("s"), MaskNode("k", (CURVE,)), links=(("s", "k"), ("k", OUTPUT_ID)))
    project = _project()
    project.tracks[0].clips[0].composition = Composition(graph, 2.0)
    assert _version(project, tmp_path) == 18
    project.tracks[0].clips[0].composition = Composition(_graph(SolidNode("s"), MaskNode("k", (Mask(),)), links=()), 2.0)
    assert _version(project, tmp_path) == 17


def test_a_curve_window_of_a_color_node_counts(tmp_path):
    project = _project()
    service = ColorGradingService()
    node = service.get_graph(project, "c").node_or_first(None).id
    service.edit_nodes(project, "c", lambda g: g.with_windows(node, (CURVE,)))
    assert _version(project, tmp_path) == 18
    service.edit_nodes(project, "c", lambda g: g.with_windows(node, ()))
    assert _version(project, tmp_path) == 17


def test_removing_the_last_curve_brings_the_file_back_to_17(tmp_path):
    project = _project(Compositing(masks=(CURVE, Mask(id="rect0001"))))
    assert _version(project, tmp_path) == 18
    clip = project.tracks[0].clips[0]
    clip.compositing = replace(clip.compositing, masks=tuple(m for m in clip.compositing.masks if m.shape is not MaskShape.PATH))
    assert _version(project, tmp_path) == 17


def test_an_older_loader_refuses_version_18_with_the_existing_message(tmp_path, monkeypatch):
    path = tmp_path / "curve.kut"
    save_project(_project(Compositing(masks=(CURVE,))), str(path))
    monkeypatch.setattr(project_io, "SUPPORTED_VERSIONS", SUPPORTED_VERSIONS - {PATH_MASK_VERSION})      # le build d'avant : ≤ 17
    with pytest.raises(ValueError, match="Version de fichier Kut-Studio non supportée.*18"):
        load_project(str(path))


def test_autosave_payload_follows_the_same_rule(tmp_path):
    assert project_payload(_project(PLAIN))["version"] == 17
    assert project_payload(_project(Compositing(masks=(CURVE,))))["version"] == 18
    path = tmp_path / "auto.kut"
    project_io.write_project_payload(project_payload(_project(Compositing(masks=(CURVE,)))), str(path))
    assert load_project(str(path)).tracks[0].clips[0].compositing.masks == (CURVE,)


def test_the_cli_reads_a_version_18_file(tmp_path, capsys):
    from core import cli

    path = tmp_path / "curve.kut"
    save_project(_project(Compositing(masks=(CURVE,))), str(path))
    assert cli.run(["info", str(path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["format_version"] == 18
    assert cli.run(["validate", str(path)]) == 0
