"""Scripts et masques de compositing d'un clip (ADR-0005 étape 4, ADR-0006 étape 3b) : `add_mask`, `set_mask`, `set_mask_points`, `remove_mask`.

Aucune sémantique propre : le résultat est celui de l'inspecteur Compositing (`ui/compositing_editor.py`) et de l'éditeur du moniteur ;
les valeurs écrites à la main sont refusées, jamais ramenées par `Mask` ; le recadrage (`crop`) reste le domaine de `set_crop`.
"""

from __future__ import annotations

import json
import math
from dataclasses import replace

import pytest

from core import mask_path, tracking_bindings
from core.clip_crop import CROP_MASK_ID, Crop, crop_of
from core.compositing import (
    DEFAULT_POLYGON,
    Compositing,
    Mask,
    MaskMode,
    MaskShape,
    mask_property_id,
)
from core.mask_path import DEFAULT_NODES, DEFAULT_TANGENTS, MAX_NODES
from core.project_io import (
    COLOR_MATRIX_VERSION,
    CURRENT_VERSION,
    PATH_MASK_VERSION,
    load_project,
    save_project,
)
from core.project_model import MediaAsset, Project, Track
from core.scripting import ScriptInvalid, Session, apply_script, parse_script
from core.scripting.mask_verbs import MASK_VERBS, TRACKING_CROP_ID
from core.scripting.path_nodes import nodes_to_arrays
from core.scripting.verbs import VERBS
from core.timeline_operations import find_clip
from core.tracking_model import ClipTracking, TrackLink, TrackTarget
from ui import i18n

SQUARE = [
    {"x": -0.5, "y": -0.5},
    {"x": 0.5, "y": -0.5},
    {"x": 0.5, "y": 0.5},
    {"x": -0.5, "y": 0.5},
]
TRIANGLE = [{"x": 0.0, "y": -0.5}, {"x": 0.5, "y": 0.4}, {"x": -0.5, "y": 0.4}]
K = mask_path.KAPPA
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


def _invalid(*ops: dict) -> ScriptInvalid:
    with pytest.raises(ScriptInvalid) as caught:
        parse_script(_script(*ops))
    return caught.value


CLIP = {"op": "add_clip", "track": "V1", "asset": "m", "at": 0, "id": "a"}


def add(shape="rectangle", **extra) -> dict:
    return {"op": "add_mask", "clip": "a", "shape": shape, "id": "m1", **extra}


def edit(**extra) -> dict:
    return {"op": "set_mask", "clip": "a", "mask": "m1", **extra}


def points(**extra) -> dict:
    return {"op": "set_mask_points", "clip": "a", "mask": "m1", **extra}


def remove(mask="m1") -> dict:
    return {"op": "remove_mask", "clip": "a", "mask": mask}


def masks(project: Project, clip_id: str = "a") -> tuple[Mask, ...]:
    return find_clip(project, clip_id).compositing.masks


def mask(project: Project, mask_id: str = "m1") -> Mask:
    found = find_clip(project, "a").compositing.mask_by_id(mask_id)
    assert found is not None
    return found


def animate(
    project: Project, mask_id: str, name: str, value: float = 0.1, clip_id: str = "a"
) -> None:
    """Pose une image-clé `mask.<id>.<nom>` comme l'interface (aucun verbe de script ne le fait)."""
    from core.animation import Keyframe

    clip = find_clip(project, clip_id)
    clip.animation = [
        *clip.animation,
        Keyframe(mask_property_id(mask_id, name), 0.0, value),
    ]


def _fails(project: Project, *ops: dict, text: str = "") -> None:
    session, report = _apply(project, *ops)
    assert not report.ok and text in report.error, report.error


# --- Table, schéma, ADR --------------------------------------------------------------------------------------------------------


def test_the_four_verbs_are_in_the_table_next_to_the_crop_ones():
    names = list(VERBS)
    assert {verb.name for verb in MASK_VERBS} == {
        "add_mask",
        "set_mask",
        "set_mask_points",
        "remove_mask",
    }
    assert names[names.index("clear_crop") + 1 : names.index("clear_crop") + 5] == [
        verb.name for verb in MASK_VERBS
    ]
    assert (
        VERBS["add_mask"].creates == "mask"
        and VERBS["add_mask"].creates_scope == "clip"
    )


def test_the_reserved_tracking_crop_id_is_the_one_of_the_tracking_module():
    assert TRACKING_CROP_ID == tracking_bindings.CROP_MASK_ID


def test_the_schema_bounds_the_lists_and_numbers():
    from core.scripting.schema import json_schema

    text = json.dumps(json_schema())
    assert '"maxItems": 128' in text and '"minItems": 3' in text


# --- Créer, modifier, retirer ---------------------------------------------------------------------------------------------------


def test_a_script_creates_a_mask_of_every_shape_edits_and_removes_them():
    session, report = _apply(
        _project(),
        CLIP,
        add("rectangle", id="r"),
        add("ellipse", id="e", mode="subtract", feather=0.1),
        add("polygon", id="p", points=TRIANGLE),
        add("path", id="c", nodes=ROUND, inverted=True, name="Courbe"),
    )
    assert report.ok, report.error
    project = session.project
    assert [m.id for m in masks(project)] == ["r", "e", "p", "c"]
    assert [m.shape for m in masks(project)] == [
        MaskShape.RECTANGLE,
        MaskShape.ELLIPSE,
        MaskShape.POLYGON,
        MaskShape.PATH,
    ]
    assert mask(project, "p").points == ((0.0, -0.5), (0.5, 0.4), (-0.5, 0.4))
    assert (
        mask(project, "c").points[0] == (0.0, -0.5)
        and mask(project, "c").inverted
        and mask(project, "c").name == "Courbe"
    )
    assert mask(project, "e").mode is MaskMode.SUBTRACT and mask(
        project, "e"
    ).feather == pytest.approx(0.1)
    session, report = _apply(
        project,
        {
            "op": "set_mask",
            "clip": "a",
            "mask": "r",
            "position_x": 0.3,
            "width": 0.25,
            "mode": "intersect",
            "opacity": 0.5,
            "name": "Cadre",
        },
        {"op": "set_mask_points", "clip": "a", "mask": "p", "points": SQUARE},
        {"op": "set_mask_points", "clip": "a", "mask": "c", "nodes": SQUARE},
        remove("e"),
    )
    assert report.ok, report.error
    project = session.project
    assert [m.id for m in masks(project)] == ["r", "p", "c"]
    r = mask(project, "r")
    assert (r.position_x, r.width, r.mode, r.opacity, r.name) == (
        0.3,
        0.25,
        MaskMode.INTERSECT,
        0.5,
        "Cadre",
    )
    assert mask(project, "p").points == (
        (-0.5, -0.5),
        (0.5, -0.5),
        (0.5, 0.5),
        (-0.5, 0.5),
    )
    assert (
        mask(project, "c").handles == ((0.0, 0.0, 0.0, 0.0),) * 4
        and len(mask(project, "c").points) == 4
    )
    assert mask(
        project, "c"
    ).inverted  # ce que set_mask_points ne touche pas est conservé


def test_defaults_are_the_models_and_the_inspectors_not_the_color_windows():
    session, report = _apply(
        _project(),
        CLIP,
        add("rectangle", id="r"),
        add("polygon", id="p"),
        add("path", id="c"),
    )
    assert report.ok, report.error
    for shape, mask_id in (
        (MaskShape.RECTANGLE, "r"),
        (MaskShape.POLYGON, "p"),
        (MaskShape.PATH, "c"),
    ):
        assert mask(session.project, mask_id) == Mask(
            shape=shape
        )  # width .5, feather 0 : pas les 0,4 / 0,03 des fenêtres
    assert mask(session.project, "p").points == DEFAULT_POLYGON
    assert (
        mask(session.project, "c").points == DEFAULT_NODES
        and mask(session.project, "c").handles == DEFAULT_TANGENTS
    )


def test_without_id_the_identifier_is_generated_like_the_inspector_and_aliases_work():
    session, report = _apply(
        _project(),
        CLIP,
        {"op": "add_mask", "clip": "a", "shape": "ellipse", "as": "oval"},
        {"op": "add_mask", "clip": "a", "shape": "ellipse"},
        {"op": "set_mask", "clip": "a", "mask": {"ref": "oval"}, "feather": 0.2},
    )
    assert report.ok, report.error
    first, second = masks(session.project)
    assert first.id == "oval" and first.feather == pytest.approx(0.2)
    assert len(second.id) == 8 and int(second.id, 16) >= 0 and second.id != first.id


def test_a_new_mask_goes_before_the_crop_which_stays_last():
    session, report = _apply(
        _project(),
        CLIP,
        {"op": "set_crop", "clip": "a", "left": 0.1},
        add("ellipse"),
        add("rectangle", id="m2"),
    )
    assert report.ok, report.error
    assert [m.id for m in masks(session.project)] == ["m1", "m2", CROP_MASK_ID]
    assert crop_of(find_clip(session.project, "a")) == Crop(left=0.1)


def test_one_undo_step_per_script_and_undo_restores_the_stack():
    session = Session(_project())
    assert apply_script(
        session, parse_script(_script(CLIP, add("path", nodes=ROUND)))
    ).ok
    before = masks(session.project)
    assert apply_script(
        session,
        parse_script(
            _script(
                edit(feather=0.3),
                points(nodes=SQUARE),
                add("ellipse", id="m2"),
                remove(),
            )
        ),
    ).ok
    assert [m.id for m in masks(session.project)] == ["m2"]
    session.undo()
    assert (
        masks(session.project) == before
        and mask(session.project).points == before[0].points
    )
    assert [m.id for m in masks(session.project)] == ["m1"] and mask(
        session.project
    ).handles == before[0].handles


def test_a_failed_script_leaves_the_project_untouched():
    session = Session(_project())
    assert apply_script(session, parse_script(_script(CLIP, add("ellipse")))).ok
    before = masks(session.project)
    report = apply_script(
        session, parse_script(_script(edit(feather=0.3), add("rectangle", id="m1")))
    )  # identifiant pris
    assert not report.ok and masks(session.project) == before


def test_a_mask_can_be_given_back_an_empty_name_and_omitting_the_name_keeps_it():
    session, report = _apply(
        _project(),
        CLIP,
        add("ellipse", name="Vignette", feather=0.2, mode="subtract"),
        edit(opacity=0.5),
    )
    assert (
        report.ok and mask(session.project).name == "Vignette"
    )  # omettre le champ conserve le nom
    before = mask(session.project)
    assert apply_script(session, parse_script(_script(edit(name="")))).ok
    after = mask(session.project)
    assert (
        after.name == ""
        and replace(after, name="Vignette", id=after.id) == before
        and after.id == "m1"
    )
    session, report = _apply(_project(), CLIP, add("ellipse", name=""))
    assert report.ok and mask(session.project).name == ""
    assert (
        _invalid(add(name="x" * 201))
        and _invalid(edit(name="x" * 201))
        and _invalid(edit(name=None))
        and _invalid(edit(name=3))
    )
    assert _invalid(edit(name=" a")) and _invalid(
        edit(name=" ")
    )  # les bords blancs restent refusés


def test_set_mask_keeps_what_it_does_not_name_and_the_shape():
    session, report = _apply(
        _project(),
        CLIP,
        add("path", nodes=ROUND, feather=0.1, mode="subtract"),
        edit(opacity=0.4),
    )
    assert report.ok, report.error
    result = mask(session.project)
    assert (result.shape, result.mode, result.feather, result.opacity) == (
        MaskShape.PATH,
        MaskMode.SUBTRACT,
        0.1,
        0.4,
    )
    assert result.points[0] == (0.0, -0.5) and result.id == "m1"


# --- Parité avec l'interface -----------------------------------------------------------------------------------------------------


def _ui_stack(qtbot, shape: MaskShape, **fields):
    """La pile que l'inspecteur Compositing écrit : choisir la forme, « Ajouter », puis régler les champs."""
    from ui.compositing_editor import CompositingEditor

    editor = CompositingEditor("")
    qtbot.addWidget(editor)
    seen: list = []
    editor.value_changed.connect(seen.append)
    editor.set_value(Compositing())
    editor.mask_shape.setCurrentIndex(editor.mask_shape.findData(shape.value))
    editor.add_mask.click()
    for name, value in fields.items():
        if name == "mode":
            editor.mask_mode.setCurrentIndex(editor.mask_mode.findData(value))
        elif name == "inverted":
            editor.inverted.setChecked(value)
        else:
            editor.mask_spins[name].setValue(
                value * (1.0 if name == "rotation" else 100.0)
            )
    return seen[-1]


@pytest.mark.parametrize(
    "shape", [MaskShape.RECTANGLE, MaskShape.ELLIPSE, MaskShape.POLYGON, MaskShape.PATH]
)
def test_the_result_is_the_one_of_the_inspector(qtbot, shape):
    fields = {
        "position_x": 0.4,
        "position_y": 0.6,
        "width": 0.7,
        "height": 0.3,
        "rotation": 15.0,
        "feather": 0.1,
        "expansion": -0.05,
        "opacity": 0.8,
        "mode": "subtract",
        "inverted": True,
    }
    by_ui = _ui_stack(qtbot, shape, **fields)
    session, report = _apply(_project(), CLIP, add(shape.value, **fields))
    assert report.ok, report.error
    assert (
        len(by_ui.masks) == 1 and masks(session.project) == by_ui.masks
    )  # Mask compare tout sauf l'identifiant


def test_the_scripted_edit_equals_the_monitors_write_and_the_matte_is_byte_identical(
    qtbot,
):
    pytest.importorskip("PySide6")
    from PySide6.QtCore import QRect

    from core.mograph_raster import render_matte
    from core.mograph_scene import IDENTITY

    session, report = _apply(
        _project(),
        CLIP,
        add("path", nodes=ROUND, width=0.6, height=0.8, feather=0.03),
        points(nodes=SQUARE),
    )
    assert report.ok, report.error
    scripted = mask(session.project)
    # Éditeur du moniteur (MaskEditMixin._write_mask_nodes) : replace(mask, points=…, handles=…, id=…).
    created = mask(
        _apply(
            _project(),
            CLIP,
            add("path", nodes=ROUND, width=0.6, height=0.8, feather=0.03),
        )[0].project
    )
    by_editor = replace(
        created, points=scripted.points, handles=scripted.handles, id=created.id
    )
    assert by_editor == scripted
    rect = QRect(0, 0, 160, 90)
    matte_script = render_matte([scripted], (160.0, 90.0), IDENTITY, rect)
    matte_editor = render_matte([by_editor], (160.0, 90.0), IDENTITY, rect)
    assert matte_script is not None and matte_editor is not None
    assert bytes(matte_script.constBits()) == bytes(matte_editor.constBits())
    assert (
        matte_script.pixelColor(80, 45).alpha() > 0
        and matte_script.pixelColor(2, 2).alpha() == 0
    )  # le matte est réellement tracé


# --- Refus -------------------------------------------------------------------------------------------------------------------


def test_the_crop_mask_belongs_to_set_crop():
    for op in (add(id="crop"), add(id="trackcrop")):
        _fails(_project(), CLIP, op, text="réservé")
    base = (CLIP, {"op": "set_crop", "clip": "a", "left": 0.1})
    for op in (
        edit(mask="crop", feather=0.1),
        remove("crop"),
        points(mask="crop", points=TRIANGLE),
    ):
        _fails(_project(), *base, {**op, "mask": "crop"}, text="réservé")
    session, report = _apply(_project(), *base)
    assert report.ok and crop_of(find_clip(session.project, "a")) == Crop(
        left=0.1
    )  # intact


def test_duplicate_ids_across_masks_color_windows_and_orphan_animation_are_refused():
    assert _invalid(CLIP, add(), add())  # le décodeur voit le doublon dans le script
    session, report = _apply(_project(), CLIP, add())
    report = apply_script(
        session, parse_script(_script(add()))
    )  # et le verbe, sur un projet qui a déjà ce masque
    assert not report.ok and "m1" in report.error
    _fails(
        _project(),
        CLIP,
        {"op": "add_color_window", "clip": "a", "shape": "ellipse", "id": "m1"},
        add(),
        text="m1",
    )
    session, report = _apply(_project(), CLIP, add("ellipse", id="o"), remove("o"))
    assert (
        report.ok
        and apply_script(session, parse_script(_script(add("ellipse", id="o")))).ok
    )  # un identifiant retiré redevient libre
    # Une animation orpheline `mask.m1.*` ne doit pas être reprise par un nouveau masque du même nom.
    session, report = _apply(_project(), CLIP)
    assert report.ok, report.error
    animate(session.project, "m1", "feather")
    report = apply_script(session, parse_script(_script(add("ellipse"))))
    assert not report.ok and "m1" in report.error
    for bad in ("a b", "é", "x" * 80, ""):
        assert _invalid(add(id=bad))


def test_numbers_are_checked_against_the_models_bounds_and_never_clamped():
    from core.compositing import _MASK_BOUNDS

    for name, (low, high) in _MASK_BOUNDS.items():
        assert _invalid(add(**{name: high + 1.0})) and _invalid(
            add(**{name: low - 1.0})
        )
        assert _invalid(edit(**{name: high + 0.001}))
        session, report = _apply(
            _project(), CLIP, add(**{name: high}), edit(**{name: low})
        )  # les bornes exactes passent, intactes
        assert report.ok, report.error
        assert getattr(mask(session.project), name) == low
    for value in (True, "0.5", None, [0.5]):
        assert _invalid(add(feather=value)) and _invalid(edit(feather=value))
    assert (
        _invalid(edit(inverted=1))
        and _invalid(add(mode="xor"))
        and _invalid(add("hexagon"))
        and _invalid(add(name=" a "))
    )
    _fails(_project(), CLIP, add(), edit(), text="au moins un champ")


def test_facade_callers_get_the_same_strictness_as_the_decoder():
    """Un appelant Python peut passer sans le décodeur : NaN, booléen, forme inconnue sont refusés par le verbe."""
    project = Session(_project()).project
    session = Session(project)
    assert apply_script(session, parse_script(_script(CLIP, add("ellipse")))).ok
    for args in (
        {"feather": 5},
        {"width": -0.1},
        {"rotation": 4000},
        {"position_x": 3.5},
        {"opacity": 1.5},
        {"expansion": -2},
    ):  # Mask les écrêterait
        with pytest.raises(ValueError):
            VERBS["set_mask"].build({"clip": "a", "mask": "m1", **args})(
                session.project
            )
        with pytest.raises(ValueError):
            VERBS["add_mask"].build(
                {"clip": "a", "shape": "ellipse", "id": "z", **args}
            )(session.project)
    assert [m.id for m in masks(session.project)] == ["m1"] and mask(
        session.project
    ) == Mask(shape=MaskShape.ELLIPSE)
    for args in (
        {"feather": math.nan},
        {"feather": math.inf},
        {"feather": True},
        {"feather": "0.2"},
        {"mode": "xor"},
        {"inverted": "oui"},
        {"name": "x" * 500},
    ):
        with pytest.raises(ValueError) as caught:
            VERBS["set_mask"].build({"clip": "a", "mask": "m1", **args})(
                session.project
            )
        assert len(str(caught.value)) < 250  # le message ne recopie pas l'entrée
    with pytest.raises(ValueError):
        VERBS["add_mask"].build({"clip": "a", "shape": "hexagon" * 100})(
            session.project
        )
    for bad in (
        None,
        3,
        "abc",
        [],
        TRIANGLE[:2],
        [{"x": math.nan, "y": 0}] * 3,
        [{"x": 3.0, "y": 0}] * 3,
        [{"x": True, "y": 0}] * 3,
        [{"x": 0, "y": 0, "z": 1}] * 3,
        [[0, 0]] * 3,
        [{"x": 0, "y": 0}] * (MAX_NODES + 1),
    ):
        with pytest.raises(ValueError) as caught:
            VERBS["add_mask"].build(
                {"clip": "a", "shape": "polygon", "id": "q", "points": bad}
            )(session.project)
        assert len(str(caught.value)) < 250
    assert masks(session.project)[0].id == "m1" and len(masks(session.project)) == 1


def test_lists_are_bounded_before_they_are_read():
    huge = [{"x": 0.0, "y": 0.0}] * (MAX_NODES + 1)
    assert _invalid(add("polygon", points=huge)) and _invalid(add("path", nodes=huge))
    assert _invalid(add("polygon", points=TRIANGLE[:2])) and _invalid(
        add("path", nodes=SQUARE[:2])
    )
    assert _invalid(add("polygon", points=[{"x": 0.0, "y": 0.0}] * 3 + [{"x": 0.0}]))
    assert _invalid(
        add("polygon", points=[{"x": 2.01, "y": 0.0}] + TRIANGLE[:2])
    ) and _invalid(add("polygon", points=[{"x": 0, "y": 0, "z": 0}] * 3))
    assert _invalid(points(points="abc")) and _invalid(points(nodes={"x": 1}))
    edge = [{"x": 2.0, "y": -2.0}, *TRIANGLE[:2]]
    session, report = _apply(_project(), CLIP, add("polygon", points=edge))
    assert report.ok and mask(session.project).points[0] == (2.0, -2.0)


def test_points_and_nodes_belong_to_their_shape_and_exactly_one_is_given():
    _fails(_project(), CLIP, add("ellipse", nodes=SQUARE), text="path")
    _fails(_project(), CLIP, add("ellipse", points=TRIANGLE), text="polygon")
    _fails(_project(), CLIP, add("path", points=TRIANGLE), text="polygon")
    _fails(_project(), CLIP, add("polygon", nodes=SQUARE), text="path")
    _fails(_project(), CLIP, add("ellipse"), points(points=TRIANGLE), text="polygone")
    _fails(_project(), CLIP, add("ellipse"), points(nodes=SQUARE), text="courbe")
    _fails(_project(), CLIP, add("path"), points(points=TRIANGLE), text="polygone")
    _fails(_project(), CLIP, add("polygon"), points(nodes=SQUARE), text="courbe")
    _fails(
        _project(),
        CLIP,
        add("path"),
        points(nodes=SQUARE, points=TRIANGLE),
        text="un seul",
    )
    _fails(_project(), CLIP, add("path"), points(), text="un seul")


def test_unknown_masks_color_windows_and_clips_are_not_found():
    _fails(_project(), CLIP, edit(feather=0.1), text="introuvable")
    _fails(_project(), CLIP, remove("nope"), text="introuvable")
    # Une fenêtre d'étalonnage est un Mask mais pas une entrée de la pile : ses verbes sont `*_color_window`.
    _fails(
        _project(),
        CLIP,
        {"op": "add_color_window", "clip": "a", "shape": "ellipse", "id": "w1"},
        edit(mask="w1", feather=0.1),
        text="introuvable",
    )
    _fails(
        _project(),
        CLIP,
        {"op": "add_color_window", "clip": "a", "shape": "ellipse", "id": "w1"},
        remove("w1"),
        text="introuvable",
    )
    _fails(_project(), add(), text="introuvable")


def test_animated_properties_are_refused_by_set_mask_but_the_others_can_change():
    session, report = _apply(_project(), CLIP, add("ellipse"))
    assert report.ok, report.error
    animate(session.project, "m1", "feather")
    before = mask(session.project)
    report = apply_script(session, parse_script(_script(edit(feather=0.3))))
    assert not report.ok and "animé" in report.error and mask(session.project) == before
    report = apply_script(
        session,
        parse_script(
            _script(edit(opacity=0.3, mode="subtract", inverted=True, name="Autre"))
        ),
    )
    assert report.ok, report.error  # une propriété non animée du même masque se règle
    assert (
        mask(session.project).opacity == 0.3
        and mask(session.project).feather == before.feather
    )
    find_clip(session.project, "a").animation = []
    assert apply_script(
        session, parse_script(_script(edit(feather=0.3)))
    ).ok  # sans image-clé, la valeur s'écrit


def test_locked_tracks_locked_layers_and_wrong_track_kinds_are_refused_by_all_verbs():
    lock = {"op": "set_track_locked", "track": "V1", "locked": True}
    seeded = (CLIP, add("path", nodes=ROUND))
    for op in (add(id="m2"), edit(feather=0.1), points(nodes=SQUARE), remove()):
        _fails(_project(), *seeded, lock, op, text="verrouillée")
    for op in (
        add(id="m2"),
        edit(feather=0.1),
        points(points=TRIANGLE),
        remove(),
    ):  # piste audio : pas de masque
        _fails(
            _project(),
            {"op": "add_clip", "track": "A1", "asset": "m", "at": 0, "id": "a"},
            op,
            text="",
        )
    session = Session(_project())
    graphic = {
        "op": "add_graphic",
        "graphic": "solid",
        "at": 0,
        "duration": 2,
        "id": "a",
    }
    assert apply_script(
        session, parse_script(_script(graphic, add("path", nodes=ROUND)))
    ).ok
    before = masks(session.project)
    clip = find_clip(session.project, "a")
    clip.graphic = replace(clip.graphic, locked=True)  # calque verrouillé
    for op in (add(id="m2"), edit(feather=0.1), points(nodes=SQUARE), remove()):
        report = apply_script(session, parse_script(_script(op)))
        assert not report.ok and "verrouillé" in report.error
    assert masks(session.project) == before
    clip = find_clip(
        session.project, "a"
    )  # un script refusé restaure le projet : relire le clip
    clip.graphic = replace(clip.graphic, locked=False)
    assert apply_script(
        session,
        parse_script(_script(edit(feather=0.2), points(nodes=SQUARE), remove())),
    ).ok  # déverrouillé : possible


def test_subtitle_tracks_have_no_image_to_mask():
    project = _project()
    project.tracks.append(Track("S1", "S1", "subtitle"))
    _fails(
        project,
        {
            "op": "add_subtitle",
            "track": "S1",
            "text": "x",
            "at": 0,
            "duration": 1,
            "id": "a",
        },
        add(),
        text="",
    )


# --- Suppression : animation et suivi ----------------------------------------------------------------------------------------


def test_removing_a_mask_removes_its_keyframes_and_tracking_links_but_not_the_others():
    session, report = _apply(_project(), CLIP, add("ellipse"), add("ellipse", id="m2"))
    assert report.ok, report.error
    animate(session.project, "m1", "feather")
    animate(session.project, "m2", "feather", 0.2)
    clip = find_clip(session.project, "a")
    clip.tracking = ClipTracking(
        links=(
            TrackLink(id="l1", target=TrackTarget.MASK, mask_id="m1"),
            TrackLink(id="l2", target=TrackTarget.MASK, mask_id="m2"),
        )
    )
    assert apply_script(session, parse_script(_script(remove()))).ok
    clip = find_clip(session.project, "a")
    assert [kf.property_name for kf in clip.animation] == ["mask.m2.feather"]
    assert [link.id for link in clip.tracking.links] == ["l2"]
    assert [m.id for m in masks(session.project)] == ["m2"]


def test_set_mask_writes_the_entered_value_even_when_a_tracking_link_follows_the_mask():
    session, report = _apply(_project(), CLIP, add("ellipse", position_x=0.4))
    assert report.ok, report.error
    find_clip(session.project, "a").tracking = ClipTracking(
        links=(TrackLink(id="l1", target=TrackTarget.MASK, mask_id="m1"),)
    )
    assert apply_script(session, parse_script(_script(edit(position_x=0.6)))).ok
    assert (
        mask(session.project).position_x == 0.6
        and find_clip(session.project, "a").tracking.links[0].id == "l1"
    )


# --- Format .kut -------------------------------------------------------------------------------------------------------------


def _saved_version(project: Project, tmp_path) -> int:
    path = tmp_path / "p.kut"
    save_project(project, str(path))
    return json.loads(path.read_text(encoding="utf-8"))["version"]


def test_a_project_without_a_curve_stays_in_version_17_and_one_with_a_scripted_curve_is_saved_in_18(
    tmp_path,
):
    session, report = _apply(
        _project(), CLIP, add("ellipse", id="e"), add("polygon", id="p")
    )
    assert report.ok, report.error
    assert _saved_version(session.project, tmp_path) == CURRENT_VERSION == 17
    session, report = _apply(_project(), CLIP, add("path", nodes=ROUND))
    assert report.ok, report.error
    assert _saved_version(session.project, tmp_path) == PATH_MASK_VERSION == 18
    path = tmp_path / "p.kut"
    loaded = load_project(str(path))
    assert (
        mask(loaded) == mask(session.project)
        and mask(loaded).handles == mask(session.project).handles
    )
    session.undo()
    assert _saved_version(session.project, tmp_path) == 17


def test_a_curve_mask_with_a_media_color_matrix_still_saves_and_loads(tmp_path):
    session, report = _apply(
        _project(),
        CLIP,
        add("path", nodes=ROUND),
        {"op": "set_media_color_matrix", "asset": "m", "matrix": "bt709"},
    )
    assert report.ok, report.error
    assert _saved_version(session.project, tmp_path) == COLOR_MATRIX_VERSION == 19
    assert mask(load_project(str(tmp_path / "p.kut"))) == mask(session.project)


# --- CLI ---------------------------------------------------------------------------------------------------------------------


def test_the_cli_validates_and_applies_the_verbs(tmp_path, capsys):
    from core import cli

    save_project(_project(), str(tmp_path / "p.kut"))
    script = tmp_path / "s.json"
    script.write_text(
        json.dumps(
            _script(
                CLIP,
                add("path", nodes=ROUND),
                edit(feather=0.2),
                points(nodes=SQUARE),
                add("polygon", id="p", points=TRIANGLE),
                {"op": "remove_mask", "clip": "a", "mask": "p"},
            )
        ),
        encoding="utf-8",
    )
    assert cli.run(["validate", str(script)]) == 0
    out = tmp_path / "out.kut"
    assert cli.run(["apply", str(script), str(tmp_path / "p.kut"), "-o", str(out)]) == 0
    capsys.readouterr()
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert (
        payload["version"] == PATH_MASK_VERSION
        and '"shape": "path"' in json.dumps(payload)
        and '"polygon"' not in json.dumps(payload)
    )
    bad = tmp_path / "bad.json"
    bad.write_text(
        json.dumps(_script(CLIP, add("polygon", points=TRIANGLE[:2]))), encoding="utf-8"
    )
    assert cli.run(["validate", str(bad)]) != 0


def test_nodes_to_arrays_is_still_strict():
    assert len(nodes_to_arrays(SQUARE)[0]) == 4
    with pytest.raises(ValueError):
        nodes_to_arrays(SQUARE[:2])
