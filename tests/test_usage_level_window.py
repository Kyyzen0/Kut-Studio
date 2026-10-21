"""Niveau d'usage dans la fenêtre principale (lot 1) : instantané, « Restaurer », ``set_usage_level``."""

from __future__ import annotations

from dataclasses import replace

import pytest
from test_scopes import _window

from core.project_io import project_payload
from core.usage_level import UsageLevel
from core.user_settings import UserSettings, load_user_settings, save_user_settings


def _seed(tmp_path, monkeypatch, level: str):
    monkeypatch.setenv("KUT_STUDIO_CONFIG_DIR", str(tmp_path))
    save_user_settings(replace(UserSettings(), usage_level=level))


def test_the_window_starts_at_the_stored_level(qtbot, monkeypatch, tmp_path):
    _seed(tmp_path, monkeypatch, "studio")
    window = _window(qtbot, monkeypatch, tmp_path)
    assert (
        window.usage_level is UsageLevel.STUDIO
        and window._settings_snapshot().usage_level == "studio"
    )


def test_a_fresh_install_window_is_simple_and_an_old_file_is_advanced(
    qtbot, monkeypatch, tmp_path
):
    fresh = tmp_path / "fresh"
    assert _window(qtbot, monkeypatch, fresh).usage_level is UsageLevel.SIMPLE
    old = tmp_path / "old"
    old.mkdir()
    (old / "user_settings.json").write_text('{"theme_mode": "dark"}', encoding="utf-8")
    assert _window(qtbot, monkeypatch, old).usage_level is UsageLevel.ADVANCED


@pytest.mark.parametrize("value", ["light", "en"])
def test_saving_another_setting_keeps_the_level(qtbot, monkeypatch, tmp_path, value):
    _seed(tmp_path, monkeypatch, "studio")
    window = _window(qtbot, monkeypatch, tmp_path)
    window.on_user_setting_changed(value)
    assert load_user_settings(tmp_path).usage_level == "studio"
    window.on_language_changed("fr")
    assert load_user_settings(tmp_path).usage_level == "studio"


def test_restore_default_preferences_keeps_the_level(qtbot, monkeypatch, tmp_path):
    _seed(tmp_path, monkeypatch, "advanced")
    window = _window(qtbot, monkeypatch, tmp_path)
    window.on_user_setting_changed("light")
    window._restore_default_preferences()
    assert window.usage_level is UsageLevel.ADVANCED
    stored = load_user_settings(tmp_path)
    assert stored.usage_level == "advanced" and stored.theme_mode == "dark"


def test_set_usage_level_saves_and_emits_once(qtbot, monkeypatch, tmp_path):
    _seed(tmp_path, monkeypatch, "advanced")
    window = _window(qtbot, monkeypatch, tmp_path)
    seen: list[str] = []
    window.usage_level_changed.connect(seen.append)
    assert window.set_usage_level("simple") is True
    assert window.usage_level is UsageLevel.SIMPLE and seen == ["simple"]
    assert load_user_settings(tmp_path).usage_level == "simple"
    assert window.set_usage_level(UsageLevel.STUDIO) is True and seen == [
        "simple",
        "studio",
    ]


def test_set_usage_level_unchanged_writes_nothing_and_emits_nothing(
    qtbot, monkeypatch, tmp_path
):
    _seed(tmp_path, monkeypatch, "studio")
    window = _window(qtbot, monkeypatch, tmp_path)
    calls: list[UserSettings] = []
    monkeypatch.setattr("ui.main_window.save_user_settings", calls.append)
    seen: list[str] = []
    window.usage_level_changed.connect(seen.append)
    assert window.set_usage_level("studio") is False
    assert calls == [] and seen == []


@pytest.mark.parametrize(
    "bad",
    ["expert", "", None, True, 2, ["simple"], "S" * 100_000],
    ids=lambda v: f"len{len(v)}" if isinstance(v, str) and len(v) > 50 else repr(v),
)
def test_set_usage_level_refuses_an_invalid_value(qtbot, monkeypatch, tmp_path, bad):
    _seed(tmp_path, monkeypatch, "studio")
    window = _window(qtbot, monkeypatch, tmp_path)
    calls: list[UserSettings] = []
    monkeypatch.setattr("ui.main_window.save_user_settings", calls.append)
    seen: list[str] = []
    window.usage_level_changed.connect(seen.append)
    assert window.set_usage_level(bad) is False
    assert window.usage_level is UsageLevel.STUDIO and calls == [] and seen == []


def test_set_usage_level_survives_a_failed_write(qtbot, monkeypatch, tmp_path):
    _seed(tmp_path, monkeypatch, "studio")
    window = _window(qtbot, monkeypatch, tmp_path)

    def boom(_settings):
        raise OSError("disque plein")

    monkeypatch.setattr("ui.main_window.save_user_settings", boom)
    seen: list[str] = []
    window.usage_level_changed.connect(seen.append)
    assert window.set_usage_level("simple") is True and seen == ["simple"]


def test_the_window_project_never_carries_the_level(qtbot, monkeypatch, tmp_path):
    _seed(tmp_path, monkeypatch, "advanced")
    window = _window(qtbot, monkeypatch, tmp_path)
    before = repr(project_payload(window.project))
    window.set_usage_level("simple")
    after = repr(project_payload(window.project))
    assert before == after and "usage_level" not in after
