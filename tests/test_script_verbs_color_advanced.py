"""Format déclaratif, lot 4 (ADR-0005 étape 4) : étalonnage avancé du premier nœud (roues, courbes, LUT).

Mêmes règles que les lots précédents : bornes lues dans le modèle (jamais de valeur ramenée en silence), verrou de piste respecté,
genres de piste vérifiés (vidéo et graphiques), un script atomique (une étape d'historique). Une LUT est un fichier lu en lecture seule
dans le dossier du script, comme ``import_media``.
"""

from __future__ import annotations

import pytest

from core.color_grading import (
    CHANNELS, MAX_CURVE_POINTS, WHEEL_MAX, WHEEL_MIN, WHEELS, ColorCurve, ColorCurves, ColorGradingService, Wheel,
)
from core.project_model import MediaAsset, Project, Track
from core.scripting import ScriptContext, ScriptInvalid, Session, apply_script, parse_script
from core.scripting.verbs import VERBS
from ui import i18n


@pytest.fixture(autouse=True)
def _french(monkeypatch):
    monkeypatch.setenv("KUT_STUDIO_LANG", "fr")
    i18n.reset_for_tests()
    yield
    i18n.reset_for_tests()


def _project() -> Project:
    return Project(
        "Démo", width=1280, height=720, fps=30.0,
        media_assets=[
            MediaAsset("m", "/x/intro.mp4", "intro", 10.0, 1280, 720, 30.0, "video", has_audio=True),
            MediaAsset("snd", "/x/voix.wav", "voix", 5.0, 0, 0, 0.0, "audio", has_audio=True),
        ],
        tracks=[Track("V1", "V1", "video"), Track("A1", "A1", "audio")],
    )


def _script(*ops: dict) -> dict:
    return {"kut_script": 1, "ops": list(ops)}


def _apply(project: Project, *ops: dict, context: ScriptContext | None = None):
    session = Session(project)
    return session, apply_script(session, parse_script(_script(*ops)), context=context)


_CLIP = {"op": "add_clip", "track": "V1", "asset": "m", "at": 0.0, "id": "a"}


def _grade(session: Session):
    return ColorGradingService().get_grade(session.project, "a")


def _cube(path, size: int = 2, title: str = "Démo") -> None:
    rows = [f"{r / (size - 1)} {g / (size - 1)} {b / (size - 1)}" for b in range(size) for g in range(size) for r in range(size)]
    path.write_text(f'TITLE "{title}"\nLUT_3D_SIZE {size}\n' + "\n".join(rows) + "\n", encoding="utf-8")


# --- Roues ---------------------------------------------------------------------------------------------------------------


def test_a_wheel_is_set_and_matches_what_the_interface_calls():
    session, report = _apply(_project(), _CLIP, {"op": "set_color_wheel", "clip": "a", "wheel": "lift", "r": 0.2, "y": -0.1})
    assert report.ok, report.error
    assert _grade(session).lift == Wheel(r=0.2, g=0.0, b=0.0, y=-0.1)
    # parité : le service appelé par l'interface (get_grade -> with_wheel -> set_grade) donne le même étalonnage
    expected_session, _ = _apply(_project(), _CLIP)
    service = ColorGradingService()
    service.set_grade(expected_session.project, "a", service.get_grade(expected_session.project, "a").with_wheel("lift", Wheel(0.2, 0.0, 0.0, -0.1)))
    assert _grade(session) == _grade(expected_session)


def test_a_second_call_keeps_the_components_it_does_not_name():
    session, report = _apply(
        _project(), _CLIP,
        {"op": "set_color_wheel", "clip": "a", "wheel": "gain", "r": 0.5, "g": 0.25},
        {"op": "set_color_wheel", "clip": "a", "wheel": "gain", "b": -0.5},
        {"op": "set_color_wheel", "clip": "a", "wheel": "gamma", "y": 0.3},
    )
    assert report.ok, report.error
    grade = _grade(session)
    assert grade.gain == Wheel(0.5, 0.25, -0.5, 0.0) and grade.gamma == Wheel(0.0, 0.0, 0.0, 0.3) and grade.lift.is_neutral()


def test_every_wheel_bound_is_the_models_so_nothing_is_clamped_in_silence():
    assert (WHEEL_MIN, WHEEL_MAX) == (-1.0, 1.0)
    for wheel in WHEELS:
        for component in "rgby":
            param = next(item for item in VERBS["set_color_wheel"].params if item.name == component)
            assert (param.minimum, param.maximum) == (WHEEL_MIN, WHEEL_MAX)
            for value in (WHEEL_MIN, WHEEL_MAX):
                session, report = _apply(_project(), _CLIP, {"op": "set_color_wheel", "clip": "a", "wheel": wheel, component: value})
                assert report.ok, (wheel, component, value, report.error)
                assert getattr(getattr(_grade(session), wheel), component) == value
            for outside in (WHEEL_MIN - 0.01, WHEEL_MAX + 0.01):
                with pytest.raises(ScriptInvalid):
                    parse_script(_script({"op": "set_color_wheel", "clip": "a", "wheel": wheel, component: outside}))


def test_a_wheel_needs_a_component_and_a_known_wheel():
    _session, report = _apply(_project(), _CLIP, {"op": "set_color_wheel", "clip": "a", "wheel": "lift"})
    assert not report.ok and "au moins une composante" in report.error
    for bad in ({"wheel": "shadows", "r": 0.1}, {"r": 0.1}, {"wheel": "lift", "r": True}, {"wheel": "lift", "r": "0.1"}, {"wheel": "lift", "z": 0.1}):
        with pytest.raises(ScriptInvalid):
            parse_script(_script({"op": "set_color_wheel", "clip": "a", **bad}))


# --- Courbes -------------------------------------------------------------------------------------------------------------


def test_a_curve_point_is_set_and_matches_the_service_the_interface_calls():
    session, report = _apply(_project(), _CLIP, {"op": "set_color_curve_point", "clip": "a", "channel": "master", "index": 4, "output": 0.5})
    assert report.ok, report.error
    curve = _grade(session).curves.master
    assert curve.points[4] == (curve.points[4][0], 0.5) and not _grade(session).curves.is_identity()
    expected_session, _ = _apply(_project(), _CLIP)
    service = ColorGradingService()
    grade = service.get_grade(expected_session.project, "a")
    service.set_grade(expected_session.project, "a", grade.with_curves(grade.curves.with_point("master", 4, 0.5)))
    assert _grade(session) == _grade(expected_session)


def test_curve_bounds_are_the_models():
    index = next(item for item in VERBS["set_color_curve_point"].params if item.name == "index")
    assert (index.minimum, index.maximum) == (0, MAX_CURVE_POINTS - 1)
    for channel in CHANNELS:
        for value in (0.0, 1.0):
            session, report = _apply(_project(), _CLIP, {"op": "set_color_curve_point", "clip": "a", "channel": channel, "index": 15, "output": value})
            assert report.ok, report.error
            assert getattr(_grade(session).curves, channel).points[15][1] == value
    for bad in ({"output": -0.01}, {"output": 1.01}, {"index": -1}, {"index": MAX_CURVE_POINTS}, {"index": 1.5}, {"index": True}, {"channel": "alpha"}):
        op = {"op": "set_color_curve_point", "clip": "a", "channel": "red", "index": 3, "output": 0.5, **bad}
        with pytest.raises(ScriptInvalid):
            parse_script(_script(op))


def test_curve_point_beyond_a_short_curve_is_refused_by_the_model():
    project = _project()
    session, report = _apply(project, _CLIP)
    assert report.ok
    service = ColorGradingService()
    grade = service.get_grade(project, "a")
    service.set_grade(project, "a", grade.with_curves(ColorCurves(master=ColorCurve(points=((0.0, 0.0), (1.0, 1.0))))))
    before = _grade(session)
    _session, report = _apply(project, {"op": "set_color_curve_point", "clip": "a", "channel": "master", "index": 5, "output": 0.5})
    assert not report.ok and "Index de point invalide" in report.error and _grade(session) == before


def test_reset_color_curves_one_channel_or_all():
    session, report = _apply(
        _project(), _CLIP,
        {"op": "set_color_curve_point", "clip": "a", "channel": "red", "index": 3, "output": 0.9},
        {"op": "set_color_curve_point", "clip": "a", "channel": "blue", "index": 3, "output": 0.1},
        {"op": "reset_color_curves", "clip": "a", "channel": "red"},
    )
    assert report.ok, report.error
    curves = _grade(session).curves
    assert curves.red.is_identity() and not curves.blue.is_identity()
    _session, report = _apply(session.project, {"op": "reset_color_curves", "clip": "a"})
    assert report.ok and _grade(session).curves.is_identity()


# --- LUT -----------------------------------------------------------------------------------------------------------------


def test_a_lut_is_read_from_the_script_folder_and_cleared(tmp_path):
    _cube(tmp_path / "look.cube", size=3, title="Mon look")
    context = ScriptContext(tmp_path)
    session, report = _apply(_project(), _CLIP, {"op": "set_color_lut", "clip": "a", "path": "look.cube"}, context=context)
    assert report.ok, report.error
    lut = _grade(session).lut
    assert lut is not None and lut.title == "Mon look" and lut.path == str((tmp_path / "look.cube").resolve()) and not lut.missing
    _session, report = _apply(session.project, {"op": "clear_color_lut", "clip": "a"})
    assert report.ok and _grade(session).lut is None


def test_a_lut_keeps_the_rest_of_the_grade(tmp_path):
    _cube(tmp_path / "look.cube")
    session, report = _apply(
        _project(), _CLIP, {"op": "set_color_grade", "clip": "a", "exposure": 0.5},
        {"op": "set_color_lut", "clip": "a", "path": "look.cube"}, context=ScriptContext(tmp_path),
    )
    assert report.ok, report.error
    assert _grade(session).exposure == 0.5 and _grade(session).lut is not None


def test_a_lut_cannot_leave_the_script_folder_without_the_callers_consent(tmp_path):
    folder = tmp_path / "montage"
    folder.mkdir()
    _cube(tmp_path / "secret.cube")
    for path in ("../secret.cube", str(tmp_path / "secret.cube")):
        session, report = _apply(_project(), _CLIP, {"op": "set_color_lut", "clip": "a", "path": path}, context=ScriptContext(folder))
        assert not report.ok and "hors du dossier du script" in report.error, path
        assert report.failed_index == 1
    _session, report = _apply(
        _project(), _CLIP, {"op": "set_color_lut", "clip": "a", "path": "../secret.cube"}, context=ScriptContext(folder, allow_outside=True),
    )
    assert report.ok


def test_a_lut_without_a_file_context_or_a_valid_file_is_refused_and_nothing_changes(tmp_path):
    _session, report = _apply(_project(), _CLIP, {"op": "set_color_lut", "clip": "a", "path": "x.cube"})
    assert not report.ok and "dossier du script" in report.error
    context = ScriptContext(tmp_path)
    (tmp_path / "vide.cube").write_text("", encoding="utf-8")
    (tmp_path / "1d.cube").write_text("LUT_1D_SIZE 2\n0 0 0\n1 1 1\n", encoding="utf-8")
    (tmp_path / "court.cube").write_text("LUT_3D_SIZE 2\n0 0 0\n", encoding="utf-8")
    (tmp_path / "look.txt").write_text("x", encoding="utf-8")
    (tmp_path / "dossier.cube").mkdir()
    for name in ("vide.cube", "1d.cube", "court.cube", "look.txt", "dossier.cube", "absent.cube"):
        session, report = _apply(_project(), _CLIP, {"op": "set_color_grade", "clip": "a", "exposure": 1}, {"op": "set_color_lut", "clip": "a", "path": name}, context=context)
        assert not report.ok and report.failed_index == 2, name
        assert not session.project.tracks[0].clips, "atomique : le clip et le réglage précédents sont annulés aussi"


def test_a_lut_ffmpeg_could_not_read_as_is_is_refused_rather_than_rewritten(tmp_path):
    _cube(tmp_path / "look.cube")
    (tmp_path / "bom.cube").write_bytes(b"\xef\xbb\xbf" + (tmp_path / "look.cube").read_bytes())
    _session, report = _apply(_project(), _CLIP, {"op": "set_color_lut", "clip": "a", "path": "bom.cube"}, context=ScriptContext(tmp_path))
    assert not report.ok and "UTF-8 sans BOM" in report.error
    assert sorted(path.name for path in tmp_path.iterdir()) == ["bom.cube", "look.cube"], "rien n'est écrit"


def test_a_too_big_lut_is_not_read(tmp_path, monkeypatch):
    import core.scripting.verbs as verbs

    _cube(tmp_path / "look.cube")
    monkeypatch.setattr(verbs, "MAX_LUT_BYTES", 10)
    _session, report = _apply(_project(), _CLIP, {"op": "set_color_lut", "clip": "a", "path": "look.cube"}, context=ScriptContext(tmp_path))
    assert not report.ok and "dépasse" in report.error


@pytest.mark.parametrize("path", ["", "a\x00b", "a\nb", "x" * 1025, " look.cube", 3, None])
def test_the_lut_path_is_checked_before_anything_runs(path):
    with pytest.raises(ScriptInvalid):
        parse_script(_script({"op": "set_color_lut", "clip": "a", "path": path}))


# --- Verrou, genre de piste, atomicité ---------------------------------------------------------------------------------------


def _ops_for(tmp_path) -> list[dict]:
    _cube(tmp_path / "look.cube")
    return [
        {"op": "set_color_wheel", "clip": "{clip}", "wheel": "gain", "y": 0.1},
        {"op": "set_color_curve_point", "clip": "{clip}", "channel": "master", "index": 2, "output": 0.5},
        {"op": "reset_color_curves", "clip": "{clip}"},
        {"op": "set_color_lut", "clip": "{clip}", "path": "look.cube"},
        {"op": "clear_color_lut", "clip": "{clip}"},
    ]


def _for(op: dict, clip: str) -> dict:
    return {key: (clip if value == "{clip}" else value) for key, value in op.items()}


def test_a_locked_track_refuses_every_advanced_color_verb(tmp_path):
    for op in _ops_for(tmp_path):
        _session, report = _apply(
            _project(), _CLIP, {"op": "set_track_locked", "track": "V1", "locked": True}, _for(op, "a"), context=ScriptContext(tmp_path),
        )
        assert not report.ok and "verrouillée" in report.error, op["op"]


def test_an_audio_clip_refuses_every_advanced_color_verb(tmp_path):
    for op in _ops_for(tmp_path):
        _session, report = _apply(
            _project(), {"op": "add_clip", "track": "A1", "asset": "snd", "at": 0, "id": "son"}, _for(op, "son"), context=ScriptContext(tmp_path),
        )
        assert not report.ok and "piste 'audio'" in report.error, op["op"]


def test_a_graphics_clip_is_accepted(tmp_path):
    project = _project()
    project.tracks.append(Track("G1", "G1", "graphics"))
    session, report = _apply(
        project, {"op": "add_graphic", "graphic": "solid", "at": 0, "duration": 2, "id": "g"},
        {"op": "set_color_wheel", "clip": "g", "wheel": "offset", "b": 0.2},
    )
    assert report.ok, report.error
    assert ColorGradingService().get_grade(session.project, "g").offset.b == 0.2


def test_a_failing_script_leaves_the_grade_untouched_and_a_good_one_is_one_history_step(tmp_path):
    _cube(tmp_path / "look.cube")
    ops = [
        _CLIP, {"op": "set_color_wheel", "clip": "a", "wheel": "lift", "y": 0.2},
        {"op": "set_color_curve_point", "clip": "a", "channel": "green", "index": 7, "output": 0.2},
        {"op": "set_color_lut", "clip": "a", "path": "look.cube"},
    ]
    session = Session(_project())
    report = apply_script(session, parse_script(_script(*ops)), context=ScriptContext(tmp_path))
    assert report.ok, report.error
    grade = _grade(session)
    assert grade.lift.y == 0.2 and grade.curves.green.points[7][1] == 0.2 and grade.lut is not None
    assert session.undo() and not session.project.tracks[0].clips and not session.can_undo, "une seule étape d'historique"
    failing = Session(_project())
    report = apply_script(failing, parse_script(_script(*ops[:3], {"op": "set_color_wheel", "clip": "nope", "wheel": "lift", "y": 0.1})))
    assert not report.ok and not failing.project.tracks[0].clips


def test_the_new_verbs_are_in_the_table_and_only_the_lut_needs_files():
    assert {"set_color_wheel", "set_color_curve_point", "reset_color_curves", "set_color_lut", "clear_color_lut"} <= set(VERBS)
    assert [name for name, verb in VERBS.items() if verb.needs_context] == ["import_media", "set_color_lut"]
