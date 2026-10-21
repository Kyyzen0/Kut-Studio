"""Scripts, images clés (lot 8 de l'extension 0.8.0) : images clés d'effet, et édition d'une image clé (valeur, instant, bloc, interpolation, pentes,
animation d'une propriété).

Chaque verbe appelle la fonction de `core.keyframe_editing` que l'interface appelle : la parité se teste contre l'appel direct sur une copie. Les refus
(bornes que le modèle ramènerait, déplacement qui sortirait du clip ou remplacerait une image clé, pente que le rendu ignorerait, verrous, genres de
piste, suivi) laissent le projet strictement inchangé.
"""

from __future__ import annotations

import copy
import math
from dataclasses import replace

import pytest

from core.animation import InterpolationType, Keyframe, TangentMode
from core.effects_model import EffectType, add_effect_to_clip
from core.keyframe_editing import (
    KeyframeRef,
    add_keyframe,
    clear_effect_animation,
    keyframe_by_id,
    move_keyframes,
    remove_keyframe_at,
    set_animation_enabled,
    set_interpolation,
    set_keyframe_time,
    set_keyframe_values,
    set_tangents,
)
from core.project_model import Clip, MediaAsset, Project, Track
from core.scripting import ScriptInvalid, Session, apply_script, parse_script
from core.scripting.schema import json_schema
from core.scripting.verbs import VERBS
from core.timeline_operations import find_clip
from core.tracking_model import ClipTracking, TrackLink, TrackTarget
from ui import i18n

KEYFRAME_VERBS = (
    "add_effect_keyframe",
    "remove_effect_keyframe",
    "clear_effect_animation",
    "set_keyframe_value",
    "set_keyframe_time",
    "move_keyframes",
    "set_keyframe_interpolation",
    "set_keyframe_tangents",
    "set_animation_enabled",
)


@pytest.fixture(autouse=True)
def _french(monkeypatch):
    monkeypatch.setenv("KUT_STUDIO_LANG", "fr")
    i18n.reset_for_tests()
    yield
    i18n.reset_for_tests()


def _project(*, locked_v1: bool = False) -> Project:
    project = Project(
        "Démo",
        width=320,
        height=180,
        fps=30.0,
        media_assets=[
            MediaAsset(
                "m",
                "/x/intro.mp4",
                "intro",
                20.0,
                320,
                180,
                30.0,
                "video",
                has_audio=True,
            ),
            MediaAsset(
                "s", "/x/son.wav", "son", 20.0, 0, 0, 0.0, "audio", has_audio=True
            ),
        ],
        tracks=[
            Track("V1", "V1", "video", clips=[Clip("a", "m", "V1", 0.0, 0.0, 10.0)]),
            Track(
                "V3",
                "V3",
                "video",
                locked=True,
                clips=[Clip("d", "m", "V3", 0.0, 0.0, 4.0)],
            ),
            Track("A1", "A1", "audio", clips=[Clip("sound", "s", "A1", 0.0, 0.0, 4.0)]),
            Track("G1", "G1", "graphics"),
        ],
    )
    add_effect_to_clip(
        project, "a", EffectType.VIGNETTE, effect_id="v1", params={"intensity": 0.5}
    )
    add_effect_to_clip(project, "a", EffectType.COLOR_CORRECTION, effect_id="cc")
    add_effect_to_clip(
        project, "a", EffectType.BLUR, effect_id="bl", params={"intensity": 3.0}
    )
    project.tracks[0].locked = locked_v1
    return project


def _op(verb: str, /, **fields) -> dict:
    return {"op": verb, **fields}


def _apply(project: Project, *ops: dict):
    session = Session(project)
    return session, apply_script(
        session, parse_script({"kut_script": 1, "ops": list(ops)})
    )


def _snapshot(project: Project):
    return copy.deepcopy(project.tracks)


def _same(first: Project, second: Project) -> bool:
    """Les clips sont égaux (les identifiants d'image clé, tirés au hasard, ne comptent pas : `Keyframe.id` n'est pas comparé)."""
    return _snapshot(first) == _snapshot(second)


def _parity(factory, op: dict, direct) -> Project:
    scripted = factory()
    session, report = _apply(scripted, op)
    assert report.ok, report.error
    reference = factory()
    direct(reference)
    assert _same(session.project, reference)
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


def _animated() -> Project:
    """Le clip `a` (10 s) : vignette v1 animée (0 s, 2 s, 4 s), position_x animée (0 s, 3 s), correction de couleur `cc` animée (1 s)."""
    project = _project()
    for moment, value in ((0.0, 0.1), (2.0, 0.5), (4.0, 0.9)):
        add_keyframe(
            project,
            "a",
            "fx.v1.intensity",
            moment,
            value,
            interpolation=InterpolationType.BEZIER,
        )
    for moment, value in ((0.0, -0.5), (3.0, 0.5)):
        add_keyframe(project, "a", "position_x", moment, value)
    add_keyframe(project, "a", "fx.cc.brightness", 1.0, 0.2)
    return project


def _ref(project: Project, prop: str, moment: float) -> KeyframeRef:
    clip = find_clip(project, "a")
    from core.animation_targets import get_target

    found = get_target(prop, clip).curve(clip).keyframe_at(moment)
    assert found is not None
    return KeyframeRef("a", prop, found.id)


def test_the_verbs_are_in_the_table_with_the_right_shape():
    assert set(KEYFRAME_VERBS) <= set(VERBS)
    for name in KEYFRAME_VERBS:
        assert (
            VERBS[name].creates is None
            and VERBS[name].doc
            and VERBS[name].group == "animation"
        )
    titles = {
        item["title"] for item in json_schema()["properties"]["ops"]["items"]["oneOf"]
    }
    assert set(KEYFRAME_VERBS) <= titles


# --- Images clés d'effet -------------------------------------------------------------------------------------------------


def test_add_effect_keyframe_matches_the_model():
    project = _parity(
        _project,
        _op(
            "add_effect_keyframe",
            clip="a",
            effect="v1",
            parameter="intensity",
            at=1.0,
            value=0.8,
            interpolation="ease_in_out",
        ),
        lambda p: add_keyframe(
            p, "a", "fx.v1.intensity", 1.0, 0.8, interpolation="ease_in_out"
        ),
    )
    assert [k.property_name for k in find_clip(project, "a").animation] == [
        "fx.v1.intensity"
    ]
    _parity(
        _project,
        _op(
            "add_effect_keyframe",
            clip="a",
            effect="cc",
            parameter="contrast",
            at=0.0,
            value=1.5,
        ),
        lambda p: add_keyframe(p, "a", "fx.cc.contrast", 0.0, 1.5),
    )


def test_add_effect_keyframe_replaces_the_keyframe_at_the_same_instant_and_keeps_its_id():
    session, report = _apply(
        _animated(),
        _op(
            "add_effect_keyframe",
            clip="a",
            effect="v1",
            parameter="intensity",
            at=2.0,
            value=0.3,
        ),
    )
    assert report.ok, report.error
    frames = [
        k
        for k in find_clip(session.project, "a").animation
        if k.property_name == "fx.v1.intensity"
    ]
    assert [(k.time_seconds, k.value) for k in frames] == [
        (0.0, 0.1),
        (2.0, 0.3),
        (4.0, 0.9),
    ]


def test_add_effect_keyframe_refuses_what_the_model_would_clamp_or_cannot_animate():
    for value in (1.0001, -0.1):  # la vignette va de 0 à 1
        report = _refused(
            _project(),
            _op(
                "add_effect_keyframe",
                clip="a",
                effect="v1",
                parameter="intensity",
                at=0,
                value=value,
            ),
        )
        assert "hors bornes" in report.error
    sharpened = _project()
    add_effect_to_clip(sharpened, "a", EffectType.SHARPEN, effect_id="sh")
    report = _refused(
        sharpened,
        _op(
            "add_effect_keyframe",
            clip="a",
            effect="sh",
            parameter="intensity",
            at=0,
            value=1.0,
        ),
    )
    assert "pas animable" in report.error  # la netteté (``unsharp``) reste statique
    glowing = _project()
    add_effect_to_clip(glowing, "a", EffectType.GLOW, effect_id="gl")
    # Lueur : seul le rayon est animable. Le gain est refusé par le modèle ; le seuil n'est pas un nom que le verbe connaît.
    assert (
        "pas animable"
        in _refused(
            glowing,
            _op(
                "add_effect_keyframe",
                clip="a",
                effect="gl",
                parameter="intensity",
                at=0,
                value=0.5,
            ),
        ).error
    )
    _invalid(
        _op(
            "add_effect_keyframe",
            clip="a",
            effect="gl",
            parameter="threshold",
            at=0,
            value=0.5,
        )
    )
    report = _refused(
        _project(),
        _op(
            "add_effect_keyframe",
            clip="a",
            effect="bl",
            parameter="intensity",
            at=0,
            value=51.0,
        ),
    )
    assert "hors bornes" in report.error  # le flou va de 0 à 50
    report = _refused(
        _project(),
        _op(
            "add_effect_keyframe",
            clip="a",
            effect="v1",
            parameter="brightness",
            at=0,
            value=0.1,
        ),
    )
    assert "pas animable" in report.error
    _refused(
        _project(),
        _op(
            "add_effect_keyframe",
            clip="a",
            effect="absent",
            parameter="intensity",
            at=0,
            value=0.1,
        ),
    )
    report = _refused(
        _project(),
        _op(
            "add_effect_keyframe",
            clip="a",
            effect="v1",
            parameter="intensity",
            at=10.5,
            value=0.1,
        ),
    )
    assert "hors du clip" in report.error
    _invalid(
        _op(
            "add_effect_keyframe",
            clip="a",
            effect="v1",
            parameter="threshold",
            at=0,
            value=0.1,
        )
    )
    for bad in (math.nan, math.inf, True, "0.5"):
        _invalid(
            _op(
                "add_effect_keyframe",
                clip="a",
                effect="v1",
                parameter="intensity",
                at=0,
                value=bad,
            )
        )
    for bad in (-1.0, math.nan, 1e12):
        _invalid(
            _op(
                "add_effect_keyframe",
                clip="a",
                effect="v1",
                parameter="intensity",
                at=bad,
                value=0.5,
            )
        )


def test_remove_effect_keyframe_matches_the_model_and_keeps_the_displayed_value():
    project = _parity(
        _animated,
        _op(
            "remove_effect_keyframe",
            clip="a",
            effect="v1",
            parameter="intensity",
            at=2.0,
        ),
        lambda p: remove_keyframe_at(p, "a", "fx.v1.intensity", 2.0),
    )
    assert (
        len(
            [
                k
                for k in find_clip(project, "a").animation
                if k.property_name == "fx.v1.intensity"
            ]
        )
        == 2
    )
    only = _project()
    add_keyframe(only, "a", "fx.v1.intensity", 1.0, 0.8)
    _parity(
        lambda: copy.deepcopy(only),
        _op(
            "remove_effect_keyframe",
            clip="a",
            effect="v1",
            parameter="intensity",
            at=1.0,
        ),
        lambda p: remove_keyframe_at(p, "a", "fx.v1.intensity", 1.0),
    )
    assert (
        find_clip(
            _apply(
                copy.deepcopy(only),
                _op(
                    "remove_effect_keyframe",
                    clip="a",
                    effect="v1",
                    parameter="intensity",
                    at=1.0,
                ),
            )[0].project,
            "a",
        )
        .effects[0]
        .params["intensity"]
        == 0.8
    )  # le paramètre garde la valeur qu'il montrait
    report = _refused(
        _animated(),
        _op(
            "remove_effect_keyframe",
            clip="a",
            effect="v1",
            parameter="intensity",
            at=1.0,
        ),
    )
    assert "Aucune image clé" in report.error


def test_clear_effect_animation_matches_the_model():
    project = _parity(
        _animated,
        _op("clear_effect_animation", clip="a", effect="v1", at=2.0),
        lambda p: clear_effect_animation(p, "a", "v1", 2.0),
    )
    assert not [
        k
        for k in find_clip(project, "a").animation
        if k.property_name.startswith("fx.v1.")
    ]
    assert [
        k.property_name
        for k in find_clip(project, "a").animation
        if k.property_name.startswith("fx.cc.")
    ] == ["fx.cc.brightness"]
    _parity(
        _project,
        _op("clear_effect_animation", clip="a", effect="bl", at=0.0),
        lambda p: clear_effect_animation(p, "a", "bl", 0.0),
    )  # rien à retirer
    assert (
        "hors du clip"
        in _refused(
            _animated(), _op("clear_effect_animation", clip="a", effect="v1", at=11.0)
        ).error
    )


# --- Édition d'une image clé -----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("prop", "at", "value"),
    [
        ("fx.v1.intensity", 2.0, 0.7),
        ("position_x", 3.0, 0.25),
        ("fx.cc.brightness", 1.0, -0.3),
    ],
)
def test_set_keyframe_value_matches_the_model(prop, at, value):
    def direct(project: Project) -> None:
        set_keyframe_values(project, {_ref(project, prop, at): value})

    _parity(
        _animated,
        _op("set_keyframe_value", clip="a", property=prop, at=at, value=value),
        direct,
    )


def test_require_value_refuses_non_numbers_even_when_called_directly():
    """Le décodage de script les refuse déjà ; la règle commune ne s'y fie pas."""
    from core.animation_targets import get_target
    from core.scripting.keyframe_rules import require_value

    target = get_target("position_x")
    for bad in (math.nan, math.inf, -math.inf, True, "1", None):
        with pytest.raises(ValueError):
            require_value(target, bad, "position_x")
    assert require_value(target, 1, "position_x") == 1.0


def test_set_keyframe_value_refuses_bounds_missing_keyframes_and_unsuitable_properties():
    report = _refused(
        _animated(),
        _op(
            "set_keyframe_value",
            clip="a",
            property="fx.v1.intensity",
            at=2.0,
            value=1.5,
        ),
    )
    assert "hors bornes" in report.error
    report = _refused(
        _animated(),
        _op("set_keyframe_value", clip="a", property="opacity", at=1.0, value=2.0),
    )
    assert "Aucune image clé" in report.error or "hors bornes" in report.error
    assert (
        "Aucune image clé"
        in _refused(
            _animated(),
            _op(
                "set_keyframe_value",
                clip="a",
                property="fx.v1.intensity",
                at=1.0,
                value=0.5,
            ),
        ).error
    )
    assert (
        "vitesse"
        in _refused(
            _animated(),
            _op(
                "set_keyframe_value", clip="a", property="time.speed", at=0.0, value=1.0
            ),
        ).error
    )
    assert (
        "n'est pas un nombre"
        in _refused(
            _animated(),
            _op("set_keyframe_value", clip="a", property="flip_h", at=0.0, value=1.0),
        ).error
    )
    sharpened = _animated()
    add_effect_to_clip(sharpened, "a", EffectType.SHARPEN, effect_id="sh")
    assert (
        "propriété animable"
        in _refused(
            sharpened,
            _op(
                "set_keyframe_value",
                clip="a",
                property="fx.sh.intensity",
                at=0.0,
                value=1.0,
            ),
        ).error
    )
    assert (
        "Aucune image clé"
        in _refused(
            _animated(),
            _op(
                "set_keyframe_value",
                clip="a",
                property="fx.bl.intensity",
                at=0.0,
                value=1.0,
            ),
        ).error
    )
    assert (
        "propriété animable"
        in _refused(
            _animated(),
            _op(
                "set_keyframe_value",
                clip="a",
                property="nimporte.quoi",
                at=0.0,
                value=1.0,
            ),
        ).error
    )
    for bad in (math.nan, math.inf, True, "1"):
        _invalid(
            _op("set_keyframe_value", clip="a", property="position_x", at=0, value=bad)
        )


def test_set_keyframe_time_matches_the_model_without_snapping_to_a_frame():
    def direct(project: Project) -> None:
        set_keyframe_time(project, _ref(project, "position_x", 3.0), 3.0123)

    project = _parity(
        _animated,
        _op("set_keyframe_time", clip="a", property="position_x", at=3.0, to=3.0123),
        direct,
    )
    assert [k.time_seconds for k in find_clip(project, "a").transform_keyframes] == [
        0.0,
        3.0123,
    ]  # l'interface aurait aimanté sur une image : pas un script


def test_set_keyframe_time_refuses_leaving_the_clip_staying_put_and_replacing_a_keyframe():
    assert (
        "hors du clip"
        in _refused(
            _animated(),
            _op("set_keyframe_time", clip="a", property="position_x", at=3.0, to=10.5),
        ).error
    )
    assert (
        "déjà à cet instant"
        in _refused(
            _animated(),
            _op("set_keyframe_time", clip="a", property="position_x", at=3.0, to=3.0),
        ).error
    )
    report = _refused(
        _animated(),
        _op("set_keyframe_time", clip="a", property="fx.v1.intensity", at=4.0, to=2.0),
    )  # le modèle remplacerait celle de 2 s
    assert "remplacerait" in report.error
    _refused(
        _animated(),
        _op("set_keyframe_time", clip="a", property="position_x", at=1.0, to=2.0),
    )  # aucune image clé à 1 s
    _invalid(_op("set_keyframe_time", clip="a", property="position_x", at=3.0, to=-1.0))


def test_move_keyframes_matches_the_model_for_a_block_across_properties():
    def direct(project: Project) -> None:
        refs = [
            _ref(project, "fx.v1.intensity", 2.0),
            _ref(project, "fx.v1.intensity", 4.0),
            _ref(project, "position_x", 3.0),
        ]
        move_keyframes(project, refs, 1.5, fps=None)

    project = _parity(
        _animated,
        _op(
            "move_keyframes",
            clip="a",
            delta=1.5,
            keyframes=[
                {"property": "fx.v1.intensity", "at": 2.0},
                {"property": "fx.v1.intensity", "at": 4.0},
                {"property": "position_x", "at": 3.0},
            ],
        ),
        direct,
    )
    assert [
        k.time_seconds
        for k in find_clip(project, "a").animation
        if k.property_name == "fx.v1.intensity"
    ] == [0.0, 3.5, 5.5]


def test_move_keyframes_refuses_what_the_model_would_clamp_or_overwrite():
    block = [
        {"property": "fx.v1.intensity", "at": 2.0},
        {"property": "fx.v1.intensity", "at": 4.0},
    ]
    assert (
        "hors du clip"
        in _refused(
            _animated(), _op("move_keyframes", clip="a", delta=6.5, keyframes=block)
        ).error
    )  # le modèle réduirait le décalage
    assert (
        "hors du clip"
        in _refused(
            _animated(), _op("move_keyframes", clip="a", delta=-2.5, keyframes=block)
        ).error
    )
    assert (
        "remplacerait"
        in _refused(
            _animated(),
            _op(
                "move_keyframes",
                clip="a",
                delta=2.0,
                keyframes=[{"property": "fx.v1.intensity", "at": 2.0}],
            ),
        ).error
    )
    assert (
        "citée deux fois"
        in _refused(
            _animated(),
            _op("move_keyframes", clip="a", delta=1.0, keyframes=block + block[:1]),
        ).error
    )
    assert (
        "nul"
        in _refused(
            _animated(), _op("move_keyframes", clip="a", delta=0.0, keyframes=block)
        ).error
    )
    _refused(
        _animated(),
        _op(
            "move_keyframes",
            clip="a",
            delta=1.0,
            keyframes=[{"property": "position_x", "at": 1.0}],
        ),
    )
    _refused(
        _animated(),
        _op(
            "move_keyframes",
            clip="a",
            delta=1.0,
            keyframes=[
                {"property": "fx.v1.intensity", "at": 2.0},
                {"property": "flip_h", "at": 0},
            ],
        ),
    )


def test_move_keyframes_bounds_the_block_at_parse():
    many = [{"property": "position_x", "at": float(index)} for index in range(65)]
    _invalid(_op("move_keyframes", clip="a", delta=1.0, keyframes=many))
    _invalid(_op("move_keyframes", clip="a", delta=1.0, keyframes=[]))
    _invalid(_op("move_keyframes", clip="a", delta=math.nan, keyframes=many[:1]))
    _invalid(
        _op(
            "move_keyframes",
            clip="a",
            delta=1.0,
            keyframes=[{"property": "position_x"}],
        )
    )


@pytest.mark.parametrize("interpolation", [item.value for item in InterpolationType])
def test_set_keyframe_interpolation_matches_the_model(interpolation):
    def direct(project: Project) -> None:
        set_interpolation(
            project, [_ref(project, "fx.v1.intensity", 2.0)], interpolation
        )

    _parity(
        _animated,
        _op(
            "set_keyframe_interpolation",
            clip="a",
            property="fx.v1.intensity",
            at=2.0,
            interpolation=interpolation,
        ),
        direct,
    )


def test_set_keyframe_interpolation_errors():
    _refused(
        _animated(),
        _op(
            "set_keyframe_interpolation",
            clip="a",
            property="fx.v1.intensity",
            at=1.0,
            interpolation="linear",
        ),
    )
    _invalid(
        _op(
            "set_keyframe_interpolation",
            clip="a",
            property="position_x",
            at=0.0,
            interpolation="cubique",
        )
    )


def test_set_keyframe_tangents_matches_the_model():
    def slopes(project: Project) -> None:
        set_tangents(
            project,
            _ref(project, "fx.v1.intensity", 2.0),
            in_slope=0.1,
            out_slope=0.4,
            mode=TangentMode.BROKEN,
        )

    project = _parity(
        _animated,
        _op(
            "set_keyframe_tangents",
            clip="a",
            property="fx.v1.intensity",
            at=2.0,
            in_slope=0.1,
            out_slope=0.4,
            mode="broken",
        ),
        slopes,
    )
    middle = next(
        k
        for k in find_clip(project, "a").animation
        if k.property_name == "fx.v1.intensity" and k.time_seconds == 2.0
    )
    assert (middle.in_slope, middle.out_slope, middle.tangent_mode) == (
        0.1,
        0.4,
        TangentMode.BROKEN,
    )
    _parity(
        _animated,
        _op(
            "set_keyframe_tangents",
            clip="a",
            property="fx.v1.intensity",
            at=2.0,
            out_slope=0.3,
        ),
        lambda p: set_tangents(p, _ref(p, "fx.v1.intensity", 2.0), out_slope=0.3),
    )  # lié : la pente entrante suit
    _parity(
        _animated,
        _op(
            "set_keyframe_tangents",
            clip="a",
            property="fx.v1.intensity",
            at=2.0,
            mode="broken",
        ),
        lambda p: set_tangents(p, _ref(p, "fx.v1.intensity", 2.0), mode="broken"),
    )
    reset = _animated()
    set_tangents(reset, _ref(reset, "fx.v1.intensity", 2.0), out_slope=0.3)
    _parity(
        lambda: copy.deepcopy(reset),
        _op(
            "set_keyframe_tangents",
            clip="a",
            property="fx.v1.intensity",
            at=2.0,
            auto=True,
        ),
        lambda p: set_tangents(p, _ref(p, "fx.v1.intensity", 2.0), auto=True),
    )


def test_set_keyframe_tangents_refuses_what_the_editor_does_not_offer_or_the_model_would_drop():
    base = {"clip": "a", "property": "fx.v1.intensity", "at": 2.0}
    assert (
        "au moins" in _refused(_animated(), _op("set_keyframe_tangents", **base)).error
    )
    assert (
        "exclut"
        in _refused(
            _animated(), _op("set_keyframe_tangents", **base, auto=True, out_slope=1.0)
        ).error
    )
    assert (
        "égales"
        in _refused(
            _animated(),
            _op("set_keyframe_tangents", **base, in_slope=1.0, out_slope=2.0),
        ).error
    )  # lié : une seule pente survivrait
    _apply(
        _animated(), _op("set_keyframe_tangents", **base, in_slope=1.0, out_slope=1.0)
    )  # égales : permis
    last = {"clip": "a", "property": "fx.v1.intensity", "at": 4.0}
    assert (
        "suivie"
        in _refused(
            _animated(),
            _op("set_keyframe_tangents", **last, out_slope=1.0, mode="broken"),
        ).error
    )  # rien après : le rendu l'ignorerait
    assert (
        "précédente"
        in _refused(
            _animated(),
            _op(
                "set_keyframe_tangents",
                clip="a",
                property="fx.v1.intensity",
                at=0.0,
                in_slope=1.0,
                mode="broken",
            ),
        ).error
    )
    _apply(
        _animated(), _op("set_keyframe_tangents", **last, out_slope=1.0)
    )  # lié : la pente entrante, elle, compte
    straight = _animated()
    set_interpolation(straight, [_ref(straight, "fx.v1.intensity", 2.0)], "linear")
    assert (
        "bezier"
        in _refused(
            straight, _op("set_keyframe_tangents", **base, out_slope=1.0, mode="broken")
        ).error
    )
    only_linear = copy.deepcopy(straight)
    set_interpolation(
        only_linear, [_ref(only_linear, "fx.v1.intensity", 0.0)], "linear"
    )
    set_interpolation(
        only_linear, [_ref(only_linear, "fx.v1.intensity", 2.0)], "linear"
    )
    assert (
        "aucune pente"
        in _refused(
            only_linear, _op("set_keyframe_tangents", **base, out_slope=1.0)
        ).error
    )
    for bad in (math.nan, math.inf, 1e7, -1e7, True):
        _invalid(_op("set_keyframe_tangents", **base, out_slope=bad))
    _invalid(_op("set_keyframe_tangents", **base, mode="smooth"))


def test_set_animation_enabled_matches_the_model_both_ways():
    project = _parity(
        _project,
        _op("set_animation_enabled", clip="a", property="scale", enabled=True, at=1.0),
        lambda p: set_animation_enabled(p, "a", "scale", True, 1.0),
    )
    assert [k.property_name for k in find_clip(project, "a").transform_keyframes] == [
        "scale"
    ]
    project = _parity(
        _animated,
        _op(
            "set_animation_enabled",
            clip="a",
            property="fx.v1.intensity",
            enabled=False,
            at=3.0,
        ),
        lambda p: set_animation_enabled(p, "a", "fx.v1.intensity", False, 3.0),
    )
    assert not [
        k
        for k in find_clip(project, "a").animation
        if k.property_name == "fx.v1.intensity"
    ]
    _parity(
        _project,
        _op(
            "set_animation_enabled",
            clip="a",
            property="fx.v1.intensity",
            enabled=True,
            at=0.0,
        ),
        lambda p: set_animation_enabled(p, "a", "fx.v1.intensity", True, 0.0),
    )
    assert (
        "hors du clip"
        in _refused(
            _project(),
            _op(
                "set_animation_enabled",
                clip="a",
                property="scale",
                enabled=True,
                at=10.5,
            ),
        ).error
    )
    assert (
        "hors du clip"
        in _refused(
            _animated(),
            _op(
                "set_animation_enabled",
                clip="a",
                property="position_x",
                enabled=False,
                at=10.5,
            ),
        ).error
    )
    assert (
        "vitesse"
        in _refused(
            _project(),
            _op(
                "set_animation_enabled",
                clip="a",
                property="time.speed",
                enabled=True,
                at=0.0,
            ),
        ).error
    )


# --- Masques, suivi, genres de piste -----------------------------------------------------------------------------------------


def _masked() -> Project:
    project = _animated()
    session, report = _apply(
        project,
        _op("add_mask", clip="a", shape="ellipse", id="m1"),
        _op(
            "add_mask_keyframe",
            clip="a",
            mask="m1",
            property="feather",
            at=0.0,
            value=0.1,
        ),
        _op(
            "add_mask_keyframe",
            clip="a",
            mask="m1",
            property="feather",
            at=2.0,
            value=0.2,
        ),
    )
    assert report.ok, report.error
    return session.project


def test_mask_keyframes_are_edited_by_the_generic_verbs():
    def direct(project: Project) -> None:
        set_keyframe_values(project, {_ref(project, "mask.m1.feather", 2.0): 0.3})

    _parity(
        _masked,
        _op(
            "set_keyframe_value",
            clip="a",
            property="mask.m1.feather",
            at=2.0,
            value=0.3,
        ),
        direct,
    )
    _parity(
        _masked,
        _op(
            "set_keyframe_interpolation",
            clip="a",
            property="mask.m1.feather",
            at=0.0,
            interpolation="hold",
        ),
        lambda p: set_interpolation(p, [_ref(p, "mask.m1.feather", 0.0)], "hold"),
    )


def test_the_crop_is_refused():
    session, report = _apply(_masked(), _op("set_crop", clip="a", left=0.1))
    assert report.ok, report.error
    mask_id = next(
        mask.id
        for mask in find_clip(session.project, "a").compositing.masks
        if mask.id != "m1"
    )
    animated = find_clip(session.project, "a")
    animated.animation = [
        *animated.animation,
        Keyframe(f"mask.{mask_id}.feather", 0.0, 0.1, id="kc"),
    ]
    assert (
        "recadrage"
        in _refused(
            session.project,
            _op(
                "set_keyframe_value",
                clip="a",
                property=f"mask.{mask_id}.feather",
                at=0.0,
                value=0.1,
            ),
        ).error
    )
    _refused(
        _masked(),
        _op(
            "set_keyframe_value",
            clip="a",
            property="mask.absent.feather",
            at=0.0,
            value=0.1,
        ),
    )


def test_the_position_of_a_tracked_mask_is_refused_but_its_other_properties_are_not():
    tracked = _masked()
    find_clip(tracked, "a").tracking = ClipTracking(
        links=(TrackLink(id="l1", target=TrackTarget.MASK, mask_id="m1"),)
    )
    inside = find_clip(tracked, "a")
    inside.animation = [
        *inside.animation,
        Keyframe("mask.m1.position_x", 0.0, 0.1, id="kx"),
    ]
    assert (
        "suivi"
        in _refused(
            tracked,
            _op(
                "set_keyframe_value",
                clip="a",
                property="mask.m1.position_x",
                at=0.0,
                value=0.1,
            ),
        ).error
    )
    assert (
        "suivi"
        in _refused(
            tracked,
            _op(
                "move_keyframes",
                clip="a",
                delta=1.0,
                keyframes=[{"property": "mask.m1.position_x", "at": 0.0}],
            ),
        ).error
    )
    assert (
        "suivi"
        in _refused(
            tracked,
            _op(
                "set_animation_enabled",
                clip="a",
                property="mask.m1.position_x",
                enabled=False,
                at=0.0,
            ),
        ).error
    )
    _session, report = _apply(
        tracked,
        _op(
            "set_keyframe_value",
            clip="a",
            property="mask.m1.feather",
            at=2.0,
            value=0.25,
        ),
    )
    assert report.ok, report.error
    link = TrackLink(
        id="l1", target=TrackTarget.MASK, mask_id="m1", enabled=False
    )  # liaison désactivée : le rendu la saute, rien n'écrase
    find_clip(tracked, "a").tracking = ClipTracking(links=(link,))
    _session, report = _apply(
        tracked,
        _op(
            "set_keyframe_value",
            clip="a",
            property="mask.m1.position_x",
            at=0.0,
            value=0.1,
        ),
    )
    assert report.ok, report.error


def test_a_color_window_keyframe_is_refused():
    project = _animated()
    session, report = _apply(
        project, _op("add_color_window", clip="a", shape="ellipse", id="w1")
    )
    assert report.ok, report.error
    _refused(
        session.project,
        _op(
            "set_keyframe_value",
            clip="a",
            property="mask.w1.feather",
            at=0.0,
            value=0.1,
        ),
    )


def test_other_kinds_of_clip_are_refused():
    project = _project()
    session, report = _apply(
        project,
        _op(
            "add_graphic",
            graphic="text",
            at=0,
            duration=3,
            id="t1",
            text="A",
            track="G1",
        ),
        _op("add_keyframe", clip="t1", property="opacity", at=0.0, value=0.5),
    )
    assert report.ok, report.error
    _refused(
        session.project,
        _op(
            "add_effect_keyframe",
            clip="t1",
            effect="v1",
            parameter="intensity",
            at=0,
            value=0.5,
        ),
    )
    _refused(
        session.project,
        _op(
            "add_effect_keyframe",
            clip="sound",
            effect="v1",
            parameter="intensity",
            at=0,
            value=0.5,
        ),
    )
    _refused(
        session.project,
        _op("set_keyframe_value", clip="sound", property="opacity", at=0.0, value=0.5),
    )
    _refused(
        session.project,
        _op("set_keyframe_value", clip="t1", property="skew", at=0.0, value=1.0),
    )  # `skew` n'a pas d'image clé ici
    report = _refused(
        session.project,
        _op(
            "set_animation_enabled",
            clip="sound",
            property="opacity",
            enabled=True,
            at=0.0,
        ),
    )
    assert "ne s'applique pas" in report.error


# --- Verrous ------------------------------------------------------------------------------------------------------------------

_SAMPLES = [
    _op(
        "add_effect_keyframe",
        clip="CLIP",
        effect="v1",
        parameter="intensity",
        at=1.0,
        value=0.4,
    ),
    _op(
        "remove_effect_keyframe",
        clip="CLIP",
        effect="v1",
        parameter="intensity",
        at=2.0,
    ),
    _op("clear_effect_animation", clip="CLIP", effect="v1", at=1.0),
    _op("set_keyframe_value", clip="CLIP", property="position_x", at=3.0, value=0.2),
    _op("set_keyframe_time", clip="CLIP", property="position_x", at=3.0, to=4.0),
    _op(
        "move_keyframes",
        clip="CLIP",
        delta=1.0,
        keyframes=[{"property": "position_x", "at": 3.0}],
    ),
    _op(
        "set_keyframe_interpolation",
        clip="CLIP",
        property="position_x",
        at=3.0,
        interpolation="hold",
    ),
    _op(
        "set_keyframe_tangents",
        clip="CLIP",
        property="fx.v1.intensity",
        at=2.0,
        mode="broken",
    ),
    _op(
        "set_animation_enabled",
        clip="CLIP",
        property="position_x",
        enabled=False,
        at=1.0,
    ),
]


def _with_clip(operation: dict, clip: str) -> dict:
    return {
        key: (clip if value == "CLIP" else value) for key, value in operation.items()
    }


@pytest.mark.parametrize("operation", _SAMPLES, ids=lambda item: item["op"])
def test_a_locked_track_refuses_every_keyframe_verb(operation):
    locked = _animated()
    locked.tracks[0].locked = True
    assert "verrouill" in _refused(locked, _with_clip(operation, "a")).error
    _refused(_animated(), _with_clip(operation, "d"))  # `d` est sur V3, verrouillée


_ON_LAYERS = [
    item
    for item in _SAMPLES
    if item["op"]
    not in (
        "add_effect_keyframe",
        "remove_effect_keyframe",
        "clear_effect_animation",
        "set_keyframe_tangents",
    )
]


@pytest.mark.parametrize("operation", _ON_LAYERS, ids=lambda item: item["op"])
def test_a_locked_layer_refuses_every_keyframe_verb(operation):
    """Les verbes d'effet ne visent que des clips vidéo (un calque est refusé pour ce motif : `test_other_kinds_of_clip_are_refused`)."""
    project = _animated()
    session, report = _apply(
        project,
        _op(
            "add_graphic",
            graphic="text",
            at=0,
            duration=3,
            id="t1",
            text="A",
            track="G1",
        ),
        _op("add_keyframe", clip="t1", property="position_x", at=3.0, value=0.1),
    )
    assert report.ok, report.error
    layer = find_clip(session.project, "t1")
    layer.graphic = replace(layer.graphic, locked=True)
    assert "verrouill" in _refused(session.project, _with_clip(operation, "t1")).error


# --- Atomicité, annulation, de bout en bout ---------------------------------------------------------------------------------


def test_scripts_are_atomic_and_one_undo_step():
    session, report = _apply(
        _animated(),
        _op(
            "set_keyframe_value",
            clip="a",
            property="fx.v1.intensity",
            at=2.0,
            value=0.6,
        ),
        _op(
            "move_keyframes",
            clip="a",
            delta=1.0,
            keyframes=[{"property": "position_x", "at": 3.0}],
        ),
        _op(
            "set_animation_enabled",
            clip="a",
            property="fx.cc.brightness",
            enabled=False,
            at=1.0,
        ),
    )
    assert report.ok, report.error
    assert session.undo()
    clip = find_clip(session.project, "a")
    assert [k.time_seconds for k in clip.transform_keyframes] == [0.0, 3.0]
    assert any(k.property_name == "fx.cc.brightness" for k in clip.animation)
    assert (
        next(
            k.value
            for k in clip.animation
            if k.property_name == "fx.v1.intensity" and k.time_seconds == 2.0
        )
        == 0.5
    )
    _refused(
        _animated(),
        _op("set_keyframe_value", clip="a", property="position_x", at=3.0, value=0.2),
        _op("set_keyframe_time", clip="a", property="position_x", at=3.0, to=99.0),
    )  # la 2e échoue : la 1re est annulée


def test_effect_animation_end_to_end_with_aliases():
    session, report = _apply(
        _project(),
        _op("cut_clip", clip="a", at=4.0, **{"as": "droite"}),
        _op(
            "add_effect_keyframe",
            clip={"ref": "droite"},
            effect="v1",
            parameter="intensity",
            at=0.0,
            value=0.1,
            interpolation="bezier",
        ),
        _op(
            "add_effect_keyframe",
            clip={"ref": "droite"},
            effect="v1",
            parameter="intensity",
            at=2.0,
            value=0.9,
            interpolation="bezier",
        ),
        _op(
            "set_keyframe_tangents",
            clip={"ref": "droite"},
            property="fx.v1.intensity",
            at=0.0,
            mode="broken",
            out_slope=0.1,
        ),
        _op(
            "set_keyframe_time",
            clip={"ref": "droite"},
            property="fx.v1.intensity",
            at=2.0,
            to=3.0,
        ),
        _op(
            "set_keyframe_value",
            clip={"ref": "droite"},
            property="fx.v1.intensity",
            at=3.0,
            value=0.8,
        ),
    )
    assert report.ok, report.error
    frames = [
        k
        for k in find_clip(session.project, "droite").animation
        if k.property_name == "fx.v1.intensity"
    ]
    assert [(k.time_seconds, k.value) for k in frames] == [(0.0, 0.1), (3.0, 0.8)]
    assert session.undo() and not [
        k
        for k in find_clip(session.project, "a").animation
        if k.property_name == "fx.v1.intensity"
    ]


def test_huge_and_hostile_inputs_are_bounded_at_parse():
    with pytest.raises(ScriptInvalid) as caught:
        parse_script(
            {
                "kut_script": 1,
                "ops": [
                    _op(
                        "set_keyframe_value",
                        clip="a",
                        property="x" * 5_000_000,
                        at=0,
                        value=1.0,
                    )
                ],
            }
        )
    assert len(str(caught.value)) < 10_000
    _invalid(
        _op(
            "set_keyframe_value",
            clip="a",
            property="position_x",
            at=0,
            value=1.0,
            extra=1,
        )
    )
    _invalid(_op("set_keyframe_value", clip="a", property="", at=0, value=1.0))
    _invalid(_op("move_keyframes", clip="a", delta=1.0, keyframes="all"))
    _invalid(
        _op(
            "add_effect_keyframe",
            clip="a",
            effect="v1",
            parameter="intensity",
            at=1e12,
            value=0.5,
        )
    )
    report = _refused(
        _animated(),
        _op("set_keyframe_value", clip="a", property="p" * 150, at=0.0, value=1.0),
    )
    assert len(report.error) < 1_000
    assert keyframe_by_id(find_clip(_animated(), "a"), "position_x", "inconnu") is None


# --- Aller-retour avec `describe` --------------------------------------------------------------------------------------------


def test_the_tangents_and_interpolations_of_the_description_are_rewritten_by_the_verbs():
    from core.scripting.describe import describe

    original = _animated()
    set_tangents(
        original,
        _ref(original, "fx.v1.intensity", 2.0),
        in_slope=0.1,
        out_slope=0.4,
        mode=TangentMode.BROKEN,
    )
    set_tangents(original, _ref(original, "fx.v1.intensity", 0.0), out_slope=0.3)
    set_interpolation(original, [_ref(original, "position_x", 0.0)], "ease_out")
    wanted = describe(original)
    rebuilt = copy.deepcopy(original)
    clip = find_clip(rebuilt, "a")
    clip.animation, clip.transform_keyframes = [], []
    ops: list[dict] = []
    data = next(
        c
        for t in wanted["sequences"][0]["tracks"]
        for c in t["clips"]
        if c["id"] == "a"
    )
    for keyframe in data["transform_keyframes"] + data["animation"]:
        name, base = (
            keyframe["property_name"],
            {"clip": "a", "at": keyframe["time_seconds"]},
        )
        value = {"value": keyframe["value"], "interpolation": keyframe["interpolation"]}
        if name.startswith("fx."):
            _, effect, parameter = name.split(".")
            ops.append(
                _op(
                    "add_effect_keyframe",
                    **base,
                    **value,
                    effect=effect,
                    parameter=parameter,
                )
            )
        else:
            ops.append(_op("add_keyframe", **base, **value, property=name))
        slopes = {
            key: keyframe[key]
            for key in ("in_slope", "out_slope")
            if keyframe[key] is not None
        }
        if slopes or keyframe["tangent_mode"] != "linked":
            ops.append(
                _op(
                    "set_keyframe_tangents",
                    **base,
                    property=name,
                    mode=keyframe["tangent_mode"],
                    **slopes,
                )
            )
    ordered = [op for op in ops if op["op"] != "set_keyframe_tangents"] + [
        op for op in ops if op["op"] == "set_keyframe_tangents"
    ]  # les pentes après toutes les images clés
    _session, report = _apply(rebuilt, *ordered)
    assert report.ok, report.error

    def view(project: Project):
        tree = describe(project)
        found = next(
            c
            for t in tree["sequences"][0]["tracks"]
            for c in t["clips"]
            if c["id"] == "a"
        )
        return [
            {key: value for key, value in k.items() if key != "id"}
            for k in found["transform_keyframes"] + found["animation"]
        ]

    assert view(rebuilt) == view(original)


# --- ADR-0008, étape 9 : l'étalonnage animé (`color.*`) est atteignable par les verbes génériques ---------------------------------------
# (le refus de l'étape 6, volontaire tant que les verbes n'existaient pas, est levé ; ses contreparties, cas par cas :
# `tests/test_script_verbs_grade_keyframes.py`)

_COLOR_OPS = [
    _op("add_keyframe", clip="a", property="color.exposure", at=1.0, value=0.5),
    _op("set_keyframe_value", clip="a", property="color.exposure", at=1.0, value=0.2),
    _op("set_keyframe_time", clip="a", property="color.exposure", at=1.0, to=2.0),
    _op(
        "move_keyframes",
        clip="a",
        delta=0.5,
        keyframes=[{"property": "color.exposure", "at": 1.0}],
    ),
    _op(
        "set_keyframe_interpolation",
        clip="a",
        property="color.exposure",
        at=1.0,
        interpolation="hold",
    ),
    _op(
        "set_animation_enabled",
        clip="a",
        property="color.exposure",
        enabled=False,
        at=1.0,
    ),
    _op("remove_keyframe", clip="a", property="color.exposure", at=1.0),
]


@pytest.mark.parametrize("operation", _COLOR_OPS, ids=lambda o: o["op"])
def test_the_generic_verbs_now_reach_a_color_keyframe(operation):
    """Un clip qui porte une clé `color.exposure` à 1 s : chaque verbe générique l'atteint (plus de refus « pas encore disponible »)."""
    project = _project()
    find_clip(project, "a").animation = [Keyframe("color.exposure", 1.0, 0.3, id="kc")]
    _session, report = _apply(project, operation)
    assert report.ok, report.error


def test_the_direct_operation_of_add_and_remove_keyframe_accepts_a_color_property_too():
    project = _project()
    VERBS["add_keyframe"].build(
        {"clip": "a", "property": "color.exposure", "at": 1.0, "value": 0.5}
    )(project)
    assert [k.property_name for k in find_clip(project, "a").animation] == [
        "color.exposure"
    ]
    VERBS["remove_keyframe"].build(
        {"clip": "a", "property": "color.exposure", "at": 1.0}
    )(project)
    assert not find_clip(project, "a").animation


def test_the_grade_support_does_not_touch_other_properties():
    project = _project()
    _session, report = _apply(
        project, _op("add_keyframe", clip="a", property="position_x", at=1.0, value=1.0)
    )
    assert report.ok
