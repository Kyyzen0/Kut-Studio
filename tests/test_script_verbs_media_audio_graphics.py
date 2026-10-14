"""Format déclaratif, lot 2 (ADR-0005 étape 4) : import de médias sous contexte de fichiers, inserts de piste (dont l'égaliseur),
état des pistes, calques graphiques.

Le point sensible est ``import_media`` : c'est le seul verbe qui lit un fichier. Il ne lit que dans le dossier du script, liens
symboliques résolus, sauf si **l'appelant** (jamais le script) a dit ``--allow-outside``.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import wave
from pathlib import Path

import pytest

from core import cli
from core.project_io import load_project, save_project
from core.project_model import MediaAsset, Project, Track
from core.scripting import ScriptContext, ScriptInvalid, Session, apply_script, created_ids, parse_script
from core.scripting.verbs import VERBS
from core.timeline_operations import find_clip
from ui import i18n

needs_ffprobe = pytest.mark.skipif(shutil.which("ffprobe") is None, reason="ffprobe absent")


@pytest.fixture(autouse=True)
def _french(monkeypatch):
    monkeypatch.setenv("KUT_STUDIO_LANG", "fr")
    i18n.reset_for_tests()
    yield
    i18n.reset_for_tests()


def _project() -> Project:
    return Project(
        "Démo", width=1280, height=720, fps=30.0,
        media_assets=[MediaAsset("m", "/x/intro.mp4", "intro", 10.0, 1280, 720, 30.0, "video", has_audio=True)],
        tracks=[Track("V1", "V1", "video"), Track("A1", "A1", "audio")],
    )


def _script(*ops: dict) -> dict:
    return {"kut_script": 1, "ops": list(ops)}


def _apply(project: Project, *ops: dict, context: ScriptContext | None = None):
    session = Session(project)
    return session, apply_script(session, parse_script(_script(*ops)), context=context)


def _wav(path: Path, seconds: float = 0.2) -> Path:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"\x00\x00" * int(8000 * seconds))
    return path


@pytest.fixture
def fake_probe(monkeypatch):
    """Une sonde sans ffprobe : le test porte sur les chemins, pas sur le média."""
    def probe(path: str) -> MediaAsset:
        return MediaAsset("probe-id", path, os.path.basename(path), 1.0, 0, 0, 0.0, "audio", has_audio=True)

    monkeypatch.setattr("core.scripting.verbs.probe_media", probe)


# --- import_media : confinement ---------------------------------------------------------------------------------------------


def test_import_media_reads_inside_the_script_folder(tmp_path, fake_probe):
    folder = tmp_path / "montage"
    folder.mkdir()
    sound = _wav(folder / "voix.wav")
    session, report = _apply(_project(), {"op": "import_media", "path": "voix.wav", "as": "voix"}, context=ScriptContext(folder))
    assert report.ok, report.error
    asset = next(item for item in session.project.media_assets if item.id == "voix")
    assert asset.path == str(sound.resolve()) and asset.name == "voix.wav"
    assert created_ids(parse_script(_script({"op": "import_media", "path": "x", "as": "voix"})), report) == [{"op": "0:import_media", "id": "voix"}]


def test_a_script_cannot_leave_its_folder_without_the_callers_consent(tmp_path, fake_probe):
    folder = tmp_path / "montage"
    folder.mkdir()
    _wav(tmp_path / "secret.wav")
    for path in ("../secret.wav", str(tmp_path / "secret.wav"), "sub/../../secret.wav"):
        session, report = _apply(_project(), {"op": "import_media", "path": path}, context=ScriptContext(folder))
        assert not report.ok and "hors du dossier du script" in report.error, path
        assert len(session.project.media_assets) == 1, "rien n'a été importé"
    session, report = _apply(_project(), {"op": "import_media", "path": "../secret.wav"}, context=ScriptContext(folder, allow_outside=True))
    assert report.ok, "--allow-outside est la décision de l'appelant"


@pytest.mark.skipif(sys.platform.startswith("win"), reason="liens symboliques : droits particuliers sous Windows")
def test_a_symlink_that_escapes_the_folder_is_refused(tmp_path, fake_probe):
    folder = tmp_path / "montage"
    folder.mkdir()
    target = _wav(tmp_path / "secret.wav")
    (folder / "lien.wav").symlink_to(target)
    _session, report = _apply(_project(), {"op": "import_media", "path": "lien.wav"}, context=ScriptContext(folder))
    assert not report.ok and "hors du dossier du script" in report.error
    inside = _wav(folder / "reel.wav")
    (folder / "alias.wav").symlink_to(inside)
    _session, report = _apply(_project(), {"op": "import_media", "path": "alias.wav"}, context=ScriptContext(folder))
    assert report.ok, "un lien qui reste dans le dossier est permis"


def test_import_media_refuses_a_missing_file_a_folder_and_a_duplicate(tmp_path, fake_probe):
    folder = tmp_path / "montage"
    (folder / "dossier.wav").mkdir(parents=True)
    context = ScriptContext(folder)
    for path in ("absent.wav", "dossier.wav"):
        _session, report = _apply(_project(), {"op": "import_media", "path": path}, context=context)
        assert not report.ok and "introuvable" in report.error, path
    _wav(folder / "a.wav")
    session, report = _apply(
        _project(), {"op": "import_media", "path": "a.wav", "id": "a"}, {"op": "import_media", "path": "a.wav", "id": "b"}, context=context,
    )
    assert not report.ok and report.failed_index == 1 and "déjà dans le projet" in report.error
    assert len(session.project.media_assets) == 1, "tout ou rien : le premier import est annulé avec le second"


def test_import_media_without_a_file_context_is_refused(fake_probe):
    _session, report = _apply(_project(), {"op": "import_media", "path": "a.wav"})
    assert not report.ok and "contexte de fichiers" in report.error


def test_an_imported_media_can_be_used_by_a_later_operation_through_its_alias(tmp_path):
    """Intégration : la sonde réelle, puis ``add_clip`` sur le média importé, par alias."""
    if shutil.which("ffprobe") is None:
        pytest.skip("ffprobe absent")
    folder = tmp_path / "montage"
    folder.mkdir()
    _wav(folder / "voix.wav", 0.5)
    session, report = _apply(
        _project(), {"op": "import_media", "path": "voix.wav", "as": "voix"},
        {"op": "add_clip", "track": "A1", "asset": {"ref": "voix"}, "at": 1.0, "as": "clip_voix"}, context=ScriptContext(folder),
    )
    assert report.ok, report.error
    clip = find_clip(session.project, "clip_voix")
    assert clip.asset_id == "voix" and clip.timeline_start == 1.0 and 0.4 < clip.duration < 0.6


# --- import_media : la commande -------------------------------------------------------------------------------------------


def test_apply_resolves_media_from_the_script_folder_and_needs_allow_outside_to_leave_it(tmp_path, capsys, fake_probe):
    folder = tmp_path / "montage"
    folder.mkdir()
    _wav(tmp_path / "ailleurs.wav")
    project = tmp_path / "p.kut"
    save_project(_project(), str(project))
    script = folder / "s.json"
    script.write_text(json.dumps(_script({"op": "import_media", "path": "../ailleurs.wav", "as": "dehors"})), encoding="utf-8")
    code = cli.run(["apply", str(script), str(project), "-o", str(tmp_path / "o.kut"), "--json"])
    captured = capsys.readouterr()
    assert code == cli.EXIT_FAILED and "hors du dossier du script" in captured.err and not (tmp_path / "o.kut").exists()
    code = cli.run(["apply", str(script), str(project), "-o", str(tmp_path / "o.kut"), "--allow-outside", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert code == 0 and data["created"] == [{"op": "0:import_media", "id": "dehors"}]
    assert "dehors" in {asset.id for asset in load_project(str(tmp_path / "o.kut")).media_assets}


@needs_ffprobe
def test_apply_end_to_end_with_the_real_probe(tmp_path, capsys):
    folder = tmp_path / "montage"
    folder.mkdir()
    _wav(folder / "voix.wav", 0.3)
    project = tmp_path / "p.kut"
    save_project(_project(), str(project))
    script = folder / "s.json"
    script.write_text(json.dumps(_script(
        {"op": "import_media", "path": "voix.wav", "as": "voix"}, {"op": "add_clip", "track": "A1", "asset": "voix", "at": 0},
    )), encoding="utf-8")
    code = cli.run(["apply", str(script), str(project), "-o", str(tmp_path / "o.kut")])
    capsys.readouterr()
    assert code == 0
    result = load_project(str(tmp_path / "o.kut"))
    assert any(asset.id == "voix" and asset.media_type == "audio" for asset in result.media_assets)
    assert len(next(track for track in result.tracks if track.id == "A1").clips) == 1


# --- Inserts de piste et égaliseur ------------------------------------------------------------------------------------------


def test_a_track_equalizer_is_a_track_insert_that_can_be_tuned_toggled_and_removed():
    session, report = _apply(
        _project(),
        {"op": "add_track_insert", "track": "A1", "effect": "parametric_eq", "as": "eq", "params": {"low_gain_db": 3.0, "mid2_hz": 2500}},
        {"op": "update_track_insert", "track": "A1", "insert": {"ref": "eq"}, "params": {"high_gain_db": -2.0}},
        {"op": "set_track_insert_enabled", "track": "A1", "insert": "eq", "enabled": False},
    )
    assert report.ok, report.error
    insert = next(track for track in session.project.tracks if track.id == "A1").inserts[0]
    assert insert.id == "eq" and insert.enabled is False
    assert insert.params["low_gain_db"] == 3.0 and insert.params["high_gain_db"] == -2.0 and insert.params["mid2_hz"] == 2500
    _session, removed = _apply(session.project, {"op": "remove_track_insert", "track": "A1", "insert": "eq"})
    assert removed.ok and next(track for track in session.project.tracks if track.id == "A1").inserts == []


def test_the_model_bounds_effect_parameters_so_a_script_cannot_smuggle_a_value_past_them():
    session, report = _apply(_project(), {"op": "add_track_insert", "track": "A1", "effect": "parametric_eq", "params": {"low_gain_db": 99}})
    assert not report.ok and "hors bornes" in report.error and next(t for t in session.project.tracks if t.id == "A1").inserts == []
    _session, report = _apply(_project(), {"op": "add_track_insert", "track": "A1", "effect": "parametric_eq", "params": {"inconnu": 1}})
    assert not report.ok and "inconnu" in report.error


def test_inserts_do_not_apply_to_a_subtitle_track_nor_to_a_locked_one():
    project = _project()
    project.tracks.append(Track("S1", "S1", "subtitle"))
    _session, report = _apply(project, {"op": "add_track_insert", "track": "S1", "effect": "limiter"})
    assert not report.ok
    _session, report = _apply(_project(), {"op": "set_track_locked", "track": "A1", "locked": True},
                              {"op": "add_track_insert", "track": "A1", "effect": "limiter"})
    assert not report.ok and report.failed_index == 1


# --- État des pistes -------------------------------------------------------------------------------------------------------


def test_track_state_verbs():
    session, report = _apply(
        _project(), {"op": "set_track_muted", "track": "A1", "muted": True}, {"op": "set_track_visible", "track": "V1", "visible": False},
        {"op": "set_track_locked", "track": "V1", "locked": True},
    )
    assert report.ok, report.error
    tracks = {track.id: track for track in session.project.tracks}
    assert tracks["A1"].muted is True and tracks["V1"].visible is False and tracks["V1"].locked is True


# --- Calques graphiques ----------------------------------------------------------------------------------------------------


def test_a_title_is_added_then_restyled_and_stays_one_history_step():
    session, report = _apply(
        _project(),
        {"op": "add_graphic", "graphic": "text", "at": 0.5, "duration": 3, "text": "Bonjour\nle monde", "as": "titre"},
        {"op": "set_graphic", "clip": {"ref": "titre"}, "fill_color": "#FF8800", "stroke_color": "#000000CC", "stroke_width": 4, "font_size": 96},
    )
    assert report.ok, report.error
    clip = find_clip(session.project, "titre")
    graphic = clip.graphic
    assert graphic.text == "Bonjour\nle monde" and clip.text == "Bonjour\nle monde"
    assert (graphic.fill_color, graphic.stroke_color, graphic.stroke_width, graphic.font_size) == ("#FF8800", "#000000CC", 4, 96)
    assert clip.timeline_start == 0.5 and clip.duration == 3.0
    assert len(session.history.entries()) == 2


def test_a_shape_and_an_adjustment_layer_can_be_added_on_the_default_graphics_track():
    session, report = _apply(
        _project(), {"op": "add_graphic", "graphic": "shape", "shape": "ellipse", "at": 0, "duration": 2, "id": "rond"},
        {"op": "add_graphic", "graphic": "adjustment", "at": 0, "duration": 5, "id": "reglage"},
    )
    assert report.ok, report.error
    assert find_clip(session.project, "rond").graphic.shape.value == "ellipse"
    assert find_clip(session.project, "reglage").graphic.type.value == "adjustment"


def test_set_graphic_needs_a_field_and_a_graphic_clip():
    session, report = _apply(_project(), {"op": "add_graphic", "graphic": "text", "at": 0, "duration": 1, "id": "t"}, {"op": "set_graphic", "clip": "t"})
    assert not report.ok and "au moins un champ" in report.error
    _session, report = _apply(
        _project(), {"op": "add_clip", "track": "V1", "asset": "m", "at": 0, "id": "c"}, {"op": "set_graphic", "clip": "c", "font_size": 10},
    )
    assert not report.ok and "graphique" in report.error


@pytest.mark.parametrize("op", [
    {"op": "set_graphic", "clip": "t", "fill_color": "orange"},
    {"op": "set_graphic", "clip": "t", "fill_color": "#12345"},
    {"op": "set_graphic", "clip": "t", "fill_color": "#GGGGGG"},
    {"op": "set_graphic", "clip": "t", "font_size": 5},
    {"op": "set_graphic", "clip": "t", "font_size": 513},
    {"op": "set_graphic", "clip": "t", "font_size": 12.5},
    {"op": "set_graphic", "clip": "t", "font_size": True},
    {"op": "set_graphic", "clip": "t", "width": 8193},
    {"op": "set_graphic", "clip": "t", "width": 1},
    {"op": "set_graphic", "clip": "t", "text": "x" * 1001},
    {"op": "set_graphic", "clip": "t", "text": "a\x00b"},
    {"op": "add_graphic", "graphic": "image", "at": 0, "duration": 1},
    {"op": "add_graphic", "graphic": "text", "at": 0, "duration": 0},
    {"op": "add_graphic", "graphic": "text", "at": 0, "duration": -1},
    {"op": "add_graphic", "graphic": "shape", "shape": "pentagone-inconnu", "at": 0, "duration": 1},
    {"op": "import_media", "path": ""},
    {"op": "import_media", "path": "a\x00b"},
    {"op": "import_media", "path": "x" * 1025},
])
def test_graphic_and_path_values_are_checked_before_anything_runs(op):
    with pytest.raises(ScriptInvalid):
        parse_script(_script(op))


def test_a_multiline_text_is_only_allowed_where_the_verb_says_so():
    parse_script(_script({"op": "add_graphic", "graphic": "text", "at": 0, "duration": 1, "text": "a\nb"}))
    with pytest.raises(ScriptInvalid):
        parse_script(_script({"op": "rename_bus", "bus": "b", "name": "a\nb"}))


def test_every_graphic_bound_is_the_models_bound_so_nothing_is_clamped_in_silence():
    """Revue Codex : accepter font_size 1 puis voir le modèle stocker 6 faisait « réussir » un script sans appliquer sa valeur."""
    for name in ("stroke_width", "font_size", "width", "height"):
        param = next(item for item in VERBS["set_graphic"].params if item.name == name)
        for value in (param.minimum, param.maximum):
            session, report = _apply(
                _project(), {"op": "add_graphic", "graphic": "text", "at": 0, "duration": 1, "id": "t"},
                {"op": "set_graphic", "clip": "t", name: int(value)},
            )
            assert report.ok and getattr(find_clip(session.project, "t").graphic, name) == value, (name, value)
        for outside in (param.minimum - 1, param.maximum + 1):
            with pytest.raises(ScriptInvalid):
                parse_script(_script({"op": "set_graphic", "clip": "t", name: int(outside)}))


def test_a_corrupt_media_is_a_refusal_not_a_crash(tmp_path):
    """Revue Codex : MediaProbeError n'est ni ValueError, ni KeyError, ni OSError ; elle remontait en traceback."""
    if shutil.which("ffprobe") is None:
        pytest.skip("ffprobe absent")
    folder = tmp_path / "montage"
    folder.mkdir()
    (folder / "casse.wav").write_bytes(b"ceci n'est pas un fichier audio")
    session, report = _apply(_project(), {"op": "import_media", "path": "casse.wav"}, context=ScriptContext(folder))
    assert not report.ok and report.error_type == "ValueError" and len(session.project.media_assets) == 1


def test_a_missing_ffprobe_is_a_refusal_too(tmp_path, monkeypatch):
    from core.media_probe import MediaProbeError

    def no_ffprobe(_path: str):
        raise MediaProbeError("ffprobe est introuvable dans le PATH.")

    monkeypatch.setattr("core.scripting.verbs.probe_media", no_ffprobe)
    _wav(tmp_path / "a.wav")
    _session, report = _apply(_project(), {"op": "import_media", "path": "a.wav"}, context=ScriptContext(tmp_path))
    assert not report.ok and "ffprobe" in report.error


def test_a_graphic_goes_only_on_an_unlocked_graphics_track():
    project = _project()
    project.tracks.append(Track("G1", "Titres", "graphics", locked=True))
    for target, message in (("V1", "piste 'video'"), ("A1", "piste 'audio'"), ("G1", "verrouillée")):
        session, report = _apply(project, {"op": "add_graphic", "graphic": "text", "at": 0, "duration": 1, "track": target})
        assert not report.ok and message in report.error, target
    project.tracks[-1].locked = False
    _session, report = _apply(project, {"op": "add_graphic", "graphic": "text", "at": 0, "duration": 1, "track": "Titres", "id": "t"})
    assert report.ok and next(track for track in project.tracks if track.id == "G1").clips[0].id == "t"


def test_set_graphic_respects_a_locked_track():
    session, report = _apply(
        _project(), {"op": "add_graphic", "graphic": "text", "at": 0, "duration": 1, "id": "t", "text": "avant"},
        {"op": "set_track_locked", "track": "G1", "locked": True}, {"op": "set_graphic", "clip": "t", "text": "après"},
    )
    assert not report.ok and report.failed_index == 2 and "verrouillée" in report.error
    assert not session.can_undo, "tout ou rien : le calque n'a pas été ajouté non plus"


def test_track_inserts_are_for_audio_and_video_tracks_only():
    project = _project()
    project.tracks.append(Track("G1", "G1", "graphics"))
    _session, report = _apply(project, {"op": "add_track_insert", "track": "G1", "effect": "limiter"})
    assert not report.ok and "seules les pistes audio et vidéo" in report.error
    _session, report = _apply(project, {"op": "add_track_insert", "track": "V1", "effect": "limiter"})
    assert report.ok


# --- Le catalogue ----------------------------------------------------------------------------------------------------------


def test_only_file_reading_verbs_need_the_file_context():
    assert [name for name, verb in VERBS.items() if verb.needs_context] == ["import_media", "add_image_layer", "import_lut"]
