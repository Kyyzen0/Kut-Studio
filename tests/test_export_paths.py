"""Chemin d'export proposé par défaut : ne dépend d'aucun dossier macOS et ne crée rien."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.export_paths import FALLBACK_NAME, default_export_directory, export_file_name


def test_the_movies_folder_is_used_when_it_exists_otherwise_videos_otherwise_home(tmp_path):
    assert default_export_directory(str(tmp_path)) == str(tmp_path)
    (tmp_path / "Videos").mkdir()
    assert default_export_directory(str(tmp_path)) == str(tmp_path / "Videos")
    (tmp_path / "Movies").mkdir()
    assert default_export_directory(str(tmp_path)) == str(tmp_path / "Movies")


def test_looking_for_a_default_folder_creates_nothing(tmp_path):
    default_export_directory(str(tmp_path))
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("project_name", "expected"),
    [
        ("Mon film", "Mon film.mp4"),
        ("Été 2025 — v2", "Été 2025 — v2.mp4"),
        ("a/b\\c:d*e?f", "a_b_c_d_e_f.mp4"),             # séparateurs de chemin et caractères interdits sous Windows
        ("  ..  ", f"{FALLBACK_NAME}.mp4"),               # rien d'exploitable : nom de secours
        ("", f"{FALLBACK_NAME}.mp4"),
        (None, f"{FALLBACK_NAME}.mp4"),
        ("film. ", "film.mp4"),                           # Windows refuse un nom qui finit par un point ou un espace
        ("../../etc/passwd", "_.._etc_passwd.mp4"),
    ],
)
def test_the_file_name_is_valid_on_every_platform(project_name, expected):
    assert export_file_name(project_name, "mp4") == expected


def test_the_project_file_name_uses_its_own_fallback():
    assert export_file_name("", "kut", fallback="projet") == "projet.kut"
    assert export_file_name("a/b", "kut", fallback="projet") == "a_b.kut"


# --- câblage dans la fenêtre -------------------------------------------------------------------------------------


def _ask(monkeypatch, open_dialog):
    """Exécute ``open_dialog()`` en capturant le chemin proposé à la boîte « Enregistrer »."""
    import ui.main_window as main_window

    proposed = []

    def fake_dialog(_parent, _title, path, _filter):
        proposed.append(path)
        return "", ""

    monkeypatch.setattr(main_window.QFileDialog, "getSaveFileName", staticmethod(fake_dialog))
    open_dialog()
    return proposed[0]


def test_the_export_dialog_proposes_a_valid_path_without_creating_a_movies_folder(qtbot, monkeypatch, tmp_path):
    import os

    from test_scopes import _window

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    window = _window(qtbot, monkeypatch)
    window.project.name = "Voyage/été"
    proposed = _ask(monkeypatch, lambda: window._ask_export_path(SimpleNamespace(container="mp4")))
    assert os.path.dirname(proposed) == str(tmp_path)
    assert os.path.basename(proposed) == "Voyage_été.mp4"
    assert not (tmp_path / "Movies").exists()


def test_the_save_as_dialog_does_not_turn_the_project_name_into_a_subfolder(qtbot, monkeypatch, tmp_path):
    import os

    from test_scopes import _window

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    window = _window(qtbot, monkeypatch)
    window.current_project_path = None
    window.project.name = "a/b"
    proposed = _ask(monkeypatch, window.save_project_as)
    assert os.path.dirname(proposed) == str(tmp_path)
    assert os.path.basename(proposed) == "a_b.kut"
