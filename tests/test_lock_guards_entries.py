"""Verrous (piste, calque, mauvais genre) : les derniers chemins d'entrée qui écrivaient sans regarder.

Une seule règle (``core.compositing_guard``) ; ici les points d'entrée du cœur (images-clés, transform, transitions,
sous-titres, impact) et de l'interface (menu « Réseaux sociaux », modèles de texte, sous-titres, transitions). Chaque
refus ne change ni le projet ni l'historique ; le même chemin sur un clip libre fonctionne ; annuler / rétablir restent
possibles sur un clip dont la piste est verrouillée. Un test d'inventaire compare la liste des entrées gardées au code.
"""

from __future__ import annotations

import copy
import inspect
from dataclasses import replace

import pytest

import core.keyframe_editing as keyframe_editing
from core.compositing_guard import ClipEditRefused, REFUSAL_LAYER_LOCKED, REFUSAL_TRACK_LOCKED, REFUSAL_WRONG_TRACK
from core.graphics import GraphicOverlay
from core.impact_fx import apply_impact_on_cuts
from core.keyframe_editing import AnimationClipboard, KeyframeRef, add_keyframe
from core.project_model import Clip, Project, Track
from core.timeline_operations import (
    add_subtitle_clip,
    find_clip,
    remove_transform_keyframe,
    reset_clip_transform,
    set_clip_transform,
    set_transform_keyframe,
)
from core.transitions import TransitionType, add_transition, remove_transition, update_transition
from tests.test_inspector_lock_guards import (  # noqa: F401 - fixtures et aides communes
    _add_track_clip,
    _refused,
    window,
)

KINDS = ["track", "layer", "photo"]


def _video_project(kind: str | None = None, *, clips: int = 1) -> Project:
    """Une piste vidéo de ``clips`` clips bord à bord ; ``kind`` la verrouille (piste) ou verrouille le calque du premier."""
    project = Project(name="G", width=1280, height=720, fps=25.0)
    track = Track(id="t1", name="T1", type="video", locked=kind == "track")
    for index in range(clips):
        track.clips.append(Clip(id=f"c{index + 1}", asset_id="a1", track_id="t1", timeline_start=2.0 * index,
                                source_in=0.0, source_out=2.0))
    if kind in ("layer", "photo"):
        track.clips[0].graphic = GraphicOverlay(locked=True)
    project.tracks.append(track)
    return project


def _free_with_keyframe() -> Project:
    project = _video_project()
    set_transform_keyframe(project, "c1", "position_x", 0.5, 0.2)
    return project


def _locked_with_keyframe(kind: str) -> Project:
    project = _free_with_keyframe()
    track = project.tracks[0]
    if kind == "track":
        track.locked = True
    else:
        track.clips[0].graphic = GraphicOverlay(locked=True)
    return project


def _ref(project: Project) -> KeyframeRef:
    frame = keyframe_editing.curve_of(project.tracks[0].clips[0], "position_x").keyframes[0]
    return KeyframeRef("c1", "position_x", frame.id)


# ---------------------------------------------------------------- cœur : images-clés, transform


def _keyframe_calls(project: Project):
    ref = _ref(project)
    clipboard = AnimationClipboard((("position_x", tuple(keyframe_editing.curve_of(
        project.tracks[0].clips[0], "position_x").keyframes)),))
    return {
        "add_keyframe": lambda: add_keyframe(project, "c1", "position_y", 0.5, 0.3),
        "set_value_at": lambda: keyframe_editing.set_value_at(project, "c1", "position_x", 0.5, 0.4),
        "remove_keyframes": lambda: keyframe_editing.remove_keyframes(project, [ref]),
        "remove_keyframe_at": lambda: keyframe_editing.remove_keyframe_at(project, "c1", "position_x", 0.5),
        "set_animation_enabled": lambda: keyframe_editing.set_animation_enabled(project, "c1", "position_x", False, 0.5),
        "move_keyframes": lambda: keyframe_editing.move_keyframes(project, [ref], 0.2),
        "set_keyframe_time": lambda: keyframe_editing.set_keyframe_time(project, ref, 0.7),
        "set_keyframe_values": lambda: keyframe_editing.set_keyframe_values(project, {ref: 0.9}),
        "set_interpolation": lambda: keyframe_editing.set_interpolation(project, [ref], "hold"),
        "set_tangents": lambda: keyframe_editing.set_tangents(project, ref, out_slope=0.3),
        "paste_keyframes": lambda: keyframe_editing.paste_keyframes(project, "c1", clipboard, 1.0),
        "set_clip_transform": lambda: set_clip_transform(project, "c1", replace(
            project.tracks[0].clips[0].transform, opacity=0.5)),
        "set_transform_keyframe": lambda: set_transform_keyframe(project, "c1", "opacity", 1.0, 0.5),
        "remove_transform_keyframe": lambda: remove_transform_keyframe(project, "c1", "position_x", 0.5),
        "reset_clip_transform": lambda: reset_clip_transform(project, "c1"),
    }


KEYFRAME_CALLS = sorted(_keyframe_calls(_free_with_keyframe()))


@pytest.mark.parametrize("name", KEYFRAME_CALLS)
@pytest.mark.parametrize("kind", ["track", "layer"])
def test_keyframe_and_transform_mutators_refuse_a_locked_clip_and_write_nothing(name, kind):
    project = _locked_with_keyframe(kind)
    before = copy.deepcopy(project)
    with pytest.raises(ClipEditRefused) as error:
        _keyframe_calls(project)[name]()
    assert error.value.code == (REFUSAL_TRACK_LOCKED if kind == "track" else REFUSAL_LAYER_LOCKED)
    assert project == before


@pytest.mark.parametrize("name", KEYFRAME_CALLS)
def test_keyframe_and_transform_mutators_still_work_on_a_free_clip(name):
    project = _free_with_keyframe()
    before = copy.deepcopy(project)
    _keyframe_calls(project)[name]()
    if name not in {"set_animation_enabled"}:                       # ce chemin réécrit une propriété déjà animée
        assert project != before or name in {"paste_keyframes"}


def test_a_visual_property_on_an_audio_or_subtitle_clip_is_refused_with_the_wrong_track_code():
    for track_type in ("audio", "subtitle"):
        project = Project(name="G", width=1280, height=720, fps=25.0)
        track = Track(id="t1", name="T1", type=track_type)
        track.clips.append(Clip(id="c1", asset_id="a1", track_id="t1", timeline_start=0.0, source_in=0.0, source_out=2.0))
        project.tracks.append(track)
        before = copy.deepcopy(project)
        with pytest.raises(ClipEditRefused) as error:
            add_keyframe(project, "c1", "opacity", 0.5, 0.3)
        assert error.value.code == REFUSAL_WRONG_TRACK
        assert project == before


def test_an_unknown_clip_keeps_its_key_error():
    with pytest.raises(KeyError):
        add_keyframe(_video_project(), "nope", "opacity", 0.0, 0.3)


# ---------------------------------------------------------------- cœur : transitions, sous-titres, impact


def _with_transition(kind: str | None) -> tuple[Project, str]:
    free = _video_project(clips=2)
    transition = add_transition(free, "c1", "c2", TransitionType.CROSSFADE, 0.5)
    if kind == "track":
        free.tracks[0].locked = True
    elif kind:
        free.tracks[0].clips[1].graphic = GraphicOverlay(locked=True)
    return free, transition.id


@pytest.mark.parametrize("kind", ["track", "layer"])
def test_transition_add_update_and_remove_refuse_locked_clips(kind):
    project = _video_project(clips=2)
    project.tracks[0].clips[1].graphic = GraphicOverlay(locked=True) if kind == "layer" else None
    project.tracks[0].locked = kind == "track"
    before = copy.deepcopy(project)
    with pytest.raises(ClipEditRefused):
        add_transition(project, "c1", "c2", TransitionType.CROSSFADE, 0.5)
    assert project == before
    project, transition_id = _with_transition(kind)
    before = copy.deepcopy(project)
    with pytest.raises(ClipEditRefused):
        update_transition(project, transition_id, duration=0.3)
    with pytest.raises(ClipEditRefused):
        remove_transition(project, transition_id)
    assert project == before


def test_transition_functions_still_work_on_free_clips():
    project, transition_id = _with_transition(None)
    update_transition(project, transition_id, duration=0.3)
    remove_transition(project, transition_id)
    assert project.transitions == []


def test_a_subtitle_cannot_be_added_to_a_locked_track_and_nothing_is_created():
    project = Project(name="G", width=1280, height=720, fps=25.0)
    project.tracks.append(Track(id="S1", name="S1", type="subtitle", locked=True))
    before = copy.deepcopy(project)
    with pytest.raises(ValueError):
        add_subtitle_clip(project, "Bonjour", 0.0, 2.0)
    assert project == before
    project.tracks[0].locked = False
    assert add_subtitle_clip(project, "Bonjour", 0.0, 2.0).text == "Bonjour"


def test_impact_on_cuts_refuses_a_locked_layer_among_the_incoming_clips_before_any_write():
    project = _video_project(clips=3)
    project.tracks[0].clips[2].graphic = GraphicOverlay(locked=True)       # le dernier plan qui arrive
    before = copy.deepcopy(project)
    with pytest.raises(ClipEditRefused):
        apply_impact_on_cuts(project, "t1")
    assert project == before                                               # le plan 2 n'a pas été animé à moitié
    project.tracks[0].clips[2].graphic = None
    assert apply_impact_on_cuts(project, "t1") == 2


# ---------------------------------------------------------------- interface


def _select(win, *clip_ids):
    win.timeline_panel._set_selection(list(clip_ids), clip_ids[0], announce=False)


def _lock_kind(win, kind, track, clip):
    if kind == "track":
        track.locked = True
    else:
        clip.graphic = GraphicOverlay(locked=True) if clip.graphic is None else replace(clip.graphic, locked=True)
    win._reload_timeline_preserving_selection(clip.id)
    win.history.reset(win.project)


@pytest.fixture(params=["track", "layer"])
def locked_video(request, window):  # noqa: F811
    track, clip = _add_track_clip(window, "video", "v")
    _lock_kind(window, request.param, track, clip)
    return window, track, clip


SOCIAL = ["toggle_fill_frame_for_selection", "apply_impact_zoom_to_selection", "apply_shake_to_selection",
          "follow_subject_in_selection"]


@pytest.mark.parametrize("command", SOCIAL)
def test_social_menu_commands_are_refused_on_a_locked_clip(locked_video, command):
    win, _track, clip = locked_video
    _select(win, clip.id)
    _refused(win, clip, getattr(win, command))


def test_social_ken_burns_is_refused_on_a_locked_photo_layer(window, tmp_path):  # noqa: F811
    from PySide6.QtGui import QImage

    from core.graphics import add_graphic_clip

    picture = tmp_path / "photo.png"
    image = QImage(64, 36, QImage.Format_RGB32)
    image.fill(0xFF336699)
    assert image.save(str(picture))
    clip = add_graphic_clip(window.project, "image", timeline_start=0.0, duration=3.0, source_path=str(picture))
    track = next(t for t in window.project.tracks if clip in t.clips)
    window.history.reset(window.project)
    window._reload_timeline_preserving_selection(clip.id)
    _lock_kind(window, "track", track, clip)
    _select(window, clip.id)
    _refused(window, clip, window.apply_ken_burns_to_selection)


def test_social_fill_frame_still_works_on_a_free_clip_and_ignores_audio(window):  # noqa: F811
    _track, clip = _add_track_clip(window, "video", "v")
    _select(window, clip.id)
    window.toggle_fill_frame_for_selection()
    assert clip.transform.fill is True
    audio_track, audio = _add_track_clip(window, "audio", "au")
    _select(window, audio.id)
    before = copy.deepcopy(audio)
    window.apply_impact_zoom_to_selection()
    window.apply_shake_to_selection()
    window.apply_ken_burns_to_selection()
    assert audio == before                                                  # un clip audio n'a ni cadrage ni mouvement


def _subtitle(win, *, locked=False, track_type="subtitle"):
    track = Track(id="S9", name="S9", type=track_type, locked=locked)
    win.project.tracks.append(track)
    if track_type == "subtitle":
        clip = add_subtitle_clip(win.project, "Salut", 0.0, 2.0, track_id="S9") if not locked else None
        if clip is None:
            track.locked = False
            clip = add_subtitle_clip(win.project, "Salut", 0.0, 2.0, track_id="S9")
            track.locked = True
    else:
        clip = Clip(id="aud", asset_id="x", track_id="S9", timeline_start=0.0, source_in=0.0, source_out=2.0)
        track.clips.append(clip)
    win.history.reset(win.project)
    win._reload_timeline_preserving_selection(clip.id)
    return track, clip


def _text_preset():
    from core.text_presets import builtin_text_presets

    return next(p for p in builtin_text_presets() if p.style != Clip(
        id="x", asset_id="x", track_id="x", timeline_start=0, source_in=0, source_out=1).text_style)


def test_text_preset_and_subtitle_edits_are_refused_on_a_locked_subtitle_track(window):  # noqa: F811
    _track, clip = _subtitle(window, locked=True)
    preset = _text_preset()
    clip.text_style = replace(preset.style, font_size=preset.style.font_size + 3)     # ni par défaut ni celui du modèle
    window.history.reset(window.project)
    style = replace(preset.style, font_size=preset.style.font_size + 7)
    _refused(window, clip, lambda: window._apply_text_preset_to_clip(clip.id, preset))
    _refused(window, clip, lambda: window.on_subtitle_content_changed(clip.id, "Autre"))
    _refused(window, clip, lambda: window.on_subtitle_style_changed(clip.id, style))
    _refused(window, clip, lambda: window.on_subtitle_style_reset(clip.id))


def test_text_preset_is_refused_on_a_clip_that_is_not_a_subtitle(window):  # noqa: F811
    _track, clip = _subtitle(window, track_type="audio")
    _refused(window, clip, lambda: window._apply_text_preset_to_clip(clip.id, _text_preset()))


def test_text_preset_and_subtitle_edits_work_on_a_free_subtitle(window):  # noqa: F811
    _track, clip = _subtitle(window)
    preset = _text_preset()
    assert window._apply_text_preset_to_clip(clip.id, preset) is True
    assert clip.text_style == preset.style
    window.on_subtitle_content_changed(clip.id, "Autre")
    assert clip.text == "Autre"
    window._finalize_pending_edit_sessions()


def _two_clips(win, *, locked_kind=None):
    track, first = _add_track_clip(win, "video", "v1")
    second = Clip(id="v2", asset_id=first.asset_id, track_id=track.id, timeline_start=5.0, source_in=0.0, source_out=5.0)
    track.clips.append(second)
    win.history.reset(win.project)
    win._reload_timeline_preserving_selection(first.id)
    return track, first, second


@pytest.mark.parametrize("kind", ["track", "layer"])
def test_transition_apply_edit_and_remove_are_refused_on_locked_clips(window, kind):  # noqa: F811
    from core.transition_presets import builtin_transition_presets

    track, first, second = _two_clips(window)
    transition = add_transition(window.project, "v1", "v2", TransitionType.CROSSFADE, 0.5)
    window.history.reset(window.project)
    window._reload_timeline_preserving_selection(first.id)
    _lock_kind(window, kind, track, second)
    baseline = copy.deepcopy(window.project)
    entries = len(window.history)
    window.statusBar().clearMessage()
    window.on_transition_duration_changed(transition.id, 0.3)
    window.on_transition_type_changed(transition.id, "dissolve")
    window.remove_selected_transition(transition.id)
    assert window.project == baseline and len(window.history) == entries
    assert window.statusBar().currentMessage()
    # ... et la pose d'un préréglage entre deux clips sélectionnés d'une piste verrouillée.
    window.project.transitions.clear()
    baseline = copy.deepcopy(window.project)
    _select_both(window, first.id, second.id)
    window.on_transition_preset_apply_requested(builtin_transition_presets()[0].id, 0.5)
    assert window.project == baseline and len(window.history) == entries


def _select_both(win, a, b):
    win.timeline_panel._set_selection([a, b], a, announce=False)


def test_transition_apply_edit_and_remove_work_on_free_clips(window):  # noqa: F811
    from core.transition_presets import builtin_transition_presets

    _track, first, second = _two_clips(window)
    _select_both(window, first.id, second.id)
    window.on_transition_preset_apply_requested(builtin_transition_presets()[0].id, 0.5)
    (transition,) = window.project.transitions
    window.on_transition_duration_changed(transition.id, 0.3)
    assert window.project.transitions[0].duration == pytest.approx(0.3)
    window.remove_selected_transition(transition.id)
    assert window.project.transitions == []


def test_undo_and_redo_still_restore_a_clip_whose_track_got_locked(window):  # noqa: F811
    track, clip = _add_track_clip(window, "video", "v")
    set_clip_transform(window.project, clip.id, replace(clip.transform, opacity=0.4))
    window._record_history("opacité")
    track.locked = True                                                     # verrouillée après coup : l'historique reste libre
    window.undo_last()
    assert find_clip(window.project, "v").transform.opacity == pytest.approx(1.0)
    window.redo_last()
    assert find_clip(window.project, "v").transform.opacity == pytest.approx(0.4)


# ---------------------------------------------------------------- inventaire


KEYFRAME_READ_ONLY = {"find_clip", "clip_duration", "curve_of", "is_animated", "value_at", "keyframe_by_id", "keyframe_times",
                      "previous_keyframe_time", "next_keyframe_time", "snap_to_frame", "copy_keyframes", "can_paste"}

# Entrées qui écrivent dans un clip et leur garde (texte attendu dans leur source). Une nouvelle entrée non listée
# ne casse rien ici : elle doit être ajoutée à cette liste ET à docs/lock-guards.md (la doc dit ce qui reste non gardé).
GUARDED_UI = {
    ("ui.main_window_mixins.social", "SocialMixin.toggle_fill_frame_for_selection"): "_social_clips_refused",
    ("ui.main_window_mixins.social", "SocialMixin.follow_subject_in_selection"): "_social_clips_refused",
    ("ui.main_window_mixins.social", "SocialMixin.apply_ken_burns_to_selection"): "_social_clips_refused",
    ("ui.main_window_mixins.social", "SocialMixin._each_clip"): "_social_clips_refused",
    ("ui.main_window_mixins.social", "SocialMixin.apply_text_animation_to_clip"): "_clip_edit_refused",
    ("ui.main_window_mixins.social", "SocialMixin.sync_text_on_voice"): "_clip_edit_refused",
    ("ui.main_window_mixins.presets", "PresetsMixin._apply_text_preset_to_clip"): "_clip_edit_refused",
    ("ui.main_window_mixins.presets", "PresetsMixin.on_transition_preset_apply_requested"): "_clip_edit_refused",
    ("ui.main_window_mixins.presets", "PresetsMixin.on_effect_preset_apply_requested"): "_clip_edit_refused",
    ("ui.main_window_mixins.subtitles_graphics", "SubtitlesGraphicsMixin.on_subtitle_content_changed"): "_clip_edit_refused",
    ("ui.main_window_mixins.subtitles_graphics", "SubtitlesGraphicsMixin.on_subtitle_style_changed"): "_clip_edit_refused",
    ("ui.main_window_mixins.subtitles_graphics", "SubtitlesGraphicsMixin.on_subtitle_style_reset"): "_clip_edit_refused",
    ("ui.main_window_mixins.subtitles_graphics", "SubtitlesGraphicsMixin.on_graphic_property_changed"): "_clip_edit_refused",
    ("ui.main_window_mixins.timeline_editing", "TimelineEditingMixin._update_transition"): "_transition_edit_refused",
    ("ui.main_window_mixins.timeline_editing", "TimelineEditingMixin.remove_selected_transition"): "_transition_edit_refused",
}


def _resolve(module_name: str, dotted: str):
    import importlib

    obj = importlib.import_module(module_name)
    for part in dotted.split("."):
        obj = getattr(obj, part)
    return obj


@pytest.mark.parametrize(("entry", "marker"), sorted(GUARDED_UI.items()))
def test_every_listed_ui_entry_still_carries_its_guard(entry, marker):
    module_name, dotted = entry
    assert marker in inspect.getsource(_resolve(module_name, dotted)), f"{dotted} n'appelle plus {marker}"


def test_every_keyframe_mutator_of_the_core_is_guarded():
    unguarded = []
    for name, function in inspect.getmembers(keyframe_editing, inspect.isfunction):
        if function.__module__ != keyframe_editing.__name__ or name.startswith("_") or name in KEYFRAME_READ_ONLY:
            continue
        parameters = list(inspect.signature(function).parameters)
        if not parameters or parameters[0] != "project":
            continue
        source = inspect.getsource(function)
        if "_guard(" not in source and not any(
                f"{other}(" in source for other in ("add_keyframe", "move_keyframes", "remove_keyframes")):
            unguarded.append(name)
    assert unguarded == [], f"mutateurs d'images-clés sans garde : {unguarded}"


def test_every_transform_and_transition_mutator_of_the_core_is_guarded():
    from core import timeline_operations, transitions

    for function, marker in ((timeline_operations._require_video_clip, "require_clip_editable"),
                             (transitions.add_transition, "_require_clips_editable"),
                             (transitions.update_transition, "_require_clips_editable"),
                             (transitions.remove_transition, "_require_clips_editable"),
                             (timeline_operations.add_subtitle_clip, "_ensure_track_editable")):
        assert marker in inspect.getsource(function), function.__name__
