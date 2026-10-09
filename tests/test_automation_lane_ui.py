"""Courbe de volume dans la timeline : la bande sous les clips d'une piste audio, ses gestes et le menu audio des pistes.

Les gestes passent par de vrais événements souris / clavier sur la bande, et vont jusqu'au modèle et à l'historique
par les handlers de ``ui/main_window_mixins/audio.py`` (autrefois définis mais branchés à aucun signal).
"""

from __future__ import annotations

import pytest
from main_window_harness import build_window, install_dialogs, track
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMenu

from core.audio_automation import AutomationPoint, TrackAutomation
from core.project_model import MediaAsset
from core.timeline_operations import add_clip_to_track
from ui import i18n
from ui.timeline_widgets.automation_lane import LANE_HEIGHT, AutomationLane


@pytest.fixture
def dialogs(monkeypatch):
    return install_dialogs(monkeypatch)


@pytest.fixture
def window(qtbot, monkeypatch, tmp_path, dialogs):
    window = build_window(qtbot, monkeypatch, tmp_path / "config")
    media = tmp_path / "musique.wav"
    media.write_bytes(b"\x00" * 64)
    window.project.media_assets.append(MediaAsset(
        id="music", path=str(media), name="musique", duration=12.0, width=0, height=0, fps=0.0,
        media_type="audio", has_audio=True))
    add_clip_to_track(window.project, "music", "A1", 0.0)
    window._reload_timeline_preserving_selection()
    window.history.reset(window.project)
    window.resize(1400, 900)
    window.show()
    return window


def _lane(window, track_id: str = "A1") -> AutomationLane:
    lane = window.timeline_panel.automation_lanes.get(track_id)
    assert lane is not None, "la bande de la piste doit être affichée"
    return lane


def _show(window, track_id: str = "A1") -> AutomationLane:
    window.timeline_panel.set_automation_visible(track_id, True)
    return _lane(window, track_id)


def _at(lane: AutomationLane, seconds: float, gain_db: float) -> QPoint:
    return QPoint(round(lane.x_of(seconds)), round(lane.y_of(gain_db)))


def _move(lane: AutomationLane, position: QPoint, modifiers=Qt.NoModifier) -> None:
    event = QMouseEvent(QEvent.MouseMove, QPointF(position), QPointF(lane.mapToGlobal(position)), Qt.NoButton,
                        Qt.LeftButton, modifiers)
    QApplication.sendEvent(lane, event)


def _drag(lane: AutomationLane, start: QPoint, end: QPoint, modifiers=Qt.NoModifier) -> None:
    QTest.mousePress(lane, Qt.LeftButton, Qt.NoModifier, start)
    middle = QPoint((start.x() + end.x()) // 2, (start.y() + end.y()) // 2)
    _move(lane, middle, modifiers)
    _move(lane, end, modifiers)
    QTest.mouseRelease(lane, Qt.LeftButton, Qt.NoModifier, end)


def _steps(window) -> int:
    return len(window.history._undo_stack)


def _points(window, track_id: str = "A1") -> list[tuple[float, float, float]]:
    return [(p.time_seconds, p.gain_db, p.fade_seconds) for p in track(window, track_id).automation.points]


def _with_points(window, *points: tuple[float, float]) -> AutomationLane:
    track(window, "A1").automation = [AutomationPoint(time, gain) for time, gain in points]
    window._reload_timeline_preserving_selection()
    window.history.reset(window.project)
    return _lane(window)


# --- affichage ------------------------------------------------------------------------------------------------------


def test_the_band_shows_under_the_clips_of_a_track_whose_curve_has_points(window):
    panel = window.timeline_panel
    a1 = track(window, "A1")
    assert "A1" not in panel.automation_lanes, "courbe vide : rien ne s'affiche tant qu'on ne le demande pas"
    clip_height = panel.clip_height_of(a1)

    _with_points(window, (1.0, -6.0))
    lane = _lane(window)
    index = window.project.tracks.index(a1)
    assert panel.row_height_of(a1) == clip_height + LANE_HEIGHT
    assert panel.clip_height_of(a1) == clip_height, "les clips gardent leur hauteur, la bande s'ajoute dessous"
    assert lane.geometry().top() == panel.row_top(index) + clip_height
    assert lane.geometry().left() == panel.left_margin
    clip_widget = next(widget for widget in panel.clip_widgets.values() if widget.view.track_id == "A1")
    assert clip_widget.geometry().bottom() < lane.geometry().top()
    assert panel.row_top(index + 1) >= lane.geometry().bottom(), "la piste suivante descend d'autant"


def test_the_track_menu_shows_and_hides_the_curve_and_a_folded_track_hides_it(window):
    panel = window.timeline_panel
    header = panel.track_header_widgets["A1"]
    menu = QMenu()
    header._extend_menu(menu)
    toggle = next(action for action in menu.actions() if action.text() == i18n.translate("timeline.automation.show"))
    assert toggle.isCheckable() and not toggle.isChecked()
    toggle.trigger()
    assert "A1" in panel.automation_lanes

    track(window, "A1").collapsed = True
    window._reload_timeline_preserving_selection()
    assert "A1" not in panel.automation_lanes, "une piste repliée n'a pas de bande"
    track(window, "A1").collapsed = False
    window._reload_timeline_preserving_selection()

    menu = QMenu()
    header = panel.track_header_widgets["A1"]
    header._extend_menu(menu)
    toggle = next(action for action in menu.actions() if action.text() == i18n.translate("timeline.automation.show"))
    assert toggle.isChecked()
    toggle.trigger()
    assert "A1" not in panel.automation_lanes
    video_menu = QMenu()
    panel.track_header_widgets["V1"]._extend_menu(video_menu)
    assert not video_menu.actions(), "le menu audio n'existe que sur les pistes audio"


def test_the_drawn_curve_is_the_curve_the_export_applies(window):
    lane = _with_points(window, (1.0, 0.0), (2.0, -12.0), (4.0, -12.0), (5.0, 6.0))
    curve = TrackAutomation(points=list(track(window, "A1").automation.points))
    path = lane._curve_path(lane.points, 0.0, 7.0)
    assert path.elementCount() >= 5
    for index in range(path.elementCount()):
        element = path.elementAt(index)
        assert element.y == pytest.approx(lane.y_of(curve.gain_at(lane.time_of(element.x))), abs=0.05)


# --- gestes ---------------------------------------------------------------------------------------------------------


def test_a_double_click_puts_a_point_where_the_pointer_is_in_one_undo_step(window):
    lane = _show(window)
    QTest.mouseDClick(lane, Qt.LeftButton, Qt.NoModifier, _at(lane, 2.0, -6.0))

    [(time, gain, hold)] = _points(window)
    assert time == pytest.approx(2.0, abs=1.0 / window.project.fps + 1e-9)
    assert round(time * window.project.fps) == pytest.approx(time * window.project.fps), "calé sur une image"
    assert gain == pytest.approx(-6.0, abs=0.6) and hold == 0.0
    assert window.history.undo_label == i18n.translate("history.audio.automation_add")
    assert _steps(window) == 2
    assert _lane(window).points == track(window, "A1").automation.points, "la bande montre le nouveau point"
    window.undo_last()
    assert _points(window) == []
    assert "A1" in window.timeline_panel.automation_lanes, "la bande ouverte reste ouverte"


def test_dragging_a_point_moves_it_in_time_and_gain_in_one_undo_step(window):
    lane = _with_points(window, (1.0, 0.0), (3.0, -6.0), (5.0, 0.0))
    _drag(lane, _at(lane, 3.0, -6.0), _at(lane, 3.5, -12.0))

    assert [round(t, 2) for t, _g, _h in _points(window)] == [1.0, 3.5, 5.0]
    assert _points(window)[1][1] == pytest.approx(-12.0, abs=0.6)
    assert window.history.undo_label == i18n.translate("history.audio.automation_edit")
    assert _steps(window) == 2, "un glisser, une entrée"
    window.undo_last()
    assert _points(window) == [(1.0, 0.0, 0.0), (3.0, -6.0, 0.0), (5.0, 0.0, 0.0)]


def test_shift_drag_changes_the_gain_only_and_a_point_never_passes_its_neighbours(window):
    lane = _with_points(window, (1.0, 0.0), (3.0, -6.0), (5.0, 0.0))
    _drag(lane, _at(lane, 3.0, -6.0), _at(lane, 4.0, -3.0), Qt.ShiftModifier)
    assert _points(window)[1][0] == 3.0 and _points(window)[1][1] == pytest.approx(-3.0, abs=0.6)

    lane = _lane(window)
    _drag(lane, _at(lane, 3.0, _points(window)[1][1]), _at(lane, 8.0, -3.0))
    times = [t for t, _g, _h in _points(window)]
    assert times == sorted(times) and len(set(times)) == 3
    assert times[1] == pytest.approx(5.0 - 1.0 / window.project.fps), "arrêté une image avant son voisin"


def test_delete_removes_the_selected_point_and_the_menu_resets_or_clears(window, qtbot):
    lane = _with_points(window, (1.0, -3.0), (3.0, -9.0))
    clip_id = track(window, "A1").clips[0].id
    window.timeline_panel.select_clip(clip_id)
    QTest.mouseClick(lane, Qt.LeftButton, Qt.NoModifier, _at(lane, 3.0, -9.0))
    QTest.keyClick(lane, Qt.Key_Delete)
    assert _points(window) == [(1.0, -3.0, 0.0)]
    assert [clip.id for clip in track(window, "A1").clips] == [clip_id], "Suppr retire le point, pas le clip"
    assert window.history.undo_label == i18n.translate("history.audio.automation_remove")

    lane = _lane(window)
    menu = lane.build_menu(QPointF(_at(lane, 1.0, -3.0)))
    reset = next(a for a in menu.actions() if a.text() == i18n.translate("timeline.automation.reset_point"))
    reset.trigger()
    assert _points(window) == [(1.0, 0.0, 0.0)]

    lane = _lane(window)
    menu = lane.build_menu(QPointF(lane.x_of(4.0), lane.y_of(0.0)))
    next(a for a in menu.actions() if a.text() == i18n.translate("timeline.automation.clear")).trigger()
    assert _points(window) == []
    assert window.history.undo_label == i18n.translate("history.audio.automation_clear")
    assert "A1" in window.timeline_panel.automation_lanes, "une courbe effacée depuis sa bande ne la fait pas disparaître"


def test_a_locked_track_shows_its_curve_but_refuses_every_gesture(window):
    lane = _with_points(window, (1.0, -3.0))
    track(window, "A1").locked = True
    window._reload_timeline_preserving_selection()
    lane = _lane(window)
    assert lane.locked
    QTest.mouseDClick(lane, Qt.LeftButton, Qt.NoModifier, _at(lane, 3.0, -6.0))
    _drag(lane, _at(lane, 1.0, -3.0), _at(lane, 2.0, -9.0))
    QTest.keyClick(lane, Qt.Key_Delete)
    window.on_track_automation_point_added("A1", 4.0, -1.0, 0.0)
    assert _points(window) == [(1.0, -3.0, 0.0)]
    assert _steps(window) == 1


def test_a_refused_move_warns_and_keeps_the_curve(window, dialogs):
    _with_points(window, (1.0, -3.0), (2.0, -6.0))
    window.on_track_automation_point_moved("A1", 2.0, 1.0, -6.0)
    assert _points(window) == [(1.0, -3.0, 0.0), (2.0, -6.0, 0.0)]
    assert [title for title, _text in dialogs.of_kind("warning")] == [i18n.translate("dialog.title.mix")]
    assert _steps(window) == 1


# --- menu audio de la piste : rôle et ducking -----------------------------------------------------------------------


def _track_menu(window, track_id: str) -> QMenu:
    """Menu ⋯ tel qu'ouvert (à garder dans une variable : ses sous-menus vivent avec lui)."""
    menu = QMenu()
    window.timeline_panel.track_header_widgets[track_id]._extend_menu(menu)
    return menu


def _submenu(menu: QMenu, key: str) -> QMenu:
    return next(a.menu() for a in menu.actions() if a.menu() is not None and a.text() == i18n.translate(key))


def test_the_track_menu_sets_the_role_of_an_audio_track(window):
    menu = _track_menu(window, "A1")
    roles = _submenu(menu, "timeline.track.role")
    checked = [a.text() for a in roles.actions() if a.isChecked()]
    assert checked == [i18n.translate(f"timeline.track.role.{track(window, 'A1').audio_role}")]
    next(a for a in roles.actions() if a.text() == i18n.translate("timeline.track.role.music")).trigger()
    assert track(window, "A1").audio_role == "music" and type(track(window, "A1").audio_role) is str
    assert window.history.undo_label == i18n.translate("history.audio.track_role")
    menu = _track_menu(window, "A1")
    roles = _submenu(menu, "timeline.track.role")
    assert [a.text() for a in roles.actions() if a.isChecked()] == [i18n.translate("timeline.track.role.music")]


def test_duck_under_adds_then_removes_the_pair_with_one_undo_step_each(window):
    window.timeline_panel.add_track_requested.emit("audio")
    voice = next(t for t in window.project.tracks if t.type == "audio" and t.id != "A1")
    window.history.reset(window.project)

    menu = _track_menu(window, "A1")
    ducking = _submenu(menu, "timeline.track.duck_under")
    entry = next(a for a in ducking.actions() if a.text() == (voice.name or voice.id))
    assert not entry.isChecked()
    entry.trigger()
    assert [(s.music_track_id, s.voice_track_id) for s in window.project.ducking_sidechains] == [("A1", voice.id)]
    assert window.history.undo_label == i18n.translate("history.audio.ducking_add")

    menu = _track_menu(window, "A1")
    ducking = _submenu(menu, "timeline.track.duck_under")
    entry = next(a for a in ducking.actions() if a.text() == (voice.name or voice.id))
    assert entry.isChecked()
    entry.trigger()
    assert window.project.ducking_sidechains == []
    assert window.history.undo_label == i18n.translate("history.audio.ducking_remove")
    assert _steps(window) == 3
