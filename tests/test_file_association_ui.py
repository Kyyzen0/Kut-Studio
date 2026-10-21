"""Groupe « Fichiers de projet » des préférences : présent sous Windows et Linux, absent sous macOS."""

from __future__ import annotations

import pytest

import ui.preferences_dialog as preferences_dialog
from ui.preferences_dialog import PreferencesDialog


def test_the_group_is_absent_where_the_bundle_already_declares_the_association(qtbot, monkeypatch):
    monkeypatch.setattr(preferences_dialog, "file_association_supported", lambda: False)
    dialog = PreferencesDialog()
    qtbot.addWidget(dialog)
    assert dialog.files_box is None


def test_the_group_offers_one_button_that_asks_for_the_association(qtbot, monkeypatch):
    monkeypatch.setattr(preferences_dialog, "file_association_supported", lambda: True)
    dialog = PreferencesDialog()
    qtbot.addWidget(dialog)
    assert dialog.files_box is not None
    asked: list[bool] = []
    dialog.file_association_requested.connect(lambda: asked.append(True))
    dialog.files_button.click()
    assert asked == [True]
    assert dialog.files_button.text() and dialog.files_note.text()


def test_the_association_result_is_announced_and_a_failure_names_its_cause(qtbot, monkeypatch):
    from core.file_association import AssociationResult
    import ui.main_window_mixins.preferences as preferences_mixin

    announced: list[tuple[str, str]] = []
    monkeypatch.setattr(preferences_mixin.QMessageBox, "information",
                        lambda parent, title, text: announced.append(("ok", text)))
    monkeypatch.setattr(preferences_mixin.QMessageBox, "warning",
                        lambda parent, title, text: announced.append(("warn", text)))

    class Host:
        _preferences_dialog = None

    import core.file_association as association

    monkeypatch.setattr(association, "associate_project_files",
                        lambda: AssociationResult("associated"))
    preferences_mixin.PreferencesMixin._associate_project_files(Host())
    assert announced and announced[-1][0] == "ok"

    monkeypatch.setattr(association, "associate_project_files",
                        lambda: AssociationResult("failed", "Accès refusé"))
    preferences_mixin.PreferencesMixin._associate_project_files(Host())
    assert announced[-1][0] == "warn" and "Accès refusé" in announced[-1][1]


@pytest.mark.parametrize("language", ["fr", "en", "es"])
def test_the_association_texts_exist_in_every_language(language):
    from ui import i18n

    for key in ("prefs.files.title", "prefs.files.note", "prefs.files.button", "prefs.files.done_text",
                "prefs.files.failed_text"):
        assert i18n.translate_strict(key, language=language, error="accès refusé")
