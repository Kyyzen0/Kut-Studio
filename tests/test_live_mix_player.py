"""Lecteur du mixage en direct : calage sur la tête de lecture, son envoyé à la sortie, vumètres, repli sur erreur.

La sortie audio est simulée (les machines de CI n'ont pas de carte son) : elle garde tout ce qu'on lui envoie. Le flux,
lui, vient du vrai FFmpeg.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from audio_scenes import write_wav
from render_probe import needs_ffmpeg

from core.live_mix import SAMPLE_RATE, LiveMixCommand, build_live_mix_command
from core.project_model import Clip, MediaAsset, Project, Track
from ui.live_mix_player import SINK_BUFFER_SECONDS, LiveMixPlayer


class FakeSink:
    """Une sortie qui consomme tout aussitôt : ``bytesFree`` vaut toujours son tampon."""

    def __init__(self, sample_format: str) -> None:
        self.sample_format = sample_format
        self.data = bytearray()
        self.size = 0
        self.volume = 1.0
        self.stopped = False

    def setBufferSize(self, size: int) -> None:
        self.size = size

    def bufferSize(self) -> int:
        return self.size

    def bytesFree(self) -> int:
        return self.size

    def setVolume(self, volume: float) -> None:
        self.volume = volume

    def start(self):
        return self

    def write(self, data: bytes) -> int:
        self.data += data
        return len(data)

    def processedUSecs(self) -> int:
        """Tout ce qu'elle a reçu est joué (elle consomme aussitôt)."""
        return int(len(self.data) / 8 * 1_000_000 / SAMPLE_RATE)

    def stop(self) -> None:
        self.stopped = True


def _ramp_project(tmp_path) -> Project:
    """Un son dont chaque échantillon dit son instant : on lit dans la sortie où le calage est tombé."""
    seconds = 12.0
    t = np.arange(int(seconds * SAMPLE_RATE)) / SAMPLE_RATE
    left = 0.5 * np.sin(2 * math.pi * 440.0 * t)
    right = 0.3 * np.sin(2 * math.pi * 330.0 * t)
    path = write_wav(tmp_path / "tone.wav", np.stack([left, right], axis=1), rate=SAMPLE_RATE)
    asset = MediaAsset("tone", str(path), "tone", seconds, 0, 0, 0.0, "audio", has_audio=True)
    return Project("p", width=64, height=36, fps=25.0, media_assets=[asset],
                   tracks=[Track("A1", "A1", "audio", clips=[Clip("t", "tone", "A1", 0.0, 0.0, seconds)])])


def _player(qtbot, sinks):
    def factory(sample_format):
        sink = FakeSink(sample_format)
        sinks.append(sink)
        return sink

    player = LiveMixPlayer(sink_factory=factory, float_supported=lambda: True)
    return player


@needs_ffmpeg
def test_the_first_sample_heard_is_the_playhead_and_the_meters_follow(qtbot, tmp_path):
    project = _ramp_project(tmp_path)
    start = 6.0
    live = build_live_mix_command(project, start)
    sinks: list[FakeSink] = []
    player = _player(qtbot, sinks)
    player.set_volume(0.4)
    player.start(live, clock=lambda: start)                # tête de lecture immobile : le calage ne saute que la latence
    try:
        qtbot.waitUntil(lambda: bool(sinks) and len(sinks[0].data) >= 8 * SAMPLE_RATE // 2, timeout=10000)
        sink = sinks[0]
        assert sink.sample_format == "float" and sink.volume == pytest.approx(0.4)
        heard = np.frombuffer(bytes(sink.data), dtype="<f4").reshape(-1, 2)
        first = int(round((start + SINK_BUFFER_SECONDS) * SAMPLE_RATE))
        t = (first + np.arange(len(heard))) / SAMPLE_RATE
        expected = np.stack([0.5 * np.sin(2 * math.pi * 440.0 * t), 0.3 * np.sin(2 * math.pi * 330.0 * t)], axis=1)
        assert np.abs(heard - expected).max() < 2e-4, "le bon échantillon, au bon instant"
        assert player.heard_time == pytest.approx(start + len(heard) / SAMPLE_RATE, abs=1e-3), \
            "joué depuis le calage, moins la latence que le calage avait comptée"
        reading = player.meter()
        assert reading is not None and reading.time <= player.heard_time
        assert reading.peak_db == pytest.approx(20 * math.log10(0.5), abs=0.1)
        assert reading.track_peaks_db == (pytest.approx(20 * math.log10(0.5), abs=0.1),)
    finally:
        player.stop()
    assert sinks[0].stopped and not player.active


@needs_ffmpeg
def test_a_stream_that_fails_is_reported_and_stops(qtbot):
    player = _player(qtbot, [])
    broken = LiveMixCommand(command=("ffmpeg", "-v", "error", "-f", "lavfi", "-i", "nosuchsource", "-f", "f32le", "-"),
                            start=0.0, preroll_frames=0, track_ids=())
    with qtbot.waitSignal(player.failed, timeout=10000) as blocker:
        player.start(broken, clock=lambda: 0.0)
    assert blocker.args[0] and not player.active


# --- fenêtre -----------------------------------------------------------------------------------------------------------


@pytest.fixture
def window(qtbot, monkeypatch, tmp_path):
    from main_window_harness import build_window, install_dialogs

    monkeypatch.setenv("KUT_STUDIO_LIVE_MIX", "on")
    monkeypatch.setattr("ui.live_mix_player._qt_sink", lambda sample_format: FakeSink(sample_format))
    monkeypatch.setattr("ui.live_mix_player._qt_float_supported", lambda: True)
    install_dialogs(monkeypatch)
    win = build_window(qtbot, monkeypatch, tmp_path / "config")
    win._live_mix_output = True                            # une sortie audio, même en CI
    return win


def test_playback_plays_the_mix_instead_of_the_monitor_sound_and_pause_gives_it_back(window, monkeypatch):
    started = []
    monkeypatch.setattr("ui.live_mix_player.LiveMixPlayer.start",
                        lambda self, live, clock: (started.append(live), setattr(self, "_session", object())))
    monkeypatch.setattr("ui.live_mix_player.LiveMixPlayer.stop", lambda self: setattr(self, "_session", None))
    window.toggle_play()
    try:
        assert started and window.live_mix_active
        assert started[0].start == pytest.approx(window.playhead_seconds, abs=0.1)
        assert window.preview_panel.audio_output.isMuted(), "le mixage contient déjà le son du moniteur"
        window.update_volume(30)
        assert window._live_mix._volume == pytest.approx(0.3)
    finally:
        window.toggle_play()                               # pause
    assert not window.live_mix_active and not window.preview_panel.audio_output.isMuted()


def test_an_edit_during_playback_restarts_the_mix_once_settled(window, monkeypatch):
    started = []
    monkeypatch.setattr("ui.live_mix_player.LiveMixPlayer.start",
                        lambda self, live, clock: (started.append(live), setattr(self, "_session", object())))
    monkeypatch.setattr("ui.live_mix_player.LiveMixPlayer.stop", lambda self: setattr(self, "_session", None))
    monkeypatch.setattr("ui.main_window_mixins.live_mix.RESTART_SETTLE_SECONDS", 0.0)
    window.toggle_play()
    try:
        window._sync_live_mix()
        assert len(started) == 1, "rien n'a changé : le flux continue"
        window._master_gain_db = -6.0                      # le Master change (comme un fader qu'on lâche)
        window._sync_live_mix()                            # changement vu : relance en attente
        window._sync_live_mix()                            # resté tel quel : relance
        assert len(started) == 2
    finally:
        window.toggle_play()


def test_a_failed_mix_gives_the_monitor_its_sound_back_without_retrying(window, monkeypatch):
    def fail(self, live, clock):
        self._session = object()
        self.failed.emit("FFmpeg indisponible")

    monkeypatch.setattr("ui.live_mix_player.LiveMixPlayer.start", fail)
    monkeypatch.setattr("ui.live_mix_player.LiveMixPlayer.stop", lambda self: setattr(self, "_session", None))
    window.toggle_play()
    try:
        assert not window.live_mix_active and not window.preview_panel.audio_output.isMuted()
        attempts = window._live_mix_failed_key
        window._sync_live_mix()
        assert window._live_mix_failed_key == attempts and not window.live_mix_active, "pas de nouvel essai"
    finally:
        window.toggle_play()


def test_the_live_mix_is_off_without_an_audio_output(window):
    window._live_mix_output = False
    window.toggle_play()
    try:
        assert not window.live_mix_active and not window.preview_panel.audio_output.isMuted()
    finally:
        window.toggle_play()


def test_the_audio_page_shows_the_mixer_and_the_rail_leads_there(window):
    from core.workspace_state import PAGE_AUDIO, PAGE_EDIT, PanelId

    window.resize(1440, 900)
    window.show()
    window.switch_page(PAGE_AUDIO)
    assert window.workspace.page == PAGE_AUDIO and window.workspace.is_visible(PanelId.MIXER)
    assert window.page_buttons[PAGE_AUDIO].isChecked() and window.side_rail.active() == "audio"
    assert window.mixer_panel.strip_count() == sum(1 for t in window.project.tracks if t.type == "audio")
    window.switch_page(PAGE_EDIT)
    assert window.workspace.is_visible(PanelId.MIXER) and window.mixer_panel.collapsed, "le Montage garde sa disposition : mixeur replié en barre"
    assert window.side_rail.active() == "edit"
    window._on_side_rail_changed("audio")
    assert window.workspace.page == PAGE_AUDIO


def test_a_mix_that_cannot_keep_up_gives_the_monitor_its_sound_back(window, monkeypatch):
    """Trois recalages en 5 s : FFmpeg ne tient pas le temps réel, plus de relances en boucle."""
    started = []
    monkeypatch.setattr("ui.live_mix_player.LiveMixPlayer.start",
                        lambda self, live, clock: (started.append(live), setattr(self, "_session", object())))
    monkeypatch.setattr("ui.live_mix_player.LiveMixPlayer.stop", lambda self: setattr(self, "_session", None))
    monkeypatch.setattr("ui.live_mix_player.LiveMixPlayer.heard_time", property(lambda self: -10.0))
    window.toggle_play()
    try:
        for _ in range(4):
            window._sync_live_mix()
        assert len(started) == 3, "deux recalages, puis l'abandon"
        assert not window.live_mix_active and not window.preview_panel.audio_output.isMuted()
    finally:
        window.toggle_play()


def test_a_synchronous_startup_failure_is_remembered_and_not_retried_every_tick(window, monkeypatch):
    """Un plan ou une commande impossibles à construire ne sont pas réessayés 25 fois par seconde (et sans trace à
    chaque tic) : l'échec est retenu pour cet état du projet, le moniteur garde son son."""
    attempts = []

    def broken(*args, **kwargs):
        attempts.append(1)
        raise RuntimeError("FFmpeg introuvable")

    monkeypatch.setattr("core.live_mix.build_live_mix_command", broken)
    window.toggle_play()
    try:
        for _ in range(10):                                # dix tics de lecture
            window._sync_live_mix()
        assert len(attempts) == 1, "un seul essai pour cet état du projet"
        assert not window.live_mix_active and not window.preview_panel.audio_output.isMuted()
        window.history.record(window.project, "autre état")  # le projet change : un nouvel essai est permis
        window._sync_live_mix()
        assert len(attempts) == 2
    finally:
        window.toggle_play()


def test_the_pages_are_ordered_edit_composition_color_audio(window):
    """L'ordre des puces de la barre supérieure, de gauche à droite, et du menu Fenêtre."""
    from core.workspace_state import PAGE_AUDIO, PAGE_COLOR, PAGE_COMPOSITION, PAGE_EDIT, PAGES

    assert PAGES == (PAGE_EDIT, PAGE_COMPOSITION, PAGE_COLOR, PAGE_AUDIO)
    assert list(window.page_buttons) == list(PAGES)
    window.resize(1440, 900)
    window.show()
    xs = [window.page_buttons[page].mapTo(window, window.page_buttons[page].rect().topLeft()).x() for page in PAGES]
    assert xs == sorted(xs), "affichées de gauche à droite dans cet ordre"
