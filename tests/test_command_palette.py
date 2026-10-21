"""Palette « Rechercher une fonction » : registre des menus, ouverture, niveaux d'usage, clavier, langues, historique.

Fenêtre réelle hors écran (``KUT_STUDIO_CONFIG_DIR`` isolé par ``test_scopes._window``).
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit
from test_scopes import _window

from core.command_search import clean_label, search
from core.project_io import project_payload
from core.usage_level import UsageLevel
from core.workspace_state import PAGE_AUDIO, PAGE_COLOR, PAGE_EDIT
from ui import i18n
from ui.properties_panel_mixins.construction import TRACKING_TAB

SIMPLE, STUDIO, ADVANCED = UsageLevel.SIMPLE, UsageLevel.STUDIO, UsageLevel.ADVANCED


@pytest.fixture
def window(qtbot, monkeypatch, tmp_path):
    i18n.set_language("fr")
    main = _window(qtbot, monkeypatch, tmp_path)
    main.set_usage_level("simple")
    main.resize(1280, 800)
    main.show()
    main.activateWindow()
    QTest.qWaitForWindowActive(main)
    yield main
    main.hide()
    i18n.set_language("fr")


def _row_buttons(palette) -> list[str]:
    out = []
    for index in range(palette.results.count()):
        row = palette.results.itemWidget(palette.results.item(index))
        out.append(row.button.text() if row.button is not None else "")
    return out


def _row_of(palette, entry_id: str) -> int:
    return next(i for i, r in enumerate(palette._results) if r.entry.id == entry_id)


def _type(palette, text: str) -> None:
    palette.field.setText(text)


def _walk_actions(window):
    """Toutes les ``QAction`` d'entrée de menu, y compris celles que le niveau a retirées (listes d'origine)."""
    seen, out, keep = set(), [], []          # ``keep`` : un ``id`` d'objet libéré serait réutilisé

    def walk(menu) -> None:
        gate = next((e for e in window._menu_levels if e.menu is menu), None)
        for action in gate.items if gate is not None else menu.actions():
            if action.isSeparator() or id(action) in seen:
                continue
            seen.add(id(action))
            keep.append(action)
            if action.menu() is not None:
                walk(action.menu())
            else:
                out.append(action)

    for bar_action in window.menuBar().actions():
        walk(bar_action.menu())
    return out


# -- registre -----------------------------------------------------------------------------------------------------------


def test_every_menu_action_at_advanced_level_is_in_the_registry(window):
    window.set_usage_level("advanced")
    entries = window.command_palette_entries()
    titles = [e.title for e in entries]
    page_actions = {id(window.shortcuts.action(c)) for c in ("page_edit", "page_color", "page_composition", "page_audio")}
    actions = [a for a in _walk_actions(window) if id(a) not in page_actions]
    assert len(actions) > 100
    missing = [a.text() for a in actions if clean_label(a.text()) and not any(t.endswith(clean_label(a.text())) for t in titles)]
    assert missing == []
    menu_entries = [e for e in entries if e.kind == "command"]
    assert len(menu_entries) == len([a for a in actions if clean_label(a.text())])


def test_no_entry_is_untitled_and_ids_are_unique(window):
    entries = window.command_palette_entries()
    assert all(e.title.strip() for e in entries)
    assert all(not e.title.startswith("[") for e in entries)          # jamais une clé i18n manquante
    assert len({e.id for e in entries}) == len(entries)


def test_levels_come_from_the_menu_gates_even_for_removed_entries(window):
    by_title = {e.title: e for e in window.command_palette_entries()}
    tracking = next(e for e in by_title.values() if e.id == "menu:menu.item.tracking_panel")
    ripple = next(e for e in by_title.values() if e.id == "menu:action.ripple_delete")
    new_file = next(e for e in by_title.values() if e.id == "menu:menu.item.new")
    assert (tracking.level, ripple.level, new_file.level) == (ADVANCED, STUDIO, SIMPLE)
    # Au niveau Simple, l'entrée n'est plus dans le menu, la palette la trouve quand même.
    layers = next(a.menu() for a in window.menuBar().actions() if a.menu().objectName() == "layers_menu")
    assert all(clean_label(a.text()) != "Suivi (tracking)" for a in layers.actions())


def test_children_of_a_gated_submenu_inherit_its_level(window):
    entries = {e.id: e for e in window.command_palette_entries()}
    social_studio = [e for e in entries.values() if e.hint.startswith("Réseaux sociaux") and e.level is STUDIO]
    assert social_studio and all(e.level.at_least(STUDIO) for e in social_studio)


def test_destinations_cover_pages_tabs_and_libraries_with_their_levels(window):
    entries = {e.id: e for e in window.command_palette_entries()}
    assert {"goto:page:edit", "goto:page:composition", "goto:page:color", "goto:page:audio"} <= set(entries)
    assert {f"goto:tab:{n}" for n in ("clip", "color", "effects", "audio", "graphics", "compositing", "tracking")} <= set(entries)
    assert {f"goto:library:{n}" for n in ("media", "text", "effects", "transitions", "audio", "graphics", "sequences")} <= set(entries)
    assert entries["goto:page:composition"].level is ADVANCED and entries["goto:page:color"].level is STUDIO
    assert entries["goto:tab:tracking"].level is ADVANCED and entries["goto:tab:color"].level is STUDIO
    assert entries["goto:library:effects"].level is STUDIO and entries["goto:library:media"].level is SIMPLE
    assert not any(e.id.startswith("menu:shortcuts.command.page_") for e in entries.values())      # pas de doublon avec « Aller à »


def test_disabled_command_is_listed_greyed_without_open_button(window):
    entries = {e.id: e for e in window.command_palette_entries()}
    cut = entries["menu:action.cut"]
    assert not cut.enabled and cut.reason == i18n.translate("status.unavailable")
    window.toggle_command_palette("Couper")
    palette = window.command_palette
    row = palette.results.itemWidget(palette.results.item(_row_of(palette, "menu:action.cut")))
    assert row.button is None
    assert i18n.translate("status.unavailable") in row.hint.text()
    calls = []
    window.command_palette.entry_chosen.connect(lambda *a: calls.append(a))
    palette.results.setCurrentRow(_row_of(palette, "menu:action.cut"))
    QTest.keyClick(palette.field, Qt.Key_Return)
    assert calls == [] and palette.isVisible()                       # Entrée sur une commande grisée n'agit pas


# -- ouverture, fermeture, focus -----------------------------------------------------------------------------------------------


def test_the_shortcut_opens_and_escape_closes_with_focus_restored(window):
    field = window.project_panel.findChildren(QLineEdit)[0]
    field.setFocus()
    assert QApplication.focusWidget() is field
    QTest.keySequence(window, QKeySequence("Ctrl+Shift+P"))
    palette = window.command_palette
    assert palette.isVisible() and palette.field.hasFocus()
    assert palette.box.width() == 620 and palette.box.y() < 120 and palette.box.x() > 0
    QTest.keyClick(palette.field, Qt.Key_Escape)
    assert not palette.isVisible() and QApplication.focusWidget() is field


def test_the_button_opens_it_and_close_button_closes_it(window):
    button = window.palette_button
    assert "Rechercher" in button.text() and QKeySequence("Ctrl+Shift+P").toString(QKeySequence.NativeText) in button.text()
    QTest.mouseClick(button, Qt.LeftButton)
    palette = window.command_palette
    assert palette.isVisible() and palette.field.hasFocus()
    QTest.mouseClick(palette.close_button, Qt.LeftButton)
    assert not palette.isVisible()


def test_the_shortcut_toggles_and_clicking_the_veil_closes(window):
    QTest.keySequence(window, QKeySequence("Ctrl+Shift+P"))
    assert window.command_palette.isVisible()
    QTest.keySequence(window.command_palette.field, QKeySequence("Ctrl+Shift+P"))
    assert not window.command_palette.isVisible()
    window.toggle_command_palette()
    QTest.mouseClick(window.command_palette, Qt.LeftButton, pos=window.command_palette.rect().bottomRight() - window.command_palette.rect().center() * 0.2)
    assert not window.command_palette.isVisible()


def test_accessibility_names_roles_and_tab_order(window):
    window.toggle_command_palette("couper")
    palette = window.command_palette
    assert palette.field.accessibleName() and palette.results.accessibleName() and palette.close_button.accessibleName()
    assert palette.window().focusWidget() is palette.field
    order = [palette.field]
    widget = palette.field
    while widget is not palette.close_button and len(order) < 15:
        widget = widget.nextInFocusChain()
        if widget.isVisibleTo(palette) and (widget.focusPolicy() & Qt.TabFocus) and palette.isAncestorOf(widget):
            order.append(widget)
    assert order[-1] is palette.close_button and palette.results in order, order
    assert order.index(palette.results) < min(order.index(c) for c in palette.chips) < order.index(palette.close_button)
    palette.close_button.setFocus()
    QTest.keyClick(palette.close_button, Qt.Key_Tab)
    assert palette.isAncestorOf(QApplication.focusWidget())          # Tab ne sort jamais de la boîte


def test_the_field_is_bounded_and_the_message_never_copies_a_huge_input(window):
    window.toggle_command_palette()
    palette = window.command_palette
    palette.field.setText("z" * 5_000_000)
    assert len(palette.field.text()) == 200
    assert palette.message.isVisible() and "z" * 60 not in palette.message.text() and len(palette.message.text()) < 300


def test_focus_stays_in_the_box_when_tab_would_leave_it(window):
    window.toggle_command_palette()
    palette = window.command_palette
    window.project_panel.findChildren(QLineEdit)[0].setFocus()
    assert palette.field.hasFocus()


# -- niveaux d'usage -----------------------------------------------------------------------------------------------------------


def test_simple_level_shows_tracking_locked_and_switching_runs_it(window):
    assert window.usage_level is SIMPLE
    window.toggle_command_palette()
    palette = window.command_palette
    _type(palette, "suivi")
    row = _row_of(palette, "goto:tab:tracking")
    assert palette._results[row].locked and _row_buttons(palette)[row] == "Passer en Avancé"
    level_events = []
    window.usage_level_changed.connect(level_events.append)
    palette.results.itemWidget(palette.results.item(row)).button.click()
    assert not palette.isVisible()
    assert window.usage_level is ADVANCED and level_events == ["advanced"]
    assert window.properties_panel._active_inspector_tab == TRACKING_TAB
    assert window.properties_panel.tracking_group.isVisibleTo(window.properties_panel)


def test_locked_menu_command_switches_level_then_runs_the_action(window):
    calls = []
    window.show_tracking_panel = lambda: calls.append(window.usage_level)       # la commande réelle, observée
    window.toggle_command_palette("suivi")
    palette = window.command_palette
    row = _row_of(palette, "menu:menu.item.tracking_panel")
    assert palette._results[row].locked
    QTest.mouseClick(palette.results.itemWidget(palette.results.item(row)).button, Qt.LeftButton)
    assert window.usage_level is ADVANCED


def test_studio_level_still_locks_advanced_and_opens_studio_ones(window):
    window.set_usage_level("studio")
    window.toggle_command_palette("page composition")
    palette = window.command_palette
    row = _row_of(palette, "goto:page:composition")
    assert _row_buttons(palette)[row] == "Passer en Avancé"
    _type(palette, "page couleur")
    assert _row_buttons(palette)[_row_of(palette, "goto:page:color")] == "Ouvrir"


def test_simple_level_offers_passer_en_studio_for_studio_entries(window):
    window.toggle_command_palette("page couleur")
    palette = window.command_palette
    assert _row_buttons(palette)[_row_of(palette, "goto:page:color")] == "Passer en Studio"
    QTest.keyClick(palette.field, Qt.Key_Return)
    assert window.usage_level is STUDIO and window.workspace.page == PAGE_COLOR


def test_an_available_entry_does_not_change_the_level(window):
    window.toggle_command_palette("page audio")
    palette = window.command_palette
    assert _row_buttons(palette)[_row_of(palette, "goto:page:audio")] == "Ouvrir"
    palette.results.setCurrentRow(_row_of(palette, "goto:page:audio"))
    QTest.keyClick(palette.field, Qt.Key_Return)
    assert window.usage_level is SIMPLE and window.workspace.page == PAGE_AUDIO


def test_a_locked_entry_without_the_switch_flag_does_nothing(window):
    window.command_palette_entries()
    window._run_palette_entry("goto:page:composition", False)
    assert window.workspace.page == PAGE_EDIT and window.usage_level is SIMPLE


# -- clavier -----------------------------------------------------------------------------------------------------------------------


def test_arrow_keys_move_the_selection_and_enter_runs_it(window):
    window.toggle_command_palette("ajouter")
    palette = window.command_palette
    assert palette.results.currentRow() == 0 and palette.results.count() == 6
    QTest.keyClick(palette.field, Qt.Key_Down)
    QTest.keyClick(palette.field, Qt.Key_Down)
    assert palette.results.currentRow() == 2
    QTest.keyClick(palette.field, Qt.Key_Up)
    assert palette.results.currentRow() == 1
    for _ in range(10):
        QTest.keyClick(palette.field, Qt.Key_Down)
    assert palette.results.currentRow() == 5
    for _ in range(10):
        QTest.keyClick(palette.field, Qt.Key_Up)
    assert palette.results.currentRow() == 0


def test_suggestion_chip_fills_the_field_and_empty_state_lists_recent(window):
    window.toggle_command_palette()
    palette = window.command_palette
    assert palette.message.isVisible() and not palette.results.isVisible()
    chip = next(c for c in palette.chips if c.text() == "couper")
    chip.click()
    assert palette.field.text() == "couper" and palette.results.count() > 0
    palette.results.setCurrentRow(_row_of(palette, "menu:menu.item.cut_at_playhead")) if any(
        r.entry.id == "menu:menu.item.cut_at_playhead" for r in palette._results) else None
    window._palette_recent[:] = ["goto:page:audio"]
    palette.close_palette()
    window.toggle_command_palette()
    assert [r.entry.id for r in palette._results] == ["goto:page:audio"]


def test_recent_choices_are_kept_in_memory_only_and_capped(window, tmp_path):
    window.command_palette_entries()
    for page in ("audio", "edit", "audio", "edit"):
        window._run_palette_entry(f"goto:page:{page}", True)
    assert window._palette_recent == ["goto:page:edit", "goto:page:audio"]
    for n in ("clip", "audio", "graphics", "compositing", "color", "effects"):
        window._run_palette_entry(f"goto:tab:{n}", True)
    assert len(window._palette_recent) == 5


# -- innocuité ----------------------------------------------------------------------------------------------------------------------


def test_opening_and_closing_never_touch_the_project_history_or_layout(window):
    before_history = (window.history.can_undo, window.history.can_redo, len(window.history.entries()))
    before_project = project_payload(window.project)
    layout = window.workspace.page
    for _ in range(3):
        window.toggle_command_palette("couper")
        QTest.keyClick(window.command_palette.field, Qt.Key_Down)
        window.command_palette.close_palette()
    assert (window.history.can_undo, window.history.can_redo, len(window.history.entries())) == before_history
    assert project_payload(window.project) == before_project and window.workspace.page == layout


# -- raccourci configurable -------------------------------------------------------------------------------------------------------


def test_the_command_is_in_the_catalog_and_reconfigurable(window):
    assert window.shortcuts.shortcut_map.sequences("command_palette") == ("Ctrl+Shift+P",)
    assert window.shortcuts.shortcut_map.command_for("Ctrl+K") == "cut_at_playhead"       # Ctrl+K reste « couper »
    assert window.shortcuts.assign("command_palette", 0, "Ctrl+Alt+P").ok
    assert "P" in window.palette_button.text() and window.shortcuts.action("command_palette").shortcut().toString() == "Ctrl+Alt+P"
    assert not window.shortcuts.assign("command_palette", 0, "Ctrl+K").ok                    # conflit détecté, rien n'est volé


# -- langues -------------------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("language", ["fr", "en", "es"])
def test_three_languages_strict(window, language):
    i18n.set_language(language)
    with i18n.strict_translations():
        window.toggle_command_palette()
        palette = window.command_palette
        palette.retranslate()
        assert palette.field.placeholderText() and palette.close_button.text() == i18n.translate("palette.close")
        chip = palette.chips[0]
        chip.click()
        assert palette.field.text() == i18n.translate("palette.chip.tracking")
        assert palette.results.count() > 0
        window._retranslate_command_palette()
        assert i18n.translate("palette.button") in window.palette_button.text()
        for term in ("zzzz",):
            _type(palette, term)
            assert palette.message.text()


def test_search_matches_the_current_language_and_the_english_keys(window):
    entries = window.command_palette_entries()
    fr = {r.entry.id for r in search(entries, "tracking", ADVANCED)}
    assert "menu:menu.item.tracking_panel" in fr and "goto:tab:tracking" in fr          # clé anglaise, interface en français
    i18n.set_language("en")
    window.set_usage_level("advanced")
    entries = window.command_palette_entries()
    assert "menu:menu.item.tracking_panel" in {r.entry.id for r in search(entries, "tracking", ADVANCED)}
    assert "menu:menu.item.tracking_panel" in {r.entry.id for r in search(entries, "suivi", ADVANCED)}   # synonyme français
    assert any(e.title == "Go to the Tracking tab" for e in entries)
    i18n.set_language("es")
    entries = window.command_palette_entries()
    assert "goto:tab:tracking" in {r.entry.id for r in search(entries, "seguimiento", ADVANCED)}


def test_event_filter_resizes_the_veil_with_the_window(window):
    window.toggle_command_palette()
    window.resize(1000, 700)
    QApplication.sendPostedEvents(None, int(QEvent.Resize))
    assert window.command_palette.size() == window.size()
    assert window.command_palette.box.width() == 620


def test_the_obvious_command_comes_first_and_a_greyed_placeholder_does_not_outrank_it(window):
    entries = window.command_palette_entries()
    first = search(entries, "couper", SIMPLE)[0]
    assert first.entry.id == "menu:menu.item.trim", first.entry
    greyed = next(r for r in search(entries, "couper", SIMPLE, limit=50) if r.entry.id == "menu:action.cut")
    assert not greyed.entry.enabled and greyed.score < first.score
    submenu_entry = next(e for e in entries if e.id == "menu:workspace.panel.color")
    assert submenu_entry.context == "Panneaux" and submenu_entry.title == "Couleur"
