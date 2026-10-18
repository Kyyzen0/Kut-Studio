"""Scripts et effets visuels d'un clip vidéo (ADR-0005 étape 4, lot 8) : `add_effect`, `remove_effect`, `set_effect_enabled`, `update_effect`, `move_effect`.

Les verbes appellent les fonctions de `core.effects_model` que le panneau d'effets de l'interface appelle : la parité est testée contre
l'appel direct. Bornes lues dans le modèle (`EFFECT_PARAMETER_SPECS`), verrous de piste et de calque, genres de piste.
"""

from __future__ import annotations

import random
import string

import pytest

from core.effects_model import (
    EFFECT_PARAMETER_SPECS, EffectType, add_effect_to_clip, move_clip_effect, remove_effect_from_clip, set_clip_effect_enabled,
    update_clip_effect_parameters,
)
from core.graphics import GraphicOverlay, GraphicType
from core.project_model import MediaAsset, Project, Track
from core.scripting import ScriptInvalid, Session, apply_script, created_ids, parse_script
from core.scripting.verbs import VERBS, Kind
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
        "Démo", width=320, height=180, fps=30.0,
        media_assets=[
            MediaAsset("m", "/x/intro.mp4", "intro", 10.0, 320, 180, 30.0, "video", has_audio=True),
            MediaAsset("voix", "/x/voix.wav", "voix", 10.0, 0, 0, 0.0, "audio", has_audio=True),
        ],
        tracks=[Track("V1", "V1", "video"), Track("A1", "A1", "audio"), Track("G1", "G1", "graphics")],
    )


def _script(*ops: dict) -> dict:
    return {"kut_script": 1, "ops": list(ops)}


def _apply(project: Project, *ops: dict):
    session = Session(project)
    return session, apply_script(session, parse_script(_script(*ops)))


CLIP = {"op": "add_clip", "track": "V1", "asset": "m", "at": 0, "id": "a"}
LOCK = {"op": "set_track_locked", "track": "V1", "locked": True}


def _fx(project: Project, clip_id: str = "a") -> list:
    return list(find_clip(project, clip_id).effects)


def _blur(**extra) -> dict:
    return {"op": "add_effect", "clip": "a", "type": "blur", **extra}


# --- Parité avec l'interface -----------------------------------------------------------------------------------------------


def test_the_verbs_do_what_the_effects_panel_does():
    session, report = _apply(
        _project(), CLIP, _blur(id="flou", params={"intensity": 8}), {"op": "add_effect", "clip": "a", "type": "sepia", "id": "sepia1"},
        {"op": "add_effect", "clip": "a", "type": "vignette", "id": "vig", "enabled": False},
        {"op": "update_effect", "clip": "a", "effect": "flou", "params": {"intensity": 12}},
        {"op": "set_effect_enabled", "clip": "a", "effect": "sepia1", "enabled": False},
        {"op": "move_effect", "clip": "a", "effect": "vig", "delta": -2},
        {"op": "remove_effect", "clip": "a", "effect": "sepia1"},
    )
    assert report.ok, report.error
    reference = _project()
    assert apply_script(Session(reference), parse_script(_script(CLIP))).ok
    add_effect_to_clip(reference, "a", EffectType.BLUR, effect_id="flou", params={"intensity": 8})
    add_effect_to_clip(reference, "a", "sepia", effect_id="sepia1")
    add_effect_to_clip(reference, "a", "vignette", effect_id="vig", enabled=False)
    update_clip_effect_parameters(reference, "a", "flou", {"intensity": 12})
    set_clip_effect_enabled(reference, "a", "sepia1", False)
    move_clip_effect(reference, "a", "vig", -2)
    remove_effect_from_clip(reference, "a", "sepia1")
    assert _fx(session.project) == _fx(reference) == [_fx(reference)[0], _fx(reference)[1]]
    assert [item.id for item in _fx(session.project)] == ["vig", "flou"]
    assert session.can_undo, "un script réussi est une étape d'historique"


def test_a_script_replayed_on_a_fresh_project_gives_the_same_effects():
    ops = (CLIP, _blur(id="flou", params={"intensity": 3}), {"op": "add_effect", "clip": "a", "type": "glow", "as": "lueur"},
           {"op": "update_effect", "clip": "a", "effect": {"ref": "lueur"}, "params": {"radius": 20}})
    first, report = _apply(_project(), *ops)
    second, again = _apply(_project(), *ops)
    assert report.ok and again.ok and _fx(first.project) == _fx(second.project)
    assert [item.id for item in _fx(first.project)] == ["flou", "lueur"]
    assert created_ids(parse_script(_script(*ops)), report)[1:] == [{"op": "1:add_effect", "id": "flou"}, {"op": "2:add_effect", "id": "lueur"}]


def test_a_generated_effect_id_is_the_models_and_the_default_params_are_the_models():
    session, report = _apply(_project(), CLIP, _blur())
    assert report.ok
    (effect,) = _fx(session.project)
    assert effect.id.startswith("fx-") and effect.enabled and effect.params == {"intensity": EFFECT_PARAMETER_SPECS[EffectType.BLUR][0].default}


# --- Bornes lues dans le modèle ---------------------------------------------------------------------------------------------


def test_every_parameter_of_every_effect_is_bounded_by_the_model_at_both_ends_and_never_clamped():
    for effect_type, specs in EFFECT_PARAMETER_SPECS.items():
        for spec in specs:
            for value in (spec.minimum, spec.maximum):
                session, report = _apply(_project(), CLIP, {"op": "add_effect", "clip": "a", "type": effect_type.value, "params": {spec.name: value}})
                assert report.ok, (effect_type, spec.name, value, report.error)
                assert _fx(session.project)[0].params[spec.name] == value
            for outside in (spec.minimum - 0.5, spec.maximum + 0.5):
                session, report = _apply(_project(), CLIP, {"op": "add_effect", "clip": "a", "type": effect_type.value, "params": {spec.name: outside}})
                assert not report.ok and "hors bornes" in report.error, (effect_type, spec.name, outside)
                assert not session.can_undo
                session, report = _apply(_project(), CLIP, _blur(id="x") if effect_type is EffectType.BLUR else
                                         {"op": "add_effect", "clip": "a", "type": effect_type.value, "id": "x"},
                                         {"op": "update_effect", "clip": "a", "effect": "x", "params": {spec.name: outside}})
                assert not report.ok and "hors bornes" in report.error, (effect_type, spec.name, outside)
    session, report = _apply(_project(), CLIP, _blur(id="x", params={"intensity": 4}), {"op": "update_effect", "clip": "a", "effect": "x", "params": {"intensity": 9999}})
    assert not report.ok and not session.can_undo, "tout ou rien : l'effet ajouté par le même script est repris"


def test_unknown_parameters_and_empty_updates_are_refused():
    _s, report = _apply(_project(), CLIP, _blur(params={"force": 1}))
    assert not report.ok and "inconnu" in report.error
    _s, report = _apply(_project(), CLIP, _blur(id="x"), {"op": "update_effect", "clip": "a", "effect": "x", "params": {}})
    assert not report.ok and "au moins un paramètre" in report.error
    _s, report = _apply(_project(), CLIP, _blur(id="x"), {"op": "update_effect", "clip": "a", "effect": "x", "params": {"force": 1}})
    assert not report.ok and "inconnu" in report.error
    for params in ({"intensity": True}, {"intensity": "4"}):
        _s, report = _apply(_project(), CLIP, _blur(params=params))
        assert not report.ok and "numérique" in report.error, params


def test_a_single_instance_effect_twice_and_a_taken_id_are_refused_not_renamed():
    _s, report = _apply(_project(), CLIP, {"op": "add_effect", "clip": "a", "type": "sepia"}, {"op": "add_effect", "clip": "a", "type": "sepia"})
    assert not report.ok and "qu'une fois" in report.error
    with pytest.raises(ScriptInvalid):                                       # deux fois le même identifiant dans un script : refusé au décodage
        parse_script(_script(CLIP, _blur(id="x"), {"op": "add_effect", "clip": "a", "type": "sharpen", "id": "x"}))
    session, report = _apply(_project(), CLIP, _blur(id="x"))
    assert report.ok
    report = apply_script(session, parse_script(_script({"op": "add_effect", "clip": "a", "type": "sharpen", "id": "x"})))
    assert not report.ok and "existe déjà" in report.error and [item.id for item in _fx(session.project)] == ["x"]
    two, report = _apply(_project(), CLIP, {"op": "add_clip", "track": "V1", "asset": "m", "at": 10, "id": "b"}, _blur(id="x"),
                         {"op": "add_effect", "clip": "b", "type": "blur", "id": "x"})
    assert report.ok, "un identifiant d'effet est local au clip"
    assert [item.id for item in _fx(two.project, "b")] == ["x"]


def test_move_effect_refuses_what_the_model_would_clamp():
    base = (CLIP, _blur(id="b1"), {"op": "add_effect", "clip": "a", "type": "sharpen", "id": "s1"})
    session, report = _apply(_project(), *base, {"op": "move_effect", "clip": "a", "effect": "b1", "delta": 1})
    assert report.ok and [item.id for item in _fx(session.project)] == ["s1", "b1"]
    for delta in (-1, 2, 0):
        session, report = _apply(_project(), *base, {"op": "move_effect", "clip": "a", "effect": "b1", "delta": delta})
        assert not report.ok and "déplacement impossible" in report.error, delta
    with pytest.raises(ScriptInvalid):
        parse_script(_script({"op": "move_effect", "clip": "a", "effect": "b1", "delta": 1.5}))


# --- Verrous, genres de piste, identifiants ---------------------------------------------------------------------------------


def test_a_locked_track_refuses_every_effect_verb():
    seeded = (CLIP, _blur(id="x"))
    for op in (_blur(id="y"), {"op": "remove_effect", "clip": "a", "effect": "x"}, {"op": "set_effect_enabled", "clip": "a", "effect": "x", "enabled": False},
               {"op": "update_effect", "clip": "a", "effect": "x", "params": {"intensity": 3}}, {"op": "move_effect", "clip": "a", "effect": "x", "delta": 1}):
        session, report = _apply(_project(), *seeded, LOCK, op)
        assert not report.ok and "verrouillée" in report.error, op["op"]
        assert not session.can_undo, "tout ou rien"


def test_a_locked_layer_refuses_every_effect_verb():
    """La photo de template est un clip vidéo qui porte un `graphic` ; verrouillé, il refuse les effets comme l'inspecteur."""
    session = Session(_project())
    assert apply_script(session, parse_script(_script(CLIP, _blur(id="x")))).ok
    before = _fx(session.project)
    for op in (_blur(id="y"), {"op": "remove_effect", "clip": "a", "effect": "x"}, {"op": "set_effect_enabled", "clip": "a", "effect": "x", "enabled": False},
               {"op": "update_effect", "clip": "a", "effect": "x", "params": {"intensity": 3}}, {"op": "move_effect", "clip": "a", "effect": "x", "delta": 1}):
        clip = find_clip(session.project, "a")                                          # un script refusé restaure le projet : relire le clip
        clip.graphic = GraphicOverlay(type=GraphicType.TEXT, locked=True)
        report = apply_script(session, parse_script(_script(op)))
        assert not report.ok and "verrouillé" in report.error, op["op"]
        assert _fx(session.project) == before
    clip = find_clip(session.project, "a")
    clip.graphic = GraphicOverlay(type=GraphicType.TEXT, locked=False)
    assert apply_script(session, parse_script(_script({"op": "set_effect_enabled", "clip": "a", "effect": "x", "enabled": False}))).ok


def test_effects_belong_to_video_clips_only():
    graphic = {"op": "add_graphic", "graphic": "solid", "at": 0, "duration": 2, "id": "g"}
    for clip_op, clip_id in (({"op": "add_clip", "track": "A1", "asset": "voix", "at": 0, "id": "au"}, "au"), (graphic, "g"),
                             ({"op": "add_subtitle", "text": "x", "at": 0, "duration": 2, "id": "st"}, "st")):
        ops = [clip_op]
        if clip_id == "st":
            ops.insert(0, {"op": "add_track", "type": "subtitle", "id": "S1"})
        _s, report = _apply(_project(), *ops, {"op": "add_effect", "clip": clip_id, "type": "blur"})
        assert not report.ok and "cette opération vise video" in report.error, clip_id


def test_unknown_clip_effect_and_alias_kind_are_refused():
    _s, report = _apply(_project(), CLIP, {"op": "remove_effect", "clip": "a", "effect": "nope"})
    assert not report.ok and "introuvable" in report.error
    _s, report = _apply(_project(), {"op": "remove_effect", "clip": "zz", "effect": "nope"})
    assert not report.ok and "introuvable" in report.error
    with pytest.raises(ScriptInvalid):
        parse_script(_script(CLIP, {"op": "add_track", "type": "video", "as": "piste"}, {"op": "remove_effect", "clip": "a", "effect": {"ref": "piste"}}))
    for bad in (_blur(type="inconnu"), _blur(id="bad id!"), {"op": "add_effect", "clip": "a"}, _blur(params=[1]), _blur(enabled="oui"),
                {"op": "set_effect_enabled", "clip": "a", "effect": "x"}, {"op": "update_effect", "clip": "a", "effect": "x"}):
        with pytest.raises(ScriptInvalid):
            parse_script(_script(bad))


def test_the_effect_types_of_the_verb_are_the_models():
    types = next(p for p in VERBS["add_effect"].params if p.name == "type").choices
    assert types == tuple(item.value for item in EffectType)


def _seeded() -> Project:
    session, report = _apply(_project(), CLIP, _blur(id="x"))
    assert report.ok
    return session.project


# --- Fuzz ciblé -------------------------------------------------------------------------------------------------------------


def _fuzz_value(rng: random.Random, depth: int = 0):
    choices = [
        lambda: rng.choice([0, 1, -1, 0.5, 2.5, 1e308, -1e308, float("nan"), float("inf"), 10**30, True, False, None]),
        lambda: "".join(rng.choice(string.printable) for _ in range(rng.randint(0, 12))),
        lambda: rng.choice(["a", "x", "m1", "blur", "intensity", "../x", "é" * 300]),
    ]
    if depth < 2:
        choices.append(lambda: [_fuzz_value(rng, depth + 1) for _ in range(rng.randint(0, 3))])
        choices.append(lambda: {rng.choice(["ref", "intensity", "radius", "x"]): _fuzz_value(rng, depth + 1) for _ in range(rng.randint(0, 3))})
    return rng.choice(choices)()


def _plausible(rng: random.Random, param):
    """Une valeur du bon genre (souvent dans les bornes, parfois à côté) : le fuzz atteint ainsi les opérations, pas seulement le décodeur."""
    if param.kind is Kind.SELECTOR:
        return rng.choice({"clip": ["a", "t"], "mask": ["m1"], "effect": ["x"]}.get(param.of, ["a"]))
    if param.kind is Kind.ENUM:
        return rng.choice(param.choices)
    if param.kind is Kind.BOOLEAN:
        return rng.random() < 0.5
    if param.kind is Kind.INTEGER:
        return rng.randint(-3, 3)
    if param.kind is Kind.OBJECT:
        return {rng.choice(["intensity", "radius", "amount"]): rng.uniform(-1, 60) for _ in range(rng.randint(0, 2))}
    if param.kind is Kind.NUMBER:
        low = -5.0 if param.minimum is None else (0.0 if param.minimum < -1e3 else param.minimum)
        high = 5.0 if param.maximum is None else min(param.maximum, 1e4)
        return rng.uniform(low - 1, high + 1) if rng.random() < 0.2 else rng.uniform(low, min(high, low + 12))
    return rng.choice(["Titre", "#FF00AA", "x"])


def test_fuzz_the_new_verbs_only_ever_raise_script_invalid_or_a_report():
    rng = random.Random(20261018)
    verbs = [VERBS[name] for name in ["add_effect", "remove_effect", "set_effect_enabled", "update_effect", "move_effect"]]
    decoded = applied = 0
    for _ in range(1200):
        ops = []
        for _step in range(rng.randint(1, 2)):
            verb = rng.choice(verbs)
            op: dict = {"op": verb.name}
            for param in verb.params:
                if rng.random() < 0.9:
                    op[param.name] = _fuzz_value(rng) if rng.random() < 0.2 else _plausible(rng, param)
            ops.append(op)
        try:
            script = parse_script(_script(*ops))
        except ScriptInvalid as error:
            assert error.issues and len(error.issues) <= 50
            continue
        decoded += 1
        session = Session(_seeded())
        report = apply_script(session, script)
        assert report.ok or report.failed_index is not None
        applied += report.ok
    assert decoded > 100 and applied > 5, (decoded, applied)
