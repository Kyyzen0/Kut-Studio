"""Couche de performance dans l'interface : proxies, préférences, aperçu, qualité adaptative."""

from __future__ import annotations

import functools
import json
import os
import sys
import time
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMenu, QMessageBox

from core.cache_keys import SignatureMemo
from core.project_model import Clip, MediaAsset, Project, Track
from core.proxy_manager import ProxyManager, ProxyState, RunResult
from core.user_settings import load_user_settings, settings_file_path
from ui import i18n
from ui.library_organization_widgets import AssetContextMenuBuilder
from ui.preferences_dialog import PreferencesDialog

ROOT = Path(__file__).resolve().parent.parent
FAKE_FFMPEG = ROOT / "tests" / "fixtures" / "fake_ffmpeg.py"
TIMEOUT = 8000


# --- helpers ---------------------------------------------------------------------------------


def _fake_runner(command, cancel, on_progress, on_start):
    on_start(1)
    on_progress(1.0)
    Path(command[-1]).write_bytes(b"P" * 32)
    return RunResult(0)


def _project(tmp_path: Path, videos: int = 2, *, with_files: bool = True) -> Project:
    assets, clips = [], []
    for index in range(videos):
        path = tmp_path / "media" / f"clip{index}.mp4"
        path.parent.mkdir(exist_ok=True)
        if with_files:
            path.write_bytes(b"V" * 100)
        assets.append(MediaAsset(id=f"v{index}", path=str(path), name=f"clip{index}", duration=4.0,
                                 width=1920, height=1080, fps=25.0, media_type="video", has_audio=True))
        clips.append(Clip(id=f"c{index}", asset_id=f"v{index}", track_id="V1",
                          timeline_start=index * 4.0, source_in=0.0, source_out=4.0))
    audio = tmp_path / "media" / "song.wav"
    audio.write_bytes(b"A" * 10)
    assets.append(MediaAsset(id="song", path=str(audio), name="song", duration=10.0, width=0,
                             height=0, fps=0.0, media_type="audio", has_audio=True))
    return Project(name="perf", width=1280, height=720, fps=25.0, media_assets=assets,
                   tracks=[Track(id="V1", name="V1", type="video", clips=clips)])


@pytest.fixture
def fake_proxies(monkeypatch):
    """Tous les gestionnaires de proxies de la fenêtre utilisent un faux FFmpeg instantané."""
    monkeypatch.setattr(
        "ui.main_window_mixins.performance.ProxyManager",
        functools.partial(ProxyManager, runner=_fake_runner, ffmpeg_command=lambda: ["fake"],
                          memo=SignatureMemo(ttl=0.0), disk_ttl=0.0),
    )
    monkeypatch.setattr("core.tool_paths.find_media_tool", lambda name: "/usr/bin/" + name)


def _window(qtbot, monkeypatch, tmp_path, *, project=True, answer=QMessageBox.Yes):
    from ui.main_window import MainWindow

    calls = {"warning": [], "question": []}
    monkeypatch.setattr("ui.main_window.QMessageBox.information", lambda *_a, **_k: None)
    monkeypatch.setattr("ui.main_window.QMessageBox.critical", lambda *_a, **_k: None)
    monkeypatch.setattr("ui.main_window.QMessageBox.warning",
                        lambda *a, **_k: calls["warning"].append(a[2]))
    monkeypatch.setattr("ui.main_window.QMessageBox.question",
                        lambda *a, **_k: calls["question"].append(a[2]) or answer)
    window = MainWindow()
    qtbot.addWidget(window)
    window.timeline_timer.stop()
    if project:
        window.project = _project(tmp_path)
        window.timeline_panel.set_project(window.project)
        window._refresh_project_library()
    window._calls = calls
    return window


def _wait_state(qtbot, window, asset_index, state, profile=None):
    asset = window.project.media_assets[asset_index]
    qtbot.waitUntil(lambda: window.proxies.info(asset.path, profile).state is state, timeout=TIMEOUT)
    return window.proxies.info(asset.path, profile)


def _pid_alive(pid):
    if sys.platform == "win32":  # pragma: no cover
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


# --- réglages ---------------------------------------------------------------------------------------


def test_performance_settings_have_defaults_and_roundtrip(tmp_path):
    from core.user_settings import UserSettings, save_user_settings

    settings = load_user_settings(tmp_path)
    assert settings.proxies_enabled is True and settings.proxy_profile == "medium"
    assert settings.cache_max_gb == 4.0
    save_user_settings(UserSettings(proxies_enabled=False, proxy_profile="high", cache_max_gb=12.5), tmp_path)
    again = load_user_settings(tmp_path)
    assert (again.proxies_enabled, again.proxy_profile, again.cache_max_gb) == (False, "high", 12.5)


def test_old_preferences_files_and_corrupt_values_fall_back_to_safe_defaults(tmp_path):
    path = settings_file_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"theme_mode": "light"}), encoding="utf-8")   # écrit avant les proxies
    settings = load_user_settings(tmp_path)
    assert settings.theme_mode == "light" and settings.proxies_enabled and settings.proxy_profile == "medium"
    path.write_text(json.dumps({"proxy_profile": "inconnu", "cache_max_gb": "beaucoup",
                                "proxies_enabled": "yes"}), encoding="utf-8")
    settings = load_user_settings(tmp_path)
    assert settings.proxy_profile == "medium" and settings.cache_max_gb == 4.0 and settings.proxies_enabled
    path.write_text(json.dumps({"cache_max_gb": 10 ** 9}), encoding="utf-8")
    assert load_user_settings(tmp_path).cache_max_gb == 512.0
    path.write_text(json.dumps({"cache_max_gb": -3}), encoding="utf-8")
    assert load_user_settings(tmp_path).cache_max_gb == 0.5


# --- fenêtre : génération, badges, aperçu --------------------------------------------------------------


def test_window_builds_the_managers_from_the_saved_settings(qtbot, monkeypatch, tmp_path, fake_proxies):
    from core.user_settings import UserSettings, save_user_settings

    save_user_settings(UserSettings(proxies_enabled=False, proxy_profile="high", cache_max_gb=2.0))
    window = _window(qtbot, monkeypatch, tmp_path, project=False)
    assert window.proxies.enabled is False and window.proxies.profile.id == "high"
    assert window.cache_manager.max_bytes == 2 * 1024 ** 3
    assert window.proxies_action.isChecked() is False
    snapshot = window._settings_snapshot()
    assert (snapshot.proxies_enabled, snapshot.proxy_profile, snapshot.cache_max_gb) == (False, "high", 2.0)


def test_generating_a_proxy_updates_the_badge_and_leaves_the_project_untouched(
    qtbot, monkeypatch, tmp_path, fake_proxies
):
    window = _window(qtbot, monkeypatch, tmp_path)
    before = json.dumps(__import__("core.project_io", fromlist=["x"]).project_payload(window.project))
    assert window.generate_proxy_for_asset("v0") == 1
    info = _wait_state(qtbot, window, 0, ProxyState.READY)
    qtbot.waitUntil(lambda: window.project_panel._badges["v0"].proxy_state == "ready", timeout=TIMEOUT)
    assert window.project_panel._badges["v0"].proxy_progress == 100
    assert window.project_panel._badges["song"].proxy_state == ""          # l'audio n'a pas de proxy
    assert Path(info.proxy_path).is_file()
    after = json.dumps(__import__("core.project_io", fromlist=["x"]).project_payload(window.project))
    assert after == before                                                 # rien n'est écrit dans le projet


def test_generating_for_the_whole_project_skips_audio_and_missing_files(qtbot, monkeypatch, tmp_path, fake_proxies):
    window = _window(qtbot, monkeypatch, tmp_path)
    Path(window.project.media_assets[1].path).unlink()                    # un média hors ligne
    assert window.generate_proxies_for_project() == 1
    _wait_state(qtbot, window, 0, ProxyState.READY)
    assert window.proxies.info(window.project.media_assets[1].path).state is ProxyState.NONE
    assert window.generate_proxy_for_asset("inconnu") == 0 and window.generate_proxy_for_asset("song") == 0


def test_generating_for_the_selection_uses_the_selected_clips_only(qtbot, monkeypatch, tmp_path, fake_proxies):
    window = _window(qtbot, monkeypatch, tmp_path)
    window.timeline_panel.select_clip("c1")
    assert window.generate_proxies_for_selection() == 1
    _wait_state(qtbot, window, 1, ProxyState.READY)
    assert window.proxies.info(window.project.media_assets[0].path).state is ProxyState.NONE
    window.timeline_panel._set_selection([], None, announce=False)
    assert window.generate_proxies_for_selection() == 0


def test_missing_ffmpeg_warns_instead_of_failing(qtbot, monkeypatch, tmp_path, fake_proxies):
    window = _window(qtbot, monkeypatch, tmp_path)
    monkeypatch.setattr("core.tool_paths.find_media_tool", lambda name: None)
    assert window.generate_proxies_for_project() == 0
    assert window._calls["warning"] and "FFmpeg" in window._calls["warning"][0]
    assert all(window.proxies.info(a.path).state is ProxyState.NONE for a in window.project.media_assets)


def test_preview_switches_to_the_proxy_and_back_to_the_original(qtbot, monkeypatch, tmp_path, fake_proxies):
    window = _window(qtbot, monkeypatch, tmp_path)
    original = window.project.media_assets[0].path
    window.seek_to_position(1.0)
    assert window.preview_panel._timeline_preview_path == original          # pas encore de proxy
    window.generate_proxy_for_asset("v0")
    info = _wait_state(qtbot, window, 0, ProxyState.READY)
    qtbot.waitUntil(lambda: window.preview_panel._timeline_preview_path == info.proxy_path, timeout=TIMEOUT)
    Path(info.proxy_path).unlink()                                           # supprimé à la main
    window.seek_to_position(1.2)
    assert window.preview_panel._timeline_preview_path == original           # retour propre à l'original
    assert Path(original).is_file()


def test_the_global_switch_controls_the_preview_and_is_saved(qtbot, monkeypatch, tmp_path, fake_proxies):
    window = _window(qtbot, monkeypatch, tmp_path)
    window.generate_proxy_for_asset("v0")
    info = _wait_state(qtbot, window, 0, ProxyState.READY)
    window.seek_to_position(1.0)
    assert window.preview_panel._timeline_preview_path == info.proxy_path
    window.proxies_action.setChecked(False)                                  # bascule rapide du menu
    assert window.proxies.enabled is False
    assert window.preview_panel._timeline_preview_path == window.project.media_assets[0].path
    assert load_user_settings().proxies_enabled is False
    window.set_proxies_enabled(True)
    assert window.proxies_action.isChecked() and load_user_settings().proxies_enabled is True
    assert window.preview_panel._timeline_preview_path == info.proxy_path


def test_export_and_render_queue_always_use_the_original_media(qtbot, monkeypatch, tmp_path, fake_proxies):
    window = _window(qtbot, monkeypatch, tmp_path)
    window.generate_proxies_for_project()
    _wait_state(qtbot, window, 0, ProxyState.READY)
    _wait_state(qtbot, window, 1, ProxyState.READY)
    plan = window.get_render_plan()
    originals = {a.path for a in window.project.media_assets}
    assert {layer.source_path for layer in plan.video_layers} <= originals
    assert {layer.source_path for layer in plan.audio_layers} <= originals
    request = window.export_panel.build_request(plan, str(tmp_path / "out.mp4"))
    assert {layer.source_path for layer in request.render_plan.video_layers} <= originals
    # La file de rendu rend un instantané du projet : il ne contient que les originaux.
    from core.render_presets import default_preset

    job = window.render_queue.enqueue(window.project, default_preset(), str(tmp_path / "queued.mp4"))
    from core.project_io import load_project
    from core.render_plan import build_render_plan

    queued = build_render_plan(load_project(job.snapshot_path))
    assert {layer.source_path for layer in queued.video_layers} <= originals
    window.render_queue.shutdown()


def test_faithful_preview_segments_follow_the_proxy_and_never_mix_with_original_ones(
    qtbot, monkeypatch, tmp_path, fake_proxies
):
    window = _window(qtbot, monkeypatch, tmp_path)
    original_job = window._preview_segment_jobs(1.0)[0]
    assert original_job.plan.video_layers[0].source_path == window.project.media_assets[0].path
    window.generate_proxy_for_asset("v0")
    info = _wait_state(qtbot, window, 0, ProxyState.READY)
    proxy_job = window._preview_segment_jobs(1.0)[0]
    assert proxy_job.plan.video_layers[0].source_path == info.proxy_path
    assert proxy_job.key != original_job.key                                 # jamais de segment mélangé
    window.set_proxies_enabled(False)
    assert window._preview_segment_jobs(1.0)[0].key == original_job.key


def test_closing_during_a_generation_kills_ffmpeg_and_leaves_no_partial_file(qtbot, monkeypatch, tmp_path):
    monkeypatch.setenv("FAKE_FFMPEG_SECONDS", "60")
    pid_file = tmp_path / "pid.txt"
    monkeypatch.setenv("FAKE_FFMPEG_PID_FILE", str(pid_file))
    monkeypatch.setattr(
        "ui.main_window_mixins.performance.ProxyManager",
        functools.partial(ProxyManager, ffmpeg_command=lambda: [sys.executable, str(FAKE_FFMPEG)],
                          memo=SignatureMemo(ttl=0.0), disk_ttl=0.0),
    )
    monkeypatch.setattr("core.tool_paths.find_media_tool", lambda name: "/usr/bin/ffmpeg")
    window = _window(qtbot, monkeypatch, tmp_path)
    window.generate_proxy_for_asset("v0")
    qtbot.waitUntil(lambda: pid_file.exists() and bool(window.proxies.active_process_ids()), timeout=TIMEOUT)
    pid = int(pid_file.read_text())
    folder = Path(window.proxies.directory)
    assert window.close()
    assert not _pid_alive(pid)
    assert not [p for p in folder.glob("*") if ".partial." in p.name]


# --- bibliothèque : menu contextuel -------------------------------------------------------------------------


def _builder(state, progress=0):
    return AssetContextMenuBuilder(
        asset_id="v0", asset_name="clip", is_missing=False, usage_count=0, folders=[], tags=[],
        assigned_folder_id=None, assigned_tag_ids=set(), proxy_state=state, proxy_progress=progress,
    )


def _proxy_entries(builder, qtbot):
    menu = builder.build()
    qtbot.addWidget(menu)
    builder._keep_alive = menu            # le menu n'a pas de parent : on le garde vivant
    submenu = next(a.menu() for a in menu.actions() if a.menu() and a.text() == i18n.translate("proxy.menu.title"))
    return submenu, [a for a in submenu.actions() if a.text() and a.isEnabled()]


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("none", ["generate", "generate_project"]),
        ("error", ["generate", "generate_project"]),
        ("stale", ["generate", "delete", "generate_project"]),
        ("pending", ["cancel", "generate_project"]),
        ("generating", ["cancel", "generate_project"]),
        ("ready", ["regenerate", "delete", "generate_project"]),
    ],
)
def test_context_menu_offers_the_actions_that_fit_the_proxy_state(qtbot, state, expected):
    builder = _builder(state, 40)
    emitted = []
    builder.proxy_action_requested.connect(lambda asset, action: emitted.append((asset, action)))
    _submenu, entries = _proxy_entries(builder, qtbot)
    for entry in entries:
        entry.trigger()
    assert [action for _asset, action in emitted] == expected and {a for a, _ in emitted} == {"v0"}


def test_context_menu_shows_the_progress_and_hides_proxies_for_audio(qtbot):
    builder = _builder("generating", 42)
    submenu, _ = _proxy_entries(builder, qtbot)
    assert "42" in submenu.actions()[0].text()
    menu = _builder("").build()
    qtbot.addWidget(menu)
    assert not any(a.menu() and a.text() == i18n.translate("proxy.menu.title") for a in menu.actions())


def test_window_handles_every_context_menu_action(qtbot, monkeypatch, tmp_path, fake_proxies):
    window = _window(qtbot, monkeypatch, tmp_path)
    path = window.project.media_assets[0].path
    window._on_proxy_action_requested("v0", "generate")
    _wait_state(qtbot, window, 0, ProxyState.READY)
    window._on_proxy_action_requested("v0", "regenerate")
    _wait_state(qtbot, window, 0, ProxyState.READY)
    window._on_proxy_action_requested("v0", "delete")
    assert window.proxies.info(path).state is ProxyState.NONE
    window._on_proxy_action_requested("v1", "generate_project")
    _wait_state(qtbot, window, 1, ProxyState.READY)
    window._on_proxy_action_requested("v0", "cancel")                        # rien à annuler : sans effet
    window._on_proxy_action_requested("absent", "generate")


def test_badge_tooltip_explains_errors(qtbot, monkeypatch, tmp_path):
    def failing(command, cancel, on_progress, on_start):
        return RunResult(1, "No space left on device")

    monkeypatch.setattr(
        "ui.main_window_mixins.performance.ProxyManager",
        functools.partial(ProxyManager, runner=failing, ffmpeg_command=lambda: ["fake"],
                          memo=SignatureMemo(ttl=0.0), disk_ttl=0.0),
    )
    monkeypatch.setattr("core.tool_paths.find_media_tool", lambda name: "/usr/bin/ffmpeg")
    window = _window(qtbot, monkeypatch, tmp_path)
    window.generate_proxy_for_asset("v0")
    _wait_state(qtbot, window, 0, ProxyState.ERROR)
    qtbot.waitUntil(lambda: window.project_panel._badges["v0"].proxy_state == "error", timeout=TIMEOUT)
    badge = window.project_panel._badges["v0"]
    assert "Espace disque" in badge.proxy_error
    bin_widget = window.project_panel.bin_videos
    item = bin_widget.item(0)
    assert "Espace disque" in item.toolTip()


# --- préférences : onglet Performance ---------------------------------------------------------------------------


def _prefs(qtbot, window):
    dialog = PreferencesDialog(shortcut_manager=window.shortcuts, performance_host=window)
    qtbot.addWidget(dialog)
    dialog.show()
    return dialog, dialog.performance_tab


def test_preferences_has_a_performance_tab_with_the_current_state(qtbot, monkeypatch, tmp_path, fake_proxies):
    window = _window(qtbot, monkeypatch, tmp_path)
    dialog, tab = _prefs(qtbot, window)
    assert dialog.tabs.count() == 3 and dialog.tabs.tabText(2) == i18n.translate("perf.tab")
    assert tab.use_proxies.isChecked() and tab.profile_combo.currentData() == "medium"
    assert tab.max_spin.value() == pytest.approx(4.0)
    assert "Mo" in tab.usage_label.text() or "o" in tab.usage_label.text()
    plain = PreferencesDialog()
    qtbot.addWidget(plain)
    assert plain.tabs is None and plain.performance_tab is None            # le dialogue historique reste intact


def test_preference_controls_change_the_real_settings_immediately(qtbot, monkeypatch, tmp_path, fake_proxies):
    window = _window(qtbot, monkeypatch, tmp_path)
    _dialog, tab = _prefs(qtbot, window)
    tab.use_proxies.setChecked(False)
    assert window.proxies.enabled is False and load_user_settings().proxies_enabled is False
    assert window.proxies_action.isChecked() is False
    tab.profile_combo.setCurrentIndex(tab.profile_combo.findData("high"))
    assert window.proxies.profile.id == "high" and load_user_settings().proxy_profile == "high"
    tab.max_spin.setValue(7.5)
    tab.max_spin.editingFinished.emit()
    assert window.cache_manager.max_bytes == int(7.5 * 1024 ** 3)
    assert load_user_settings().cache_max_gb == 7.5
    tab.use_proxies.setChecked(True)
    assert window.proxies.enabled is True


def test_a_smaller_cache_budget_evicts_immediately(qtbot, monkeypatch, tmp_path, fake_proxies):
    window = _window(qtbot, monkeypatch, tmp_path)
    cache = window.preview_engine.cache
    for n in range(5):
        source = tmp_path / f"seg{n}.mp4"
        source.write_bytes(b"x" * 1_000_000)
        from core.preview_cache import PreviewSegmentKey

        cache.store(PreviewSegmentKey(f"c{n}", 0.0, 2.0, "standard", "h"), source)
    assert window.cache_manager.disk_bytes() >= 5_000_000
    window.cache_manager.set_max_bytes(1_500_000)
    assert window.cache_manager.disk_bytes() <= 2_000_000


def test_purge_buttons_ask_confirmation_then_free_the_space(qtbot, monkeypatch, tmp_path, fake_proxies):
    window = _window(qtbot, monkeypatch, tmp_path)
    window.generate_proxies_for_project()
    _wait_state(qtbot, window, 0, ProxyState.READY)
    _wait_state(qtbot, window, 1, ProxyState.READY)
    _dialog, tab = _prefs(qtbot, window)
    window._calls["question"].clear()
    answers = {"value": QMessageBox.No}
    monkeypatch.setattr("ui.main_window.QMessageBox.question",
                        lambda *a, **_k: window._calls["question"].append(a[2]) or answers["value"])
    tab.purge_proxies_button.click()
    assert window._calls["question"] and window.proxies.usage_bytes() > 0   # refusé : rien n'est supprimé
    answers["value"] = QMessageBox.Yes
    tab.purge_proxies_button.click()
    assert window.proxies.usage_bytes() == 0 and window.proxies.entries() == []
    assert window.proxies.resolve(window.project.media_assets[0].path) == window.project.media_assets[0].path
    assert tab.status_label.text()
    assert Path(window.project.media_assets[0].path).is_file()              # les originaux ne sont jamais touchés


def test_purge_project_and_all_caches_buttons(qtbot, monkeypatch, tmp_path, fake_proxies):
    window = _window(qtbot, monkeypatch, tmp_path)
    window.generate_proxy_for_asset("v0")
    _wait_state(qtbot, window, 0, ProxyState.READY)
    window.runtime.cache.put("thumb:x", b"t", size_bytes=10)
    _dialog, tab = _prefs(qtbot, window)
    tab.purge_project_button.click()
    assert window.proxies.entries() == []
    tab.purge_all_button.click()
    assert window.runtime.cache.get("thumb:x") is None
    tab.purge_previews_button.click()


def test_generate_and_cancel_buttons_drive_the_manager(qtbot, monkeypatch, tmp_path, fake_proxies):
    window = _window(qtbot, monkeypatch, tmp_path)
    _dialog, tab = _prefs(qtbot, window)
    tab.generate_button.click()
    _wait_state(qtbot, window, 0, ProxyState.READY)
    tab.cancel_button.click()                                                # rien en cours : sans effet


def test_missing_ffmpeg_is_shown_in_the_tab(qtbot, monkeypatch, tmp_path, fake_proxies):
    window = _window(qtbot, monkeypatch, tmp_path)
    monkeypatch.setattr("ui.performance_settings.find_media_tool", lambda name: None)
    _dialog, tab = _prefs(qtbot, window)
    assert not tab.ffmpeg_warning.isHidden() and not tab.generate_button.isEnabled()
    monkeypatch.setattr("ui.performance_settings.find_media_tool", lambda name: "/usr/bin/ffmpeg")
    tab.refresh()
    assert tab.ffmpeg_warning.isHidden() and tab.generate_button.isEnabled()


def test_performance_tab_follows_the_language(qtbot, monkeypatch, tmp_path, fake_proxies):
    window = _window(qtbot, monkeypatch, tmp_path)
    dialog, tab = _prefs(qtbot, window)
    original = i18n.current_language()
    try:
        i18n.set_language("en")
        assert dialog.tabs.tabText(2) == "Performance"
        assert tab.proxies_box.title() == "Proxies" and tab.cache_box.title() == "Cache"
        assert tab.purge_all_button.text() == "Purge all caches"
        assert tab.profile_combo.itemText(0) == "Low (480p)"
        i18n.set_language("es")
        assert tab.cache_box.title() == "Caché"
    finally:
        i18n.set_language(original)


def test_restoring_default_preferences_keeps_the_performance_settings(qtbot, monkeypatch, tmp_path, fake_proxies):
    window = _window(qtbot, monkeypatch, tmp_path)
    window.set_proxy_profile("high")
    window.set_cache_max_gb(9.0)
    window._restore_default_preferences()
    assert window.proxies.profile.id == "high" and window.cache_manager.max_bytes == 9 * 1024 ** 3


def test_every_new_translation_exists_in_all_languages():
    keys = [k for k in i18n._TRANSLATIONS if k.startswith(("proxy.", "perf."))] + [
        "menu.item.use_proxies", "preview.quality_reduced"]
    assert len(keys) > 30
    for key in keys:
        entry = i18n._TRANSLATIONS[key]
        assert set(entry) == {"fr", "en", "es"} and all(entry.values()), key
    from core.proxy_profiles import builtin_profiles

    assert all(f"proxy.profile.{p.id}" in i18n._TRANSLATIONS for p in builtin_profiles())


# --- qualité d'aperçu adaptative dans la fenêtre ------------------------------------------------------------------


def _slow_ticks(window, count, interval=0.065, start=500.0):
    now = start
    for _ in range(count):
        now += interval
        window._observe_playback_quality(now)
    return now


def test_auto_quality_degrades_under_load_shows_a_notice_and_returns_on_pause(
    qtbot, monkeypatch, tmp_path, fake_proxies
):
    window = _window(qtbot, monkeypatch, tmp_path)
    window.runtime.set_preview_quality("auto")
    baseline = window.runtime.preview_divisor()
    window.show()
    _slow_ticks(window, 160)
    assert window.runtime.preview_divisor() > baseline and window.runtime.preview.degraded
    assert window.preview_panel.preview_divisor == window.runtime.preview_divisor()
    assert not window.preview_panel.preview_quality_notice.isHidden()
    assert window.runtime.preview_label() in window.preview_panel.preview_quality_notice.text()
    window._pause_internal()                                                 # pause : retour au niveau de base
    assert window.runtime.preview_divisor() == baseline
    assert window.preview_panel.preview_quality_notice.isHidden()


def test_a_forced_quality_is_never_changed_by_the_load(qtbot, monkeypatch, tmp_path, fake_proxies):
    window = _window(qtbot, monkeypatch, tmp_path)
    window.runtime.set_preview_quality("full")
    _slow_ticks(window, 300, interval=0.15)
    assert window.runtime.preview_divisor() == 1 and not window.runtime.preview.degraded
    assert window.preview_panel.preview_quality_notice.isHidden()


def test_changing_the_quality_or_profile_resets_the_adaptation(qtbot, monkeypatch, tmp_path, fake_proxies):
    window = _window(qtbot, monkeypatch, tmp_path)
    window.runtime.set_preview_quality("auto")
    _slow_ticks(window, 160)
    assert window.runtime.preview.degraded
    window.on_performance_setting_changed("high")
    assert not window.runtime.preview.degraded


def test_scopes_are_not_analysed_during_playback_while_the_preview_is_reduced(
    qtbot, monkeypatch, tmp_path, fake_proxies
):
    window = _window(qtbot, monkeypatch, tmp_path)
    requests = []
    monkeypatch.setattr(window, "_request_scopes_analysis", lambda: requests.append(1))
    monkeypatch.setattr(window, "_sync_preview_to_timeline", lambda: [])
    monkeypatch.setattr(window, "update_subtitle_overlay", lambda *_a: None)
    window.is_playing = True
    window.runtime.set_preview_quality("auto")
    window._tick_playback()
    assert requests == [1]                                                  # charge normale : analyse habituelle
    _slow_ticks(window, 160, start=time.perf_counter() + 100.0)
    assert window.runtime.preview.degraded
    window._tick_playback()
    assert requests == [1]                                                  # réduit : pas de FFmpeg de scopes en lecture
    window.is_playing = False


# --- préchargement dans la fenêtre ----------------------------------------------------------------------------------


def test_prefetch_only_plans_a_few_segments_with_small_plans_on_a_big_project(qtbot, monkeypatch, tmp_path, fake_proxies):
    from tools.perf.synthetic import build_project

    window = _window(qtbot, monkeypatch, tmp_path, project=False)
    window.project = build_project(3000, "short_8t", media_path=str(tmp_path / "media" / "m.mp4"))
    (tmp_path / "media").mkdir(exist_ok=True)
    window.timeline_panel.set_project(window.project)
    window._prefetch_planner().reset()
    jobs = window._preview_segment_jobs(500.0, velocity=0.0)
    assert 1 <= len(jobs) <= 4
    total = len(window._ensure_timeline_index()._clips_by_id)
    assert total == 3000
    for job in jobs:
        assert len(job.plan.video_layers) < 40                              # jamais les 3000 clips du montage
        assert job.start % 2.0 == 0.0                                       # segments alignés sur la grille


def test_fast_scrubbing_abandons_distant_requests_and_keeps_the_current_one(qtbot, monkeypatch, tmp_path, fake_proxies):
    window = _window(qtbot, monkeypatch, tmp_path)
    window.project = _project(tmp_path, videos=2)
    window.project.tracks[0].clips[0].source_out = 300.0
    window.timeline_panel.set_project(window.project)
    engine = window.preview_engine
    window._preview_pump_timer.stop()
    planner = window._prefetch_planner()
    fake_now = {"t": 1000.0}
    planner._clock = lambda: fake_now["t"]
    window._schedule_preview_around(10.0)
    first = engine.pending_starts()
    assert 10.0 // 2 * 2 in first
    fake_now["t"] += 0.05
    window._schedule_preview_around(210.0)                                  # saut très rapide : 4000 s/s
    starts = engine.pending_starts()
    assert starts == [210.0 // 2 * 2]                                       # seul le segment courant reste demandé
    assert all(abs(start - 210.0) < 4.0 for start in starts)
    fake_now["t"] += 5.0                                                     # l'utilisateur s'arrête
    window._schedule_preview_around(210.0)
    assert len(engine.pending_starts()) > 1                                  # à l'arrêt : devant et derrière aussi


def test_a_far_edit_keeps_the_cached_segment_under_the_playhead_valid(qtbot, monkeypatch, tmp_path, fake_proxies):
    window = _window(qtbot, monkeypatch, tmp_path)
    window._preview_pump_timer.stop()
    window.project.tracks[0].clips[0].source_out = 3.0
    window.timeline_panel.set_project(window.project)
    job = window._preview_segment_jobs(1.0, velocity=0.0)[0]
    window.preview_engine.cache.lookup = lambda key: Path("/tmp/segment.mp4") if key == job.key else None
    window._last_preview_jobs = [job]
    assert window._cached_preview_at(1.0) == ("/tmp/segment.mp4", 0.0)
    window.project.tracks[0].clips[1].source_out = 2.0                       # on retaille l'AUTRE clip (4–8 s)
    assert window._cached_preview_at(1.0) == ("/tmp/segment.mp4", 0.0)        # le segment de 0–2 s reste valide
    window.project.tracks[0].clips[0].source_out = 3.5                       # on retaille CELUI-CI
    assert window._cached_preview_at(1.0) is None                            # là, il est périmé
    assert isinstance(Qt.AlignLeft, object) and QMenu is not None
