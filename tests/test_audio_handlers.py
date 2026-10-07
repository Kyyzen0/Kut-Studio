"""Audio depuis l'interface : mixeur, inspecteur, timeline, effets audio, SFX et voix off.

Couvre la partie **câblée** de ``ui/main_window_mixins/audio.py`` (signaux du mixeur, de l'inspecteur et de la
timeline branchés par ``_connect_audio_controls``, effets audio de l'inspecteur), les chemins de refus de
``social_audio.py`` et la prise de voix off de ``recording.py`` (``timeline_panel.record_requested``), avec une vraie
``AudioRecorder`` sur une source synthétique.

Volontairement **hors** de ces tests : ``on_track_role_changed``, ``on_track_automation_point_added/removed/updated``,
``on_ducking_sidechain_added/removed`` et ``on_ducking_config_changed``. Aucun signal ne les déclenche
(docs/dead-code-audit.md) : les tester gonflerait la couverture sans rien garantir à l'utilisateur. Leur sort (câblage
ou retrait) se décide à part.
"""

from __future__ import annotations

import json

import pytest
from main_window_harness import build_window, install_dialogs, track

from core.audio_recorder import AudioRecorder, ToneSource
from core.project_model import MediaAsset
from core.timeline_operations import add_clip_to_track, find_clip
from ui import i18n


@pytest.fixture
def dialogs(monkeypatch):
    return install_dialogs(monkeypatch)


@pytest.fixture
def config_dir(tmp_path):
    return tmp_path / "config"


@pytest.fixture
def window(qtbot, monkeypatch, config_dir, dialogs):
    return build_window(qtbot, monkeypatch, config_dir)


@pytest.fixture
def voice(window, tmp_path):
    """Un clip audio de 4 s sur A1 (à 1 s), sélectionné comme à la souris ; historique remis à zéro."""
    media = tmp_path / "voix.wav"
    media.write_bytes(b"\x00" * 64)
    window.project.media_assets.append(MediaAsset(
        id="voice-asset", path=str(media), name="voix", duration=4.0, width=0, height=0, fps=0.0,
        media_type="audio", has_audio=True))
    clip = add_clip_to_track(window.project, "voice-asset", "A1", 1.0)
    window._reload_timeline_preserving_selection()
    window.history.reset(window.project)
    window.timeline_panel.select_clip(clip.id)
    return clip.id


def _steps(window) -> int:
    return len(window.history._undo_stack)


def _settings(config_dir) -> dict:
    return json.loads((config_dir / "user_settings.json").read_text(encoding="utf-8"))


# --- mixeur : pistes ----------------------------------------------------------------------------------------------


def test_a_fader_gesture_is_one_undo_step_whatever_the_number_of_moves(window):
    for value in (-1.0, -2.5, -4.0, -6.0):
        window.mixer_panel.volume_changed.emit("A1", value)

    assert track(window, "A1").volume_db == pytest.approx(-6.0)
    assert window.history.undo_label == i18n.translate("mixer.volume")
    assert _steps(window) == 2, "état initial + un seul geste"
    window.undo_last()
    assert track(window, "A1").volume_db == pytest.approx(0.0)


def test_volume_and_pan_are_clamped_to_the_mixer_range(window):
    window.mixer_panel.volume_changed.emit("A1", 40.0)
    window.mixer_panel.pan_changed.emit("A1", -5.0)

    assert track(window, "A1").volume_db == pytest.approx(12.0)
    assert track(window, "A1").pan == pytest.approx(-1.0)
    assert window.history.undo_label == i18n.translate("mixer.pan")


def test_a_locked_track_ignores_the_mixer_and_the_strip_shows_the_real_values(window):
    window.timeline_panel.toggle_track_lock_requested.emit("A1", True)
    steps = _steps(window)

    window.mixer_panel.volume_changed.emit("A1", -10.0)
    window.mixer_panel.pan_changed.emit("A1", 0.5)
    window.mixer_panel.mute_toggled.emit("A1", True)
    window.mixer_panel.solo_toggled.emit("A1", True)
    window.mixer_panel.arm_toggled.emit("A1", True)
    window.mixer_panel.reset_requested.emit("A1")

    a1 = track(window, "A1")
    assert (a1.volume_db, a1.pan, a1.muted, a1.solo, a1.armed) == (0.0, 0.0, False, False, False)
    assert _steps(window) == steps
    strip = window.mixer_panel.strip_for("A1")
    assert not strip.mute_button.isChecked() and not strip.solo_button.isChecked()


def test_the_mixer_only_drives_audio_tracks(window):
    window.mixer_panel.volume_changed.emit("V1", -10.0)
    window.mixer_panel.mute_toggled.emit("V1", True)

    assert track(window, "V1").muted is False
    assert not window.history.can_undo


@pytest.mark.parametrize(("signal", "attribute", "label_key"), [
    ("mute_toggled", "muted", "mixer.mute"),
    ("solo_toggled", "solo", "mixer.solo"),
    ("arm_toggled", "armed", "mixer.arm"),
])
def test_mixer_buttons_change_the_track_and_are_undoable(window, signal, attribute, label_key):
    getattr(window.mixer_panel, signal).emit("A1", True)

    assert getattr(track(window, "A1"), attribute) is True
    assert window.history.undo_label == i18n.translate(label_key)
    window.undo_last()
    assert getattr(track(window, "A1"), attribute) is False


def test_resetting_a_strip_restores_unity_gain_and_centre_in_one_undoable_step(window):
    window.mixer_panel.volume_changed.emit("A1", -9.0)
    window.mixer_panel.pan_changed.emit("A1", 0.4)

    window.mixer_panel.reset_requested.emit("A1")

    assert (track(window, "A1").volume_db, track(window, "A1").pan) == (0.0, 0.0)
    assert window.history.undo_label == i18n.translate("mixer.reset")
    window.undo_last()
    assert track(window, "A1").volume_db == pytest.approx(-9.0) and track(window, "A1").pan == pytest.approx(0.4)


# --- mixeur : Master (préférence, pas projet) ---------------------------------------------------------------------


def test_the_master_is_a_user_preference_never_an_edit_of_the_project(window, config_dir):
    window.mixer_panel.master_volume_changed.emit(-4.5)
    window.mixer_panel.master_mute_toggled.emit(True)

    saved = _settings(config_dir)
    assert (saved["master_gain_db"], saved["master_muted"]) == (-4.5, True)
    assert not window.history.can_undo, "le Master n'entre pas dans l'historique du projet"
    assert not window.project_dirty

    window.mixer_panel.master_reset_requested.emit()

    saved = _settings(config_dir)
    assert (saved["master_gain_db"], saved["master_muted"]) == (0.0, False)
    assert (window._master_gain_db, window._master_muted) == (0.0, False)


# --- inspecteur et timeline : clip audio --------------------------------------------------------------------------


def test_clip_gain_and_pan_from_the_inspector_follow_the_selection(window, voice):
    window.properties_panel.audio_gain_changed.emit(-3.0)
    window.properties_panel.audio_gain_changed.emit(-5.0)
    window.properties_panel.audio_pan_changed.emit(-0.25)

    clip = find_clip(window.project, voice)
    assert clip.gain_db == pytest.approx(-5.0) and clip.pan == pytest.approx(-0.25)
    assert _steps(window) == 3, "un geste de gain (fusionné) + un geste de pan"
    window.undo_last()
    window.undo_last()
    assert find_clip(window.project, voice).gain_db == pytest.approx(0.0)


def test_the_inspector_does_nothing_without_a_selected_clip(window):
    window.properties_panel.audio_gain_changed.emit(-3.0)
    window.properties_panel.audio_fade_changed.emit("in", 1.0)

    assert not window.history.can_undo


def test_fades_never_overlap_and_are_reset_from_the_timeline(window, voice):
    window.properties_panel.audio_fade_changed.emit("in", 3.0)
    window.timeline_panel.fade_changed_requested.emit(voice, "out", 3.0)

    clip = find_clip(window.project, voice)
    assert clip.fade_in == pytest.approx(3.0)
    assert clip.fade_out == pytest.approx(1.0), "le fondu de sortie s'arrête où commence celui d'entrée (clip de 4 s)"
    assert window.history.undo_label == i18n.translate("audio.fade_out")

    window.timeline_panel.reset_clip_fades_requested.emit(voice)

    clip = find_clip(window.project, voice)
    assert (clip.fade_in, clip.fade_out) == (0.0, 0.0)
    assert window.history.undo_label == i18n.translate("audio.reset_fades")
    window.undo_last()
    assert find_clip(window.project, voice).fade_in == pytest.approx(3.0)


def test_the_inspector_reset_button_clears_the_selected_clip_fades(window, voice):
    window.properties_panel.audio_fade_changed.emit("out", 1.5)

    window.properties_panel.audio_fades_reset.emit()

    assert find_clip(window.project, voice).fade_out == 0.0


def test_a_locked_track_keeps_its_clip_gain_and_fades(window, voice):
    window.timeline_panel.toggle_track_lock_requested.emit("A1", True)
    steps = _steps(window)

    window.properties_panel.audio_gain_changed.emit(-8.0)
    window.timeline_panel.fade_changed_requested.emit(voice, "in", 1.0)
    window.timeline_panel.reset_clip_fades_requested.emit(voice)

    clip = find_clip(window.project, voice)
    assert (clip.gain_db, clip.fade_in) == (0.0, 0.0)
    assert _steps(window) == steps


# --- effets audio de l'inspecteur ---------------------------------------------------------------------------------


def test_audio_effects_are_added_reordered_toggled_tuned_and_removed(window, voice):
    panel = window.properties_panel
    panel.audio_effect_add_requested.emit(voice, "compressor")
    panel.audio_effect_add_requested.emit(voice, "limiter")
    first, second = (effect.id for effect in find_clip(window.project, voice).audio_effects)
    assert window.history.undo_label == i18n.translate("history.audio.effect_add")

    panel.audio_effect_moved.emit(voice, second, -1)
    assert [effect.id for effect in find_clip(window.project, voice).audio_effects] == [second, first]
    assert window.history.undo_label == i18n.translate("history.audio.effect_move")

    panel.audio_effect_enabled_changed.emit(voice, first, False)
    assert next(e for e in find_clip(window.project, voice).audio_effects if e.id == first).enabled is False

    panel.audio_effect_parameter_changed.emit(voice, first, "threshold_db", -6.0)
    tuned = next(e for e in find_clip(window.project, voice).audio_effects if e.id == first)
    assert tuned.params["threshold_db"] == pytest.approx(-6.0)
    assert window.history.undo_label == i18n.translate("history.audio.effect_edit")

    panel.audio_effect_removed.emit(voice, second)
    assert [effect.id for effect in find_clip(window.project, voice).audio_effects] == [first]
    assert window.history.undo_label == i18n.translate("history.audio.effect_remove")
    window.undo_last()
    assert len(find_clip(window.project, voice).audio_effects) == 2


def test_an_unknown_audio_effect_type_is_refused_with_a_message(window, voice, dialogs):
    window.properties_panel.audio_effect_add_requested.emit(voice, "flanger-9000")

    assert find_clip(window.project, voice).audio_effects == []
    assert len(dialogs.of_kind("warning")) == 1
    assert not window.history.can_undo


def test_an_out_of_range_effect_parameter_is_refused_and_the_value_kept(window, voice):
    window.properties_panel.audio_effect_add_requested.emit(voice, "compressor")
    [effect] = find_clip(window.project, voice).audio_effects
    before = float(effect.params["threshold_db"])     # copie du scalaire : un dict muté sur place passerait sinon
    steps = _steps(window)

    window.properties_panel.audio_effect_parameter_changed.emit(voice, effect.id, "threshold_db", 2.0)

    [kept] = find_clip(window.project, voice).audio_effects
    assert kept.params["threshold_db"] == pytest.approx(before)
    assert effect.params["threshold_db"] == pytest.approx(before), "l'effet d'origine n'est pas muté non plus"
    assert "threshold_db" in window.statusBar().currentMessage()
    assert _steps(window) == steps


def test_editing_a_missing_audio_effect_is_reported_in_the_status_bar(window, voice):
    for emit in (
        lambda: window.properties_panel.audio_effect_removed.emit(voice, "ghost"),
        lambda: window.properties_panel.audio_effect_moved.emit(voice, "ghost", 1),
        lambda: window.properties_panel.audio_effect_enabled_changed.emit(voice, "ghost", False),
        lambda: window.properties_panel.audio_effect_parameter_changed.emit(voice, "ghost", "gain", 1.0),
    ):
        window.statusBar().clearMessage()
        emit()
        assert window.statusBar().currentMessage(), "chaque refus est dit dans la barre d'état"
    assert not window.history.can_undo


# --- SFX : refus ----------------------------------------------------------------------------------------------------


def test_sfx_on_cuts_without_a_video_track_says_so(window):
    for item in window.project.tracks:
        item.clips.clear()
    window.project.tracks[:] = [item for item in window.project.tracks if item.type != "video"]
    window._reload_timeline_preserving_selection()
    window.history.reset(window.project)

    window.project_panel.sfx_on_cuts_requested.emit(["whoosh"])

    assert window.statusBar().currentMessage() == i18n.translate("social.message.no_video")
    assert not window.history.can_undo


def test_an_unknown_sfx_is_refused_and_nothing_is_placed(window):
    find_clip(window.project, "plan_a").timeline_start = 4.0          # un cut à 4 s sur V1
    window._reload_timeline_preserving_selection()
    window.history.reset(window.project)
    clips_before = sum(len(item.clips) for item in window.project.tracks)

    window.project_panel.sfx_on_cuts_requested.emit(["whoosh", "not-a-sound"])

    assert "not-a-sound" in window.statusBar().currentMessage()
    assert sum(len(item.clips) for item in window.project.tracks) == clips_before
    assert not window.history.can_undo


# --- voix off -------------------------------------------------------------------------------------------------------


@pytest.fixture
def microphone(window, tmp_path):
    """Microphone synthétique (sinus) ; le projet a un chemin, la prise s'écrit donc à côté, dans ``tmp_path``."""
    window._audio_recorder = AudioRecorder(ToneSource(sample_rate=48000, channels=1))
    window.current_project_path = str(tmp_path / "projet.kut")
    return window._audio_recorder


def test_recording_without_an_armed_track_explains_and_releases_the_button(window, microphone, dialogs):
    window.timeline_panel.record_button.setChecked(True)

    assert [text for _t, text in dialogs.of_kind("information")] == [i18n.translate("dialog.recording.arm_first")]
    assert not microphone.is_recording
    assert not window.timeline_panel.record_button.isChecked()


def test_a_voice_take_lands_on_the_armed_track_at_the_playhead_and_is_undoable(window, microphone, qtbot, tmp_path):
    window.mixer_panel.arm_toggled.emit("A1", True)
    window.playhead_seconds = 2.0

    window.timeline_panel.record_requested.emit(True)
    assert microphone.is_recording
    qtbot.wait(300)
    window.timeline_panel.record_requested.emit(False)

    [clip] = track(window, "A1").clips
    asset = next(item for item in window.project.media_assets if item.id == clip.asset_id)
    assert clip.timeline_start == pytest.approx(2.0)
    assert asset.media_type == "audio" and asset.duration > 0.05
    takes = list((tmp_path / "enregistrements").glob("prise-*.wav"))
    assert [str(take) for take in takes] == [asset.path]
    assert takes[0].read_bytes()[:4] == b"RIFF"
    assert window.history.undo_label == i18n.translate("history.recording.take")
    window.undo_last()
    assert track(window, "A1").clips == []


def test_a_take_shorter_than_50_ms_is_refused(window, microphone, dialogs, tmp_path):
    window.mixer_panel.arm_toggled.emit("A1", True)
    steps = _steps(window)

    window.timeline_panel.record_requested.emit(True)
    window.timeline_panel.record_requested.emit(False)

    assert track(window, "A1").clips == []
    assert [text for _t, text in dialogs.of_kind("information")] == [i18n.translate("dialog.recording.too_short")]
    assert not (tmp_path / "enregistrements").exists()
    assert _steps(window) == steps


def test_a_microphone_that_cannot_start_is_reported(window, dialogs):
    class DeadSource:
        sample_rate = 48000
        channels = 1

        def start(self):
            return None

        def stop(self):
            return None

    window._audio_recorder = AudioRecorder(DeadSource())
    window.mixer_panel.arm_toggled.emit("A1", True)
    window.timeline_panel.record_button.setChecked(True)

    assert len(dialogs.of_kind("critical")) == 1
    assert not window._audio_recorder.is_recording
    assert not window.timeline_panel.record_button.isChecked()
