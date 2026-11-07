"""Interface : couper ou supprimer la source d'un suivi ne réécrit jamais un suiveur verrouillé (même règle que le modèle et les scripts).

Chaque entrée de la fenêtre (coupe à la tête de lecture, suppression simple, ripple, sélection multiple) est refusée avec un message
dans la barre d'état, sans écrire dans le projet ni dans l'historique ; avec un suiveur libre, l'opération a lieu et s'annule / se rétablit.
"""

from __future__ import annotations

import copy
from dataclasses import replace

import pytest
from test_scopes import _window

from core.graphics import GraphicOverlay, GraphicType
from core.project_model import Clip, MediaAsset, Project, Track
from core.tracking_model import ClipTracking, TrackLink


def _project(lock: str | None, *, continuation: tuple[str, ...] = ("b",)) -> Project:
    follower = Clip("f", "m", "V2", 0.0, 0.0, 2.0)
    follower.tracking = ClipTracking(links=(TrackLink(source_clip_id="a", tracker_ids=("t1",), continuation_ids=continuation),))
    if lock == "layer":
        follower.graphic = replace(GraphicOverlay(type=GraphicType.TEXT), locked=True)
    return Project(
        "Démo", width=320, height=180, fps=30.0,
        media_assets=[MediaAsset("m", "/x/a.mp4", "a", 60.0, 320, 180, 30.0, "video", has_audio=True)],
        tracks=[
            Track("V1", "V1", "video", clips=[Clip("a", "m", "V1", 0.0, 0.0, 4.0), Clip("b", "m", "V1", 4.0, 0.0, 3.0)]),
            Track("V2", "V2", "video", locked=(lock == "track"), clips=[follower]),
        ],
    )


def _open(qtbot, monkeypatch, lock, **link):
    window = _window(qtbot, monkeypatch)
    window.project = _project(lock, **link)
    window.history.reset(window.project)
    window.timeline_panel.set_project(window.project)
    window.statusBar().clearMessage()
    return window


def _refused(window, before) -> None:
    assert "verrouillé" in window.statusBar().currentMessage()
    assert window.project == before and not window.history.can_undo


def _cut(window):
    window.cut_selected_clip("a", 2.0)


def _delete(window):
    window.delete_selected_clip("a")


def _ripple(window):
    window.timeline_panel.selected_clip_id = "a"
    window.ripple_delete_selected_clip()


def _selection(window):
    window.timeline_panel.selected_clip_ids = {"a", "b"}
    window.timeline_panel.selected_clip_id = "a"
    window.delete_selected_clip_with_check()


def _selection_ripple(window):
    window.timeline_panel.ripple_enabled = True
    _selection(window)


ENTRIES = [_cut, _delete, _ripple, _selection, _selection_ripple]


@pytest.mark.parametrize("lock", ["layer", "track"])
@pytest.mark.parametrize("entry", ENTRIES, ids=lambda f: f.__name__)
def test_every_window_entry_refuses_a_locked_follower_before_writing(qtbot, monkeypatch, lock, entry):
    window = _open(qtbot, monkeypatch, lock)
    before = copy.deepcopy(window.project)
    entry(window)
    _refused(window, before)


@pytest.mark.parametrize("entry", ENTRIES, ids=lambda f: f.__name__)
def test_a_free_follower_lets_the_window_do_it_and_undo_redo_restore_it(qtbot, monkeypatch, entry):
    window = _open(qtbot, monkeypatch, None)
    before = copy.deepcopy(window.project.tracks)
    entry(window)
    after = copy.deepcopy(window.project.tracks)
    assert after != before and window.history.can_undo
    window.undo_last()
    assert window.project.tracks == before
    window.redo_last()
    assert window.project.tracks == after


def test_a_locked_follower_of_another_source_does_not_block_the_cut(qtbot, monkeypatch):
    window = _open(qtbot, monkeypatch, "layer", continuation=())
    window.cut_selected_clip("b", 5.0)                                  # « f » suit « a » : la coupe de « b » ne le réécrit pas
    assert len(window.project.tracks[0].clips) == 3 and window.history.can_undo
