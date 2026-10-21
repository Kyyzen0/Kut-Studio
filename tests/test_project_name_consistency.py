"""D4 : la barre du haut et le dialogue « Enregistrer les modifications ? » nomment le projet de la même façon."""

import pytest
from PySide6.QtWidgets import QMessageBox

from tools import ui_audit as audit
from ui import i18n
from ui.main_window_mixins.project_files import ProjectFilesMixin

_REAL_CONFIRM = (
    ProjectFilesMixin._confirm_discard_changes
)  # l'autouse de conftest le remplace par « abandonner »


@pytest.fixture(scope="module")
def window(tmp_path_factory):
    patch = pytest.MonkeyPatch()
    base = tmp_path_factory.mktemp("project_name")
    patch.setenv("KUT_STUDIO_CONFIG_DIR", str(base / "config"))
    patch.setenv("KUT_STUDIO_CACHE_DIR", str(base / "cache"))
    patch.setenv("KUT_STUDIO_PROXY_DIR", str(base / "proxies"))
    patch.setenv("KUT_STUDIO_HARDWARE_ENCODING", "off")
    main = audit.make_main_window(1280, 720, scopes=False, rich=False)
    yield main
    i18n.set_language("fr")
    main.project_dirty = False
    main.close()
    patch.undo()


def _asked_text(window, monkeypatch) -> str:
    """Le texte du dialogue de modifications non enregistrées (sans l'ouvrir pour de bon)."""
    monkeypatch.setattr(ProjectFilesMixin, "_confirm_discard_changes", _REAL_CONFIRM)
    seen: list[str] = []

    def fake_question(_parent, _title, text, *_args):
        seen.append(text)
        return QMessageBox.Discard

    monkeypatch.setattr(QMessageBox, "question", staticmethod(fake_question))
    window.project_dirty = True
    window._confirm_discard_changes()
    return seen[0]


@pytest.mark.parametrize(
    "language,default_name",
    [("fr", "Mon montage"), ("en", "My edit"), ("es", "Mi montaje")],
)
@pytest.mark.parametrize("stored_name", ["Projet sans titre", "", "   "])
def test_an_unnamed_project_has_the_same_name_in_the_title_bar_and_the_dialog(
    window, monkeypatch, language, default_name, stored_name
) -> None:
    i18n.set_language(language)
    window.project.name = stored_name
    window._update_top_bar()
    assert window.project_label.text() == default_name
    assert default_name in _asked_text(window, monkeypatch)
    assert "Projet sans titre" not in _asked_text(window, monkeypatch)


def test_a_named_project_keeps_its_name_in_both_places(window, monkeypatch) -> None:
    i18n.set_language("fr")
    window.project.name = "Reportage Lisbonne"
    window._update_top_bar()
    assert window.project_label.text() == "Reportage Lisbonne"
    assert "Reportage Lisbonne" in _asked_text(window, monkeypatch)
