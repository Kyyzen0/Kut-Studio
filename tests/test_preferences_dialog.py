"""Régressions du dialogue Préférences."""

from __future__ import annotations

from PySide6.QtWidgets import QApplication

from core.user_settings import (
    DEFAULT_LANGUAGE,
    DEFAULT_PERFORMANCE_PROFILE,
    DEFAULT_PREVIEW_QUALITY,
    DEFAULT_RENDER_QUALITY,
    DEFAULT_THEME,
)
from ui import i18n
from ui.preferences_dialog import PreferencesDialog


def test_invalid_choices_fall_back_to_model_defaults(qtbot) -> None:
    dialog = PreferencesDialog(
        current_theme="invalid",
        current_language_code="invalid",
        current_performance="invalid",
        current_preview_quality="invalid",
        current_render_quality="invalid",
    )
    qtbot.addWidget(dialog)

    assert dialog._theme_radios[DEFAULT_THEME].isChecked()
    assert dialog._language_radios[DEFAULT_LANGUAGE].isChecked()
    assert dialog._performance_radios[DEFAULT_PERFORMANCE_PROFILE].isChecked()
    assert dialog._preview_radios[DEFAULT_PREVIEW_QUALITY].isChecked()
    assert dialog._render_radios[DEFAULT_RENDER_QUALITY].isChecked()


def test_missing_language_uses_current_application_language(qtbot) -> None:
    original = i18n.current_language()
    try:
        i18n.set_language("es")
        dialog = PreferencesDialog(current_language_code=None)
        qtbot.addWidget(dialog)

        assert dialog._language_radios["es"].isChecked()
    finally:
        i18n.set_language(original)


def test_radio_click_keeps_public_handler_and_signal_compatible(qtbot) -> None:
    dialog = PreferencesDialog(current_render_quality="high")
    qtbot.addWidget(dialog)
    seen: list[str] = []
    dialog.render_quality_changed.connect(seen.append)

    dialog._render_radios["draft"].click()
    dialog._on_render_chosen(dialog._render_radios["standard"])

    assert seen == ["draft", "standard"]


def test_open_dialog_retranslates_and_unsubscribes_on_close(qtbot) -> None:
    original = i18n.current_language()
    try:
        i18n.set_language("fr")
        dialog = PreferencesDialog(current_language_code="fr")
        qtbot.addWidget(dialog)
        callback = dialog._i18n_callback
        assert callback in i18n._subscribers

        i18n.set_language("en")
        assert dialog.windowTitle() == i18n.translate("prefs.title")
        assert dialog.close_button.text() == "Close"

        dialog.reject()
        QApplication.processEvents()
        assert callback not in i18n._subscribers
    finally:
        i18n.set_language(original)

