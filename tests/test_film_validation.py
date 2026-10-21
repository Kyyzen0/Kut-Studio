"""Façade ``Film`` : noms, bornes, erreurs, isolation des imports, nettoyage après échec ou interruption (sans FFmpeg réel)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from core import film_render
from core.film import Film, FilmError, Shot
from tests.kut3d_scenes import WHITE_AMBIENT, flat, front_camera, quad_model, scene

ROOT = Path(__file__).resolve().parent.parent
SIZE = (16, 16)


def tiny_scene():
    return scene(quad_model(flat((1.0, 0.0, 0.0, 1.0)), half=0.4), [WHITE_AMBIENT], front_camera())


@pytest.fixture
def fake_ffmpeg(monkeypatch):
    """Un « ffmpeg » résolu mais jamais lancé : les tests de validation et de nettoyage ne dépendent pas de la machine."""
    monkeypatch.setattr("core.tool_paths.find_media_tool", lambda name, **_: f"/fake/{name}")


def leftovers(folder: Path) -> list[str]:
    return sorted(str(p.relative_to(folder)) for p in folder.rglob("*") if p.is_file() or p.is_dir())


def make_film(tmp_path: Path, **kwargs) -> Film:
    return Film(tmp_path / "out" / "film.kut", size=kwargs.pop("size", SIZE), fps=kwargs.pop("fps", 4.0), **kwargs)


def test_import_core_film_loads_neither_kut3d_nor_the_export_engine():
    code = "import sys, core.film; bad = [m for m in ('kut3d', 'core.export_engine', 'core.export_runner') if m in sys.modules]; print(bad)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT, check=True)
    assert out.stdout.strip() == "[]"


def test_film_bounds_mirror_the_kut3d_limits():
    from kut3d.render import limits

    assert film_render.MAX_SIDE == limits.MAX_IMAGE_SIDE
    assert film_render.MAX_FPS == limits.MAX_FPS
    assert film_render.MAX_SHOT_FRAMES == limits.MAX_SEQUENCE_FRAMES
    assert film_render.SAMPLES_CHOICES == limits.SAMPLES_CHOICES


@pytest.mark.parametrize("path", ["film", "film.mp4", ".kut", 12, "", "a\0.kut"])
def test_project_path_must_be_a_kut_file(path):
    with pytest.raises(FilmError):
        Film(path)


@pytest.mark.parametrize("size", [(1920,), (0, 10), (-2, 10), (101, 100), (100, 8194), (1.5, 2), "ab", (True, 4), None])
def test_size_is_validated_without_silent_rounding(tmp_path, size):
    with pytest.raises(FilmError):
        Film(tmp_path / "f.kut", size=size)


@pytest.mark.parametrize("fps", [0, -1, 241, float("nan"), float("inf"), "24", True, None])
def test_fps_is_validated(tmp_path, fps):
    with pytest.raises(FilmError):
        Film(tmp_path / "f.kut", fps=fps)


def test_output_folder_is_created(tmp_path):
    Film(tmp_path / "a" / "b" / "f.kut", size=SIZE)
    assert (tmp_path / "a" / "b").is_dir()


@pytest.mark.parametrize(
    "name", ["", "a/b", "a\\b", "..", "a.b", "a b", "é", "x" * 49, "CON", "nul", "Com1", None, 3, "a\0"],
)
def test_shot_names_are_refused_without_creating_anything(tmp_path, fake_ffmpeg, name):
    film = make_film(tmp_path)
    with pytest.raises(FilmError):
        film.render_shot(tiny_scene(), duration=1.0, name=name, backend="numpy")
    assert not film.assets_dir.exists()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"duration": 0}, {"duration": -1.0}, {"duration": float("nan")}, {"duration": float("inf")}, {"duration": True},
        {"duration": "3"}, {"duration": 3601}, {"duration": 3600},                   # 3600 s à 4 i/s : 14 400 images, mais borne de durée ci-dessous
        {"duration": 1.0, "samples": 3}, {"duration": 1.0, "samples": True}, {"duration": 1.0, "samples": "2"},
    ],
)
def test_bounds_are_checked_before_any_frame_is_rendered(tmp_path, fake_ffmpeg, kwargs):
    film = make_film(tmp_path, fps=240.0)                # 3600 s x 240 i/s dépasse la borne d'images, avant tout rendu
    called: list[float] = []

    def never(t):
        called.append(t)
        return tiny_scene()

    with pytest.raises(FilmError) as error:
        film.render_shot(never, name="plan", backend="numpy", **kwargs)
    assert called == [] and not film.assets_dir.exists()
    assert len(str(error.value)) < 300


def test_huge_inputs_are_not_copied_into_messages(tmp_path, fake_ffmpeg):
    film = make_film(tmp_path)
    huge = "x" * 5_000_000
    for call in (
        lambda: film.render_shot(tiny_scene(), duration=1.0, name=huge),
        lambda: film.render_shot(tiny_scene(), duration=huge, name="plan"),
        lambda: film.background(huge),
        lambda: film.audio(huge),
        lambda: Film(huge),
        lambda: film.render_shot(tiny_scene(), duration=1.0, name="plan", backend=huge),
    ):
        with pytest.raises(FilmError) as error:
            call()
        assert len(str(error.value)) < 500


def test_a_non_scene_argument_is_refused(tmp_path, fake_ffmpeg):
    with pytest.raises(FilmError):
        make_film(tmp_path).render_shot("pas une scène", duration=1.0, name="plan")


def test_missing_ffmpeg_is_reported_before_rendering(tmp_path, monkeypatch):
    monkeypatch.setattr("core.tool_paths.find_media_tool", lambda name, **_: None)
    called: list[float] = []
    film = make_film(tmp_path)
    with pytest.raises(FilmError, match="FFmpeg est introuvable"):
        film.render_shot(lambda t: called.append(t) or tiny_scene(), duration=1.0, name="plan", backend="numpy")
    assert called == [] and not film.assets_dir.exists()


def test_scene_at_is_called_with_the_frame_instants(tmp_path, fake_ffmpeg, monkeypatch):
    monkeypatch.setattr("core.film.encode_alpha_video", lambda folder, count, fps, output: output.write_bytes(b"x"))
    times: list[float] = []

    def scene_at(t):
        times.append(t)
        return tiny_scene()

    film = make_film(tmp_path, fps=4.0)
    shot = film.render_shot(scene_at, duration=1.0, name="plan", backend="numpy")
    assert times == [0.0, 0.25, 0.5, 0.75]
    assert (shot.frames, shot.duration, shot.size, shot.fps) == (4, 1.0, SIZE, 4.0)
    assert not (film.assets_dir / "plan").exists()            # les PNG sont supprimés après l'encodage


def test_encoder_failure_leaves_nothing_and_truncates_stderr(tmp_path, fake_ffmpeg, monkeypatch):
    noise = "erreur ffmpeg " * 5000
    monkeypatch.setattr(
        "core.process_supervisor.supervised_run",
        lambda command, **options: subprocess.CompletedProcess(command, 1, "", noise),
    )
    film = make_film(tmp_path)
    with pytest.raises(FilmError, match="FFmpeg a échoué") as error:
        film.render_shot(tiny_scene(), duration=1.0, name="plan", backend="numpy")
    assert len(str(error.value)) < 1200
    assert leftovers(film.assets_dir) == []


def test_ffmpeg_that_cannot_start_is_a_clean_error(tmp_path, fake_ffmpeg, monkeypatch):
    def boom(command, **options):
        raise FileNotFoundError("introuvable")

    monkeypatch.setattr("core.process_supervisor.supervised_run", boom)
    film = make_film(tmp_path)
    with pytest.raises(FilmError, match="Impossible de lancer FFmpeg"):
        film.render_shot(tiny_scene(), duration=1.0, name="plan", backend="numpy")
    assert leftovers(film.assets_dir) == []


def test_interruption_while_rendering_leaves_no_file(tmp_path, fake_ffmpeg):
    film = make_film(tmp_path)

    def scene_at(t):
        if t >= 0.5:
            raise KeyboardInterrupt
        return tiny_scene()

    with pytest.raises(KeyboardInterrupt):
        film.render_shot(scene_at, duration=1.0, name="plan", backend="numpy")
    assert leftovers(film.assets_dir) == []


def test_interruption_while_encoding_leaves_no_file(tmp_path, fake_ffmpeg, monkeypatch):
    def interrupted(command, **options):
        Path(command[-1]).write_bytes(b"x")      # FFmpeg avait commencé à écrire le fichier partiel
        raise KeyboardInterrupt

    monkeypatch.setattr("core.process_supervisor.supervised_run", interrupted)
    film = make_film(tmp_path)
    with pytest.raises(KeyboardInterrupt):
        film.render_shot(tiny_scene(), duration=1.0, name="plan", backend="numpy")
    assert leftovers(film.assets_dir) == []


def test_user_files_next_to_the_frames_are_never_deleted(tmp_path, fake_ffmpeg, monkeypatch):
    film = make_film(tmp_path)
    (film.assets_dir / "plan").mkdir(parents=True)
    (film.assets_dir / "plan" / "notes.txt").write_text("à moi", encoding="utf-8")
    (film.assets_dir / "plan" / "frame_000099.png").write_bytes(b"ancienne image")
    monkeypatch.setattr("core.film.encode_alpha_video", lambda folder, count, fps, output: output.write_bytes(b"x"))
    film.render_shot(tiny_scene(), duration=1.0, name="plan", backend="numpy")
    assert (film.assets_dir / "plan" / "notes.txt").read_text(encoding="utf-8") == "à moi"
    assert not (film.assets_dir / "plan" / "frame_000099.png").exists()


def test_name_collision_is_refused_unless_overwrite(tmp_path, fake_ffmpeg, monkeypatch):
    monkeypatch.setattr("core.film.encode_alpha_video", lambda folder, count, fps, output: output.write_bytes(b"x"))
    film = make_film(tmp_path)
    film.render_shot(tiny_scene(), duration=1.0, name="Plan", backend="numpy")
    with pytest.raises(FilmError, match="existe déjà"):
        film.render_shot(tiny_scene(), duration=1.0, name="plan", backend="numpy")      # même fichier sous macOS et Windows
    film.render_shot(tiny_scene(), duration=1.0, name="plan", backend="numpy", overwrite=True)
    again = make_film(tmp_path)                                                         # un nouveau script, le fichier est resté
    with pytest.raises(FilmError, match="existe déjà"):
        again.render_shot(tiny_scene(), duration=1.0, name="Plan", backend="numpy")


def fake_shot(tmp_path: Path, name="plan", size=SIZE, fps=4.0, frames=4) -> Shot:
    path = tmp_path / f"{name}.mov"
    path.write_bytes(b"x")
    return Shot(name, path, frames / fps, frames, size, fps)


def test_place_refuses_bad_arguments(tmp_path):
    film = make_film(tmp_path)
    shot = fake_shot(tmp_path)
    for kwargs in ({"start": -1}, {"start": float("nan")}, {"start": 2e6}, {"track": 0}, {"track": 17}, {"track": True}, {"track": 2.0}):
        with pytest.raises(FilmError):
            film.place(shot, **kwargs)
    with pytest.raises(FilmError):
        film.place("pas un plan")
    with pytest.raises(FilmError, match="ne correspond pas"):
        film.place(fake_shot(tmp_path, "autre", size=(32, 32)))
    with pytest.raises(FilmError, match="ne correspond pas"):
        film.place(fake_shot(tmp_path, "autre", fps=8.0))
    shot.path.unlink()
    with pytest.raises(FilmError, match="a disparu"):
        film.place(shot)


def test_place_refuses_overlap_but_accepts_touching_and_other_tracks(tmp_path):
    film = make_film(tmp_path)
    shot = fake_shot(tmp_path)                                  # 1 s
    film.place(shot, start=0.0)
    with pytest.raises(FilmError, match="chevauche"):
        film.place(shot, start=0.5)
    film.place(shot, start=1.0)                                 # bout à bout : permis
    film.place(shot, start=0.5, track=3)
    assert film.duration == 2.0


def test_background_validation_and_track_one(tmp_path):
    film = make_film(tmp_path)
    for bad in ((1, 2), (0, 0, 0, 0.5), (1.2, 0, 0), (-0.1, 0, 0), (float("nan"), 0, 0), ("a", 0, 0), tmp_path / "absent.png", 5):
        with pytest.raises(FilmError):
            film.background(bad)
    film.place(fake_shot(tmp_path), track=1)
    with pytest.raises(FilmError, match="piste 1"):
        film.background((0, 0, 0))
    other = make_film(tmp_path)
    other.background((0, 0, 0, 1))
    with pytest.raises(FilmError, match="piste 1"):
        other.place(fake_shot(tmp_path), track=1)


def test_image_background_name_with_percent_is_refused(tmp_path):
    image = tmp_path / "fond_%03d.png"
    image.write_bytes(b"x")
    with pytest.raises(FilmError, match="%"):
        make_film(tmp_path).background(image)


def test_nothing_placed_cannot_be_saved(tmp_path):
    with pytest.raises(FilmError, match="Aucun plan posé"):
        make_film(tmp_path).save()


def test_audio_refuses_missing_file_and_bad_arguments(tmp_path):
    film = make_film(tmp_path)
    with pytest.raises(FilmError, match="introuvable"):
        film.audio(tmp_path / "absent.wav")
    existing = tmp_path / "x.wav"
    existing.write_bytes(b"x")
    for kwargs in ({"gain_db": 13}, {"gain_db": -61}, {"gain_db": float("nan")}, {"start": -1}, {"track": 0}, {"track": 17}):
        with pytest.raises(FilmError):
            film.audio(existing, **kwargs)


def test_export_arguments_are_checked_before_any_work(tmp_path):
    film = make_film(tmp_path)
    film.place(fake_shot(tmp_path))
    with pytest.raises(FilmError, match="Préréglage inconnu"):
        film.export(tmp_path / "out.mp4", preset="n_existe_pas")
    with pytest.raises(FilmError, match=r"\.mp4"):
        film.export(tmp_path / "out.avi")
    with pytest.raises(FilmError, match=r"\.mov"):
        film.export(tmp_path / "out.mp4", preset="prores_master")
    assert not (tmp_path / "out.mp4").exists()


def test_user_config_is_not_touched(tmp_path):
    """Le chemin sans fenêtre n'écrit jamais dans la configuration de l'utilisateur (conftest isole le dossier)."""
    config = os.environ.get("KUT_STUDIO_CONFIG_DIR", "")
    assert config and "Application Support" not in config
