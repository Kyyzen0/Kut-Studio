"""Piste ou calque verrouillé : toute l'édition d'un clip (Mouvement, Avancé, images-clés, Vitesse, Effets, calque
graphique, moniteur, script) obéit à la même règle que le groupe Compositing (``core.compositing_guard``)."""

from __future__ import annotations

import copy
from dataclasses import replace

import pytest

from core.compositing_guard import (
    REFUSAL_LAYER_LOCKED,
    REFUSAL_NOT_VISUAL,
    REFUSAL_TRACK_LOCKED,
    clip_edit_refusal,
    compositing_edit_refusal,
)
from core.graphics import GraphicOverlay
from core.mograph_layers import layer_clips
from core.project_model import Clip, MediaAsset, Project, Track
from core.timeline_operations import find_clip, set_transform_keyframe

KINDS = ["track", "layer", "photo"]


# ---------------------------------------------------------------- la règle partagée


def _project(track_type="video", locked=False):
    project = Project(name="G", width=1280, height=720, fps=25.0)
    track = Track(id="t1", name="T1", type=track_type, locked=locked)
    track.clips.append(Clip(id="c1", asset_id="a1", track_id="t1", timeline_start=0.0, source_in=0.0, source_out=2.0))
    project.tracks.append(track)
    return project


def test_shared_rule_without_the_genre_check_accepts_audio_but_not_a_locked_track():
    assert clip_edit_refusal(_project("audio"), "c1", visual_only=False) is None
    assert clip_edit_refusal(_project("audio"), "c1") == REFUSAL_NOT_VISUAL
    assert clip_edit_refusal(_project("audio", locked=True), "c1", visual_only=False) == REFUSAL_TRACK_LOCKED
    assert clip_edit_refusal(_project(), "c1") is None


def test_compositing_rule_is_the_shared_rule():
    for project in (_project(), _project(locked=True), _project("audio")):
        assert compositing_edit_refusal(project, "c1") == clip_edit_refusal(project, "c1")


def test_a_locked_graphic_carried_by_a_video_clip_is_refused():
    project = _project()
    project.tracks[0].clips[0].graphic = GraphicOverlay(locked=True)
    assert clip_edit_refusal(project, "c1") == REFUSAL_LAYER_LOCKED
    assert clip_edit_refusal(project, "c1", visual_only=False) == REFUSAL_LAYER_LOCKED


# ---------------------------------------------------------------- fenêtre principale


@pytest.fixture
def window(qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from ui.main_window import MainWindow

    monkeypatch.setattr("ui.main_window.QMessageBox.information", lambda *_a, **_k: None)
    monkeypatch.setattr("ui.main_window.QMessageBox.question", lambda *_a, **_k: QMessageBox.Yes)
    monkeypatch.setattr(QMessageBox, "warning", lambda *_a, **_k: QMessageBox.Ok)
    win = MainWindow()
    qtbot.addWidget(win)
    win.timeline_timer.stop()
    monkeypatch.setattr(win.preview_panel, "preview_at", lambda *_a, **_k: None)
    win.project = Project(name="Guard", width=1280, height=720, fps=25.0)
    win._timeline_index = None
    win._reload_timeline_preserving_selection()
    win.history.reset(win.project)
    win.resize(1400, 900)
    win.show()
    return win


def _add_track_clip(win, track_type, clip_id, locked=False):
    asset = MediaAsset(
        id=f"a_{clip_id}", path="/nonexistent/x.mp4", name="x", media_type="audio" if track_type == "audio" else "video",
        duration=5.0, width=0 if track_type == "audio" else 640, height=0 if track_type == "audio" else 360,
        fps=0.0 if track_type == "audio" else 25.0, has_audio=track_type == "audio",
    )
    track = Track(id=f"tr_{clip_id}", name=clip_id, type=track_type, locked=locked)
    clip = Clip(id=clip_id, asset_id=asset.id, track_id=track.id, timeline_start=0.0, source_in=0.0, source_out=5.0)
    track.clips.append(clip)
    win.project.media_assets.append(asset)
    win.project.tracks.append(track)
    win.history.reset(win.project)
    win._reload_timeline_preserving_selection(clip_id)
    win.on_clip_selected(clip_id)
    return track, clip


def _layer(win):
    win.add_layer_at_playhead("shape", "rectangle")
    ((_i, track, clip),) = layer_clips(win.project)
    win.on_clip_selected(clip.id)
    return track, clip


def _prepare(win, kind):
    """Un clip qui porte une image-clé de mouvement et un effet, puis verrouillé selon ``kind`` (rien d'autre ne change)."""
    if kind == "layer":
        track, clip = _layer(win)
    else:
        track, clip = _add_track_clip(win, "video", "v")
        if kind == "photo":
            clip.graphic = GraphicOverlay()
    set_transform_keyframe(win.project, clip.id, "position_x", 0.5, 0.2)
    from core.effects_model import add_effect_to_clip

    if track.type == "video":
        add_effect_to_clip(win.project, clip.id, "blur")
    win.history.reset(win.project)
    return track, clip


def _lock(win, kind, track, clip):
    if kind == "track":
        track.locked = True
    elif kind == "layer":
        win._on_layer_lock(clip.id, True)
    else:
        clip.graphic = replace(clip.graphic, locked=True)
    win._reload_timeline_preserving_selection(clip.id)
    win.on_clip_selected(clip.id)
    win.history.reset(win.project)


def _unlock(win, kind, track, clip):
    if kind == "track":
        track.locked = False
    elif kind == "layer":
        win._on_layer_lock(clip.id, False)
    else:
        clip.graphic = replace(clip.graphic, locked=False)
    win._reload_timeline_preserving_selection(clip.id)
    win.on_clip_selected(clip.id)


@pytest.fixture(params=KINDS)
def locked_clip(request, window):
    track, clip = _prepare(window, request.param)
    _lock(window, request.param, track, clip)
    return window, track, clip


def _refused(win, clip, call):
    """``call()`` ne change ni le modèle ni l'historique, et la barre d'état dit pourquoi."""
    before = copy.deepcopy(clip)
    entries = len(win.history)
    win.statusBar().clearMessage()
    call()
    win._finalize_pending_edit_sessions()
    assert clip == before
    assert len(win.history) == entries
    assert win.statusBar().currentMessage()


# ---------------------------------------------------------------- Mouvement


def test_motion_slider_is_refused_and_the_field_goes_back(locked_clip):
    win, _track, clip = locked_clip
    spin = win.properties_panel._spin_boxes["position_x"]
    shown = spin.value()
    _refused(win, clip, lambda: spin.setValue(shown + 0.5))      # le champ émet : le modèle refuse, le champ revient
    assert spin.value() == pytest.approx(shown)
    _refused(win, clip, lambda: win.properties_panel.transform_changed.emit(clip.id, "opacity", 0.3))


def test_motion_keyframe_buttons_and_reset_are_refused(locked_clip):
    win, _track, clip = locked_clip
    _refused(win, clip, lambda: win.on_transform_keyframe_added(clip.id, "position_y", 0.3, 0.1))
    _refused(win, clip, lambda: win.on_transform_keyframe_removed(clip.id, "position_x", 0.5))
    _refused(win, clip, lambda: win.on_transform_reset(clip.id))


def test_advanced_transform_and_its_keyframe_toggle_are_refused(locked_clip):
    win, _track, clip = locked_clip
    _refused(win, clip, lambda: win.properties_panel.advanced_transform_changed.emit(clip.id, "scale_x", 2.0))
    _refused(win, clip, lambda: win._on_advanced_transform_changed(clip.id, "anchor_x", 0.2))
    _refused(win, clip, lambda: win.properties_panel.advanced_keyframe_toggled.emit(clip.id, "scale_x"))


def test_viewer_drag_cannot_write_and_its_handles_are_not_editable(locked_clip):
    win, _track, clip = locked_clip
    before = copy.deepcopy(clip)
    assert win._apply_property_values(clip.id, {"position_x": 0.4}) is False
    assert clip == before
    win._refresh_viewer_overlay(0.0)
    selection = win.preview_panel.overlay.selection
    assert selection is not None and selection.editable is False


def test_animation_commands_are_refused(locked_clip):
    win, _track, clip = locked_clip
    win.timeline_panel.select_clip(clip.id)
    _refused(win, clip, lambda: win.on_animation_toggled(clip.id, "position_y", True))
    _refused(win, clip, lambda: win.add_keyframe_at_playhead())
    assert win.copy_animation("position_x")
    _refused(win, clip, lambda: win.paste_animation("position_x"))
    from core.keyframe_editing import KeyframeRef, curve_of

    keyframe = curve_of(clip, "position_x").keyframes[0]
    ref = KeyframeRef(clip.id, "position_x", keyframe.id)
    win.set_keyframe_selection({ref})
    _refused(win, clip, lambda: win.on_keyframe_interpolation_requested(None, "linear"))
    _refused(win, clip, lambda: win.on_keyframes_move_requested([ref], 0.1))
    _refused(win, clip, lambda: win.remove_keyframes_command())
    _refused(win, clip, lambda: win.remove_keyframe_at_playhead("position_x"))


# ---------------------------------------------------------------- Vitesse, Effets


def test_speed_group_handlers_are_refused(locked_clip):
    win, _track, clip = locked_clip
    _refused(win, clip, lambda: win.on_speed_changed(clip.id, 2.0))
    _refused(win, clip, lambda: win.on_reverse_toggled(clip.id, True))
    _refused(win, clip, lambda: win.on_freeze_frame_created(clip.id, 1.0, 1.0))
    _refused(win, clip, lambda: win.on_freeze_duration_changed(clip.id, 2.0))
    _refused(win, clip, lambda: win.on_time_remapping_reset(clip.id))


def test_effect_handlers_are_refused(locked_clip):
    win, track, clip = locked_clip
    if track.type != "video":
        pytest.skip("les effets ne s'appliquent qu'aux pistes vidéo")
    effect_id = clip.effects[0].id
    _refused(win, clip, lambda: win.on_clip_effect_added(clip.id, "sepia"))
    _refused(win, clip, lambda: win.on_clip_effect_enabled_changed(clip.id, effect_id, False))
    _refused(win, clip, lambda: win.on_clip_effect_moved(clip.id, effect_id, 1))
    _refused(win, clip, lambda: win.on_clip_effect_parameter_changed(clip.id, effect_id, "intensity", 3.0))
    _refused(win, clip, lambda: win.on_clip_effect_removed(clip.id, effect_id))


# ---------------------------------------------------------------- Calque graphique


@pytest.mark.parametrize("kind", ["layer", "layer_on_locked_track"])
def test_graphic_properties_are_refused_and_the_field_goes_back(window, kind):
    track, clip = _layer(window)
    if kind == "layer":
        window._on_layer_lock(clip.id, True)
    else:
        track.locked = True
    window._reload_timeline_preserving_selection(clip.id)
    window.on_clip_selected(clip.id)
    window.history.reset(window.project)
    text_before = clip.graphic.text
    _refused(window, clip, lambda: window.properties_panel.graphic_property_changed.emit(clip.id, "text", "autre"))
    _refused(window, clip, lambda: window.on_graphic_property_changed(clip.id, "fill_color", "#FF0000"))
    _refused(window, clip, lambda: window.apply_text_animation_to_clip(clip.id, "pop_in"))
    _refused(window, clip, lambda: window.sync_text_on_voice(clip.id))
    assert clip.graphic.text == text_before


# ---------------------------------------------------------------- l'interface suit : désactivée puis réactivée


def _groups(win):
    panel = win.properties_panel
    return {"movement": panel.movement_group, "speed": panel.speed_group, "effects": panel.effects_group,
            "graphics": panel.graphics_group}


@pytest.mark.parametrize("kind", KINDS)
def test_groups_are_disabled_when_locked_and_enabled_again(window, kind):
    track, clip = _prepare(window, kind)
    groups = _groups(window)
    expected = {"movement", "speed"} | ({"effects"} if track.type == "video" else set()) | (
        {"graphics"} if track.type == "graphics" else set())
    assert all(groups[name].isEnabled() for name in expected)
    _lock(window, kind, track, clip)
    assert not any(groups[name].isEnabled() for name in expected)
    assert window.properties_panel.advanced_transform.isEnabled() or not groups["movement"].isEnabled()
    _unlock(window, kind, track, clip)
    assert all(groups[name].isEnabled() for name in expected)


def test_changing_clip_re_enables_the_groups(window):
    track, clip = _prepare(window, "track")
    _lock(window, "track", track, clip)
    other_track, other = _add_track_clip(window, "video", "w")
    assert window.properties_panel.movement_group.isEnabled()
    assert window.properties_panel.speed_group.isEnabled()
    assert window.properties_panel.effects_group.isEnabled()


def test_update_effects_default_keeps_the_old_behaviour(window):
    panel = window.properties_panel
    panel.update_effects_from_clip([], "video")
    assert panel.effects_group.isEnabled()
    panel.update_effects_from_clip([], "video", editable=False)
    assert not panel.effects_group.isEnabled()
    panel.update_effects_from_clip([], "video", True)
    assert panel.effects_group.isEnabled()


# ---------------------------------------------------------------- un clip éditable passe comme avant


def test_editable_video_clip_still_changes_with_one_history_step(window):
    _track, clip = _prepare(window, "track")
    window.history.reset(window.project)
    entries = len(window.history)
    window.properties_panel.transform_changed.emit(clip.id, "position_x", 0.3)
    window._finalize_transform_session()
    assert clip.transform.position_x == pytest.approx(0.3)
    assert len(window.history) == entries + 1
    window.on_speed_changed(clip.id, 2.0)
    assert len(window.history) == entries + 2
    window.on_clip_effect_parameter_changed(clip.id, clip.effects[0].id, "intensity", 3.0)
    assert len(window.history) == entries + 3
    window.statusBar().clearMessage()
    window.on_transform_keyframe_added(clip.id, "position_y", 0.4, 0.1)
    window._finalize_transform_session()
    assert any(k.property_name == "position_y" for k in clip.transform_keyframes)
    assert len(window.history) == entries + 4


def test_editable_layer_still_changes_with_one_history_step(window):
    _track, clip = _layer(window)
    window.history.reset(window.project)
    entries = len(window.history)
    window.on_graphic_property_changed(clip.id, "fill_color", "#FF0000")
    window._finalize_pending_edit_sessions()
    assert clip.graphic.fill_color.upper().startswith("#FF0000")
    assert len(window.history) == entries + 1
    assert window._apply_property_values(clip.id, {"position_x": 0.2}) is True


# ---------------------------------------------------------------- audio : déjà gardé, la garde reste


def test_audio_clip_controls_stay_refused_on_a_locked_track(window):
    track, clip = _add_track_clip(window, "audio", "a", locked=True)
    window.timeline_panel.select_clip(clip.id)
    snapshot = copy.deepcopy(clip)
    entries = len(window.history)
    window.on_clip_gain_changed(6.0)
    window.on_clip_pan_changed(0.5)
    window.on_clip_fade_changed("in", 1.0)
    window.on_clip_fade_from_timeline(clip.id, "out", 1.0)
    window.on_clip_fades_reset(clip.id)
    assert clip == snapshot and len(window.history) == entries
    assert not window.properties_panel.audio_group.isEnabled()
    window.on_clip_audio_effect_removed(clip.id, "nope")
    window.on_clip_audio_effect_moved(clip.id, "nope", 1)
    assert clip == snapshot and len(window.history) == entries


def test_motion_handlers_refuse_an_audio_clip_with_a_message(window):
    _track, clip = _add_track_clip(window, "audio", "a")
    _refused(window, clip, lambda: window.on_transform_property_changed(clip.id, "position_x", 0.3))


# ---------------------------------------------------------------- verbes de script


def _script(project, *ops):
    from core.scripting import Session, apply_script, parse_script

    session = Session(project)
    return session, apply_script(session, parse_script({"kut_script": 1, "ops": list(ops)}))


def _script_layer(locked):
    from core.mograph_layers import add_layer, set_layer_locked

    project = Project(name="S", width=1280, height=720, fps=25.0)
    clip = add_layer(project, "shape", at=0.0, duration=2.0, shape="rectangle")
    if locked:
        set_layer_locked(project, clip.id, True)
    return project, clip


def test_script_verbs_refuse_a_locked_layer():
    project, clip = _script_layer(True)
    before = copy.deepcopy(clip)
    for op in (
        {"op": "set_graphic", "clip": clip.id, "fill_color": "#FF0000"},
        {"op": "add_keyframe", "clip": clip.id, "property": "position_x", "at": 0.5, "value": 0.1},
        {"op": "set_crop", "clip": clip.id, "left": 0.1},
    ):
        _session, report = _script(project, op)
        assert not report.ok, op
        assert "verrouillé" in report.error
    assert find_clip(project, clip.id) == before


def test_script_verbs_still_edit_an_unlocked_layer():
    project, clip = _script_layer(False)
    _session, report = _script(project, {"op": "set_graphic", "clip": clip.id, "fill_color": "#FF0000"})
    assert report.ok, report.error
    assert find_clip(project, clip.id).graphic.fill_color.upper().startswith("#FF0000")


# ---------------------------------------------------------------- menu et raccourcis « Vitesse »


@pytest.mark.parametrize("kind", ["layer", "photo"])
def test_time_commands_refuse_a_locked_layer(window, kind):
    track, clip = _prepare(window, kind)
    _lock(window, kind, track, clip)
    window.timeline_panel.select_clip(clip.id)
    _refused(window, clip, lambda: window.on_time_command(clip.id, "reverse"))
    _refused(window, clip, lambda: window.on_time_command(clip.id, "preset", 2.0))


def test_time_commands_still_edit_an_editable_clip(window):
    _track, clip = _prepare(window, "track")
    window.timeline_panel.select_clip(clip.id)
    entries = len(window.history)
    window.on_time_command(clip.id, "reverse")
    assert len(window.history) == entries + 1
