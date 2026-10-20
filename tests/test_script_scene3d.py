"""Verbe de script ``add_scene3d_clip`` : un fichier ``.kut3d`` du dossier du script devient un calque « scène 3D » (ADR-0005, ADR-0009, lot B4).

Frontière de sécurité : lecture seule, confiné au dossier du script (même avec ``--allow-outside``), fichier lu AVANT toute écriture.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from core import cli
from core.project_io import SCENE3D_VERSION, load_project, save_project
from core.project_model import Project, Track
from core.scripting import ScriptContext, ScriptInvalid, Session, apply_script, json_schema, parse_script
from core.scripting.api import describe
from core.scripting.verbs import VERBS
from tests.scene3d_render_samples import write_static_scene
from ui import i18n


@pytest.fixture(autouse=True)
def _french(monkeypatch):
    monkeypatch.setenv("KUT_STUDIO_LANG", "fr")
    i18n.reset_for_tests()
    yield
    i18n.reset_for_tests()


def _project(**kw) -> Project:
    return Project("Démo", width=160, height=90, fps=10.0, tracks=[Track("V1", "V1", "video")], **kw)


def _op(path: str = "static.kut3d", **extra) -> dict:
    return {"op": "add_scene3d_clip", "path": path, "at": 0.5, "duration": 1.5, **extra}


def _apply(project: Project, *ops: dict, folder: Path | None, allow_outside: bool = False):
    session = Session(project)
    context = ScriptContext(folder, allow_outside=allow_outside) if folder is not None else None
    return session, apply_script(session, parse_script({"kut_script": 1, "ops": list(ops)}), context=context)


def _snapshot(project: Project):
    return deepcopy(project.__dict__)


def test_the_verb_is_registered_like_the_image_layer_with_a_required_bounded_duration():
    verb = VERBS["add_scene3d_clip"]
    duration = next(param for param in verb.params if param.name == "duration")
    assert duration.required and duration.minimum == 0.04 and verb.needs_context and verb.creates == "clip" and verb.group == "graphics"
    assert [param.name for param in verb.params] == [param.name for param in VERBS["add_image_layer"].params]
    names = {item["properties"]["op"]["const"] for item in json_schema()["properties"]["ops"]["items"]["oneOf"]}
    assert "add_scene3d_clip" in names


def test_add_graphic_still_cannot_create_a_scene_without_a_file():
    graphic = next(param for param in VERBS["add_graphic"].params if param.name == "graphic")
    assert "scene3d" not in graphic.choices


def test_the_scene_becomes_a_graphic_layer_on_the_right_track_with_the_given_duration(tmp_path):
    scene = write_static_scene(tmp_path)
    session, report = _apply(_project(), _op(id="cube3d"), folder=tmp_path)
    assert report.ok, report.error
    track = next(item for item in session.project.tracks if item.type == "graphics")
    clip = next(item for item in track.clips if item.id == "cube3d")
    assert clip.graphic.type.value == "scene3d" and clip.duration == pytest.approx(1.5) and clip.timeline_start == pytest.approx(0.5)
    assert clip.graphic.source_path == str(scene.resolve()) and Path(clip.graphic.source_path).is_absolute()
    asset = next(item for item in session.project.media_assets if item.id == clip.asset_id)
    assert asset.path == str(scene.resolve())


def test_an_explicit_track_is_used(tmp_path):
    write_static_scene(tmp_path)
    project = _project()
    project.tracks.append(Track("G1", "G1", "graphics"))
    project.tracks.append(Track("G2", "G2", "graphics"))
    session, report = _apply(project, _op(track="G2", id="c"), folder=tmp_path)
    assert report.ok, report.error
    assert [clip.id for clip in session.project.tracks[-1].clips] == ["c"] and not session.project.tracks[-2].clips


def test_undo_removes_the_clip_and_the_asset(tmp_path):
    write_static_scene(tmp_path)
    project = _project()
    tracks_before = [track.id for track in project.tracks]
    session, report = _apply(project, _op(), folder=tmp_path)
    assert report.ok and session.can_undo
    assert session.undo()
    assert not session.project.media_assets and not any(track.clips for track in session.project.tracks)
    assert [track.id for track in session.project.tracks] == tracks_before


def test_the_project_is_saved_in_version_22_and_round_trips(tmp_path):
    scene = write_static_scene(tmp_path)
    session, report = _apply(_project(), _op(id="cube3d"), folder=tmp_path)
    assert report.ok, report.error
    target = tmp_path / "p.kut"
    save_project(session.project, str(target))
    assert json.loads(target.read_text(encoding="utf-8"))["version"] == SCENE3D_VERSION == 22
    loaded = load_project(str(target))
    clip = next(clip for track in loaded.tracks for clip in track.clips if clip.id == "cube3d")
    assert clip.graphic.type.value == "scene3d" and clip.graphic.source_path == str(scene.resolve())
    assert clip.duration == pytest.approx(1.5)


def test_describe_reads_the_scene_layer_with_its_source_path(tmp_path):
    scene = write_static_scene(tmp_path)
    session, report = _apply(_project(), _op(id="cube3d"), folder=tmp_path)
    assert report.ok, report.error
    clips = [clip for sequence in describe(session.project)["sequences"] for track in sequence["tracks"] for clip in track["clips"]]
    clip = next(item for item in clips if item["id"] == "cube3d")
    assert clip["kind"] == "graphic" and clip["graphic"]["type"] == "scene3d" and clip["graphic"]["source_path"] == str(scene.resolve())


def _refused(project: Project, op: dict, folder: Path | None, expected: str, **kw) -> None:
    before = _snapshot(project)
    session, report = _apply(project, op, folder=folder, **kw)
    assert not report.ok and expected in report.error, report.error
    assert len(report.error) < 800, "le message ne recopie pas l'entrée entière"
    assert not session.can_undo
    assert _snapshot(session.project) == before, "projet intact après un refus"


def test_a_locked_or_wrong_kind_or_unknown_track_is_refused(tmp_path):
    write_static_scene(tmp_path)
    project = _project()
    project.tracks += [
        Track("G1", "G1", "graphics", locked=True), Track("A1", "A1", "audio"), Track("S1", "S1", "subtitle"),
    ]
    for selector, expected in (("G1", "verrouillée"), ("V1", "piste de graphiques"), ("A1", "piste de graphiques"),
                               ("S1", "piste de graphiques"), ("absente", "")):
        _refused(project, _op(track=selector), tmp_path, expected)


@pytest.mark.parametrize("duration", [0, 0.01, -1, 2_000_000])
def test_a_duration_out_of_bounds_is_refused_by_the_parser(tmp_path, duration):
    write_static_scene(tmp_path)
    with pytest.raises(ScriptInvalid):
        parse_script({"kut_script": 1, "ops": [_op(duration=duration)]})


def test_an_unreadable_or_wrong_extension_scene_is_refused(tmp_path):
    (tmp_path / "bad.kut3d").write_text("pas du json " * 50, encoding="utf-8")
    (tmp_path / "cube.txt").write_text("{}", encoding="utf-8")
    _refused(_project(), _op("bad.kut3d"), tmp_path, "Scène 3D refusée")
    _refused(_project(), _op("cube.txt"), tmp_path, ".kut3d")
    _refused(_project(), _op("absent.kut3d"), tmp_path, "introuvable")


def test_a_scene_whose_model_is_missing_is_refused_before_any_write(tmp_path):
    scene = write_static_scene(tmp_path)
    (tmp_path / "cube.obj").unlink()
    assert scene.is_file()
    _refused(_project(), _op(), tmp_path, "Scène 3D refusée")


def test_a_scene_outside_the_script_folder_is_refused_even_with_allow_outside(tmp_path):
    folder = tmp_path / "script"
    folder.mkdir()
    elsewhere = tmp_path / "ailleurs"
    elsewhere.mkdir()
    scene = write_static_scene(elsewhere)
    for path in (str(scene), "../ailleurs/static.kut3d"):
        _refused(_project(), _op(path), folder, "hors du dossier", allow_outside=False)
        _refused(_project(), _op(path), folder, "hors du dossier", allow_outside=True)


def test_a_model_outside_the_scene_folder_is_refused(tmp_path):
    folder = tmp_path / "script"
    folder.mkdir()
    write_static_scene(folder)
    document = json.loads((folder / "static.kut3d").read_text(encoding="utf-8"))
    document["scene"]["models"][0]["path"] = "../cube.obj"
    (folder / "escape.kut3d").write_text(json.dumps(document), encoding="utf-8")
    _refused(_project(), _op("escape.kut3d"), folder, "Scène 3D refusée", allow_outside=True)


def test_without_a_file_context_the_verb_is_refused(tmp_path):
    write_static_scene(tmp_path)
    _refused(_project(), _op(), None, "dossier du script")


def test_a_failure_later_in_the_script_is_all_or_nothing(tmp_path):
    write_static_scene(tmp_path)
    project = _project()
    before = _snapshot(project)
    session, report = _apply(project, _op(), _op("absent.kut3d"), folder=tmp_path)
    assert not report.ok and not session.can_undo
    assert _snapshot(session.project) == before


def test_an_oversized_scene_message_does_not_echo_the_input(tmp_path):
    huge = "x" * 900 + ".kut3d"
    session, report = _apply(_project(), _op(huge), folder=tmp_path)
    assert not report.ok and len(report.error) < 600 and not session.can_undo


def test_the_cli_reaches_the_verb_and_the_clip_survives_a_save(tmp_path, capsys):
    folder = tmp_path / "montage"
    folder.mkdir()
    write_static_scene(folder)
    project = tmp_path / "p.kut"
    save_project(_project(), str(project))
    script = folder / "s.json"
    script.write_text(json.dumps({"kut_script": 1, "ops": [_op(**{"as": "cube3d"})]}), encoding="utf-8")
    code = cli.run(["apply", str(script), str(project), "-o", str(tmp_path / "o.kut"), "--json"])
    data = json.loads(capsys.readouterr().out)
    assert code == 0 and data["created"] == [{"op": "0:add_scene3d_clip", "id": "cube3d"}]
    assert json.loads((tmp_path / "o.kut").read_text(encoding="utf-8"))["version"] == 22
    graphics = next(track for track in load_project(str(tmp_path / "o.kut")).tracks if track.type == "graphics")
    assert graphics.clips[0].graphic.source_path == str((folder / "static.kut3d").resolve())
    for path in ("../x.kut3d", "bad.txt"):
        script.write_text(json.dumps({"kut_script": 1, "ops": [_op(path)]}), encoding="utf-8")
        out = tmp_path / "o2.kut"
        code = cli.run(["apply", str(script), str(project), "-o", str(out), "--allow-outside"])
        assert code == cli.EXIT_FAILED and not out.exists()
    capsys.readouterr()


def test_a_locked_track_is_refused_by_the_cli_and_the_project_stays_intact(tmp_path, capsys):
    folder = tmp_path / "montage"
    folder.mkdir()
    write_static_scene(folder)
    base = _project()
    base.tracks.append(Track("G1", "G1", "graphics", locked=True))
    project = tmp_path / "p.kut"
    save_project(base, str(project))
    script = folder / "s.json"
    script.write_text(json.dumps({"kut_script": 1, "ops": [_op(track="G1")]}), encoding="utf-8")
    code = cli.run(["apply", str(script), str(project), "-o", str(tmp_path / "o.kut")])
    assert code == cli.EXIT_FAILED and not (tmp_path / "o.kut").exists()
    capsys.readouterr()
