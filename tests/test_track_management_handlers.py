"""Gestion des pistes depuis l'interface (``ui/main_window_mixins/track_management.py``).

Chaque action est déclenchée comme l'utilisateur la déclenche : par le signal de l'en-tête de piste de la timeline
(``timeline_panel.*``, branchés dans ``MainWindow``) ou par l'action du menu Séquence. On vérifie l'état du projet, le
refus présenté à l'utilisateur, l'entrée d'historique et ce qu'annuler / rétablir en font.
"""

from __future__ import annotations

import pytest
from main_window_harness import build_window, install_dialogs, menu_action, track, track_ids

from ui import i18n


@pytest.fixture
def dialogs(monkeypatch):
    return install_dialogs(monkeypatch)


@pytest.fixture
def window(qtbot, monkeypatch, tmp_path, dialogs):
    return build_window(qtbot, monkeypatch, tmp_path / "config")


def _warnings(dialogs) -> list[str]:
    return [text for _title, text in dialogs.of_kind("warning")]


# --- ajout --------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("kind", "label_key"), [
    ("video", "tracks.add_video_long"),
    ("audio", "tracks.add_audio_long"),
    ("subtitle", "tracks.add_subtitle_long"),
])
def test_adding_a_track_from_the_timeline_is_one_undoable_step(window, kind, label_key):
    before = track_ids(window)

    window.timeline_panel.add_track_requested.emit(kind)

    added = [item for item in window.project.tracks if item.id not in before]
    assert [item.type for item in added] == [kind]
    assert window.history.undo_label == i18n.translate(label_key)
    assert window.project_dirty
    window.undo_last()
    assert track_ids(window) == before
    window.redo_last()
    assert [item.id for item in window.project.tracks if item.id not in before] == [added[0].id]


def test_the_sequence_menu_adds_an_audio_track(window):
    audio_before = [item.id for item in window.project.tracks if item.type == "audio"]

    menu_action(window, "tracks.add_audio_long").trigger()

    audio_after = [item.id for item in window.project.tracks if item.type == "audio"]
    assert len(audio_after) == len(audio_before) + 1
    assert window.history.undo_label == i18n.translate("tracks.add_audio_long")


def test_an_unknown_track_type_is_refused_with_a_message_and_no_history(window, dialogs):
    before = track_ids(window)

    window.timeline_panel.add_track_requested.emit("midi")

    assert track_ids(window) == before
    assert len(_warnings(dialogs)) == 1
    assert not window.history.can_undo


# --- suppression --------------------------------------------------------------------------------------------------


def test_removing_an_empty_track_then_undoing_puts_it_back_in_place(window):
    before = track_ids(window)
    assert track(window, "A1").clips == []

    window.timeline_panel.remove_track_requested.emit("A1")

    assert "A1" not in track_ids(window)
    assert window.history.undo_label == i18n.translate("tracks.remove")
    window.undo_last()
    assert track_ids(window) == before, "l'ordre des pistes est restauré, pas seulement leur présence"


def test_a_track_that_holds_clips_is_not_removed_and_the_user_is_told_why(window, dialogs):
    before = track_ids(window)
    clips = [clip.id for clip in track(window, "V1").clips]

    window.timeline_panel.remove_track_requested.emit("V1")

    assert track_ids(window) == before
    assert [clip.id for clip in track(window, "V1").clips] == clips
    assert len(_warnings(dialogs)) == 1 and "V1" in _warnings(dialogs)[0] and "2 clip" in _warnings(dialogs)[0]
    assert not window.history.can_undo


def test_the_remove_menu_without_a_selection_explains_and_removes_nothing(window, dialogs):
    assert window.timeline_panel.selected_clip_id is None and window.properties_panel.selected_clip is None
    before = track_ids(window)

    menu_action(window, "tracks.remove").trigger()

    assert track_ids(window) == before
    assert len(dialogs.of_kind("information")) == 1
    assert not window.history.can_undo


def test_the_remove_menu_targets_the_track_of_the_selected_clip(window, dialogs):
    window.timeline_panel.select_clip("b_roll")

    menu_action(window, "tracks.remove").trigger()

    assert "V2" in track_ids(window), "V2 porte b_roll : refusée"
    assert "V2" in _warnings(dialogs)[0]


def test_removing_the_last_track_leaves_an_empty_project_that_accepts_a_new_track(window):
    for item in window.project.tracks:
        item.clips.clear()
    window.project.transitions.clear()
    window._reload_timeline_preserving_selection()

    for track_id in list(track_ids(window)):
        window.timeline_panel.remove_track_requested.emit(track_id)

    assert window.project.tracks == []
    window.timeline_panel.add_track_requested.emit("video")
    assert [item.type for item in window.project.tracks] == ["video"]


# --- renommage ----------------------------------------------------------------------------------------------------


def test_renaming_from_the_header_trims_the_name_and_undo_restores_it(window):
    window.timeline_panel.rename_track_requested.emit("V2", "  B-roll  ")

    assert track(window, "V2").name == "B-roll"
    assert track(window, "V2").id == "V2", "l'identifiant technique ne change pas"
    assert window.history.undo_label == i18n.translate("tracks.rename")
    window.undo_last()
    assert track(window, "V2").name == "V2"


def test_a_blank_name_is_ignored_without_history(window, dialogs):
    window.timeline_panel.rename_track_requested.emit("V2", "   ")

    assert track(window, "V2").name == "V2"
    assert not window.history.can_undo
    assert dialogs.shown == []


def test_a_name_already_used_by_another_track_is_refused(window, dialogs):
    window.timeline_panel.rename_track_requested.emit("V2", "V1")

    assert track(window, "V2").name == "V2"
    assert len(_warnings(dialogs)) == 1 and "V1" in _warnings(dialogs)[0]
    assert not window.history.can_undo


def test_the_rename_menu_asks_for_the_name_of_the_selected_clip_track(window, dialogs):
    window.timeline_panel.select_clip("b_roll")
    dialogs.text_reply = ("Plans de coupe", True)

    menu_action(window, "tracks.rename").trigger()

    assert dialogs.inputs and dialogs.inputs[-1][1] == "V2", "le dialogue propose le nom actuel"
    assert track(window, "V2").name == "Plans de coupe"


def test_cancelling_the_rename_dialog_changes_nothing(window, dialogs):
    window.timeline_panel.select_clip("b_roll")
    dialogs.text_reply = ("Autre nom", False)

    menu_action(window, "tracks.rename").trigger()

    assert track(window, "V2").name == "V2"
    assert not window.history.can_undo


# --- verrou, visibilité, muet -------------------------------------------------------------------------------------


def test_a_locked_track_refuses_moves_solo_mute_and_arm(window, dialogs):
    window.timeline_panel.toggle_track_lock_requested.emit("A1", True)
    assert track(window, "A1").locked
    order = track_ids(window)
    steps = len(window.history._undo_stack)

    window.timeline_panel.move_track_up_requested.emit("A1")
    window.timeline_panel.solo_toggled.emit("A1", True)
    window.timeline_panel.toggle_track_muted_requested.emit("A1", True)
    window.timeline_panel.arm_toggled.emit("A1", True)

    a1 = track(window, "A1")
    assert track_ids(window) == order
    assert (a1.solo, a1.muted, a1.armed) == (False, False, False)
    assert len(_warnings(dialogs)) == 1 and "verrouill" in _warnings(dialogs)[0].lower()
    assert len(window.history._undo_stack) == steps, "aucune entrée d'historique pour une action refusée"


def test_unlocking_restores_the_actions(window):
    window.timeline_panel.toggle_track_lock_requested.emit("A1", True)
    window.timeline_panel.toggle_track_lock_requested.emit("A1", False)

    window.timeline_panel.toggle_track_muted_requested.emit("A1", True)

    assert track(window, "A1").muted
    assert window.history.undo_label == i18n.translate("tracks.toggle_mute")


def test_hiding_a_track_is_undoable(window):
    window.timeline_panel.toggle_track_visible_requested.emit("V2", False)

    assert track(window, "V2").visible is False
    assert window.history.undo_label == i18n.translate("tracks.toggle_visible")
    window.undo_last()
    assert track(window, "V2").visible is True


def test_muting_from_the_timeline_is_reflected_in_the_mixer(window):
    window.timeline_panel.toggle_track_muted_requested.emit("A1", True)

    assert track(window, "A1").muted
    strip = window.mixer_panel.strip_for("A1")
    assert strip is not None and strip.mute_button.isChecked()


@pytest.mark.parametrize(("key", "attribute"), [
    ("tracks.toggle_lock", "locked"),
    ("tracks.toggle_visible", "visible"),
    ("tracks.toggle_mute", "muted"),
])
def test_the_sequence_menu_toggles_the_selected_clip_track(window, key, attribute):
    window.timeline_panel.select_clip("b_roll")
    before = getattr(track(window, "V2"), attribute)

    menu_action(window, key).trigger()

    assert getattr(track(window, "V2"), attribute) is (not before)
    assert getattr(track(window, "V1"), attribute) is before, "seule la piste du clip sélectionné change"
    assert window.history.undo_label == i18n.translate(key)


# --- ordre --------------------------------------------------------------------------------------------------------


def test_moving_a_track_down_then_undoing_restores_the_order(window):
    before = track_ids(window)

    window.timeline_panel.move_track_down_requested.emit("V1")

    assert track_ids(window)[:2] == ["V2", "V1"]
    assert window.history.undo_label == i18n.translate("tracks.move_down")
    window.undo_last()
    assert track_ids(window) == before


def test_moving_past_either_end_is_a_no_op_without_history(window):
    before = track_ids(window)

    window.timeline_panel.move_track_up_requested.emit(before[0])
    window.timeline_panel.move_track_down_requested.emit(before[-1])

    assert track_ids(window) == before
    assert not window.history.can_undo


def test_the_move_menu_moves_the_selected_clip_track(window):
    window.timeline_panel.select_clip("b_roll")

    menu_action(window, "tracks.move_up").trigger()

    assert track_ids(window)[:2] == ["V2", "V1"]
    assert window.history.undo_label == i18n.translate("tracks.move_up")


# --- solo, armement, hauteur, repli -------------------------------------------------------------------------------


def test_solo_and_arm_from_the_header_are_recorded(window):
    window.timeline_panel.solo_toggled.emit("A1", True)
    assert track(window, "A1").solo
    assert window.history.undo_label == i18n.translate("history.track.solo")

    window.timeline_panel.arm_toggled.emit("A1", True)
    assert track(window, "A1").armed
    assert window.history.undo_label == i18n.translate("history.track.arm")

    window.undo_last()
    assert track(window, "A1").armed is False and track(window, "A1").solo is True


def test_only_an_audio_track_can_be_armed(window):
    window.timeline_panel.arm_toggled.emit("V1", True)

    assert track(window, "V1").armed is False
    assert not window.history.can_undo


def test_the_height_cycles_compact_normal_large_one_step_each(window):
    seen = []
    for _ in range(3):
        window.timeline_panel.height_cycle_requested.emit("V1")
        seen.append(track(window, "V1").height_mode)

    assert seen == ["large", "compact", "normal"]
    assert window.history.undo_label == i18n.translate("history.track.height")
    window.undo_last()
    assert track(window, "V1").height_mode == "compact"


def test_collapsing_a_track_is_undoable(window):
    window.timeline_panel.collapse_toggled.emit("V1", True)

    assert track(window, "V1").collapsed is True
    assert window.history.undo_label == i18n.translate("history.track.collapse")
    window.undo_last()
    assert track(window, "V1").collapsed is False


def test_an_unknown_track_id_changes_nothing(window, dialogs):
    before = [(item.id, item.solo, item.collapsed, item.height_mode) for item in window.project.tracks]

    window.timeline_panel.solo_toggled.emit("ZZ", True)
    window.timeline_panel.height_cycle_requested.emit("ZZ")
    window.timeline_panel.collapse_toggled.emit("ZZ", True)
    window.timeline_panel.move_track_up_requested.emit("ZZ")

    assert [(item.id, item.solo, item.collapsed, item.height_mode) for item in window.project.tracks] == before
    assert not window.history.can_undo
