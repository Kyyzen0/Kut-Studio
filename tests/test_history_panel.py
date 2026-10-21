"""Panneau Historique : les étapes nommées, et revenir à l'une d'elles en une fois."""

from __future__ import annotations

import pytest

import core.edit_history as edit_history
from core.edit_history import ProjectHistory
from core.project_model import Clip, MediaAsset, Project, Track
from core.workspace_state import DEFAULT_HIDDEN, PanelId, WorkspaceState


def _project() -> Project:
    return Project(name="H", tracks=[Track(id="V1", name="V1", type="video")],
                   media_assets=[MediaAsset("a", "/tmp/v.mp4", "V", 10.0, 1920, 1080, 30.0, "video")])


def _history_with_steps(count: int = 4) -> tuple[ProjectHistory, Project]:
    history = ProjectHistory()
    project = _project()
    history.reset(project)
    for index in range(count):
        project.tracks[0].clips.append(Clip(f"c{index}", "a", "V1", float(index), 0.0, 1.0))
        history.record(project, f"Ajouter c{index}")
    return history, project


def _clips(project: Project) -> list[str]:
    return [clip.id for clip in project.tracks[0].clips]


def test_entries_list_every_state_from_the_initial_one():
    history, _project = _history_with_steps(3)
    history.mark_saved()
    history.undo()
    entries = history.entries()
    assert [entry.label for entry in entries] == ["", "Ajouter c0", "Ajouter c1", "Ajouter c2"]
    assert [entry.current for entry in entries] == [False, False, True, False]
    assert [entry.undone for entry in entries] == [False, False, False, True]
    assert [entry.saved for entry in entries] == [False, False, False, True], "l'état enregistré, même annulé"


def test_going_back_several_steps_is_a_single_copy_with_the_redo_branch_kept(monkeypatch):
    history, _project = _history_with_steps(4)
    copies: list[object] = []
    original = edit_history._deepcopy_project
    monkeypatch.setattr(edit_history, "_deepcopy_project", lambda project: copies.append(1) or original(project))
    restored = history.go_to(1)
    assert restored is not None and _clips(restored) == ["c0"]
    assert len(copies) == 1, "vingt étapes ne copient pas vingt projets"
    assert history.undo_label == "Ajouter c0" and history.redo_label == "Ajouter c1"
    assert [entry.undone for entry in history.entries()] == [False, False, True, True, True]
    forward = history.go_to(3)                               # dans la branche annulée : autant de Rétablir
    assert forward is not None and _clips(forward) == ["c0", "c1", "c2"]
    assert history.redo_label == "Ajouter c3"


def test_going_to_a_state_gives_the_project_of_as_many_undos():
    jumped, _ = _history_with_steps(4)
    stepped, _ = _history_with_steps(4)
    for _step in range(3):
        expected = stepped.undo()
    assert expected is not None
    assert jumped.go_to(1) == expected


def test_the_current_state_or_an_unknown_one_changes_nothing():
    history, _project = _history_with_steps(2)
    assert history.go_to(2) is None
    assert history.go_to(9) is None and history.go_to(-1) is None
    assert history.undo_label == "Ajouter c1"


def test_an_edit_after_going_back_drops_the_undone_steps():
    history, _project = _history_with_steps(3)
    restored = history.go_to(0)
    assert restored is not None
    restored.tracks[0].clips.append(Clip("autre", "a", "V1", 5.0, 0.0, 1.0))
    history.record(restored, "Autre chemin")
    assert [entry.label for entry in history.entries()] == ["", "Autre chemin"]


def test_the_history_panel_is_hidden_until_opened_from_the_window_menu():
    assert PanelId.HISTORY in DEFAULT_HIDDEN
    state = WorkspaceState.from_dict({"panels": [{"panel": "mixer", "visible": True}]})
    history = next(panel for panel in state.panels if panel.panel is PanelId.HISTORY)
    assert not history.visible, "un espace de travail enregistré avant ce panneau le garde fermé"


@pytest.fixture
def window(qtbot, monkeypatch, tmp_path):
    from main_window_harness import build_window, install_dialogs

    install_dialogs(monkeypatch)
    return build_window(qtbot, monkeypatch, tmp_path / "config")


def test_clicking_a_step_in_the_panel_brings_the_montage_back_to_it(window):
    from PySide6.QtCore import Qt

    clips = window.project.tracks[0].clips
    assert clips, "le projet d'exemple a des clips"
    before = len(clips)
    for _step in range(2):
        clips.pop()
        window._record_history("Supprimer un clip")
        window._reload_timeline_preserving_selection()
    panel = window.history_panel
    assert panel.list.count() == 0, "panneau fermé : rien n'est reconstruit à chaque modification"
    window.show()                                        # (le harnais ne montre pas la fenêtre)
    window.toggle_panel(PanelId.HISTORY)                 # Fenêtre › Panneaux › Historique
    assert panel.isVisible() and panel.list.count() == 3
    assert panel.list.currentItem().font().bold()
    first = panel.list.item(0)
    panel.list.itemClicked.emit(first)
    assert len(window.project.tracks[0].clips) == before
    assert window.history.can_redo and not window.history.can_undo
    assert panel.list.currentRow() == 0
    assert panel.list.item(2).font().italic(), "les étapes annulées restent, atténuées"
    assert window.undo_button.isEnabled() is False and window.redo_button.isEnabled()
    panel.list.itemActivated.emit(panel.list.item(2))   # clavier (Entrée) : même effet
    assert len(window.project.tracks[0].clips) == before - 2
    assert panel.list.item(0).data(Qt.DisplayRole)
