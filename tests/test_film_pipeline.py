"""Façade ``Film`` avec le vrai FFmpeg : plan ProRes 4444 transparent, projet sans changement de format, export MP4 réel."""

from __future__ import annotations

import json
import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np
import pytest

from core.film import Film, FilmError
from core.project_io import load_project, save_project
from tests.kut3d_scenes import WHITE_AMBIENT, flat, front_camera, quad_model, scene

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="FFmpeg absent"
)

SIZE = (64, 64)
FPS = 6.0
BLUE = (0.0, 0.0, 1.0)


def quad_scene(color=(1.0, 0.0, 0.0, 1.0), half=0.4):
    """Un carré de couleur unie au centre de l'image, le reste transparent."""
    return scene(quad_model(flat(color), half=half), [WHITE_AMBIENT], front_camera())


def probe(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-print_format", "json", str(path)],
        capture_output=True, text=True, check=True,
    )
    return json.loads(out.stdout)


def first_frame(path: Path, size: tuple[int, int]) -> np.ndarray:
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"],
        capture_output=True, check=True,
    )
    return np.frombuffer(out.stdout, np.uint8).reshape(size[1], size[0], 3)


def write_wav(path: Path, seconds: float = 2.0) -> Path:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        t = np.arange(int(8000 * seconds)) / 8000
        handle.writeframes((np.sin(2 * np.pi * 440 * t) * 8000).astype("<i2").tobytes())
    return path


def make_film(tmp_path: Path, size=SIZE, fps=FPS) -> Film:
    return Film(tmp_path / "out" / "film.kut", size=size, fps=fps)


def tree(folder: Path) -> list[str]:
    return sorted(str(p.relative_to(folder)) for p in folder.rglob("*"))


def test_render_shot_is_a_prores_4444_video_with_alpha(tmp_path):
    film = make_film(tmp_path)
    shot = film.render_shot(quad_scene(), duration=1.0, name="plan1", backend="numpy")
    assert (shot.frames, shot.duration, shot.size, shot.fps) == (6, 1.0, SIZE, FPS)
    assert shot.path == (film.assets_dir / "plan1.mov").resolve()
    info = probe(shot.path)
    stream = info["streams"][0]
    assert stream["codec_name"] == "prores" and "yuva" in stream["pix_fmt"]
    assert (int(stream["nb_frames"]), stream["width"], stream["height"]) == (6, 64, 64)
    assert stream.get("color_space", "bt709") == "bt709"        # FFmpeg 7.1 ne relit pas l'étiquette d'un ProRes : alors absente
    assert tree(tmp_path / "out") == ["film_assets", "film_assets/plan1.mov"]      # PNG intermédiaires supprimés, rien d'autre


def test_keep_frames_keeps_the_png_files(tmp_path):
    film = make_film(tmp_path)
    film.render_shot(quad_scene(), duration=1.0, name="plan1", backend="numpy", keep_frames=True)
    assert len(list((film.assets_dir / "plan1").glob("frame_*.png"))) == 6


def test_alpha_reaches_the_exported_mp4_and_matrix_is_right(tmp_path):
    """Le canal alpha traverse le graphe d'export (le fond se voit) et la couleur n'est pas décalée à 720 lignes."""
    film = make_film(tmp_path, size=(1280, 720), fps=2.0)
    film.background(BLUE)
    green = (0.2, 0.9, 0.3, 1.0)
    shot = film.render_shot(quad_scene(green), duration=1.0, name="plan1", backend="numpy", keep_frames=True)
    film.place(shot)
    from kut3d.render.png import decode_png

    rendered = decode_png((film.assets_dir / "plan1" / "frame_000001.png").read_bytes())
    assert rendered[10, 10, 3] == 0 and rendered[360, 640, 3] == 255            # le plan a bien un fond transparent
    out = film.export(tmp_path / "out" / "film.mp4")
    frame = first_frame(out, (1280, 720))
    assert np.abs(frame[10, 10].astype(int) - (0, 0, 255)).max() <= 6          # transparent : le fond bleu
    assert np.abs(frame[360, 640].astype(int) - rendered[360, 640, :3]).max() <= 8     # le carré, dans la couleur rendue
    assert probe(out)["streams"][0]["codec_name"] == "h264"


def test_project_is_saved_in_the_ordinary_format_version(tmp_path):
    from core.project_model import Project, Track
    from core.timeline_operations import add_clip_to_track
    from core.media_probe import probe_media

    film = make_film(tmp_path)
    film.background(BLUE)
    shot = film.render_shot(quad_scene(), duration=1.0, name="plan1", backend="numpy")
    film.place(shot, start=0.5)
    saved = film.save()
    version = json.loads(saved.read_text(encoding="utf-8"))["version"]

    by_hand = Project(name="main", width=64, height=64, fps=FPS, tracks=[Track("V1", "V1", "video"), Track("V2", "V2", "video")])
    asset = probe_media(str(shot.path))
    asset.id = "a"
    by_hand.media_assets.append(asset)
    add_clip_to_track(by_hand, "a", "V2", 0.5)
    save_project(by_hand, str(tmp_path / "hand.kut"))
    assert version == json.loads((tmp_path / "hand.kut").read_text(encoding="utf-8"))["version"]


def test_saved_project_reloads_with_the_expected_clips(tmp_path):
    film = make_film(tmp_path)
    film.background(BLUE)
    shot = film.render_shot(quad_scene(), duration=1.0, name="plan1", backend="numpy")
    first = film.place(shot, start=0.0)
    second = film.place(shot, start=1.0)
    sound = film.audio(write_wav(tmp_path / "musique.wav", 5.0), start=0.5, gain_db=-6.0)
    project = load_project(str(film.save()))
    assert (project.width, project.height, project.fps) == (64, 64, FPS)
    by_track = {track.id: track for track in project.tracks}
    assert list(by_track) == ["V1", "V2", "A1"]
    (back,) = by_track["V1"].clips
    assert (back.id, back.timeline_start, back.source_out) == ("background", 0.0, 2.0)
    assert [(c.id, c.timeline_start, c.source_out) for c in by_track["V2"].clips] == [(first, 0.0, 1.0), (second, 1.0, 1.0)]
    (music,) = by_track["A1"].clips
    assert (music.id, music.timeline_start, music.gain_db) == (sound, 0.5, -6.0)
    assert music.source_out == pytest.approx(1.5)               # coupée à la fin du film (2 s), jamais au-delà
    assert {a.id for a in project.media_assets} == {"background", "shot_plan1", "audio_1"}
    assert film.duration == 2.0


def test_full_pipeline_with_audio_and_a_known_preset(tmp_path):
    film = make_film(tmp_path)
    film.background(BLUE)
    film.place(film.render_shot(quad_scene(), duration=1.0, name="plan1", backend="numpy"))
    film.audio(write_wav(tmp_path / "musique.wav", 3.0))
    out = film.export(tmp_path / "out" / "film.mp4", preset="youtube")
    streams = {s["codec_type"]: s for s in probe(out)["streams"]}
    assert (streams["video"]["width"], streams["video"]["height"]) == (1920, 1080)
    assert "audio" in streams
    assert float(probe(out)["format"]["duration"]) == pytest.approx(1.0, abs=0.2)
    assert tree(tmp_path / "out") == ["film.mp4", "film_assets", "film_assets/background.mp4", "film_assets/plan1.mov"]


def test_image_background_is_covered_to_the_frame(tmp_path):
    from kut3d.render.png import encode_png

    picture = np.zeros((40, 80, 4), np.uint8)
    picture[...] = (0, 200, 0, 255)
    image = tmp_path / "fond.png"
    image.write_bytes(encode_png(picture))
    film = make_film(tmp_path)
    film.background(image)
    film.place(film.render_shot(quad_scene(), duration=1.0, name="plan1", backend="numpy"))
    out = film.export(tmp_path / "out" / "film.mp4")
    assert np.abs(first_frame(out, SIZE)[3, 3].astype(int) - (0, 200, 0)).max() <= 8


def test_too_short_video_background_is_refused(tmp_path):
    short = tmp_path / "court.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=64x64:r=6:d=0.5", str(short)], check=True
    )
    film = make_film(tmp_path)
    film.background(short)
    film.place(film.render_shot(quad_scene(), duration=1.0, name="plan1", backend="numpy"))
    with pytest.raises(FilmError, match="Le fond dure"):
        film.save()


def test_audio_starting_after_the_end_and_non_audio_files_are_refused(tmp_path):
    film = make_film(tmp_path)
    shot = film.render_shot(quad_scene(), duration=1.0, name="plan1", backend="numpy")
    film.place(shot)
    film.audio(write_wav(tmp_path / "m.wav"), start=5.0)
    with pytest.raises(FilmError, match="après la fin"):
        film.save()
    with pytest.raises(FilmError, match="pas un fichier audio seul"):
        film.audio(shot.path)


def test_export_interrupted_leaves_no_partial_file(tmp_path, monkeypatch):
    film = make_film(tmp_path)
    film.place(film.render_shot(quad_scene(), duration=1.0, name="plan1", backend="numpy"))

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr("core.export_runner._run_ffmpeg", interrupted)
    with pytest.raises(KeyboardInterrupt):
        film.export(tmp_path / "out" / "film.mp4")
    assert tree(tmp_path / "out") == ["film_assets", "film_assets/plan1.mov"]


def test_export_refuses_to_overwrite_a_source_media(tmp_path):
    film = make_film(tmp_path)
    shot = film.render_shot(quad_scene(), duration=1.0, name="plan1", backend="numpy")
    film.place(shot)
    with pytest.raises(FilmError, match=r"\.mp4"):
        film.export(shot.path)


def test_the_hello_film_example_runs_end_to_end(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location("kut3d_hello_film", Path(__file__).resolve().parent.parent / "examples" / "kut3d_hello_film.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    out = module.main(tmp_path, backend="numpy", size=(96, 64), seconds=0.5)
    frame = first_frame(out, (96, 64))
    assert out == tmp_path / "hello.mp4" and (tmp_path / "hello.kut").is_file()
    assert np.abs(frame[1, 1].astype(int) - (13, 15, 31)).max() <= 8          # le fond
    assert frame[32, 48].max() > 80                                            # le cube, au centre


def make_video(path: Path, *, fps: float, frames: int) -> Path:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=red:s=64x64:r={fps:g}", "-frames:v", str(frames),
         "-pix_fmt", "yuv420p", str(path)], check=True,
    )
    return path


def last_frame(path: Path, size: tuple[int, int]) -> np.ndarray:
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-pix_fmt", "rgb24", "-f", "rawvideo", "-"], capture_output=True, check=True,
    )
    frames = np.frombuffer(out.stdout, np.uint8).reshape(-1, size[1], size[0], 3)
    return frames[-1]


def test_video_background_half_a_frame_too_short_is_refused(tmp_path):
    """Un fond plus court que le film, même d'une demi-image, laisserait la dernière image sans fond : refusé."""
    short = make_video(tmp_path / "presque.mp4", fps=12.0, frames=11)            # 11/12 s, le film dure 1 s (6 i/s : ½ image de moins)
    film = make_film(tmp_path)
    film.background(short)
    film.place(film.render_shot(quad_scene(), duration=1.0, name="plan1", backend="numpy"))
    with pytest.raises(FilmError, match="Le fond dure"):
        film.save()


def test_fabricated_background_is_never_shorter_than_the_film(tmp_path):
    """Le fond fabriqué par Film n'est jamais refusé à tort (durées non entières, cadences non entières)."""
    for fps, seconds in ((6.0, 1.0), (24.0, 0.5), (29.97, 1.0), (23.976, 0.75)):
        film = make_film(tmp_path, fps=fps)
        film.background(BLUE)
        film.place(film.render_shot(quad_scene(), duration=seconds, name=f"p{int(fps)}", backend="numpy"))
        (back,) = [c for t in film.project().tracks if t.id == "V1" for c in t.clips]
        assert back.source_out >= film.duration - 1e-6
        assert back.source_out == pytest.approx(film.duration, abs=1e-3)


def test_exact_length_video_background_shows_in_the_last_frame(tmp_path):
    exact = make_video(tmp_path / "exact.mp4", fps=FPS, frames=6)
    film = make_film(tmp_path)
    film.background(exact)
    film.place(film.render_shot(quad_scene(), duration=1.0, name="plan1", backend="numpy"))
    out = film.export(tmp_path / "out" / "film.mp4")
    corner = last_frame(out, SIZE)[3, 3].astype(int)
    assert corner[0] > 150 and corner[1] < 90 and corner[2] < 90, corner        # rouge du fond, pas le noir du moteur


def test_audio_unreadable_or_video_less_background_is_refused_at_the_call(tmp_path):
    film = make_film(tmp_path)
    wav = write_wav(tmp_path / "x.wav")
    with pytest.raises(FilmError, match="vidéo"):
        film.background(wav)
    garbage = tmp_path / "casse.mp4"
    garbage.write_bytes(b"pas une video" * 1000)
    with pytest.raises(FilmError):
        film.background(garbage)
    audio_only = tmp_path / "son.m4a"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(wav), str(audio_only)], check=True)
    with pytest.raises(FilmError, match="vidéo"):
        film.background(audio_only)
    assert film._background is None                                             # rien n'a été retenu
