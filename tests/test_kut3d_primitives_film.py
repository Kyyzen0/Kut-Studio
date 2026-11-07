"""Formes de base côté montage : `Film.primitive`, `Film.place_scene` d'une scène sans aucun fichier de modèle, verbe `add_scene3d_clip`."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

import kut3d
from core import scene3d
from core.film import Film, FilmError
from core.project_io import load_project
from core.project_model import Project, Track
from core.scripting import ScriptContext, Session, apply_script, parse_script
from kut3d import (
    AmbientLight,
    Camera,
    Cube,
    Instance,
    Material,
    Scene,
    Sphere,
    load_model,
    primitive_instance,
)
from tests.kut3d_primitive_scenes import six_forms
from tests.kut3d_samples import write_cube_obj

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="FFmpeg absent",
)
SIZE = (64, 36)
FPS = 10.0


def _film(tmp_path: Path) -> Film:
    return Film(tmp_path / "out" / "film.kut", size=SIZE, fps=FPS)


def _red_sphere_scene(film: Film) -> Scene:
    red = Material(base_color=(1.0, 0.0, 0.0, 1.0), roughness=1.0)
    return Scene(
        [film.primitive("sphere", material=red, radius=1.0, segments=24, rings=12)],
        [AmbientLight((1, 1, 1), 1.0)],
        Camera((0, 0, 4), (0, 0, 0)),
    )


def test_film_primitive_builds_an_instance_and_refuses_bad_input(
    tmp_path: Path,
) -> None:
    film = _film(tmp_path)
    inst = film.primitive(
        "cube", width=2.0, position=(1, 2, 3), scale=2.0, name="boite"
    )
    assert (
        isinstance(inst, Instance)
        and inst.model.primitive == Cube(2.0, 1.0, 1.0)
        and inst.name == "boite"
    )
    assert np.allclose(inst.transform[:3, 3], (1, 2, 3)) and np.allclose(
        np.diag(inst.transform)[:3], 2.0
    )
    assert film.primitive(Sphere(0.5)).model.primitive == Sphere(0.5)
    for call in (
        lambda: film.primitive("pyramide"),
        lambda: film.primitive("sphere", radius=-1),
        lambda: film.primitive("sphere", segments=10**9),
        lambda: film.primitive("cube", position=(0, 0)),
    ):
        with pytest.raises(FilmError) as info:
            call()
        assert len(str(info.value)) < 500
    assert not (tmp_path / "out" / "film_assets").exists() or not any(
        (tmp_path / "out" / "film_assets").iterdir()
    )


def test_a_scene_of_primitives_is_placed_without_any_model_file(tmp_path: Path) -> None:
    film = _film(tmp_path)
    clip_id = film.place_scene(
        _red_sphere_scene(film), duration=1.0, start=0.5, name="boule"
    )
    written = film.assets_dir / "boule.kut3d"
    assert clip_id == "scene_boule_1" and written.is_file()
    assert not (film.assets_dir / "boule_models").exists(), (
        "aucun fichier de modèle à confiner : aucun dossier"
    )
    assert json.loads(written.read_bytes())["version"] == 2
    assert kut3d.load_scene(written).instances[0].model.primitive is not None
    assert scene3d.dependencies(written) == (written.resolve(),)
    scene3d.validate(written)
    project = film.project()
    clip = next(c for t in project.tracks if t.type == "graphics" for c in t.clips)
    assert clip.graphic.type.value == "scene3d" and clip.duration == pytest.approx(1.0)
    film.save()
    assert (
        json.loads(film.path.read_text(encoding="utf-8"))["version"] == 22
    )  # le format du projet ne bouge pas
    reloaded = load_project(str(film.path))
    assert any(
        c.graphic.source_path == str(written.resolve())
        for t in reloaded.tracks
        for c in t.clips
        if c.graphic
    )


def test_a_scene_mixing_a_model_file_and_primitives_copies_only_the_file(
    tmp_path: Path,
) -> None:
    src = tmp_path / "src"
    src.mkdir()
    write_cube_obj(src, texture=False, name="cube")
    scene = Scene(
        [
            Instance(load_model(src / "cube.obj"), name="fichier"),
            primitive_instance("torus", name="anneau"),
        ],
        [AmbientLight((1, 1, 1), 1.0)],
        Camera((0, 0, 6), (0, 0, 0)),
    )
    film = _film(tmp_path)
    film.place_scene(scene, duration=1.0, name="mix")
    copies = sorted(
        p.relative_to(film.assets_dir).as_posix()
        for p in (film.assets_dir / "mix_models").rglob("*")
        if p.is_file()
    )
    assert (
        copies
        and all("cube" in c for c in copies)
        and not any("torus" in c for c in copies)
    )
    back = kut3d.load_scene(film.assets_dir / "mix.kut3d")
    assert [i.model.primitive is not None for i in back.instances] == [False, True]


def test_a_hand_built_model_without_a_file_is_still_refused_by_place_scene(
    tmp_path: Path,
) -> None:
    import dataclasses

    film = _film(tmp_path)
    model = kut3d.primitive_model(Cube())
    handmade = dataclasses.replace(
        model, primitive=None
    )  # même maillage, mais plus de description : géométrie « construite en code »
    scene = Scene([Instance(handmade)], [], Camera((0, 0, 4), (0, 0, 0)))
    with pytest.raises(FilmError, match="pas de fichier source"):
        film.place_scene(scene, duration=1.0, name="x")
    assert not (film.assets_dir / "x.kut3d").exists()


def test_overwriting_a_primitive_scene_keeps_the_old_one_on_failure(
    tmp_path: Path,
) -> None:
    film = _film(tmp_path)
    film.place_scene(_red_sphere_scene(film), duration=1.0, name="s")
    before = (film.assets_dir / "s.kut3d").read_bytes()
    with pytest.raises(FilmError):
        film.place_scene(_red_sphere_scene(film), duration=1.0, name="s")  # nom pris
    bad = Scene(
        [
            Instance(
                kut3d.primitive_model(
                    Cube(),
                    Material(
                        base_color_texture=kut3d.model.TextureRef(name="t", data=b"x")
                    ),
                )
            )
        ],
        [],
        Camera((0, 0, 4), (0, 0, 0)),
    )
    film2 = _film(tmp_path)
    with pytest.raises(FilmError, match="texture"):
        film2.place_scene(bad, duration=1.0, name="s", overwrite=True)
    assert (film.assets_dir / "s.kut3d").read_bytes() == before


@needs_ffmpeg
def test_the_exported_mp4_shows_the_sphere(tmp_path: Path, qapp) -> None:
    film = _film(tmp_path)
    film.background((0.05, 0.06, 0.12))
    film.place_scene(_red_sphere_scene(film), duration=1.0, name="boule")
    film.save()
    mp4 = film.export(tmp_path / "out" / "film.mp4")
    out = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-ss",
            "0.3",
            "-i",
            str(mp4),
            "-frames:v",
            "1",
            "-pix_fmt",
            "rgb24",
            "-f",
            "rawvideo",
            "-",
        ],
        capture_output=True,
        check=True,
    )
    frame = np.frombuffer(out.stdout, np.uint8).reshape(SIZE[1], SIZE[0], 3).astype(int)
    centre, corner = frame[SIZE[1] // 2, SIZE[0] // 2], frame[1, 1]
    assert centre[0] - max(centre[1], centre[2]) > 100, centre  # la sphère rouge
    assert abs(corner - np.array([13, 15, 31])).max() <= 12, corner  # le fond


# --- verbe de script ---------------------------------------------------------------------------------------------


def _project() -> Project:
    return Project(
        "Démo", width=160, height=90, fps=10.0, tracks=[Track("V1", "V1", "video")]
    )


def test_the_script_verb_accepts_a_kut3d_of_primitives(tmp_path: Path) -> None:
    path = tmp_path / "formes.kut3d"
    kut3d.save_scene(six_forms(), path)
    session = Session(_project())
    script = parse_script(
        {
            "kut_script": 1,
            "ops": [
                {
                    "op": "add_scene3d_clip",
                    "path": "formes.kut3d",
                    "at": 0.0,
                    "duration": 2.0,
                    "id": "f",
                }
            ],
        }
    )
    report = apply_script(session, script, context=ScriptContext(tmp_path))
    assert report.ok, report.error
    clip = next(
        c for t in session.project.tracks if t.type == "graphics" for c in t.clips
    )
    assert clip.graphic.type.value == "scene3d" and clip.graphic.source_path == str(
        path.resolve()
    )


def test_the_script_verb_refuses_a_hostile_primitive_file_with_a_short_message(
    tmp_path: Path,
) -> None:
    doc = {
        "format": "kut3d",
        "version": 2,
        "scene": {
            "camera": {"position": [0, 0, 5], "target": [0, 0, 0]},
            "models": [
                {
                    "id": "p",
                    "primitive": {"kind": "sphere", "segments": 10**9, "radius": 1e30},
                }
            ],
        },
    }
    (tmp_path / "mal.kut3d").write_text(json.dumps(doc), encoding="utf-8")
    session = Session(_project())
    script = parse_script(
        {
            "kut_script": 1,
            "ops": [
                {
                    "op": "add_scene3d_clip",
                    "path": "mal.kut3d",
                    "at": 0.0,
                    "duration": 2.0,
                }
            ],
        }
    )
    report = apply_script(session, script, context=ScriptContext(tmp_path))
    assert (
        not report.ok and "Scène 3D refusée" in report.error and len(report.error) < 800
    )
    assert not any(t.clips for t in session.project.tracks)
