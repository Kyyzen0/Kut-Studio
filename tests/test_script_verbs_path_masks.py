"""Scripts et masques en courbe de Bézier (ADR-0005 étape 4, ADR-0006 étape 3b) : `add_color_window` en `path`, `set_color_window_points`.

Les valeurs écrites à la main sont refusées, jamais ramenées par ``Mask`` (voir ``core.scripting.path_nodes``) ; le résultat est celui de
l'éditeur du moniteur (``replace(mask, points=…, handles=…)``) et une transaction s'annule en un pas.
"""

from __future__ import annotations

import json
import math
from dataclasses import replace

import pytest

from core import mask_path
from core.color_nodes import window_by_id
from core.color_windows import window_matte
from core.compositing import Mask, MaskShape
from core.mask_path import (
    DEFAULT_NODES,
    DEFAULT_TANGENTS,
    HANDLE_LIMIT,
    MAX_NODES,
    MIN_NODES,
    POINT_LIMIT,
)
from core.project_io import CURRENT_VERSION, PATH_MASK_VERSION, save_project
from core.project_model import MediaAsset, Project, Track
from core.scripting import ScriptInvalid, Session, apply_script, parse_script
from core.scripting.path_nodes import nodes_to_arrays
from core.scripting.verbs import VERBS
from core.timeline_operations import find_clip
from ui import i18n

K = mask_path.KAPPA
SQUARE = [
    {"x": -0.5, "y": -0.5},
    {"x": 0.5, "y": -0.5},
    {"x": 0.5, "y": 0.5},
    {"x": -0.5, "y": 0.5},
]
ROUND = [
    {"x": 0.0, "y": -0.5, "in_dx": -K / 2, "out_dx": K / 2},
    {"x": 0.5, "y": 0.0, "in_dy": -K / 2, "out_dy": K / 2},
    {"x": 0.0, "y": 0.5, "in_dx": K / 2, "out_dx": -K / 2},
    {"x": -0.5, "y": 0.0, "in_dy": K / 2, "out_dy": -K / 2},
]


@pytest.fixture(autouse=True)
def _french(monkeypatch):
    monkeypatch.setenv("KUT_STUDIO_LANG", "fr")
    i18n.reset_for_tests()
    yield
    i18n.reset_for_tests()


def _project() -> Project:
    return Project(
        "Démo",
        width=320,
        height=180,
        fps=30.0,
        media_assets=[
            MediaAsset(
                "m",
                "/x/intro.mp4",
                "intro",
                10.0,
                320,
                180,
                30.0,
                "video",
                has_audio=True,
            )
        ],
        tracks=[
            Track("V1", "V1", "video"),
            Track("A1", "A1", "audio"),
            Track("G1", "G1", "graphics"),
        ],
    )


def _script(*ops: dict) -> dict:
    return {"kut_script": 1, "ops": list(ops)}


def _apply(project: Project, *ops: dict):
    session = Session(project)
    return session, apply_script(session, parse_script(_script(*ops)))


_CLIP = {"op": "add_clip", "track": "V1", "asset": "m", "at": 0, "id": "a"}


def _add(nodes=None, **extra) -> dict:
    op = {"op": "add_color_window", "clip": "a", "shape": "path", "id": "w1", **extra}
    if nodes is not None:
        op["nodes"] = nodes
    return op


def _set(nodes, **extra) -> dict:
    return {
        "op": "set_color_window_points",
        "clip": "a",
        "window": "w1",
        "nodes": nodes,
        **extra,
    }


def _window(project: Project, window_id: str = "w1") -> Mask:
    window = window_by_id(find_clip(project, "a").color_grade, window_id)
    assert window is not None
    return window


def _invalid(*ops: dict) -> ScriptInvalid:
    with pytest.raises(ScriptInvalid) as caught:
        parse_script(_script(*ops))
    return caught.value


# --- Création et modification -----------------------------------------------------------------------------------------


def test_a_script_creates_then_edits_a_curve_and_the_result_is_the_editors():
    session, report = _apply(
        _project(),
        _CLIP,
        _add(ROUND, width=0.6, height=0.4, feather=0.05),
        _set(SQUARE),
    )
    assert report.ok, report.error
    window = _window(session.project)
    assert window.shape is MaskShape.PATH and window.width == pytest.approx(0.6)
    assert window.points == ((-0.5, -0.5), (0.5, -0.5), (0.5, 0.5), (-0.5, 0.5))
    assert window.handles == ((0.0, 0.0, 0.0, 0.0),) * 4
    # La sortie du verbe est exactement ce qu'écrit l'éditeur du moniteur : replace(mask, points=…, handles=…).
    created = _apply(
        _project(), _CLIP, _add(ROUND, width=0.6, height=0.4, feather=0.05)
    )[0].project
    by_editor = replace(
        _window(created), points=window.points, handles=window.handles, id="w1"
    )
    assert by_editor == window and by_editor.id == window.id


def test_one_undo_step_per_script_and_undo_restores_the_project():
    session = Session(_project())
    assert apply_script(session, parse_script(_script(_CLIP))).ok
    before = find_clip(session.project, "a").color_grade
    assert apply_script(session, parse_script(_script(_add(ROUND), _set(SQUARE)))).ok
    assert _window(session.project).handles[0] == (0.0, 0.0, 0.0, 0.0)
    session.undo()
    assert window_by_id(find_clip(session.project, "a").color_grade, "w1") is None
    assert find_clip(session.project, "a").color_grade == before


def test_without_nodes_the_model_default_circle_is_written():
    session, report = _apply(_project(), _CLIP, _add())
    assert report.ok, report.error
    window = _window(session.project)
    assert (
        window.shape is MaskShape.PATH
        and window.points == DEFAULT_NODES
        and window.handles == DEFAULT_TANGENTS
    )


def test_set_color_window_keeps_a_curve_and_the_node_list_can_be_changed_after_it():
    session, report = _apply(
        _project(),
        _CLIP,
        _add(ROUND),
        {"op": "set_color_window", "clip": "a", "window": "w1", "opacity": 0.5},
        _set(SQUARE),
    )
    assert report.ok, report.error
    assert (
        _window(session.project).opacity == pytest.approx(0.5)
        and len(_window(session.project).points) == 4
    )


def test_the_matte_equals_the_one_of_a_curve_made_by_the_editor_path():
    pytest.importorskip("PySide6")
    session, report = _apply(
        _project(), _CLIP, _add(ROUND, width=0.6, height=0.8, feather=0.03)
    )
    assert report.ok, report.error
    scripted = _window(session.project)
    by_editor = replace(
        Mask(shape=MaskShape.PATH, width=0.6, height=0.8, feather=0.03, id="w1"),
        points=scripted.points,
        handles=scripted.handles,
    )
    _key, matte_script = window_matte(session.project, "a", [scripted], 0.0, 160, 90)
    _key, matte_editor = window_matte(session.project, "a", [by_editor], 0.0, 160, 90)
    assert bytes(matte_script.constBits()) == bytes(matte_editor.constBits())
    assert (
        matte_script.pixelColor(80, 45).alpha() == 255
        and matte_script.pixelColor(2, 2).alpha() == 0
    )  # le matte est réellement tracé


# --- Refus --------------------------------------------------------------------------------------------------------------


def _node(**values) -> dict:
    return {"x": 0.0, "y": 0.0, **values}


def test_the_decoder_refuses_counts_types_and_bounds_without_clamping():
    triangle = SQUARE[:3]
    assert _invalid(_add(triangle[:2]))  # moins que le minimum
    assert _invalid(
        _add([{"x": i / 100.0, "y": 0.0} for i in range(MAX_NODES + 1)])
    )  # plus que le plafond
    assert _invalid(_add([{"x": 0.0}, *triangle[:2]]))  # y manquant
    assert _invalid(_add([{"x": POINT_LIMIT + 0.01, "y": 0.0}, *triangle[:2]]))
    assert _invalid(_add([_node(in_dx=HANDLE_LIMIT + 0.01), *triangle[:2]]))
    assert _invalid(_add([_node(out_dy=-HANDLE_LIMIT - 0.01), *triangle[:2]]))
    assert _invalid(
        _add([{"x": True, "y": 0.0}, *triangle[:2]])
    )  # un booléen n'est pas un nombre
    assert _invalid(_add([{"x": "0.5", "y": 0.0}, *triangle[:2]]))
    assert _invalid(_add([_node(bogus=1), *triangle[:2]]))  # champ inconnu
    assert _invalid(_add(["0,0", *triangle[:2]]))  # nœud pas un objet
    assert _invalid(_add({"x": 0}))  # pas une liste
    for text in (
        "NaN",
        "Infinity",
        "-Infinity",
    ):  # JSON non standard accepté par Python
        value = float(text.lower().replace("infinity", "inf"))
        assert _invalid(_add([{"x": value, "y": 0.0}, *triangle[:2]]))
    # Les bornes exactes passent, sans être modifiées.
    edge = [
        {
            "x": POINT_LIMIT,
            "y": -POINT_LIMIT,
            "in_dx": HANDLE_LIMIT,
            "out_dy": -HANDLE_LIMIT,
        },
        *triangle[:2],
    ]
    session, report = _apply(_project(), _CLIP, _add(edge))
    assert report.ok, report.error
    assert _window(session.project).points[0] == (POINT_LIMIT, -POINT_LIMIT)
    assert _window(session.project).handles[0] == (
        HANDLE_LIMIT,
        0.0,
        0.0,
        -HANDLE_LIMIT,
    )


def test_nodes_to_arrays_is_strict_for_python_callers_too():
    for bad in (
        None,
        3,
        {},
        "abc",
        [],
        SQUARE[:2],
        [{"x": math.nan, "y": 0}] * 3,
        [{"x": 3.0, "y": 0}] * 3,
        [{"x": True, "y": 0}] * 3,
        [{"x": 0, "y": 0, "z": 1}] * 3,
        [[0, 0]] * 3,
        [{"x": 0, "y": 0}] * (MAX_NODES + 1),
    ):
        with pytest.raises(ValueError) as caught:
            nodes_to_arrays(bad)
        assert len(str(caught.value)) < 200  # le message ne recopie pas l'entrée
    assert len(nodes_to_arrays(SQUARE)[0]) == MIN_NODES + 1


def test_nodes_only_belong_to_a_path_and_set_points_only_edits_a_path():
    _session, report = _apply(_project(), _CLIP, _add(SQUARE, shape="ellipse"))
    assert not report.ok and "path" in report.error
    _session, report = _apply(_project(), _CLIP, _add(shape="ellipse"), _set(SQUARE))
    assert not report.ok and "courbe de Bézier" in report.error
    _session, report = _apply(_project(), _CLIP, _add(shape="polygon"), _set(SQUARE))
    assert not report.ok and report.failed_name.endswith("set_color_window_points")
    _session, report = _apply(_project(), _CLIP, _set(SQUARE))  # fenêtre inconnue
    assert not report.ok


def test_a_failed_edit_leaves_the_project_untouched():
    session = Session(_project())
    assert apply_script(session, parse_script(_script(_CLIP, _add(ROUND)))).ok
    before = _window(session.project)
    report = apply_script(
        session, parse_script(_script(_set(SQUARE), _add(shape="ellipse", id="w1")))
    )  # identifiant déjà pris
    assert not report.ok and _window(session.project) == before


def test_locked_tracks_locked_layers_and_wrong_track_kinds_are_refused_by_both_verbs():
    _session, report = _apply(
        _project(),
        _CLIP,
        _add(ROUND),
        {"op": "set_track_locked", "track": "V1", "locked": True},
        _set(SQUARE),
    )
    assert not report.ok and "verrouillée" in report.error
    _session, report = _apply(
        _project(),
        _CLIP,
        {"op": "set_track_locked", "track": "V1", "locked": True},
        _add(ROUND),
    )
    assert not report.ok and "verrouillée" in report.error
    _session, report = _apply(
        _project(),
        {"op": "add_clip", "track": "A1", "asset": "m", "at": 0, "id": "a"},
        _add(ROUND),
    )
    assert not report.ok
    # Un calque graphique verrouillé (graphic.locked) : même refus.
    session = Session(_project())
    assert apply_script(
        session,
        parse_script(
            _script(
                {
                    "op": "add_graphic",
                    "graphic": "solid",
                    "at": 0,
                    "duration": 2,
                    "id": "a",
                }
            )
        ),
    ).ok
    assert apply_script(session, parse_script(_script(_add(ROUND)))).ok
    clip = find_clip(session.project, "a")
    clip.graphic = replace(clip.graphic, locked=True)
    for op in (_add(ROUND, id="w2"), _set(SQUARE)):
        report = apply_script(session, parse_script(_script(op)))
        assert not report.ok and "verrouillé" in report.error


# --- Format -------------------------------------------------------------------------------------------------------------


def _saved_version(project: Project, tmp_path) -> int:
    path = tmp_path / "p.kut"
    save_project(project, str(path))
    return json.loads(path.read_text(encoding="utf-8"))["version"]


def test_a_project_with_a_scripted_curve_is_saved_in_version_18_and_without_in_17(
    tmp_path,
):
    session, report = _apply(_project(), _CLIP, _add(shape="ellipse", id="e"))
    assert report.ok, report.error
    assert _saved_version(session.project, tmp_path) == CURRENT_VERSION == 17
    session, report = _apply(_project(), _CLIP, _add(ROUND))
    assert report.ok, report.error
    assert _saved_version(session.project, tmp_path) == PATH_MASK_VERSION == 18
    # Après annulation, plus aucune courbe : le fichier redevient une 17.
    session.undo()
    assert _saved_version(session.project, tmp_path) == 17


# --- Table, schéma, CLI -------------------------------------------------------------------------------------------------


def test_the_verb_is_in_the_table_and_the_generated_schema_bounds_the_node_list():
    from core.scripting.schema import json_schema

    assert "set_color_window_points" in VERBS
    schema = json.dumps(json_schema())
    assert '"maxItems": 128' in schema and '"minItems": 3' in schema


def test_the_cli_validates_and_applies_the_verb(tmp_path, capsys):
    from core import cli

    save_project(_project(), str(tmp_path / "p.kut"))
    script = tmp_path / "s.json"
    script.write_text(
        json.dumps(_script(_CLIP, _add(ROUND), _set(SQUARE))), encoding="utf-8"
    )
    assert cli.run(["validate", str(script)]) == 0
    out = tmp_path / "out.kut"
    assert cli.run(["apply", str(script), str(tmp_path / "p.kut"), "-o", str(out)]) == 0
    capsys.readouterr()
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["version"] == PATH_MASK_VERSION and '"shape": "path"' in json.dumps(
        payload
    )
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(_script(_CLIP, _add(ROUND[:2]))), encoding="utf-8")
    assert cli.run(["validate", str(bad)]) != 0
