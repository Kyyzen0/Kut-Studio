"""Tests unitaires de ``core.user_settings`` (tâche 14)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.user_settings import (
    DEFAULT_LANGUAGE,
    DEFAULT_THEME,
    VALID_LANGUAGES,
    VALID_THEME_MODES,
    UserSettings,
    load_user_settings,
    save_user_settings,
    settings_file_path,
)


# ---------------------------------------------------------------------------
# Cycle simple
# ---------------------------------------------------------------------------


def test_load_when_file_absent_returns_defaults(tmp_path):
    settings = load_user_settings(tmp_path)
    assert settings.theme_mode == DEFAULT_THEME
    assert settings.language == DEFAULT_LANGUAGE


def test_save_and_load_roundtrip_preserves_values(tmp_path):
    save_user_settings(
        UserSettings(theme_mode="light", language="en"),
        tmp_path,
    )
    loaded = load_user_settings(tmp_path)
    assert loaded.theme_mode == "light"
    assert loaded.language == "en"


def test_settings_file_path_inside_directory(tmp_path):
    target = settings_file_path(tmp_path)
    assert target.parent == Path(tmp_path)
    assert target.name == "user_settings.json"


def test_default_settings_file_does_not_write_inside_repo(tmp_path):
    """Le helper sans répertoire ne doit PAS écrire dans le dépôt."""
    settings = load_user_settings(tmp_path)
    assert settings.theme_mode in VALID_THEME_MODES
    assert settings.language in VALID_LANGUAGES


# ---------------------------------------------------------------------------
# Robustesse aux fichiers malformés
# ---------------------------------------------------------------------------


def test_load_returns_defaults_for_invalid_json(tmp_path):
    (tmp_path / "user_settings.json").write_text("{not json at all", encoding="utf-8")
    settings = load_user_settings(tmp_path)
    assert settings.theme_mode == DEFAULT_THEME
    assert settings.language == DEFAULT_LANGUAGE


def test_load_returns_defaults_when_payload_is_not_dict(tmp_path):
    (tmp_path / "user_settings.json").write_text("[1, 2, 3]", encoding="utf-8")
    settings = load_user_settings(tmp_path)
    assert settings == UserSettings()


@pytest.mark.parametrize(
    "field,bad,good",
    [
        ("theme_mode", "neon", DEFAULT_THEME),
        ("language", "de", DEFAULT_LANGUAGE),
    ],
)
def test_load_invalid_value_for_field_falls_back(tmp_path, field, bad, good):
    payload = {"theme_mode": "dark", "language": "fr"}
    payload[field] = bad
    (tmp_path / "user_settings.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    settings = load_user_settings(tmp_path)
    assert getattr(settings, field) == good


def test_save_writes_valid_json_utf8(tmp_path):
    save_user_settings(UserSettings(theme_mode="system", language="es"), tmp_path)
    raw = (tmp_path / "user_settings.json").read_text(encoding="utf-8")
    payload = json.loads(raw)
    assert payload["theme_mode"] == "system"
    assert payload["language"] == "es"


def test_save_normalises_invalid_values_to_defaults(tmp_path):
    save_user_settings(UserSettings(theme_mode="rainbow", language="jp"), tmp_path)
    loaded = load_user_settings(tmp_path)
    assert loaded.theme_mode == DEFAULT_THEME
    assert loaded.language == DEFAULT_LANGUAGE


def test_save_is_atomic_no_partial_file_on_error(tmp_path, monkeypatch):
    """Un échec d'écriture ne doit pas laisser de ``.tmp`` traçant."""
    # On intercepte ``os.replace`` pour le faire lever. La cible ne
    # doit pas être créée et le ``.tmp`` doit être nettoyé.
    import core.user_settings as user_settings_module

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(user_settings_module.os, "replace", boom)
    with pytest.raises(OSError):
        save_user_settings(UserSettings(), tmp_path)
    target = tmp_path / "user_settings.json"
    assert not target.exists()
    leftovers = list(tmp_path.glob("user_settings.json.*.tmp"))
    assert leftovers == []


# ---------------------------------------------------------------------------
# Découplage avec le dépôt du projet
# ---------------------------------------------------------------------------


def test_no_settings_file_in_cwd_when_using_explicit_dir(tmp_path, monkeypatch):
    """Aucun fichier ``user_settings.json`` ne doit apparaître en CWD."""
    cwd_tmp = tmp_path / "cwd"
    cwd_tmp.mkdir()
    monkeypatch.chdir(cwd_tmp)
    save_user_settings(UserSettings(), tmp_path)
    assert not (cwd_tmp / "user_settings.json").exists()


def test_directory_is_created_when_missing(tmp_path):
    nested = tmp_path / "subdir"
    save_user_settings(UserSettings(), nested)
    assert nested.exists()
    assert (nested / "user_settings.json").exists()
