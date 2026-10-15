"""D3 : supprimer des clips nettoie le moniteur (sous-titre, titre ou graphique du clip supprimé)."""

import pytest
from PySide6.QtWidgets import QApplication

from tools import ui_audit as audit
from ui import i18n


@pytest.fixture
def window(tmp_path, monkeypatch):
    from ui.main_window_mixins.project_files import ProjectFilesMixin

    monkeypatch.setenv("KUT_STUDIO_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("KUT_STUDIO_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("KUT_STUDIO_PROXY_DIR", str(tmp_path / "proxies"))
    monkeypatch.setenv("KUT_STUDIO_HARDWARE_ENCODING", "off")
    monkeypatch.setattr(ProjectFilesMixin, "_confirm_discard_changes", lambda self: True)
    main = audit.make_main_window(1280, 720, scopes=False, rich=False)   # projet de démonstration : titre « Bienvenue… »
    i18n.set_language("fr")
    yield main
    main.close()


def _all_clip_ids(window) -> list[str]:
    return [clip.id for track in window.project.tracks for clip in track.clips]


def test_deleting_the_displayed_subtitle_hides_it_from_the_monitor(window) -> None:
    window.seek_to_position(2.0)
    assert window.preview_panel.preview_subtitle_overlay.isVisible()
    window.timeline_panel.selected_clip_id = "subtitle_01"
    window.delete_selected_clip("subtitle_01")
    QApplication.processEvents()
    assert not window.preview_panel.preview_subtitle_overlay.isVisible()


@pytest.mark.parametrize("ripple", [False, True])
def test_deleting_every_clip_leaves_a_clean_empty_monitor(window, ripple) -> None:
    window.seek_to_position(2.0)
    window.timeline_panel.ripple_enabled = ripple
    window.timeline_panel.selected_clip_ids = set(_all_clip_ids(window))
    window.timeline_panel.selected_clip_id = _all_clip_ids(window)[0]
    window.delete_selected_clip_with_check()
    QApplication.processEvents()
    assert _all_clip_ids(window) == []
    assert not window.preview_panel.preview_subtitle_overlay.isVisible()
    assert window.preview_panel.empty_state.isVisible()


def test_ripple_delete_of_the_displayed_subtitle_hides_it(window) -> None:
    window.seek_to_position(2.0)
    window.timeline_panel.selected_clip_id = "subtitle_01"
    window.ripple_delete_selected_clip()
    QApplication.processEvents()
    assert not window.preview_panel.preview_subtitle_overlay.isVisible()
