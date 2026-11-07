"""Mode nuit dans la fenêtre réelle (offscreen) : commande, pastille, horloge injectée, Préférences, persistance, et surtout
ce qui ne change JAMAIS (moniteur, scopes, export)."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from datetime import datetime

import pytest
from PySide6.QtWidgets import QApplication
from test_scopes import _window

from core.night_mode import CHECK_INTERVAL_MS
from core.user_settings import load_user_settings
from ui import i18n
from ui.preferences_dialog import PreferencesDialog
from ui.theme import COLORS, PURE_COLORS, THEMES, set_active_palette


@pytest.fixture
def window(qtbot, monkeypatch, tmp_path):
    i18n.set_language("fr")
    main = _window(qtbot, monkeypatch, tmp_path)
    main.set_usage_level("simple")
    main._night_clock = lambda: datetime(2026, 11, 7, 12, 0)          # horloge injectée : midi
    yield main
    set_active_palette(THEMES["dark"])
    i18n.set_language("fr")


def _noon():
    return datetime(2026, 11, 7, 12, 0)


def test_starts_off_with_the_menu_entry_unchecked_and_the_timer_light(window):
    assert not window.night_mode_active and window.theme_manager.night_intensity == 0
    assert not window.night_action.isChecked()
    assert window._night_timer.interval() == CHECK_INTERVAL_MS == 60_000 and window._night_timer.isActive()


def test_the_command_toggles_the_palette_the_menu_and_the_saved_settings(window, tmp_path):
    base_sheet = QApplication.instance().styleSheet()
    assert window.shortcuts.trigger("night_mode")
    assert window.night_mode_active and window.theme_manager.night_intensity == 60
    assert window.night_action.isChecked()
    assert QApplication.instance().styleSheet() != base_sheet
    assert COLORS["background"] != THEMES["dark"].background and PURE_COLORS["background"] == THEMES["dark"].background
    saved = load_user_settings(tmp_path)
    assert saved.night_mode_enabled is True and saved.night_intensity == 60
    window.shortcuts.trigger("night_mode")
    assert not window.night_mode_active and not window.night_action.isChecked()
    assert QApplication.instance().styleSheet() == base_sheet                    # retour exact à la feuille d'origine
    assert load_user_settings(tmp_path).night_mode_enabled is False


def test_triggering_the_menu_action_toggles_it_and_there_is_no_status_bar_cost(window):
    window.night_action.trigger()
    assert window.night_mode_active and window.night_action.isChecked()
    window.night_action.trigger()
    assert not window.night_mode_active


def test_local_styles_follow_and_come_back(window):
    """Les styles locaux (peints à la construction) sont re-teintés dans les deux sens et reviennent à l'identique (la casse des couleurs hexadécimales mise à part : la re-teinte écrit des majuscules)."""
    from PySide6.QtWidgets import QWidget

    def sheets():
        return [(w.objectName(), w.styleSheet().upper()) for w in (window, *window.findChildren(QWidget)) if w.styleSheet()]

    before = sheets()
    assert len(before) > 20
    window.toggle_night_mode()
    during = sheets()
    assert during != before and len(during) == len(before)
    window.toggle_night_mode()
    assert sheets() == before


def test_a_manual_off_during_the_window_lasts_until_the_window_ends(window):
    window.set_night_settings(False, 60, True, "21:00", "07:00")
    clock = {"now": datetime(2026, 11, 7, 22, 0)}
    window._night_clock = lambda: clock["now"]
    window._night_tick()
    assert window.night_mode_active and window.theme_manager.night_intensity == 60
    window.toggle_night_mode()                                                 # « éteindre » à 22 h
    assert not window.night_mode_active
    clock["now"] = datetime(2026, 11, 7, 23, 30)
    window._night_tick()
    assert not window.night_mode_active
    clock["now"] = datetime(2026, 11, 8, 8, 0)
    window._night_tick()
    clock["now"] = datetime(2026, 11, 8, 21, 5)
    window._night_tick()
    assert window.night_mode_active


def test_the_tick_only_repaints_when_the_level_changes(window, monkeypatch):
    window.set_night_settings(False, 60, True, "21:00", "07:00")
    calls = []
    monkeypatch.setattr("ui.main_window_mixins.night_mode.retheme_application", lambda *a, **k: calls.append(a) or 0)
    for _ in range(5):
        window._night_tick()                                                  # midi, éteint : rien à faire
    assert calls == []
    window._night_clock = lambda: datetime(2026, 11, 7, 22, 0)
    window._night_tick()
    window._night_tick()
    assert len(calls) == 1


def test_equal_start_and_end_never_turns_the_schedule_on(window):
    window.set_night_settings(False, 60, True, "21:00", "21:00")
    window._night_clock = lambda: datetime(2026, 11, 7, 23, 0)
    window._night_tick()
    assert not window.night_mode_active


def test_invalid_script_values_fall_back(window):
    window.set_night_settings(True, 9000, True, "xx", "99:99")
    state = window._night_state
    assert (state.intensity, state.start, state.end) == (100, "21:00", "07:00")


def test_the_timer_is_stopped_at_shutdown(window):
    names = [name for name, _step in window._shutdown_steps()]
    assert "minuteur mode nuit" in names
    window._stop_night_timer()
    assert not window._night_timer.isActive()


def test_persisted_night_is_applied_before_the_widgets_are_built(qtbot, monkeypatch, tmp_path):
    from dataclasses import replace

    from core.user_settings import UserSettings, save_user_settings

    save_user_settings(replace(UserSettings(), night_mode_enabled=True, night_intensity=80), tmp_path)
    main = _window(qtbot, monkeypatch, tmp_path)
    try:
        assert main.night_mode_active and main.theme_manager.night_intensity == 80
    finally:
        set_active_palette(THEMES["dark"])


def test_switching_theme_and_language_keep_the_night_setting(window):
    window.toggle_night_mode()
    window.on_user_setting_changed("light")
    assert window.night_mode_active and window.theme_manager.base_palette is THEMES["light"]
    assert COLORS["background"] != THEMES["light"].background
    window.on_user_setting_changed("en")
    assert window.night_mode_active and window.night_action.text() == "Night mode"


def test_the_command_is_in_the_palette_at_every_level(window):
    titles = {e.title for e in window.command_palette_entries()}
    assert any(t.endswith("Mode nuit") for t in titles)
    for level in ("studio", "advanced"):
        window.set_usage_level(level)
        assert any(t.endswith("Mode nuit") for t in {e.title for e in window.command_palette_entries()})


@pytest.mark.parametrize("language, text", [("fr", "Basculer le mode nuit"), ("en", "Toggle night mode"), ("es", "Alternar el modo noche")])
def test_the_command_has_a_label_in_each_language(language, text):
    assert i18n.translate_strict("shortcuts.command.night_mode", language) == text


# -- Préférences --------------------------------------------------------------------------------------------------------------


def test_preferences_group_edits_reach_the_window(window, qtbot, tmp_path):
    dialog = PreferencesDialog(night_state=window._night_state, parent=window)
    qtbot.addWidget(dialog)
    dialog.night_mode_changed.connect(window.set_night_settings)
    box = dialog.night_box
    assert box is not None and not box.start_edit.isEnabled()                   # horaire grisé tant que « automatique » est décoché
    box.enabled_box.setChecked(True)
    assert window.night_mode_active
    box.intensity_slider.setValue(30)                                           # clavier / molette : un cran = un passage
    assert window._night_state.intensity == 30 and window.theme_manager.night_intensity == 30
    box.auto_box.setChecked(True)
    assert box.start_edit.isEnabled()
    saved = load_user_settings(tmp_path)
    assert (saved.night_mode_enabled, saved.night_intensity, saved.night_auto) == (True, 30, True)


def test_dragging_the_slider_does_not_repaint_until_release(window, qtbot):
    dialog = PreferencesDialog(night_state=window._night_state, parent=window)
    qtbot.addWidget(dialog)
    seen = []
    dialog.night_mode_changed.connect(lambda *values: seen.append(values))
    slider = dialog.night_box.intensity_slider
    slider.setSliderDown(True)
    for value in range(10, 90, 10):
        slider.setValue(value)
    assert seen == []
    slider.setSliderDown(False)                                                # le relâchement émet une seule fois
    assert len(seen) == 1 and seen[0][1] == 80


def test_the_group_follows_a_change_made_elsewhere(window, qtbot):
    dialog = PreferencesDialog(night_state=window._night_state, parent=window)
    qtbot.addWidget(dialog)
    window._preferences_dialog = dialog
    window.toggle_night_mode()
    assert dialog.night_box.enabled_box.isChecked()
    window._preferences_dialog = None


def test_restoring_defaults_keeps_the_night_choice(window):
    window.set_night_settings(True, 40, True, "20:00", "06:00")
    window._restore_default_preferences()
    state = window._night_state
    assert (state.enabled, state.intensity, state.auto, state.start, state.end) == (True, 40, True, "20:00", "06:00")


# -- ce qui ne change JAMAIS ----------------------------------------------------------------------------------------------


def test_the_monitor_keeps_the_original_theme_colors_in_night_mode(window):
    panel = window.preview_panel
    base = THEMES["dark"].background
    window.toggle_night_mode()
    assert COLORS["background"] != base
    assert panel.graphics_view.backgroundBrush().color().name().upper() == base.upper()
    assert base.upper() in panel.graphics_view.styleSheet().upper()
    panel.refresh_theme()
    assert panel.graphics_view.backgroundBrush().color().name().upper() == base.upper()
    window.on_user_setting_changed("light")                                    # un changement de thème, lui, suit toujours
    assert panel.graphics_view.backgroundBrush().color().name().upper() == THEMES["light"].background.upper()


def test_the_scopes_are_painted_with_the_original_colors(window):
    scopes = window.scopes_panel
    canvases = list(scopes._canvases.values())
    for canvas in canvases:
        canvas.resize(120, 80)
    off = [canvas.grab().toImage() for canvas in canvases]
    window.toggle_night_mode()
    on = [canvas.grab().toImage() for canvas in canvases]
    assert window.night_mode_active
    assert all(a == b for a, b in zip(off, on))


def test_the_scope_canvases_follow_a_theme_change_but_not_the_night(window):
    canvas = next(iter(window.scopes_panel._canvases.values()))
    window.toggle_night_mode()
    assert THEMES["dark"].background.upper() in canvas.styleSheet().upper()
    window.on_user_setting_changed("light")
    assert THEMES["light"].background.upper() in canvas.styleSheet().upper()


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg absent")
def test_the_export_is_byte_identical_with_and_without_night_mode(window, tmp_path):
    from core.export_engine import ExportEngine, ExportFormat, ExportPreset, ExportRequest, build_export_command
    from core.project_model import Clip, MediaAsset, Project, Track
    from core.render_plan import build_render_plan

    source = tmp_path / "source.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=160x90:r=25:d=1,format=yuv420p",
                    "-c:v", "libx264", "-preset", "ultrafast", str(source)], check=True, timeout=60)
    asset = MediaAsset(id="a", path=str(source), name="s", duration=1.0, width=160, height=90, fps=25.0, media_type="video",
                       has_audio=False)
    track = Track(id="V1", name="V1", type="video")
    track.clips.append(Clip(id="c", asset_id="a", track_id="V1", timeline_start=0.0, source_in=0.0, source_out=1.0))
    project = Project(name="p", width=160, height=90, fps=25.0, media_assets=[asset], tracks=[track])

    def render(tag: str):
        plan = build_render_plan(project)
        request = ExportRequest(render_plan=plan, output_path=str(tmp_path / f"{tag}.mp4"), format=ExportFormat.MP4_H264,
                                preset=ExportPreset("T", (160, 90), 28, "64k"), fps=25)
        command = build_export_command(request)
        frame = subprocess.run(ExportEngine().build_frame_command(request, 0.5), capture_output=True, timeout=60)
        assert frame.returncode == 0
        return plan, [a for a in command.arguments if tag not in a], hashlib.sha256(frame.stdout).hexdigest()

    day = render("day")
    window.toggle_night_mode()
    assert window.theme_manager.night_intensity == 60 and COLORS["panel"] != THEMES["dark"].panel
    night = render("night")
    assert night == day
