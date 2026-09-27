"""Tests unitaires de ``ui.i18n`` (tâche 14)."""

from __future__ import annotations

import pytest

from ui import i18n
from ui.i18n import (
    DEFAULT_LANGUAGE,
    available_languages,
    current_language,
    reset_for_tests,
    set_language,
    translate,
)


# Réinitialiser l'état entre chaque test : la langue est une variable
# globale et plusieurs tests la modifient.
@pytest.fixture(autouse=True)
def _i18n_reset():
    reset_for_tests()
    yield
    reset_for_tests()


# ---------------------------------------------------------------------------
# État initial
# ---------------------------------------------------------------------------


def test_default_language_is_french():
    assert DEFAULT_LANGUAGE == "fr"
    assert current_language() == "fr"


def test_available_languages_contains_fr_en_es():
    assert set(available_languages()) == {"fr", "en", "es"}


# ---------------------------------------------------------------------------
# Traduction
# ---------------------------------------------------------------------------


def test_translate_uses_current_language():
    set_language("en")
    assert translate("action.open") == "Open…"
    set_language("fr")
    assert translate("action.open") == "Ouvrir…"
    set_language("es")
    assert translate("action.open") == "Abrir…"


def test_translate_unknown_key_returns_marker():
    out = translate("does.not.exist")
    assert out.startswith("[") and out.endswith("]")
    assert "does.not.exist" in out


def test_translate_supports_format_substitution():
    out = translate("preview.fade", seconds=0.5)
    assert "0.5" in out
    assert "{seconds}" not in out


def test_translate_key_missing_in_language_falls_back_to_default():
    """Une clé présente uniquement dans la langue par défaut doit
    être renvoyée si on la demande dans une autre langue non chargée.
    """
    from ui import i18n as i18n_module

    test_key = "_test_only_fallback_key"
    i18n_module._TRANSLATIONS[test_key] = {
        "fr": "valeur-fr",
        "en": "fallback to default",
    }
    set_language("en")
    # L'entrée anglaise existe → on récupère la valeur anglaise.
    assert translate(test_key) == "fallback to default"
    set_language("es")
    # L'entrée espagnole n'existe pas → fallback ``fr``.
    assert translate(test_key) == "valeur-fr"


# ---------------------------------------------------------------------------
# Changement runtime
# ---------------------------------------------------------------------------


def test_set_language_ignores_unknown_codes():
    assert set_language("jp") is False
    assert current_language() == "fr"


def test_set_language_callback_is_invoked_on_change():
    codes: list[str] = []

    def listener(code: str) -> None:
        codes.append(code)

    i18n.subscribe(listener)
    assert set_language("en") is True
    assert set_language("en") is False  # même valeur : pas d'appel.
    assert set_language("es") is True
    assert codes == ["en", "es"]
    i18n.unsubscribe(listener)


def test_unsubscribe_stops_callbacks():
    called = []

    def listener(code: str) -> None:
        called.append(code)

    i18n.subscribe(listener)
    set_language("en")
    i18n.unsubscribe(listener)
    set_language("es")
    assert called == ["en"]


def test_subscriber_exception_does_not_break_set_language():
    def bad_listener(code: str) -> None:
        raise RuntimeError("boom")

    i18n.subscribe(bad_listener)
    # Si ``set_language`` continue malgré l'exception, le second appel
    # confirme que la langue a effectivement été changée.
    assert set_language("en") is True
    assert current_language() == "en"


# ---------------------------------------------------------------------------
# Roundtrip des libellés principaux
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", [
    "app.title",
    "action.new_project",
    "action.open",
    "action.save",
    "action.undo",
    "action.redo",
    "tracks.add_video_long",
    "tracks.add_audio_long",
    "tracks.add_subtitle_long",
    "tracks.remove",
    "tracks.move_up",
    "tracks.move_down",
    "panel.preview",
    "panel.timeline",
    "panel.properties",
    "panel.project",
    "menu.file",
    "menu.edit",
    "menu.timeline",
    "prefs.title",
    "prefs.theme.dark",
    "prefs.theme.light",
    "prefs.theme.system",
    "prefs.language",
])
def test_traductions_disponibles_dans_toutes_langues(key):
    set_language("fr")
    fr = translate(key)
    set_language("en")
    en = translate(key)
    set_language("es")
    es = translate(key)
    assert fr and en and es
    assert fr != f"[{key}]" and en != f"[{key}]" and es != f"[{key}]"


def test_translate_does_not_mutate_project_state():
    """L'i18n ne doit avoir aucun effet de bord sur quoi que ce soit."""
    set_language("fr")
    state = current_language()
    translate("action.open")
    translate("does.not.exist")
    assert current_language() == state
