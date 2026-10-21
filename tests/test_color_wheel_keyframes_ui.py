"""Étape 8 de l'ADR-0008 : un losange par roue (quatre canaux ensemble) sur la page Couleur (hors écran, vraie fenêtre).

Les sept réglages de l'inspecteur : ``tests/test_color_keyframes_ui.py``. Le visualiseur est noir hors écran : on lit ce que le
moniteur reçoit (``effective_grade``), pas l'image.
"""

from __future__ import annotations

import pytest
from main_window_harness import build_window, install_dialogs

from core.animation import InterpolationType, Keyframe
from core.color_grading import WHEELS, ColorGrade, Wheel
from core.color_nodes import ColorNode, ColorNodeGraph
from core.grade_targets import WHEEL_CHANNELS, effective_grade
from core.workspace_state import PAGE_COLOR, PAGE_EDIT
from ui import i18n


@pytest.fixture
def window(qtbot, monkeypatch, tmp_path):
    install_dialogs(monkeypatch)
    win = build_window(qtbot, monkeypatch, tmp_path / "config")
    monkeypatch.setattr(win.preview_panel, "preview_at", lambda *_a, **_k: None)
    monkeypatch.setattr(win, "_present_cached_preview_at", lambda *_a, **_k: False)
    win.set_usage_level("advanced")
    win.resize(1440, 900)
    win.show()
    qtbot.waitExposed(win)
    return win


def _prepared(win, *, grade=None, locked: bool = False):
    track = win.project.tracks[0]
    clip = track.clips[0]
    clip.color_grade = grade
    clip.animation = []
    track.locked = locked
    win.history.reset(win.project)
    win._reload_timeline_preserving_selection(clip.id)
    win.on_clip_selected(clip.id)
    win.switch_page(PAGE_COLOR)
    return track, clip


def _clip(win):
    return win.project.tracks[0].clips[0]


def _cell(win, wheel):
    return win.color_panel.wheels.cells[wheel]


def _frames(clip, property_id):
    return sorted((k.time_seconds, k.value) for k in clip.animation if k.property_name == property_id)


def _ids(wheel):
    return [f"color.{wheel}.{channel}" for channel in WHEEL_CHANNELS]


def _kf(prop, time, value):
    return Keyframe(prop, time, value, InterpolationType.LINEAR)


def _nodes():
    return ColorNodeGraph.serial((ColorNode("n1", ColorGrade()), ColorNode("n2", ColorGrade())))


def _move(win, wheel, value):
    """Le geste d'un mouvement de la roue (le palet ou la molette émettent ce signal)."""
    win.color_panel.wheels.wheels[wheel].changed.emit(value)


def test_each_wheel_has_its_own_diamond_row_built_with_it(window):
    _prepared(window)
    cells = window.color_panel.wheels.cells
    assert tuple(cells) == WHEELS
    for name, cell in cells.items():
        assert cell.wheel is window.color_panel.wheels.wheels[name] and cell.wheel.parentWidget() is cell
        assert cell.keys.diamond.accessibleName() and cell.keys.diamond.isEnabled()
        assert not cell.keys.diamond.isChecked() and not cell.keys.diamond.animated
        assert not cell.keys.previous.isEnabled() and not cell.keys.next.isEnabled()


def test_the_diamond_keys_the_four_channels_together_and_removes_them_in_one_step(window):
    _track, clip = _prepared(window, grade=ColorGrade(gain=Wheel(0.1, 0.0, -0.1, 0.2)))
    window.seek_to_position(1.0)
    entries = len(window.history)
    _cell(window, "gain").keys.diamond.click()
    assert len(window.history) == entries + 1
    assert {pid: _frames(clip, pid) for pid in _ids("gain")} == {
        "color.gain.r": [(1.0, 0.1)], "color.gain.g": [(1.0, 0.0)], "color.gain.b": [(1.0, -0.1)], "color.gain.y": [(1.0, 0.2)]}
    assert not any(k.property_name.startswith(("color.lift", "color.gamma", "color.offset")) for k in clip.animation)
    cell = _cell(window, "gain")
    assert cell.keys.diamond.isChecked() and cell.keys.diamond.animated
    assert not _cell(window, "lift").keys.diamond.animated
    cell.keys.diamond.click()
    assert not clip.animation and len(window.history) == entries + 2
    assert clip.color_grade.gain == Wheel(0.1, 0.0, -0.1, 0.2), "la roue garde les valeurs qu'elle montrait"
    window.undo_last()
    assert len(_clip(window).animation) == 4 and _cell(window, "gain").keys.diamond.isChecked()
    window.undo_last()
    assert not _clip(window).animation


def test_moving_an_animated_wheel_writes_the_four_keys_at_the_playhead_and_leaves_the_base(window):
    _track, clip = _prepared(window, grade=ColorGrade(lift=Wheel(0.0, 0.0, 0.0, 0.1)))
    window.seek_to_position(1.0)
    _cell(window, "lift").keys.diamond.click()
    window.seek_to_position(2.0)
    entries = len(window.history)
    for amount in (0.2, 0.3, 0.4):
        _move(window, "lift", Wheel(0.05, -0.02, -0.03, amount))
    window._finalize_color_history()
    assert len(window.history) == entries + 1, "un mouvement de roue, une étape d'historique"
    assert _frames(clip, "color.lift.y") == [(1.0, 0.1), (2.0, 0.4)]
    assert _frames(clip, "color.lift.r") == [(1.0, 0.0), (2.0, 0.05)]
    assert clip.color_grade.lift == Wheel(0.0, 0.0, 0.0, 0.1), "la base ne bouge pas"
    assert effective_grade(clip, 2.0).lift == Wheel(0.05, -0.02, -0.03, 0.4)
    window.undo_last()
    assert _frames(_clip(window), "color.lift.y") == [(1.0, 0.1)]
    window.redo_last()
    assert _frames(_clip(window), "color.lift.y") == [(1.0, 0.1), (2.0, 0.4)]


def test_an_unanimated_wheel_still_writes_its_base_value(window):
    _track, clip = _prepared(window)
    window.seek_to_position(1.0)
    _move(window, "gamma", Wheel(y=0.3))
    window._finalize_color_history()
    assert clip.color_grade.gamma == Wheel(y=0.3) and not clip.animation


def test_a_wheel_with_one_animated_channel_sends_it_to_the_key_and_the_others_to_the_base(window):
    _track, clip = _prepared(window, grade=ColorGrade(offset=Wheel(0.0, 0.0, 0.0, 0.5)))
    clip.animation = [_kf("color.offset.y", 0.0, 0.0), _kf("color.offset.y", 4.0, 1.0)]
    window._reload_timeline_preserving_selection(clip.id)
    window.on_clip_selected(clip.id)
    window.seek_to_position(2.0)
    assert window.color_panel.wheels.wheels["offset"].wheel.y == pytest.approx(0.5), "la roue montre la valeur évaluée"
    _move(window, "offset", Wheel(0.1, -0.05, -0.05, 0.8))
    window._finalize_color_history()
    now = _clip(window)
    assert _frames(now, "color.offset.y") == [(0.0, 0.0), (2.0, 0.8), (4.0, 1.0)]
    assert now.color_grade.offset == Wheel(0.1, -0.05, -0.05, 0.5), "r, v, b en base ; le maître animé garde sa base (0,5)"
    assert not [k for k in now.animation if k.property_name in ("color.offset.r", "color.offset.g", "color.offset.b")]


def test_the_wheels_and_diamonds_follow_the_playhead_without_writing(window):
    _track, clip = _prepared(window)
    clip.animation = [_kf("color.gain.y", 1.0, -0.5), _kf("color.gain.y", 3.0, 0.5)]
    window._reload_timeline_preserving_selection(clip.id)
    window.on_clip_selected(clip.id)
    entries = len(window.history)
    states = []
    for position in (0.0, 1.0, 2.0, 2.5):
        window.seek_to_position(position)
        cell = _cell(window, "gain")
        states.append((cell.keys.diamond.isChecked(), cell.keys.diamond.animated, round(cell.wheel.wheel.y, 3)))
    assert states == [(False, True, -0.5), (False, True, -0.5), (False, True, 0.0), (False, True, 0.25)]    # canal y seul : jamais plein
    assert _clip(window).color_grade is None and len(window.history) == entries
    assert not _cell(window, "lift").keys.diamond.animated


def test_a_full_wheel_diamond_is_filled_under_its_keys_and_outlined_between_them(window):
    _track, clip = _prepared(window)
    window.seek_to_position(1.0)
    _cell(window, "lift").keys.diamond.click()
    window.seek_to_position(3.0)
    _cell(window, "lift").keys.diamond.click()
    window.seek_to_position(2.0)
    cell = _cell(window, "lift")
    assert not cell.keys.diamond.isChecked() and cell.keys.diamond.animated
    assert cell.keys.previous.isEnabled() and cell.keys.next.isEnabled()
    cell.keys.next.click()
    assert window.playhead_seconds == pytest.approx(3.0) and _cell(window, "lift").keys.diamond.isChecked()
    assert not _cell(window, "lift").keys.next.isEnabled()
    _cell(window, "lift").keys.previous.click()
    assert window.playhead_seconds == pytest.approx(1.0)


def test_the_page_reset_removes_the_keys_with_the_grade_and_undo_restores_both(window):
    _track, clip = _prepared(window, grade=ColorGrade(lift=Wheel(0.0, 0.0, 0.0, 0.2)))
    window.seek_to_position(1.0)
    _cell(window, "lift").keys.diamond.click()
    entries = len(window.history)
    window.color_panel.reset_button.click()
    assert len(window.history) == entries + 1
    assert not clip.animation and clip.color_grade.lift == Wheel()
    assert window.statusBar().currentMessage() == i18n.translate("status.color.reset_keys", count=4)
    assert not _cell(window, "lift").keys.diamond.animated
    window.undo_last()
    assert len(_clip(window).animation) == 4 and _clip(window).color_grade.lift.y == 0.2
    assert _cell(window, "lift").keys.diamond.animated


def test_a_node_graph_says_why_nothing_animates_and_offers_to_remove_the_ignored_keys(window):
    _track, clip = _prepared(window)
    clip.animation = [_kf("color.lift.r", 1.0, 0.3), _kf("color.hue", 2.0, 5.0)]
    clip.color_grade = _nodes()
    window._reload_timeline_preserving_selection(clip.id)
    window.on_clip_selected(clip.id)
    panel = window.color_panel
    assert panel.animation_notice.isVisibleTo(panel) and i18n.translate("color.animation.nodes") in panel.animation_notice_label.text()
    assert "2" in panel.animation_notice_label.text() and panel.animation_clear_button.isVisibleTo(panel)
    assert all(not cell.keys.diamond.isEnabled() for cell in panel.wheels.cells.values())
    _cell(window, "lift").keys.diamond.click()
    window.on_color_wheel_keys_toggled("lift")                                          # le signal atteint tout de même la fenêtre
    assert len(_clip(window).animation) == 2
    _move(window, "gain", Wheel(y=0.4))                                                 # une roue d'un graphe : valeur du nœud
    window._finalize_color_history()
    assert _clip(window).color_grade.correctors[0].grade.gain == Wheel(y=0.4) and len(_clip(window).animation) == 2
    entries = len(window.history)
    panel.animation_clear_button.click()
    assert not _clip(window).animation and len(window.history) == entries + 1
    assert not panel.animation_notice_label.text().count("2") and not panel.animation_clear_button.isVisibleTo(panel)
    window.undo_last()
    assert len(_clip(window).animation) == 2 and window.color_panel.animation_clear_button.isVisibleTo(window.color_panel)


def test_a_single_node_has_no_notice(window):
    _prepared(window)
    assert not window.color_panel.animation_notice.isVisibleTo(window.color_panel)


def test_a_locked_track_disables_the_wheel_diamonds_and_refuses_every_signal(window):
    _track, clip = _prepared(window, grade=ColorGrade(), locked=True)
    clip.animation = [_kf("color.gain.y", 1.0, 0.5)]
    snapshot = list(clip.animation), clip.color_grade
    entries = len(window.history)
    assert not window.color_panel.wheels.isEnabled()
    assert all(not cell.keys.diamond.isEnabledTo(window.color_panel.wheels.window()) or not cell.isEnabled()
               for cell in window.color_panel.wheels.cells.values())
    window.on_color_wheel_keys_toggled("lift")
    window.on_color_wheel_changed("gain", Wheel(0.1, 0.0, -0.1, 0.3))                     # la roue animée d'une piste verrouillée
    window.on_color_wheel_changed("lift", Wheel(0.1, 0.0, -0.1, 0.3))
    window.on_color_animation_cleared(None)
    window._finalize_color_history()
    assert (list(clip.animation), clip.color_grade) == snapshot and len(window.history) == entries


def test_the_removal_from_the_wheel_menu_keeps_the_value_shown_at_the_playhead(window):
    _track, clip = _prepared(window)
    clip.animation = [_kf("color.lift.y", 0.0, 0.0), _kf("color.lift.y", 4.0, 0.8)]
    window._reload_timeline_preserving_selection(clip.id)
    window.on_clip_selected(clip.id)
    window.seek_to_position(2.0)
    entries = len(window.history)
    _cell(window, "lift").cleared.emit()
    assert not _clip(window).animation and len(window.history) == entries + 1
    assert _clip(window).color_grade.lift.y == pytest.approx(0.4)


def test_the_wheel_menu_opens_the_graph_editor_on_its_first_animated_channel(window):
    _track, clip = _prepared(window)
    clip.animation = [_kf("color.gamma.b", 0.0, 0.0), _kf("color.gamma.b", 4.0, 0.5)]
    window._reload_timeline_preserving_selection(clip.id)
    window.on_clip_selected(clip.id)
    _cell(window, "gamma").graph_requested.emit("gamma")
    assert window.graph_editor.isVisible() and window.active_animation_property == "color.gamma.b"
    _cell(window, "lift").graph_requested.emit("lift")
    assert window.active_animation_property == "color.lift.r"


def test_the_monitor_receives_the_curve_the_wheel_wrote(window):
    _track, clip = _prepared(window)
    window.seek_to_position(1.0)
    _cell(window, "gain").keys.diamond.click()
    window.seek_to_position(3.0)
    _move(window, "gain", Wheel(0.0, 0.0, 0.0, 0.6))
    window._finalize_color_history()
    assert effective_grade(_clip(window), 2.0).gain.y == pytest.approx(0.3)
    assert window._monitor_color_grade(_clip(window), 2.0) == effective_grade(_clip(window), 2.0)     # ce que reçoit le moniteur


def test_the_diamond_texts_exist_in_the_three_languages_and_follow_the_language(window):
    _prepared(window)
    for key in ("color.animation.nodes", "color.animation.clear", "color.animation.wheel_tip", "animation.menu.clear_grade",
                "history.color.keyframe", "status.color.reset_keys"):
        texts = {language: i18n._TRANSLATIONS[key][language] for language in ("fr", "en", "es")}
        assert all(texts.values()) and len(set(texts.values())) == 3, key
    assert i18n.translate("color.animation.wheel_tip", wheel="Gain") in _cell(window, "gain").keys.diamond.toolTip()


def test_opening_the_page_after_the_playhead_moved_shows_the_wheels_at_that_instant(window):
    """Le panneau fermé ne suit pas la tête de lecture : à l'ouverture il lit le modèle à l'instant courant."""
    _track, clip = _prepared(window)
    window.switch_page(PAGE_EDIT)
    clip.animation = [_kf(f"color.gain.{c}", t, v) for c, (a, b) in {"r": (0.0, 0.4), "g": (0.0, 0.0), "b": (0.0, -0.4), "y": (0.0, 0.2)}.items()
                      for t, v in ((0.0, a), (2.0, b))]
    window._reload_timeline_preserving_selection(clip.id)
    window.on_clip_selected(clip.id)
    window.seek_to_position(1.0)
    window.switch_page(PAGE_COLOR)
    cell = _cell(window, "gain")
    assert cell.wheel.wheel == Wheel(0.2, 0.0, -0.2, 0.1)
    assert cell.keys.diamond.animated and not cell.keys.diamond.isChecked() and cell.keys.next.isEnabled()
    window.seek_to_position(2.0)
    assert _cell(window, "gain").keys.diamond.isChecked() and _cell(window, "gain").wheel.wheel == Wheel(0.4, 0.0, -0.4, 0.2)
