"""Calques graphiques limités à leurs images visibles, images de calques encodées en parallèle, cadence des listes.

Un élément affiché 0,8 s sur 31 s était composé, image transparente après image transparente, pendant tout le rendu
(mesuré sur un montage social de 31 s : 7 flashs en Addition = 104 s de CPU et 6,7 Go de mémoire pour 4 s d'images).
Il n'entre plus dans le graphe que pendant ses images visibles ; les images composées restent celles du flux complet,
au bit près, ce que vérifient ces tests avec le vrai FFmpeg.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest
from PySide6.QtGui import QColor, QImage

from core import mograph_ffmpeg, mograph_stream
from core.blend_modes import BlendMode
from core.compositing import Compositing
from core.export_engine import ExportEngine, input_arguments
from core.graphics import add_graphic_clip, update_graphic
from core.mograph_raster import MographRenderer
from core.project_model import Clip, MediaAsset, Project, Track
from core.render_plan import build_render_plan
from core.text_animations import apply_text_animation

HAS_FFMPEG = shutil.which("ffmpeg") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="FFmpeg absent : rendu réel impossible")

W, H, FPS, LENGTH = 160, 90, 10, 6.0


def _shape(project: Project, start: float, duration: float, color: str, mode: BlendMode):
    shape = add_graphic_clip(project, "shape", timeline_start=start, duration=duration)
    update_graphic(shape, "fill_color", color)
    shape.compositing = Compositing(blend_mode=mode)
    return shape


def _project(tmp_path: Path) -> Project:
    """Vidéo de fond, puis des éléments courts : au début, au milieu, à la fin de la composition, en Normal et en fusion."""
    project = Project(name="Plages", width=W, height=H, fps=float(FPS))
    source = tmp_path / "fond.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"testsrc2=s={W}x{H}:r={FPS}:d={LENGTH}", "-pix_fmt", "yuv420p", str(source)], check=True)
    project.media_assets.append(MediaAsset("a", str(source), "fond", LENGTH, W, H, FPS, "video"))
    project.tracks.append(Track("V1", "V1", "video", clips=[Clip("v", "a", "V1", 0.0, 0.0, LENGTH)]))
    _shape(project, 0.0, 0.8, "#E0A040", BlendMode.MULTIPLY)        # dès la première image
    title = add_graphic_clip(project, "text", timeline_start=1.0, duration=1.0)
    update_graphic(title, "text", "Kut")
    update_graphic(title, "font_size", 24)
    apply_text_animation(title, "bounce")                          # une image différente à chaque instant
    _shape(project, 2.5, 0.7, "#3060F0", BlendMode.ADD)             # au milieu
    _shape(project, 3.3, 0.4, "#20C080", BlendMode.NORMAL)
    _shape(project, 5.0, 1.0, "#F04080", BlendMode.SCREEN)          # jusqu'à la dernière image
    return project


def _frames(plan, *, origin: float = 0.0) -> list[tuple[str, str]]:
    """``(horodatage, empreinte)`` de chaque image composée (RVBA), à partir de ``origin``."""
    graph, video, audio, inputs = ExportEngine._build_filter_complex(plan, W, H, FPS, None, origin=origin)
    command = ["ffmpeg", "-v", "error"]
    for path in inputs:
        command += input_arguments(path)
    # Le son part vers une seconde sortie : FFmpeg 7.x avorte quand ``anullsink`` vide une source interne (silence).
    command += ["-filter_complex", f"{graph};[{video}]format=rgba[probe]", "-map", "[probe]",
                "-fps_mode", "passthrough", "-f", "framemd5", "-", "-map", f"[{audio}]", "-f", "null", "-"]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=120)
    assert completed.returncode == 0, completed.stderr
    rows = [line.split(",") for line in completed.stdout.splitlines() if line and line[0] != "#"]
    return [(row[2].strip(), row[5].strip()) for row in rows]


@needs_ffmpeg
@pytest.mark.parametrize("origin", [0.0, 2.0], ids=["export", "segment"])
def test_limited_elements_compose_the_frames_of_the_full_streams_bit_for_bit(tmp_path, monkeypatch, origin):
    plan = build_render_plan(_project(tmp_path))
    limited = _frames(plan, origin=origin)
    monkeypatch.setattr(mograph_ffmpeg, "LIMIT_TO_VISIBLE_SPAN", False)
    full = _frames(plan, origin=origin)
    assert len(limited) == round((LENGTH - origin) * FPS)
    assert limited == full                                      # mêmes images, mêmes horodatages


def test_a_short_element_enters_the_graph_only_while_visible(tmp_path):
    project = Project(name="Titre", width=W, height=H, fps=float(FPS))
    _shape(project, 2.5, 0.7, "#3060F0", BlendMode.NORMAL)
    _shape(project, 1.0, 1.0, "#3060F0", BlendMode.ADD)
    graph, _video, _audio, inputs = ExportEngine._build_filter_complex(build_render_plan(project), W, H, FPS, None)
    # Ses 7 images, posées à la 25ᵉ, au lieu d'un flux transparent de 60 images.
    assert "trim=end_frame=7,setpts=PTS-STARTPTS+25" in graph
    playlists = [Path(path).read_text() for path in inputs if path.endswith(".ffconcat")]
    assert all("blank-" not in text for text in playlists)
    # La fusion ne voit que les images du dessous où le calque est visible ; ailleurs des images transparentes
    # dessinées une fois la complètent, et ``overlay`` laisse passer le dessous sans le parcourir.
    assert "trim=start_frame=10:end_frame=20,format=gbrp" in graph
    assert "trim=end_frame=10,setsar=1" in graph and "concat=n=3:v=1:a=0" in graph
    assert "enable='between(t,0.95,1.95)'" in graph


def test_an_element_that_is_never_visible_adds_nothing_to_the_graph(tmp_path, monkeypatch):
    project = Project(name="Vide", width=W, height=H, fps=float(FPS))
    _shape(project, 1.0, 1.0, "#3060F0", BlendMode.ADD)
    monkeypatch.setattr(MographRenderer, "any_active", lambda self, ids, t: False)
    graph, _video, _audio, inputs = ExportEngine._build_filter_complex(build_render_plan(project), W, H, FPS, None)
    assert not [path for path in inputs if path.endswith(".ffconcat")]
    assert "blend=" not in graph


def _numbered(t: float) -> QImage:
    """Une couleur différente par image (``t`` en dixièmes ou trentièmes de seconde)."""
    index = round(t * 30)
    image = QImage(8, 8, QImage.Format_ARGB32_Premultiplied)
    image.fill(QColor(index * 7 % 256, index * 13 % 256, 200, 255))
    return image


@needs_ffmpeg
def test_every_frame_of_a_30fps_animation_reaches_ffmpeg(tmp_path, monkeypatch):
    """Une image PNG s'ouvre à 25 i/s : sans cadence déclarée, deux images à 30 i/s tombaient sur le même tic de 40 ms
    et l'animation sautait une image sur six (vu à l'export : images 93, 99, 105 doublées)."""
    monkeypatch.setattr(mograph_stream, "cache_directory", lambda: tmp_path)
    playlist = mograph_stream.write_stream(width=8, height=8, fps=30.0, duration=1.0, start=0.0, end=1.0,
                                           frame_key=lambda t: round(t * 30), render=_numbered)
    assert "option framerate 30" in Path(playlist).read_text()
    completed = subprocess.run(
        ["ffmpeg", "-v", "error", *input_arguments(playlist), "-vf", mograph_stream.stream_input_filter(30, 1.0),
         "-fps_mode", "passthrough", "-f", "framemd5", "-"], capture_output=True, text=True, timeout=60)
    assert completed.returncode == 0, completed.stderr
    digests = [line.rsplit(",", 1)[-1] for line in completed.stdout.splitlines() if line and line[0] != "#"]
    assert len(digests) == 30
    assert len(set(digests)) == 30                             # aucune image sautée ni doublée


def test_only_layer_playlists_get_the_concat_options():
    assert input_arguments("/cache/mograph/s-1.ffconcat")[-4:] == ["-safe", "0", "-i", "/cache/mograph/s-1.ffconcat"]
    assert input_arguments("/media/clip.mp4") == ["-i", "/media/clip.mp4"]


def test_parallel_png_writes_produce_the_bytes_of_a_serial_write(tmp_path, monkeypatch):
    def write(directory: Path) -> dict[str, str]:
        directory.mkdir()
        monkeypatch.setattr(mograph_stream, "cache_directory", lambda: directory)
        mograph_stream.write_stream(width=8, height=8, fps=30.0, duration=1.0, start=0.0, end=1.0,
                                    frame_key=lambda t: round(t * 30), render=_numbered)
        return {path.name: hashlib.sha1(path.read_bytes()).hexdigest() for path in directory.iterdir()}

    monkeypatch.setattr(mograph_stream, "WRITE_WORKERS", 1)
    serial = write(tmp_path / "serie")
    monkeypatch.setattr(mograph_stream, "WRITE_WORKERS", 4)
    parallel = write(tmp_path / "parallele")
    assert len(serial) == 32                                   # 30 images, l'image vide, la liste
    assert parallel == serial


def test_a_failed_png_write_is_reported_after_the_others_finish(tmp_path, monkeypatch):
    monkeypatch.setattr(mograph_stream, "cache_directory", lambda: tmp_path)
    monkeypatch.setattr(mograph_stream, "WRITE_WORKERS", 4)
    original = mograph_stream._write_png

    def failing(image, target):
        if target.name.startswith("f-") and image.pixelColor(0, 0).red() == 7 * 3 % 256:
            raise OSError("disque plein")
        original(image, target)

    monkeypatch.setattr(mograph_stream, "_write_png", failing)
    with pytest.raises(OSError, match="disque plein"):
        mograph_stream.write_stream(width=8, height=8, fps=30.0, duration=1.0, start=0.0, end=1.0,
                                    frame_key=lambda t: round(t * 30), render=_numbered)
    assert not list(tmp_path.glob("*.ffconcat"))               # pas de liste vers des images manquantes


def test_several_media_share_the_decoding_threads(monkeypatch):
    from core.export_engine import decoder_threads

    monkeypatch.setattr("os.cpu_count", lambda: 10)
    assert decoder_threads(["/media/a.mp4"]) == 0                       # un média seul : réglage de FFmpeg
    assert decoder_threads(["/media/a.mp4", "/media/b.wav"]) == 5
    assert decoder_threads([f"/media/{index}.mp4" for index in range(19)]) == 2
    assert decoder_threads(["/media/a.mp4", "/cache/s-1.ffconcat"]) == 0  # les listes d'images ont leurs 2 fils
    assert input_arguments("/media/a.mp4", threads=2) == ["-threads", "2", "-i", "/media/a.mp4"]
