"""Tests pour l'historique d'édition non destructif ``ProjectHistory``."""

from __future__ import annotations

import pytest

from core.edit_history import MAX_HISTORY, ProjectHistory
from core.project_model import Clip, MediaAsset, Project, Track


def _make_project(name: str = "Test") -> Project:
    return Project(
        name=name,
        tracks=[
            Track(id="V1", name="V1", type="video"),
            Track(id="S1", name="S1", type="subtitle"),
        ],
        media_assets=[
            MediaAsset(
                id="asset-v", path="/tmp/v.mp4", name="V",
                duration=10.0, width=1920, height=1080, fps=30.0,
                media_type="video",
            ),
        ],
    )


def _add_clip(project: Project, clip_id: str, start: float = 0.0) -> None:
    project.tracks[0].clips.append(
        Clip(
            id=clip_id,
            asset_id="asset-v",
            track_id="V1",
            timeline_start=start,
            source_in=0.0,
            source_out=2.0,
        )
    )


# ---------------------------------------------------------------------------
# Cas de base
# ---------------------------------------------------------------------------


def test_empty_history():
    history = ProjectHistory()
    project = _make_project()
    history.reset(project)

    assert history.can_undo is False
    assert history.can_redo is False
    assert history.undo_label is None
    assert history.redo_label is None
    assert history.is_dirty is False
    assert len(history) == 1


def test_record_then_undo_restores_previous_state():
    history = ProjectHistory()
    project = _make_project()
    history.reset(project)

    _add_clip(project, "c1")
    history.record(project, "Ajouter c1")

    assert history.can_undo is True
    assert history.can_redo is False
    assert history.undo_label == "Ajouter c1"
    assert history.is_dirty is True

    restored = history.undo()
    assert restored is not None
    assert restored.tracks[0].clips == []
    assert history.can_undo is False
    assert history.can_redo is True
    assert history.redo_label == "Ajouter c1"


def test_redo_reapplies_last_action():
    history = ProjectHistory()
    project = _make_project()
    history.reset(project)

    _add_clip(project, "c1")
    history.record(project, "Ajouter c1")

    history.undo()
    restored = history.redo()

    assert restored is not None
    assert len(restored.tracks[0].clips) == 1
    assert restored.tracks[0].clips[0].id == "c1"
    assert history.can_redo is False
    assert history.is_dirty is True


def test_new_action_after_undo_clears_redo_stack():
    history = ProjectHistory()
    project = _make_project()
    history.reset(project)

    _add_clip(project, "c1")
    history.record(project, "Ajouter c1")
    _add_clip(project, "c2")
    history.record(project, "Ajouter c2")

    assert history.can_undo
    history.undo()
    assert history.can_redo

    _add_clip(project, "c3")
    history.record(project, "Ajouter c3")

    # La nouvelle action doit avoir vidé le ``redo``.
    assert history.can_redo is False


# ---------------------------------------------------------------------------
# Indépendance des snapshots
# ---------------------------------------------------------------------------


def test_snapshots_are_independent_copies():
    """Modifier le projet courant ne doit pas affecter les snapshots."""
    history = ProjectHistory()
    project = _make_project()
    history.reset(project)

    _add_clip(project, "c1")
    history.record(project, "Snapshot c1")

    _add_clip(project, "c2")
    history.record(project, "Snapshot c1 + c2")

    # On mute ``project`` après les snapshots : aucun snapshot ne doit
    # être affecté par cette mutation.
    _add_clip(project, "c3-mutated-only-on-project")

    restored_with_c2 = history.undo()
    assert restored_with_c2 is not None
    # Le premier ``undo`` ramène au snapshot précédent (« c1 » seul).
    clip_ids = {c.id for c in restored_with_c2.tracks[0].clips}
    assert clip_ids == {"c1"}
    assert "c2" not in clip_ids
    assert "c3-mutated-only-on-project" not in clip_ids

    restored_initial = history.undo()
    assert restored_initial is not None
    clip_ids_initial = {c.id for c in restored_initial.tracks[0].clips}
    assert clip_ids_initial == set()
    # Le projet vivant, lui, contient bien c3.
    assert any(c.id == "c3-mutated-only-on-project" for c in project.tracks[0].clips)


def test_snapshots_have_independent_clips_lists():
    """Les listes ``clips`` des snapshots ne sont jamais partagées."""
    history = ProjectHistory()
    project = _make_project()
    history.reset(project)

    _add_clip(project, "c1")
    history.record(project, "Snapshot 1")

    _add_clip(project, "c2")
    history.record(project, "Snapshot 2")

    restored_1 = history.undo()
    assert restored_1 is not None

    # L'ajout d'un clip sur ``project`` ne doit pas affecter
    # le snapshot restauré.
    _add_clip(project, "c3-only-on-project")
    assert any(c.id == "c3-only-on-project" for c in project.tracks[0].clips)
    assert not any(c.id == "c3-only-on-project" for c in restored_1.tracks[0].clips)


# ---------------------------------------------------------------------------
# Limite de taille
# ---------------------------------------------------------------------------


def test_history_limit_is_100():
    history = ProjectHistory()
    project = _make_project()
    history.reset(project)

    for i in range(MAX_HISTORY + 50):
        _add_clip(project, f"c{i}")
        history.record(project, f"Ajout {i}")

    # On ne dépasse jamais ``MAX_HISTORY`` entrées.
    assert len(history) == MAX_HISTORY
    # L'opération la plus ancienne est bien perdue (rotation FIFO).
    assert history.undo_label != f"Ajout 0"
    # Et les plus récentes restent présentes.
    assert history.undo_label == f"Ajout {MAX_HISTORY + 49}"


# ---------------------------------------------------------------------------
# Sauvegarde / état dirty
# ---------------------------------------------------------------------------


def test_mark_unsaved_keeps_restored_state_dirty_without_undo_step():
    history = ProjectHistory()
    project = _make_project()
    history.reset(project)
    history.mark_unsaved()

    assert history.is_dirty is True
    assert history.can_undo is False

    history.mark_saved()
    assert history.is_dirty is False


def test_mark_saved_clears_dirty_flag():
    history = ProjectHistory()
    project = _make_project()
    history.reset(project)

    _add_clip(project, "c1")
    history.record(project, "Ajout")

    assert history.is_dirty is True
    history.mark_saved()
    assert history.is_dirty is False

    _add_clip(project, "c2")
    history.record(project, "Ajout 2")
    assert history.is_dirty is True


def test_undo_returns_to_saved_state():
    history = ProjectHistory()
    project = _make_project()
    history.reset(project)
    history.mark_saved()

    _add_clip(project, "c1")
    history.record(project, "Ajout")
    assert history.is_dirty

    history.undo()
    assert history.is_dirty is False


def test_redo_after_undo_makes_dirty_again():
    history = ProjectHistory()
    project = _make_project()
    history.reset(project)
    history.mark_saved()

    _add_clip(project, "c1")
    history.record(project, "Ajout")
    history.undo()
    assert history.is_dirty is False

    history.redo()
    assert history.is_dirty is True


# ---------------------------------------------------------------------------
# Labels
# ---------------------------------------------------------------------------


def test_undo_redo_labels_are_informative():
    history = ProjectHistory()
    project = _make_project()
    history.reset(project)

    _add_clip(project, "c1")
    history.record(project, "Déplacer le clip")
    assert history.undo_label == "Déplacer le clip"
    assert history.redo_label is None

    history.undo()
    assert history.undo_label is None
    assert history.redo_label == "Déplacer le clip"


def test_record_rejects_none_project():
    history = ProjectHistory()
    project = _make_project()
    history.reset(project)
    with pytest.raises(ValueError):
        history.record(None, "x")