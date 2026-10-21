"""Verbe de script ``add_image_layer`` : une image fixe du dossier du script devient un calque graphique (ADR-0005).

Frontière de sécurité : lecture seule, dans le dossier du script, en-tête seul lu avant d'accepter les dimensions."""

from __future__ import annotations

import json
import struct
import sys
import zlib
from pathlib import Path

import pytest
from render_probe import needs_ffmpeg, render_frame

from core import cli
from core.project_io import load_project, save_project
from core.project_model import Project, Track
from core.render_plan import build_render_plan
from core.scripting import ScriptContext, ScriptInvalid, Session, apply_script, parse_script
from core.scripting import image_layer
from core.scripting.verbs import VERBS
from ui import i18n


@pytest.fixture(autouse=True)
def _french(monkeypatch):
    monkeypatch.setenv("KUT_STUDIO_LANG", "fr")
    i18n.reset_for_tests()
    yield
    i18n.reset_for_tests()


def _project(**kw) -> Project:
    return Project("Démo", width=160, height=90, fps=10.0, tracks=[Track("V1", "V1", "video")], **kw)


def _apply(project: Project, *ops: dict, folder: Path | None = None, allow_outside: bool = False):
    session = Session(project)
    context = ScriptContext(folder, allow_outside=allow_outside) if folder is not None else None
    return session, apply_script(session, parse_script({"kut_script": 1, "ops": list(ops)}), context=context)


def _image(path: Path, colour: str = "#E01010", size=(160, 90), fmt: str = "PNG") -> Path:
    from PySide6.QtGui import QColor, QImage

    image = QImage(size[0], size[1], QImage.Format_RGB32)
    image.fill(QColor(colour))
    assert image.save(str(path), fmt)
    return path


def _png_header(width: int, height: int) -> bytes:
    """Un PNG dont seul l'en-tête est honnête : de quoi tester des dimensions démesurées sans fichier démesuré."""
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    def block(tag: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body))

    return b"\x89PNG\r\n\x1a\n" + block(b"IHDR", ihdr) + block(b"IDAT", zlib.compress(b"\x00")) + block(b"IEND", b"")


def _op(path: str = "a.png", **extra) -> dict:
    return {"op": "add_image_layer", "path": path, "at": 0, "duration": 1.5, **extra}


def test_the_verb_is_registered_with_a_bounded_duration():
    verb = VERBS["add_image_layer"]
    duration = next(param for param in verb.params if param.name == "duration")
    assert duration.required and duration.minimum == 0.04 and verb.needs_context and verb.creates == "clip"


def test_the_image_becomes_a_graphic_layer_with_the_given_duration(tmp_path, qapp):
    _image(tmp_path / "a.png", size=(80, 45))
    session, report = _apply(_project(), _op(id="logo"), folder=tmp_path)
    assert report.ok, report.error
    track = next(item for item in session.project.tracks if item.type == "graphics")
    clip = next(item for item in track.clips if item.id == "logo")
    assert clip.graphic.type.value == "image" and clip.duration == pytest.approx(1.5)
    assert (clip.graphic.width, clip.graphic.height) == (80, 45), "l'image tient dans le cadre sans être agrandie"
    asset = next(item for item in session.project.media_assets if item.id == clip.asset_id)
    assert (asset.width, asset.height) == (80, 45) and asset.media_type == "graphic"
    assert report.ok and session.can_undo
    session.undo()
    assert not any(item.type == "graphics" for item in session.project.tracks) and not session.project.media_assets


def test_a_big_image_is_fitted_inside_the_frame(tmp_path, qapp):
    _image(tmp_path / "a.png", size=(400, 90))
    session, report = _apply(_project(), _op(), folder=tmp_path)
    clip = next(item for track in session.project.tracks for item in track.clips)
    assert report.ok and (clip.graphic.width, clip.graphic.height) == (160, 36)


@needs_ffmpeg
def test_end_to_end_the_exported_frame_shows_the_image(tmp_path, qapp):
    _image(tmp_path / "a.png", "#E01010")
    session, report = _apply(_project(), _op(duration=2.0), folder=tmp_path)
    assert report.ok, report.error
    plan = build_render_plan(session.project)
    assert len(plan.graphics_layers) == 1 and not plan.video_layers
    frame = render_frame(plan, 160, 90, 0.5).astype(int)
    for pixel in (frame[45, 80], frame[2, 2]):
        assert pixel[0] > 200 and pixel[1] < 40 and pixel[2] < 40, ("rouge attendu", pixel)


@needs_ffmpeg
def test_a_jpeg_and_a_webp_are_accepted_too(tmp_path, qapp):
    _image(tmp_path / "a.jpg", "#10E010", fmt="JPEG")
    _image(tmp_path / "b.webp", "#1010E0", fmt="WEBP")
    session, report = _apply(_project(), _op("a.jpg"), _op("b.webp", at=3), folder=tmp_path)
    assert report.ok, report.error
    frame = render_frame(build_render_plan(session.project), 160, 90, 0.5).astype(int)
    assert frame[45, 80][1] > 180 and frame[45, 80][0] < 60


def test_it_is_refused_without_a_file_context(qapp):
    _session, report = _apply(_project(), _op())
    assert not report.ok and "dossier du script" in report.error


def test_a_path_outside_the_folder_needs_allow_outside(tmp_path, qapp):
    folder = tmp_path / "montage"
    folder.mkdir()
    _image(tmp_path / "ailleurs.png")
    for relative in ("../ailleurs.png", str(tmp_path / "ailleurs.png")):
        session, report = _apply(_project(), _op(relative), folder=folder)
        assert not report.ok and "hors du dossier du script" in report.error and not session.can_undo
    _session, report = _apply(_project(), _op("../ailleurs.png"), folder=folder, allow_outside=True)
    assert report.ok, "--allow-outside est la décision de l'appelant"


@pytest.mark.skipif(sys.platform.startswith("win"), reason="liens symboliques : droits particuliers sous Windows")
def test_a_symlink_that_escapes_the_folder_is_refused(tmp_path, qapp):
    folder = tmp_path / "montage"
    folder.mkdir()
    (folder / "lien.png").symlink_to(_image(tmp_path / "secret.png"))
    session, report = _apply(_project(), _op("lien.png"), folder=folder)
    assert not report.ok and "hors du dossier du script" in report.error and not session.can_undo
    inside = _image(folder / "reel.png")
    (folder / "alias.png").symlink_to(inside)
    _session, report = _apply(_project(), _op("alias.png"), folder=folder)
    assert report.ok, "un lien qui reste dans le dossier est permis, comme pour import_media"


def test_missing_file_and_folder_are_refused(tmp_path, qapp):
    (tmp_path / "dossier.png").mkdir()
    for name in ("absente.png", "dossier.png"):
        session, report = _apply(_project(), _op(name), folder=tmp_path)
        assert not report.ok and not session.can_undo


def test_an_empty_corrupt_or_fake_file_is_refused_with_a_clear_message(tmp_path, qapp):
    (tmp_path / "vide.png").write_bytes(b"")
    (tmp_path / "abime.png").write_bytes(b"ceci n'est pas une image" * 20)
    (tmp_path / "entete.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (tmp_path / "texte.txt").write_text("x", encoding="utf-8")
    messages = {}
    for name in ("vide.png", "abime.png", "entete.png", "texte.txt"):
        session, report = _apply(_project(), _op(name), folder=tmp_path)
        assert not report.ok and not session.can_undo and not session.project.tracks[0].clips
        messages[name] = report.error
    assert "est vide" in messages["vide.png"]
    assert "lisible" in messages["abime.png"] and "lisible" in messages["entete.png"]
    assert "pas une image acceptée" in messages["texte.txt"]
    assert all(len(message) < 400 for message in messages.values()), "le message ne recopie pas le fichier"


def test_the_extension_must_match_the_content(tmp_path, qapp):
    _image(tmp_path / "png_deguise.jpg", fmt="PNG")
    _image(tmp_path / "jpeg_deguise.png", fmt="JPEG")
    for name in ("png_deguise.jpg", "jpeg_deguise.png"):
        session, report = _apply(_project(), _op(name), folder=tmp_path)
        assert not report.ok and "ne correspond pas au contenu" in report.error and not session.can_undo


def test_huge_dimensions_are_refused_from_the_header_alone(tmp_path, qapp):
    for name, size in (("large.png", (100_000, 10)), ("pixels.png", (8192, 8192))):
        (tmp_path / name).write_bytes(_png_header(*size))
        session, report = _apply(_project(), _op(name), folder=tmp_path)
        assert not report.ok and "trop grande" in report.error and not session.can_undo, name


def test_a_huge_file_is_refused_before_it_is_read(tmp_path, qapp, monkeypatch):
    _image(tmp_path / "a.png")
    monkeypatch.setattr(image_layer, "MAX_IMAGE_BYTES", 10)
    session, report = _apply(_project(), _op(), folder=tmp_path)
    assert not report.ok and "trop lourde" in report.error and not session.can_undo


@pytest.mark.parametrize("duration", [0, -1, 0.01, float("nan"), float("inf"), 2_000_000, "3", True, None])
def test_the_duration_is_bounded_by_the_decoder(duration):
    op = _op()
    op["duration"] = duration
    with pytest.raises(ScriptInvalid):
        parse_script({"kut_script": 1, "ops": [op]})


def test_the_duration_time_and_path_are_required():
    for missing in ("duration", "at", "path"):
        op = _op()
        del op[missing]
        with pytest.raises(ScriptInvalid):
            parse_script({"kut_script": 1, "ops": [op]})


def test_a_locked_or_wrong_kind_or_unknown_track_is_refused(tmp_path, qapp):
    _image(tmp_path / "a.png")
    project = _project()
    project.tracks.append(Track("G1", "G1", "graphics", locked=True))
    project.tracks.append(Track("G2", "G2", "graphics"))
    for selector, expected in (("G1", "verrouillée"), ("V1", "piste de graphiques"), ("absente", "")):
        session, report = _apply(project, _op(track=selector), folder=tmp_path)
        assert not report.ok and expected in report.error and not session.can_undo, selector
        assert not any(track.clips for track in project.tracks) and len(project.media_assets) == 0
    _session, report = _apply(project, _op(track="G2"), folder=tmp_path)
    assert report.ok


def test_a_failure_later_in_the_script_leaves_nothing_behind(tmp_path, qapp):
    _image(tmp_path / "a.png")
    session, report = _apply(_project(), _op(), {"op": "add_image_layer", "path": "absente.png", "at": 0, "duration": 1}, folder=tmp_path)
    assert not report.ok and not session.can_undo
    assert not session.project.media_assets and not any(track.type == "graphics" for track in session.project.tracks)


def test_the_cli_reaches_the_verb_and_the_layer_survives_a_save(tmp_path, qapp, capsys):
    folder = tmp_path / "montage"
    folder.mkdir()
    _image(folder / "a.png")
    project = tmp_path / "p.kut"
    save_project(_project(), str(project))
    script = folder / "s.json"
    script.write_text(json.dumps({"kut_script": 1, "ops": [_op(**{"as": "logo"})]}), encoding="utf-8")
    code = cli.run(["apply", str(script), str(project), "-o", str(tmp_path / "o.kut"), "--json"])
    data = json.loads(capsys.readouterr().out)
    assert code == 0 and data["created"] == [{"op": "0:add_image_layer", "id": "logo"}]
    result = load_project(str(tmp_path / "o.kut"))
    graphics = next(track for track in result.tracks if track.type == "graphics")
    assert graphics.clips[0].graphic.source_path == str((folder / "a.png").resolve())
    (folder / "s.json").write_text(json.dumps({"kut_script": 1, "ops": [_op("../x.png")]}), encoding="utf-8")
    code = cli.run(["apply", str(script), str(project), "-o", str(tmp_path / "o2.kut")])
    assert code == cli.EXIT_FAILED and not (tmp_path / "o2.kut").exists()
    capsys.readouterr()
