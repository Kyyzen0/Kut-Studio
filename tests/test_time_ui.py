"""Le temps d'un clip dans l'interface : menu « Vitesse », section « Temps » de l'inspecteur, historique, raccourcis, avis du moniteur.

Fenêtre réelle (offscreen) : on déclenche les commandes comme le ferait l'utilisateur, puis on lit le projet, l'historique et les
widgets. Les opérations elles-mêmes sont testées sans Qt (``test_time_commands`` / ``test_time_presets``).
"""

from __future__ import annotations

import pytest
from multicam_stubs import keep_preview_player_off_the_disk
from PySide6.QtWidgets import QMenu

from core.animation import InterpolationType
from core.project_model import Clip, MediaAsset, Project, Sequence, Track
from core.time_ops import add_speed_point, speed_points
from core.time_remapping import FlowQuality, TimeInterpolation, TimeRemapping
from core.timeline_operations import set_clip_freeze_frame


@pytest.fixture
def window(qtbot, monkeypatch):
    from ui import i18n
    from ui.main_window import MainWindow

    i18n.set_language("fr")
    monkeypatch.setattr("ui.main_window.QMessageBox.information", lambda *_, **__: None)
    monkeypatch.setattr("ui.main_window.QMessageBox.critical", lambda *_, **__: None)
    main = MainWindow()
    qtbot.addWidget(main)
    main.timeline_timer.stop()
    keep_preview_player_off_the_disk(main, monkeypatch)
    return main


def _asset(asset_id="a", duration=60.0, fps=30.0):
    return MediaAsset(asset_id, f"/media/{asset_id}.mp4", asset_id, duration, 1920, 1080, fps, "video", True)


def _load(window, project):
    window.project = project
    window.history.reset(project)
    window.timeline_panel.set_project(project)
    window._update_timeline_duration()


def _two_clips(window):
    first = Clip("c1", "a", "V1", 0.0, 0.0, 10.0)
    second = Clip("c2", "a", "V1", 12.0, 20.0, 30.0)
    project = Project("p", media_assets=[_asset()], tracks=[Track("V1", "V1", "video", clips=[first, second]),
                                                            Track("A1", "A1", "audio")])
    _load(window, project)
    return first, second


def _entries(window):
    return len(window.history)


# ---------------------------------------------------------------------------
# Commandes et historique
# ---------------------------------------------------------------------------


def test_a_speed_command_changes_the_clip_and_is_one_undoable_entry(window):
    first, _second = _two_clips(window)
    before = _entries(window)
    window.on_time_command("c1", "speed", 0.25)
    assert first.time_remapping.speed == 0.25 and _entries(window) == before + 1
    assert window.history.undo_label == "Modifier la vitesse"
    window.undo_last()
    assert window.project.tracks[0].clips[0].time_remapping.speed == 1.0
    window.redo_last()
    assert window.project.tracks[0].clips[0].time_remapping.speed == 0.25


def test_a_command_applies_to_the_whole_selection_in_one_history_entry(window):
    first, second = _two_clips(window)
    window.timeline_panel._set_selection(["c1", "c2"], "c1", announce=False)
    before = _entries(window)
    window.on_time_command("c1", "interpolation", "blending")
    assert first.time_remapping.interpolation is TimeInterpolation.BLENDING
    assert second.time_remapping.interpolation is TimeInterpolation.BLENDING
    assert _entries(window) == before + 1
    window.undo_last()
    clips = window.project.tracks[0].clips
    assert all(c.time_remapping.interpolation is TimeInterpolation.SAMPLING for c in clips)


def test_a_refused_command_reports_to_the_status_bar_and_records_nothing(window):
    first, _second = _two_clips(window)
    set_clip_freeze_frame(window.project, "c1", 5.0, 2.0)
    window.history.reset(window.project)
    before = _entries(window)
    window.on_time_command("c1", "interpolation", "optical_flow")
    assert _entries(window) == before
    assert first.time_remapping.interpolation is TimeInterpolation.SAMPLING
    assert window.statusBar().currentMessage()                                           # l'utilisateur est prévenu


def test_adding_a_speed_point_needs_the_playhead_on_the_clip(window):
    first, _second = _two_clips(window)
    window.playhead_seconds = 4.0
    window.on_time_command("c1", "add_point")
    assert [round(k.time_seconds, 2) for k in speed_points(first)] == [4.0]
    window.playhead_seconds = 11.0                                                       # entre les deux clips
    before = _entries(window)
    window.on_time_command("c1", "add_point")
    assert _entries(window) == before and "tête de lecture" in window.statusBar().currentMessage()


def test_the_hold_command_lengthens_the_clip_by_one_second_at_the_playhead(window):
    first, _second = _two_clips(window)
    window.playhead_seconds = 3.0
    duration = first.duration
    window.on_time_command("c1", "hold")
    assert first.duration == pytest.approx(duration + 1.0)
    assert first.time_map.source_time(3.5) == pytest.approx(first.time_map.source_time(3.0))
    assert window.history.undo_label == "Arrêt sur image dans la courbe"


def test_a_preset_is_named_in_the_history(window):
    first, _second = _two_clips(window)
    window.on_time_command("c1", "preset", "ramp_in")
    assert first.has_speed_curve and window.history.undo_label == "Rampe d'entrée"


def test_copy_then_paste_carries_the_time_to_another_clip(window):
    first, second = _two_clips(window)
    window.on_time_command("c1", "speed", 0.5)
    window.on_time_command("c1", "interpolation", "optical_flow")
    window.on_time_command("c1", "copy")
    window.on_time_command("c2", "paste")
    assert second.time_remapping.speed == 0.5 and second.time_remapping.interpolation is TimeInterpolation.OPTICAL_FLOW
    assert (second.source_in, second.source_out) == (20.0, 30.0)
    assert window.history.undo_label == "Coller le temps"


def test_pasting_with_nothing_copied_says_so(window):
    _two_clips(window)
    before = _entries(window)
    window.on_time_command("c1", "paste")
    assert _entries(window) == before and "copiez" in window.statusBar().currentMessage()


def test_the_keyboard_commands_act_on_the_selected_clip_or_the_clip_under_the_playhead(window):
    first, second = _two_clips(window)
    window.playhead_seconds = 2.0
    window._time_shortcut_handlers()["time_add_speed_point"]()                           # rien de sélectionné : le clip sous la tête
    assert len(speed_points(first)) == 1
    window.playhead_seconds = 14.0
    window.timeline_panel._set_selection(["c2"], "c2", announce=False)
    window._time_shortcut_handlers()["time_freeze_frame"]()
    assert second.has_speed_curve and second.duration > 10.0


def test_the_time_shortcuts_are_registered_configurable_commands(window):
    from core.shortcuts import COMMANDS_BY_ID, Category

    assert COMMANDS_BY_ID["time_add_speed_point"].category is Category.TIME
    assert COMMANDS_BY_ID["time_freeze_frame"].category is Category.TIME
    assert {"time_add_speed_point", "time_freeze_frame"} <= set(window._shortcut_handlers())


# ---------------------------------------------------------------------------
# Menu « Vitesse »
# ---------------------------------------------------------------------------


def _menu_actions(window, clip_id):
    menu = QMenu()
    actions = window.timeline_panel._add_time_menu(menu, clip_id)
    return menu, actions


def _commands(actions):
    return {(command, argument) for command, argument in actions.values()}


def test_the_speed_menu_offers_the_constant_speeds_the_interpolation_and_the_curve_tools(window):
    _two_clips(window)
    _menu, actions = _menu_actions(window, "c1")
    commands = _commands(actions)
    for speed in (0.25, 0.5, 1.0, 2.0, 4.0):
        assert ("speed", speed) in commands
    for mode in ("sampling", "blending", "optical_flow"):
        assert ("interpolation", mode) in commands
    for command in ("add_point", "hold", "clear_curve", "preserve_pitch", "remap_audio", "copy", "paste", "reset", "analyze"):
        assert any(item[0] == command for item in commands), command
    assert ("preset", "slow_25") in commands and ("preset", "ramp_in") in commands


def test_the_menu_shows_the_current_state(window):
    first, _second = _two_clips(window)
    first.time_remapping = TimeRemapping(speed=0.5, reverse=True, interpolation=TimeInterpolation.BLENDING)
    _menu, actions = _menu_actions(window, "c1")
    checked = {(command, argument) for action, (command, argument) in actions.items() if action.isChecked()}
    assert ("speed", 0.5) in checked and ("reverse", False) in checked or ("reverse", True) not in checked
    assert ("interpolation", "blending") in checked and ("interpolation", "sampling") not in checked


def test_the_menu_greys_what_cannot_apply(window):
    first, _second = _two_clips(window)
    _menu, actions = _menu_actions(window, "c1")
    by_command = {(command, argument): action for action, (command, argument) in actions.items()}
    assert not by_command[("analyze", None)].isEnabled()                                  # pas en flux optique
    assert not by_command[("clear_curve", None)].isEnabled()                              # pas de courbe
    first.time_remapping = TimeRemapping(interpolation=TimeInterpolation.OPTICAL_FLOW)
    add_speed_point(window.project, "c1", 1.0, 0.5)
    _menu, actions = _menu_actions(window, "c1")
    by_command = {(command, argument): action for action, (command, argument) in actions.items()}
    assert by_command[("analyze", None)].isEnabled() and by_command[("clear_curve", None)].isEnabled()


def test_no_speed_menu_for_a_frozen_clip_and_no_interpolation_for_audio(window):
    first, _second = _two_clips(window)
    audio = Clip("a1", "a", "A1", 0.0, 0.0, 5.0)
    window.project.tracks[1].clips.append(audio)
    set_clip_freeze_frame(window.project, "c2", 25.0, 2.0)
    _load(window, window.project)
    assert _menu_actions(window, "c2")[1] == {}
    commands = _commands(_menu_actions(window, "a1")[1])
    assert ("speed", 2.0) in commands and not any(command == "interpolation" for command, _argument in commands)


def test_a_nested_clip_only_accepts_sampling_in_the_menu(window):
    from core.sequences import insert_sequence_clip

    inner = Sequence(id="inner", name="Inner", width=1920, height=1080, fps=30.0, tracks=[
        Track(id="V1", name="V1", type="video", clips=[Clip(id="i1", asset_id="a", track_id="V1", timeline_start=0.0, source_in=0.0,
                                                           source_out=5.0)])])
    main = Sequence(id="main", name="Main", width=1920, height=1080, fps=30.0, tracks=[Track(id="V1", name="V1", type="video")])
    project = Project("p", media_assets=[_asset()], sequences=[main, inner], active_sequence_id="main")
    nested = insert_sequence_clip(project, "inner", "V1", 0.0)
    _load(window, project)
    _menu, actions = _menu_actions(window, nested.id)
    by_command = {(command, argument): action for action, (command, argument) in actions.items()}
    assert by_command[("interpolation", "sampling")].isEnabled()
    assert not by_command[("interpolation", "blending")].isEnabled() and not by_command[("interpolation", "optical_flow")].isEnabled()


def test_the_timeline_signal_reaches_the_window_command(window):
    first, _second = _two_clips(window)
    window.timeline_panel.time_command_requested.emit("c1", "speed", 2.0)
    assert first.time_remapping.speed == 2.0


# ---------------------------------------------------------------------------
# Inspecteur : section « Temps »
# ---------------------------------------------------------------------------


def _select(window, clip_id):
    window.timeline_panel.select_clip(clip_id)
    window.on_clip_selected(clip_id)


def test_the_inspector_section_reflects_the_clip_and_hides_what_does_not_apply(window):
    first, _second = _two_clips(window)
    first.time_remapping = TimeRemapping(interpolation=TimeInterpolation.OPTICAL_FLOW, flow_quality=FlowQuality.BEST,
                                         preserve_pitch=False)
    _load(window, window.project)
    _select(window, "c1")
    section = window.properties_panel.time_section
    assert section.interpolation_combo.currentData() == "optical_flow" and section.quality_combo.currentData() == "best"
    assert not section.pitch_check.isChecked() and section.audio_check.isChecked()
    assert not section.quality_combo.isHidden() and not section.analyze_button.isHidden()
    first.time_remapping = TimeRemapping()
    _load(window, window.project)
    _select(window, "c1")
    assert section.quality_combo.isHidden() and section.analyze_button.isHidden()         # seulement avec le flux optique
    assert section.curve_label.text() == "Vitesse constante"


def test_changing_a_control_of_the_section_applies_the_command(window):
    first, _second = _two_clips(window)
    _select(window, "c1")
    section = window.properties_panel.time_section
    section.interpolation_combo.setCurrentIndex(section.interpolation_combo.findData("blending"))
    assert first.time_remapping.interpolation is TimeInterpolation.BLENDING
    section.pitch_check.setChecked(False)
    assert not window.project.tracks[0].clips[0].time_remapping.preserve_pitch
    assert window.history.undo_label == "Changer la hauteur du son"


def test_the_curve_buttons_add_a_point_and_delete_the_curve(window):
    first, _second = _two_clips(window)
    window.playhead_seconds = 3.0
    _select(window, "c1")
    window.playhead_seconds = 3.0
    section = window.properties_panel.time_section
    section.add_point_button.click()
    assert len(speed_points(first)) == 1
    assert section.curve_label.text() == "1 points de vitesse"
    section.clear_curve_button.click()
    assert not first.has_speed_curve and section.curve_label.text() == "Vitesse constante"


def test_the_section_follows_the_language(window):
    from ui import i18n

    _two_clips(window)
    _select(window, "c1")
    section = window.properties_panel.time_section
    i18n.set_language("en")
    try:
        assert section.interpolation_combo.itemText(2) == "Optical flow" and section.add_point_button.text() == "Speed point"
    finally:
        i18n.set_language("fr")
    assert section.interpolation_combo.itemText(2) == "Flux optique"


def test_a_nested_clip_cannot_pick_interpolation_in_the_section(window):
    from core.sequences import insert_sequence_clip

    inner = Sequence(id="inner", name="Inner", width=1920, height=1080, fps=30.0, tracks=[
        Track(id="V1", name="V1", type="video", clips=[Clip(id="i1", asset_id="a", track_id="V1", timeline_start=0.0, source_in=0.0,
                                                           source_out=5.0)])])
    main = Sequence(id="main", name="Main", width=1920, height=1080, fps=30.0, tracks=[Track(id="V1", name="V1", type="video")])
    project = Project("p", media_assets=[_asset()], sequences=[main, inner], active_sequence_id="main")
    nested = insert_sequence_clip(project, "inner", "V1", 0.0)
    _load(window, project)
    _select(window, nested.id)
    section = window.properties_panel.time_section
    model = section.interpolation_combo.model()
    assert model.item(0).isEnabled() and not model.item(1).isEnabled() and not model.item(2).isEnabled()


# ---------------------------------------------------------------------------
# Moniteur : avis « aperçu simplifié »
# ---------------------------------------------------------------------------


def test_the_monitor_says_when_it_shows_a_simplified_preview(window):
    first, _second = _two_clips(window)
    window.playhead_seconds = 2.0
    window._update_preview_notice()
    assert window.preview_panel.preview_quality_notice.isHidden()
    window.on_time_command("c1", "speed", 0.5)
    window.on_time_command("c1", "interpolation", "optical_flow")
    window.playhead_seconds = 2.0
    window._update_preview_notice()
    notice = window.preview_panel.preview_quality_notice
    assert not notice.isHidden() and "Flux optique" in notice.text() and "simplifié" in notice.text()
    window.playhead_seconds = 21.0                                                        # c1 (20 s à 50 %) est fini : c2, normal
    window._update_preview_notice()
    assert notice.isHidden()


def test_no_notice_when_the_speed_makes_every_frame_exact(window):
    _two_clips(window)
    window.on_time_command("c1", "speed", 2.0)
    window.on_time_command("c1", "interpolation", "optical_flow")
    window.playhead_seconds = 2.0
    window._update_preview_notice()
    assert window.preview_panel.preview_quality_notice.isHidden()                         # 200 % : l'échantillonnage est exact


def test_the_notice_merges_with_the_adaptive_quality_message(window, monkeypatch):
    from ui.i18n import translate

    _two_clips(window)
    window.on_time_command("c1", "speed", 0.5)
    window.on_time_command("c1", "interpolation", "blending")
    window.playhead_seconds = 2.0
    monkeypatch.setattr(type(window.runtime.preview), "degraded", property(lambda self: True))
    window._update_preview_notice()
    text = window.preview_panel.preview_quality_notice.text()
    assert translate("preview.quality_reduced", quality=window.runtime.preview_label()) in text and "Mélange d'images" in text


# ---------------------------------------------------------------------------
# Analyse du flux optique depuis la fenêtre (vrai FFmpeg)
# ---------------------------------------------------------------------------


@pytest.fixture
def flow_window(window, tmp_path, monkeypatch):
    import shutil

    if shutil.which("ffmpeg") is None:
        pytest.skip("FFmpeg absent")
    from test_retime_prepare_real import SCENE_FRAMES, SH, SW, _encode_scene

    from core.flow_cache import FlowCache

    monkeypatch.setattr("ui.main_window_mixins.time_editing.TimeEditingMixin._flow_cache_for_analysis",
                        lambda self: FlowCache(tmp_path / "flow"))
    media = _encode_scene(tmp_path / "scene.mp4", 1.0, SCENE_FRAMES)
    asset = MediaAsset("s", media, "scene", SCENE_FRAMES / 30.0, SW, SH, 30.0, "video", False)
    clip = Clip("c1", "s", "V1", 0.0, 0.0, SCENE_FRAMES / 30.0,
                time_remapping=TimeRemapping(speed=0.5, interpolation=TimeInterpolation.OPTICAL_FLOW,
                                             flow_quality=FlowQuality.BALANCED))
    project = Project("p", width=SW, height=SH, fps=30.0, media_assets=[asset], tracks=[Track("V1", "V1", "video", clips=[clip])])
    _load(window, project)
    return window


def test_the_analysis_from_the_menu_reports_progress_then_the_result(flow_window, qtbot):
    flow_window.on_time_command("c1", "analyze")
    assert flow_window._flow_job is not None
    qtbot.waitUntil(lambda: flow_window._flow_job is None, timeout=60000)
    message = flow_window.statusBar().currentMessage()
    assert "Flux optique analysé" in message and "paires" in message
    assert flow_window.properties_panel.time_section.analyze_button.text() == "Analyser le flux"


def test_the_inspector_button_becomes_cancel_while_running_and_stops_the_analysis(flow_window, qtbot):
    flow_window.on_time_command("c1", "preset", "slow_25")
    flow_window.on_time_command("c1", "quality", "best")
    _select(flow_window, "c1")
    section = flow_window.properties_panel.time_section
    flow_window.on_time_command("c1", "analyze")
    assert section.analyze_button.text() == "Annuler l'analyse"
    section.analyze_button.click()                                                         # « Annuler l'analyse »
    qtbot.waitUntil(lambda: flow_window._flow_job is None, timeout=60000)
    assert section.analyze_button.text() == "Analyser le flux"
    assert flow_window.statusBar().currentMessage() in {"Analyse du flux optique annulée.", flow_window.statusBar().currentMessage()}


def test_a_second_analysis_is_refused_while_one_runs_and_closing_stops_it(flow_window, qtbot):
    flow_window.on_time_command("c1", "preset", "slow_25")
    flow_window.on_time_command("c1", "quality", "best")
    flow_window.on_time_command("c1", "analyze")
    flow_window.on_time_command("c1", "analyze")
    assert "déjà en cours" in flow_window.statusBar().currentMessage()
    flow_window._cancel_flow_analysis()                                                    # la fermeture de l'application
    assert flow_window._flow_job is None and flow_window.properties_panel.time_section.analyze_button.text() == "Analyser le flux"


def test_nothing_to_analyze_without_the_optical_flow_mode_or_when_every_frame_is_exact(flow_window):
    flow_window.on_time_command("c1", "interpolation", "blending")
    flow_window.on_time_command("c1", "analyze")
    assert "Flux optique" in flow_window.statusBar().currentMessage() and flow_window._flow_job is None
    flow_window.on_time_command("c1", "interpolation", "optical_flow")
    flow_window.on_time_command("c1", "speed", 2.0)
    flow_window.on_time_command("c1", "analyze")
    assert "Rien à analyser" in flow_window.statusBar().currentMessage() and flow_window._flow_job is None


# ---------------------------------------------------------------------------
# Graph Editor : la vitesse en pourcentage
# ---------------------------------------------------------------------------


def test_the_graph_editor_shows_the_speed_in_percent_and_edits_it_back_in_factor(window, qtbot):
    first, _second = _two_clips(window)
    add_speed_point(window.project, "c1", 0.0, 1.0)
    point = add_speed_point(window.project, "c1", 4.0, 0.5, interpolation=InterpolationType.LINEAR)
    window._reload_timeline_preserving_selection("c1")
    editor = window.open_graph_editor("time.speed")
    qtbot.waitExposed(editor)
    assert editor.property_id == "time.speed" and editor.value_spin.suffix() == " %"
    editor.select_ids({point.id})
    assert editor.value_spin.value() == pytest.approx(50.0)                              # 0,5 stocké, « 50 % » affiché
    editor.value_spin.setValue(200.0)
    editor.value_spin.editingFinished.emit()
    assert [k.value for k in speed_points(first)] == [1.0, 2.0]                          # stocké en facteur
    assert editor.value_spin.maximum() == pytest.approx(1000.0)                          # bornes ±10× = ±1000 %


def test_other_properties_keep_their_raw_values_in_the_graph_editor(window, qtbot):
    from core.keyframe_editing import add_keyframe

    _two_clips(window)
    add_keyframe(window.project, "c1", "opacity", 0.0, 0.25)
    add_keyframe(window.project, "c1", "opacity", 2.0, 1.0)
    window._reload_timeline_preserving_selection("c1")
    editor = window.open_graph_editor("opacity")
    qtbot.waitExposed(editor)
    editor.select_ids({editor.curve().keyframes[0].id})
    assert editor.value_spin.suffix() == "" and editor.value_spin.value() == pytest.approx(0.25)


def test_a_refused_paste_keeps_its_reason_instead_of_saying_pasted(window):
    first, second = _two_clips(window)
    window.on_time_command("c1", "speed", 0.5)
    window.on_time_command("c1", "copy")
    window.statusBar().clearMessage()
    window.project.tracks[0].locked = True
    before = _entries(window)
    window.on_time_command("c2", "paste")
    assert _entries(window) == before and window.statusBar().currentMessage()
    assert "collé" not in window.statusBar().currentMessage()                              # le refus n'est pas recouvert
    window.project.tracks[0].locked = False
    window.on_time_command("c2", "paste")
    assert window.project.tracks[0].clips[1].time_remapping.speed == 0.5
    assert window.statusBar().currentMessage() == "Temps collé."


def _speed_editor(window, qtbot, *, points=((0.0, 1.0), (4.0, 1.0))):
    """Graph Editor ouvert sur la vitesse de ``c1`` ; retourne ``(éditeur, points)``."""
    created = [add_speed_point(window.project, "c1", moment, value, interpolation=InterpolationType.LINEAR) for moment, value in points]
    window.history.reset(window.project)
    window._reload_timeline_preserving_selection("c1")
    editor = window.open_graph_editor("time.speed")
    qtbot.waitExposed(editor)
    return editor, created


def test_a_speed_value_that_would_leave_a_degenerate_clip_is_refused_by_the_graph_editor(window, qtbot):
    asset = _asset(duration=60.0)
    clip = Clip("c1", "a", "V1", 0.0, 0.0, 0.1)                                            # 0,1 s de source
    window.project = Project("p", media_assets=[asset], tracks=[Track("V1", "V1", "video", clips=[clip]), Track("A1", "A1", "audio")])
    _load(window, window.project)
    editor, (point,) = _speed_editor(window, qtbot, points=((0.0, 1.0),))
    before, entries = clip.duration, _entries(window)
    editor.select_ids({point.id})
    editor.value_spin.setValue(1000.0)                                                       # 10× : 0,01 s, sous le plancher
    editor.value_spin.editingFinished.emit()
    live = window.project.tracks[0].clips[0]
    assert [k.value for k in speed_points(live)] == [1.0] and live.duration == pytest.approx(before)   # l'édition générique l'aurait laissé
    assert _entries(window) == entries and window.statusBar().currentMessage()               # refusé, dit, rien d'enregistré


def test_dragging_a_speed_point_back_and_forth_restores_every_keyframe_cut_on_the_way(window, qtbot):
    from PySide6.QtCore import QPointF

    from core.keyframe_editing import KeyframeRef, add_keyframe

    first, _second = _two_clips(window)
    add_keyframe(window.project, "c1", "opacity", 7.0, 0.2)
    add_keyframe(window.project, "c1", "opacity", 9.0, 0.9)
    editor, (_start, moving) = _speed_editor(window, qtbot)
    start = [(k.time_seconds, k.value) for k in window.project.tracks[0].clips[0].transform_keyframes]
    duration = window.project.tracks[0].clips[0].duration
    entries = _entries(window)
    window.set_keyframe_selection({KeyframeRef("c1", "time.speed", moving.id)})
    origin = {"anchor": moving, "anchor_time": moving.time_seconds, "values": {moving.id: moving.value}}
    press = editor.canvas.to_screen(moving.time_seconds, moving.value)
    editor.drag_keyframes(origin, press, editor.canvas.to_screen(moving.time_seconds, 9.0))   # bien plus vite : le clip raccourcit
    shortened = window.project.tracks[0].clips[0]
    assert shortened.duration < duration - 1.0
    editor.drag_keyframes(origin, press, QPointF(press))                                       # …retour à la valeur de départ, même geste
    restored = window.project.tracks[0].clips[0]
    assert restored.duration == pytest.approx(duration)
    assert [(k.time_seconds, k.value) for k in restored.transform_keyframes] == start          # aucun keyframe perdu
    editor.finish_gesture("Modifier")
    assert _entries(window) == entries + 1                                                     # un geste = une entrée d'historique


def test_every_other_graph_editor_edit_of_the_speed_is_a_recorded_time_operation(window, qtbot):
    first, _second = _two_clips(window)
    editor, (_start, point) = _speed_editor(window, qtbot)
    editor.select_ids({point.id})
    entries = _entries(window)
    editor.time_spin.setValue(6.0)                                                           # champ « temps » : le point passe de 4 s à 6 s
    editor.time_spin.editingFinished.emit()
    live = window.project.tracks[0].clips[0]
    assert [round(k.time_seconds, 2) for k in speed_points(live)] == [0.0, 6.0] and _entries(window) == entries + 1
    editor.add_keyframe_at(2.0)                                                              # double-clic : un point qui garde la vitesse
    assert [round(k.time_seconds, 2) for k in speed_points(window.project.tracks[0].clips[0])] == [0.0, 2.0, 6.0]
    editor.select_ids({point.id})
    for combo, data in ((editor.interpolation_combo, InterpolationType.BEZIER.value), (editor.tangent_combo, "broken")):
        index = combo.findData(data)
        combo.setCurrentIndex(index)
        combo.activated.emit(index)                                                          # le choix de l'utilisateur, pas un réglage de code
    kinds = {k.id: k for k in speed_points(window.project.tracks[0].clips[0])}[point.id]
    assert kinds.interpolation is InterpolationType.BEZIER and kinds.tangent_mode.value == "broken"
    assert _entries(window) == entries + 4                                                   # une entrée par réglage, jamais zéro
    window.undo_last()
    window.undo_last()
    window.undo_last()
    window.undo_last()
    assert [round(k.time_seconds, 2) for k in speed_points(window.project.tracks[0].clips[0])] == [0.0, 4.0]


def test_a_speed_edit_on_a_locked_track_is_refused_by_the_graph_editor_and_says_why(window, qtbot):
    first, _second = _two_clips(window)
    editor, (_start, point) = _speed_editor(window, qtbot)
    editor.select_ids({point.id})
    window.project.tracks[0].locked = True
    entries = _entries(window)
    editor.time_spin.setValue(6.0)
    editor.time_spin.editingFinished.emit()
    editor.add_keyframe_at(2.0)
    assert [round(k.time_seconds, 2) for k in speed_points(window.project.tracks[0].clips[0])] == [0.0, 4.0]
    assert _entries(window) == entries and "verrouill" in window.statusBar().currentMessage()


def test_the_graph_editor_follows_the_ripple_policy_of_the_user_for_speed(window, qtbot):
    first, _second = _two_clips(window)
    window.set_time_ripple_timeline(True)                                                    # « conserver la durée sur la timeline »
    editor, (_start, point) = _speed_editor(window, qtbot)
    duration = window.project.tracks[0].clips[0].duration
    editor.select_ids({point.id})
    editor.value_spin.setValue(50.0)                                                         # ralentir allongerait le clip (16 s)
    editor.value_spin.editingFinished.emit()
    assert window.project.tracks[0].clips[0].duration == pytest.approx(duration)               # la politique est respectée
    assert [k.value for k in speed_points(window.project.tracks[0].clips[0])] == [1.0, 0.5]


def test_the_graph_editor_paints_with_no_clip_and_no_property(window, qtbot):
    _two_clips(window)
    window.timeline_panel._set_selection([], None, announce=False)
    editor = window.open_graph_editor()
    qtbot.waitExposed(editor)
    assert editor.property_id is None and editor.display_scale() == 1.0
    assert not editor.canvas.grab().isNull()                                             # pas d'exception dans paintEvent


def test_the_preparation_report_of_an_export_is_shown_never_silent(window):
    from core.optical_flow import Fallback
    from core.retime_prepare import PrepareReport

    report = PrepareReport()
    for _ in range(40):
        report.add(Fallback.NONE, 0.9)
    window._on_preparation_reported(report)
    assert "40 images" in window.statusBar().currentMessage() and "confiance 90 %" in window.statusBar().currentMessage()
    for _ in range(3):
        report.add(Fallback.SCENE_CUT, None)
    window._on_preparation_reported(report)
    assert "3 image(s) remplacée(s)" in window.statusBar().currentMessage()                # l'écart est dit, avec son compte
    window.statusBar().clearMessage()
    window._on_preparation_reported(PrepareReport())                                       # rien de fabriqué : rien à dire
    assert window.statusBar().currentMessage() == ""


# ---------------------------------------------------------------------------
# Réglage « Calcul du flux optique »
# ---------------------------------------------------------------------------


def test_the_flow_backend_setting_is_persisted_tolerantly(tmp_path):
    import json
    from dataclasses import replace

    from core.user_settings import FILE_NAME, UserSettings, load_user_settings, save_user_settings

    assert UserSettings().flow_backend == "auto"
    path = save_user_settings(replace(UserSettings(), flow_backend="cpu"), tmp_path)
    assert path.name == FILE_NAME and json.loads(path.read_text(encoding="utf-8"))["flow_backend"] == "cpu"
    assert load_user_settings(tmp_path).flow_backend == "cpu"
    invalid = tmp_path / "invalid"
    invalid.mkdir()
    (invalid / FILE_NAME).write_text('{"flow_backend": "turbo"}', encoding="utf-8")
    assert load_user_settings(invalid).flow_backend == "auto"                            # une valeur inconnue : Auto
    older = tmp_path / "older"
    older.mkdir()
    (older / FILE_NAME).write_text('{"language": "en"}', encoding="utf-8")
    loaded = load_user_settings(older)
    assert loaded.language == "en" and loaded.flow_backend == "auto"                     # un fichier d'avant : Auto


def test_choosing_a_backend_reaches_every_engine_and_is_saved(window):
    from core.optical_flow import BackendPreference

    window.set_flow_backend("cpu")
    assert window._flow_backend_request == "cpu"
    assert window.export_engine.flow_preference is BackendPreference.CPU
    assert window.preview_engine.flow_preference is BackendPreference.CPU
    assert window._settings_snapshot().flow_backend == "cpu"
    window.set_flow_backend("n'importe quoi")
    assert window.export_engine.flow_preference is BackendPreference.AUTO                # une valeur inconnue redonne Auto


def test_the_gpu_choice_is_greyed_out_because_no_accelerator_exists_yet(window):
    options = {value: (label, available) for value, label, available in window.flow_backend_options()}
    assert options["auto"][1] and options["cpu"][1] and not options["gpu"][1]
    assert "indisponible" in options["gpu"][0]


def test_the_performance_page_offers_the_flow_backend_and_applies_the_choice(window, qtbot):
    from ui.performance_settings import PerformanceSettingsTab

    tab = PerformanceSettingsTab(window)
    qtbot.addWidget(tab)
    tab.refresh_encoding()
    combo = tab.flow_backend_combo
    assert not combo.isHidden() or not tab.isVisible()
    assert combo.count() == 3 and not combo.model().item(combo.findData("gpu")).isEnabled()
    combo.setCurrentIndex(combo.findData("cpu"))
    combo.activated.emit(combo.currentIndex())
    assert window._flow_backend_request == "cpu"
    assert combo.itemText(combo.findData("cpu")) == "Processeur"


def test_the_diagnostics_name_the_requested_and_the_used_backend(window):
    text = window.encoding_diagnostics_text()
    assert "Flux optique" in text and "numpy" in text and "auto" in text


# ---------------------------------------------------------------------------
# Ripple : portion de média (défaut) ou durée sur la timeline
# ---------------------------------------------------------------------------


def test_the_speed_edits_keep_the_source_range_by_default_and_the_timeline_duration_on_request(window):
    first, _second = _two_clips(window)
    duration = first.duration
    window.on_time_command("c1", "speed", 0.5)
    assert first.duration == pytest.approx(duration * 2) and first.source_out == 10.0           # portion de média gardée
    window.on_time_command("c1", "speed", 1.0)
    window.on_time_command("c1", "ripple", True)
    assert window._time_ripple_timeline and window.timeline_panel.time_ripple_timeline
    window.on_time_command("c1", "preset", "slow_25")                                           # constant : set_clip_speed ignore le ripple
    window.on_time_command("c1", "reset")
    window.playhead_seconds = 2.0
    window.on_time_command("c1", "hold")                                                        # un arrêt, durée gardée
    assert first.duration == pytest.approx(duration, abs=1e-3)


def test_the_ripple_choice_is_persisted_and_shown_in_the_menu(window):
    _two_clips(window)
    window.on_time_command("c1", "ripple", True)
    assert window._settings_snapshot().time_ripple_timeline is True
    _menu, actions = _menu_actions(window, "c1")
    by_command = {(command, argument): action for action, (command, argument) in actions.items()}
    toggle = next(action for (command, _argument), action in by_command.items() if command == "ripple")
    assert toggle.isChecked()
    assert by_command[("ripple", False)] is toggle                                               # un clic le désactive
    window.on_time_command("c1", "ripple", False)
    assert window._settings_snapshot().time_ripple_timeline is False


def test_the_ripple_setting_is_saved_and_read_back_with_a_safe_default(tmp_path):
    import json
    from dataclasses import replace

    from core.user_settings import FILE_NAME, UserSettings, load_user_settings, save_user_settings

    assert UserSettings().time_ripple_timeline is False
    path = save_user_settings(replace(UserSettings(), time_ripple_timeline=True), tmp_path)
    assert json.loads(path.read_text(encoding="utf-8"))["time_ripple_timeline"] is True
    assert load_user_settings(tmp_path).time_ripple_timeline is True
    garbled = tmp_path / "garbled"
    garbled.mkdir()
    (garbled / FILE_NAME).write_text('{"time_ripple_timeline": "peut-être"}', encoding="utf-8")
    assert load_user_settings(garbled).time_ripple_timeline is False                             # une valeur incohérente : le défaut


# ---------------------------------------------------------------------------
# Points de vitesse dans la timeline : losanges, glisser, supprimer
# ---------------------------------------------------------------------------


def _ramped_window(window):
    first, second = _two_clips(window)
    add_speed_point(window.project, "c1", 0.0, 1.0)
    add_speed_point(window.project, "c1", 3.0, 0.5)
    add_speed_point(window.project, "c1", 6.0, 2.0)
    window.history.reset(window.project)                                                 # l'état de départ du test : courbe posée
    window._reload_timeline_preserving_selection("c1")
    return first, second


def _speed_refs(clip):
    from core.keyframe_editing import KeyframeRef

    return [KeyframeRef(clip.id, "time.speed", point.id) for point in speed_points(clip)]


def test_the_speed_points_are_drawn_as_keyframe_diamonds_on_the_clip(window):
    first, _second = _ramped_window(window)
    widget = window.timeline_panel.clip_widgets["c1"]
    items = widget._keyframe_items()
    assert [frames[0].property_name for _rect, frames in items].count("time.speed") == 3
    assert window.timeline_panel.clip_widgets["c2"]._keyframe_items() == []              # un clip sans courbe reste net


def test_an_audio_clip_shows_its_speed_points_but_no_transform_diamonds(window):
    first, _second = _two_clips(window)
    audio = Clip("a1", "a", "A1", 0.0, 0.0, 6.0)
    window.project.tracks[1].clips.append(audio)
    add_speed_point(window.project, "a1", 0.0, 1.0)
    add_speed_point(window.project, "a1", 2.0, 0.5)
    _load(window, window.project)
    properties = {frames[0].property_name for _rect, frames in window.timeline_panel.clip_widgets["a1"]._keyframe_items()}
    assert properties == {"time.speed"}


def test_dragging_speed_points_moves_them_in_one_history_entry_and_changes_the_duration(window):
    first, _second = _ramped_window(window)
    refs = _speed_refs(first)
    duration, before = first.duration, _entries(window)
    window.on_keyframes_move_requested(refs[1:], 1.0)
    assert [round(k.time_seconds, 2) for k in speed_points(first)] == [0.0, 4.0, 7.0]
    assert first.duration != duration and _entries(window) == before + 1
    window.undo_last()
    assert [round(k.time_seconds, 2) for k in speed_points(window.project.tracks[0].clips[0])] == [0.0, 3.0, 6.0]


def test_a_refused_drag_reports_and_records_nothing(window):
    first, _second = _ramped_window(window)
    refs = _speed_refs(first)
    window.project.tracks[0].locked = True
    before = _entries(window)
    window.on_keyframes_move_requested(refs[1:], 1.0)
    assert _entries(window) == before and window.statusBar().currentMessage()
    assert [round(k.time_seconds, 2) for k in speed_points(first)] == [0.0, 3.0, 6.0]


def test_deleting_the_selected_speed_points_goes_through_the_time_transaction(window):
    first, _second = _ramped_window(window)
    window.set_keyframe_selection(set(_speed_refs(first)[1:]))
    before = _entries(window)
    assert window.remove_keyframes_command() == 2
    assert len(speed_points(first)) == 1 and _entries(window) == before + 1
    window.set_keyframe_selection(set(_speed_refs(first)))
    assert window.remove_keyframes_command() == 1
    assert not first.has_speed_curve and first.time_remapping.speed == 1.0               # la vitesse constante est restaurée


def test_a_mixed_drag_moves_transform_keyframes_and_speed_points_together(window):
    from core.keyframe_editing import KeyframeRef, add_keyframe

    first, _second = _ramped_window(window)
    opacity = add_keyframe(window.project, "c1", "opacity", 1.0, 0.5)
    refs = [*_speed_refs(first)[1:2], KeyframeRef("c1", "opacity", opacity.id)]
    window.on_keyframes_move_requested(refs, 0.5)
    assert round(speed_points(first)[1].time_seconds, 2) == 3.5
    assert [round(k.time_seconds, 2) for k in first.transform_keyframes if k.property_name == "opacity"] == [1.5]


def test_the_analysis_is_made_at_the_size_of_the_selected_export_so_the_export_recomputes_nothing(flow_window, qtbot, tmp_path, monkeypatch):
    from types import SimpleNamespace

    from core.export_engine import ExportFormat, ExportPreset
    from core.flow_cache import FlowCache
    from core.render_plan import build_render_plan
    from core.retime_layers import prepare_plan

    preset = ExportPreset("Petit", (160, 90), 8, "96k")                                     # autre taille que le projet (320 × 180)
    monkeypatch.setattr(flow_window.export_panel, "current_spec",
                        lambda: SimpleNamespace(export_parts=lambda: (ExportFormat.MP4_H264, preset, 30)))
    flow_window.on_time_command("c1", "analyze")
    qtbot.waitUntil(lambda: flow_window._flow_job is None, timeout=60000)
    assert "Flux optique analysé" in flow_window.statusBar().currentMessage()
    done = prepare_plan(build_render_plan(flow_window.project), 160, 90, 30, FlowCache(tmp_path / "flow"))
    assert done.report.pairs_computed == 0 and done.report.pairs_cached > 0                 # tout venait de l'analyse


def test_the_analysis_follows_the_backend_asked_for_in_the_settings(flow_window, qtbot, monkeypatch):
    seen = []
    import ui.main_window_mixins.time_editing as module

    class Recorder:
        def __init__(self, requests, cache):
            seen.extend(requests)

        def start(self): ...
        def snapshot(self):
            return SimpleNamespace(finished=True, total=0, done=0, state=None, report=None, message="")

    from types import SimpleNamespace

    monkeypatch.setattr(module, "FlowAnalysisJob", Recorder)
    flow_window.set_flow_backend("cpu")
    flow_window.on_time_command("c1", "analyze")
    flow_window._flow_timer.stop()
    assert seen and all(request.preference.value == "cpu" for request in seen)
