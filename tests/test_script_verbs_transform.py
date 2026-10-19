"""Format déclaratif : transform d'un clip (lot 2 de l'extension 0.8.0) : `set_transform`, `reset_transform`.

`set_transform` fait ce que l'inspecteur « Mouvement » fait (`ClipTransform.with_property` puis `set_clip_transform`) ; le test de parité fait le
même appel directement. Les valeurs hors bornes, les propriétés animées, les genres de piste et les verrous sont refusés, sans écriture partielle.
"""

from __future__ import annotations

import copy
import math
from dataclasses import replace

import pytest

from core.project_model import Clip, MediaAsset, Project, Track
from core.scripting import ScriptInvalid, Session, apply_script, parse_script
from core.scripting.schema import json_schema
from core.scripting.verbs import VERBS
from core.timeline_operations import find_clip, reset_clip_transform, set_clip_transform, set_transform_keyframe
from core.visual_effects import TRANSFORM_PROPERTIES, TRANSFORM_PROPERTY_NAMES, ClipTransform
from ui import i18n


@pytest.fixture(autouse=True)
def _french(monkeypatch):
    monkeypatch.setenv("KUT_STUDIO_LANG", "fr")
    i18n.reset_for_tests()
    yield
    i18n.reset_for_tests()


def _project(*, locked_v1: bool = False, locked_layer: bool = False) -> Project:
    project = Project(
        "Démo", width=320, height=180, fps=30.0,
        media_assets=[MediaAsset("m", "/x/intro.mp4", "intro", 20.0, 320, 180, 30.0, "video", has_audio=True),
                      MediaAsset("s", "/x/son.wav", "son", 20.0, 0, 0, 0.0, "audio", has_audio=True)],
        tracks=[
            Track("V1", "V1", "video", locked=locked_v1, clips=[Clip("fond", "m", "V1", 0.0, 0.0, 6.0)]),
            Track("V2", "V2", "video", clips=[Clip("c", "m", "V2", 1.0, 0.0, 2.0, transform=ClipTransform(scale=0.5, rotation=15.0))]),
            Track("V3", "V3", "video", locked=True, clips=[Clip("d", "m", "V3", 0.0, 0.0, 2.0)]),
            Track("A1", "A1", "audio", clips=[Clip("sound", "s", "A1", 0.0, 0.0, 4.0)]),
            Track("G1", "G1", "graphics"),
            Track("S1", "S1", "subtitle"),
        ],
    )
    session = Session(project)
    report = apply_script(session, parse_script({"kut_script": 1, "ops": [
        {"op": "add_graphic", "graphic": "text", "at": 0, "duration": 3, "id": "t1", "text": "A", "track": "G1"},
    ]}))
    assert report.ok, report.error
    if locked_layer:
        layer = find_clip(project, "t1")
        layer.graphic = replace(layer.graphic, locked=True)
    return project


def _op(verb: str, /, **fields) -> dict:
    return {"op": verb, **fields}


def _apply(project: Project, *ops: dict):
    session = Session(project)
    return session, apply_script(session, parse_script({"kut_script": 1, "ops": list(ops)}))


def _snapshot(project: Project):
    return copy.deepcopy(project.tracks)


def _parity(project_factory, op: dict, direct) -> Project:
    scripted = project_factory()
    session, report = _apply(scripted, op)
    assert report.ok, report.error
    reference = project_factory()
    direct(reference)
    assert _snapshot(session.project) == _snapshot(reference)
    return session.project


def _refused(project: Project, *ops: dict):
    before = _snapshot(project)
    session, report = _apply(project, *ops)
    assert not report.ok, "le script aurait dû être refusé"
    assert _snapshot(session.project) == before
    return report


def _invalid(*ops: dict) -> None:
    with pytest.raises(ScriptInvalid):
        parse_script({"kut_script": 1, "ops": list(ops)})


def _inspector(project: Project, clip_id: str, **values) -> None:
    """Le geste de l'inspecteur : un champ après l'autre, par ``with_property``, puis ``set_clip_transform``."""
    for name, value in values.items():
        set_clip_transform(project, clip_id, find_clip(project, clip_id).transform.with_property(name, value))


def test_the_verbs_are_in_the_table_with_the_right_shape():
    for name in ("set_transform", "reset_transform"):
        assert VERBS[name].creates is None and VERBS[name].doc and VERBS[name].group == "transform"
    assert {"set_transform", "reset_transform"} <= {item["title"] for item in json_schema()["properties"]["ops"]["items"]["oneOf"]}


def test_set_transform_has_one_parameter_per_transform_property_with_the_models_bounds():
    params = {param.name: param for param in VERBS["set_transform"].params}
    for name in TRANSFORM_PROPERTY_NAMES:
        assert name in params and not params[name].required
        spec = TRANSFORM_PROPERTIES[name]
        if params[name].kind.value == "number":
            assert (params[name].minimum, params[name].maximum) == (spec.minimum, spec.maximum), name


# --- Nominal et parité -------------------------------------------------------------------------------------------------


def test_set_transform_matches_the_inspector():
    project = _parity(
        _project, _op("set_transform", clip="fond", position_x=0.25, position_y=-0.5, scale=1.2, rotation=-30.0, opacity=0.8),
        lambda p: _inspector(p, "fond", position_x=0.25, position_y=-0.5, scale=1.2, rotation=-30.0, opacity=0.8),
    )
    transform = find_clip(project, "fond").transform
    assert (transform.position_x, transform.scale, transform.opacity) == (0.25, 1.2, 0.8)
    _parity(_project, _op("set_transform", clip="fond", anchor_x=0.0, anchor_y=1.0, scale_x=2.0, scale_y=0.5, flip_h=True, flip_v=True,
                          fill=True, pan_x=-1.0, pan_y=1.0),
            lambda p: _inspector(p, "fond", anchor_x=0.0, anchor_y=1.0, scale_x=2.0, scale_y=0.5, flip_h=True, flip_v=True,
                                 fill=True, pan_x=-1.0, pan_y=1.0))


def test_set_transform_keeps_the_other_fields_and_the_keyframes():
    def animated() -> Project:
        project = _project()
        set_transform_keyframe(project, "c", "position_x", 0.0, 0.0)
        set_transform_keyframe(project, "c", "position_x", 1.0, 1.0)
        return project

    project = _parity(animated, _op("set_transform", clip="c", opacity=0.5), lambda p: _inspector(p, "c", opacity=0.5))
    clip = find_clip(project, "c")
    assert (clip.transform.scale, clip.transform.rotation, clip.transform.opacity) == (0.5, 15.0, 0.5)     # un clip tourné, réduit : il garde tout
    assert [k.property_name for k in clip.transform_keyframes] == ["position_x", "position_x"]


def test_set_transform_on_a_rotated_layer_and_a_full_frame_background():
    project = _parity(_project, _op("set_transform", clip="t1", rotation=45.0, skew=10.0, scale=1.0),
                      lambda p: _inspector(p, "t1", rotation=45.0, skew=10.0, scale=1.0))
    assert find_clip(project, "t1").transform.skew == 10.0
    _parity(_project, _op("set_transform", clip="fond", scale=1.0, position_x=0.0), lambda p: _inspector(p, "fond", scale=1.0, position_x=0.0))   # déjà aligné


def test_set_transform_at_the_exact_bounds():
    for name in ("position_x", "scale", "rotation", "opacity", "anchor_x", "scale_x", "pan_x"):
        spec = TRANSFORM_PROPERTIES[name]
        for value in (spec.minimum, spec.maximum):
            _session, report = _apply(_project(), _op("set_transform", clip="fond", **{name: value}))
            assert report.ok, (name, value, report.error)


def test_reset_transform_matches_the_inspector():
    def busy() -> Project:
        project = _project()
        set_transform_keyframe(project, "c", "position_x", 0.5, 1.0)
        return project

    project = _parity(busy, _op("reset_transform", clip="c"), lambda p: reset_clip_transform(p, "c"))
    clip = find_clip(project, "c")
    assert clip.transform == ClipTransform() and clip.transform_keyframes == []
    _parity(_project, _op("reset_transform", clip="t1"), lambda p: reset_clip_transform(p, "t1"))


# --- Refus -------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("field,value", [
    ("scale", 0.04), ("scale", 10.1), ("opacity", 1.01), ("opacity", -0.01), ("position_x", 4.01), ("rotation", 3601.0), ("skew", 86.0),
    ("anchor_y", 5.1), ("scale_x", -0.1), ("pan_y", 1.01), ("scale", math.nan), ("rotation", math.inf), ("opacity", True), ("scale", "2"),
    ("flip_h", 1), ("fill", "oui"),
])
def test_out_of_bound_or_ill_typed_values_are_refused_at_reading(field, value):
    _invalid(_op("set_transform", clip="fond", **{field: value}))


def test_set_transform_needs_a_field_and_refuses_unknown_ones():
    _refused(_project(), _op("set_transform", clip="fond"))
    _invalid(_op("set_transform", clip="fond", positon_x=0.5))
    _invalid(_op("set_transform", clip="fond", scale=1.0, rotation=1.0, extra=3))


def test_an_animated_property_is_refused_but_the_others_are_not():
    def animated() -> Project:
        project = _project()
        set_transform_keyframe(project, "c", "opacity", 0.0, 1.0)
        set_transform_keyframe(project, "c", "opacity", 1.0, 0.0)
        return project

    report = _refused(animated(), _op("set_transform", clip="c", opacity=0.5))
    assert "animé" in str(report.error)
    _refused(animated(), _op("set_transform", clip="c", scale=2.0, opacity=0.5))          # refus atomique : `scale` n'est pas écrit non plus
    _parity(animated, _op("set_transform", clip="c", scale=2.0), lambda p: _inspector(p, "c", scale=2.0))


def test_properties_that_do_not_exist_for_a_kind_of_track_are_refused():
    _refused(_project(), _op("set_transform", clip="fond", skew=5.0))          # inclinaison : graphiques seulement
    for field in ("fill", "pan_x", "pan_y"):
        value = True if field == "fill" else 0.5
        _refused(_project(), _op("set_transform", clip="t1", **{field: value}))   # cadrage : vidéo seulement


def test_locks_and_kinds_of_track_are_refused_for_both_verbs():
    for operation in (_op("set_transform", clip="CLIP", scale=1.5), _op("reset_transform", clip="CLIP")):
        def aimed(clip: str, operation=operation) -> dict:
            return {key: (clip if value == "CLIP" else value) for key, value in operation.items()}

        assert "verrouill" in str(_refused(_project(locked_v1=True), aimed("fond")).error)
        assert "verrouill" in str(_refused(_project(), aimed("d")).error)                                  # piste V3 verrouillée
        assert "verrouill" in str(_refused(_project(locked_layer=True), aimed("t1")).error)                 # calque verrouillé sur une piste libre
        _refused(_project(), aimed("sound"))                                                               # piste audio
        _refused(_project(), aimed("nope"))
    _session, report = _apply(_project(), _op("set_transform", clip="t1", scale=1.5))                       # le même calque, déverrouillé
    assert report.ok, report.error


def test_atomic_undoable_and_end_to_end_with_aliases():
    session, report = _apply(
        _project(),
        _op("duplicate_clip", clip="fond", **{"as": "copie"}),
        _op("set_transform", clip={"ref": "copie"}, scale=0.5, position_x=0.3),
        _op("set_transform", clip="t1", rotation=10.0),
    )
    assert report.ok, report.error
    assert find_clip(session.project, "copie").transform.scale == 0.5 and find_clip(session.project, "t1").transform.rotation == 10.0
    assert session.undo() and find_clip(session.project, "t1").transform.rotation == 0.0
    with pytest.raises(KeyError):
        find_clip(session.project, "copie")
    _refused(_project(), _op("set_transform", clip="fond", scale=2.0), _op("set_transform", clip="d", scale=2.0))      # la 2e échoue : la 1re est annulée


def test_hostile_inputs_are_bounded_at_parse():
    with pytest.raises(ScriptInvalid) as caught:
        parse_script({"kut_script": 1, "ops": [_op("set_transform", clip="x" * 5_000_000, scale=1.0)]})
    assert len(str(caught.value)) < 10_000
    _invalid(_op("set_transform", clip="fond", position_x=float("nan")))
