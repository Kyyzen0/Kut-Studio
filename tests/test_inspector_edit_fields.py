"""D5 : valider un champ de l'inspecteur ne replie pas son groupe."""

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
