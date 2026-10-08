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


@pytest.fixture(scope="module")
def untagged_source(tmp_path_factory):
    """Source sans balise de couleur (capture d'écran, vidéo SD, image générée) : FFmpeg la lit en BT.601."""
    path = tmp_path_factory.mktemp("color") / "src_untagged.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=0xDD5C1D:s={W}x{H}:d=2:r={FPS},format=rgb24",
         "-vf", "scale=out_color_matrix=bt601:out_range=tv,format=yuv420p,setparams=colorspace=unknown:range=unknown",
         "-c:v", "libx264", "-crf", "10", str(path)], check=True, timeout=60)
    assert _tags(path) == ("unknown", "unknown", "unknown")
    return str(path)


def _plan(source_path):
    asset = MediaAsset(id="a", path=source_path, name="s", duration=2.0, width=W, height=H, fps=float(FPS),
                       media_type="video", has_audio=False)
    clip = Clip(id="c", asset_id="a", track_id="V1", timeline_start=0.0, source_in=0.0, source_out=2.0)
    project = Project(name="p", width=W, height=H, fps=float(FPS), media_assets=[asset],
                      tracks=[Track(id="V1", name="V1", type="video", clips=[clip])])
    return build_render_plan(project)


def _decoded_as_bt709(path, matrix: str = "bt709") -> tuple[int, int, int]:
    """Pixel central tel que le voit un lecteur qui décode en BT.709 (ou ``matrix``), plage limitée.

    ``full_chroma_int`` comme ``core.hardware_validation`` : sans lui, ``accurate_rnd`` vers ``rgb24`` lit lui-même deux
    niveaux trop sombre (mesuré, FFmpeg 7.1 et 9 : (219, 91, 27) au lieu de (221, 92, 29) pour la source non balisée).
    """
    data = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-vf",
         f"scale=in_color_matrix={matrix}:in_range=tv:flags=accurate_rnd+full_chroma_int,format=rgb24", "-frames:v", "1",
         "-f", "rawvideo", "-"], capture_output=True, timeout=60, check=True).stdout
    offset = ((H // 2) * W + W // 2) * 3
    return tuple(data[offset:offset + 3])


def _tags(path) -> tuple[str, ...]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries",
         "stream=color_space,color_primaries,color_transfer", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, timeout=30, check=True).stdout.strip()
    return tuple(out.split(","))


def _pixel_format(path) -> str:
    return subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "stream=pix_fmt", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, timeout=30, check=True).stdout.strip()


def _export(source_path, tmp_path, fmt, name):
    output = tmp_path / name
    request = ExportRequest(render_plan=_plan(source_path), output_path=str(output), format=fmt,
                            preset=ExportPreset("T", (W, H), 18, "128k"), fps=FPS)
    completed = subprocess.run(ExportEngine()._build_command(request), capture_output=True, text=True, timeout=180)
    assert completed.returncode == 0, completed.stderr[-500:]
    return output


def _close(actual, expected, tolerance=3):
    return all(abs(a - e) <= tolerance for a, e in zip(actual, expected))


FORMATS = [(ExportFormat.MP4_H264, "out.mp4", "yuv420p"), (ExportFormat.MOV_PRORES, "out.mov", "yuv422p10le"),
           (ExportFormat.MOV_H264, "out_h264.mov", "yuv420p")]


@pytest.mark.parametrize("fmt, name, pixel_format", FORMATS)
def test_an_export_keeps_the_colours_of_a_bt709_source(source, tmp_path, fmt, name, pixel_format):
    reference = _decoded_as_bt709(source)
    assert _close(reference, ORIGINAL, 5)                       # la source elle-même est fidèle (±5 : encodage avec perte)
    output = _export(source, tmp_path, fmt, name)
    assert _tags(output) == ("bt709", "bt709", "bt709"), "le flux doit être balisé BT.709"
    actual = _decoded_as_bt709(output)
    assert _close(actual, reference), f"{fmt.name} : export {actual} ≠ source {reference} (avant : (229, 98, 20))"
    # La composition est en RVBA : laissé à la négociation, libx264 recevait du yuv444p (« High 4:4:4 Predictive »,
    # illisible pour QuickTime, Safari, iOS) et ProRes du 4:4:4 sous l'étiquette 422 HQ. Le format est fixé.
    assert _pixel_format(output) == pixel_format
    if fmt is ExportFormat.MOV_PRORES:
        from core.hardware_decoding import codec_class

        assert codec_class("prores", pixel_format) is not None   # réimporté, il garde le décodage matériel validé


@pytest.mark.parametrize("fmt, name, pixel_format", FORMATS)
def test_an_untagged_source_is_not_darkened_by_the_export(untagged_source, tmp_path, fmt, name, pixel_format):
    """Régression : la composition en 4:2:0 lue en BT.601 changeait de matrice YUV → YUV en sortie, ce que swscale
    arrondit vers le sombre (−2 niveaux par canal avec FFmpeg 9, jusqu'à −3,6 avec FFmpeg 7.1 sur arm64). Composée en
    RVBA, la sortie n'a plus qu'une conversion RVB → YUV, exacte : ±1 ici, que l'ancienne sortie dépassait."""
    reference = _decoded_as_bt709(untagged_source, matrix="bt601")
    assert _close(reference, ORIGINAL, 5)
    output = _export(untagged_source, tmp_path, fmt, name)
    actual = _decoded_as_bt709(output)
    assert _close(actual, reference, 1), f"{fmt.name} : export {actual} ≠ source {reference}"
    assert _pixel_format(output) == pixel_format


def test_a_preview_segment_has_the_same_colours_and_tags_as_the_export(source, tmp_path):
    output = tmp_path / "segment.mp4"
    command = build_preview_command(_plan(source), width=W, height=H, fps=FPS, quality="high", start=0.0,
                                    duration=1.0, output_path=str(output))
    completed = subprocess.run(command, capture_output=True, text=True, timeout=180)
    assert completed.returncode == 0, completed.stderr[-500:]
    assert _tags(output) == ("bt709", "bt709", "bt709")
    assert _close(_decoded_as_bt709(output), _decoded_as_bt709(source))


def test_the_graph_alone_tags_the_stream_whatever_the_command_line_options_do(source, tmp_path):
    """Régression macOS : seule la matrice sortait balisée « bt709 » (primaires et transfert « unknown »).

    Les options ``-color_primaries`` / ``-color_trc`` de la ligne de commande ne sont pas appliquées de la même
    façon d'une version de FFmpeg à l'autre. Les propriétés posées sur les images par le graphe, elles, le sont :
    sans aucune option de ligne de commande, le flux doit déjà être balisé BT.709.
    """
    from core.export_engine import OUTPUT_COLOR_TAGS

    request = ExportRequest(render_plan=_plan(source), output_path=str(tmp_path / "graph_only.mp4"),
                            format=ExportFormat.MP4_H264, preset=ExportPreset("T", (W, H), 18, "128k"), fps=FPS)
    command = ExportEngine()._build_command(request)
    tags = list(OUTPUT_COLOR_TAGS)
    start = next(i for i in range(len(command)) if command[i:i + len(tags)] == tags)
    stripped = command[:start] + command[start + len(tags):]
    completed = subprocess.run(stripped, capture_output=True, text=True, timeout=180)
    assert completed.returncode == 0, completed.stderr[-500:]
    assert _tags(tmp_path / "graph_only.mp4") == ("bt709", "bt709", "bt709")
