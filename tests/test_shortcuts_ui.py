"""Raccourcis configurables : gestionnaire Qt, éditeur et fenêtre principale."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLineEdit, QMessageBox, QPushButton

from core.shortcuts import COMMANDS, Scope, ShortcutMap
from core.user_settings import load_user_settings, settings_file_path
from ui.preferences_dialog import PreferencesDialog
from ui.shortcut_manager import ShortcutManager, event_sequence
from ui.shortcuts_editor import ShortcutsEditor

# --- helpers ----------------------------------------------------------------


def _manager(**overrides) -> ShortcutManager:
    return ShortcutManager(ShortcutMap.from_overrides(overrides))


def _window(qtbot, monkeypatch, overrides: dict | None = None, *, active: bool = False):
    """Fenêtre principale prête pour les tests.

    ``active=True`` est requis par les raccourcis de ``QAction`` : Qt ne
    les déclenche que dans une fenêtre active. Certains environnements
    (CI Linux/Windows sans gestionnaire de fenêtres) ne l'activent pas ;
    le test est alors ignoré plutôt que de devenir instable. Le chemin
    des touches nues, lui, ne dépend pas de l'activation.
    """
    from ui.main_window import MainWindow

    monkeypatch.setattr("ui.main_window.QMessageBox.information", lambda *_, **__: None)
    monkeypatch.setattr("ui.main_window.QMessageBox.critical", lambda *_, **__: None)
    if overrides is not None:
        from core.user_settings import UserSettings, save_user_settings

        save_user_settings(UserSettings(shortcuts=overrides))
    window = MainWindow()
    qtbot.addWidget(window)
    window.timeline_timer.stop()
    window.show()
    qtbot.waitExposed(window)
    window.activateWindow()
    if active:
        try:
            qtbot.waitUntil(window.isActiveWindow, timeout=2000)
        except qtbot.TimeoutError:
            pytest.skip("fenêtre non activable dans cet environnement")
    # Aucun widget enfant ne doit garder le focus et consommer la touche.
    window.setFocus()
    return window


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, command_id: str):
        return lambda: self.calls.append(command_id)


def _key_event(key, modifiers=Qt.NoModifier):
    from PySide6.QtGui import QKeyEvent

    return QKeyEvent(QKeyEvent.KeyPress, key, modifiers)


# --- gestionnaire : actions -------------------------------------------------------


def test_one_qaction_per_action_command_with_default_shortcuts(qtbot):
    manager = _manager()
    manager.register_handlers({c.id: (lambda: None) for c in COMMANDS})
    for command in COMMANDS:
        if command.scope is Scope.ACTION:
            manager.create_action(command.id, command.id)
    redo = manager.action("redo")
    assert [s.toString() for s in redo.shortcuts()] == ["Ctrl+Shift+Z", "Ctrl+Y"]
    assert [s.toString() for s in manager.action("delete_clip").shortcuts()] == ["Del", "Backspace"]
    with pytest.raises(ValueError):  # une seconde QAction rendrait le raccourci ambigu
        manager.create_action("redo", "dup")
    with pytest.raises(ValueError):  # une commande clavier n'a pas de QAction
        manager.create_action("tool_blade", "x")


def test_changing_a_shortcut_updates_the_action_immediately(qtbot):
    manager = _manager()
    action = manager.create_action("undo", "Annuler")
    seen = []
    manager.overrides_changed.connect(seen.append)
    assert manager.assign("undo", 0, "Ctrl+Alt+U").ok
    assert [s.toString() for s in action.shortcuts()] == ["Ctrl+Alt+U"]
    assert seen == [{"undo": ["Ctrl+Alt+U"]}]
    manager.reset("undo")
    assert [s.toString() for s in action.shortcuts()] == ["Ctrl+Z"]
    assert seen[-1] == {}


def test_refused_assignment_changes_nothing_and_persists_nothing(qtbot):
    manager = _manager()
    action = manager.create_action("undo", "Annuler")
    seen = []
    manager.overrides_changed.connect(seen.append)
    assert not manager.assign("undo", 0, "Ctrl+Y").ok  # pris par « Rétablir »
    assert [s.toString() for s in action.shortcuts()] == ["Ctrl+Z"]
    assert seen == []


def test_action_trigger_calls_the_registered_handler(qtbot):
    manager = _manager()
    record = _Recorder()
    manager.register_handlers({"undo": record("undo")})
    action = manager.create_action("undo", "Annuler")
    action.trigger()
    assert record.calls == ["undo"]


# --- gestionnaire : touches -------------------------------------------------------------


def test_event_sequence_matches_portable_text_and_ignores_keypad(qtbot):
    assert event_sequence(_key_event(Qt.Key_K, Qt.ControlModifier)) == "Ctrl+K"
    assert event_sequence(_key_event(Qt.Key_Left, Qt.ShiftModifier)) == "Shift+Left"
    assert event_sequence(_key_event(Qt.Key_Right, Qt.KeypadModifier)) == "Right"
    assert event_sequence(_key_event(Qt.Key_Shift, Qt.ShiftModifier)) is None


def test_key_event_runs_the_bound_command_and_follows_rebinding(qtbot):
    manager = _manager()
    record = _Recorder()
    manager.register_handlers({c.id: record(c.id) for c in COMMANDS})
    assert manager.handle_key_event(_key_event(Qt.Key_B), focus_widget=None)
    assert record.calls == ["tool_blade"]
    manager.assign("tool_blade", 0, "Ctrl+Alt+B")
    assert not manager.handle_key_event(_key_event(Qt.Key_B), focus_widget=None)
    assert manager.handle_key_event(
        _key_event(Qt.Key_B, Qt.ControlModifier | Qt.AltModifier), focus_widget=None
    )
    assert record.calls == ["tool_blade", "tool_blade"]


def test_shifted_plus_still_zooms_in(qtbot):
    manager = _manager()
    record = _Recorder()
    manager.register_handlers({"zoom_in": record("zoom_in")})
    assert manager.handle_key_event(_key_event(Qt.Key_Plus, Qt.ShiftModifier), None)
    assert record.calls == ["zoom_in"]


def test_text_fields_never_trigger_key_commands(qtbot):
    manager = _manager()
    record = _Recorder()
    manager.register_handlers({c.id: record(c.id) for c in COMMANDS})
    field = QLineEdit()
    qtbot.addWidget(field)
    assert not manager.handle_key_event(_key_event(Qt.Key_Space), focus_widget=field)
    assert not manager.handle_key_event(_key_event(Qt.Key_K, Qt.ControlModifier), focus_widget=field)
    assert record.calls == []


def test_action_scope_commands_are_not_run_twice_by_the_key_path(qtbot):
    manager = _manager()
    record = _Recorder()
    manager.register_handlers({"undo": record("undo")})
    manager.create_action("undo", "Annuler")
    assert not manager.handle_key_event(_key_event(Qt.Key_Z, Qt.ControlModifier), None)
    assert record.calls == []


# --- éditeur ----------------------------------------------------------------------------------------


@pytest.fixture
def editor(qtbot):
    manager = _manager()
    manager.register_handlers({c.id: (lambda: None) for c in COMMANDS})
    widget = ShortcutsEditor(manager)
    qtbot.addWidget(widget)
    widget.show()
    return widget


def _visible_ids(widget: ShortcutsEditor) -> set[str]:
    return {cid for cid, item in widget._items.items() if not item.isHidden()}


def _type_sequence(edit, text: str) -> None:
    """Simule une saisie terminée dans le champ de capture."""
    edit.setKeySequence(QKeySequence(text))
    edit.editingFinished.emit()


def test_editor_lists_every_command_with_its_current_shortcut(editor):
    assert len(editor._items) == len(COMMANDS)
    assert editor._items["tool_blade"].text(2) != ""
    assert editor._items["redo"].text(3) != ""  # raccourci secondaire affiché


def test_editor_search_matches_name_shortcut_and_category(editor):
    editor.search_edit.setText("lame")
    assert "tool_blade" in _visible_ids(editor)
    assert "tool_roll" not in _visible_ids(editor)
    editor.search_edit.setText("ctrl+k")
    assert _visible_ids(editor) == {"cut_at_playhead"}
    editor.search_edit.setText("marqueurs")
    assert _visible_ids(editor) == {"marker_add", "marker_previous", "marker_next"}
    editor.search_edit.setText("zzz-introuvable")
    assert _visible_ids(editor) == set()


def test_editor_category_filter(editor):
    index = editor.category_combo.findData("tools")
    editor.category_combo.setCurrentIndex(index)
    assert _visible_ids(editor) == {
        "tool_select", "tool_blade", "tool_roll", "tool_slip", "tool_slide",
    }
    editor.search_edit.setText("slip")
    assert _visible_ids(editor) == {"tool_slip"}


def test_editor_changes_a_shortcut_immediately(editor):
    editor.select_command("tool_blade")
    _type_sequence(editor.primary_edit, "F6")
    assert editor._manager.shortcut_map.sequences("tool_blade") == ("F6",)
    assert editor._items["tool_blade"].text(2) == format_native("F6")


def format_native(text: str) -> str:
    from core.shortcuts import format_sequence

    return format_sequence(text)


def test_editor_conflict_explains_and_keeps_the_old_shortcut(editor):
    editor.select_command("tool_blade")
    _type_sequence(editor.primary_edit, "R")
    assert editor._manager.shortcut_map.sequences("tool_blade") == ("B",)
    assert "Outil Roll" in editor.status_label.text()
    assert not editor.reassign_button.isHidden()
    assert editor.primary_edit.keySequence().toString() == "B"  # éditeur remis sur la valeur active


def test_editor_reassign_takes_the_shortcut_from_the_other_command(editor):
    editor.select_command("tool_blade")
    _type_sequence(editor.primary_edit, "R")
    editor.reassign_button.click()
    shortcut_map = editor._manager.shortcut_map
    assert shortcut_map.sequences("tool_blade") == ("R",)
    assert shortcut_map.sequences("tool_roll") == ()
    assert editor.reassign_button.isHidden()


def test_editor_refuses_reserved_and_invalid_shortcuts(editor):
    editor.select_command("tool_blade")
    _type_sequence(editor.primary_edit, "Ctrl+C")
    assert editor._manager.shortcut_map.sequences("tool_blade") == ("B",)
    assert editor.reassign_button.isHidden()  # une réservation ne se « réassigne » pas
    assert editor.status_label.text() != ""


def test_editor_accepts_a_multi_step_chord(editor):
    editor.select_command("tool_blade")
    _type_sequence(editor.secondary_edit, "Ctrl+Alt+K, B")
    assert editor._manager.shortcut_map.sequences("tool_blade") == ("B", "Ctrl+Alt+K, B")
    assert editor.secondary_edit.maximumSequenceLength() == 4


def test_editor_reports_a_chord_that_collides_with_a_single_key(editor):
    editor.select_command("tool_blade")
    _type_sequence(editor.secondary_edit, "Ctrl+K, B")
    assert "Couper à la tête de lecture" in editor.status_label.text()
    assert editor._manager.shortcut_map.sequences("tool_blade") == ("B",)


def test_editor_secondary_shortcut_add_and_remove(editor):
    editor.select_command("tool_blade")
    _type_sequence(editor.secondary_edit, "F7")
    assert editor._manager.shortcut_map.sequences("tool_blade") == ("B", "F7")
    editor.secondary_clear.click()
    assert editor._manager.shortcut_map.sequences("tool_blade") == ("B",)


def test_editor_reset_one_and_reset_all(editor, monkeypatch):
    editor.select_command("tool_blade")
    _type_sequence(editor.primary_edit, "F6")
    editor.select_command("tool_roll")
    _type_sequence(editor.primary_edit, "F7")
    editor.select_command("tool_blade")
    editor.reset_button.click()
    shortcut_map = editor._manager.shortcut_map
    assert shortcut_map.sequences("tool_blade") == ("B",)
    assert shortcut_map.sequences("tool_roll") == ("F7",)

    monkeypatch.setattr(QMessageBox, "question", lambda *_, **__: QMessageBox.No)
    editor.reset_all_button.click()
    assert shortcut_map.sequences("tool_roll") == ("F7",)  # annulé
    monkeypatch.setattr(QMessageBox, "question", lambda *_, **__: QMessageBox.Yes)
    editor.reset_all_button.click()
    assert editor._manager.shortcut_map.overrides() == {}


def test_editor_follows_the_application_language(editor):
    from ui import i18n

    original = i18n.current_language()
    try:
        i18n.set_language("en")
        editor.retranslate()
        assert editor._items["tool_blade"].text(0) == "Blade tool"
        assert editor.search_edit.placeholderText() == "Search commands…"
    finally:
        i18n.set_language(original)


def test_preferences_dialog_has_a_shortcuts_tab_only_with_a_manager(qtbot):
    plain = PreferencesDialog()
    qtbot.addWidget(plain)
    assert plain.tabs is None and plain.shortcuts_editor is None
    full = PreferencesDialog(shortcut_manager=_manager())
    qtbot.addWidget(full)
    assert full.tabs.count() == 2
    assert full.shortcuts_editor is not None
    assert full.restore_button is not None  # le comportement existant reste accessible


# --- fenêtre principale : activation réelle ------------------------------------------------------------


def test_every_command_has_a_handler_in_the_main_window(qtbot, monkeypatch):
    window = _window(qtbot, monkeypatch)
    assert window.shortcuts.missing_handlers() == []
    for command in COMMANDS:
        assert (window.shortcuts.action(command.id) is not None) == (
            command.scope is Scope.ACTION
        ), command.id


def test_main_window_defaults_match_the_previous_menu_shortcuts(qtbot, monkeypatch):
    window = _window(qtbot, monkeypatch)
    expected = {
        "project_new": ["Ctrl+N"],
        "project_open": ["Ctrl+O"],
        "project_save": ["Ctrl+S"],
        "project_save_as": ["Ctrl+Shift+S"],
        "quit": ["Ctrl+Q"],
        "undo": ["Ctrl+Z"],
        "redo": ["Ctrl+Shift+Z", "Ctrl+Y"],
        "duplicate_clip": ["Ctrl+D"],
        "delete_clip": ["Del", "Backspace"],
        "ripple_delete": ["Ctrl+Backspace"],
        "toggle_clip_enabled": ["Ctrl+E"],
        "preferences": ["Ctrl+,"],
        "toggle_scopes": ["Ctrl+Alt+S"],
    }
    for command_id, shortcuts in expected.items():
        action = window.shortcuts.action(command_id)
        assert [s.toString(QKeySequence.PortableText) for s in action.shortcuts()] == shortcuts


def test_key_shortcuts_drive_the_real_editor_actions(qtbot, monkeypatch):
    window = _window(qtbot, monkeypatch)
    timeline = window.timeline_panel
    QTest.keyClick(window, Qt.Key_B)
    assert timeline.tool == "blade"
    QTest.keyClick(window, Qt.Key_B)
    assert timeline.tool == "select"
    QTest.keyClick(window, Qt.Key_R)
    assert timeline.tool == "roll"
    QTest.keyClick(window, Qt.Key_V)
    assert timeline.tool == "select"

    snap = timeline.snap_button.isChecked()
    QTest.keyClick(window, Qt.Key_S)
    assert timeline.snap_button.isChecked() is (not snap)

    before = window.playhead_seconds
    QTest.keyClick(window, Qt.Key_Right, Qt.ShiftModifier)
    assert window.playhead_seconds == pytest.approx(before + 1)
    QTest.keyClick(window, Qt.Key_Left, Qt.ShiftModifier)
    assert window.playhead_seconds == pytest.approx(before)


def test_play_pause_and_markers_via_keys(qtbot, monkeypatch):
    window = _window(qtbot, monkeypatch)
    assert not window.is_playing
    QTest.keyClick(window, Qt.Key_Space)
    assert window.is_playing
    QTest.keyClick(window, Qt.Key_K)
    assert not window.is_playing
    count = len(window.project.markers)
    QTest.keyClick(window, Qt.Key_M)
    assert len(window.project.markers) == count + 1


def test_zoom_shortcuts_work_when_the_timeline_has_focus(qtbot, monkeypatch):
    window = _window(qtbot, monkeypatch)
    timeline = window.timeline_panel
    timeline.setFocus()
    before = timeline.zoom
    QTest.keyClick(timeline, Qt.Key_Equal)
    assert timeline.zoom > before
    QTest.keyClick(timeline, Qt.Key_Minus)
    assert timeline.zoom == pytest.approx(before)


def test_menu_action_shortcut_activates_the_action(qtbot, monkeypatch):
    window = _window(qtbot, monkeypatch, active=True)
    calls = []
    window.shortcuts.register_handlers({"project_new": lambda: calls.append("new")})
    QTest.keyClick(window, Qt.Key_N, Qt.ControlModifier)
    assert calls == ["new"]


def test_rebinding_takes_effect_without_restart(qtbot, monkeypatch):
    window = _window(qtbot, monkeypatch, active=True)
    timeline = window.timeline_panel
    assert window.shortcuts.assign("tool_blade", 0, "F6").ok
    QTest.keyClick(window, Qt.Key_B)
    assert timeline.tool == "select"  # l'ancienne touche est libre
    QTest.keyClick(window, Qt.Key_F6)
    assert timeline.tool == "blade"
    assert window.shortcuts.assign("project_new", 0, "Ctrl+Alt+N").ok
    calls = []
    window.shortcuts.register_handlers({"project_new": lambda: calls.append("new")})
    QTest.keyClick(window, Qt.Key_N, Qt.ControlModifier)
    assert calls == []
    QTest.keyClick(window, Qt.Key_N, Qt.ControlModifier | Qt.AltModifier)
    assert calls == ["new"]


def test_typing_in_a_text_field_does_not_trigger_global_commands(qtbot, monkeypatch):
    window = _window(qtbot, monkeypatch)
    field = QLineEdit(window)
    field.move(5, 5)
    field.show()
    field.setFocus()
    qtbot.waitUntil(field.hasFocus, timeout=2000)
    timeline = window.timeline_panel
    QTest.keyClicks(field, "bvrk ")
    assert field.text() == "bvrk "
    assert timeline.tool == "select"
    assert not window.is_playing


def test_buttons_keep_their_space_key(qtbot, monkeypatch):
    window = _window(qtbot, monkeypatch)
    button = QPushButton("test", window)
    clicked = []
    button.clicked.connect(lambda: clicked.append(1))
    button.show()
    button.setFocus()
    qtbot.waitUntil(button.hasFocus, timeout=2000)
    QTest.keyClick(button, Qt.Key_Space)
    assert clicked == [1]
    assert not window.is_playing  # la touche a été consommée par le bouton


def test_timeline_tooltips_follow_the_current_shortcut(qtbot, monkeypatch):
    window = _window(qtbot, monkeypatch)
    timeline = window.timeline_panel
    assert timeline.blade_button.toolTip() == "Outil lame (B)"
    window.shortcuts.assign("tool_blade", 0, "F6")
    assert "F6" in timeline.blade_button.toolTip()
    window.shortcuts.assign("tool_blade", 0, None)
    assert timeline.blade_button.toolTip() == "Outil lame"


# --- persistance ------------------------------------------------------------------------------------------------


def test_changes_are_saved_automatically_and_restored_on_next_start(qtbot, monkeypatch):
    window = _window(qtbot, monkeypatch)
    window.shortcuts.assign("tool_blade", 0, "F6")
    on_disk = json.loads(settings_file_path().read_text(encoding="utf-8"))
    assert on_disk["shortcuts"] == {"tool_blade": ["F6"]}
    window.close()

    again = _window(qtbot, monkeypatch)
    assert again.shortcuts.shortcut_map.sequences("tool_blade") == ("F6",)
    QTest.keyClick(again, Qt.Key_F6)
    assert again.timeline_panel.tool == "blade"


def test_other_settings_changes_do_not_erase_shortcuts(qtbot, monkeypatch):
    window = _window(qtbot, monkeypatch)
    window.shortcuts.assign("tool_blade", 0, "F6")
    window.on_performance_setting_changed("low")
    window._restore_default_preferences()  # « Restaurer » ne touche pas aux raccourcis
    assert load_user_settings().shortcuts == {"tool_blade": ["F6"]}


def test_old_preferences_file_without_shortcuts_starts_with_defaults(qtbot, monkeypatch):
    path = settings_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"theme_mode": "dark", "language": "fr"}), encoding="utf-8")
    window = _window(qtbot, monkeypatch)
    assert window.shortcuts.shortcut_map.overrides() == {}
    QTest.keyClick(window, Qt.Key_B)
    assert window.timeline_panel.tool == "blade"


def test_corrupted_shortcut_entries_in_preferences_fall_back_safely(qtbot, monkeypatch):
    window = _window(qtbot, monkeypatch, overrides={"tool_blade": ["Ctrl+Banane"], "inconnu": ["F1"]})
    assert window.shortcuts.shortcut_map.sequences("tool_blade") == ("B",)


def test_audio_commands_are_available_but_unbound_by_default(qtbot, monkeypatch):
    window = _window(qtbot, monkeypatch)
    assert window.shortcuts.shortcut_map.sequences("audio_master_mute") == ()
    muted = window._master_muted
    window.shortcuts.trigger("audio_master_mute")
    assert window._master_muted is (not muted)


def test_track_mute_command_runs_the_existing_track_handler(qtbot, monkeypatch):
    window = _window(qtbot, monkeypatch, active=True)
    assert window.shortcuts.shortcut_map.sequences("track_toggle_mute") == ()
    calls = []
    monkeypatch.setattr(window, "toggle_selected_track_muted", lambda: calls.append(1))
    window.shortcuts.register_handlers(window._shortcut_handlers())
    window.shortcuts.assign("track_toggle_mute", 0, "Ctrl+Alt+M")
    QTest.keyClick(window, Qt.Key_M, Qt.ControlModifier | Qt.AltModifier)
    assert calls == [1]


# --- accords et modificateurs souples --------------------------------------------


def test_chord_runs_after_both_steps_and_waits_for_the_second(qtbot):
    manager = _manager()
    record = _Recorder()
    manager.register_handlers({c.id: record(c.id) for c in COMMANDS})
    assert manager.assign("tool_blade", 1, "Ctrl+Alt+K, B").ok
    ctrl_alt = Qt.ControlModifier | Qt.AltModifier
    assert manager.handle_key_event(_key_event(Qt.Key_K, ctrl_alt), None)  # consommée
    assert record.calls == [] and manager.pending_chord == "Ctrl+Alt+K"
    assert manager.handle_key_event(_key_event(Qt.Key_B), None)
    assert record.calls == ["tool_blade"] and manager.pending_chord == ""


def test_broken_chord_is_dropped_and_the_key_is_read_again(qtbot):
    manager = _manager()
    record = _Recorder()
    manager.register_handlers({c.id: record(c.id) for c in COMMANDS})
    manager.assign("tool_blade", 1, "Ctrl+Alt+K, B")
    ctrl_alt = Qt.ControlModifier | Qt.AltModifier
    manager.handle_key_event(_key_event(Qt.Key_K, ctrl_alt), None)
    assert manager.handle_key_event(_key_event(Qt.Key_R), None)  # pas B : « R » = Roll
    assert record.calls == ["tool_roll"] and manager.pending_chord == ""


def test_chord_expires_and_modifier_keys_do_not_break_it(qtbot):
    manager = _manager()
    record = _Recorder()
    manager.register_handlers({c.id: record(c.id) for c in COMMANDS})
    manager.assign("tool_blade", 1, "Ctrl+Alt+K, B")
    ctrl_alt = Qt.ControlModifier | Qt.AltModifier
    manager.handle_key_event(_key_event(Qt.Key_K, ctrl_alt), None)
    assert not manager.handle_key_event(_key_event(Qt.Key_Shift, Qt.ShiftModifier), None)
    assert manager.pending_chord == "Ctrl+Alt+K"
    manager._pending_timer.timeout.emit()  # le délai est écoulé
    assert manager.pending_chord == ""


def test_text_field_focus_cancels_a_pending_chord(qtbot):
    manager = _manager()
    manager.register_handlers({c.id: (lambda: None) for c in COMMANDS})
    manager.assign("tool_blade", 1, "Ctrl+Alt+K, B")
    manager.handle_key_event(_key_event(Qt.Key_K, Qt.ControlModifier | Qt.AltModifier), None)
    field = QLineEdit()
    qtbot.addWidget(field)
    assert not manager.handle_key_event(_key_event(Qt.Key_B), field)
    assert manager.pending_chord == ""


def test_chord_works_through_the_real_main_window(qtbot, monkeypatch):
    window = _window(qtbot, monkeypatch)
    window.shortcuts.assign("tool_blade", 1, "Ctrl+Alt+K, B")
    ctrl_alt = Qt.ControlModifier | Qt.AltModifier
    QTest.keyClick(window, Qt.Key_K, ctrl_alt)
    assert window.timeline_panel.tool == "select"
    QTest.keyClick(window, Qt.Key_B)
    assert window.timeline_panel.tool == "blade"


def test_action_chord_is_handled_by_qt_through_the_qaction(qtbot, monkeypatch):
    window = _window(qtbot, monkeypatch, active=True)
    calls = []
    window.shortcuts.register_handlers({"project_new": lambda: calls.append("new")})
    assert window.shortcuts.assign("project_new", 0, "Ctrl+Alt+P, N").ok
    ctrl_alt = Qt.ControlModifier | Qt.AltModifier
    QTest.keyClick(window, Qt.Key_P, ctrl_alt)
    QTest.keyClick(window, Qt.Key_N)
    assert calls == ["new"]


def test_ctrl_equal_and_ctrl_minus_still_zoom(qtbot, monkeypatch):
    window = _window(qtbot, monkeypatch)
    timeline = window.timeline_panel
    timeline.setFocus()
    before = timeline.zoom
    QTest.keyClick(timeline, Qt.Key_Equal, Qt.ControlModifier)
    assert timeline.zoom > before
    QTest.keyClick(timeline, Qt.Key_Minus, Qt.ControlModifier)
    assert timeline.zoom == pytest.approx(before)


def test_loose_modifiers_do_not_leak_to_other_commands(qtbot):
    manager = _manager()
    record = _Recorder()
    manager.register_handlers({c.id: record(c.id) for c in COMMANDS})
    assert not manager.handle_key_event(_key_event(Qt.Key_B, Qt.ControlModifier | Qt.AltModifier), None)
    assert record.calls == []


# --- libellés de menu traduits à chaud ------------------------------------------------


def test_menu_labels_follow_the_language_live(qtbot, monkeypatch):
    from ui import i18n

    original = i18n.current_language()
    window = _window(qtbot, monkeypatch)
    try:
        assert window.shortcuts.action("project_new").text() == "Nouveau"
        assert window.window_menu.title().replace("&", "") == "Fenêtre"  # « & » : lettre mnémonique
        monkeypatch.setattr("ui.main_window.save_user_settings", lambda *_: None)
        window._apply_settings(replace(window._settings_snapshot(), language="en"))
        assert window.shortcuts.action("project_new").text() == "New"
        assert window.shortcuts.action("toggle_scopes").text() == "Show scopes"
        assert window.undo_action.text() == "Undo"
        assert window.window_menu.title().replace("&", "") == "Window"
    finally:
        i18n.set_language(original)


def test_preferences_dialog_retranslates_live_including_the_shortcuts_tab(qtbot):
    from ui import i18n

    original = i18n.current_language()
    dialog = PreferencesDialog(shortcut_manager=_manager())
    qtbot.addWidget(dialog)
    try:
        i18n.set_language("en")
        assert dialog.tabs.tabText(0) == "General"
        assert dialog.tabs.tabText(1) == "Shortcuts"
        editor = dialog.shortcuts_editor
        assert editor._items["tool_blade"].text(0) == "Blade tool"
        assert editor.search_edit.placeholderText() == "Search commands…"
        assert editor.category_combo.itemText(0) == "All categories"
        assert editor.reset_all_button.text() == "Reset all"
        i18n.set_language("es")
        assert dialog.tabs.tabText(1) == "Atajos"
        assert editor._items["tool_blade"].text(0) == "Herramienta Cuchilla"
    finally:
        i18n.set_language(original)


# --- menu Fenêtre : indépendant de la langue ------------------------------------------


def _set_language(window, monkeypatch, code: str) -> None:
    monkeypatch.setattr("ui.main_window.save_user_settings", lambda *_: None)
    window._apply_settings(replace(window._settings_snapshot(), language=code))


def test_workspace_menu_sync_does_not_depend_on_the_language(qtbot, monkeypatch):
    from ui import i18n
    from core.workspace_state import PanelId

    original = i18n.current_language()
    window = _window(qtbot, monkeypatch)
    try:
        for code in ("en", "es"):
            _set_language(window, monkeypatch, code)
            panels = window.window_menu.findChild(type(window.window_menu), "panels_menu")
            assert panels is not None
            panel = next(iter(PanelId))
            window.workspace.set_panel_visible(panel, False)
            window._sync_workspace_menu()
            checked = {a.isChecked() for a, p in zip(panels.actions(), PanelId) if p is panel}
            assert checked == {False}
            window.workspace.set_panel_visible(panel, True)
            window._sync_workspace_menu()
            assert {a.isChecked() for a, p in zip(panels.actions(), PanelId) if p is panel} == {True}
    finally:
        i18n.set_language(original)


def _all_actions(menu):
    for action in menu.actions():
        yield action
        if action.menu() is not None:
            yield from _all_actions(action.menu())


def test_workspace_submenus_and_actions_are_retranslated_live(qtbot, monkeypatch):
    from ui import i18n

    original = i18n.current_language()
    window = _window(qtbot, monkeypatch)
    try:
        titles_fr = {m.title() for m in window.window_menu.findChildren(type(window.window_menu))}
        assert {"Panneaux", "Espaces de travail"} <= titles_fr
        _set_language(window, monkeypatch, "en")
        titles_en = {m.title() for m in window.window_menu.findChildren(type(window.window_menu))}
        assert {"Panels", "Workspaces"} <= titles_en
        assert not ({"Panneaux", "Espaces de travail"} & titles_en)
        texts = {a.text() for a in _all_actions(window.window_menu)}
        assert {"Restore layout", "Save layout as…"} <= texts
        assert not ({"Restaurer la disposition", "Enregistrer la disposition sous…"} & texts)
    finally:
        i18n.set_language(original)
