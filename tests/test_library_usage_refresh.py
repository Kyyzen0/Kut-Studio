"""D2 : le badge « inutilisé » de la bibliothèque suit les clips posés, dupliqués, supprimés ou annulés."""

import pytest

from core.project_model import MediaAsset
from tools import ui_audit as audit


@pytest.fixture(scope="module")
def window(tmp_path_factory):
    from ui.main_window_mixins.project_files import ProjectFilesMixin

    patch = pytest.MonkeyPatch()
    base = tmp_path_factory.mktemp("library_usage")
    patch.setenv("KUT_STUDIO_CONFIG_DIR", str(base / "config"))
    patch.setenv("KUT_STUDIO_CACHE_DIR", str(base / "cache"))
    patch.setenv("KUT_STUDIO_PROXY_DIR", str(base / "proxies"))
    patch.setenv("KUT_STUDIO_HARDWARE_ENCODING", "off")
    patch.setattr(ProjectFilesMixin, "_confirm_discard_changes", lambda self: True)
    main = audit.make_main_window(1280, 720, scopes=False)
    yield main
    main.close()
    patch.undo()


def _count(window, asset_id: str) -> int:
    return window.project_panel._badges[asset_id].usage_count


def _settle(qtbot) -> None:
    qtbot.wait(250)           # le rafraîchissement est regroupé (100 ms)


def _fresh_asset(window, asset_id: str) -> None:
    window.project.media_assets.append(MediaAsset(asset_id, f"/nonexistent/{asset_id}.mp4", asset_id, 5.0, 1920, 1080, 30.0, "video", True))
    window._refresh_project_library()
    assert _count(window, asset_id) == 0


def test_adding_a_clip_with_the_add_button_clears_the_unused_badge(window, qtbot) -> None:
    _fresh_asset(window, "added")
    window.add_asset_to_timeline("added")
    _settle(qtbot)
    assert _count(window, "added") == 1


def test_duplicate_delete_and_undo_keep_the_badge_exact(window, qtbot) -> None:
    _fresh_asset(window, "cycle")
    window.add_asset_to_timeline("cycle")
    _settle(qtbot)
    window.duplicate_selected_clip()
    _settle(qtbot)
    assert _count(window, "cycle") == 2
    window.delete_selected_clip(window.timeline_panel.selected_clip_id)
    _settle(qtbot)
    assert _count(window, "cycle") == 1
    window.undo_last()
    _settle(qtbot)
    assert _count(window, "cycle") == 2


def test_editing_without_changing_usage_does_not_rebuild_the_library(window, qtbot, monkeypatch) -> None:
    _fresh_asset(window, "quiet")
    calls = []
    original = window.project_panel.set_usage_for_assets
    monkeypatch.setattr(window.project_panel, "set_usage_for_assets", lambda: (calls.append(1), original())[1])
    window._mark_dirty()
    window._mark_dirty()
    _settle(qtbot)
    assert calls == []
