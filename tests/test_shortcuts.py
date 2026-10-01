"""Modèle des raccourcis (``core.shortcuts``) : pur, sans Qt."""

from __future__ import annotations

import json

import pytest

from core.shortcuts import (
    COMMANDS,
    COMMANDS_BY_ID,
    MAX_SEQUENCES_PER_COMMAND,
    AssignStatus,
    Category,
    ShortcutMap,
    format_sequence,
    key_candidates,
    normalize_sequence,
    reserved_reason,
    validate_defaults,
)
from core.user_settings import (
    UserSettings,
    load_user_settings,
    save_user_settings,
    settings_file_path,
)

# Table d'origine (avant la centralisation) : (touche Qt, modificateurs) -> action.
# Elle fige le comportement par défaut : toute dérive fait échouer le test.
LEGACY_KEY_TABLE = {
    "Space": "play_pause",
    "K": "play_pause",
    "Left": "frame_back",
    "Right": "frame_forward",
    "Shift+Left": "second_back",
    "Shift+Right": "second_forward",
    "J": "shuttle_back",
    "L": "shuttle_forward",
    "=": "zoom_in",
    "+": "zoom_in",
    "-": "zoom_out",
    "Ctrl+0": "zoom_fit",
    "Shift+Z": "zoom_fit",
    "B": "tool_blade",
    "R": "tool_roll",
    "Y": "tool_slip",
    "U": "tool_slide",
    "V": "tool_select",
    "S": "toggle_snap",
    "N": "toggle_ripple",
    "M": "marker_add",
    "[": "marker_previous",
    "]": "marker_next",
    "Ctrl+K": "cut_at_playhead",
    "Ctrl+A": "select_all",
}
# Raccourcis qui étaient portés par des QAction.
LEGACY_ACTION_TABLE = {
    "Ctrl+N": "project_new",
    "Ctrl+O": "project_open",
    "Ctrl+S": "project_save",
    "Ctrl+Shift+S": "project_save_as",
    "Ctrl+Alt+S": "toggle_scopes",
    "Ctrl+Q": "quit",
    "Ctrl+Z": "undo",
    "Ctrl+Shift+Z": "redo",
    "Ctrl+Y": "redo",
    "Ctrl+D": "duplicate_clip",
    "Delete": "delete_clip",
    "Backspace": "delete_clip",
    "Ctrl+Backspace": "ripple_delete",
    "Ctrl+E": "toggle_clip_enabled",
    "Ctrl+,": "preferences",
}


# --- valeurs par défaut -----------------------------------------------------


def test_defaults_preserve_every_legacy_shortcut():
    shortcuts = ShortcutMap()
    for sequence, command_id in {**LEGACY_KEY_TABLE, **LEGACY_ACTION_TABLE}.items():
        assert shortcuts.command_for(sequence) == command_id, sequence


def test_defaults_have_no_extra_shortcut_beyond_legacy_ones():
    shortcuts = ShortcutMap()
    legacy = {
        normalize_sequence(s) for s in {**LEGACY_KEY_TABLE, **LEGACY_ACTION_TABLE}
    }
    # Raccourcis ajoutés avec le moteur d'animation (aucun ne remplace un ancien).
    animation = {"Alt+K", "Alt+Shift+K", "Alt+J", "Alt+L", "Ctrl+Alt+A", "Ctrl+Alt+G"}
    assigned = {s for c in COMMANDS for s in shortcuts.sequences(c.id)}
    assert assigned == legacy | {normalize_sequence(s) for s in animation}
    assert not legacy & {normalize_sequence(s) for s in animation}


def test_defaults_are_valid_and_conflict_free():
    assert validate_defaults() == []


def test_command_ids_are_unique_and_every_category_is_declared():
    ids = [c.id for c in COMMANDS]
    assert len(ids) == len(set(ids))
    assert {c.category for c in COMMANDS} == set(Category)
    assert all(len(c.default) <= MAX_SEQUENCES_PER_COMMAND for c in COMMANDS)


def test_save_as_keeps_ctrl_shift_s_and_scopes_uses_ctrl_alt_s():
    # Les deux partageaient Ctrl+Shift+S : Qt jugeait le raccourci
    # ambigu et ne déclenchait ni l'un ni l'autre.
    shortcuts = ShortcutMap()
    assert shortcuts.sequences("project_save_as") == ("Ctrl+Shift+S",)
    assert shortcuts.sequences("toggle_scopes") == ("Ctrl+Alt+S",)


# --- normalisation, validité ---------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("ctrl+shift+z", "Ctrl+Shift+Z"),
        ("Shift+Ctrl+Z", "Ctrl+Shift+Z"),
        ("Cmd+S", "Ctrl+S"),
        ("Delete", "Del"),
        ("Escape", "Esc"),
        ("Ctrl++", "Ctrl++"),
        ("+", "+"),
        ("Ctrl+,", "Ctrl+,"),
        (" f5 ", "F5"),
        ("PageUp", "PgUp"),
    ],
)
def test_normalize_sequence_accepts_equivalent_forms(raw, expected):
    assert normalize_sequence(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["", "   ", "Ctrl", "Shift+", "Ctrl+Ctrl+A", "Ctrl+K, Ctrl+Banane", "A, B, C, D, E", "Ctrl+Banane", "F99", "é", None, 42],
)
def test_invalid_sequences_are_rejected(raw):
    assert normalize_sequence(raw) is None


def test_invalid_shortcut_is_refused_and_changes_nothing():
    shortcuts = ShortcutMap()
    result = shortcuts.assign("tool_blade", 0, "Ctrl+Banane")
    assert result.status is AssignStatus.INVALID
    assert shortcuts.sequences("tool_blade") == ("B",)
    assert shortcuts.overrides() == {}


def test_unknown_command_or_slot_is_refused():
    shortcuts = ShortcutMap()
    assert shortcuts.assign("nope", 0, "F5").status is AssignStatus.UNKNOWN_COMMAND
    assert shortcuts.assign("tool_blade", 2, "F5").status is AssignStatus.UNKNOWN_COMMAND


# --- modification, secondaire ---------------------------------------------------------


def test_changing_a_shortcut_replaces_the_default():
    shortcuts = ShortcutMap()
    assert shortcuts.assign("tool_blade", 0, "ctrl+b").ok
    assert shortcuts.sequences("tool_blade") == ("Ctrl+B",)
    assert shortcuts.command_for("B") is None
    assert shortcuts.command_for("Ctrl+B") == "tool_blade"
    assert shortcuts.overrides() == {"tool_blade": ["Ctrl+B"]}


def test_secondary_shortcut_can_be_added_changed_and_removed():
    shortcuts = ShortcutMap()
    assert shortcuts.assign("tool_blade", 1, "F6").ok
    assert shortcuts.sequences("tool_blade") == ("B", "F6")
    assert shortcuts.assign("tool_blade", 1, "F7").ok
    assert shortcuts.sequences("tool_blade") == ("B", "F7")
    assert shortcuts.assign("tool_blade", 1, None).ok
    assert shortcuts.sequences("tool_blade") == ("B",)
    assert shortcuts.overrides() == {}  # revenu au défaut : plus d'écart


def test_removing_the_primary_promotes_the_secondary():
    shortcuts = ShortcutMap()
    shortcuts.assign("play_pause", 0, None)
    assert shortcuts.sequences("play_pause") == ("K",)


def test_a_command_can_be_left_without_any_shortcut():
    shortcuts = ShortcutMap()
    shortcuts.assign("tool_blade", 0, None)
    assert shortcuts.sequences("tool_blade") == ()
    assert shortcuts.overrides() == {"tool_blade": []}
    restored = ShortcutMap.from_overrides(shortcuts.overrides())
    assert restored.sequences("tool_blade") == ()


def test_same_shortcut_twice_on_one_command_is_refused():
    shortcuts = ShortcutMap()
    result = shortcuts.assign("play_pause", 1, "Space")
    assert result.status is AssignStatus.DUPLICATE
    assert shortcuts.sequences("play_pause") == ("Space", "K")


# --- conflits --------------------------------------------------------------------------


def test_conflict_names_the_command_that_already_uses_the_shortcut():
    shortcuts = ShortcutMap()
    result = shortcuts.assign("tool_blade", 0, "R")
    assert result.status is AssignStatus.CONFLICT
    assert result.conflicts == ("tool_roll",)
    assert shortcuts.sequences("tool_blade") == ("B",)
    assert shortcuts.sequences("tool_roll") == ("R",)


def test_conflict_is_detected_across_secondary_shortcuts_and_equivalent_spellings():
    shortcuts = ShortcutMap()
    # « K » est le secondaire de play_pause ; « ctrl+shift+z » est le principal de redo.
    assert shortcuts.assign("tool_blade", 0, "k").conflicts == ("play_pause",)
    assert shortcuts.assign("tool_blade", 0, "shift+ctrl+z").conflicts == ("redo",)
    assert shortcuts.conflicts("Ctrl+Y") == ("redo",)
    assert shortcuts.conflicts("Ctrl+Y", exclude="redo") == ()


def test_conflicts_cross_action_and_key_scopes():
    shortcuts = ShortcutMap()
    # Ctrl+Z (QAction) contre une commande clavier : même règle.
    assert shortcuts.assign("zoom_in", 0, "Ctrl+Z").conflicts == ("undo",)


def test_replace_takes_the_shortcut_away_from_the_other_command():
    shortcuts = ShortcutMap()
    result = shortcuts.assign("tool_blade", 0, "R", replace=True)
    assert result.ok and result.displaced == ("tool_roll",)
    assert shortcuts.sequences("tool_roll") == ()
    assert shortcuts.command_for("R") == "tool_blade"


@pytest.mark.parametrize("sequence", ["Ctrl+C", "Ctrl+V", "Ctrl+X", "Esc", "Tab"])
def test_dangerous_shortcuts_are_refused_even_with_replace(sequence):
    shortcuts = ShortcutMap()
    result = shortcuts.assign("tool_blade", 0, sequence, replace=True)
    assert result.status is AssignStatus.RESERVED
    assert shortcuts.sequences("tool_blade") == ("B",)


# --- réinitialisation ---------------------------------------------------------------------


def test_reset_one_command_restores_its_defaults_only():
    shortcuts = ShortcutMap()
    shortcuts.assign("tool_blade", 0, "F6")
    shortcuts.assign("tool_roll", 0, "F7")
    shortcuts.reset("tool_blade")
    assert shortcuts.sequences("tool_blade") == ("B",)
    assert shortcuts.sequences("tool_roll") == ("F7",)


def test_reset_takes_back_a_default_that_another_command_borrowed():
    shortcuts = ShortcutMap()
    shortcuts.assign("tool_roll", 0, "B", replace=True)  # vole « B » à la lame
    assert shortcuts.sequences("tool_blade") == ()
    affected = shortcuts.reset("tool_blade")
    assert affected == ("tool_roll",)
    assert shortcuts.sequences("tool_blade") == ("B",)
    assert shortcuts.sequences("tool_roll") == ()
    assert validate_conflict_free(shortcuts)


def test_reset_all_restores_every_default():
    shortcuts = ShortcutMap()
    shortcuts.assign("tool_blade", 0, "F6")
    shortcuts.assign("play_pause", 0, None)
    shortcuts.assign("zoom_in", 0, "R", replace=True)
    shortcuts.reset_all()
    assert shortcuts.overrides() == {}
    assert all(shortcuts.is_default(c.id) for c in COMMANDS)


def validate_conflict_free(shortcuts: ShortcutMap) -> bool:
    seen: set[str] = set()
    for command in COMMANDS:
        for sequence in shortcuts.sequences(command.id):
            if sequence in seen:
                return False
            seen.add(sequence)
    return True


# --- sérialisation --------------------------------------------------------------------------


def test_serialization_only_stores_differences_and_round_trips():
    shortcuts = ShortcutMap()
    assert shortcuts.overrides() == {}
    shortcuts.assign("tool_blade", 0, "F6")
    shortcuts.assign("redo", 1, None)
    data = shortcuts.overrides()
    assert data == {"tool_blade": ["F6"], "redo": ["Ctrl+Shift+Z"]}
    restored = ShortcutMap.from_overrides(json.loads(json.dumps(data)))
    for command in COMMANDS:
        assert restored.sequences(command.id) == shortcuts.sequences(command.id)


@pytest.mark.parametrize("garbage", [None, 3, "x", [], {"tool_blade": 5}, {"tool_blade": [None, 3]}, {"nope": ["F1"]}])
def test_deserialization_never_raises_and_falls_back_to_defaults(garbage):
    restored = ShortcutMap.from_overrides(garbage)
    assert validate_conflict_free(restored)
    assert restored.sequences("tool_blade") in (("B",), ())


def test_deserialization_drops_invalid_and_reserved_entries():
    restored = ShortcutMap.from_overrides(
        {"tool_blade": ["Ctrl+Banane", "Ctrl+C", "f6", "F6", "F7", "F8"]}
    )
    assert restored.sequences("tool_blade") == ("F6", "F7")  # dédoublonné, borné à 2


def test_deserialization_resolves_conflicts_in_favour_of_the_explicit_choice():
    restored = ShortcutMap.from_overrides({"tool_blade": ["R"]})  # « R » = Roll par défaut
    assert restored.sequences("tool_blade") == ("R",)
    assert restored.sequences("tool_roll") == ()
    assert validate_conflict_free(restored)
    # La perte du défaut de Roll est visible : elle sera persistée.
    assert restored.overrides()["tool_roll"] == []


def test_deserialization_between_two_explicit_conflicts_keeps_the_first_command():
    restored = ShortcutMap.from_overrides({"tool_roll": ["F6"], "tool_blade": ["F6"]})
    assert validate_conflict_free(restored)
    owners = [c for c in ("tool_blade", "tool_roll") if "F6" in restored.sequences(c)]
    assert len(owners) == 1


# --- migration d'anciennes préférences --------------------------------------------------------


def test_old_preferences_without_shortcuts_load_with_defaults(tmp_path):
    old_file = {
        "theme_mode": "light",
        "language": "en",
        "performance_profile": "auto",
        "preview_quality": "auto",
    }
    settings_file_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    settings_file_path(tmp_path).write_text(json.dumps(old_file), encoding="utf-8")
    settings = load_user_settings(tmp_path)
    assert settings.theme_mode == "light"
    assert settings.shortcuts == {}
    assert ShortcutMap.from_overrides(settings.shortcuts).overrides() == {}


def test_shortcuts_survive_save_and_load(tmp_path):
    shortcuts = ShortcutMap()
    shortcuts.assign("tool_blade", 0, "F6")
    save_user_settings(UserSettings(shortcuts=shortcuts.overrides()), tmp_path)
    on_disk = json.loads(settings_file_path(tmp_path).read_text(encoding="utf-8"))
    assert on_disk["shortcuts"] == {"tool_blade": ["F6"]}
    loaded = load_user_settings(tmp_path)
    assert ShortcutMap.from_overrides(loaded.shortcuts).sequences("tool_blade") == ("F6",)


def test_corrupt_shortcuts_in_the_settings_file_do_not_break_other_settings(tmp_path):
    settings_file_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    settings_file_path(tmp_path).write_text(
        json.dumps({"theme_mode": "light", "shortcuts": "oops"}), encoding="utf-8"
    )
    settings = load_user_settings(tmp_path)
    assert settings.theme_mode == "light"
    assert settings.shortcuts == {}


# --- multiplateforme (sans dépendre de l'hôte) -----------------------------------------------------


def test_format_follows_platform_conventions():
    assert format_sequence("Ctrl+Shift+Z", "darwin") == "⇧⌘Z"
    assert format_sequence("Ctrl+S", "darwin") == "⌘S"
    assert format_sequence("Left", "darwin") == "←"
    assert format_sequence("Ctrl+Shift+Z", "win32") == "Ctrl+Shift+Z"
    assert format_sequence("Ctrl+S", "linux") == "Ctrl+S"
    assert format_sequence("Meta+L", "win32") == "Win+L"
    assert format_sequence("Meta+L", "darwin") == "⌃L"


def test_reserved_shortcuts_depend_on_the_platform():
    assert reserved_reason("Ctrl+H", "darwin") == "system"
    assert reserved_reason("Ctrl+H", "linux") is None
    assert reserved_reason("Alt+F4", "win32") == "system"
    assert reserved_reason("Alt+F4", "darwin") is None
    assert reserved_reason("Meta+E", "win32") == "system"
    assert reserved_reason("Meta+E", "linux") is None
    assert reserved_reason("Ctrl+C", "linux") == "clipboard"


def test_platform_reserved_shortcut_is_refused_by_the_map():
    mac = ShortcutMap(platform="darwin")
    linux = ShortcutMap(platform="linux")
    assert mac.assign("tool_blade", 0, "Ctrl+H").status is AssignStatus.RESERVED
    assert linux.assign("tool_blade", 0, "Ctrl+H").ok


def test_shifted_symbol_keys_fall_back_to_the_bare_symbol():
    # Sur un clavier US, « + » est Maj+= : Qt rapporte « Shift++ ».
    assert key_candidates("Shift++") == ("Shift++", "+")
    assert key_candidates("Ctrl+Shift+]") == ("Ctrl+Shift+]", "Ctrl+]")
    assert key_candidates("Shift+Left") == ("Shift+Left",)
    assert key_candidates("Shift+Z") == ("Shift+Z",)


def test_every_command_has_a_name_in_every_language():
    from ui import i18n

    original = i18n.current_language()
    try:
        for language in i18n.available_languages():
            i18n.set_language(language)
            for command in COMMANDS:
                assert not i18n.translate(command.name_key).startswith("["), (language, command.id)
            for category in Category:
                assert not i18n.translate(f"shortcuts.category.{category.value}").startswith("[")
    finally:
        i18n.set_language(original)
    assert COMMANDS_BY_ID["play_pause"].name_key == "shortcuts.command.play_pause"


def test_non_list_override_keeps_the_default_instead_of_unbinding():
    restored = ShortcutMap.from_overrides({"tool_blade": 5, "tool_roll": ["Ctrl+Banane"]})
    assert restored.sequences("tool_blade") == ("B",)
    assert restored.sequences("tool_roll") == ("R",)


# --- accords à plusieurs étapes ---------------------------------------------------


def test_chords_are_normalized_step_by_step():
    assert normalize_sequence("ctrl+k, ctrl+c") == "Ctrl+K, Ctrl+C"
    assert normalize_sequence("Ctrl+K,   C") == "Ctrl+K, C"
    assert normalize_sequence("Ctrl+K, ,") == "Ctrl+K, ,"  # la virgule est une touche
    assert normalize_sequence("A, B, C, D") == "A, B, C, D"
    assert format_sequence("Ctrl+K, Ctrl+C", "darwin") == "⌘K, ⌘C"
    assert format_sequence("Ctrl+K, Ctrl+C", "linux") == "Ctrl+K, Ctrl+C"


def test_a_chord_can_be_assigned_and_round_trips():
    shortcuts = ShortcutMap()
    assert shortcuts.assign("tool_blade", 1, "Ctrl+Alt+K, B").ok
    assert shortcuts.sequences("tool_blade") == ("B", "Ctrl+Alt+K, B")
    restored = ShortcutMap.from_overrides(json.loads(json.dumps(shortcuts.overrides())))
    assert restored.sequences("tool_blade") == ("B", "Ctrl+Alt+K, B")
    assert restored.command_for("Ctrl+Alt+K, B") == "tool_blade"
    assert shortcuts.continuations("Ctrl+Alt+K") == ("tool_blade",)


def test_a_chord_conflicts_with_its_own_first_step_both_ways():
    shortcuts = ShortcutMap()
    # « Ctrl+K » (couper) bloquerait tout accord qui commence par Ctrl+K.
    result = shortcuts.assign("tool_blade", 1, "Ctrl+K, B")
    assert result.conflicts == ("cut_at_playhead",)
    # Inversement, un raccourci simple ne peut pas être le début d'un accord existant.
    shortcuts.assign("tool_blade", 1, "Ctrl+Alt+K, B")
    assert shortcuts.assign("tool_roll", 0, "Ctrl+Alt+K").conflicts == ("tool_blade",)
    replaced = shortcuts.assign("tool_roll", 0, "Ctrl+Alt+K", replace=True)
    assert replaced.ok and shortcuts.sequences("tool_blade") == ("B",)


def test_two_overlapping_sequences_on_one_command_are_refused():
    shortcuts = ShortcutMap()
    assert shortcuts.assign("tool_blade", 1, "B, X").status is AssignStatus.DUPLICATE


def test_chord_reserved_checks_look_at_the_first_step():
    assert reserved_reason("Ctrl+C, X") == "clipboard"
    assert reserved_reason("Ctrl+K, Ctrl+C") is None
    assert reserved_reason("Ctrl+K, Meta+X", "win32") == "system"


def test_loading_resolves_chord_prefix_conflicts():
    restored = ShortcutMap.from_overrides({"tool_blade": ["Ctrl+K, B"]})
    assert restored.sequences("tool_blade") == ("Ctrl+K, B",)
    assert restored.sequences("cut_at_playhead") == ()  # Ctrl+K du défaut cède
    assert validate_conflict_free(restored)


def test_lenient_modifiers_only_apply_to_symbol_keys():
    from core.shortcuts import loose_candidates

    assert loose_candidates("Ctrl+=") == ("=",)
    assert "+" in loose_candidates("Ctrl+Shift++")
    assert loose_candidates("Ctrl+K") == ()
    assert loose_candidates("=") == ()
    assert COMMANDS_BY_ID["zoom_in"].lenient_modifiers
    assert COMMANDS_BY_ID["zoom_out"].lenient_modifiers
    assert not COMMANDS_BY_ID["tool_blade"].lenient_modifiers
