"""Le groupe Compositing (masques, fusion, incrustation) obéit au verrou comme les autres modifications de calques."""

from __future__ import annotations

from dataclasses import replace

import pytest

from core.compositing import ChromaKey, Compositing, Mask
from core.compositing_guard import (
    REFUSAL_LAYER_LOCKED,
    REFUSAL_MISSING,
    REFUSAL_NOT_VISUAL,
    REFUSAL_TRACK_LOCKED,
    compositing_edit_refusal,
)
from core.mograph_layers import layer_clips
from core.project_model import Clip, MediaAsset, Project, Track

NEW = Compositing(masks=(Mask(),), chroma_key=ChromaKey(True, "#00FF00"), blend_mode="multiply")


# ---------------------------------------------------------------- fonction pure


def _project(track_type="video", locked=False):
    project = Project(name="G", width=1280, height=720, fps=25.0)
    track = Track(id="t1", name="T1", type=track_type, locked=locked)
    track.clips.append(Clip(id="c1", asset_id="a1", track_id="t1", timeline_start=0.0, source_in=0.0, source_out=2.0))
    project.tracks.append(track)
    return project


def test_refusal_is_none_for_an_editable_video_clip():
    assert compositing_edit_refusal(_project(), "c1") is None


def test_refusal_codes():
    assert compositing_edit_refusal(_project(), "nope") == REFUSAL_MISSING
    assert compositing_edit_refusal(_project(locked=True), "c1") == REFUSAL_TRACK_LOCKED
    assert compositing_edit_refusal(_project("audio"), "c1") == REFUSAL_NOT_VISUAL
    assert compositing_edit_refusal(_project("subtitle"), "c1") == REFUSAL_NOT_VISUAL


def test_refusal_for_a_locked_graphic_layer_and_its_locked_track():
    from core.mograph_layers import add_layer, set_layer_locked

    project = Project(name="G", width=1280, height=720, fps=25.0)
    clip = add_layer(project, "shape", at=0.0, duration=2.0, shape="rectangle")
    assert compositing_edit_refusal(project, clip.id) is None
    set_layer_locked(project, clip.id, True)
    assert compositing_edit_refusal(project, clip.id) == REFUSAL_LAYER_LOCKED
    set_layer_locked(project, clip.id, False)
    next(t for t in project.tracks if t.type == "graphics").locked = True
    assert compositing_edit_refusal(project, clip.id) == REFUSAL_TRACK_LOCKED


# ---------------------------------------------------------------- fenêtre principale


@pytest.fixture
def window(qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from ui.main_window import MainWindow

    monkeypatch.setattr("ui.main_window.QMessageBox.information", lambda *_a, **_k: None)
    monkeypatch.setattr("ui.main_window.QMessageBox.question", lambda *_a, **_k: QMessageBox.Yes)
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


def _group(win):
    return win.properties_panel.compositing_group


def test_editable_clip_still_changes_with_one_history_step(window):
    _track, clip = _add_track_clip(window, "video", "v")
    assert _group(window).isEnabled()
    entries = len(window.history)
    window.properties_panel.compositing_changed.emit("v", NEW)
    window._finalize_pending_edit_sessions()
    assert clip.compositing == NEW
    assert len(window.history) == entries + 1


@pytest.mark.parametrize("kind", ["track", "layer", "audio"])
def test_refused_change_writes_nothing_and_says_so(window, kind):
    if kind == "track":
        track, clip = _add_track_clip(window, "video", "v", locked=True)
    elif kind == "audio":
        track, clip = _add_track_clip(window, "audio", "a")
    else:
        track, clip = _layer(window)
        window._on_layer_lock(clip.id, True)
    before = clip.compositing
    entries = len(window.history)
    window.statusBar().clearMessage()
    window.properties_panel.compositing_changed.emit(clip.id, NEW)
    window._finalize_pending_edit_sessions()
    assert clip.compositing == before
    assert len(window.history) == entries
    assert window.statusBar().currentMessage()


def test_refused_change_puts_the_inspector_back_to_the_real_value(window):
    _track, clip = _add_track_clip(window, "video", "v", locked=True)
    _group(window).blend_mode.setCurrentIndex(2)       # le champ émet : le modèle refuse, le champ doit revenir
    assert _group(window).blend_mode.currentData() == clip.compositing.blend_mode.value
    assert clip.compositing == Compositing()


@pytest.mark.parametrize("kind", ["track", "layer", "audio"])
def test_group_is_disabled_then_reenabled(window, kind):
    editable_track, editable_clip = _add_track_clip(window, "video", "ok")
    if kind == "track":
        track, clip = _add_track_clip(window, "video", "v", locked=True)
    elif kind == "audio":
        track, clip = _add_track_clip(window, "audio", "a")
    else:
        track, clip = _layer(window)
        window._on_layer_lock(clip.id, True)
        window.on_clip_selected(clip.id)
    assert not _group(window).isEnabled()
    window.on_clip_selected(editable_clip.id)
    assert _group(window).isEnabled()
    if kind == "track":
        track.locked = False
        window._reload_timeline_preserving_selection(clip.id)
    elif kind == "layer":
        window._on_layer_lock(clip.id, False)
    else:
        return
    window.on_clip_selected(clip.id)
    assert _group(window).isEnabled()


def test_group_is_enabled_with_no_selection(window):
    _add_track_clip(window, "audio", "a")
    assert not _group(window).isEnabled()
    window.properties_panel.show_clip(None)
    assert _group(window).isEnabled()


def test_set_editable_default_keeps_the_old_behaviour():
    from ui.compositing_editor import CompositingEditor

    editor = CompositingEditor("")
    assert editor.isEnabled()
    editor.set_editable()
    assert editor.isEnabled()
    editor.set_editable(False)
    assert not editor.isEnabled()


def test_mask_added_by_hand_is_kept_not_rewritten(window):
    """Une valeur identique au modèle reste un no-op : pas d'étape d'historique."""
    _track, clip = _add_track_clip(window, "video", "v")
    entries = len(window.history)
    window.properties_panel.compositing_changed.emit("v", replace(clip.compositing))
    assert len(window.history) == entries
