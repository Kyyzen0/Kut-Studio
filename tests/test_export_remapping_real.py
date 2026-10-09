"""Export réel (FFmpeg) du remappage temporel : on lit les COULEURS montrées à des instants précis.

Régression : la vitesse ≠ 1 branchait ``atempo`` (filtre audio) dans la chaîne vidéo et l'arrêt sur image
produisait ``select=eq(n,75)`` (virgule non échappée, « No such filter: '75)' ») : FFmpeg refusait le
graphe, donc l'export entier échouait. Les anciens tests comparaient des chaînes de caractères sans jamais
lancer FFmpeg. La source dure 6 s : rouge 0-2 s, bleu 2-4 s, vert 4-6 s.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from core.export_engine import ExportEngine, input_arguments
from core.project_model import Clip, MediaAsset, Project, Track
from core.render_plan import build_render_plan
from core.time_remapping import FreezeFrameMode, TimeRemapping
from core.timeline_operations import trim_clip_left

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg absent")

W, H, FPS = 160, 90, 25


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    path = tmp_path_factory.mktemp("remap") / "rgb.mp4"
    inputs = []
    for color in ("red", "blue", "green"):
        inputs += ["-f", "lavfi", "-i", f"color=c={color}:s=64x64:d=2:r={FPS}"]
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", "[0][1][2]concat=n=3:v=1:a=0,format=yuv420p",
         "-c:v", "libx264", "-crf", "10", str(path)],
        check=True, timeout=60,
    )
    return str(path)


def _project(media_path, *, source_in=0.0, source_out=6.0, remapping=None):
    asset = MediaAsset(id="a", path=media_path, name="rgb", duration=6.0, width=64, height=64, fps=float(FPS),
                       media_type="video", has_audio=False)
    clip = Clip(id="c1", asset_id="a", track_id="V1", timeline_start=0.0, source_in=source_in,
                source_out=source_out, time_remapping=remapping or TimeRemapping())
    project = Project(name="p", width=W, height=H, fps=float(FPS), media_assets=[asset],
                      tracks=[Track(id="V1", name="V1", type="video", clips=[clip])])
    return project, clip


def _color_at(project, t: float, dx: int = 0) -> str:
    """Couleur dominante (R, G, B, noir ou ?) du pixel du centre (décalé de ``dx``) à l'instant ``t``."""
    plan = build_render_plan(project)
    graph, video, audio, inputs = ExportEngine._build_filter_complex(plan, W, H, FPS, None)
    graph += f";[{video}]trim=start={t},setpts=PTS-STARTPTS,format=rgb24[probe];[{audio}]anullsink"
    command = ["ffmpeg", "-y", "-loglevel", "error"]
    for path in inputs:
        command += input_arguments(path)
    command += ["-filter_complex", graph, "-map", "[probe]", "-frames:v", "1", "-fps_mode", "passthrough", "-f", "rawvideo", "-"]
    completed = subprocess.run(command, capture_output=True, timeout=60)
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")[-600:]
    data = completed.stdout
    assert len(data) >= W * H * 3, f"aucune image à t={t}"
    offset = ((H // 2) * W + W // 2 + dx) * 3
    r, g, b = data[offset], data[offset + 1], data[offset + 2]
    if r > 150 and g < 100 and b < 100:
        return "R"
    if g > 100 and r < 100 and b < 100:
        return "G"
    if b > 150 and r < 100 and g < 100:
        return "B"
    if max(r, g, b) < 40:
        return "noir"
    return f"?{(r, g, b)}"


def _colors(project, instants):
    return [_color_at(project, t) for t in instants]


def test_normal_playback_is_the_baseline(media):
    project, _ = _project(media)
    assert _colors(project, (0.5, 2.5, 4.5)) == ["R", "B", "G"]


def test_speed_x2_plays_twice_as_fast(media):
    project, clip = _project(media, remapping=TimeRemapping(speed=2.0))
    assert clip.duration == pytest.approx(3.0)
    assert _colors(project, (0.5, 1.5, 2.5)) == ["R", "B", "G"]


def test_speed_x4_chains_without_audio_filters(media):
    project, clip = _project(media, remapping=TimeRemapping(speed=4.0))
    assert clip.duration == pytest.approx(1.5)
    assert _colors(project, (0.2, 0.75, 1.3)) == ["R", "B", "G"]


def test_slow_motion_stretches_each_colour(media):
    project, clip = _project(media, remapping=TimeRemapping(speed=0.5))
    assert clip.duration == pytest.approx(12.0)
    assert _colors(project, (2.0, 6.0, 10.0)) == ["R", "B", "G"]


def test_reverse_plays_backwards(media):
    project, _ = _project(media, remapping=TimeRemapping(reverse=True))
    assert _colors(project, (1.0, 3.0, 5.0)) == ["G", "B", "R"]


def test_reverse_and_speed_combine(media):
    project, _ = _project(media, remapping=TimeRemapping(speed=2.0, reverse=True))
    assert _colors(project, (0.5, 1.5, 2.5)) == ["G", "B", "R"]


def test_a_freeze_frame_holds_one_image_for_its_whole_duration(media):
    freeze = TimeRemapping(freeze_mode=FreezeFrameMode.FREEZE, freeze_source_time=3.0, freeze_duration=2.0)
    project, clip = _project(media, remapping=freeze)
    assert clip.duration == pytest.approx(2.0)
    assert _colors(project, (0.1, 1.0, 1.9)) == ["B", "B", "B"]


def test_a_freeze_frame_counts_its_instant_from_the_clip_in_point(media):
    """Avant : l'image était cherchée à ``instant × fps`` depuis le début du flux déjà rogné."""
    freeze = TimeRemapping(freeze_mode=FreezeFrameMode.FREEZE, freeze_source_time=5.0, freeze_duration=1.0)
    project, _ = _project(media, source_in=2.0, source_out=6.0, remapping=freeze)
    assert _colors(project, (0.1, 0.9)) == ["G", "G"]


def test_a_trim_after_a_speed_change_keeps_the_frame_shown_at_each_instant(media):
    """Trim (corrigé) puis export : la couleur à un instant donné de la timeline ne change pas."""
    project, clip = _project(media, remapping=TimeRemapping(speed=2.0))
    before = _colors(project, (1.5, 2.5))                       # B puis G
    trim_clip_left(project, "c1", 1.2)
    assert before == ["B", "G"]
    assert _colors(project, (1.5, 2.5)) == before


@pytest.mark.parametrize("frozen", [False, True], ids=["normal", "freeze"])
def test_the_clip_scale_applies_to_normal_and_frozen_clips_alike(media, frozen):
    """La branche freeze ignorait l'échelle (et la rotation) du clip.

    Cadre 160×90, source 64×64 ajustée à 90×90 (x de 35 à 125). À l'échelle 1, le pixel x = 80 + 30
    est coloré ; à l'échelle 0,5 l'image n'occupe plus que x de 57 à 102 : il doit être noir.
    """
    from core.visual_effects import ClipTransform

    remapping = (TimeRemapping(freeze_mode=FreezeFrameMode.FREEZE, freeze_source_time=3.0, freeze_duration=2.0)
                 if frozen else TimeRemapping())
    project, clip = _project(media, remapping=remapping)
    assert _color_at(project, 0.5, dx=30) != "noir"                  # échelle 1 : l'image couvre ce pixel
    clip.transform = ClipTransform(scale=0.5)
    assert _color_at(project, 0.5, dx=30) == "noir"                  # échelle 0,5 : elle ne le couvre plus
    assert _color_at(project, 0.5, dx=0) != "noir"                   # le centre reste coloré
