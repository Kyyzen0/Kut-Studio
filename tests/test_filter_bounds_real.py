"""Réglages aux bornes, rendus par le vrai FFmpeg : ce que les tests de forme de la commande ne voient pas.

Un balayage des bornes des réglages (incrustation, transitions, vitesse, durées) a trouvé des valeurs, atteignables ou
non depuis l'interface, que le modèle acceptait et qu'FFmpeg refusait ou rendait noires. Chaque cas ci-dessous est un de
ces réglages, avec le vrai FFmpeg.
"""

from __future__ import annotations

import subprocess

import numpy as np
import pytest
from audio_scenes import write_wav
from render_probe import lavfi_video, needs_ffmpeg, render_frame

from core.compositing import MIN_CHROMA_TOLERANCE, ChromaKey, Compositing
from core.export_engine import ExportEngine, filter_graph_arguments, input_arguments
from core.project_model import Clip, MediaAsset, Project, Track
from core.render_plan import build_render_plan
from core.time_remapping import TimeRemapping
from core.transitions import MIN_TRANSITION_SECONDS, Transition, TransitionType, add_transition

W, H = 64, 36


# --- incrustation -------------------------------------------------------------------------------------------------------


def test_the_chroma_tolerance_is_never_below_what_chromakey_accepts():
    """FFmpeg refuse une similarité de 0 (« Result too large ») : la valeur où démarre le curseur."""
    key = ChromaKey(enabled=True, tolerance=0.0)
    assert key.tolerance == MIN_CHROMA_TOLERANCE == 0.01
    assert ChromaKey(tolerance=-3.0).tolerance == 0.01 and ChromaKey(tolerance=7.0).tolerance == 1.0
    assert ChromaKey(tolerance=0.37).tolerance == 0.37


@needs_ffmpeg
@pytest.mark.parametrize("tolerance", [0.0, 0.01, 1.0])
def test_a_keyed_clip_exports_at_every_tolerance(tmp_path, tolerance):
    media = lavfi_video(tmp_path / "green.mp4", "color=c=0x00FF00:d=2", size=(W, H), seconds=2.0)
    clip = Clip("c", "v", "V1", 0.0, 0.0, 2.0)
    clip.compositing = Compositing(chroma_key=ChromaKey(enabled=True, color="#00FF00", tolerance=tolerance))
    project = Project("p", width=W, height=H, fps=25.0,
                      media_assets=[MediaAsset("v", str(media), "v", 2.0, W, H, 25.0, "video")],
                      tracks=[Track("V1", "V1", "video", clips=[clip])])
    frame = render_frame(build_render_plan(project), W, H, 0.5)
    assert frame.shape == (H, W, 3)


# --- transitions --------------------------------------------------------------------------------------------------------


def test_a_transition_shorter_than_half_a_frame_is_refused():
    """``xfade`` arrondit sa durée à la cadence : sous une demi-image, rien n'est produit."""
    for duration in (0.001, 0.01, 0.0499, 0.0, -1.0, float("nan")):
        with pytest.raises(ValueError):
            Transition("t", "a", "b", TransitionType.CROSSFADE, duration)
    assert Transition("t", "a", "b", TransitionType.CROSSFADE, MIN_TRANSITION_SECONDS).duration == 0.05


def _two_colours(tmp_path) -> Project:
    red = lavfi_video(tmp_path / "red.mp4", "color=c=0xC81E1E:d=2", size=(W, H), seconds=2.0)
    blue = lavfi_video(tmp_path / "blue.mp4", "color=c=0x1E1EC8:d=2", size=(W, H), seconds=2.0)
    assets = [MediaAsset("r", str(red), "r", 2.0, W, H, 25.0, "video"),
              MediaAsset("b", str(blue), "b", 2.0, W, H, 25.0, "video")]
    first, second = Clip("a", "r", "V1", 0.0, 0.0, 2.0), Clip("b", "b", "V1", 2.0, 0.0, 2.0)
    return Project("p", width=W, height=H, fps=25.0, media_assets=assets,
                   tracks=[Track("V1", "V1", "video", clips=[first, second])])


@needs_ffmpeg
def test_the_shortest_transition_still_shows_the_second_clip_after_it(tmp_path):
    """Avant : 0,001 s donnait un export noir de la transition à la fin."""
    project = _two_colours(tmp_path)
    with pytest.raises(ValueError):
        add_transition(project, "a", "b", TransitionType.CROSSFADE, 0.001)
    add_transition(project, "a", "b", TransitionType.CROSSFADE, MIN_TRANSITION_SECONDS)
    plan = build_render_plan(project)
    for moment in (0.5, 3.0, 3.8):
        frame = render_frame(plan, W, H, moment).reshape(-1, 3).mean(axis=0)
        expected = (200, 30, 30) if moment < 1.9 else (30, 30, 200)
        assert np.abs(frame - expected).max() < 12, f"t={moment} s : {frame}"


# --- durées de la vitesse ------------------------------------------------------------------------------------------------


@needs_ffmpeg
@pytest.mark.parametrize("duration", [1e-6, 5e-5, 1e-4, 0.5])
def test_a_clip_with_an_imposed_duration_is_exported_however_short(tmp_path, duration):
    """Les options de durée d'``atrim`` et d'``apad`` sont lues comme un temps : « 1e-06 » était refusé."""
    path = write_wav(tmp_path / "tone.wav", np.zeros((48000, 2)), rate=48000)
    asset = MediaAsset("a", str(path), "a", 1.0, 0, 0, 0.0, "audio", has_audio=True)
    clip = Clip("c", "a", "A1", 0.0, 0.0, 1.0, time_remapping=TimeRemapping(duration=duration))
    project = Project("p", width=W, height=H, fps=25.0, media_assets=[asset],
                      tracks=[Track("A1", "A1", "audio", clips=[clip])])
    graph, _video, audio, inputs = ExportEngine._build_filter_complex(
        build_render_plan(project), W, H, 25.0, None, audio_only=True)
    assert "e-0" not in graph, "aucune notation scientifique dans le graphe"
    command = ["ffmpeg", "-v", "error", "-nostdin"]
    for source in inputs:
        command += input_arguments(source)
    command += filter_graph_arguments(graph, []) + ["-map", f"[{audio}]", "-f", "null", "-"]
    completed = subprocess.run(command, capture_output=True, timeout=60)
    assert completed.returncode == 0, completed.stderr.decode("utf-8", "replace")


def test_time_options_of_the_retime_graph_are_decimal():
    from core.retime_graph import _fmt, _fmt_time

    assert _fmt_time(1e-6) == "0.000001" and _fmt_time(5e-5) == "0.00005"
    assert _fmt_time(2.0) == "2" and _fmt_time(0.0) == "0" and _fmt_time(3600.0) == "3600"
    assert "e" in _fmt(1e-6), "les expressions gardent leurs 12 chiffres significatifs"


def test_a_refused_transition_leaves_the_clips_where_they_were(tmp_path):
    """Avant : le clip entrant était décalé de la durée refusée, puis l'erreur était levée."""
    project = Project("p", width=W, height=H, fps=25.0, media_assets=[
        MediaAsset("r", "/m/r.mp4", "r", 2.0, W, H, 25.0, "video")], tracks=[
        Track("V1", "V1", "video", clips=[Clip("a", "r", "V1", 0.0, 0.0, 2.0), Clip("b", "r", "V1", 2.0, 0.0, 2.0)])])
    second = project.tracks[0].clips[1]
    with pytest.raises(ValueError):
        add_transition(project, "a", "b", TransitionType.CROSSFADE, 0.001)       # trop courte
    assert second.timeline_start == 2.0 and not project.transitions
    add_transition(project, "a", "b", TransitionType.CROSSFADE, 0.5)
    assert second.timeline_start == 1.5
    with pytest.raises(ValueError):                                              # déjà une transition ici
        add_transition(project, "a", "b", TransitionType.CROSSFADE, 0.5)
    assert second.timeline_start == 1.5 and len(project.transitions) == 1


def test_the_chroma_key_slider_cannot_go_below_the_accepted_tolerance(qtbot):
    from ui.compositing_editor import CompositingEditor

    editor = CompositingEditor("")
    qtbot.addWidget(editor)
    spin = editor.spins["tolerance"]
    assert spin.minimum() == MIN_CHROMA_TOLERANCE
    spin.setValue(0.0)
    assert spin.value() == MIN_CHROMA_TOLERANCE
