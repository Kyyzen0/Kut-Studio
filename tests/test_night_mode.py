"""Mode nuit : réglages, horaire (horloge injectée), état et persistance. Sans Qt."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime

import pytest

from core.night_mode import (
    DEFAULT_END, DEFAULT_INTENSITY, DEFAULT_START, NightModeState, coerce_intensity, coerce_time, in_window, parse_time,
)
from core.user_settings import UserSettings, load_user_settings, save_user_settings


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, 7, hour, minute)


@pytest.mark.parametrize("value, expected", [
    (0, 0), (100, 100), (60, 60), (-5, 0), (250, 100), (59.6, 60), (True, DEFAULT_INTENSITY), ("60", DEFAULT_INTENSITY),
    (None, DEFAULT_INTENSITY), (float("nan"), DEFAULT_INTENSITY), (float("inf"), DEFAULT_INTENSITY), ([1], DEFAULT_INTENSITY),
])
def test_intensity_is_bounded_and_invalid_values_fall_back(value, expected):
    assert coerce_intensity(value) == expected


@pytest.mark.parametrize("value, expected", [
    ("21:00", 1260), ("7:05", 425), ("00:00", 0), ("23:59", 1439), ("24:00", None), ("12:60", None), ("abc", None),
    ("", None), (None, None), (2100, None), ("21:00:00", None), ("-1:00", None), ("9" * 400, None),
])
def test_parse_time(value, expected):
    assert parse_time(value) == expected


def test_coerce_time_normalises_and_falls_back():
    assert coerce_time("7:05", "21:00") == "07:05"
    assert coerce_time("nope", "21:00") == "21:00"


@pytest.mark.parametrize("hour, minute, expected", [
    (21, 0, True), (23, 59, True), (0, 0, True), (6, 59, True), (7, 0, False), (12, 0, False), (20, 59, False),
])
def test_window_across_midnight(hour, minute, expected):
    assert in_window("21:00", "07:00", at(hour, minute)) is expected


@pytest.mark.parametrize("hour, expected", [(8, False), (9, True), (12, True), (16, True), (17, False), (23, False)])
def test_window_within_one_day(hour, expected):
    assert in_window("09:00", "17:00", at(hour)) is expected


@pytest.mark.parametrize("hour", [0, 7, 12, 21, 23])
def test_equal_start_and_end_is_never_active(hour):
    assert in_window("21:00", "21:00", at(hour)) is False


def test_invalid_hours_are_never_active():
    assert in_window("25:00", "07:00", at(23)) is False
    assert in_window("21:00", "xx", at(23)) is False


def test_state_is_off_by_default_and_level_is_zero():
    state = NightModeState()
    assert not state.active(at(23)) and state.level(at(23)) == 0


def test_manual_enable_gives_the_intensity_at_any_hour():
    state = NightModeState(enabled=True, intensity=40)
    assert state.level(at(12)) == 40


def test_automatic_follows_the_window_with_an_injected_clock():
    state = NightModeState(auto=True)
    assert state.level(at(22)) == DEFAULT_INTENSITY and state.level(at(12)) == 0
    assert (state.start, state.end) == (DEFAULT_START, DEFAULT_END)


def test_automatic_off_ignores_the_window():
    assert NightModeState(auto=False).level(at(22)) == 0


def test_toggle_on_during_the_day_is_manual_and_persistent():
    state = NightModeState(auto=True)
    assert state.toggle(at(12)) is True and state.enabled and state.active(at(12))
    assert state.active(at(18))                                   # manuel : ne suit pas l'horaire


def test_toggle_off_during_the_window_lasts_until_the_schedule_flips():
    state = NightModeState(auto=True)
    state.sync(at(22))
    assert state.toggle(at(22)) is False and not state.active(at(22))
    state.sync(at(23))
    assert not state.active(at(23))                               # toujours éteint dans la même plage
    state.sync(at(8))                                             # la plage s'est terminée : l'override tombe
    assert state.override is None and not state.active(at(8))
    state.sync(at(21, 30))                                        # le lendemain soir
    assert state.active(at(21, 30))


def test_toggle_off_when_manual_without_schedule():
    state = NightModeState(enabled=True)
    assert state.toggle(at(12)) is False and not state.enabled and not state.active(at(12))


def test_changed_settings_reset_the_override():
    state = NightModeState(auto=True, override=False)
    state.reset_override(at(22))
    assert state.override is None and state.active(at(22))


# -- persistance ----------------------------------------------------------------------------------------------------------


def test_defaults_are_off_60_percent_21_to_07():
    s = UserSettings()
    assert (s.night_mode_enabled, s.night_intensity, s.night_auto, s.night_start, s.night_end) == (
        False, 60, False, "21:00", "07:00")


def test_night_settings_round_trip(tmp_path):
    save_user_settings(replace(UserSettings(), night_mode_enabled=True, night_intensity=35, night_auto=True,
                               night_start="22:30", night_end="06:15"), tmp_path)
    loaded = load_user_settings(tmp_path)
    assert (loaded.night_mode_enabled, loaded.night_intensity, loaded.night_auto, loaded.night_start, loaded.night_end) == (
        True, 35, True, "22:30", "06:15")


def test_invalid_stored_values_fall_back_to_the_defaults(tmp_path):
    (tmp_path / "user_settings.json").write_text(json.dumps({
        "night_mode_enabled": "peut-être", "night_intensity": "fort", "night_auto": None, "night_start": "99:99",
        "night_end": 7}), encoding="utf-8")
    loaded = load_user_settings(tmp_path)
    assert (loaded.night_mode_enabled, loaded.night_intensity, loaded.night_auto, loaded.night_start, loaded.night_end) == (
        False, 60, False, "21:00", "07:00")


def test_out_of_range_intensity_is_clamped_on_load_and_on_save(tmp_path):
    (tmp_path / "user_settings.json").write_text(json.dumps({"night_intensity": 900}), encoding="utf-8")
    assert load_user_settings(tmp_path).night_intensity == 100
    save_user_settings(replace(UserSettings(), night_intensity=-3, night_start="bad"), tmp_path)
    data = json.loads((tmp_path / "user_settings.json").read_text(encoding="utf-8"))
    assert data["night_intensity"] == 0 and data["night_start"] == "21:00"


def test_the_render_and_export_side_never_imports_the_interface():
    """Garde de frontière : le mode nuit vit dans ``ui`` ; le plan de rendu, l'export, l'aperçu rendu et les scopes d'analyse (``core``)
    n'importent jamais l'interface, donc ne lisent jamais la palette. (Quelques modules de ``core`` importent ``ui`` à l'appel, pour un
    message ou une boîte : ils ne rendent aucune image.)"""
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    offenders = []
    pixel_modules = ("export_engine", "render_plan", "filter_graph", "preview_render", "scopes", "scopes_analyzer", "playhead_snapshot")
    for path in sorted(root.joinpath("core", f"{name}.py") for name in pixel_modules):
        assert path.exists(), path
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = [node.module or ""] if isinstance(node, ast.ImportFrom) and node.level == 0 else (
                [alias.name for alias in node.names] if isinstance(node, ast.Import) else [])
            if any(name == "ui" or name.startswith("ui.") for name in names):
                offenders.append(f"{path.relative_to(root)}:{node.lineno}")
    assert offenders == []
