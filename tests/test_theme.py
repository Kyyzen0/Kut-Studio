"""Tests de régression pour l'application du thème global."""

import pytest

from ui.theme import ThemeManager


class _FakeApplication:
    """Double minimal de QApplication pour vérifier les appels de style."""

    def __init__(self) -> None:
        self.properties: dict[str, str] = {}
        self.stylesheet_calls: list[str] = []

    def property(self, name: str):
        return self.properties.get(name)

    def setProperty(self, name: str, value: str) -> None:
        self.properties[name] = value

    def setStyleSheet(self, stylesheet: str) -> None:
        self.stylesheet_calls.append(stylesheet)


def test_apply_to_skips_an_identical_application_stylesheet() -> None:
    app = _FakeApplication()
    manager = ThemeManager("dark")

    manager.apply_to(app)
    manager.apply_to(app)

    assert len(app.stylesheet_calls) == 1


@pytest.mark.usefixtures("restore_global_theme")   # ``set_mode("light")`` publie la palette claire pour tout le processus
def test_apply_to_replaces_stylesheet_when_theme_changes() -> None:
    app = _FakeApplication()
    manager = ThemeManager("dark")

    manager.apply_to(app)
    manager.set_mode("light")
    manager.apply_to(app)

    assert len(app.stylesheet_calls) == 2
    assert app.stylesheet_calls[0] != app.stylesheet_calls[1]


def test_dialogs_get_an_explicit_themed_background():
    """Le fond d'un dialogue ne doit pas dépendre du thème natif de l'OS."""
    from ui.theme import THEMES, global_stylesheet

    for palette in THEMES.values():
        sheet = global_stylesheet(palette)
        assert f"QDialog {{ background: {palette.background}; }}" in sheet
