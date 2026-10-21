"""Format déclaratif : guides du moniteur (lot 4 de l'extension 0.8.0) : `add_guide`, `move_guide`, `remove_guide`, `clear_guides`.

Chaque verbe appelle la fonction de ``core/canvas_guides.py`` que le moniteur appelle. Le modèle bornerait une position et lirait une
orientation inconnue comme verticale : un script les reçoit refusés. Un guide verrouillé ne bouge pas et ne part pas.
"""

from __future__ import annotations

import copy
import math

import pytest

from core import canvas_guides
from core.canvas_guides import Guide, GuideOrientation
from core.project_model import Project, Sequence, Track
from core.scripting import (
    ScriptInvalid,
    Session,
    apply_script,
    created_ids,
    parse_script,
)
from core.scripting.schema import json_schema
from core.scripting.verbs import VERBS
from ui import i18n

GUIDE_VERBS = ("add_guide", "move_guide", "remove_guide", "clear_guides")


@pytest.fixture(autouse=True)
def _french(monkeypatch):
    monkeypatch.setenv("KUT_STUDIO_LANG", "fr")
    i18n.reset_for_tests()
    yield
    i18n.reset_for_tests()


def _project() -> Project:
    project = Project(
        "Démo", width=320, height=180, fps=30.0, tracks=[Track("V1", "V1", "video")]
    )
    sequence = project.active_sequence
    sequence.guides = [
        Guide(GuideOrientation.VERTICAL, 0.5, id="un"),
        Guide(GuideOrientation.HORIZONTAL, 0.25, id="deux"),
        Guide(GuideOrientation.VERTICAL, 0.9, locked=True, id="verrou"),
    ]
    return project


def _op(verb: str, /, **fields) -> dict:
    return {"op": verb, **fields}


def _apply(project: Project, *ops: dict):
    session = Session(project)
    return session, apply_script(
        session, parse_script({"kut_script": 1, "ops": list(ops)})
    )


def _guides(project: Project):
    return copy.deepcopy(project.active_sequence.guides)


def _parity(op: dict, direct) -> Project:
    scripted = _project()
    session, report = _apply(scripted, op)
    assert report.ok, report.error
    reference = _project()
    direct(reference.active_sequence)
    assert _guides(session.project) == _guides(reference)
    return session.project


def _refused(project: Project, *ops: dict, match: str | None = None):
    before = _guides(project)
    session, report = _apply(project, *ops)
    assert not report.ok, "le script aurait dû être refusé"
    assert _guides(session.project) == before
    if match is not None:
        assert match in report.error, report.error
    return report


def _invalid(*ops: dict) -> None:
    with pytest.raises(ScriptInvalid):
        parse_script({"kut_script": 1, "ops": list(ops)})


def test_the_verbs_are_in_the_table_with_the_right_shape():
    for name in GUIDE_VERBS:
        assert name in VERBS and VERBS[name].group == "guides" and VERBS[name].doc, name
    assert (
        VERBS["add_guide"].creates == "guide"
        and "id" in VERBS["add_guide"].accepts
        and "as" in VERBS["add_guide"].accepts
    )
    assert all(VERBS[name].creates is None for name in GUIDE_VERBS[1:])
    titles = {
        item["title"] for item in json_schema()["properties"]["ops"]["items"]["oneOf"]
    }
    assert set(GUIDE_VERBS) <= titles


def test_add_move_remove_and_clear_match_the_model():
    project = _parity(
        _op("add_guide", orientation="horizontal", position=0.75, id="neuf"),
        lambda s: canvas_guides.add_guide(s, "horizontal", 0.75, guide_id="neuf"),
    )
    assert [g.id for g in project.active_sequence.guides] == [
        "un",
        "deux",
        "verrou",
        "neuf",
    ]
    _parity(
        _op("move_guide", guide="un", position=0.3),
        lambda s: canvas_guides.move_guide(s, "un", 0.3),
    )
    _parity(
        _op("remove_guide", guide="deux"),
        lambda s: canvas_guides.remove_guide(s, "deux"),
    )
    project = _parity(_op("clear_guides"), canvas_guides.clear_guides)
    assert [g.id for g in project.active_sequence.guides] == ["verrou"]


def test_a_guide_can_be_cited_by_alias_end_to_end():
    project = _project()
    script = parse_script(
        {
            "kut_script": 1,
            "ops": [
                _op(
                    "add_guide", orientation="vertical", position=0.1, **{"as": "marge"}
                ),
                _op("move_guide", guide={"ref": "marge"}, position=0.2),
                _op(
                    "add_guide",
                    orientation="horizontal",
                    position=0.5,
                    **{"as": "milieu"},
                ),
                _op("remove_guide", guide={"ref": "milieu"}),
            ],
        }
    )
    session = Session(project)
    report = apply_script(session, script)
    assert report.ok, report.error
    assert created_ids(script, report) == [
        {"op": "0:add_guide", "id": "marge"},
        {"op": "2:add_guide", "id": "milieu"},
    ]
    guides = {g.id: g for g in session.project.active_sequence.guides}
    assert guides["marge"].position == 0.2 and "milieu" not in guides


def test_add_guide_without_id_keeps_the_models_generated_id():
    session, report = _apply(
        _project(), _op("add_guide", orientation="vertical", position=0.4)
    )
    assert report.ok
    guide = report.results[0].value
    assert len(guide.id) == 8 and guide in session.project.active_sequence.guides


def test_guide_ids_are_checked_like_every_created_id():
    _refused(
        _project(),
        _op("add_guide", orientation="vertical", position=0.4, id="un"),
        match="existe déjà",
    )
    _invalid(_op("add_guide", orientation="vertical", position=0.4, id="a b"))
    _invalid(_op("add_guide", orientation="vertical", position=0.4, id="x" * 65))
    _invalid(
        _op("add_guide", orientation="vertical", position=0.4, id="g1"),
        _op("add_guide", orientation="vertical", position=0.4, id="g1"),
    )


@pytest.mark.parametrize("position", [-1.0, 0.0, 1.0, 2.0])
def test_positions_inside_the_models_range_are_stored_as_written(position):
    session, report = _apply(
        _project(), _op("add_guide", orientation="vertical", position=position, id="g")
    )
    assert (
        report.ok
        and next(
            g for g in session.project.active_sequence.guides if g.id == "g"
        ).position
        == position
    )


@pytest.mark.parametrize(
    "position",
    [-1.0001, 2.0001, math.nan, math.inf, -math.inf, 1e300, True, "0.5", None],
)
def test_positions_the_model_would_clamp_are_refused_at_reading(position):
    _invalid(_op("add_guide", orientation="vertical", position=position))
    _invalid(_op("move_guide", guide="un", position=position))


def test_the_orientation_is_an_enumeration_and_nothing_is_read_as_vertical():
    _invalid(_op("add_guide", orientation="diagonal", position=0.5))
    _invalid(_op("add_guide", orientation="", position=0.5))
    _invalid(_op("add_guide", orientation=1, position=0.5))
    _invalid(_op("add_guide", position=0.5))
    _invalid(_op("add_guide", orientation="vertical"))


def test_a_locked_guide_neither_moves_nor_goes_away_but_survives_clear():
    _refused(
        _project(), _op("move_guide", guide="verrou", position=0.1), match="verrouillé"
    )
    _refused(_project(), _op("remove_guide", guide="verrou"), match="verrouillé")
    session, report = _apply(_project(), _op("clear_guides"))
    assert report.ok and [g.id for g in session.project.active_sequence.guides] == [
        "verrou"
    ]
    assert report.results[0].value == 2  # le nombre de guides retirés


def test_unknown_guides_and_wrong_selectors_are_refused():
    _refused(
        _project(),
        _op("move_guide", guide="fantome", position=0.1),
        match="introuvable",
    )
    _refused(_project(), _op("remove_guide", guide="fantome"), match="introuvable")
    _invalid(_op("remove_guide"))
    _invalid(_op("move_guide", guide="un"))
    _invalid(_op("clear_guides", position=0.5))
    with pytest.raises(ScriptInvalid) as caught:  # un alias de guide n'est pas un clip
        parse_script(
            {
                "kut_script": 1,
                "ops": [
                    _op(
                        "add_guide", orientation="vertical", position=0.4, **{"as": "g"}
                    ),
                    _op("move_clip", clip={"ref": "g"}, at=0),
                ],
            }
        )
    assert caught.value.issues[0].code == "alias_kind"


def test_guides_belong_to_the_active_sequence():
    project = _project()
    other = Sequence(id="seq2", name="Autre", width=320, height=180, fps=30.0)
    project.sequences.append(other)
    session, report = _apply(
        project,
        _op("set_active_sequence", sequence="seq2"),
        _op("add_guide", orientation="vertical", position=0.4, id="ailleurs"),
    )
    assert report.ok, report.error
    assert [g.id for g in other.guides] == ["ailleurs"]
    assert [g.id for g in session.project.sequences[0].guides] == [
        "un",
        "deux",
        "verrou",
    ]
    _refused(
        project,
        _op("set_active_sequence", sequence="seq2"),
        _op("move_guide", guide="un", position=0.1),
        match="introuvable",
    )


def test_guide_edits_are_atomic_and_one_undo_step():
    project = _project()
    before = _guides(project)
    session, report = _apply(
        project,
        _op("add_guide", orientation="vertical", position=0.4, id="g"),
        _op("remove_guide", guide="verrou"),
    )
    assert not report.ok and _guides(session.project) == before
    session, report = _apply(
        project,
        _op("add_guide", orientation="vertical", position=0.4, id="g"),
        _op("move_guide", guide="un", position=0.1),
    )
    assert (
        report.ok
        and _guides(session.project) != before
        and session.undo()
        and _guides(session.project) == before
    )


def test_the_models_interface_calls_are_unchanged():
    """`add_guide(sequence, orientation, position)` sans identifiant imposé garde son comportement (repli et bornes comprises)."""
    sequence = _project().active_sequence
    guide = canvas_guides.add_guide(sequence, "n'importe quoi", 9.0)
    assert (
        guide.orientation is GuideOrientation.VERTICAL
        and guide.position == 2.0
        and len(guide.id) == 8
    )
