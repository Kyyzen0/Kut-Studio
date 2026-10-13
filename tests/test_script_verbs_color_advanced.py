"""Format déclaratif, lot 4 (ADR-0005 étape 4) : étalonnage avancé — courbes, roues, LUT, nœuds, détail.

Mêmes règles que le lot 3 : bornes du modèle (rien n'est ramené en silence), verrou de piste, genres vidéo / graphiques.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.color_grading import ColorGradingService, WHEEL_MAX, WHEEL_MIN
from core.color_nodes import MAX_BLUR, MAX_SHARPEN, MixerKind, as_graph
from core.project_model import MediaAsset, Project, Track
from core.scripting import ScriptContext, ScriptInvalid, Session, apply_script, parse_script
from core.scripting.verbs import VERBS
from core.timeline_operations import find_clip
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
        media_assets=[MediaAsset("m", "/x/intro.mp4", "intro", 10.0, 1280, 720, 30.0, "video", has_audio=True)],
        tracks=[Track("V1", "V1", "video"), Track("A1", "A1", "audio")],
    )


def _script(*ops: dict) -> dict:
    return {"kut_script": 1, "ops": list(ops)}


def _apply(project: Project, *ops: dict, context: ScriptContext | None = None):
    session = Session(project)
    return session, apply_script(session, parse_script(_script(*ops)), context=context)


def _clip() -> dict:
    return {"op": "add_clip", "track": "V1", "asset": "m", "at": 0, "id": "a"}


def _cube(path) -> None:
    path.write_text(
        "TITLE \"Look\"\nLUT_3D_SIZE 2\n"
        "0 0 0\n1 0 0\n0 1 0\n1 1 0\n0 0 1\n1 0 1\n0 1 1\n1 1 1\n",
        encoding="utf-8",
    )


# --- Courbes -----------------------------------------------------------------------------------------------------------


def test_a_curve_point_is_set_then_reset():
    session, report = _apply(
        _project(), _clip(),
        {"op": "set_color_curve", "clip": "a", "channel": "master", "index": 8, "output": 0.7},
    )
    assert report.ok, report.error
    grade = ColorGradingService().get_grade(session.project, "a")
    assert grade.curves.master.points[8][1] == pytest.approx(0.7)
    _session, report = _apply(session.project, {"op": "reset_color_curve", "clip": "a", "channel": "master"})
    assert report.ok and ColorGradingService().get_grade(session.project, "a").curves.master.is_identity()


def test_curve_bounds_reject_silent_clamp_candidates():
    for op in (
        {"op": "set_color_curve", "clip": "a", "channel": "master", "index": -1, "output": 0.5},
        {"op": "set_color_curve", "clip": "a", "channel": "master", "index": 16, "output": 0.5},
        {"op": "set_color_curve", "clip": "a", "channel": "master", "index": 0, "output": -0.01},
        {"op": "set_color_curve", "clip": "a", "channel": "master", "index": 0, "output": 1.01},
        {"op": "set_color_curve", "clip": "a", "channel": "alpha", "index": 0, "output": 0.5},
    ):
        with pytest.raises(ScriptInvalid):
            parse_script(_script(op))


# --- Roues -------------------------------------------------------------------------------------------------------------


def test_a_wheel_component_is_set_without_clamping():
    session, report = _apply(
        _project(), _clip(),
        {"op": "set_color_wheel", "clip": "a", "wheel": "lift", "y": 0.25, "r": -0.1},
    )
    assert report.ok, report.error
    lift = ColorGradingService().get_grade(session.project, "a").lift
    assert (lift.y, lift.r, lift.g) == (0.25, -0.1, 0.0)
    for outside in (WHEEL_MIN - 0.01, WHEEL_MAX + 0.01):
        with pytest.raises(ScriptInvalid):
            parse_script(_script({"op": "set_color_wheel", "clip": "a", "wheel": "gain", "y": outside}))
    _session, report = _apply(_project(), _clip(), {"op": "set_color_wheel", "clip": "a", "wheel": "gain"})
    assert not report.ok and "au moins une composante" in report.error


# --- LUT ---------------------------------------------------------------------------------------------------------------


def test_import_lut_then_remove(tmp_path):
    cube = tmp_path / "look.cube"
    _cube(cube)
    context = ScriptContext(base_dir=tmp_path)
    session, report = _apply(_project(), _clip(), {"op": "import_lut", "clip": "a", "path": "look.cube"}, context=context)
    assert report.ok, report.error
    grade = ColorGradingService().get_grade(session.project, "a")
    assert grade.lut is not None and grade.lut.title == "Look"
    _session, report = _apply(session.project, {"op": "remove_lut", "clip": "a"})
    assert report.ok and ColorGradingService().get_grade(session.project, "a").lut is None


def test_import_lut_needs_context_and_stays_in_script_folder(tmp_path):
    _session, report = _apply(_project(), _clip(), {"op": "import_lut", "clip": "a", "path": "look.cube"})
    assert not report.ok and "dossier du script" in report.error
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    _cube(outside / "look.cube")
    script_dir = tmp_path / "script"
    script_dir.mkdir()
    context = ScriptContext(base_dir=script_dir)
    _session, report = _apply(
        _project(), _clip(), {"op": "import_lut", "clip": "a", "path": "../elsewhere/look.cube"}, context=context,
    )
    assert not report.ok and "--allow-outside" in report.error


# --- Nœuds -------------------------------------------------------------------------------------------------------------


def test_nodes_serial_branch_rename_and_target_grade():
    session, report = _apply(
        _project(), _clip(),
        {"op": "add_color_node", "clip": "a", "after": "n1", "as": "ombres", "label": "Ombres"},
        {"op": "set_color_grade", "clip": "a", "node": {"ref": "ombres"}, "exposure": -0.5},
        {"op": "set_color_grade", "clip": "a", "node": "n1", "saturation": 1.4},
        {"op": "branch_color_node", "clip": "a", "node": {"ref": "ombres"}, "kind": "parallel", "as": "haut", "label": "Haut"},
        {"op": "rename_color_node", "clip": "a", "node": {"ref": "haut"}, "label": "Hautes"},
    )
    assert report.ok, report.error
    graph = as_graph(find_clip(session.project, "a").color_grade)
    assert graph.corrector("ombres").grade.exposure == -0.5
    assert graph.corrector("n1").grade.saturation == 1.4
    assert graph.corrector("haut").label == "Hautes"
    assert any(mixer.kind is MixerKind.PARALLEL for mixer in graph.mixers)


def test_remove_color_node_refuses_the_last_one():
    session, report = _apply(_project(), _clip(), {"op": "remove_color_node", "clip": "a", "node": "n1"})
    assert not report.ok and "dernier" in report.error.lower()
    session, report = _apply(
        _project(), _clip(),
        {"op": "add_color_node", "clip": "a", "id": "n2"},
        {"op": "remove_color_node", "clip": "a", "node": "n2"},
    )
    assert report.ok, report.error
    assert len(as_graph(find_clip(session.project, "a").color_grade).correctors) == 1


def test_detail_bounds_match_the_model():
    session, report = _apply(
        _project(), _clip(),
        {"op": "set_color_detail", "clip": "a", "blur": MAX_BLUR, "sharpen": MAX_SHARPEN},
    )
    assert report.ok, report.error
    node = as_graph(find_clip(session.project, "a").color_grade).corrector("n1")
    assert (node.blur, node.sharpen) == (MAX_BLUR, MAX_SHARPEN)
    for op in (
        {"op": "set_color_detail", "clip": "a", "blur": MAX_BLUR + 0.01},
        {"op": "set_color_detail", "clip": "a", "sharpen": -0.01},
    ):
        with pytest.raises(ScriptInvalid):
            parse_script(_script(op))
    _session, report = _apply(_project(), _clip(), {"op": "set_color_detail", "clip": "a"})
    assert not report.ok and "blur et/ou sharpen" in report.error


def test_color_advanced_respects_track_lock_and_track_type():
    _session, report = _apply(
        _project(), _clip(),
        {"op": "set_track_locked", "track": "V1", "locked": True},
        {"op": "set_color_curve", "clip": "a", "channel": "red", "index": 1, "output": 0.2},
    )
    assert not report.ok and "verrouillée" in report.error
    project = _project()
    project.media_assets.append(MediaAsset("snd", "/x/voix.wav", "voix", 5.0, 0, 0, 0.0, "audio", has_audio=True))
    _session, report = _apply(
        project, {"op": "add_clip", "track": "A1", "asset": "snd", "at": 0, "id": "son"},
        {"op": "add_color_node", "clip": "son"},
    )
    assert not report.ok and "piste 'audio'" in report.error


def test_the_new_verbs_are_in_the_table():
    assert {
        "set_color_curve", "reset_color_curve", "set_color_wheel", "import_lut", "remove_lut",
        "add_color_node", "branch_color_node", "remove_color_node", "rename_color_node", "set_color_detail",
    } <= set(VERBS)


def test_color_node_ids_are_scoped_per_clip():
    """Revue Codex : un même ``n2`` sur deux clips n'est pas un doublon (les nœuds sont locaux au clip)."""
    session, report = _apply(
        _project(),
        {"op": "add_clip", "track": "V1", "asset": "m", "at": 0, "id": "a"},
        {"op": "add_clip", "track": "V1", "asset": "m", "at": 5, "id": "b"},
        {"op": "add_color_node", "clip": "a", "id": "n2"},
        {"op": "add_color_node", "clip": "b", "id": "n2"},
        {"op": "set_color_grade", "clip": "a", "node": "n2", "exposure": 0.5},
        {"op": "set_color_grade", "clip": "b", "node": "n2", "exposure": -0.5},
    )
    assert report.ok, report.error
    assert ColorGradingService().get_grade(session.project, "a", "n2").exposure == 0.5
    assert ColorGradingService().get_grade(session.project, "b", "n2").exposure == -0.5
    with pytest.raises(ScriptInvalid):
        parse_script(_script(
            {"op": "add_clip", "track": "V1", "asset": "m", "at": 0, "id": "a"},
            {"op": "add_color_node", "clip": "a", "id": "n2"},
            {"op": "add_color_node", "clip": "a", "id": "n2"},
        ))


def test_import_lut_normalizes_bom_for_ffmpeg(tmp_path):
    """Revue Codex : une LUT avec BOM est acceptée, et la ressource pointe vers la copie UTF-8 lisible par FFmpeg."""
    cube = tmp_path / "bom.cube"
    body = (
        'TITLE "Look"\nLUT_3D_SIZE 2\n'
        "0 0 0\n1 0 0\n0 1 0\n1 1 0\n0 0 1\n1 0 1\n0 1 1\n1 1 1\n"
    )
    cube.write_bytes(b"\xef\xbb\xbf" + body.encode("utf-8"))
    context = ScriptContext(base_dir=tmp_path)
    session, report = _apply(_project(), _clip(), {"op": "import_lut", "clip": "a", "path": "bom.cube"}, context=context)
    assert report.ok, report.error
    lut = ColorGradingService().get_grade(session.project, "a").lut
    assert lut is not None and lut.source_path is not None
    stored = Path(lut.source_path).read_bytes()
    assert not stored.startswith(b"\xef\xbb\xbf")
    assert b"LUT_3D_SIZE" in stored


def test_branch_with_mixer_shaped_id_does_not_collide():
    """Revue Codex : ``new_id="m1"`` ne doit pas faire allouer le mélangeur sous le même identifiant."""
    graph, branch = as_graph(None).with_branch("n1", MixerKind.PARALLEL, new_id="m1")
    assert branch == "m1"
    assert graph.corrector("m1").id == "m1"
    assert all(mixer.id != "m1" for mixer in graph.mixers)
    assert len({node.id for node in graph.nodes}) == len(graph.nodes)
