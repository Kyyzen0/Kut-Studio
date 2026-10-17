"""Bascule du niveau d'usage (lot 2) : Préférences, sous-menu Fenêtre, puce de la barre du haut, synchronisés."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import unicodedata
from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtWidgets import QRadioButton
from test_scopes import _window

from core.usage_level import UsageLevel
from core.user_settings import UserSettings, load_user_settings, save_user_settings
from ui import i18n
from ui.preferences_dialog import _CHOICE_GROUPS, PreferencesDialog

ROOT = Path(__file__).resolve().parent.parent
LEVELS = ("simple", "studio", "advanced")


def _seed(tmp_path, monkeypatch, level: str | None):
    monkeypatch.setenv("KUT_STUDIO_CONFIG_DIR", str(tmp_path))
    if level is not None:
        save_user_settings(replace(UserSettings(), usage_level=level))


def _checked(window) -> list[str]:
    return [level.value for level, action in window._usage_level_actions.items() if action.isChecked()]


def _assert_all_show(window, level: str, dialog=None) -> None:
    assert window.usage_level.value == level
    assert _checked(window) == [level]
    assert window.usage_level_chip.text() == i18n.translate(f"prefs.usage.{level}")
    if dialog is not None:
        assert dialog.current_usage_level == level
        assert [c for c, r in dialog._usage_radios.items() if r.isChecked()] == [level]


# --- état initial ---------------------------------------------------------------------------------


def test_initial_state_follows_the_loaded_level(qtbot, monkeypatch, tmp_path):
    for level in LEVELS:
        _seed(tmp_path / level, monkeypatch, level)
        window = _window(qtbot, monkeypatch, tmp_path / level)
        _assert_all_show(window, level)


def test_fresh_install_shows_simple_and_old_file_shows_advanced(qtbot, monkeypatch, tmp_path):
    fresh = _window(qtbot, monkeypatch, tmp_path / "fresh")
    _assert_all_show(fresh, "simple")
    old = tmp_path / "old"
    old.mkdir()
    (old / "user_settings.json").write_text('{"theme_mode": "dark"}', encoding="utf-8")
    _assert_all_show(_window(qtbot, monkeypatch, old), "advanced")


# --- chaque emplacement met à jour les deux autres -----------------------------------------------------------------


@pytest.mark.parametrize("target", ["studio", "simple"])
def test_the_window_menu_updates_model_chip_dialog_and_file(qtbot, monkeypatch, tmp_path, target):
    _seed(tmp_path, monkeypatch, "advanced")
    window = _window(qtbot, monkeypatch, tmp_path)
    dialog = window_dialog(window, qtbot)
    seen: list[str] = []
    window.usage_level_changed.connect(seen.append)
    window._usage_level_actions[UsageLevel(target)].trigger()
    _assert_all_show(window, target, dialog)
    assert seen == [target]
    assert load_user_settings(tmp_path).usage_level == target


def window_dialog(window, qtbot):
    """Boîte Préférences branchée comme ``show_preferences`` (sans ``exec`` bloquant)."""
    import ui.main_window_mixins.preferences as prefs

    created = {}
    original = PreferencesDialog.exec

    def fake_exec(self):
        created["dialog"] = self
        qtbot.addWidget(self)
        return 0

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(PreferencesDialog, "exec", fake_exec)
        window.show_preferences()
    del original, prefs
    return created["dialog"]


def test_the_chip_menu_updates_the_others(qtbot, monkeypatch, tmp_path):
    _seed(tmp_path, monkeypatch, "advanced")
    window = _window(qtbot, monkeypatch, tmp_path)
    dialog = window_dialog(window, qtbot)
    seen: list[str] = []
    window.usage_level_changed.connect(seen.append)
    chip_actions = {a.objectName(): a for a in window.usage_level_chip.menu().actions()}
    assert set(chip_actions) == {f"usage_level_{v}" for v in LEVELS}
    chip_actions["usage_level_studio"].trigger()
    _assert_all_show(window, "studio", dialog)
    assert seen == ["studio"] and load_user_settings(tmp_path).usage_level == "studio"
    # Le sous-menu Fenêtre et la puce portent les mêmes actions : l'état coché est celui du modèle.
    assert [a.objectName() for a in window.usage_level_menu.actions()] == [f"usage_level_{v}" for v in LEVELS]


def test_the_preferences_dialog_updates_the_others(qtbot, monkeypatch, tmp_path):
    _seed(tmp_path, monkeypatch, "advanced")
    window = _window(qtbot, monkeypatch, tmp_path)
    dialog = window_dialog(window, qtbot)
    seen: list[str] = []
    window.usage_level_changed.connect(seen.append)
    dialog._usage_radios["simple"].click()
    _assert_all_show(window, "simple", dialog)
    assert seen == ["simple"] and load_user_settings(tmp_path).usage_level == "simple"
    dialog._usage_radios["simple"].click()                      # même choix : rien de plus
    assert seen == ["simple"]


def test_a_change_from_outside_the_ui_is_followed_everywhere(qtbot, monkeypatch, tmp_path):
    _seed(tmp_path, monkeypatch, "simple")
    window = _window(qtbot, monkeypatch, tmp_path)
    dialog = window_dialog(window, qtbot)
    assert window.set_usage_level("advanced")
    _assert_all_show(window, "advanced", dialog)
    assert window.set_usage_level(UsageLevel.STUDIO)
    _assert_all_show(window, "studio", dialog)


def test_no_confirmation_dialog_on_change(qtbot, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QMessageBox

    _seed(tmp_path, monkeypatch, "advanced")
    window = _window(qtbot, monkeypatch, tmp_path)
    calls = []
    for name in ("question", "warning", "information", "critical"):
        monkeypatch.setattr(QMessageBox, name, lambda *a, **k: calls.append(name))
    monkeypatch.setattr(window.statusBar(), "showMessage", lambda *a, **k: calls.append("status"))
    window._usage_level_actions[UsageLevel.SIMPLE].trigger()
    assert calls == []


def test_the_level_survives_a_simulated_restart(qtbot, monkeypatch, tmp_path):
    _seed(tmp_path, monkeypatch, "advanced")
    window = _window(qtbot, monkeypatch, tmp_path)
    window._usage_level_actions[UsageLevel.STUDIO].trigger()
    reopened = _window(qtbot, monkeypatch, tmp_path)
    _assert_all_show(reopened, "studio")


def test_changing_the_level_keeps_the_level_choices_and_shortens_the_window_menu(qtbot, monkeypatch, tmp_path):
    _seed(tmp_path, monkeypatch, "advanced")
    window = _window(qtbot, monkeypatch, tmp_path)
    window.show()
    before = {id(w): w.isVisible() for w in window.findChildren(QRadioButton)}
    entries = [a.text() for a in window.window_menu.actions()]
    window.set_usage_level("simple")
    assert {id(w): w.isVisible() for w in window.findChildren(QRadioButton)} == before
    shorter = [a.text() for a in window.window_menu.actions()]
    assert 0 < len(shorter) < len(entries) and set(shorter) <= set(entries)      # le lot 4 retire des entrées (voir test_usage_level_menus)
    window.set_usage_level("advanced")
    assert [a.text() for a in window.window_menu.actions()] == entries


# --- boîte Préférences ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("current", [*LEVELS, "expert", "", None, 3])
def test_the_dialog_always_has_exactly_one_checked_level(qtbot, current):
    dialog = PreferencesDialog(current_usage_level=current)  # type: ignore[arg-type]
    qtbot.addWidget(dialog)
    checked = [c for c, r in dialog._usage_radios.items() if r.isChecked()]
    assert len(checked) == 1
    assert checked == [current if current in LEVELS else "simple"]
    assert tuple(dialog._usage_radios) == LEVELS


def test_the_usage_group_is_declared_with_the_other_choice_groups():
    group = next(g for g in _CHOICE_GROUPS if g.key == "usage")
    assert group.codes == LEVELS and group.signal_name == "usage_level_changed" and group.hints


def test_the_dialog_levels_have_a_short_hint_in_every_language(qtbot):
    dialog = PreferencesDialog(current_usage_level="studio")
    qtbot.addWidget(dialog)
    for language in ("fr", "en", "es"):
        i18n.set_language(language)
        dialog._retranslate()
        for level, radio in dialog._usage_radios.items():
            assert radio.text() == i18n.translate(f"prefs.usage.{level}")
            assert 0 < len(radio.toolTip()) <= 60
    i18n.set_language("fr")


def test_restoring_defaults_keeps_the_level_in_the_dialog_and_the_window(qtbot, monkeypatch, tmp_path):
    _seed(tmp_path, monkeypatch, "studio")
    window = _window(qtbot, monkeypatch, tmp_path)
    dialog = window_dialog(window, qtbot)
    dialog._on_restore_defaults()
    _assert_all_show(window, "studio", dialog)


def test_closing_the_dialog_does_not_change_the_level(qtbot, monkeypatch, tmp_path):
    _seed(tmp_path, monkeypatch, "studio")
    window = _window(qtbot, monkeypatch, tmp_path)
    dialog = window_dialog(window, qtbot)
    dialog.reject()
    assert window.usage_level is UsageLevel.STUDIO
    window.set_usage_level("advanced")          # le dialogue fermé n'est plus joint : aucune erreur
    assert load_user_settings(tmp_path).usage_level == "advanced"


# --- menu Fenêtre : mnémoniques, raccourcis ------------------------------------------------------------------------


def _mnemonic(title: str) -> str | None:
    match = re.search(r"(?<!&)&(?!&)(.)", title)
    if match is None:
        return None
    return unicodedata.normalize("NFD", match.group(1)).encode("ascii", "ignore").decode("ascii").casefold()


@pytest.mark.parametrize("language", ["fr", "en", "es"])
def test_level_mnemonics_are_unique_and_present(language):
    original = i18n.current_language()
    try:
        i18n.set_language(language)
        letters = [_mnemonic(i18n.translate(f"usage.menu.{level}")) for level in LEVELS]
        assert all(letters) and len(set(letters)) == 3, letters
    finally:
        i18n.set_language(original)


def test_the_levels_are_exclusive_checkable_actions_without_default_shortcut(qtbot, monkeypatch, tmp_path):
    _seed(tmp_path, monkeypatch, "advanced")
    window = _window(qtbot, monkeypatch, tmp_path)
    actions = list(window._usage_level_actions.values())
    assert all(a.isCheckable() and a.shortcut().isEmpty() for a in actions)
    groups = {a.actionGroup() for a in actions}
    assert len(groups) == 1 and groups.pop().isExclusive()
    assert window.usage_level_menu in [a.menu() for a in window.window_menu.actions()]


# --- retraduction à chaud ---------------------------------------------------------------------------------------


def test_the_three_places_follow_the_language(qtbot, monkeypatch, tmp_path):
    _seed(tmp_path, monkeypatch, "studio")
    window = _window(qtbot, monkeypatch, tmp_path)
    dialog = window_dialog(window, qtbot)
    try:
        seen = {}
        for language in ("fr", "en", "es"):
            window._apply_settings(replace(window._settings_snapshot(), language=language))
            seen[language] = (
                window.usage_level_menu.title(),
                window._usage_level_actions[UsageLevel.ADVANCED].text(),
                window.usage_level_chip.accessibleName(),
                window.usage_level_chip.toolTip(),
                dialog._usage_radios["advanced"].text(),
                dialog._group_boxes["usage"].title(),
            )
            assert seen[language][0] == i18n.translate("usage.menu.title")
            assert seen[language][1] == i18n.translate("usage.menu.advanced")
            assert seen[language][2] == i18n.translate("usage.chip.name", level="Studio")
            assert seen[language][4] == i18n.translate("prefs.usage.advanced")
            assert seen[language][5] == i18n.translate("prefs.usage")
        assert len({v[2] for v in seen.values()}) == 3 and len({v[1] for v in seen.values()}) == 3
    finally:
        window._apply_settings(replace(window._settings_snapshot(), language="fr"))


def test_the_chip_has_an_accessible_name_that_follows_the_level(qtbot, monkeypatch, tmp_path):
    _seed(tmp_path, monkeypatch, "advanced")
    window = _window(qtbot, monkeypatch, tmp_path)
    chip = window.usage_level_chip
    assert chip.accessibleName() == i18n.translate("usage.chip.name", level=i18n.translate("prefs.usage.advanced"))
    window.set_usage_level("simple")
    assert chip.accessibleName() == i18n.translate("usage.chip.name", level=i18n.translate("prefs.usage.simple"))
    assert chip.toolTip()


# --- la barre du haut tient à 1180 px, à +20 % de police ---------------------------------------------------------------

_TOP_BAR_SCRIPT = """
SCALE = {scale}
from tools import ui_audit as audit
window = audit.make_main_window(1180, 720, font_scale=SCALE)
bar = window.findChild(type(window.export_button.parentWidget()), "main_topbar")
chip = window.usage_level_chip
assert bar.isVisible() and bar.width() <= 1180
# La puce a trois formes (complète, abrégée, absente) selon la place : jamais au détriment des boutons voisins.
assert chip.form() in ("full", "compact", "hidden") and chip.isVisible() == (chip.form() != "hidden")
if SCALE <= 1.0:
    assert chip.form() == "full" and chip.text() == chip.full_text()
if chip.isVisible():
    assert chip.geometry().right() < window.settings_button.geometry().left() + 1
    for button in (window.settings_button, window.export_button):
        assert button.width() >= button.sizeHint().width() - 1, (button.text(), button.width(), button.sizeHint().width())
if SCALE >= 1.45:
    assert chip.form() == "hidden"       # polices de repli larges (Linux) : la barre reste celle d'avant
assert window.settings_button.geometry().right() < window.export_button.geometry().left() + 1
assert window.export_button.geometry().right() <= bar.width()
found = [str(f) for f in audit.sibling_overlaps(bar) + audit.parent_overflow(bar)]
assert not found, found
print("ok")
"""


# 1,2 : le critère du dépôt ; 1,45 : approche les polices de repli de Linux (Debian CI : « Réglages » à 86 px pour 140 voulus).
@pytest.mark.parametrize("scale", [1.0, 1.2, 1.45])
def test_top_bar_fits_with_the_chip_at_the_smallest_size_and_wider_fonts(tmp_path, scale):
    env = {
        **os.environ, "QT_QPA_PLATFORM": "offscreen", "KUT_STUDIO_CONFIG_DIR": str(tmp_path / "config"),
        "KUT_STUDIO_CACHE_DIR": str(tmp_path / "cache"), "KUT_STUDIO_PROXY_DIR": str(tmp_path / "proxies"),
        "KUT_STUDIO_HARDWARE_ENCODING": "off",
    }
    done = subprocess.run([sys.executable, "-c", _TOP_BAR_SCRIPT.replace("{scale}", str(scale))], cwd=ROOT, env=env, capture_output=True, text=True,
                          timeout=600, check=False)
    assert done.returncode == 0 and done.stdout.strip().endswith("ok"), f"{done.stdout[-1500:]}\n{done.stderr[-1500:]}"
