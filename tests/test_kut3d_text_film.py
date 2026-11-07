"""Texte 3D côté montage : `Film.text3d`, `Film.place_scene` d'une scène de texte (aucun fichier de police ni de modèle), verbe de script, export."""

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
from kut3d import AmbientLight, Camera, Cube, Instance, Material, Scene, Text3D

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="FFmpeg absent",
)
SIZE = (96, 54)
FPS = 10.0


def _film(tmp_path: Path) -> Film:
    return Film(tmp_path / "out" / "film.kut", size=SIZE, fps=FPS)


def _title_scene(film: Film) -> Scene:
    gold = Material(base_color=(1.0, 0.8, 0.1, 1.0), roughness=1.0)
    return Scene(
        [film.text3d("KUT", size=1.6, depth=0.3, bevel=0.01, material=gold)],
        [AmbientLight((1, 1, 1), 1.0)],
        Camera((0, 0, 4), (0, 0, 0)),
    )


def test_film_text3d_builds_an_instance_with_the_asked_parameters(
    tmp_path: Path,
) -> None:
    film = _film(tmp_path)
    inst = film.text3d(
        "Kut\nStudio",
        font="Saira ExtraCondensed",
        size=2.0,
        depth=0.5,
        bevel=0.01,
        align="left",
        position=(1, 2, 3),
        scale=2.0,
        name="titre",
    )
    assert isinstance(inst, Instance) and inst.name == "titre"
    assert inst.model.primitive == Text3D(
        "Kut\nStudio",
        font="Saira ExtraCondensed",
        size=2.0,
        depth=0.5,
        bevel=0.01,
        align="left",
    )
    assert np.allclose(inst.transform[:3, 3], (1, 2, 3)) and np.allclose(
        np.diag(inst.transform)[:3], 2.0
    )
    assert film.text3d("A").model.primitive == Text3D("A")  # défauts = valeurs écrites


@pytest.mark.parametrize(
    "call",
    [
        lambda f: f.text3d(""),
        lambda f: f.text3d("x" * 5000),
        lambda f: f.text3d("😀"),
        lambda f: f.text3d("A", size=float("nan")),
        lambda f: f.text3d("A", depth=0),
        lambda f: f.text3d("A", bevel=1.0),
        lambda f: f.text3d("A", align="milieu"),
        lambda f: f.text3d("A", font="Police Qui N'Existe Pas"),
        lambda f: f.text3d("α"),
        lambda f: f.text3d("A", position=(0, 0)),
        lambda f: f.text3d("A", scale=0),
    ],
    ids=[
        "vide",
        "5000",
        "emoji",
        "nan",
        "profondeur",
        "biseau",
        "alignement",
        "police",
        "glyphe",
        "position",
        "échelle",
    ],
)
def test_film_text3d_refuses_bad_input_in_french_with_a_short_message(
    tmp_path: Path, call
) -> None:  # type: ignore[no-untyped-def]
    film = _film(tmp_path)
    with pytest.raises(FilmError) as info:
        call(film)
    assert "Texte 3D refusé" in str(info.value) and len(str(info.value)) < 500
    assert not (tmp_path / "out" / "film_assets").exists() or not any(
        (tmp_path / "out" / "film_assets").iterdir()
    )


def test_a_scene_of_text_is_placed_without_any_model_or_font_file(
    tmp_path: Path,
) -> None:
    film = _film(tmp_path)
    clip_id = film.place_scene(
        _title_scene(film), duration=1.0, start=0.5, name="titre"
    )
    written = film.assets_dir / "titre.kut3d"
    assert clip_id == "scene_titre_1" and written.is_file()
    assert not (film.assets_dir / "titre_models").exists(), "aucun fichier à confiner"
    assert json.loads(written.read_bytes())["version"] == 3
    assert kut3d.load_scene(written).instances[0].model.primitive is not None
    assert scene3d.dependencies(written) == (written.resolve(),)
    scene3d.validate(written)
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


def test_a_primitive_only_scene_is_still_written_in_version_2(tmp_path: Path) -> None:
    film = _film(tmp_path)
    scene = Scene([film.primitive("cube")], [], Camera((0, 0, 4), (0, 0, 0)))
    film.place_scene(scene, duration=1.0, name="boite")
    assert (
        json.loads((film.assets_dir / "boite.kut3d").read_bytes())["version"] == 2
    )  # non-régression : pas de texte, pas de version 3
    mixed = Scene(
        [film.primitive("cube"), film.text3d("A")], [], Camera((0, 0, 4), (0, 0, 0))
    )
    film.place_scene(mixed, duration=1.0, start=2.0, name="mixte")
    assert json.loads((film.assets_dir / "mixte.kut3d").read_bytes())["version"] == 3


def test_film_primitive_accepts_the_text_kind_too(tmp_path: Path) -> None:
    inst = _film(tmp_path).primitive("text", text="Z", size=2.0)
    assert inst.model.primitive == Text3D("Z", size=2.0)
    assert isinstance(_film(tmp_path).primitive("cube").model.primitive, Cube)


@needs_ffmpeg
def test_the_exported_mp4_shows_the_title(tmp_path: Path, qapp) -> None:  # type: ignore[no-untyped-def]
    film = _film(tmp_path)
    film.background((0.05, 0.06, 0.12))
    film.place_scene(_title_scene(film), duration=1.0, name="titre")
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
    gold = (frame[..., 0] > 150) & (frame[..., 2] < 90)  # le texte doré
    assert gold.sum() > 60, int(gold.sum())
    assert abs(frame[1, 1] - np.array([13, 15, 31])).max() <= 12  # le fond
    ys, xs = np.nonzero(gold)
    assert (
        abs((xs.min() + xs.max()) / 2 - SIZE[0] / 2) < 8
        and abs((ys.min() + ys.max()) / 2 - SIZE[1] / 2) < 8
    )  # centré


# --- verbe de script ---------------------------------------------------------------------------------------------


def _project() -> Project:
    return Project(
        "Démo", width=160, height=90, fps=10.0, tracks=[Track("V1", "V1", "video")]
    )


def _op(path: str) -> dict:  # type: ignore[type-arg]
    return {
        "kut_script": 1,
        "ops": [
            {
                "op": "add_scene3d_clip",
                "path": path,
                "at": 0.0,
                "duration": 2.0,
                "id": "t",
            }
        ],
    }


def test_the_script_verb_accepts_a_kut3d_with_a_text(tmp_path: Path) -> None:
    path = tmp_path / "titre.kut3d"
    kut3d.save_scene(
        Scene(
            [kut3d.primitive_instance("text", text="Salut")],
            [],
            Camera((0, 0, 5), (0, 0, 0)),
        ),
        path,
    )
    session = Session(_project())
    report = apply_script(
        session, parse_script(_op("titre.kut3d")), context=ScriptContext(tmp_path)
    )
    assert report.ok, report.error
    clip = next(
        c for t in session.project.tracks if t.type == "graphics" for c in t.clips
    )
    assert clip.graphic.type.value == "scene3d" and clip.graphic.source_path == str(
        path.resolve()
    )


@pytest.mark.parametrize(
    "primitive",
    [
        {"kind": "text", "text": "x" * 10_000},
        {"kind": "text", "text": "\U0001f600"},
        {"kind": "text", "text": "A", "font": "Police Qui N'Existe Pas"},
        {"kind": "text", "text": "A", "size": 1e30},
    ],
    ids=["long", "emoji", "police", "taille"],
)
def test_the_script_verb_refuses_a_hostile_text_file_with_a_short_message(
    tmp_path: Path, primitive: dict
) -> None:  # type: ignore[type-arg]
    doc = {
        "format": "kut3d",
        "version": 3,
        "scene": {
            "camera": {"position": [0, 0, 5], "target": [0, 0, 0]},
            "models": [{"id": "p", "primitive": primitive}],
        },
    }
    (tmp_path / "mal.kut3d").write_text(json.dumps(doc), encoding="utf-8")
    session = Session(_project())
    report = apply_script(
        session, parse_script(_op("mal.kut3d")), context=ScriptContext(tmp_path)
    )
    assert (
        not report.ok and "Scène 3D refusée" in report.error and len(report.error) < 800
    )
    assert not any(t.clips for t in session.project.tracks)
