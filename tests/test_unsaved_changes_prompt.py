"""Fermer, créer ou ouvrir un projet modifié propose d'enregistrer ; la fermeture ne s'interrompt jamais.

Régression : ces trois actions écrasaient le travail sans rien demander (l'autosave ignore les projets
sans chemin). Le ``conftest`` neutralise la boîte par défaut (elle est modale) ; ces tests la réactivent.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QFileDialog, QMessageBox

from core.project_io import load_project
from ui.main_window_mixins.project_files import ProjectFilesMixin

_REAL_CONFIRM = ProjectFilesMixin._confirm_discard_changes


@pytest.fixture
def window(qtbot, monkeypatch):
    monkeypatch.setattr(ProjectFilesMixin, "_confirm_discard_changes", _REAL_CONFIRM)
    from ui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    window.timeline_timer.stop()
    return window


def _answer(monkeypatch, button):
    asked = []

    def question(*args, **kwargs):
        asked.append(args[2] if len(args) > 2 else "")
        return button

    monkeypatch.setattr(QMessageBox, "question", question)
    return asked


def _forbid_question(monkeypatch):
    def question(*_args, **_kwargs):
        raise AssertionError("aucune question ne doit être posée pour un projet enregistré")

    monkeypatch.setattr(QMessageBox, "question", question)


def test_a_saved_project_is_replaced_without_asking(window, monkeypatch):
    _forbid_question(monkeypatch)
    before = window.project
    window.new_project()
    assert window.project is not before


def test_cancel_keeps_the_modified_project_for_new_open_and_close(window, monkeypatch, tmp_path):
    window.project_dirty = True
    asked = _answer(monkeypatch, QMessageBox.Cancel)
    before = window.project
    window.new_project()
    window._load_project_from_path(str(tmp_path / "other.kut"))
    assert window.project is before
    assert window.close() is False                      # fermeture refusée : la fenêtre reste ouverte
    assert len(asked) == 3 and window.project_dirty


def test_the_question_names_the_project(window, monkeypatch):
    window.project_dirty = True
    window.project.name = "Mon film"
    asked = _answer(monkeypatch, QMessageBox.Cancel)
    window.new_project()
    assert "Mon film" in asked[0]


def test_discard_replaces_the_project(window, monkeypatch):
    window.project_dirty = True
    _answer(monkeypatch, QMessageBox.Discard)
    before = window.project
    window.new_project()
    assert window.project is not before and not window.project_dirty


def test_save_writes_the_file_then_continues(window, monkeypatch, tmp_path):
    path = tmp_path / "film.kut"
    window.current_project_path = str(path)
    window.project.name = "Film"
    window.project_dirty = True
    _answer(monkeypatch, QMessageBox.Save)
    before = window.project
    window.new_project()
    assert load_project(str(path)).name == "Film"
    assert window.project is not before


def test_save_cancelled_in_the_file_dialog_keeps_everything(window, monkeypatch):
    """« Enregistrer » sans chemin ouvre un dialogue : l'annuler ne doit rien faire perdre."""
    window.project_dirty = True
    _answer(monkeypatch, QMessageBox.Save)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_a, **_k: ("", ""))
    before = window.project
    window.new_project()
    assert window.project is before and window.project_dirty


def test_a_failing_shutdown_step_does_not_stop_the_following_ones(window, monkeypatch):
    """Si la file de rendu lève à la fermeture, FFmpeg, l'autosave et le runtime doivent quand même s'arrêter."""
    reached = []

    def boom():
        raise RuntimeError("file de rendu en panne")

    monkeypatch.setattr(window.render_queue, "shutdown", boom)
    monkeypatch.setattr(window.runtime, "shutdown", lambda: reached.append("runtime"))
    monkeypatch.setattr(window, "_shutdown_proxies", lambda: reached.append("proxies"))
    monkeypatch.setattr(window, "_cancel_tracking_jobs", lambda: reached.append("tracking"))
    assert window.close() is True
    assert {"runtime", "proxies", "tracking"} <= set(reached)
