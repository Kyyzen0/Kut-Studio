"""Tests d'intégration UI ↔ Project (Qt offscreen).

Ces tests vérifient que ``MainWindow`` utilise bien ``Project`` comme
source de vérité pour la timeline, et que les opérations UI (couper,
supprimer, éditer un sous-titre) sont propagées au modèle métier.
"""

import pathlib

import pytest

from core.project_model import Project
from core.timeline_operations import find_clip
from core.timeline_view_model import build_clip_views


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_window(qtbot, monkeypatch):
    """Construit une MainWindow configurée pour les tests offscreen."""
    from ui.main_window import MainWindow

    # On évite que le menu "Save" ne tente quoi que ce soit en cas de notif.
    monkeypatch.setattr("ui.main_window.QMessageBox.information", lambda *_, **__: None)
    window = MainWindow()
    qtbot.addWidget(window)
    return window


# ---------------------------------------------------------------------------
# Project comme source de vérité
# ---------------------------------------------------------------------------


def test_main_window_owns_a_real_project(qtbot, monkeypatch) -> None:
    """MainWindow expose un vrai Project métier au démarrage."""
    window = _build_window(qtbot, monkeypatch)

    assert isinstance(window.project, Project)
    assert window.project.name == "Projet sans titre"


def test_main_window_timeline_reflects_project(qtbot, monkeypatch) -> None:
    """La timeline de MainWindow est une projection de self.project."""
    window = _build_window(qtbot, monkeypatch)

    assert window.timeline_panel.project is window.project
    expected_views = build_clip_views(window.project)
    rendered_ids = {v.id for v in window.timeline_panel.clip_views}
    expected_ids = {v.id for v in expected_views}
    assert rendered_ids == expected_ids
    # Au moins les clips de démo sont présents.
    assert {"intro", "plan_a", "b_roll", "subtitle_01"}.issubset(rendered_ids)


# ---------------------------------------------------------------------------
# Opérations métier déclenchées par MainWindow
# ---------------------------------------------------------------------------


def test_main_window_cut_modifies_project_and_refreshes(qtbot, monkeypatch) -> None:
    """Une coupe via MainWindow modifie self.project et la timeline se reconstruit."""
    window = _build_window(qtbot, monkeypatch)

    initial_clip_count = sum(len(t.clips) for t in window.project.tracks)
    intro = find_clip(window.project, "intro")
    cut_at = intro.timeline_start + intro.duration / 2

    window.cut_selected_clip("intro", cut_at)

    new_clip_count = sum(len(t.clips) for t in window.project.tracks)
    assert new_clip_count == initial_clip_count + 1

    # La projection est rafraîchie : on doit voir l'id généré "-split-2".
    rendered_ids = {v.id for v in window.timeline_panel.clip_views}
    assert "intro-split-2" in rendered_ids


def test_main_window_delete_modifies_project(qtbot, monkeypatch) -> None:
    """Une suppression via MainWindow retire le clip du Project."""
    window = _build_window(qtbot, monkeypatch)

    window.delete_selected_clip("b_roll")

    with pytest.raises(KeyError):
        find_clip(window.project, "b_roll")
    rendered_ids = {v.id for v in window.timeline_panel.clip_views}
    assert "b_roll" not in rendered_ids


def test_main_window_subtitle_editor_updates_project(qtbot, monkeypatch) -> None:
    """Éditer le sous-titre dans l'inspecteur met à jour Clip.text."""
    window = _build_window(qtbot, monkeypatch)

    # Sélectionner le clip sous-titre : on simule le clic en passant l'ID.
    window.on_clip_selected("subtitle_01")

    new_text = "Bienvenue dans la nouvelle version"
    window.properties_panel.subtitle_editor.setPlainText(new_text)

    # Clip.text dans le Project est mis à jour.
    updated = find_clip(window.project, "subtitle_01")
    assert updated.text == new_text

    # La projection reflète aussi la modification.
    view = window.timeline_panel.find_view_by_id("subtitle_01")
    assert view is not None
    assert view.text == new_text


# ---------------------------------------------------------------------------
# Aucune dépendance à l'ancien module timeline_model dans l'UI
# ---------------------------------------------------------------------------


def test_ui_does_not_import_legacy_timeline_model() -> None:
    """L'interface ne référence plus ``core.timeline_model`` (provisoirement gardé pour ses tests)."""
    ui_dir = pathlib.Path(__file__).resolve().parent.parent / "ui"
    offenders: list[str] = []
    for py_file in sorted(ui_dir.glob("*.py")):
        content = py_file.read_text(encoding="utf-8")
        if "timeline_model" in content:
            offenders.append(py_file.name)
    assert not offenders, (
        "Les fichiers ui/ suivants importent encore timeline_model : "
        f"{offenders}"
    )
