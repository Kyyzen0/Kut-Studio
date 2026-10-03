"""Multicam : rendus FFmpeg réels (export, aperçu, audio) sur un tournage synthétique de quatre caméras et un enregistreur.

Chaque caméra est un aplat de couleur propre, avec un ton propre, à sa propre cadence (25, 29,97 et 24 i/s) ; l'enregistreur
est un ton à 1 100 Hz. On monte dix changements d'angle, on exporte, puis on relit l'image et le son **à des instants
précis** : l'angle attendu à chaque instant se déduit du montage, pas du rendu. Promesse centrale : seul l'angle actif est
rendu, aperçu et export donnent la même image, la politique audio se retrouve dans le son exporté.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from core.export_engine import ExportEngine, ExportFormat, ExportPreset, ExportRequest, _ffmpeg_command_prefix
from core.filter_graph import build_preview_command
from core.multicam_model import AudioMode
from core.multicam_ops import (
    AngleSpec,
    create_multicam_source,
    flatten_multicam_clip,
    insert_multicam_clip,
    set_audio_policy,
    switch_angle,
)
from core.preview_segments import segment_plan
from core.project_model import Clip, MediaAsset, Project, Track
from core.render_plan import build_render_plan
from core.sequences import create_sequence_from_selection
from core.timeline_operations import trim_clip_right
from core.transitions import add_transition
from core.visual_effects import ClipTransform

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg indisponible")

W, H, FPS = 160, 90, 25
DURATION = 30.0
RED, GREEN, BLUE, YELLOW, BLACK = (255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0), (0, 0, 0)

# nom, couleur ffmpeg, couleur attendue, ton (Hz), cadence, décalage dans la source (s)
CAMERAS = [
    ("camA", "red", RED, 300, 25.0, 0.0),
    ("camB", "lime", GREEN, 500, 29.97, 1.5),
    ("camC", "blue", BLUE, 700, 24.0, 3.0),
    ("camD", "yellow", YELLOW, 900, 25.0, 0.5),
]
RECORDER_HZ = 1100
EXPECTED = {f"angle-{i + 1}": colour for i, (_n, _c, colour, _h, _f, _o) in enumerate(CAMERAS)}


def _run(command: list[str]) -> None:
    done = subprocess.run(command, capture_output=True, text=True, timeout=180)
    assert done.returncode == 0, done.stderr[-1500:]


@pytest.fixture(scope="module")
def shoot(tmp_path_factory):
    """Quatre caméras (image + son) et un enregistreur, chacun avec sa couleur / son identifiants."""
    folder = tmp_path_factory.mktemp("multicam-media")
    assets = []
    for name, colour, _expected, hertz, fps, _offset in CAMERAS:
        path = folder / f"{name}.mp4"
        _run([
            "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c={colour}:s={W}x{H}:r={fps}:d={DURATION}",
            "-f", "lavfi", "-i", f"sine=frequency={hertz}:sample_rate=48000:duration={DURATION}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path),
        ])
        assets.append(MediaAsset(name, str(path), name, DURATION, W, H, fps, "video", True))
    recorder = folder / "recorder.wav"
    _run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
          f"sine=frequency={RECORDER_HZ}:sample_rate=48000:duration={DURATION + 2}", str(recorder)])
    assets.append(MediaAsset("rec", str(recorder), "rec", DURATION + 2, 0, 0, 0.0, "audio", True))
    return assets


def _project(shoot, *, audio: AudioMode = AudioMode.FOLLOW_VIDEO, recorder: bool = True, length: float = 22.0):
    project = Project(
        "Tournage", width=W, height=H, fps=float(FPS), media_assets=list(shoot),
        tracks=[Track("V1", "V1", "video"), Track("V2", "V2", "video"), Track("A1", "A1", "audio")],
    )
    specs = [AngleSpec(asset_id=name, name=name, offset=offset) for name, _c, _e, _h, _f, offset in CAMERAS]
    if recorder:
        specs.append(AngleSpec(asset_id="rec", name="Recorder", offset=0.0))
    source = create_multicam_source(project, specs, name="Concert")
    ids = ("angle-5",) if recorder else ()
    set_audio_policy(project, source.id, audio, ids if audio is AudioMode.FIXED else
                     ("angle-1", "angle-5") if audio is AudioMode.MIX else ())
    segment = insert_multicam_clip(project, source.id, "V1", 0.0, angle_id="angle-1")
    trim_clip_right(project, segment.id, length)
    return project, source, segment


def _export(plan, path: Path) -> Path:
    graph, video, audio, inputs = ExportEngine._build_filter_complex(plan, W, H, FPS, None)
    command = [*_ffmpeg_command_prefix(), "-y", "-v", "error"]
    for item in inputs:
        command += ["-i", item]
    command += ["-filter_complex", graph, "-map", f"[{video}]", "-map", f"[{audio}]", "-c:a", "aac",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)]
    _run(command)
    return path


def _pixel(path, seconds: float, x: int = W // 2, y: int = H // 2) -> tuple[int, int, int]:
    data = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{seconds:.3f}", "-i", str(path), "-frames:v", "1",
         "-vf", f"crop=1:1:{x}:{y},format=rgb24", "-f", "rawvideo", "-"],
        capture_output=True, timeout=60,
    ).stdout
    assert len(data) == 3, f"aucune image lue à {seconds:.2f} s"
    return tuple(data)  # type: ignore[return-value]


def _near(actual, expected, tolerance: int = 45) -> bool:
    return all(abs(a - e) <= tolerance for a, e in zip(actual, expected))


def _dominant(path, start: float, length: float) -> list[float]:
    """Fréquences les plus fortes du son exporté entre ``start`` et ``start + length`` (Hz, de la plus forte à la moins forte)."""
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", str(path), "-vn", "-ac", "1",
         "-ar", "16000", "-f", "f32le", "-"], capture_output=True, timeout=60,
    ).stdout
    samples = np.frombuffer(raw, dtype=np.float32)
    assert samples.size > 8000, "aucun son"
    spectrum = np.abs(np.fft.rfft(samples * np.hanning(samples.size)))
    frequencies = np.fft.rfftfreq(samples.size, 1 / 16000)
    peaks = []
    working = spectrum.copy()
    for _ in range(2):
        index = int(np.argmax(working))
        peaks.append(float(frequencies[index]))
        working[max(0, index - 40):index + 40] = 0.0
    return peaks


def _montage(project, source, segment) -> list[tuple[float, str]]:
    """Dix changements d'angle ; retourne ``(instant, angle)`` de chaque segment du montage (le premier commence à 0)."""
    plan = [(2, 2), (4, 3), (6, 1), (8, 4), (10, 2), (12, 1), (14, 3), (16, 4), (18, 1), (20, 2)]
    for time, number in plan:
        assert switch_angle(project, float(time), f"angle-{number}") is not None
    return [(0.0, "angle-1")] + [(float(t), f"angle-{n}") for t, n in plan]


def _expected_at(montage: list[tuple[float, str]], seconds: float) -> str:
    angle = montage[0][1]
    for start, name in montage:
        if start <= seconds:
            angle = name
    return angle


# --- l'image : seul l'angle actif, au bon instant ------------------------------------------------------------------------------


def test_ten_angle_switches_export_the_expected_camera_at_every_instant(shoot, tmp_path):
    project, source, segment = _project(shoot)
    montage = _montage(project, source, segment)
    plan = build_render_plan(project)
    assert len(plan.video_layers) == 11
    assert len(plan.nested_sequences) == 4          # un sous-plan par angle réellement montré (A, B, C, D), jamais cinq
    out = _export(plan, tmp_path / "montage.mp4")
    for start, angle in montage:
        middle = start + 1.0
        assert _near(_pixel(out, middle), EXPECTED[angle]), (middle, angle, _pixel(out, middle))
    # juste après chaque coupe, c'est déjà le nouvel angle (la coupe est instantanée par défaut)
    for start, angle in montage[1:]:
        assert _near(_pixel(out, start + 0.12), EXPECTED[angle]), (start, angle)


def test_an_angle_that_has_not_started_yet_renders_nothing_instead_of_a_wrong_picture(shoot, tmp_path):
    project, source, segment = _project(shoot, length=8.0)
    assert switch_angle(project, 1.0, "angle-3") is not None      # la caméra C ne démarre qu'à 3 s dans la source
    out = _export(build_render_plan(project), tmp_path / "late.mp4")
    assert _near(_pixel(out, 2.0), BLACK, 30)                      # avant son premier clip : pas de signal, pas d'autre image
    assert _near(_pixel(out, 5.0), BLUE)


def test_preview_segments_render_the_same_picture_as_the_export_across_a_cut(shoot, tmp_path):
    project, source, segment = _project(shoot)
    montage = _montage(project, source, segment)
    exported = _export(build_render_plan(project), tmp_path / "export.mp4")
    segment_file = tmp_path / "segment.mp4"
    plan = segment_plan(project, 5.0, 7.0)                           # contient la coupe à 6 s (angle 3 → angle 1)
    _run(build_preview_command(plan, width=W, height=H, fps=FPS, quality="high", start=5.0, duration=2.0,
                               output_path=str(segment_file)))
    assert {layer.asset_id for entry in plan.nested_sequences for layer in entry.plan.video_layers} == {"camC", "camA"}
    for local, absolute in ((0.4, 5.4), (1.6, 6.6)):
        assert _near(_pixel(segment_file, local), _pixel(exported, absolute), 20), (local, absolute)
    assert _near(_pixel(segment_file, 0.4), EXPECTED[_expected_at(montage, 5.4)])


def test_a_crossfade_between_two_angles_blends_them_with_the_ordinary_transition_engine(shoot, tmp_path):
    project, source, segment = _project(shoot, length=10.0)
    switch_angle(project, 5.0, "angle-2")
    first, second = sorted(project.tracks[0].clips, key=lambda clip: clip.timeline_start)
    add_transition(project, first.id, second.id, duration=1.0)
    out = _export(build_render_plan(project), tmp_path / "fade.mp4")
    mixed = _pixel(out, 4.5)                                          # au milieu du recouvrement (4 s → 5 s)
    assert mixed[0] > 60 and mixed[1] > 60 and mixed[2] < 60, mixed  # ni rouge pur, ni vert pur : un fondu
    assert _near(_pixel(out, 2.0), RED) and _near(_pixel(out, 7.0), GREEN)


def test_an_effect_on_one_camera_is_seen_at_every_appearance_of_that_camera(shoot, tmp_path):
    project, source, segment = _project(shoot, length=12.0)
    switch_angle(project, 3.0, "angle-2")
    switch_angle(project, 6.0, "angle-1")
    switch_angle(project, 9.0, "angle-2")
    source.tracks[1].clips[0].transform = ClipTransform(scale=0.4)        # la caméra B est réduite, une seule fois
    out = _export(build_render_plan(project), tmp_path / "scaled.mp4")
    for centre, corner in ((4.5, 4.5), (10.5, 10.5)):
        assert _near(_pixel(out, centre), GREEN) and _near(_pixel(out, corner, 6, 6), BLACK, 30)
    assert _near(_pixel(out, 1.5, 6, 6), RED) and _near(_pixel(out, 7.5, 6, 6), RED)    # la caméra A, elle, est intacte


def test_a_multicam_segment_inside_a_nested_sequence_exports_like_a_direct_one(shoot, tmp_path):
    project, source, segment = _project(shoot, length=8.0)
    switch_angle(project, 4.0, "angle-3")
    direct = _export(build_render_plan(project), tmp_path / "direct.mp4")
    clips = [clip.id for clip in project.tracks[0].clips]
    create_sequence_from_selection(project, clips, "Scène")
    nested = _export(build_render_plan(project), tmp_path / "nested.mp4")
    for seconds in (1.0, 3.0, 5.0, 7.0):
        assert _near(_pixel(direct, seconds), _pixel(nested, seconds), 20), seconds


def test_flattening_a_segment_keeps_the_exported_picture(shoot, tmp_path):
    project, source, segment = _project(shoot, audio=AudioMode.FOLLOW_VIDEO, length=12.0)
    switch_angle(project, 4.0, "angle-2")
    switch_angle(project, 8.0, "angle-4")
    before = _export(build_render_plan(project), tmp_path / "before.mp4")
    for clip in list(project.tracks[0].clips):
        flatten_multicam_clip(project, clip.id)
    assert not any(clip.sequence_id for clip in project.tracks[0].clips)
    plan = build_render_plan(project)
    assert not plan.nested_sequences
    after = _export(plan, tmp_path / "after.mp4")
    for seconds in (1.0, 3.5, 5.0, 7.5, 9.0, 11.0):
        assert _near(_pixel(before, seconds), _pixel(after, seconds), 25), seconds
    assert _dominant(after, 5.0, 2.0)[0] == pytest.approx(500, abs=15)         # le son de la caméra B a suivi


def test_cameras_at_different_frame_rates_are_converted_without_truncation(shoot, tmp_path):
    project, source, segment = _project(shoot, length=12.0)
    switch_angle(project, 4.0, "angle-2")          # 29,97 i/s dans une séquence à 25 i/s
    switch_angle(project, 8.0, "angle-3")          # 24 i/s
    out = _export(build_render_plan(project), tmp_path / "rates.mp4")
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames", "-show_entries", "stream=nb_read_frames,r_frame_rate",
         "-of", "csv=p=0", str(out)], capture_output=True, text=True, check=True,
    ).stdout.strip().split(",")
    assert probe[0] == "25/1" and int(probe[1]) == pytest.approx(12 * FPS, abs=2)     # 300 images : aucune dérive, aucune image perdue
    assert _near(_pixel(out, 5.0), GREEN) and _near(_pixel(out, 10.0), BLUE)


# --- le son : la politique audio se retrouve dans le fichier exporté ----------------------------------------------------------


def _two_segments(shoot, audio: AudioMode):
    project, source, segment = _project(shoot, audio=audio, length=12.0)
    switch_angle(project, 6.0, "angle-2")
    return project


def test_audio_that_follows_the_picture_changes_with_the_camera(shoot, tmp_path):
    out = _export(build_render_plan(_two_segments(shoot, AudioMode.FOLLOW_VIDEO)), tmp_path / "follow.mp4")
    assert _dominant(out, 1.0, 3.0)[0] == pytest.approx(300, abs=15)
    assert _dominant(out, 8.0, 3.0)[0] == pytest.approx(500, abs=15)


def test_a_fixed_audio_source_keeps_playing_across_every_cut(shoot, tmp_path):
    out = _export(build_render_plan(_two_segments(shoot, AudioMode.FIXED)), tmp_path / "fixed.mp4")
    for start in (1.0, 8.0):
        peaks = _dominant(out, start, 3.0)
        assert peaks[0] == pytest.approx(RECORDER_HZ, abs=15)
        assert all(abs(peaks[0] - camera) > 100 for camera in (300, 500))       # le son des caméras est absent


def test_a_mixed_audio_policy_contains_every_chosen_source(shoot, tmp_path):
    out = _export(build_render_plan(_two_segments(shoot, AudioMode.MIX)), tmp_path / "mix.mp4")
    for start in (1.0, 8.0):
        peaks = sorted(_dominant(out, start, 3.0))
        assert peaks[0] == pytest.approx(300, abs=15) and peaks[1] == pytest.approx(RECORDER_HZ, abs=15)   # A + enregistreur


# --- médias hors ligne -------------------------------------------------------------------------------------------------------


def _request(plan, tmp_path):
    return ExportRequest(render_plan=plan, output_path=str(tmp_path / "x.mp4"), format=ExportFormat.MP4_H264,
                         preset=ExportPreset("T", (W, H), 28, "64k"), fps=FPS)


def test_an_offline_camera_that_is_not_shown_does_not_block_the_export_but_a_shown_one_does(shoot, tmp_path):
    project, source, segment = _project(shoot, length=8.0)
    switch_angle(project, 4.0, "angle-2")
    project.media_assets = [asset for asset in project.media_assets if asset.id != "camD"]   # la caméra D est hors ligne
    plan = build_render_plan(project)
    assert plan.missing_media == ()                                 # D n'est montrée nulle part : rien à signaler
    ExportEngine()._build_command(_request(plan, tmp_path))          # l'export est accepté
    replace_clip = sorted(project.tracks[0].clips, key=lambda c: c.timeline_start)[1]
    replace_clip.angle_id = "angle-4"
    shown = build_render_plan(project)
    assert shown.missing_media == ("camD",)
    with pytest.raises(ValueError, match="Média introuvable.*camD"):
        ExportEngine()._build_command(_request(shown, tmp_path))


def test_the_other_angles_keep_working_when_one_is_offline(shoot, tmp_path):
    project, source, segment = _project(shoot, length=8.0)
    project.media_assets = [asset for asset in project.media_assets if asset.id != "camB"]
    switch_angle(project, 4.0, "angle-3")
    out = _export(build_render_plan(project), tmp_path / "partial.mp4")
    assert _near(_pixel(out, 2.0), RED) and _near(_pixel(out, 6.0), BLUE)
