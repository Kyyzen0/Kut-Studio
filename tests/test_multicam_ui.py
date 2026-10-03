"""Multicam dans l'interface : touches d'angle, bascule en direct, remplacement, aplatir, menu et couleurs.

Fenêtre réelle (offscreen) : les touches sont envoyées comme le ferait un clavier (QWERTY, AZERTY avec Maj, pavé
numérique), puis on lit le projet, l'historique et la timeline.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMenu

from core.multicam_model import AudioMode, SyncMethod
from multicam_stubs import keep_preview_player_off_the_disk
from core.multicam_ops import AngleSpec, create_multicam_source, insert_multicam_clip, set_audio_policy
from core.project_model import Clip, MediaAsset, Project, Track


@pytest.fixture
def window(qtbot, monkeypatch):
    from ui import i18n
    from ui.main_window import MainWindow

    i18n.set_language("fr")  # la langue est un état global : les messages attendus ci-dessous sont français
    monkeypatch.setattr("ui.main_window.QMessageBox.information", lambda *_, **__: None)
    monkeypatch.setattr("ui.main_window.QMessageBox.critical", lambda *_, **__: None)
    main = MainWindow()
    qtbot.addWidget(main)
    main.timeline_timer.stop()
    keep_preview_player_off_the_disk(main, monkeypatch)
    return main


def _load(window, project: Project) -> None:
    window.project = project
    window.history.reset(project)
    window.timeline_panel.set_project(project)
    window._update_timeline_duration()


def _video(asset_id: str) -> MediaAsset:
    return MediaAsset(asset_id, f"/media/{asset_id}.mp4", asset_id, 60.0, 1920, 1080, 30.0, "video", True)


def _multicam_window(window, *, angle: str = "angle-1"):
    project = Project(
        "Concert",
        media_assets=[_video("camA"), _video("camB"), _video("camC")],
        tracks=[Track("V1", "V1", "video"), Track("V2", "V2", "video"), Track("A1", "A1", "audio")],
    )
    source = create_multicam_source(
        project,
        [AngleSpec(asset_id="camA", name="Wide"), AngleSpec(asset_id="camB", name="Close-up"),
         AngleSpec(asset_id="camC", name="Drone")],
        name="Concert",
    )
    set_audio_policy(project, source.id, AudioMode.FOLLOW_VIDEO)
    segment = insert_multicam_clip(project, source.id, "V1", 0.0, angle_id=angle)
    _load(window, project)
    return project, source, segment


def _clips(window):
    return sorted((c for t in window.project.tracks for c in t.clips), key=lambda c: c.timeline_start)


def test_an_angle_key_cuts_at_the_playhead_and_is_one_undoable_history_entry(window):
    _multicam_window(window)
    window.playhead_seconds = 5.0
    QTest.keyClick(window, Qt.Key_2)
    clips = _clips(window)
    assert [(c.angle_id, round(c.timeline_start, 2)) for c in clips] == [("angle-1", 0.0), ("angle-2", 5.0)]
    assert window.history.undo_label == "Angle 2 : Close-up"
    window.undo_last()
    assert len(_clips(window)) == 1 and _clips(window)[0].angle_id == "angle-1"
    window.redo_last()
    assert [c.angle_id for c in _clips(window)] == ["angle-1", "angle-2"]


def test_azerty_shift_digit_and_the_numeric_keypad_reach_the_angles(window):
    _multicam_window(window)
    window.playhead_seconds = 4.0
    QTest.keyClick(window, Qt.Key_3, Qt.ShiftModifier)          # AZERTY : le 3 s'obtient avec Maj
    window.playhead_seconds = 8.0
    QTest.keyClick(window, Qt.Key_2, Qt.KeypadModifier)         # pavé numérique
    assert [c.angle_id for c in _clips(window)] == ["angle-1", "angle-3", "angle-2"]


def test_a_live_session_builds_the_edit_one_cut_per_key(window):
    _multicam_window(window)
    for time, number in [(3.0, 2), (6.0, 3), (9.0, 1), (12.0, 2)]:
        window.playhead_seconds = time
        assert window.switch_multicam_angle(number) is not None
    assert [c.angle_id for c in _clips(window)] == ["angle-1", "angle-2", "angle-3", "angle-1", "angle-2"]
    for _ in range(4):
        window.undo_last()
    assert len(_clips(window)) == 1


def test_the_same_angle_again_does_not_add_a_useless_cut_or_history_entry(window):
    _multicam_window(window)
    window.playhead_seconds = 5.0
    entries = len(window.history)
    assert window.switch_multicam_angle(1) is None
    assert len(_clips(window)) == 1 and len(window.history) == entries


def test_angle_keys_are_silent_without_any_multicam_source_and_explain_otherwise(window):
    window.playhead_seconds = 1.0
    before = [c.id for c in _clips(window)]
    QTest.keyClick(window, Qt.Key_2)
    assert [c.id for c in _clips(window)] == before and window.statusBar().currentMessage() == ""
    _multicam_window(window)
    window.playhead_seconds = 100.0  # hors du segment
    QTest.keyClick(window, Qt.Key_2)
    assert window.statusBar().currentMessage() == "Aucun segment Multicam sous la tête de lecture."
    window.playhead_seconds = 5.0
    window.switch_multicam_angle(7)
    assert "3 angle" in window.statusBar().currentMessage()
    assert len(_clips(window)) == 1


def test_angle_keys_do_not_fire_while_typing_in_a_text_field(window, qtbot):
    from PySide6.QtWidgets import QLineEdit

    _multicam_window(window)
    field = QLineEdit(window)
    qtbot.addWidget(field)
    window.playhead_seconds = 5.0
    field.setFocus()
    QTest.keyClick(field, Qt.Key_2)
    assert len(_clips(window)) == 1 and field.text() == "2"


def test_replace_angle_from_the_context_menu_signal_makes_no_cut_and_is_undoable(window):
    _multicam_window(window)
    window.playhead_seconds = 5.0
    window.switch_multicam_angle(2)
    second = _clips(window)[1]
    window.timeline_panel.multicam_replace_requested.emit(second.id, 2)
    assert [c.angle_id for c in _clips(window)] == ["angle-1", "angle-3"]
    assert window.history.undo_label == "Remplacer par l'angle 3 : Drone"
    window.undo_last()
    assert [c.angle_id for c in _clips(window)] == ["angle-1", "angle-2"]


def test_the_clip_menu_offers_one_replace_entry_per_angle_for_a_segment_only(window):
    _project_, _source, segment = _multicam_window(window)
    panel = window.timeline_panel
    view = panel.find_view_by_id(segment.id)
    menu = QMenu()
    replace_actions, flatten, settings = panel._add_multicam_menu_entries(menu, view)
    assert len(replace_actions) == 3 and flatten is not None and settings is not None
    assert [a.text() for a in replace_actions] == ["1. Wide", "2. Close-up", "3. Drone"]
    plain = QMenu()
    assert panel._add_multicam_menu_entries(plain, None) == ({}, None, None)


def test_flatten_from_the_menu_signal_gives_ordinary_clips_and_is_undoable(window):
    _multicam_window(window)
    window.playhead_seconds = 5.0
    window.switch_multicam_angle(2)
    second = _clips(window)[1]
    window.timeline_panel.multicam_flatten_requested.emit(second.id)
    clips = _clips(window)
    assert [bool(c.sequence_id) for c in clips] == [True, False] and clips[1].asset_id == "camB"
    window.undo_last()
    assert [c.sequence_id != "" for c in _clips(window)] == [True, True]


def test_flatten_that_cannot_keep_the_picture_is_refused_with_a_message(window):
    from dataclasses import replace

    _project_, _source, segment = _multicam_window(window)
    segment.transform = replace(segment.transform, scale=1.5)
    window.timeline_panel.multicam_flatten_requested.emit(segment.id)
    assert "transformation" in window.statusBar().currentMessage()
    assert _clips(window)[0].sequence_id != ""


def test_creating_a_multicam_from_selected_timeline_clips_replaces_them_by_one_segment(window):
    project = Project(
        "t", media_assets=[_video("camA"), _video("camB")],
        tracks=[Track("V1", "V1", "video", clips=[Clip("a", "camA", "V1", 10.0, 0.0, 20.0, label="Cam A")]),
                Track("V2", "V2", "video", clips=[Clip("b", "camB", "V2", 12.0, 0.0, 20.0, label="Cam B")])],
    )
    _load(window, project)
    window.timeline_panel._set_selection(["a", "b"], "a", announce=False)
    from ui.multicam_dialogs import CreationChoice

    result = window.create_multicam_from_timeline_selection(
        choice=CreationChoice("Interview", SyncMethod.POSITIONS, (("a", "Cam A"), ("b", "Cam B"))),
    )
    assert result is not None
    sequence, segment = result
    assert sequence.multicam is not None and [a.name for a in sequence.multicam.angles] == ["Cam A", "Cam B"]
    assert [c.id for c in _clips(window)] == [segment.id]
    assert window.history.undo_label == "Créer la séquence Multicam « Interview »"
    window.undo_last()
    assert {c.id for c in _clips(window)} == {"a", "b"}
    window.timeline_panel._set_selection(["a"], "a", announce=False)
    assert window.create_multicam_from_timeline_selection() is None
    assert "au moins deux clips" in window.statusBar().currentMessage()


def test_a_segment_clip_carries_its_angle_in_the_timeline_and_paints_the_angle_colour(window):
    from PySide6.QtGui import QColor

    from ui.theme import THEMES

    _project_, _source, segment = _multicam_window(window, angle="angle-2")
    view = window.timeline_panel.find_view_by_id(segment.id)
    assert view.is_multicam and view.angle_index == 1 and view.angle_name == "Close-up"
    widget = window.timeline_panel.clip_widgets[segment.id]
    widget.resize(240, 48)
    image = widget.grab().toImage()
    expected = QColor(THEMES["dark"].angle_colors[1])
    strip = image.pixelColor(1, 24)
    assert (strip.red(), strip.green(), strip.blue()) == (expected.red(), expected.green(), expected.blue())
    assert "Close-up" in widget.toolTip()


def test_the_multicam_commands_exist_in_the_shortcut_table_with_handlers(window):
    ids = {f"multicam_angle_{n}" for n in range(1, 10)} | {
        "multicam_viewer", "multicam_create", "multicam_open_source", "multicam_flatten", "multicam_settings",
    }
    assert ids <= {c.id for c in window.shortcuts.shortcut_map.commands}
    assert not window.shortcuts.missing_handlers()
    assert window.shortcuts.shortcut_map.sequences("multicam_angle_4") == ("4",)


# --- moniteur Multicam dans la fenêtre ----------------------------------------------------------------------------------


def _with_stub_feeds(window):
    from core.multicam_feed import FeedPool
    from multicam_stubs import StubFeed

    StubFeed.created = []
    window.multicam_viewer._pool = FeedPool(StubFeed)  # noqa: SLF001 - flux factices : ni FFmpeg ni média réel
    return StubFeed


def test_a_double_click_on_a_segment_shows_the_multicam_monitor_instead_of_opening_the_source(window):
    project, source, segment = _multicam_window(window)
    _with_stub_feeds(window)
    widget = window.timeline_panel.clip_widgets[segment.id]
    QTest.mouseDClick(widget, Qt.LeftButton)
    assert window._monitor_stack.currentWidget() is window.multicam_viewer   # noqa: SLF001
    assert window.project.active_sequence_id != source.id                     # la source n'est pas ouverte


def test_the_source_stays_reachable_from_the_menu_command(window):
    project, source, segment = _multicam_window(window)
    window.timeline_panel._set_selection([segment.id], segment.id, announce=False)
    window.open_multicam_source()
    assert window.project.active_sequence_id == source.id


def test_the_monitor_command_toggles_between_the_viewer_and_the_multicam_monitor(window):
    _multicam_window(window)
    _with_stub_feeds(window)
    window.toggle_multicam_viewer()
    assert window._monitor_stack.currentWidget() is window.multicam_viewer   # noqa: SLF001
    window.toggle_multicam_viewer()
    assert window._monitor_stack.currentWidget() is window.preview_panel     # noqa: SLF001


def test_the_monitor_command_explains_when_the_project_has_no_multicam_source(window):
    window.toggle_multicam_viewer()
    assert window._monitor_stack.currentWidget() is window.preview_panel     # noqa: SLF001
    assert window.statusBar().currentMessage() == "Ce projet ne contient aucune source Multicam."


def test_clicking_a_tile_of_the_monitor_cuts_the_segment_at_the_playhead(window):
    _multicam_window(window)
    _with_stub_feeds(window)
    window.show_multicam_viewer()
    window.playhead_seconds = 7.0
    window.multicam_viewer.refresh()
    window.multicam_viewer.angle_requested.emit(2)
    assert [c.angle_id for c in _clips(window)] == ["angle-1", "angle-3"]
    assert window.history.undo_label == "Angle 3 : Drone"


def test_showing_the_monitor_on_a_segment_moves_the_playhead_into_it(window):
    project, source, segment = _multicam_window(window)
    _with_stub_feeds(window)
    window.playhead_seconds = 500.0
    window.show_multicam_viewer(segment.id)
    assert window.playhead_seconds == segment.timeline_start


def test_closing_the_window_stops_the_monitor_feeds(window, monkeypatch):
    monkeypatch.setattr("core.cache_keys.file_exists", lambda *_a, **_k: True)   # médias factices considérés présents
    _multicam_window(window)
    stubs = _with_stub_feeds(window)
    window.show()                       # le moniteur ne lit ses flux que visible
    window.show_multicam_viewer()
    window.multicam_viewer.refresh()
    feeds = list(window.multicam_viewer._pool._feeds.values())    # noqa: SLF001
    assert feeds
    window.multicam_viewer.shutdown()
    assert all(feed.closed for feed in feeds) and stubs is not None


def test_the_proxy_request_covers_every_camera_of_the_source_and_the_tile_menu_opens_the_source_on_an_angle(window, monkeypatch):
    project, source, segment = _multicam_window(window)
    requested = []
    monkeypatch.setattr(window, "_request_proxies", lambda assets: requested.append(sorted(a.id for a in assets)) or len(assets))
    window.generate_proxies_for_multicam()
    assert requested == [["camA", "camB", "camC"]]
    window.playhead_seconds = 1.0
    window.open_multicam_source_at_angle(2)
    assert window.project.active_sequence_id == source.id
    selected = window.timeline_panel.selected_clip_id
    assert selected == source.tracks[2].clips[0].id                      # le clip de la caméra 3
    window.playhead_seconds = 500.0
    window.go_to_parent_sequence()
    window.playhead_seconds = 500.0
    window.open_multicam_source_at_angle(0)
    assert "Aucun segment Multicam" in window.statusBar().currentMessage()
