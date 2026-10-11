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

from core.color_grading import ColorCurve, ColorCurves, ColorGrade
from core.compositing import MIN_CHROMA_TOLERANCE, ChromaKey, Compositing
from core.export_engine import ExportEngine, _build_curves_filter, filter_graph_arguments, input_arguments
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


# --- courbes de couleur ---------------------------------------------------------------------------------------------------

CLOSE_CURVES = {
    "pair au milieu": [(0.0, 0.0), (0.5, 0.4), (0.5005, 0.6), (1.0, 1.0)],
    "écart sous le millionième": [(0.0, 0.0), (0.25, 0.2), (0.2500004, 0.3), (1.0, 1.0)],
    "même case que le noir": [(0.0, 0.0), (0.002, 0.3), (1.0, 1.0)],
    "amas contre le blanc": [(0.0, 0.0), (0.996, 0.1), (0.997, 0.2), (0.998, 0.3), (0.999, 0.4), (1.0, 0.5)],
    "tous dans une seule case": [(0.5, 0.2), (0.5004, 0.8)],
    "un amas de huit points": [(0.0, 0.0)] + [(0.4 + i * 0.0004, 0.3 + i * 0.05) for i in range(8)] + [(1.0, 1.0)],
}


def _bins(expression: str) -> list[int]:
    """Les cases de 1/255 des abscisses d'un ``curves=…='x/y x/y'`` : le critère de FFmpeg (``(int)(x·255)`` strictement croissant)."""
    points = expression.split("='")[1].rstrip("'").split()
    return [int(float(point.split("/")[0]) * 255) for point in points]


@pytest.mark.parametrize("name", list(CLOSE_CURVES))
def test_curve_points_are_spaced_the_way_ffmpeg_requires(name):
    """``curves`` refuse deux points dans la même case de 1/255 (« too close from each other ») : ce n'est pas un écart de
    1e-6 mais d'une case de 8 bits. Le modèle n'impose qu'un ordre strictement croissant."""
    bins = _bins(_build_curves_filter("master", ColorCurve(points=tuple(CLOSE_CURVES[name]))))
    assert all(a < b for a, b in zip(bins, bins[1:])), bins


def test_the_default_sixteen_point_curve_is_emitted_untouched():
    expression = _build_curves_filter("master", ColorCurve(points=tuple((i / 15, min(1.0, i / 15 + 0.1)) for i in range(16))))
    assert expression.count("/") == 16, "aucun des seize points de l'éditeur n'est écarté"


def test_a_step_between_two_close_points_keeps_its_shape():
    """Le point gênant est décalé dans la case suivante, pas supprimé : la marche de 0,4 à 0,6 reste, et ce qui précède aussi."""
    expression = _build_curves_filter("master", ColorCurve(points=((0.0, 0.0), (0.5, 0.4), (0.5005, 0.6), (1.0, 1.0))))
    assert expression == "curves=master='0.0/0.0 0.5/0.4 0.503922/0.6 1.0/1.0'"
    kept = _build_curves_filter("master", ColorCurve(points=((0.5, 0.2), (0.5004, 0.8))))
    assert kept == "curves=master='0.5/0.2 0.503922/0.8'", "même un amas réduit à deux points garde sa marche"


@needs_ffmpeg
@pytest.mark.parametrize("name", list(CLOSE_CURVES))
def test_a_grade_with_close_curve_points_is_exported(tmp_path, name):
    ramp = tmp_path / "ramp.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "nullsrc=s=256x16:r=25:d=2,format=gbrp,geq=r='X':g='X':b='X'", "-c:v", "libx264", "-qp", "0",
                    "-pix_fmt", "yuv444p", str(ramp)], check=True, timeout=60)
    clip = Clip("c", "v", "V1", 0.0, 0.0, 2.0)
    clip.color_grade = ColorGrade(curves=ColorCurves(master=ColorCurve(points=tuple(CLOSE_CURVES[name]))))
    project = Project("p", width=256, height=16, fps=25.0,
                      media_assets=[MediaAsset("v", str(ramp), "v", 2.0, 256, 16, 25.0, "video")],
                      tracks=[Track("V1", "V1", "video", clips=[clip])])
    frame = render_frame(build_render_plan(project), 256, 16, 0.5)
    assert frame.shape == (16, 256, 3)
    row = frame[8].mean(axis=1)
    assert row[-1] > row[0], "la courbe rend un dégradé qui monte : l'image n'est pas écrasée"


# --- résolution de sortie ------------------------------------------------------------------------------------------------


def _probe_size(path) -> tuple[int, int]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=s=x:p=0",
         str(path)], capture_output=True, text=True, check=True, timeout=60).stdout.strip()
    width, height = out.split("x")
    return int(width), int(height)


@needs_ffmpeg
@pytest.mark.parametrize("codec", [("mp4", "h264"), ("mp4", "hevc"), ("mov", "prores_ks")], ids=lambda c: c[1])
@pytest.mark.parametrize("size", [(65, 37), (64, 37), (65, 36)])
def test_an_odd_export_resolution_is_exported_at_the_next_even_size(tmp_path, size, codec):
    """x264, x265 et ProRes ne codent que des dimensions paires (« width not divisible by 2 ») : une résolution libre
    impaire est arrondie au pair supérieur, sans perdre de pixel, au lieu de faire échouer l'export."""
    from core.render_job import RenderJob
    from core.render_presets import custom_preset

    media = lavfi_video(tmp_path / "src.mp4", "color=c=0x3060A0:d=1", size=(W, H), seconds=1.0)
    project = Project("p", width=W, height=H, fps=25.0,
                      media_assets=[MediaAsset("v", str(media), "v", 1.0, W, H, 25.0, "video")],
                      tracks=[Track("V1", "V1", "video", clips=[Clip("c", "v", "V1", 0.0, 0.0, 1.0)])])
    container, video_codec = codec
    spec = custom_preset(container=container, video_codec=video_codec, audio_codec="aac", width=size[0], height=size[1],
                         fps=25, quality=30)
    out = tmp_path / f"odd.{container}"
    job = RenderJob.create(project_fps=25.0, spec=spec, snapshot_path="s", output_path=str(out))
    command = ExportEngine()._build_command(job.to_request(build_render_plan(project)))
    done = subprocess.run(command, capture_output=True, timeout=120)
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")[-600:]
    assert _probe_size(out) == (size[0] + size[0] % 2, size[1] + size[1] % 2)


def test_the_preset_reports_the_even_resolution_it_will_export():
    from core.export_engine import ExportPreset
    from core.render_presets import custom_preset

    spec = custom_preset(width=1081, height=607)
    assert spec.resolution == (1082, 608) and spec.summary().startswith("MP4 · H.264 · 1082×608")
    assert custom_preset(width=1, height=1).resolution == (2, 2)
    assert custom_preset(width=1920, height=1080).resolution == (1920, 1080), "une résolution paire ne bouge pas"
    assert ExportPreset("x", (1081, 607), 20, "128k").resolution == (1082, 608), "le moteur s'en protège aussi"


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


@needs_ffmpeg
@pytest.mark.parametrize("anchor", [-5.0, -0.5, 2.0, 5.0])
def test_the_audio_of_a_clip_whose_anchor_is_outside_the_source_exports(tmp_path, anchor):
    """Ancre avant ou après la fenêtre de la source : la partie collée à la borne est un silence, le reste se joue."""
    path = write_wav(tmp_path / "tone.wav", np.full((48000 * 2, 2), 0.3), rate=48000)
    asset = MediaAsset("a", str(path), "a", 2.0, 0, 0, 0.0, "audio", has_audio=True)
    clip = Clip("c", "a", "A1", 0.0, 0.0, 2.0, time_remapping=TimeRemapping(speed=2.0, anchor=anchor))
    project = Project("p", width=W, height=H, fps=25.0, media_assets=[asset],
                      tracks=[Track("A1", "A1", "audio", clips=[clip])])
    graph, _video, audio, inputs = ExportEngine._build_filter_complex(
        build_render_plan(project), W, H, 25.0, None, audio_only=True)
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
