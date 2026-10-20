"""Moniteur et étalonnage animé, côté fenêtre principale (ADR-0008, étape 7) : le clip sous le curseur, les pistes en dessous, la pause."""

from __future__ import annotations

import pytest
from main_window_harness import build_window, install_dialogs

from core.animation import InterpolationType, Keyframe
from core.color_grading import ColorGrade
from core.grade_targets import effective_grade


def _kf(prop, time, value):
    return Keyframe(prop, time, value, InterpolationType.LINEAR)


@pytest.fixture
def window(qtbot, monkeypatch, tmp_path):
    install_dialogs(monkeypatch)
    window = build_window(qtbot, monkeypatch, tmp_path / "config")
    monkeypatch.setattr(window.preview_panel, "preview_at", lambda *_a, **_k: None)
    monkeypatch.setattr(window, "_present_cached_preview_at", lambda *_a, **_k: False)
    for asset in window.project.media_assets:
        asset.path = str(tmp_path / f"{asset.id}.mp4")           # le projet d'exemple n'a pas de fichiers : un chemin suffit
    window._reload_timeline_preserving_selection()
    return window


def _top_clip(window):
    """Le clip de la piste vidéo la plus haute qui en a un : celui que le moniteur montre (V2 couvre V1 dans l'exemple)."""
    return next(track for track in reversed(window.project.tracks) if track.type == "video" and track.clips).clips[0]


def _animate(clip, base=None):
    clip.color_grade = base
    clip.animation = [_kf("color.exposure", 0.0, -0.8), _kf("color.exposure", clip.duration, 0.8)]


def test_the_monitor_follows_the_curve_while_the_playhead_moves_and_stops_on_the_instant(window):
    clip = _top_clip(window)
    _animate(clip, ColorGrade(saturation=1.2))
    shown = []
    for fraction in (0.1, 0.5, 0.9):
        window.playhead_seconds = clip.timeline_start + fraction * clip.duration
        window._sync_preview_to_timeline()
        grade = window.preview_panel._gpu_grade
        assert grade == effective_grade(clip, window.playhead_seconds - clip.timeline_start)
        assert grade.saturation == 1.2
        shown.append(grade.exposure)
    assert shown == sorted(shown) and len(set(shown)) == 3


def test_a_clip_without_a_grade_key_keeps_its_own_object_at_every_frame(window):
    clip = _top_clip(window)
    grade = ColorGrade(exposure=0.4)
    clip.color_grade = grade
    for step in range(5):
        window.playhead_seconds = clip.timeline_start + 0.05 + step * 0.04
        window._sync_preview_to_timeline()
        assert window.preview_panel._gpu_grade is grade


def test_pausing_settles_the_monitor_on_the_instant_only_when_a_visible_clip_is_animated(window, monkeypatch):
    clip = _top_clip(window)
    calls = []
    original = window._sync_preview_to_timeline
    monkeypatch.setattr(window, "_sync_preview_to_timeline", lambda: calls.append(1) or original())
    window.playhead_seconds = clip.timeline_start + 0.2
    window._settle_animated_grade()
    assert calls == [], "aucun étalonnage animé : le chemin d'avant, pas de resynchronisation"
    _animate(clip)
    window._settle_animated_grade()
    assert calls == [1]
    window.playhead_seconds = clip.timeline_start + clip.duration + 100.0       # rien de visible sous le curseur
    window._settle_animated_grade()
    assert calls == [1]


def test_underlay_clips_get_their_own_evaluated_grade(window, monkeypatch):
    bottom = window.project.tracks[0].clips[0]
    _animate(bottom)
    window.playhead_seconds = bottom.timeline_start + 0.3 * bottom.duration
    active = window._ensure_timeline_index().active_at(window.project, window.playhead_seconds)
    videos = [item for item in active if item.track_type == "video"]
    assert videos, "le projet d'exemple a un clip vidéo sous le curseur"
    captured = []
    monkeypatch.setattr(type(window.preview_panel), "gpu_active", property(lambda _self: True))
    monkeypatch.setattr(window.preview_panel, "set_video_underlays", lambda entries: captured.extend(entries))
    window._sync_gpu_video_underlays(videos)
    mine = [entry for entry in captured if entry[0] == f"clip:{bottom.id}"]
    assert mine and mine[0][10] == effective_grade(bottom, window.playhead_seconds - bottom.timeline_start)
