"""Son en direct d'un segment Multicam : la politique audio se retrouve dans la lecture du moniteur.

Aucun vrai média n'est joué : les lecteurs sont remplacés par des lecteurs factices qui notent ce qu'on leur demande.
"""

from __future__ import annotations

import pytest

from core.multicam_model import AudioMode
from core.multicam_ops import AngleSpec, create_multicam_source, insert_multicam_clip, set_audio_policy, switch_angle
from core.project_model import MediaAsset, Project, Track
from ui import i18n
from ui.multicam_audio import MAX_SOURCES, AuxAudio


class FakePlayer:
    def __init__(self) -> None:
        self.source = ""
        self._position = 0
        self.state = "stopped"
        self.sources_set = 0
        self.positions_set = 0

    def setSource(self, url) -> None:     # noqa: N802 - API Qt
        self.source = url.toLocalFile()
        self.sources_set += 1

    def setPosition(self, ms: int) -> None:   # noqa: N802 - API Qt
        self._position = ms
        self.positions_set += 1

    def position(self) -> int:
        return self._position

    def play(self) -> None:
        self.state = "playing"

    def pause(self) -> None:
        self.state = "paused"

    def stop(self) -> None:
        self.state = "stopped"


def _factory(parent):
    return FakePlayer(), object()


@pytest.fixture
def aux(qtbot):
    from PySide6.QtCore import QObject

    owner = QObject()
    yield AuxAudio(owner, player_factory=_factory), owner


def test_sources_are_assigned_to_players_followed_and_released(aux):
    audio, _owner = aux
    audio.sync([("/m/rec.wav", 3.0)], playing=True)
    assert audio.active_paths() == ["/m/rec.wav"]
    player = audio._slots[0].player                      # noqa: SLF001
    assert player.source == "/m/rec.wav" and player.state == "playing" and player.position() == 3000
    audio.sync([("/m/rec.wav", 3.04)], playing=True)       # petite dérive : le lecteur garde son horloge
    assert player.positions_set == 1 and player.sources_set == 1
    audio.sync([("/m/rec.wav", 9.0)], playing=True)        # grande dérive : recalage
    assert player.position() == 9000 and player.positions_set == 2
    audio.sync([("/m/rec.wav", 9.0)], playing=False)
    assert player.state == "paused"
    audio.sync([], playing=True)
    assert audio.active_paths() == [] and player.state == "stopped"


def test_several_sources_use_separate_players_and_the_number_is_bounded(aux):
    audio, _owner = aux
    audio.sync([("/m/a.wav", 1.0), ("/m/b.wav", 2.0)], playing=True)
    assert sorted(audio.active_paths()) == ["/m/a.wav", "/m/b.wav"]
    audio.sync([(f"/m/{i}.wav", 0.0) for i in range(10)], playing=True)
    assert len(audio.active_paths()) == MAX_SOURCES and len(audio._slots) == MAX_SOURCES      # noqa: SLF001


def test_release_stops_everything_and_forgets_the_players(aux):
    audio, _owner = aux
    audio.sync([("/m/a.wav", 1.0)], playing=True)
    players = [slot.player for slot in audio._slots]        # noqa: SLF001
    audio.release()
    assert all(player.state == "stopped" for player in players) and audio._slots == []   # noqa: SLF001


# --- dans la fenêtre ---------------------------------------------------------------------------------------------------------


@pytest.fixture
def window(qtbot, monkeypatch):
    from ui.main_window import MainWindow

    i18n.set_language("fr")
    monkeypatch.setattr("ui.main_window.QMessageBox.information", lambda *_, **__: None)
    monkeypatch.setattr("ui.main_window.QMessageBox.critical", lambda *_, **__: None)
    main = MainWindow()
    qtbot.addWidget(main)
    main.timeline_timer.stop()
    # aucun vrai lecteur : le moniteur ne reçoit que des appels factices
    monkeypatch.setattr(main.preview_panel.player, "setSource", lambda *_a: None)
    monkeypatch.setattr(main.preview_panel.player, "setPosition", lambda *_a: None)
    monkeypatch.setattr(main.preview_panel.player, "play", lambda *_a: None)
    main._multicam_aux = AuxAudio(main, player_factory=_factory)      # noqa: SLF001
    return main


def _project(window, tmp_path, mode: AudioMode):
    assets = []
    for name in ("camA", "camB"):
        path = tmp_path / f"{name}.mp4"
        path.write_bytes(b"x")
        assets.append(MediaAsset(name, str(path), name, 60.0, 1920, 1080, 25.0, "video", True))
    rec = tmp_path / "rec.wav"
    rec.write_bytes(b"x")
    assets.append(MediaAsset("rec", str(rec), "rec", 70.0, 0, 0, 0.0, "audio", True))
    project = Project("p", media_assets=assets, tracks=[Track("V1", "V1", "video"), Track("A1", "A1", "audio")])
    source = create_multicam_source(project, [AngleSpec(asset_id="camA", name="A"), AngleSpec(asset_id="camB", name="B", offset=2.0),
                                              AngleSpec(asset_id="rec", name="Rec", offset=0.5)], name="C")
    set_audio_policy(project, source.id, mode, ("angle-3",) if mode is AudioMode.FIXED else
                     ("angle-1", "angle-3") if mode is AudioMode.MIX else ())
    segment = insert_multicam_clip(project, source.id, "V1", 0.0, angle_id="angle-2")
    window.project = project
    window.history.reset(project)
    window.timeline_panel.set_project(project)
    window._update_timeline_duration()
    return project, segment


def _silenced(window) -> bool:
    return window.preview_panel.audio_output.isMuted()


def test_a_fixed_audio_source_plays_beside_a_silenced_monitor(window, tmp_path):
    _project(window, tmp_path, AudioMode.FIXED)
    window.is_playing = True
    window.playhead_seconds = 6.0
    window._sync_preview_to_timeline()                                      # noqa: SLF001
    aux = window._multicam_aux                                              # noqa: SLF001
    assert _silenced(window) is True                                       # le son de la caméra B n'est pas celui voulu
    assert aux.active_paths() == [str(tmp_path / "rec.wav")]
    assert aux._slots[0].player.state == "playing"                           # noqa: SLF001
    assert aux._slots[0].player.position() == 5500                           # 6 s − 0,5 s de décalage de l'enregistreur


def test_audio_that_follows_the_picture_leaves_the_monitor_alone(window, tmp_path):
    _project(window, tmp_path, AudioMode.FOLLOW_VIDEO)
    window.is_playing = True
    window.playhead_seconds = 6.0
    window._sync_preview_to_timeline()                                      # noqa: SLF001
    assert _silenced(window) is False and window._multicam_aux.active_paths() == []   # noqa: SLF001


def test_a_mixed_policy_plays_the_other_sources_and_keeps_the_picture_sound_when_it_is_part_of_the_mix(window, tmp_path):
    _project(window, tmp_path, AudioMode.MIX)
    window.is_playing = True
    window.playhead_seconds = 6.0
    window._sync_preview_to_timeline()                                      # noqa: SLF001
    assert _silenced(window) is True                                        # l'angle B (image) n'est pas dans le mixage
    assert sorted(window._multicam_aux.active_paths()) == sorted([str(tmp_path / "camA.mp4"), str(tmp_path / "rec.wav")])  # noqa: SLF001


def test_leaving_the_segment_restores_the_monitor_sound_and_stops_the_extra_players(window, tmp_path):
    _project(window, tmp_path, AudioMode.FIXED)
    window.is_playing = True
    window.playhead_seconds = 6.0
    window._sync_preview_to_timeline()                                      # noqa: SLF001
    assert _silenced(window) is True
    window.playhead_seconds = 500.0
    window._sync_preview_to_timeline()                                      # noqa: SLF001
    assert _silenced(window) is False and window._multicam_aux.active_paths() == []   # noqa: SLF001


def test_switching_the_angle_keeps_the_fixed_source_playing_without_restarting_it(window, tmp_path):
    project, _segment = _project(window, tmp_path, AudioMode.FIXED)
    window.is_playing = True
    window.playhead_seconds = 6.0
    window._sync_preview_to_timeline()                                      # noqa: SLF001
    player = window._multicam_aux._slots[0].player                          # noqa: SLF001
    assert switch_angle(project, 6.0, "angle-1") is not None
    window.timeline_panel.set_project(project)
    window.playhead_seconds = 6.04
    window._sync_preview_to_timeline()                                      # noqa: SLF001
    assert player.sources_set == 1 and player.state == "playing"             # la même source, jamais coupée par la bascule


def test_pausing_pauses_the_extra_players(window, tmp_path):
    _project(window, tmp_path, AudioMode.FIXED)
    window.is_playing = True
    window.playhead_seconds = 6.0
    window._sync_preview_to_timeline()                                      # noqa: SLF001
    window.is_playing = False
    window._sync_preview_to_timeline()                                      # noqa: SLF001
    assert window._multicam_aux._slots[0].player.state in {"paused", "stopped"}   # noqa: SLF001


def test_closing_the_window_releases_the_extra_players(window, tmp_path):
    _project(window, tmp_path, AudioMode.FIXED)
    window.is_playing = True
    window.playhead_seconds = 6.0
    window._sync_preview_to_timeline()                                      # noqa: SLF001
    player = window._multicam_aux._slots[0].player                          # noqa: SLF001
    window._release_multicam_audio()                                        # noqa: SLF001
    assert player.state == "stopped" and window._multicam_aux._slots == []   # noqa: SLF001
