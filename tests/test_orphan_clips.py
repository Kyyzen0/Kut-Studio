"""Un clip dont le média a été supprimé est « hors ligne » : il ne montre rien et ne fait rien planter.

Régression : supprimer un média de la bibliothèque conserve ses clips (volontairement), mais le seek, l'aperçu,
l'annulation, le plan d'export et la RÉOUVERTURE du projet enregistré levaient KeyError, la réouverture laissant
la fenêtre à moitié mise à jour.
"""

from __future__ import annotations

from rich_project import build_rich_project
from test_scopes import _window

import pytest

from core.export_engine import ExportEngine, ExportFormat, ExportPreset, ExportRequest
from core.project_io import load_project, save_project
from core.render_plan import build_render_plan


def _project_with_an_orphan_clip():
    project = build_rich_project()
    orphan = project.tracks[0].clips[0]                       # clip vidéo à 0 s, sur « av1 »
    project.media_assets = [asset for asset in project.media_assets if asset.id != orphan.asset_id]
    return project, orphan


def test_the_plan_renders_a_clip_without_media_as_empty_and_says_so():
    project, orphan = _project_with_an_orphan_clip()
    plan = build_render_plan(project)
    assert all(layer.clip_id != orphan.id for layer in plan.video_layers)
    assert any(orphan.asset_id in warning for warning in plan.warnings)
    assert plan.video_layers, "les autres clips restent rendus"


def test_the_window_keeps_working_with_an_orphan_clip(qtbot, monkeypatch):
    window = _window(qtbot, monkeypatch)
    project, orphan = _project_with_an_orphan_clip()
    window.project = project
    window.timeline_panel.set_project(project)
    window.seek_to_position(orphan.timeline_start + 0.5)       # ces appels levaient KeyError
    window._sync_preview_to_timeline()
    plan = window.get_render_plan()
    assert all(layer.clip_id != orphan.id for layer in plan.video_layers)


def test_a_saved_project_with_an_orphan_clip_reopens_completely(qtbot, monkeypatch, tmp_path):
    window = _window(qtbot, monkeypatch)
    project, orphan = _project_with_an_orphan_clip()
    path = tmp_path / "orphan.kut"
    save_project(project, str(path))
    assert load_project(str(path)).tracks[0].clips[0].id == orphan.id
    window._load_project_from_path(str(path))
    assert window.project.tracks[0].clips[0].id == orphan.id
    assert window.current_project_path == str(path)


def test_an_export_refuses_a_plan_with_missing_media_with_a_clear_message(tmp_path):
    """À l'écran le clip est vide ; dans un fichier livré ce serait un trou muet : l'export refuse."""
    project, orphan = _project_with_an_orphan_clip()
    request = ExportRequest(render_plan=build_render_plan(project), output_path=str(tmp_path / "out.mp4"),
                            format=ExportFormat.MP4_H264, preset=ExportPreset("T", (320, 180), 28, "64k"), fps=25)
    with pytest.raises(ValueError, match=f"Média introuvable.*{orphan.asset_id}"):
        ExportEngine()._build_command(request)
