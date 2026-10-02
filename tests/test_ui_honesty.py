"""L'interface ne ment pas : pas d'entrée active qui ne fait rien, pas de bouton sans nom.

Régressions de l'audit UI : « Couper / Copier / Coller », « Ajouter un clip », « Couper / Réduire » et
« Marqueur » étaient actifs mais n'appelaient qu'un ``print`` (invisible dans l'application empaquetée) ;
les boutons-icônes n'avaient qu'une infobulle, sans nom accessible, et les pastilles de couleur ni l'un ni l'autre.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QAbstractButton, QLineEdit, QMenu, QMenuBar
from test_scopes import _window

from ui import i18n
from ui.icons import IconButton, IconName


def _actions(window):
    """Toutes les entrées des menus de la barre, par libellé (les ``QMenu`` sont rattachés à la fenêtre)."""
    menus = [bar_action.menu() for bar_action in window.menuBar().actions() if isinstance(bar_action.menu(), QMenu)]
    return {action.text(): action for menu in menus for action in menu.actions() if action.text()}


@pytest.mark.parametrize("key", ["action.cut", "menu.item.copy", "menu.item.paste", "menu.item.add_clip"])
def test_entries_without_a_function_are_greyed_out_with_an_explanation(qtbot, monkeypatch, key):
    window = _window(qtbot, monkeypatch)
    action = _actions(window)[i18n.translate(key)]
    assert not action.isEnabled()
    assert action.toolTip() == i18n.translate("status.unavailable")


def test_the_marker_entry_adds_a_marker_and_the_trim_entry_cuts_at_the_playhead(qtbot, monkeypatch):
    window = _window(qtbot, monkeypatch)
    calls = []
    monkeypatch.setattr(window, "add_marker_at", lambda seconds: calls.append(("marker", seconds)))
    monkeypatch.setattr(window, "cut_at_playhead", lambda: calls.append(("cut", None)))
    window.playhead_seconds = 2.5
    actions = _actions(window)
    assert actions[i18n.translate("menu.item.marker")].isEnabled()
    actions[i18n.translate("menu.item.marker")].trigger()
    actions[i18n.translate("menu.item.trim")].trigger()
    assert calls == [("marker", 2.5), ("cut", None)]


def test_an_icon_only_button_is_announced_by_its_tooltip(qtbot):
    button = IconButton(IconName.PLAY, tooltip="Lecture")
    qtbot.addWidget(button)
    assert button.accessibleName() == "Lecture"
    button.setToolTip("Play")                                  # retraduction à chaud
    assert button.accessibleName() == "Play"


def test_a_button_with_text_keeps_its_text_as_its_name(qtbot):
    button = IconButton(IconName.PLAY, text="Exporter", tooltip="Exporter la séquence")
    qtbot.addWidget(button)
    assert button.accessibleName() == ""                       # Qt retombe sur le texte du bouton
    assert button.text() == "Exporter"


def test_every_icon_only_button_of_the_main_window_has_a_name(qtbot, monkeypatch):
    window = _window(qtbot, monkeypatch)
    def qt_internal(button) -> bool:
        """Extension de la barre de menus, bouton d'effacement d'un champ de recherche : fournis par Qt."""
        return isinstance(button.parent(), (QMenuBar, QLineEdit))

    unnamed = [button.objectName() or type(button).__name__
               for button in window.findChildren(QAbstractButton)
               if not qt_internal(button) and not button.text() and not button.accessibleName()
               and not button.toolTip()]
    assert not unnamed, f"{len(unnamed)} boutons sans texte, sans infobulle et sans nom : {unnamed[:8]}"


def test_a_colour_swatch_is_named():
    from ui.graphics_editor import _ColorField

    field = _ColorField("#336699")
    assert field.swatch.accessibleName() == i18n.translate("graphics.color.pick")
    assert field.swatch.toolTip() == i18n.translate("graphics.color.pick")


def test_undo_redo_tooltips_show_the_real_shortcut_and_follow_a_rebinding(qtbot, monkeypatch):
    window = _window(qtbot, monkeypatch)
    undo_hint = window.shortcuts.hint("undo")
    assert undo_hint and undo_hint in window.undo_button.toolTip()
    assert window.shortcuts.hint("redo") in window.redo_button.toolTip()
    window.shortcuts.assign("undo", 0, "F7")
    assert "F7" in window.undo_button.toolTip()
    assert undo_hint not in window.undo_button.toolTip()
    window.shortcuts.assign("undo", 0, None)                      # plus de raccourci : plus de parenthèse
    assert window.shortcuts.hint("undo") == ""
    assert window.undo_button.toolTip() == i18n.translate("action.undo")


def test_the_export_tooltip_does_not_promise_a_shortcut_that_does_not_exist(qtbot, monkeypatch):
    window = _window(qtbot, monkeypatch)
    tooltip = window.export_button.toolTip()
    assert "⌘" not in tooltip and "Ctrl" not in tooltip
    # Si un jour une commande d'export existe, l'infobulle devra la citer ; en attendant, Ctrl+E fait autre chose.
    assert window.shortcuts.shortcut_map.sequences("toggle_clip_enabled") == ("Ctrl+E",)
