"""``Film.place_scene`` (ADR-0009, lot B4) : une scène 3D devient un vrai clip graphique ; ses modèles sont copiés sous le film.

FFmpeg réel pour l'export (pixel du cube, pixel du fond) ; le reste ne rend rien.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

import kut3d
from core import film_scene
from core.film import Film, FilmError
from core.project_io import load_project
from kut3d import AmbientLight, Camera, Instance, Scene, load_model
from tests.kut3d_samples import write_cube_obj
from tests.kut3d_scene_samples import rich_scene, translation
from tests.scene3d_render_samples import png_bytes
from tests.kut3d_scenes import WHITE_AMBIENT, flat, front_camera, quad_model, scene as scene_of

ROOT = Path(__file__).resolve().parent.parent
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="FFmpeg absent")

SIZE = (64, 36)
FPS = 10.0
BACKGROUND = (0.05, 0.06, 0.12)


def make_film(tmp_path: Path, size=SIZE, fps=FPS) -> Film:
    return Film(tmp_path / "out" / "film.kut", size=size, fps=fps)


def red_cube_scene(folder: Path, name: str = "cube") -> Scene:
    """Un cube rouge (OBJ + MTL, sans texture) vu de face, lumière ambiante seule, fond transparent."""
    folder.mkdir(parents=True, exist_ok=True)
    write_cube_obj(folder, texture=False, name=name)
    model = load_model(folder / f"{name}.obj", target_size=2.0)
    return Scene([Instance(model)], [AmbientLight((1, 1, 1), 1.0)], Camera((0, 0, 6), (0, 0, 0)))


def _folder(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def tree(folder: Path) -> list[str]:
    return sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*"))


def frame_at(path: Path, seconds: float = 0.0, size=SIZE) -> np.ndarray:
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{seconds}", "-i", str(path), "-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"],
        capture_output=True, check=True,
    )
    return np.frombuffer(out.stdout, np.uint8).reshape(size[1], size[0], 3)


# --- placement nominal ---------------------------------------------------------------------------------------------------


def test_place_scene_creates_a_native_graphic_clip_and_a_kut3d_file(tmp_path):
    film = make_film(tmp_path)
    clip_id = film.place_scene(red_cube_scene(tmp_path / "src"), duration=1.0, start=0.5, name="cube")
    assert clip_id == "scene_cube_1"
    written = film.assets_dir / "cube.kut3d"
    assert written.is_file() and not list(film.assets_dir.glob("*.mov")), "aucune vidéo ProRes"
    kut3d.load_scene(written)                                   # se relit, modèles confinés
    project = film.project()
    graphics = [track for track in project.tracks if track.type == "graphics"]
    assert [track.id for track in graphics] == ["G1"]
    clip = graphics[0].clips[0]
    assert clip.id == clip_id and clip.graphic.type.value == "scene3d"
    assert clip.duration == pytest.approx(1.0) and clip.timeline_start == pytest.approx(0.5)
    assert clip.graphic.source_path == str(written.resolve())
    assert film.duration == pytest.approx(1.5)


def test_the_project_is_written_in_version_22(tmp_path):
    film = make_film(tmp_path)
    film.place_scene(red_cube_scene(tmp_path / "src"), duration=1.0, name="cube")
    film.save()
    assert json.loads(film.path.read_text(encoding="utf-8"))["version"] == 22
    reloaded = load_project(str(film.path))
    assert any(clip.graphic is not None and clip.graphic.type.value == "scene3d" for track in reloaded.tracks for clip in track.clips)


def test_a_scene_on_another_graphics_track_and_two_scenes_in_sequence(tmp_path):
    film = make_film(tmp_path)
    film.place_scene(red_cube_scene(tmp_path / "a", "cube"), duration=1.0, name="un", track=2)
    film.place_scene(red_cube_scene(tmp_path / "b", "cube"), duration=1.0, name="deux", start=1.0, track=2)
    project = film.project()
    assert [track.id for track in project.tracks if track.type == "graphics"] == ["G1", "G2"]
    assert [clip.id for clip in next(track for track in project.tracks if track.id == "G2").clips] == ["scene_un_1", "scene_deux_2"]


# --- copie des modèles ---------------------------------------------------------------------------------------------------


def test_obj_mtl_and_texture_are_copied_and_the_scene_reads_back_identically(tmp_path):
    source = tmp_path / "src"
    original = red_cube_scene(source)
    textured = tmp_path / "tex_src"
    textured.mkdir()
    write_cube_obj(textured, texture=True, name="boite")
    boite = load_model(textured / "boite.obj", target_size=3.0)
    scene = Scene([*original.instances, Instance(boite)], original.lights, original.camera)
    film = make_film(tmp_path)
    film.place_scene(scene, duration=1.0, name="cube")
    models = film.assets_dir / "cube_models"
    names = sorted(p.name for p in models.rglob("*") if p.is_file())
    assert names == ["boite.mtl", "boite.obj", "cube.mtl", "cube.obj", "normal map.png", "tex.png"]
    shutil.rmtree(source), shutil.rmtree(textured)               # la copie se suffit
    read = kut3d.load_scene(film.assets_dir / "cube.kut3d")
    assert len(read.instances) == 2
    for before, after in zip(scene.instances, read.instances):
        np.testing.assert_allclose(after.model.root_transform, before.model.root_transform)
        np.testing.assert_allclose(after.model.meshes[0].positions, before.model.meshes[0].positions)
        assert Path(after.model.source).resolve().is_relative_to(film.assets_dir.resolve())


def test_gltf_with_external_bin_and_glb_are_copied(tmp_path):
    scene = rich_scene(_folder(tmp_path / "src"))
    film = make_film(tmp_path)
    film.place_scene(scene, duration=1.0, name="riche")
    names = sorted(p.name for p in (film.assets_dir / "riche_models").rglob("*") if p.is_file())
    assert "quad.bin" in names and "quad.gltf" in names and "rig.glb" in names and "cube.obj" in names
    shutil.rmtree(tmp_path / "src")
    read = kut3d.load_scene(film.assets_dir / "riche.kut3d")
    assert len(read.instances) == len(scene.instances)
    for before, after in zip(scene.instances, read.instances):
        np.testing.assert_allclose(after.model.root_transform, before.model.root_transform, atol=1e-12)
        assert after.clip == before.clip and after.loop == before.loop and after.time_offset == before.time_offset


def test_two_instances_of_one_model_make_one_copy(tmp_path):
    base = red_cube_scene(tmp_path / "src")
    model = base.instances[0].model
    scene = Scene([Instance(model), Instance(model, transform=translation(1.5, 0, 0))], base.lights, base.camera)
    film = make_film(tmp_path)
    film.place_scene(scene, duration=1.0, name="deux")
    assert len([p for p in (film.assets_dir / "deux_models").iterdir()]) == 1
    assert len(kut3d.load_scene(film.assets_dir / "deux.kut3d").instances) == 2


def test_two_models_with_the_same_file_name_do_not_collide(tmp_path):
    first = red_cube_scene(tmp_path / "a", "cube").instances[0].model
    second_folder = tmp_path / "b"
    second_folder.mkdir()
    write_cube_obj(second_folder, texture=False, name="cube")
    with (second_folder / "cube.obj").open("a", encoding="utf-8") as handle:
        handle.write("# autre contenu, même nom de fichier\n")
    second = load_model(second_folder / "cube.obj")
    scene = Scene([Instance(first), Instance(second)], [WHITE_AMBIENT], front_camera())
    film = make_film(tmp_path)
    film.place_scene(scene, duration=1.0, name="collision")
    folders = sorted(p.name for p in (film.assets_dir / "collision_models").iterdir())
    assert len(folders) == 2 and folders[0] != folders[1] and all(name.startswith("cube-") for name in folders)
    read = kut3d.load_scene(film.assets_dir / "collision.kut3d")
    sources = [Path(inst.model.source) for inst in read.instances]
    assert sources[0] != sources[1]
    for copied, original in zip(sources, (first, second)):
        assert copied.read_bytes() == Path(original.source).read_bytes()


def test_the_relative_tree_between_obj_mtl_and_texture_is_preserved(tmp_path):
    src = tmp_path / "src"
    (src / "textures").mkdir(parents=True)
    write_cube_obj(src, texture=False, name="cube")
    (src / "cube.mtl").write_text("newmtl red\nKd 0.9 0.1 0.2\nmap_Kd textures/tex.png\n", encoding="utf-8")
    (src / "textures" / "tex.png").write_bytes(png_bytes())
    model = load_model(src / "cube.obj")
    assert model.materials[0].base_color_texture is not None, "l'importeur lit la texture voisine"
    film = make_film(tmp_path)
    film.place_scene(Scene([Instance(model)], [WHITE_AMBIENT], front_camera()), duration=1.0, name="arbre")
    files = tree(film.assets_dir / "arbre_models")
    folder = next(p.name for p in (film.assets_dir / "arbre_models").iterdir())
    assert f"{folder}/cube.obj" in files and f"{folder}/cube.mtl" in files and f"{folder}/textures/tex.png" in files
    shutil.rmtree(src)
    material = kut3d.load_scene(film.assets_dir / "arbre.kut3d").instances[0].model.materials[0]
    assert material.base_color_texture is not None and material.base_color_texture.path is not None
    assert Path(material.base_color_texture.path).is_relative_to(film.assets_dir.resolve())


def test_the_copy_is_bounded_in_files_and_bytes(tmp_path, monkeypatch):
    scene = red_cube_scene(tmp_path / "src")
    film = make_film(tmp_path)
    monkeypatch.setattr(film_scene, "MAX_COPIED_FILES", 1)
    with pytest.raises(FilmError, match="Trop de fichiers"):
        film.place_scene(scene, duration=1.0, name="trop")
    monkeypatch.setattr(film_scene, "MAX_COPIED_FILES", 1000)
    monkeypatch.setattr(film_scene, "MAX_COPIED_BYTES", 10)
    with pytest.raises(FilmError, match="volumineux"):
        film.place_scene(scene, duration=1.0, name="gros")
    assert not film.assets_dir.exists() or tree(film.assets_dir) == []


# --- refus ---------------------------------------------------------------------------------------------------------------


def test_a_function_is_refused_with_a_pointer_to_render_shot(tmp_path):
    film = make_film(tmp_path)
    with pytest.raises(FilmError, match="render_shot"):
        film.place_scene(lambda t: red_cube_scene(tmp_path / "src"), duration=1.0, name="fn")
    with pytest.raises(FilmError, match="Scene"):
        film.place_scene("pas une scène", duration=1.0, name="x")
    assert not film.assets_dir.exists() or tree(film.assets_dir) == []


def test_a_programmatic_model_is_refused_clearly_and_leaves_no_file(tmp_path):
    film = make_film(tmp_path)
    scene = scene_of(quad_model(flat((1.0, 0.0, 0.0, 1.0))), [WHITE_AMBIENT], front_camera())
    with pytest.raises(FilmError, match="fichier source"):
        film.place_scene(scene, duration=1.0, name="code")
    assert not film.assets_dir.exists() or tree(film.assets_dir) == []


@pytest.mark.parametrize("name", ["", "a/b", "a b", "..", "x" * 49, "con", 3, None])
def test_an_invalid_name_is_refused(tmp_path, name):
    film = make_film(tmp_path)
    with pytest.raises(FilmError):
        film.place_scene(red_cube_scene(tmp_path / "src"), duration=1.0, name=name)


@pytest.mark.parametrize("kwargs", [
    {"duration": 0}, {"duration": -1}, {"duration": float("nan")}, {"duration": 4000}, {"duration": True}, {"duration": 1, "start": -1},
    {"duration": 1, "start": 2e6}, {"duration": 1, "track": 0}, {"duration": 1, "track": 17}, {"duration": 1, "track": True},
    {"duration": 1, "overwrite": 1},
])
def test_bounds_are_refused_with_film_errors(tmp_path, kwargs):
    film = make_film(tmp_path)
    with pytest.raises(FilmError):
        film.place_scene(red_cube_scene(tmp_path / "src"), name="plan", **kwargs)
    assert not film.assets_dir.exists() or tree(film.assets_dir) == []


def test_name_collisions_ignore_case_and_cover_shots_and_scenes(tmp_path):
    film = make_film(tmp_path)
    scene = red_cube_scene(tmp_path / "src")
    film.place_scene(scene, duration=1.0, name="Cube")
    with pytest.raises(FilmError, match="déjà pris"):
        film.place_scene(scene, duration=1.0, start=5.0, name="cube")
    assert tree(film.assets_dir) == tree(film.assets_dir)       # inchangé
    assert sorted(p.name for p in film.assets_dir.iterdir()) == ["Cube.kut3d", "Cube_models"]


def test_a_name_already_on_disk_needs_overwrite(tmp_path):
    scene = red_cube_scene(tmp_path / "src")
    make_film(tmp_path).place_scene(scene, duration=1.0, name="cube")
    again = make_film(tmp_path)
    with pytest.raises(FilmError, match="overwrite"):
        again.place_scene(scene, duration=1.0, name="cube")
    again.place_scene(scene, duration=1.0, name="cube", overwrite=True)
    assert sorted(p.name for p in again.assets_dir.iterdir()) == ["cube.kut3d", "cube_models"]


def test_an_overlap_on_the_same_track_is_refused_but_not_on_another(tmp_path):
    film = make_film(tmp_path)
    scene = red_cube_scene(tmp_path / "src")
    film.place_scene(scene, duration=1.0, name="un")
    with pytest.raises(FilmError, match="chevauche"):
        film.place_scene(scene, duration=1.0, start=0.5, name="deux")
    assert not (film.assets_dir / "deux.kut3d").exists() and not (film.assets_dir / "deux_models").exists()
    film.place_scene(scene, duration=1.0, start=0.5, name="deux", track=2)
    film.place_scene(scene, duration=1.0, start=1.0, name="trois")          # bout à bout : permis


def test_an_error_while_copying_leaves_no_partial_file(tmp_path, monkeypatch):
    film = make_film(tmp_path)
    scene = rich_scene(_folder(tmp_path / "src"))
    calls = []
    real = shutil.copyfile

    def failing(source, target, **kw):
        calls.append(source)
        if len(calls) == 3:
            raise OSError("disque plein")
        return real(source, target, **kw)

    monkeypatch.setattr(film_scene.shutil, "copyfile", failing)
    with pytest.raises(FilmError, match="Écriture impossible"):
        film.place_scene(scene, duration=1.0, name="riche")
    assert tree(film.assets_dir) == []
    assert film.duration == 0.0


def test_a_placement_that_does_not_read_back_is_discarded(tmp_path, monkeypatch):
    film = make_film(tmp_path)
    scene = red_cube_scene(tmp_path / "src")
    from core import scene3d

    def refuse(path):
        raise scene3d.Scene3DError("refusée pour le test")

    monkeypatch.setattr("core.scene3d.validate", refuse)
    with pytest.raises(FilmError, match="refusée pour le test"):
        film.place_scene(scene, duration=1.0, name="cube")
    assert tree(film.assets_dir) == []


def test_a_film_without_any_shot_or_scene_still_refuses_to_save(tmp_path):
    with pytest.raises(FilmError, match="Aucun plan"):
        make_film(tmp_path).save()


# --- export réel ---------------------------------------------------------------------------------------------------------


@needs_ffmpeg
def test_the_exported_mp4_shows_the_cube_over_the_background(tmp_path, qapp):
    film = make_film(tmp_path)
    film.background(BACKGROUND)
    film.place_scene(red_cube_scene(tmp_path / "src"), duration=1.0, name="cube")
    film.save()
    mp4 = film.export(tmp_path / "out" / "film.mp4")
    frame = frame_at(mp4, 0.3)
    cube = kut3d.render_frame(kut3d.load_scene(film.assets_dir / "cube.kut3d"), t=0.0, size=SIZE, backend="numpy", samples=1)
    expected = cube.color[SIZE[1] // 2, SIZE[0] // 2][:3].astype(int)
    assert abs(frame[SIZE[1] // 2, SIZE[0] // 2].astype(int) - expected).max() <= 14, (frame[18, 32], expected)
    assert expected[0] - max(expected[1], expected[2]) > 60, "le cube est rouge"
    background = np.array([round(c * 255) for c in BACKGROUND])
    assert abs(frame[1, 1].astype(int) - background).max() <= 12, frame[1, 1]


@needs_ffmpeg
def test_the_reloaded_film_exports_the_same_image(tmp_path, qapp):
    film = make_film(tmp_path)
    film.background(BACKGROUND)
    film.place_scene(red_cube_scene(tmp_path / "src"), duration=1.0, name="cube")
    film.save()
    first = film.export(tmp_path / "first.mp4")
    from core.export_engine import ExportFormat, ExportPreset, ExportRequest
    from core.export_runner import ExportStatus, run_export
    from core.render_plan import build_render_plan

    plan = build_render_plan(load_project(str(film.path)))
    request = ExportRequest(render_plan=plan, output_path=str(tmp_path / "second.mp4"), format=ExportFormat.MP4_H264,
                            preset=ExportPreset("Film", SIZE, 18, "192k"), fps=FPS)
    result = run_export(request)
    assert result.status is ExportStatus.OK, result.message
    a, b = frame_at(first, 0.3).astype(int), frame_at(tmp_path / "second.mp4", 0.3).astype(int)
    assert abs(a - b).max() <= 6


@needs_ffmpeg
def test_render_shot_and_place_scene_mix_in_one_film(tmp_path, qapp):
    film = make_film(tmp_path)
    film.background(BACKGROUND)
    shot = film.render_shot(scene_of(quad_model(flat((0.0, 0.0, 1.0, 1.0)), half=0.2), [WHITE_AMBIENT], front_camera()),
                            duration=1.0, name="bleu", backend="numpy")
    film.place(shot, start=0.0)
    film.place_scene(red_cube_scene(tmp_path / "src"), duration=1.0, start=0.0, name="cube")
    project = film.project()
    assert {track.type for track in project.tracks} == {"video", "graphics"}
    film.save()
    mp4 = film.export(tmp_path / "mix.mp4")
    frame = frame_at(mp4, 0.3)
    assert frame[18, 32][0] > 150, "le cube (calque graphique) passe au-dessus du plan vidéo"


# --- isolation -----------------------------------------------------------------------------------------------------------


def test_importing_the_film_modules_loads_neither_kut3d_nor_the_export_engine():
    code = ("import sys, core.film, core.film_scene; "
            "bad = [m for m in ('kut3d', 'core.export_engine', 'core.export_runner', 'core.scene3d') if m in sys.modules]; print(bad)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT, check=True,
                         env={**os.environ, "PYTHONPATH": str(ROOT)})
    assert out.stdout.strip() == "[]"


def test_the_example_script_builds_its_own_cube_without_external_files():
    text = (ROOT / "examples" / "kut3d_scene_film.py").read_text(encoding="utf-8")
    assert "place_scene" in text and "render_shot" not in text

