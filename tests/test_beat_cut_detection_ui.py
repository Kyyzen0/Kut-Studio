"""« Couper sur les temps » sans grille, dans la fenêtre : le tempo est mesuré sur une vraie musique, puis le clip
sélectionné est coupé sur ses temps, en une seule étape d'annulation.

La musique est une boucle de batterie synthétique à 120 BPM dont le premier temps tombe à 0,25 s du fichier ; posée à
1,0 s de la timeline, ses temps tombent à 1,25 s + k × 0,5 s. Sans FFmpeg, les tests réels sont sautés.
"""

from __future__ import annotations

import pytest
from multicam_stubs import keep_preview_player_off_the_disk
from render_probe import needs_ffmpeg
from test_beat_detection import drum_loop, write_wav

from core.project_model import Clip, MediaAsset, Project, Track
from ui import i18n


@pytest.fixture
def window(qtbot, monkeypatch):
    from ui.main_window import MainWindow

    i18n.set_language("fr")
    monkeypatch.setattr("ui.main_window.QMessageBox.information", lambda *_, **__: None)
    monkeypatch.setattr("ui.main_window.QMessageBox.critical", lambda *_, **__: None)
    main = MainWindow()
    qtbot.addWidget(main)
    main.timeline_timer.stop()
    keep_preview_player_off_the_disk(main, monkeypatch)
    return main


def _load(window, tmp_path, *, music: bool = True) -> None:
    """Un plan vidéo de 4 s (``c1``, sélectionné) et, si demandé, la musique sur la piste Musique à partir de 1 s."""
    song = tmp_path / "song.wav"
    if music:
        write_wav(song, drum_loop(120.0, 0.25, 12.0))
    assets = [
        MediaAsset("v", str(tmp_path / "plan.mp4"), "Plan", 10.0, 160, 90, 25.0, "video", False),
        MediaAsset("m", str(song), "Song", 12.0, 0, 0, 0.0, "audio", True),
    ]
    video = Track(id="V1", name="V1", type="video", clips=[
        Clip(id="c1", asset_id="v", track_id="V1", timeline_start=0.0, source_in=0.0, source_out=4.0),
    ])
    tracks = [video]
    if music:
        tracks.append(Track(id="A1", name="Musique", type="audio", audio_role="music", clips=[
            Clip(id="m1", asset_id="m", track_id="A1", timeline_start=1.0, source_in=0.0, source_out=10.0),
        ]))
    project = Project(name="Rythme", width=160, height=90, fps=25.0, media_assets=assets, tracks=tracks)
    window.project = project
    window.history.reset(project)
    window.timeline_panel.set_project(project)
    window._update_timeline_duration()
    window.timeline_panel.selected_clip_id = "c1"


def _video_cuts(window) -> list[float]:
    clips = sorted(window.project.tracks[0].clips, key=lambda clip: clip.timeline_start)
    return [clip.timeline_start for clip in clips[1:]]


def test_without_grid_nor_music_nothing_is_measured(window, tmp_path):
    _load(window, tmp_path, music=False)
    window.cut_selection_on_beats(1)
    assert getattr(window, "_tempo_detection", None) is None
    assert window.statusBar().currentMessage() == i18n.translate("beat.message.no_grid_no_music")
    assert len(window.project.tracks[0].clips) == 1


def test_a_retimed_music_is_not_measured(window, tmp_path):
    _load(window, tmp_path)
    window.project.tracks[1].clips[0].sequence_id = "imbriquée"
    window.cut_selection_on_beats(1)
    assert getattr(window, "_tempo_detection", None) is None
    assert window.statusBar().currentMessage() == i18n.translate("beat.message.music_retimed")


def test_the_music_alone_is_not_a_target(window, tmp_path):
    _load(window, tmp_path)
    window.timeline_panel.selected_clip_id = "m1"
    window.cut_selection_on_beats(1)
    assert getattr(window, "_tempo_detection", None) is None
    assert window.statusBar().currentMessage() == i18n.translate("social.message.no_video")


@needs_ffmpeg
def test_the_selected_clip_is_cut_on_the_measured_beats_in_one_undo_step(qtbot, window, tmp_path):
    _load(window, tmp_path)
    window.cut_selection_on_beats(1)
    qtbot.waitUntil(lambda: getattr(window, "_tempo_detection", None) is None, timeout=60000)

    grid = window.project.active_sequence.beat_grid
    assert grid is not None and grid.bpm == pytest.approx(120.0, abs=0.5)
    expected = [0.25 + 0.5 * k for k in range(8)]                   # 0,25 … 3,75 : les temps à l'intérieur du plan
    assert _video_cuts(window) == pytest.approx(expected, abs=0.05)
    assert [clip.id for clip in window.project.tracks[1].clips] == ["m1"]   # la musique n'est pas coupée
    assert window.statusBar().currentMessage().startswith("Tempo mesuré")

    window.undo_last()
    assert len(window.project.tracks[0].clips) == 1
    assert window.project.active_sequence.beat_grid is None          # grille et coupes s'annulent ensemble


@needs_ffmpeg
def test_cut_every_two_beats_and_a_single_measurement_at_a_time(qtbot, window, tmp_path):
    _load(window, tmp_path)
    window.cut_selection_on_beats(2)
    window.cut_selection_on_beats(2)                                 # déjà en cours : refusée
    assert window.statusBar().currentMessage() == i18n.translate("beat.message.detecting")
    qtbot.waitUntil(lambda: getattr(window, "_tempo_detection", None) is None, timeout=60000)
    assert _video_cuts(window) == pytest.approx([0.25, 1.25, 2.25, 3.25], abs=0.05)


@needs_ffmpeg
def test_a_cancelled_measurement_changes_nothing(qtbot, window, tmp_path):
    _load(window, tmp_path)
    window.cut_selection_on_beats(1)
    window._tempo_detection.dialog.canceled.emit()
    qtbot.waitUntil(lambda: getattr(window, "_tempo_detection", None) is None, timeout=60000)
    assert len(window.project.tracks[0].clips) == 1
    assert window.project.active_sequence.beat_grid is None
    assert window.statusBar().currentMessage() == i18n.translate("beat.message.detect_cancelled")


@needs_ffmpeg
def test_a_music_retimed_during_the_measurement_cuts_nothing(qtbot, window, tmp_path):
    from core.time_remapping import TimeRemapping

    _load(window, tmp_path)
    window.cut_selection_on_beats(1)
    window.project.tracks[1].clips[0].time_remapping = TimeRemapping(speed=2.0)   # la boîte n'est pas modale
    qtbot.waitUntil(lambda: getattr(window, "_tempo_detection", None) is None, timeout=60000)
    assert len(window.project.tracks[0].clips) == 1
    assert window.project.active_sequence.beat_grid is None
    assert window.statusBar().currentMessage() == i18n.translate("beat.message.music_retimed")


def test_an_existing_grid_is_used_without_measuring(window, tmp_path):
    from core.beat_grid import BeatGrid

    _load(window, tmp_path, music=False)
    window._set_beat_grid(BeatGrid(60.0, 0.0), "history.beat.set")
    window.timeline_panel.selected_clip_id = "c1"
    window.cut_selection_on_beats(1)
    assert getattr(window, "_tempo_detection", None) is None
    assert _video_cuts(window) == pytest.approx([1.0, 2.0, 3.0])
