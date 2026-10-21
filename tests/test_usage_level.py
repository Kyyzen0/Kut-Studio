"""Niveau d'usage (ADR-0006 étape 4, lot 1) : modèle pur, préférence, défauts, absence du ``.kut``."""

from __future__ import annotations

import json
import os
import stat
import sys
from dataclasses import replace

import pytest

from core.project_factory import create_default_project
from core.project_io import project_payload, save_project
from core.usage_level import (
    DEFAULT_EXISTING,
    DEFAULT_NEW_INSTALL,
    VALID_USAGE_LEVELS,
    UsageLevel,
    coerce_usage_level,
)
from core.user_settings import (
    UserSettings,
    load_user_settings,
    save_user_settings,
    settings_file_path,
)


def _write(tmp_path, text: str) -> None:
    settings_file_path(tmp_path).write_text(text, encoding="utf-8")


# --- Modèle pur -------------------------------------------------------------------------------


def test_stable_string_values_and_defaults():
    assert [level.value for level in UsageLevel] == ["simple", "studio", "advanced"]
    assert VALID_USAGE_LEVELS == ("simple", "studio", "advanced")
    assert (
        DEFAULT_NEW_INSTALL is UsageLevel.SIMPLE
        and DEFAULT_EXISTING is UsageLevel.ADVANCED
    )


def test_levels_are_ordered_for_minimum_level_filtering():
    simple, studio, advanced = UsageLevel.SIMPLE, UsageLevel.STUDIO, UsageLevel.ADVANCED
    assert simple.rank < studio.rank < advanced.rank
    assert (
        simple.at_least(simple)
        and not simple.at_least(studio)
        and not simple.at_least(advanced)
    )
    assert (
        studio.at_least(simple)
        and studio.at_least(studio)
        and not studio.at_least(advanced)
    )
    assert (
        advanced.at_least(simple)
        and advanced.at_least(studio)
        and advanced.at_least(advanced)
    )


@pytest.mark.parametrize("raw", ["simple", "studio", "advanced", UsageLevel.STUDIO])
def test_coerce_accepts_valid_values(raw):
    assert coerce_usage_level(raw) is UsageLevel(raw)


@pytest.mark.parametrize(
    "raw",
    [
        None,
        True,
        False,
        0,
        1,
        2.5,
        [],
        ["simple"],
        {},
        {"a": 1},
        b"simple",
        "",
        "Simple",
        " simple",
        "expert",
        "x" * 10_000_000,
    ],
    ids=lambda v: f"len{len(v)}" if isinstance(v, str) and len(v) > 50 else repr(v),
)
def test_coerce_never_raises_and_rejects_everything_else(raw):
    assert coerce_usage_level(raw) is None


# --- Défauts : installation neuve / fichier existant ------------------------------------------


def test_fresh_install_without_file_is_simple(tmp_path):
    assert load_user_settings(tmp_path).usage_level == "simple"
    assert UserSettings().usage_level == "simple"


def test_existing_file_without_the_field_is_advanced(tmp_path):
    _write(tmp_path, json.dumps({"theme_mode": "light", "language": "en"}))
    settings = load_user_settings(tmp_path)
    assert settings.usage_level == "advanced"
    assert settings.theme_mode == "light"


@pytest.mark.parametrize(
    "bad",
    [
        None,
        True,
        0,
        3,
        1.5,
        [],
        ["simple"],
        {"v": "simple"},
        "",
        "expert",
        "SIMPLE",
        "z" * 100_000,
    ],
    ids=lambda v: f"len{len(v)}" if isinstance(v, str) and len(v) > 50 else repr(v),
)
def test_existing_file_with_invalid_value_is_advanced(tmp_path, bad):
    _write(tmp_path, json.dumps({"usage_level": bad}))
    assert load_user_settings(tmp_path).usage_level == "advanced"


@pytest.mark.parametrize("level", ["simple", "studio", "advanced"])
def test_valid_value_is_kept_and_round_trips(tmp_path, level):
    save_user_settings(replace(UserSettings(), usage_level=level), tmp_path)
    assert (
        json.loads(settings_file_path(tmp_path).read_text("utf-8"))["usage_level"]
        == level
    )
    assert load_user_settings(tmp_path).usage_level == level


@pytest.mark.parametrize(
    "text", ["", "{not json", "[1, 2]", "null", '"simple"', "\x00\x01"]
)
def test_unreadable_or_empty_file_is_not_a_proven_fresh_install(tmp_path, text):
    _write(tmp_path, text)
    assert load_user_settings(tmp_path).usage_level == "advanced"


def test_non_utf8_file_is_advanced(tmp_path):
    settings_file_path(tmp_path).write_bytes(b"\xff\xfe\x00{")
    assert load_user_settings(tmp_path).usage_level == "advanced"


def test_an_oversized_file_is_not_read_and_is_advanced(tmp_path, monkeypatch):
    import core.user_settings as module

    monkeypatch.setattr(module, "MAX_FILE_BYTES", 100)
    _write(tmp_path, json.dumps({"usage_level": "simple", "whisper_path": "p" * 500}))
    assert load_user_settings(tmp_path).usage_level == "advanced"


def test_a_directory_in_place_of_the_file_is_advanced(tmp_path):
    settings_file_path(tmp_path).mkdir()
    assert load_user_settings(tmp_path).usage_level == "advanced"


@pytest.mark.skipif(
    sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="permissions POSIX, hors root",
)
def test_unreadable_permissions_is_advanced(tmp_path):
    save_user_settings(replace(UserSettings(), usage_level="simple"), tmp_path)
    path = settings_file_path(tmp_path)
    path.chmod(0)
    try:
        assert load_user_settings(tmp_path).usage_level == "advanced"
    finally:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)


@pytest.mark.skipif(
    sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="permissions POSIX, hors root",
)
def test_save_in_a_read_only_directory_raises_oserror_and_keeps_the_old_file(tmp_path):
    save_user_settings(replace(UserSettings(), usage_level="studio"), tmp_path)
    tmp_path.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        with pytest.raises(OSError):
            save_user_settings(
                replace(UserSettings(), usage_level="advanced"), tmp_path
            )
    finally:
        tmp_path.chmod(stat.S_IRWXU)
    assert load_user_settings(tmp_path).usage_level == "studio"


def test_a_huge_line_does_not_break_loading(tmp_path):
    _write(
        tmp_path, json.dumps({"usage_level": "studio", "whisper_path": "p" * 5_000_000})
    )
    settings = load_user_settings(tmp_path)
    assert settings.usage_level == "studio" and len(settings.whisper_path) <= 4096


@pytest.mark.parametrize("bad", ["expert", "", None, 3, ["simple"]])
def test_save_recoerces_an_invalid_level_to_advanced(tmp_path, bad):
    save_user_settings(replace(UserSettings(), usage_level=bad), tmp_path)  # type: ignore[arg-type]
    assert (
        json.loads(settings_file_path(tmp_path).read_text("utf-8"))["usage_level"]
        == "advanced"
    )


def test_saving_another_setting_keeps_the_level_in_the_file(tmp_path):
    save_user_settings(replace(UserSettings(), usage_level="studio"), tmp_path)
    save_user_settings(
        replace(load_user_settings(tmp_path), theme_mode="light", language="en"),
        tmp_path,
    )
    loaded = load_user_settings(tmp_path)
    assert (loaded.usage_level, loaded.theme_mode, loaded.language) == (
        "studio",
        "light",
        "en",
    )


# --- Jamais dans le .kut ----------------------------------------------------------------------


def test_the_level_never_enters_the_project_file(tmp_path, monkeypatch):
    monkeypatch.setenv("KUT_STUDIO_CONFIG_DIR", str(tmp_path / "cfg"))
    project = create_default_project()
    outputs = []
    for level in ("simple", "studio", "advanced"):
        save_user_settings(replace(UserSettings(), usage_level=level))
        target = tmp_path / f"{level}.kut"
        save_project(project, str(target))
        outputs.append(target.read_bytes())
        assert b"usage_level" not in outputs[-1] and "usage_level" not in json.dumps(
            project_payload(project)
        )
    assert outputs[0] == outputs[1] == outputs[2]
