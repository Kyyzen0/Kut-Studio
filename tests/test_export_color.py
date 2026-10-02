"""Les couleurs exportées sont celles de la source : conversion BT.709 et balises posées.

Régression : l'export convertissait RVB → YUV en BT.601 sans aucune balise. Un lecteur qui décode un
fichier HD comme du BT.709 (tous, pour du HD non balisé) montrait des couleurs décalées : le rouge
(221, 92, 29) devenait (229, 98, 20), soit +11 niveaux. On exporte une vraie source BT.709 puis on décode
l'export comme le ferait un lecteur, en BT.709, et on compare à la source décodée de la même façon.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from core.export_engine import ExportEngine, ExportFormat, ExportPreset, ExportRequest
from core.filter_graph import build_preview_command
from core.project_model import Clip, MediaAsset, Project, Track
from core.render_plan import build_render_plan

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg absent")

W, H, FPS = 1280, 720, 25
ORIGINAL = (221, 92, 29)


@pytest.fixture(scope="module")
def source(tmp_path_factory):
    path = tmp_path_factory.mktemp("color") / "src709.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=0xDD5C1D:s={W}x{H}:d=2:r={FPS},format=rgb24",
         "-vf", "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p", "-color_primaries", "bt709",
         "-color_trc", "bt709", "-colorspace", "bt709", "-color_range", "tv", "-c:v", "libx264", "-crf", "10",
         str(path)], check=True, timeout=60)
    return str(path)


def _plan(source_path):
    asset = MediaAsset(id="a", path=source_path, name="s", duration=2.0, width=W, height=H, fps=float(FPS),
                       media_type="video", has_audio=False)
    clip = Clip(id="c", asset_id="a", track_id="V1", timeline_start=0.0, source_in=0.0, source_out=2.0)
    project = Project(name="p", width=W, height=H, fps=float(FPS), media_assets=[asset],
                      tracks=[Track(id="V1", name="V1", type="video", clips=[clip])])
    return build_render_plan(project)


def _decoded_as_bt709(path) -> tuple[int, int, int]:
    """Pixel central tel que le voit un lecteur qui décode en BT.709, plage limitée."""
    data = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-vf",
         "scale=in_color_matrix=bt709:in_range=tv:flags=accurate_rnd,format=rgb24", "-frames:v", "1",
         "-f", "rawvideo", "-"], capture_output=True, timeout=60, check=True).stdout
    offset = ((H // 2) * W + W // 2) * 3
    return tuple(data[offset:offset + 3])


def _tags(path) -> tuple[str, ...]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries",
         "stream=color_space,color_primaries,color_transfer", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, timeout=30, check=True).stdout.strip()
    return tuple(out.split(","))


def _export(source_path, tmp_path, fmt, name):
    output = tmp_path / name
    request = ExportRequest(render_plan=_plan(source_path), output_path=str(output), format=fmt,
                            preset=ExportPreset("T", (W, H), 18, "128k"), fps=FPS)
    completed = subprocess.run(ExportEngine()._build_command(request), capture_output=True, text=True, timeout=180)
    assert completed.returncode == 0, completed.stderr[-500:]
    return output


def _close(actual, expected, tolerance=3):
    return all(abs(a - e) <= tolerance for a, e in zip(actual, expected))


@pytest.mark.parametrize("fmt, name", [(ExportFormat.MP4_H264, "out.mp4"), (ExportFormat.MOV_PRORES, "out.mov"),
                                       (ExportFormat.MOV_H264, "out_h264.mov")])
def test_an_export_keeps_the_colours_of_a_bt709_source(source, tmp_path, fmt, name):
    reference = _decoded_as_bt709(source)
    assert _close(reference, ORIGINAL, 5)                       # la source elle-même est fidèle (±5 : encodage avec perte)
    output = _export(source, tmp_path, fmt, name)
    assert _tags(output) == ("bt709", "bt709", "bt709"), "le flux doit être balisé BT.709"
    actual = _decoded_as_bt709(output)
    assert _close(actual, reference), f"{fmt.name} : export {actual} ≠ source {reference} (avant : (229, 98, 20))"


def test_a_preview_segment_has_the_same_colours_and_tags_as_the_export(source, tmp_path):
    output = tmp_path / "segment.mp4"
    command = build_preview_command(_plan(source), width=W, height=H, fps=FPS, quality="high", start=0.0,
                                    duration=1.0, output_path=str(output))
    completed = subprocess.run(command, capture_output=True, text=True, timeout=180)
    assert completed.returncode == 0, completed.stderr[-500:]
    assert _tags(output) == ("bt709", "bt709", "bt709")
    assert _close(_decoded_as_bt709(output), _decoded_as_bt709(source))
