"""Sous-titres automatiques dans la fenêtre : menu, transcription sur la file d'analyse, une entrée d'historique,
préférences de whisper.cpp.

Le déroulé tourne avec FFmpeg réel et un faux ``whisper-cli`` (script qui écrit la sortie de référence de whisper.cpp
1.9.4) : la voix, posée à 1 s de la timeline et lue depuis 0,5 s du média, place « Bonjour » à 1,01 s.
"""

from __future__ import annotations

import json
import subprocess

import pytest
from multicam_stubs import keep_preview_player_off_the_disk
from render_probe import needs_ffmpeg
from test_transcription import WHISPER_OUTPUT, _fake_whisper, posix_only

from core.project_model import Clip, MediaAsset, Project, Track
from core.user_settings import UserSettings, load_user_settings, save_user_settings
from ui import i18n


@pytest.fixture
def window(qtbot, monkeypatch, tmp_path):
    from ui.main_window import MainWindow

    i18n.set_language("fr")
    shown: list[str] = []
    monkeypatch.setattr("ui.main_window.QMessageBox.information", lambda _parent, _title, text, *_a, **_k: shown.append(text))
    monkeypatch.setattr("ui.main_window.QMessageBox.critical", lambda *_, **__: None)
    main = MainWindow()
    qtbot.addWidget(main)
    main.timeline_timer.stop()
    keep_preview_player_off_the_disk(main, monkeypatch)
    main.shown_messages = shown
    return main


def _load(window, tmp_path) -> None:
    voice = tmp_path / "voix.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=180:duration=5", str(voice)],
                   check=True, timeout=60)
    asset = MediaAsset(id="a", path=str(voice), name="voix", duration=5.0, width=0, height=0, fps=0.0,
                       media_type="audio", has_audio=True)
    clip = Clip(id="v", asset_id="a", track_id="A1", timeline_start=1.0, source_in=0.5, source_out=4.5)
    project = Project(name="Voix", width=1080, height=1920, fps=30.0, media_assets=[asset],
                      tracks=[Track(id="A1", name="Voix", type="audio", audio_role="voice", clips=[clip])])
    window.project = project
    window.history.reset(project)
    window.timeline_panel.set_project(project)
    window._update_timeline_duration()


def _whisper_ready(monkeypatch, tmp_path) -> None:
    output = json.dumps(WHISPER_OUTPUT).replace('"', '\\"')
    whisper = _fake_whisper(tmp_path, f"""
        while [ $# -gt 0 ]; do case "$1" in -of) prefix="$2"; shift;; esac; shift; done
        printf "%s" "{output}" > "$prefix.json"
        echo "whisper_print_progress_callback: progress = 100%" >&2
    """)
    model = tmp_path / "ggml-test.bin"
    model.write_bytes(b"0")
    monkeypatch.setenv("KUT_STUDIO_WHISPER", whisper)
    monkeypatch.setenv("KUT_STUDIO_WHISPER_MODEL", str(model))


def _tracks_of(window, track_type: str) -> list[Track]:
    return [track for track in window.project.tracks if track.type == track_type and track.clips]


def test_the_commands_sit_in_the_social_menu(window):
    submenu = next(action.menu() for action in window.social_menu.actions()
                   if action.text() == i18n.translate("transcription.menu.title"))
    labels = [action.text() for action in submenu.actions()]
    assert labels == [i18n.translate("transcription.menu.subtitles"), i18n.translate("transcription.menu.karaoke")]


@needs_ffmpeg
@posix_only
def test_the_voice_becomes_subtitles_in_one_undo_step(qtbot, window, monkeypatch, tmp_path):
    _whisper_ready(monkeypatch, tmp_path)
    _load(window, tmp_path)
    window.transcribe_voice("subtitles")
    qtbot.waitUntil(lambda: window._transcription is None, timeout=60000)
    (track,) = _tracks_of(window, "subtitle")
    clips = sorted(track.clips, key=lambda clip: clip.timeline_start)
    assert [clip.text for clip in clips] == ["Bonjour à tous, bienvenue dans", "Studio."]
    assert clips[0].timeline_start == pytest.approx(1.01)
    assert window.statusBar().currentMessage() == i18n.translate("transcription.message.done", count=2, code="fr")
    window.undo_last()
    assert _tracks_of(window, "subtitle") == []


@needs_ffmpeg
@posix_only
def test_the_voice_becomes_karaoke_titles(qtbot, window, monkeypatch, tmp_path):
    _whisper_ready(monkeypatch, tmp_path)
    _load(window, tmp_path)
    window.transcribe_voice("karaoke")
    window.transcribe_voice("karaoke")                                   # déjà en cours : refusée
    assert window.statusBar().currentMessage() == i18n.translate("transcription.message.running")
    qtbot.waitUntil(lambda: window._transcription is None, timeout=60000)
    (track,) = _tracks_of(window, "graphics")
    first = min(track.clips, key=lambda clip: clip.timeline_start)
    assert first.graphic.word_reveal == "karaoke"
    assert first.graphic.word_times == pytest.approx((0.0, 0.67, 0.68, 1.32, 2.29))   # « dans » après « [Musique] »


@needs_ffmpeg
@posix_only
@pytest.mark.parametrize("edit", ["trim", "retime"])
def test_a_voice_edited_during_the_transcription_gets_no_stale_lines(qtbot, window, monkeypatch, tmp_path, edit):
    from core.time_remapping import TimeRemapping

    _whisper_ready(monkeypatch, tmp_path)
    _load(window, tmp_path)
    window.transcribe_voice("subtitles")
    voice = window.project.tracks[0].clips[0]
    if edit == "trim":                                                # la boîte n'est pas modale : on rogne la voix
        voice.source_out = 2.0
    else:
        voice.time_remapping = TimeRemapping(speed=2.0)
    qtbot.waitUntil(lambda: window._transcription is None, timeout=60000)
    assert _tracks_of(window, "subtitle") == []
    assert window.statusBar().currentMessage() == i18n.translate("transcription.message.voice_changed")


@needs_ffmpeg
def test_without_whisper_the_user_is_told_how_to_install_it(window, monkeypatch, tmp_path):
    _load(window, tmp_path)
    monkeypatch.setattr("ui.main_window_mixins.auto_captions.find_whisper", lambda *_a, **_k: None)
    monkeypatch.setattr("ui.main_window_mixins.auto_captions.find_whisper_model", lambda *_a, **_k: None)
    window.transcribe_voice("subtitles")
    assert window._transcription is None
    (text,) = window.shown_messages
    assert "brew install whisper-cpp" in text and "ggml-base.bin" in text


def test_without_voice_nothing_is_transcribed(window):
    window.transcribe_voice("subtitles")
    assert window._transcription is None
    assert window.statusBar().currentMessage() == i18n.translate("transcription.message.no_voice")


def test_whisper_settings_round_trip_and_survive_a_reset(window, tmp_path):
    window.set_whisper_path("/opt/whisper/whisper-cli")
    window.set_whisper_model(str(tmp_path / "ggml-small.bin"))
    snapshot = window._settings_snapshot()
    assert (snapshot.whisper_path, snapshot.whisper_model) == ("/opt/whisper/whisper-cli", str(tmp_path / "ggml-small.bin"))
    save_user_settings(snapshot, tmp_path)
    assert load_user_settings(tmp_path).whisper_model == str(tmp_path / "ggml-small.bin")
    window._restore_default_preferences()
    assert window._settings_snapshot().whisper_path == "/opt/whisper/whisper-cli"


def test_odd_whisper_settings_fall_back_to_automatic(tmp_path):
    (tmp_path / "user_settings.json").write_text(json.dumps({"whisper_path": 3, "whisper_model": "  m.bin "}))
    loaded = load_user_settings(tmp_path)
    assert (loaded.whisper_path, loaded.whisper_model) == ("", "m.bin")
    assert UserSettings().whisper_path == ""


def test_the_preferences_group_emits_and_reports(qtbot, tmp_path, monkeypatch):
    from ui.preferences_dialog import PreferencesDialog

    monkeypatch.setenv("KUT_STUDIO_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("ui.transcription_settings.find_whisper", lambda *_a, **_k: None)
    dialog = PreferencesDialog(current_whisper_model="")
    qtbot.addWidget(dialog)
    group = dialog.transcription_box
    assert "brew install whisper-cpp" in group.status.text()
    model = tmp_path / "ggml-base.bin"
    model.write_bytes(b"0")
    with qtbot.waitSignal(dialog.whisper_model_changed) as emitted:
        group.fields["model"].setText(str(model))
        group.fields["model"].editingFinished.emit()
    assert emitted.args == [str(model)]
    assert str(model) in group.status.text()
    with qtbot.waitSignal(dialog.whisper_model_changed) as cleared:
        group.auto_buttons["model"].click()
    assert cleared.args == [""]
