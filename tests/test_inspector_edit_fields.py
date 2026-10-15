"""D5 / D6 : valider un champ de l'inspecteur ne replie pas son groupe ; « 0,5 » tapé donne 0,5 dans toutes les langues."""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from tools import ui_audit as audit
from ui import i18n


@pytest.fixture(scope="module")
def window(tmp_path_factory):
    from ui.main_window_mixins.project_files import ProjectFilesMixin

    patch = pytest.MonkeyPatch()
    base = tmp_path_factory.mktemp("edit_fields")
    patch.setenv("KUT_STUDIO_CONFIG_DIR", str(base / "config"))
    patch.setenv("KUT_STUDIO_CACHE_DIR", str(base / "cache"))
    patch.setenv("KUT_STUDIO_PROXY_DIR", str(base / "proxies"))
    patch.setenv("KUT_STUDIO_HARDWARE_ENCODING", "off")
    patch.setattr(ProjectFilesMixin, "_confirm_discard_changes", lambda self: True)
    main = audit.make_main_window(1280, 720, scopes=False)
    yield main
    i18n.set_language("fr")
    main.close()
    patch.undo()


def _opacity_spin(window):
    audit.select_first_clip(window)
    panel = window.properties_panel
    panel._select_inspector_tab(0)
    QApplication.processEvents()
    return panel, panel._spin_boxes["opacity"]


def _type(spin, text: str, finish) -> None:
    spin.setValue(1.0)
    spin.setFocus()
    spin.selectAll()
    QTest.keyClicks(spin, text)
    if finish == "leave":                # quitter le champ valide aussi la saisie
        spin.clearFocus()
        spin.interpretText()
    else:
        QTest.keyClick(spin, finish)


@pytest.mark.parametrize("language", ["fr", "en", "es"])
@pytest.mark.parametrize("typed", ["0,5", "0.5"])
@pytest.mark.parametrize("finish", [Qt.Key_Return, "leave"])
def test_typing_an_opacity_gives_that_opacity_in_every_language(window, language, typed, finish) -> None:
    i18n.set_language(language)
    _panel, spin = _opacity_spin(window)
    _type(spin, typed, finish)
    assert spin.value() == pytest.approx(0.5)
    QApplication.processEvents()
    assert spin.value() == pytest.approx(0.5)      # après le rafraîchissement de l'inspecteur par le modèle

def test_validating_a_field_keeps_its_group_open_and_the_panel_where_it_was(window) -> None:
    i18n.set_language("fr")
    panel, spin = _opacity_spin(window)
    assert panel.movement_group.is_open
    panel.scroll_area.ensureWidgetVisible(spin)
    QApplication.processEvents()
    bar = panel.scroll_area.verticalScrollBar()
    position = bar.value()
    _type(spin, "0,5", Qt.Key_Return)
    QApplication.processEvents()
    assert panel.movement_group.is_open
    assert bar.value() == position


def test_the_focused_header_still_folds_with_space_and_enter(window) -> None:
    panel, _spin = _opacity_spin(window)
    group = panel.movement_group
    group.setFocus()
    if not group.hasFocus():
        pytest.skip("le focus n'est pas livré hors écran")
    QTest.keyClick(group, Qt.Key_Space)
    assert not group.is_open
    QTest.keyClick(group, Qt.Key_Return)
    assert group.is_open


from PySide6.QtCore import QLocale  # noqa: E402
from PySide6.QtGui import QValidator  # noqa: E402

from ui.properties_widgets.tolerant_spin_box import TolerantDoubleSpinBox  # noqa: E402

_LOCALES = [QLocale(QLocale.French, QLocale.France), QLocale(QLocale.English, QLocale.UnitedStates),
            QLocale(QLocale.Spanish, QLocale.Spain), QLocale.c()]


def _spin(locale, low=0.0, high=1.0, decimals=2):
    spin = TolerantDoubleSpinBox()
    spin.setLocale(locale)
    spin.setRange(low, high)
    spin.setDecimals(decimals)
    spin.setKeyboardTracking(False)
    return spin


def _enter(spin, text: str) -> float:
    spin.setValue(spin.maximum())
    spin.selectAll()
    spin.lineEdit().setText("")
    QTest.keyClicks(spin.lineEdit(), text)
    spin.interpretText()
    return spin.value()


@pytest.mark.parametrize("locale", _LOCALES, ids=lambda loc: loc.name())
@pytest.mark.parametrize("typed", ["0,5", "0.5", ",5", ".5", "0,5 ", " 0.5"])
def test_both_separators_give_the_same_value_in_every_locale(qtbot, locale, typed) -> None:
    spin = _spin(locale)
    qtbot.addWidget(spin)
    assert _enter(spin, typed) == pytest.approx(0.5)


@pytest.mark.parametrize("locale", _LOCALES, ids=lambda loc: loc.name())
def test_signs_trailing_separator_and_out_of_range(qtbot, locale) -> None:
    spin = _spin(locale, -5.0, 5.0, 1)
    qtbot.addWidget(spin)
    assert _enter(spin, "-0,5") == pytest.approx(-0.5)
    assert _enter(spin, "2,") == pytest.approx(2.0)
    for pasted in ("99,5", "-99.5"):      # collage hors intervalle : refusé, la valeur précédente (2,0) reste
        spin.lineEdit().setText(pasted)
        spin.interpretText()
        assert spin.value() == pytest.approx(2.0)


@pytest.mark.parametrize("locale", _LOCALES, ids=lambda loc: loc.name())
def test_two_separators_are_refused_cleanly(qtbot, locale) -> None:
    spin = _spin(locale, 0.0, 10.0, 2)
    qtbot.addWidget(spin)
    spin.setValue(4.0)
    # Frappe : le validateur refuse le second séparateur (« 1,2,3 » donne « 1,23 »). Collage : texte invalide, valeur gardée.
    assert spin.validate("1,2,3", 5)[0] == QValidator.Invalid
    spin.lineEdit().setText("1,2,3")
    spin.interpretText()
    assert spin.value() == pytest.approx(4.0)


@pytest.mark.parametrize("locale", _LOCALES, ids=lambda loc: loc.name())
def test_large_values_show_no_thousands_separator_and_roundtrip(qtbot, locale) -> None:
    spin = _spin(locale, -3600.0, 3600.0, 1)
    qtbot.addWidget(spin)
    spin.setValue(3600.0)
    assert "," not in spin.text().replace(locale.decimalPoint(), "") and " " not in spin.text()
    assert _enter(spin, "1234,5") == pytest.approx(1234.5)
