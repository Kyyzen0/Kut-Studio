"""L'image extraite à la tête de lecture est la bonne : position, vitesse, point d'entrée, sens, pistes.

Régression : ``build_frame_command`` posait ``-ss`` sur la première entrée en temps de *timeline*, ce qui
n'est juste que pour un clip à 0 s, à vitesse ×1, sans point d'entrée. Partout ailleurs les scopes
analysaient une image fausse ou noire. Deux chemins sont comparés à la couleur attendue :

- exact mais lent (coût O(T)) : plan complet + rognage de la sortie ;
- rapide, celui des scopes : projet ramené à l'origine à la tête de lecture, première image.

Source : rouge 0-2 s, bleu 2-4 s, vert 4-6 s.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest
from PySide6.QtGui import QImage

from core.export_engine import ExportEngine, ExportFormat, ExportPreset, ExportRequest
from core.playhead_snapshot import project_at_playhead
from core.project_model import Clip, MediaAsset, Project, Track
from core.render_plan import build_render_plan
from core.time_remapping import TimeRemapping

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg absent")

W, H, FPS = 160, 90, 25


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    path = tmp_path_factory.mktemp("playhead") / "rgb.mp4"
    inputs = []
    for color in ("red", "blue", "green"):
        inputs += ["-f", "lavfi", "-i", f"color=c={color}:s=64x64:d=2:r={FPS}"]
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", "[0][1][2]concat=n=3:v=1:a=0,format=yuv420p",
         "-c:v", "libx264", "-crf", "10", str(path)], check=True, timeout=60)
    return str(path)


def _clip(clip_id, track, start, source_in, source_out, **remap):
    return Clip(id=clip_id, asset_id="a", track_id=track, timeline_start=start, source_in=source_in,
                source_out=source_out, time_remapping=TimeRemapping(**remap))


def _project(media_path, clips):
    asset = MediaAsset(id="a", path=media_path, name="rgb", duration=6.0, width=64, height=64, fps=float(FPS),
                       media_type="video", has_audio=False)
    tracks = [Track(id="V1", name="V1", type="video"), Track(id="V2", name="V2", type="video")]
    for clip in clips:
        next(t for t in tracks if t.id == clip.track_id).clips.append(clip)
    return Project(name="p", width=W, height=H, fps=float(FPS), media_assets=[asset], tracks=tracks)


def _request(plan, tmp_path):
    return ExportRequest(render_plan=plan, output_path=str(tmp_path / "x.mp4"), format=ExportFormat.MP4_H264,
                         preset=ExportPreset("T", (W, H), 28, "64k"), fps=FPS)


def _color(command) -> str:
    completed = subprocess.run(command, capture_output=True, timeout=60)
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")[-500:]
    image = QImage()
    assert image.loadFromData(completed.stdout), "PNG illisible"
    pixel = image.pixelColor(W // 2, H // 2)
    r, g, b = pixel.red(), pixel.green(), pixel.blue()
    if r > 150 and g < 100 and b < 100:
        return "R"
    if g > 100 and r < 100 and b < 100:
        return "G"
    if b > 150 and r < 100 and g < 100:
        return "B"
    return "noir" if max(r, g, b) < 40 else f"?{(r, g, b)}"


# (nom, clips, [(instant, couleur attendue)])
CASES = [
    ("clip à 2 s", [_clip("c", "V1", 2.0, 0.0, 6.0)], [(2.5, "R"), (4.5, "B"), (6.5, "G")]),
    ("point d'entrée à 2 s", [_clip("c", "V1", 1.0, 2.0, 6.0)], [(1.5, "B"), (4.0, "G")]),
    ("vitesse ×2", [_clip("c", "V1", 0.0, 0.0, 6.0, speed=2.0)], [(0.5, "R"), (1.5, "B"), (2.5, "G")]),
    ("à l'envers", [_clip("c", "V1", 0.0, 0.0, 6.0, reverse=True)], [(0.5, "G"), (2.5, "B"), (5.5, "R")]),
    ("à l'envers ×2 à 1 s", [_clip("c", "V1", 1.0, 0.0, 6.0, reverse=True, speed=2.0)],
     [(1.5, "G"), (2.5, "B"), (3.5, "R")]),
    ("deux pistes", [_clip("base", "V1", 0.0, 0.0, 6.0), _clip("top", "V2", 3.0, 2.0, 4.0)],
     [(1.0, "R"), (3.5, "B"), (5.5, "G")]),
]


@pytest.mark.parametrize("name, clips, expectations", CASES, ids=[c[0] for c in CASES])
def test_the_extracted_frame_is_the_one_shown_at_the_playhead(media, tmp_path, name, clips, expectations):
    project = _project(media, [Clip(**{**vars(c)}) for c in clips])
    full_plan = build_render_plan(project)
    for playhead, expected in expectations:
        slow = _color(ExportEngine().build_frame_command(_request(full_plan, tmp_path), playhead))
        snapshot = build_render_plan(project_at_playhead(project, playhead))
        fast = _color(ExportEngine().build_frame_command(_request(snapshot, tmp_path), 0.0))
        assert (slow, fast) == (expected, expected), f"{name} à {playhead} s : exact={slow}, rapide={fast}"


def test_the_snapshot_never_modifies_the_project(media):
    project = _project(media, [_clip("c", "V1", 2.0, 0.0, 6.0, speed=2.0)])
    before = (project.tracks[0].clips[0].timeline_start, project.tracks[0].clips[0].source_in)
    project_at_playhead(project, 3.0)
    assert (project.tracks[0].clips[0].timeline_start, project.tracks[0].clips[0].source_in) == before


def test_the_snapshot_keeps_only_what_is_visible_around_the_playhead(media):
    clips = [_clip("before", "V1", 0.0, 0.0, 1.0), _clip("now", "V1", 1.0, 0.0, 4.0),
             _clip("soon", "V2", 5.0, 0.0, 1.0), _clip("far", "V2", 30.0, 0.0, 1.0)]
    snapshot = project_at_playhead(_project(media, clips), 2.0, span=2.0)
    kept = {c.id: c.timeline_start for track in snapshot.tracks for c in track.clips}
    assert kept == {"now": 0.0}      # « soon » commence à 5 s : au-delà de 2 + 2 s ; « before » est fini
