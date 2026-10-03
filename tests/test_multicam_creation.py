"""Création d'une séquence Multicam : dialogue, méthodes de synchronisation, analyse sonore, résultat, historique.

Les dialogues modaux sont remplacés par la réponse qu'ils donneraient (``choice=`` / ``exec`` simulé) ; l'analyse sonore
tourne pour de vrai sur des médias synthétiques (FFmpeg local), exécutée immédiatement à la place de la file d'analyses.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtWidgets import QDialog

from audio_scenes import SAMPLE_RATE, delayed, speech, window as cut, write_wav
from core.multicam import angle_offset
from multicam_stubs import keep_preview_player_off_the_disk
from core.multicam_model import AudioMode, SyncMethod, SyncStatus
from core.project_model import Clip, Marker, MediaAsset, Project, Track
from ui import i18n
from ui.multicam_dialogs import (
    CreationChoice,
    MulticamCreateDialog,
    SourceRow,
    SummaryRow,
    SyncSummaryDialog,
    available_methods,
    default_method,
)

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg absent")


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


def _load(window, project: Project) -> None:
    window.project = project
    window.history.reset(project)
    window.timeline_panel.set_project(project)
    window._update_timeline_duration()


def _video(asset_id: str, *, timecode: str = "", path: str = "", name: str = "", has_audio: bool = True, **extra):
    return MediaAsset(asset_id, path or f"/media/{asset_id}.mp4", name or asset_id, 60.0, 1920, 1080, 25.0, "video",
                      has_audio, timecode=timecode, **extra)


def _audio(asset_id: str, *, path: str = "", name: str = ""):
    return MediaAsset(asset_id, path or f"/media/{asset_id}.wav", name or asset_id, 70.0, 0, 0, 0.0, "audio", True)


def _empty(assets) -> Project:
    return Project("p", media_assets=list(assets), tracks=[Track("V1", "V1", "video"), Track("A1", "A1", "audio")])


def _choice(method: SyncMethod, *pairs: tuple[str, str], name: str = "Concert") -> CreationChoice:
    return CreationChoice(name=name, method=method, sources=tuple(pairs))


def _source(window):
    return next(s for s in window.project.sequences if s.multicam is not None)


# --- méthodes sans analyse ---------------------------------------------------------------------------------------------


def test_timecode_sync_aligns_on_the_metadata_start_and_places_a_source_without_timecode_at_the_start(window):
    _load(window, _empty([
        _video("camA", timecode="01:00:10:00"), _video("camB", timecode="01:00:12:12"), _video("camC"),
        _audio("rec"),
    ]))
    result = window.create_multicam_from_assets(
        ["camA", "camB", "camC"],
        choice=_choice(SyncMethod.TIMECODE, ("camA", "Wide"), ("camB", "Close-up"), ("camC", "Handheld")),
    )
    assert result is not None
    sequence, segment = result
    offsets = {angle.name: angle_offset(sequence, angle) for angle in sequence.multicam.angles}
    assert offsets == pytest.approx({"Wide": 0.0, "Close-up": 2.48, "Handheld": 0.0})   # 2 s + 12 images à 25 i/s = 2,48 s
    angles = {angle.name: angle for angle in sequence.multicam.angles}
    assert angles["Wide"].sync_method is SyncMethod.TIMECODE and angles["Handheld"].sync_method is SyncMethod.START
    assert sequence.multicam.sync_method is SyncMethod.TIMECODE
    assert segment is not None and segment.sequence_id == sequence.id and segment.angle_id == "angle-1"
    assert "sans timecode" in window.statusBar().currentMessage() or "timecode" in window.statusBar().currentMessage()
    # une seule entrée d'historique pour toute la création, et un seul Ctrl+Z la défait
    assert window.history.undo_label == "Créer la séquence Multicam « Concert »"
    window.undo_last()
    assert not any(s.multicam for s in window.project.sequences) and len(window.project.sequences) == 1
    window.redo_last()
    assert any(s.multicam for s in window.project.sequences)


def test_a_timecode_with_a_different_frame_rate_is_not_truncated(window):
    exact = _video("camB", timecode="01:00:10:00", timecode_fps=29.97002997)
    _load(window, _empty([_video("camA", timecode="01:00:00:00", timecode_fps=29.97002997), exact]))
    _sequence, _segment = window.create_multicam_from_assets(
        ["camA", "camB"], choice=_choice(SyncMethod.TIMECODE, ("camA", "A"), ("camB", "B")),
    )
    sequence = _source(window)
    offset = angle_offset(sequence, sequence.multicam.angles[1])
    assert offset == pytest.approx(10.01, abs=0.001)    # 300 images à 30000/1001 : 10,01 s (et pas 10 s, ni 10,34 s)


@pytest.mark.parametrize(
    ("method", "expected"),
    [(SyncMethod.START, SyncMethod.START), (SyncMethod.MANUAL, SyncMethod.MANUAL)],
)
def test_clip_starts_and_manual_sync_start_every_angle_together(window, method, expected):
    _load(window, _empty([_video("camA"), _video("camB")]))
    window.create_multicam_from_assets(["camA", "camB"], choice=_choice(method, ("camA", "A"), ("camB", "B")))
    sequence = _source(window)
    assert [angle_offset(sequence, a) for a in sequence.multicam.angles] == [0.0, 0.0]
    assert all(a.sync_method is expected for a in sequence.multicam.angles)


def test_creation_from_the_library_needs_two_sources_and_names_the_refusal(window):
    _load(window, _empty([_video("camA"), _video("camB")]))
    assert window.create_multicam_from_assets(["camA"]) is None
    assert window.statusBar().currentMessage() == "Choisissez au moins deux sources."
    assert not any(s.multicam for s in window.project.sequences)


def test_a_cancelled_dialog_creates_nothing(window, monkeypatch):
    _load(window, _empty([_video("camA"), _video("camB")]))
    monkeypatch.setattr(window, "_ask_multicam_choice", lambda *a, **k: None)
    assert window.create_multicam_from_assets(["camA", "camB"]) is None
    assert len(window.project.sequences) == 1 and len(window.history) == 1


def test_an_audio_recorder_can_be_added_in_the_dialog_and_is_kept_in_the_order_chosen(window):
    _load(window, _empty([_video("camA"), _video("camB"), _audio("rec")]))
    window.create_multicam_from_assets(
        ["camA", "camB"],
        choice=_choice(SyncMethod.START, ("rec", "Recorder"), ("camA", "Wide"), ("camB", "Close-up")),
    )
    sequence = _source(window)
    assert [a.name for a in sequence.multicam.angles] == ["Recorder", "Wide", "Close-up"]
    assert [t.type for t in sequence.tracks] == ["audio", "video", "video"]
    assert sequence.multicam.audio.mode is AudioMode.FIXED and sequence.multicam.audio.angle_ids == ("angle-1",)


# --- depuis la timeline -----------------------------------------------------------------------------------------------------


def _timeline(window, *, markers: bool = False) -> None:
    project = Project(
        "t", media_assets=[_video("camA"), _video("camB")],
        tracks=[Track("V1", "V1", "video", clips=[Clip("a", "camA", "V1", 10.0, 3.0, 23.0, label="Cam A")]),
                Track("V2", "V2", "video", clips=[Clip("b", "camB", "V2", 12.0, 0.0, 20.0, label="Cam B")]),
                Track("A1", "A1", "audio")],
    )
    if markers:
        project.markers.append(Marker("m1", 14.0, "clap"))
    _load(window, project)
    window.timeline_panel._set_selection(["a", "b"], "a", announce=False)


def test_the_timeline_flow_keeps_the_current_positions_by_default(window):
    _timeline(window)
    sequence, segment = window.create_multicam_from_timeline_selection(
        choice=_choice(SyncMethod.POSITIONS, ("a", "Wide"), ("b", "Close-up")),
    )
    assert [angle_offset(sequence, a) for a in sequence.multicam.angles] == [0.0, 2.0]
    assert all(a.sync_method is SyncMethod.POSITIONS for a in sequence.multicam.angles)
    assert [c.id for t in window.project.tracks for c in t.clips] == [segment.id]
    window.undo_last()
    assert {c.id for t in window.project.tracks for c in t.clips} == {"a", "b"}


def test_the_marker_method_aligns_the_clips_on_their_marker(window):
    _timeline(window, markers=True)
    # le seul repère (14 s) tombe dans les deux clips : 4 s après le début du clip A, 2 s après le début du clip B
    sources = window._sources_from_clips(["a", "b"])                 # noqa: SLF001
    assert [s.row.marker_offset for s in sources] == [pytest.approx(10.0 - 14.0), pytest.approx(12.0 - 14.0)]
    sequence, _segment = window.create_multicam_from_timeline_selection(
        choice=_choice(SyncMethod.MARKER, ("a", "Wide"), ("b", "Close-up")),
    )
    # clip A démarre 2 s avant le repère de plus que B → A commence à 0, B à 2 s
    assert [angle_offset(sequence, a) for a in sequence.multicam.angles] == [0.0, 2.0]
    assert all(a.sync_method is SyncMethod.MARKER for a in sequence.multicam.angles)


def test_the_timeline_flow_refuses_a_single_clip(window):
    _timeline(window)
    window.timeline_panel._set_selection(["a"], "a", announce=False)
    assert window.create_multicam_from_timeline_selection() is None
    assert "au moins deux clips" in window.statusBar().currentMessage()


# --- boîtes de dialogue ----------------------------------------------------------------------------------------------------


def _rows(**flags):
    return [
        SourceRow("a", "Wide", has_audio=True, start_seconds=flags.get("a_tc"), marker_offset=flags.get("a_marker")),
        SourceRow("b", "Close-up", has_audio=flags.get("b_audio", True), start_seconds=flags.get("b_tc"),
                  marker_offset=flags.get("b_marker")),
    ]


def test_unavailable_methods_are_explained_and_the_default_is_the_most_useful_one():
    reasons = available_methods(_rows(), from_timeline=False)
    assert reasons[SyncMethod.AUDIO] == "" and reasons[SyncMethod.TIMECODE] and reasons[SyncMethod.MARKER]
    assert reasons[SyncMethod.POSITIONS] and reasons[SyncMethod.START] == "" and reasons[SyncMethod.MANUAL] == ""
    assert default_method(reasons, from_timeline=False) is SyncMethod.AUDIO
    both = available_methods(_rows(a_tc=1.0, b_tc=2.0), from_timeline=False)
    assert default_method(both, from_timeline=False) is SyncMethod.TIMECODE
    silent = available_methods(_rows(b_audio=False), from_timeline=False)
    assert silent[SyncMethod.AUDIO] and default_method(silent, from_timeline=False) is SyncMethod.START
    timeline = available_methods(_rows(a_marker=1.0, b_marker=2.0), from_timeline=True)
    assert timeline[SyncMethod.MARKER] == "" and default_method(timeline, from_timeline=True) is SyncMethod.POSITIONS
    partial = available_methods(_rows(a_marker=1.0), from_timeline=True)
    assert partial[SyncMethod.MARKER]


def test_the_create_dialog_lists_sources_renames_removes_and_adds(qtbot):
    extra = SourceRow("rec", "Recorder", kind="audio")
    dialog = MulticamCreateDialog(_rows(), default_name="Multicam", from_timeline=False, addable=[extra])
    qtbot.addWidget(dialog)
    assert dialog.method() is SyncMethod.AUDIO and dialog.ok_button.isEnabled()
    assert not dialog._method_buttons[SyncMethod.TIMECODE].isEnabled()          # noqa: SLF001
    assert dialog._method_buttons[SyncMethod.TIMECODE].toolTip()                # la raison est dite
    dialog._rows[0].name_edit.setText("  Plan large ")                          # noqa: SLF001
    dialog._add_source(extra)                                                   # noqa: SLF001
    assert [name for _key, name in dialog.choice().sources] == ["Plan large", "Close-up", "Recorder"]
    dialog._remove_row(dialog._rows[1])                                         # noqa: SLF001
    dialog._remove_row(dialog._rows[0])                                         # noqa: SLF001
    assert len(dialog._rows) == 1 or not dialog.ok_button.isEnabled()          # noqa: SLF001
    assert not dialog.ok_button.isEnabled() and dialog.status_label.text()      # pas de Multicam à une seule source


def test_the_create_dialog_refuses_an_empty_name_and_has_one_default_button(qtbot):
    dialog = MulticamCreateDialog(_rows(), default_name="", from_timeline=False)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog._on_accept()                                                          # noqa: SLF001
    assert dialog.result() != QDialog.Accepted
    defaults = [b for b in dialog.findChildren(type(dialog.ok_button)) if b.isDefault()]
    assert defaults == [dialog.ok_button]
    dialog.name_edit.setText("Concert")
    dialog._on_accept()                                                          # noqa: SLF001
    assert dialog.result() == QDialog.Accepted and dialog.choice().name == "Concert"


def test_the_summary_dialog_tells_the_truth_and_returns_the_audio_choice(qtbot):
    from core.multicam_model import MulticamAudio

    rows = [SummaryRow("Wide", SyncStatus.NONE, 0.0, reference=True), SummaryRow("Close-up", SyncStatus.GOOD, 2.5),
            SummaryRow("Drone", SyncStatus.FAILED), SummaryRow("Handheld", SyncStatus.UNCERTAIN, 7.0)]
    choices = [("suit", MulticamAudio(AudioMode.FOLLOW_VIDEO)), ("fixe", MulticamAudio(AudioMode.FIXED, ("angle-1",)))]
    dialog = SyncSummaryDialog(rows, audio_choices=choices)
    qtbot.addWidget(dialog)
    texts = [label.text() for label in dialog.findChildren(type(dialog._caption("")))]    # noqa: SLF001
    joined = " ".join(texts)
    assert "Référence" in joined and "Synchronisation bonne" in joined and "Échec de la synchronisation" in joined
    assert "Synchronisation incertaine" in joined and "décalage 2.500 s" in joined
    assert dialog.audio() == choices[0][1]
    dialog.audio_combo.setCurrentIndex(1)
    assert dialog.audio() == choices[1][1]
    assert SyncSummaryDialog(rows[:2]).audio() is None


# --- analyse sonore --------------------------------------------------------------------------------------------------------


SECONDS = 30.0


@pytest.fixture(scope="module")
def scene():
    return speech(np.random.default_rng(8), SECONDS + 30)


def _camera(path: Path, audio: np.ndarray) -> Path:
    wav = write_wav(path.with_suffix(".wav"), audio)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=gray:s=64x36:r=10:d={len(audio) / SAMPLE_RATE}",
         "-i", str(wav), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)],
        check=True,
    )
    return path


def _run_sync_now(window, monkeypatch):
    """L'analyse part sur la file d'analyses ; ici on l'exécute tout de suite puis on lit son résultat."""
    monkeypatch.setattr(window.runtime, "schedule_analysis", lambda key, fn, **_k: fn())


def _poll(window, key):
    window._poll_multicam_sync(key)                                              # noqa: SLF001


def _media_project(tmp_path, scene, *, recorder_offset=3.0, unrelated=False):
    base = cut(scene, 0.0, SECONDS)
    cam_a = _camera(tmp_path / "camA.mp4", base)
    cam_b = _camera(tmp_path / "camB.mp4", cut(delayed(scene, 2.4), 0.0, SECONDS))
    recorder = write_wav(tmp_path / "rec.wav", cut(delayed(scene, recorder_offset), 0.0, SECONDS))
    if unrelated:
        recorder = write_wav(tmp_path / "rec.wav", speech(np.random.default_rng(77), SECONDS))
    return _empty([
        _video("camA", path=str(cam_a)), _video("camB", path=str(cam_b)), _audio("rec", path=str(recorder)),
    ])


@needs_ffmpeg
def test_audio_sync_places_every_angle_and_picks_the_recorder_without_asking(window, monkeypatch, tmp_path, scene):
    _load(window, _media_project(tmp_path, scene))
    _run_sync_now(window, monkeypatch)
    asked = []
    monkeypatch.setattr("ui.main_window_mixins.multicam_creation.SyncSummaryDialog.exec",
                        lambda self: asked.append(1) or QDialog.Accepted)
    key = window.create_multicam_from_assets(
        ["camA", "camB", "rec"],
        choice=_choice(SyncMethod.AUDIO, ("camA", "Wide"), ("camB", "Close-up"), ("rec", "Recorder")),
    )
    assert isinstance(key, str) and window._multicam_syncs                         # noqa: SLF001 - analyse lancée, pas encore finie
    _poll(window, key)
    sequence = _source(window)
    by_name = {a.name: a for a in sequence.multicam.angles}
    offsets = {name: angle_offset(sequence, angle) for name, angle in by_name.items()}
    # B contient le son 2,4 s plus tard : elle a démarré 2,4 s avant A ; l'enregistreur 3 s avant A
    assert offsets["Close-up"] == pytest.approx(0.6, abs=0.01) and offsets["Wide"] == pytest.approx(3.0, abs=0.01)
    assert offsets["Recorder"] == pytest.approx(0.0, abs=0.01)
    assert all(a.sync_method is SyncMethod.AUDIO for a in sequence.multicam.angles)
    assert by_name["Close-up"].sync_status in {SyncStatus.EXCELLENT, SyncStatus.GOOD}
    assert by_name["Recorder"].sync_confidence is not None
    assert sequence.multicam.audio.mode is AudioMode.FIXED and sequence.multicam.audio.angle_ids == (by_name["Recorder"].id,)
    assert asked == []                                                              # tout est sûr : aucune boîte de résultat
    assert not window._multicam_syncs and window.history.undo_label.startswith("Créer la séquence Multicam")  # noqa: SLF001
    assert "3 angle(s) synchronisé(s) sur 3" in window.statusBar().currentMessage()


@needs_ffmpeg
def test_an_uncertain_sync_opens_the_result_dialog_and_never_claims_success(window, monkeypatch, tmp_path, scene):
    _load(window, _media_project(tmp_path, scene, unrelated=True))
    _run_sync_now(window, monkeypatch)
    seen = {}

    def fake_exec(self):
        seen["rows"] = [label.text() for label in self.findChildren(type(self._caption("")))]    # noqa: SLF001
        seen["choices"] = [self.audio_combo.itemText(i) for i in range(self.audio_combo.count())] if self.audio_combo else []
        if self.audio_combo is not None:
            self.audio_combo.setCurrentIndex(0)                                    # le son suit l'image
        return QDialog.Accepted

    monkeypatch.setattr("ui.main_window_mixins.multicam_creation.SyncSummaryDialog.exec", fake_exec)
    key = window.create_multicam_from_assets(
        ["camA", "camB", "rec"],
        choice=_choice(SyncMethod.AUDIO, ("camA", "Wide"), ("camB", "Close-up"), ("rec", "Recorder")),
    )
    _poll(window, key)
    text = " ".join(seen["rows"])
    assert "Échec de la synchronisation" in text or "Synchronisation incertaine" in text
    assert seen["choices"][0] == "Le son suit l'image"                              # l'enregistreur n'est pas sûr : on demande
    sequence = _source(window)
    recorder = next(a for a in sequence.multicam.angles if a.name == "Recorder")
    assert recorder.sync_status in {SyncStatus.FAILED, SyncStatus.UNCERTAIN}
    assert sequence.multicam.audio.mode is AudioMode.FOLLOW_VIDEO
    assert angle_offset(sequence, recorder) == pytest.approx(0.0, abs=0.01) or recorder.sync_status is SyncStatus.UNCERTAIN


@needs_ffmpeg
def test_cancelling_the_result_dialog_keeps_the_project_untouched(window, monkeypatch, tmp_path, scene):
    _load(window, _media_project(tmp_path, scene, unrelated=True))
    _run_sync_now(window, monkeypatch)
    monkeypatch.setattr("ui.main_window_mixins.multicam_creation.SyncSummaryDialog.exec", lambda self: QDialog.Rejected)
    key = window.create_multicam_from_assets(
        ["camA", "camB", "rec"],
        choice=_choice(SyncMethod.AUDIO, ("camA", "Wide"), ("camB", "Close-up"), ("rec", "Recorder")),
    )
    _poll(window, key)
    assert not any(s.multicam for s in window.project.sequences) and len(window.history) == 1


@needs_ffmpeg
def test_audio_sync_from_timeline_clips_uses_the_trimmed_range_of_each_clip(window, monkeypatch, tmp_path, scene):
    cam_a = _camera(tmp_path / "camA.mp4", cut(scene, 0.0, SECONDS))
    cam_b = _camera(tmp_path / "camB.mp4", cut(delayed(scene, 2.0), 0.0, SECONDS))
    project = Project(
        "t", media_assets=[_video("camA", path=str(cam_a)), _video("camB", path=str(cam_b))],
        tracks=[Track("V1", "V1", "video", clips=[Clip("a", "camA", "V1", 0.0, 5.0, 25.0, label="A")]),
                Track("V2", "V2", "video", clips=[Clip("b", "camB", "V2", 40.0, 0.0, 28.0, label="B")])],
    )
    _load(window, project)
    window.timeline_panel._set_selection(["a", "b"], "a", announce=False)
    _run_sync_now(window, monkeypatch)
    key = window.create_multicam_from_timeline_selection(
        choice=_choice(SyncMethod.AUDIO, ("a", "Wide"), ("b", "Close-up")),
    )
    _poll(window, key)
    sequence = _source(window)
    # Sur la scène sonore : le clip A commence à 5 s (source_in), le clip B à −2 s (son retardé de 2 s dans son média).
    # L'écart entre les débuts de clip est donc 5 − (−2) = 7 s : la mesure porte sur la plage rognée de chaque clip.
    start = {a.name: angle_offset(sequence, a) for a in sequence.multicam.angles}
    assert start["Wide"] - start["Close-up"] == pytest.approx(7.0, abs=0.01)


@needs_ffmpeg
def test_cancelling_the_progress_dialog_or_a_failed_job_creates_nothing_and_says_so(window, monkeypatch, tmp_path, scene):
    _load(window, _media_project(tmp_path, scene))
    monkeypatch.setattr(window.runtime, "schedule_analysis", lambda key, fn, **_k: None)   # l'analyse n'avance pas
    key = window.create_multicam_from_assets(
        ["camA", "camB", "rec"],
        choice=_choice(SyncMethod.AUDIO, ("camA", "Wide"), ("camB", "Close-up"), ("rec", "Recorder")),
    )
    pending = window._multicam_syncs[key]                                          # noqa: SLF001
    pending.dialog.canceled.emit()
    pending.job.run()                                                              # la tâche constate l'annulation
    _poll(window, key)
    assert window.statusBar().currentMessage() == "Synchronisation annulée."
    assert not any(s.multicam for s in window.project.sequences)
    # tâche en échec
    key = window.create_multicam_from_assets(
        ["camA", "camB"], choice=_choice(SyncMethod.AUDIO, ("camA", "Wide"), ("camB", "Close-up")),
    )
    pending = window._multicam_syncs[key]                                          # noqa: SLF001
    monkeypatch.setattr("core.audio_sync.analyze_sync", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("panne")))
    pending.job.run()
    _poll(window, key)
    assert "La synchronisation a échoué" in window.statusBar().currentMessage()


@needs_ffmpeg
def test_a_stale_result_after_a_project_change_is_dropped_and_closing_cancels_every_analysis(window, monkeypatch, tmp_path, scene):
    _load(window, _media_project(tmp_path, scene))
    monkeypatch.setattr(window.runtime, "schedule_analysis", lambda key, fn, **_k: None)
    key = window.create_multicam_from_assets(
        ["camA", "camB"], choice=_choice(SyncMethod.AUDIO, ("camA", "Wide"), ("camB", "Close-up")),
    )
    pending = window._multicam_syncs[key]                                          # noqa: SLF001
    pending.job.run()                                                              # terminée normalement…
    window.runtime.session_id += 1                                                 # …mais un autre projet est ouvert entre-temps
    _poll(window, key)
    assert not any(s.multicam for s in window.project.sequences) and not window._multicam_syncs   # noqa: SLF001
    key = window.create_multicam_from_assets(
        ["camA", "camB"], choice=_choice(SyncMethod.AUDIO, ("camA", "Wide"), ("camB", "Close-up")),
    )
    job = window._multicam_syncs[key].job                                          # noqa: SLF001
    window._cancel_multicam_syncs()                                                # noqa: SLF001
    assert job.snapshot().state == "cancelled" and not window._multicam_syncs      # noqa: SLF001


# --- bibliothèque -----------------------------------------------------------------------------------------------------------


def test_the_library_selects_several_media_and_the_panel_signal_starts_the_creation(window, monkeypatch):
    _load(window, _empty([_video("camA"), _video("camB"), _video("camC")]))
    window._refresh_project_library()                                              # noqa: SLF001
    bin_widget = window.project_panel.bin_videos
    bin_widget._list.selectAll()                                                   # noqa: SLF001
    assert len(bin_widget.selected_asset_ids) == 3
    got = []
    monkeypatch.setattr(window, "_ask_multicam_choice", lambda rows, **kwargs: got.append((len(rows), kwargs["from_timeline"])))
    window.project_panel.multicam_create_requested.emit(bin_widget.selected_asset_ids)
    assert got == [(3, False)]
