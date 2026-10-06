"""Interface de l'audio social : onglet SFX du panneau projet, SFX sur chaque cut, presets de ducking."""

from __future__ import annotations

import pytest

from core.audio_automation import DUCKING_PRESETS
from core.project_model import Clip, MediaAsset, Project, Track
from core.sfx_synth import CATALOG, SFX_SPECS
from ui import i18n


@pytest.fixture
def window(qtbot, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QMessageBox

    from ui.main_window import MainWindow

    monkeypatch.setattr("ui.main_window.QMessageBox.question", lambda *_a, **_k: QMessageBox.Yes)
    monkeypatch.setattr("ui.main_window.QMessageBox.warning", lambda *_a, **_k: QMessageBox.Discard)
    win = MainWindow()
    qtbot.addWidget(win)
    win.timeline_timer.stop()
    monkeypatch.setattr(win.preview_panel, "preview_at", lambda *_a, **_k: None)
    assets = [MediaAsset(id="a", path=str(tmp_path / "m.mp4"), name="m", duration=10.0, width=1920, height=1080,
                         fps=30.0, media_type="video"),
              MediaAsset(id="mus", path=str(tmp_path / "mus.wav"), name="mus", duration=20.0, width=0, height=0,
                         fps=0.0, media_type="audio", has_audio=True)]
    clips = [Clip(id=f"c{n}", asset_id="a", track_id="V1", timeline_start=2.0 * n, source_in=0.0, source_out=2.0)
             for n in range(3)]
    win.project = Project(name="s", width=1080, height=1920, fps=30.0, media_assets=assets,
                          tracks=[Track(id="V1", name="V1", type="video", clips=clips),
                                  Track(id="A1", name="A1", type="audio", audio_role="music")])
    win._reload_timeline_preserving_selection()
    win.history.reset(win.project)
    win._mark_clean()
    return win


def _sfx_clips(project):
    return [clip for track in project.tracks if track.type == "audio" and track.audio_role == "sfx"
            for clip in track.clips]


def test_the_sfx_tab_lists_the_whole_library(window):
    panel = window.project_panel
    panel.set_audio_mode("sfx")
    assert panel.content_stack.currentWidget() is panel.sfx_view
    assert panel.audio_mode_row.isVisibleTo(panel)
    assert panel.sfx_view.list.count() == len(CATALOG)
    assert str(len(CATALOG)) in panel.media_count.text()
    names = {panel.sfx_view.list.item(i).text() for i in range(panel.sfx_view.list.count())}
    assert i18n.translate("sfx.name.passby_1") in names and not any(name.startswith("sfx.") for name in names)
    panel.set_audio_mode("files")
    assert panel.content_stack.currentWidget() is panel.bin_audios


def test_adding_an_sfx_lands_its_anchor_on_the_playhead_and_undoes_in_one_step(window):
    window.playhead_seconds = 3.0
    window.project_panel.sfx_view.add_requested.emit("whoosh")
    (clip,) = _sfx_clips(window.project)
    assert clip.timeline_start == pytest.approx(3.0 - SFX_SPECS["whoosh"].anchor)
    assert any(asset.id == clip.asset_id for asset in window.project.media_assets)
    assert window.project_panel.bin_audios.count() == 2            # le SFX rejoint les médias audio du projet
    window.undo_last()
    assert not _sfx_clips(window.project)
    assert not any(track.audio_role == "sfx" for track in window.project.tracks)


def test_sfx_on_every_cut_alternate_the_selected_sounds(window):
    view = window.project_panel.sfx_view
    view.list.clearSelection()
    view.cuts_button.click()                                        # rien de sélectionné : des whooshes
    clips = sorted(_sfx_clips(window.project), key=lambda clip: clip.timeline_start)
    assert len(clips) == 2                                          # trois plans bord à bord : deux cuts
    assert [clip.asset_id for clip in clips] == ["sfx-whoosh", "sfx-whoosh_long"]
    window.undo_last()
    assert not _sfx_clips(window.project)


def test_sfx_on_cuts_reports_a_track_without_cuts(window):
    window.project.tracks[0].clips[1].timeline_start = 2.5          # un trou : plus de cut entre c0 et c1
    window.project.tracks[0].clips[2].timeline_start = 5.0
    window.place_sfx_on_cuts_of_track(["impact"])
    assert not _sfx_clips(window.project)
    assert window.statusBar().currentMessage() == i18n.translate("impact.message.no_cut")


def test_the_social_menu_offers_the_audio_actions(window):
    labels = [action.text() for action in window.social_menu.actions()]
    audio = next(action.menu() for action in window.social_menu.actions()
                 if action.text() == i18n.translate("social.menu.audio"))
    texts = [action.text() for action in audio.actions()]
    assert i18n.translate("sfx.menu.on_cuts") in texts and i18n.translate("social.menu.audio") in labels
    ducking = next(action.menu() for action in audio.actions() if action.menu() is not None)
    assert [action.text() for action in ducking.actions()] == [
        i18n.translate(f"ducking.preset.{preset}") for preset in DUCKING_PRESETS]


def test_ducking_needs_a_voice_track(window):
    window.duck_music_under_voice("social_punchy")
    assert not window.project.ducking_sidechains
    assert window.statusBar().currentMessage() == i18n.translate("ducking.message.no_roles")


def test_a_ducking_preset_is_one_undo_step(window):
    window.project.tracks.append(Track(id="A2", name="A2", type="audio", audio_role="voice"))
    window.history.reset(window.project)
    window.duck_music_under_voice("social_punchy")
    (sidechain,) = window.project.ducking_sidechains
    assert (sidechain.music_track_id, sidechain.voice_track_id) == ("A1", "A2")
    assert sidechain.config == DUCKING_PRESETS["social_punchy"]
    window.duck_music_under_voice("gentle")                         # même paire : le réglage change, pas de doublon
    assert [item.config for item in window.project.ducking_sidechains] == [DUCKING_PRESETS["gentle"]]
    window.undo_last()
    assert window.project.ducking_sidechains[0].config == DUCKING_PRESETS["social_punchy"]
