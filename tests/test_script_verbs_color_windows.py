"""Format déclaratif, lot 5 (ADR-0005 étape 4) : fenêtres et qualifieur d'étalonnage.

Le modèle ramène les bornes d'un ``Mask`` en silence : les Param et ``_require_mask_number`` refusent l'hors-bornes.
Le qualifieur, lui, lève : on propage ``QualifierError`` en refus de script.
"""

from __future__ import annotations

import pytest

from core.color_grading import ColorGradingService
from core.color_nodes import as_graph, window_by_id
from core.color_qualifier import Qualifier
from core.compositing import MaskMode, MaskShape, _MASK_BOUNDS
from core.project_model import MediaAsset, Project, Track
from core.scripting import ScriptInvalid, Session, apply_script, parse_script
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
        "Démo",
        width=1280,
        height=720,
        fps=30.0,
        media_assets=[
            MediaAsset(
                "m",
                "/x/intro.mp4",
                "intro",
                10.0,
                1280,
                720,
                30.0,
                "video",
                has_audio=True,
            )
        ],
        tracks=[Track("V1", "V1", "video")],
    )


def _script(*ops: dict) -> dict:
    return {"kut_script": 1, "ops": list(ops)}


def _apply(project: Project, *ops: dict):
    session = Session(project)
    return session, apply_script(session, parse_script(_script(*ops)))


def _clip() -> dict:
    return {"op": "add_clip", "track": "V1", "asset": "m", "at": 0, "id": "a"}


# --- Fenêtres ----------------------------------------------------------------------------------------------------------


def test_add_set_remove_color_window():
    session, report = _apply(
        _project(),
        _clip(),
        {
            "op": "add_color_window",
            "clip": "a",
            "shape": "ellipse",
            "as": "ciel",
            "feather": 0.1,
            "width": 0.6,
        },
        {
            "op": "set_color_window",
            "clip": "a",
            "window": {"ref": "ciel"},
            "opacity": 0.5,
            "mode": "subtract",
        },
    )
    assert report.ok, report.error
    window = window_by_id(find_clip(session.project, "a").color_grade, "ciel")
    assert window is not None
    assert window.shape is MaskShape.ELLIPSE
    assert window.feather == pytest.approx(0.1)
    assert window.width == pytest.approx(0.6)
    assert window.opacity == pytest.approx(0.5)
    assert window.mode is MaskMode.SUBTRACT
    _session, report = _apply(
        session.project, {"op": "remove_color_window", "clip": "a", "window": "ciel"}
    )
    assert (
        report.ok
        and window_by_id(find_clip(session.project, "a").color_grade, "ciel") is None
    )


def test_window_bounds_reject_silent_clamp_candidates():
    for name, (low, high) in _MASK_BOUNDS.items():
        for outside in (low - 0.01, high + 0.01):
            with pytest.raises(ScriptInvalid):
                parse_script(
                    _script(
                        {
                            "op": "add_color_window",
                            "clip": "a",
                            "shape": "rectangle",
                            name: outside,
                        }
                    )
                )
            with pytest.raises(ScriptInvalid):
                parse_script(
                    _script(
                        {
                            "op": "set_color_window",
                            "clip": "a",
                            "window": "w",
                            name: outside,
                        }
                    )
                )
    _session, report = _apply(
        _project(),
        _clip(),
        {"op": "set_color_window", "clip": "a", "window": "missing"},
    )
    assert not report.ok
    _session, report = _apply(
        _project(),
        _clip(),
        {"op": "add_color_window", "clip": "a", "shape": "rectangle", "id": "w1"},
        {"op": "set_color_window", "clip": "a", "window": "w1"},
    )
    assert not report.ok and "au moins un champ" in report.error


def test_window_ids_are_scoped_per_clip():
    session, report = _apply(
        _project(),
        {"op": "add_clip", "track": "V1", "asset": "m", "at": 0, "id": "a"},
        {"op": "add_clip", "track": "V1", "asset": "m", "at": 5, "id": "b"},
        {"op": "add_color_window", "clip": "a", "shape": "rectangle", "id": "w1"},
        {"op": "add_color_window", "clip": "b", "shape": "ellipse", "id": "w1"},
    )
    assert report.ok, report.error
    assert (
        window_by_id(find_clip(session.project, "a").color_grade, "w1").shape
        is MaskShape.RECTANGLE
    )
    assert (
        window_by_id(find_clip(session.project, "b").color_grade, "w1").shape
        is MaskShape.ELLIPSE
    )


def test_window_on_named_node_and_track_lock():
    session, report = _apply(
        _project(),
        _clip(),
        {"op": "add_color_node", "clip": "a", "id": "n2"},
        {
            "op": "add_color_window",
            "clip": "a",
            "node": "n2",
            "shape": "rectangle",
            "id": "w2",
        },
    )
    assert report.ok, report.error
    node = as_graph(find_clip(session.project, "a").color_grade).corrector("n2")
    assert node.window("w2") is not None
    _session, report = _apply(
        _project(),
        _clip(),
        {"op": "set_track_locked", "track": "V1", "locked": True},
        {"op": "add_color_window", "clip": "a", "shape": "rectangle"},
    )
    assert not report.ok and "verrouillée" in report.error


# --- Qualifieur --------------------------------------------------------------------------------------------------------


def test_set_and_clear_color_qualifier():
    session, report = _apply(
        _project(),
        _clip(),
        {
            "op": "set_color_qualifier",
            "clip": "a",
            "hue_center": 120,
            "hue_width": 40,
            "sat_low": 0.2,
        },
    )
    assert report.ok, report.error
    qualifier = (
        ColorGradingService().get_graph(session.project, "a").corrector("n1").qualifier
    )
    assert isinstance(qualifier, Qualifier)
    assert (
        qualifier.hue_center == 120
        and qualifier.hue_width == 40
        and qualifier.sat_low == 0.2
    )
    assert qualifier.restricts()
    _session, report = _apply(
        session.project, {"op": "clear_color_qualifier", "clip": "a"}
    )
    assert (
        report.ok
        and ColorGradingService()
        .get_graph(session.project, "a")
        .corrector("n1")
        .qualifier
        is None
    )


def test_qualifier_rejects_inverted_ranges_and_oob():
    _session, report = _apply(
        _project(),
        _clip(),
        {"op": "set_color_qualifier", "clip": "a", "sat_low": 0.8, "sat_high": 0.2},
    )
    assert not report.ok and "bas d'une plage" in report.error
    for op in (
        {"op": "set_color_qualifier", "clip": "a", "hue_center": 361},
        {"op": "set_color_qualifier", "clip": "a", "hue_soft": -0.01},
        {"op": "set_color_qualifier", "clip": "a", "lum_high": 1.01},
    ):
        with pytest.raises(ScriptInvalid):
            parse_script(_script(op))
    _session, report = _apply(
        _project(), _clip(), {"op": "set_color_qualifier", "clip": "a"}
    )
    assert not report.ok and "au moins un champ" in report.error


def test_the_new_verbs_are_in_the_table():
    assert {
        "add_color_window",
        "set_color_window",
        "remove_color_window",
        "set_color_qualifier",
        "clear_color_qualifier",
    } <= set(VERBS)
