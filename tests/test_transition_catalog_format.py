"""Format ``.kut`` du catalogue élargi de transitions : version 21 écrite seulement si un des 25 types est utilisé."""

from __future__ import annotations

import json

import pytest

from core import project_io
from core.project_io import (
    CURRENT_VERSION,
    SUPPORTED_VERSIONS,
    TRANSITION_CATALOG_VERSION,
    load_project,
    project_payload,
    save_project,
)
from core.project_model import Clip, MediaAsset, Project, Track
from core.sequences import create_sequence
from core.transitions import CATALOG_EXTENSION_TYPES, TransitionType, add_transition

LEGACY_TYPES = [t for t in TransitionType if t not in CATALOG_EXTENSION_TYPES]


def _project() -> Project:
    asset = MediaAsset("m1", "/tmp/a.mp4", "a", 20.0, 64, 36, 25.0, "video")
    clips = [Clip("a", "m1", "V1", 0.0, 0.0, 3.0), Clip("b", "m1", "V1", 3.0, 0.0, 3.0)]
    return Project("p", width=64, height=36, fps=25.0, media_assets=[asset], tracks=[Track("V1", "V1", "video", clips=clips)])


def test_constants():
    assert (CURRENT_VERSION, TRANSITION_CATALOG_VERSION) == (17, 21)
    assert TRANSITION_CATALOG_VERSION in SUPPORTED_VERSIONS


@pytest.mark.parametrize("transition_type", LEGACY_TYPES, ids=lambda t: t.value)
def test_legacy_types_keep_the_minimal_version(transition_type):
    project = _project()
    add_transition(project, "a", "b", transition_type, 0.5)
    assert project_payload(project)["version"] == CURRENT_VERSION == 17


def test_a_project_without_transition_keeps_the_minimal_version():
    assert project_payload(_project())["version"] == 17


@pytest.mark.parametrize("transition_type", sorted(CATALOG_EXTENSION_TYPES, key=lambda t: t.value), ids=lambda t: t.value)
def test_every_new_type_writes_version_21_and_round_trips(tmp_path, transition_type):
    project = _project()
    add_transition(project, "a", "b", transition_type, 0.8)
    path = tmp_path / "t.kut"
    save_project(project, path)
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == TRANSITION_CATALOG_VERSION == 21
    loaded = load_project(path)
    assert [(t.type, t.duration) for t in loaded.transitions] == [(transition_type, 0.8)]
    again = tmp_path / "again.kut"
    save_project(loaded, again)
    assert again.read_bytes() == path.read_bytes()


def test_the_version_follows_the_content_when_the_last_new_transition_is_replaced():
    project = _project()
    transition = add_transition(project, "a", "b", TransitionType.COVER_LEFT, 0.5)
    assert project_payload(project)["version"] == 21
    project.transitions.remove(transition)
    add_transition(project, "a", "b", TransitionType.SLIDE_LEFT, 0.5)
    assert project_payload(project)["version"] == 17


def test_a_new_type_in_a_secondary_sequence_writes_version_21():
    project = _project()
    clips = [Clip("c", "m1", "V1", 0.0, 0.0, 3.0), Clip("d", "m1", "V1", 3.0, 0.0, 3.0)]
    create_sequence(project, "Deux", tracks=[Track("V1", "V1", "video", clips=clips)], activate=True)
    add_transition(project, "c", "d", TransitionType.WIPE_TL, 0.5)
    first = project.sequences[0]
    assert first.transitions == [] and len(project.sequences) == 2
    assert project_payload(project)["version"] == 21


def test_a_file_with_a_new_type_is_refused_by_a_build_that_does_not_know_version_21(tmp_path, monkeypatch):
    project = _project()
    add_transition(project, "a", "b", TransitionType.DIAG_BR, 0.5)
    path = tmp_path / "t.kut"
    save_project(project, path)
    monkeypatch.setattr(project_io, "SUPPORTED_VERSIONS", SUPPORTED_VERSIONS - {TRANSITION_CATALOG_VERSION})
    with pytest.raises(ValueError, match="Version de fichier Kut-Studio non supportée"):
        load_project(path)


def test_the_reader_reads_a_new_type_back(tmp_path):
    project = _project()
    add_transition(project, "a", "b", TransitionType.FADE_GRAYS, 0.5)
    path = tmp_path / "t.kut"
    save_project(project, path)
    assert load_project(path).transitions[0].type is TransitionType.FADE_GRAYS
