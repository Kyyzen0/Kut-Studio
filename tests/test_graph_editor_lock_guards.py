"""Éditeur de courbes et préréglages d'effets : piste ou calque verrouillé (y compris posé fenêtre ouverte)."""

from __future__ import annotations

import copy

import pytest
from PySide6.QtCore import QPointF, Qt

from core.compositing_guard import clip_edit_refusal
from core.effects_library import apply_preset_to_clip, builtin_presets
from core.graphics import GraphicOverlay
from core.keyframe_editing import KeyframeRef, curve_of
from core.project_model import Clip, Project, Track
from core.time_map import SPEED_PROPERTY
from tests.test_inspector_lock_guards import (  # noqa: F401 - fixtures et aides communes
    KINDS,
    _lock,
    _prepare,
    _refused,
    _unlock,
    window,
)


def _open(win, clip):
    """Éditeur ouvert sur ``position_x`` avec l'image-clé sélectionnée ; renvoie (éditeur, image-clé, ref)."""
    win.timeline_panel.select_clip(clip.id)
    editor = win.open_graph_editor("position_x")
    editor.set_property("position_x")
    keyframe = curve_of(clip, "position_x").keyframes[0]
    ref = KeyframeRef(clip.id, "position_x", keyframe.id)
    win.set_keyframe_selection({ref})
    return editor, keyframe, ref


@pytest.fixture(params=KINDS)
def opened(request, window):
    """Verrou posé APRÈS l'ouverture de l'éditeur (le cas le plus exigeant) ; l'éditeur doit suivre."""
    track, clip = _prepare(window, request.param)
    editor, keyframe, ref = _open(window, clip)
    assert editor.time_spin.isEnabled() and editor.interpolation_combo.isEnabled()
    _lock(window, request.param, track, clip)
    return window, request.param, track, clip, editor, keyframe, ref


def test_fields_follow_the_lock_in_both_directions(opened):
    win, kind, track, clip, editor, keyframe, ref = opened
    editor._refresh_fields()
    for widget in (
        editor.time_spin,
        editor.value_spin,
        editor.interpolation_combo,
        editor.tangent_combo,
    ):
        assert not widget.isEnabled()
    assert curve_of(clip, "position_x").keyframes  # la courbe reste lisible
    win.set_keyframe_selection({ref})
    assert not editor.time_spin.isEnabled()
    _unlock(win, kind, track, clip)
    win.set_keyframe_selection({ref})
    assert editor.time_spin.isEnabled() and editor.value_spin.isEnabled()
    assert editor.interpolation_combo.isEnabled() and editor.tangent_combo.isEnabled()


def test_editor_opened_on_an_already_locked_clip_is_read_only(window):
    track, clip = _prepare(window, "track")
    _lock(window, "track", track, clip)
    editor, _kf, _ref = _open(window, clip)
    assert (
        editor.is_locked()
        and not editor.time_spin.isEnabled()
        and not editor.interpolation_combo.isEnabled()
    )


def test_dragging_keyframes_and_handles_is_refused(opened):
    win, _kind, _track, clip, editor, keyframe, _ref = opened
    origin = {
        "anchor": keyframe,
        "values": {keyframe.id: float(keyframe.value)},
        "anchor_time": keyframe.time_seconds,
    }
    _refused(
        win,
        clip,
        lambda: editor.drag_keyframes(origin, QPointF(100, 100), QPointF(160, 60)),
    )
    _refused(
        win,
        clip,
        lambda: editor.drag_handle(keyframe, "out", keyframe.time_seconds + 1.0, 1.0),
    )
    entries = len(win.history)
    editor.finish_gesture("x")  # aucun geste ouvert : aucune entrée d'historique
    assert len(win.history) == entries


def test_adding_a_keyframe_is_refused(opened):
    win, _kind, _track, clip, editor, _kf, _ref = opened
    _refused(win, clip, lambda: editor.add_keyframe_at(1.0))


def test_interpolation_and_tangent_combos_are_refused(opened):
    win, _kind, _track, clip, editor, _kf, _ref = opened
    editor.interpolation_combo.setCurrentIndex(1)
    _refused(win, clip, lambda: editor._on_interpolation(1))
    editor.tangent_combo.setCurrentIndex(1)
    _refused(win, clip, lambda: editor._on_tangent_mode(1))


def test_time_and_value_fields_are_refused(opened):
    win, _kind, _track, clip, editor, keyframe, _ref = opened
    editor.time_spin.setValue(keyframe.time_seconds + 0.2)
    _refused(win, clip, editor._on_time_edited)
    editor.value_spin.setValue(0.9)
    _refused(win, clip, editor._on_value_edited)


def test_host_graph_helpers_are_refused(opened):
    win, _kind, _track, clip, _editor, _kf, ref = opened
    _refused(win, clip, lambda: win.graph_set_keyframe(ref, time=0.7, value=0.1))
    _refused(win, clip, lambda: win.graph_set_tangents(ref, out_slope=2.0))


def test_speed_curve_edits_are_refused(opened):
    win, kind, _track, clip, editor, _kf, _ref = opened
    if kind == "layer":
        pytest.skip("un calque graphique n'a pas de courbe de vitesse")
    editor.set_property(SPEED_PROPERTY)
    assert editor._is_speed()
    _refused(win, clip, lambda: editor.add_keyframe_at(1.0))


def test_mouse_gestures_on_the_canvas_do_not_write(opened, qtbot):
    win, _kind, _track, clip, editor, keyframe, _ref = opened
    before, entries = copy.deepcopy(clip), len(win.history)
    canvas = editor.canvas
    start = canvas.to_screen(keyframe.time_seconds, float(keyframe.value)).toPoint()
    qtbot.mousePress(canvas, Qt.LeftButton, pos=start)
    qtbot.mouseMove(canvas, start + QPointF(40, 25).toPoint())
    qtbot.mouseRelease(canvas, Qt.LeftButton, pos=start + QPointF(40, 25).toPoint())
    qtbot.mouseDClick(
        canvas, Qt.LeftButton, pos=QPointF(canvas.width() - 30, 30).toPoint()
    )
    win._finalize_pending_edit_sessions()
    assert clip == before and len(win.history) == entries


def test_editable_clip_still_edits_with_one_history_step(window):
    _track, clip = _prepare(window, "track")
    editor, keyframe, _ref = _open(window, clip)
    window.history.reset(window.project)
    entries = len(window.history)
    editor.add_keyframe_at(1.0)
    assert len(window.history) == entries + 1
    editor.time_spin.setValue(keyframe.time_seconds + 0.2)
    editor._on_time_edited()
    assert len(window.history) == entries + 2


# ---------------------------------------------------------------- préréglages d'effets


def _preset():
    return builtin_presets()[0]


def test_core_preset_refuses_a_locked_graphic_on_a_video_clip():
    project = Project(name="P", width=1280, height=720, fps=25.0)
    track = Track(id="t", name="T", type="video")
    clip = Clip(
        id="c",
        asset_id="a",
        track_id="t",
        timeline_start=0.0,
        source_in=0.0,
        source_out=2.0,
    )
    track.clips.append(clip)
    project.tracks.append(track)
    assert apply_preset_to_clip(project, "c", _preset())
    clip.effects = []
    clip.graphic = GraphicOverlay(locked=True)
    with pytest.raises(ValueError):
        apply_preset_to_clip(project, "c", _preset())
    assert clip.effects == []
    assert clip_edit_refusal(project, "c", visual_only=False)


@pytest.mark.parametrize("kind", ["track", "photo"])
def test_applying_a_preset_is_refused_and_the_library_button_follows(window, kind):
    track, clip = _prepare(window, kind)
    window.timeline_panel.select_clip(clip.id)
    view = window.project_panel.effects_view
    view.select_preset(_preset().id)
    assert view.apply_button.isEnabled()
    _lock(window, kind, track, clip)
    window.timeline_panel.select_clip(clip.id)
    window._sync_effects_library_context()
    view.select_preset(_preset().id)
    assert not view.apply_button.isEnabled()
    _refused(
        window, clip, lambda: window.on_effect_preset_apply_requested(_preset().id)
    )
    _unlock(window, kind, track, clip)
    window.timeline_panel.select_clip(clip.id)
    window._sync_effects_library_context()
    view.select_preset(_preset().id)
    assert view.apply_button.isEnabled()


def test_applying_a_preset_still_works_on_an_editable_clip(window):
    _track, clip = _prepare(window, "track")
    window.timeline_panel.select_clip(clip.id)
    window.history.reset(window.project)
    entries = len(window.history)
    window.on_effect_preset_apply_requested(_preset().id)
    assert len(window.history) == entries + 1
