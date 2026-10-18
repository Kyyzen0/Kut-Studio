"""Scripts, images clés (ADR-0005 étape 4, lot 8) : `add_keyframe` sur tout le registre, `add_mask_keyframe`, `remove_mask_keyframe`.

Les trois règles de la revue de l'étape 4 : bornes lues dans le modèle, verrou respecté, genres de piste vérifiés. Le chemin est celui de
l'interface (`core.keyframe_editing`) : la parité est testée contre l'appel direct. Un test relit le tableau des unités de
`docs/scripting.md` et le compare au registre d'animation.
"""

from __future__ import annotations

import random
import re
import string
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from core.animation import ValueKind
from core.animation_targets import all_targets, get_target
from core.compositing import MASK_PROPERTY_ORDER, MASK_PROPERTY_SPECS, mask_property_id
from core.keyframe_editing import add_keyframe as direct_add_keyframe
from core.keyframe_editing import value_at
from core.project_model import MediaAsset, Project, Track
from core.scripting import ScriptInvalid, Session, apply_script, parse_script
from core.scripting.verbs import _ANIMATABLE, VERBS, Kind
from core.timeline_operations import find_clip
from core.tracking_model import ClipTracking, TrackLink, TrackTarget
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
        media_assets=[MediaAsset("m", "/x/intro.mp4", "intro", 10.0, 320, 180, 30.0, "video", has_audio=True)],
        tracks=[Track("V1", "V1", "video"), Track("A1", "A1", "audio"), Track("G1", "G1", "graphics")],
    )


def _script(*ops: dict) -> dict:
    return {"kut_script": 1, "ops": list(ops)}


def _apply(project: Project, *ops: dict):
    session = Session(project)
    return session, apply_script(session, parse_script(_script(*ops)))


CLIP = {"op": "add_clip", "track": "V1", "asset": "m", "at": 0, "id": "a"}
TEXT = {"op": "add_graphic", "graphic": "text", "at": 0, "duration": 10, "id": "t", "text": "Titre"}
MASK = {"op": "add_mask", "clip": "a", "shape": "ellipse", "id": "m1"}


def _kf(clip: str, mask: str, prop: str, at: float, value, **extra) -> dict:
    return {"op": "add_mask_keyframe", "clip": clip, "mask": mask, "property": prop, "at": at, "value": value, **extra}


def _animation(project: Project, clip_id: str) -> list:
    return list(find_clip(project, clip_id).animation)


# --- add_keyframe : le registre, plus aucun nom recopié -------------------------------------------------------------------


def test_the_properties_of_add_keyframe_are_the_registrys_not_a_copy():
    expected = tuple(t.id for t in all_targets() if t.spec.kind is not ValueKind.BOOL and not t.id.startswith("time."))
    assert _ANIMATABLE == expected
    assert {"pan_x", "pan_y", "graphic.width", "graphic.font_size"} <= set(_ANIMATABLE)
    assert not {"flip_h", "flip_v", "time.speed", "fill"} & set(_ANIMATABLE)
    assert not any(name.startswith("mask.") for name in _ANIMATABLE)
    assert tuple(next(p for p in VERBS["add_keyframe"].params if p.name == "property").choices) == expected


def test_flip_and_speed_are_refused_at_decoding():
    for name in ("flip_h", "flip_v", "time.speed", "mask.m1.feather"):
        with pytest.raises(ScriptInvalid):
            parse_script(_script({"op": "add_keyframe", "clip": "a", "property": name, "at": 0, "value": 1}))


def test_a_graphic_property_is_keyframed_like_the_interface_does():
    session, report = _apply(_project(), TEXT, {"op": "add_keyframe", "clip": "t", "property": "graphic.font_size", "at": 1, "value": 120.0})
    assert report.ok, report.error
    reference = _project()
    assert apply_script(Session(reference), parse_script(_script(TEXT))).ok
    direct_add_keyframe(reference, "t", "graphic.font_size", 1.0, 120.0)
    assert _animation(session.project, "t") == _animation(reference, "t")
    assert value_at(find_clip(session.project, "t"), "graphic.font_size", 1.0) == 120.0


def test_graphic_and_pan_properties_respect_the_clip_kind():
    _s, report = _apply(_project(), CLIP, {"op": "add_keyframe", "clip": "a", "property": "graphic.width", "at": 0, "value": 400})
    assert not report.ok and "ne s'applique pas" in report.error                            # un calque graphique seulement
    _s, report = _apply(_project(), TEXT, {"op": "add_keyframe", "clip": "t", "property": "graphic.corner_radius", "at": 0, "value": 4})
    assert not report.ok and "ne s'applique pas" in report.error                            # un rayon d'angle : une forme, pas un texte
    _s, report = _apply(_project(), TEXT, {"op": "add_keyframe", "clip": "t", "property": "pan_x", "at": 0, "value": 0.5})
    assert not report.ok and "ne s'applique pas" in report.error                            # le cadrage « remplir » : un média vidéo
    session, report = _apply(_project(), CLIP, {"op": "add_keyframe", "clip": "a", "property": "pan_x", "at": 0, "value": 0.5})
    assert report.ok and value_at(find_clip(session.project, "a"), "pan_x", 0.0) == 0.5


def test_graphic_properties_are_bounded_by_the_registry_not_clamped():
    for name in (n for n in _ANIMATABLE if n in {"graphic.width", "graphic.font_size", "graphic.reveal", "graphic.shadow_blur"}):
        spec = get_target(name).spec
        for outside in (spec.minimum - 1.0, spec.maximum + 1.0):
            _s, report = _apply(_project(), TEXT, {"op": "add_keyframe", "clip": "t", "property": name, "at": 0, "value": outside})
            assert not report.ok and "hors bornes" in report.error, (name, outside)
        for inside in (spec.minimum, spec.maximum):
            session, report = _apply(_project(), TEXT, {"op": "add_keyframe", "clip": "t", "property": name, "at": 0, "value": inside})
            assert report.ok, (name, report.error)
            assert value_at(find_clip(session.project, "t"), name, 0.0) == inside


def test_graphic_keyframes_respect_the_track_and_layer_locks():
    _s, report = _apply(_project(), TEXT, {"op": "set_track_locked", "track": "G1", "locked": True},
                        {"op": "add_keyframe", "clip": "t", "property": "graphic.width", "at": 0, "value": 400})
    assert not report.ok and "verrouillée" in report.error
    session = Session(_project())
    assert apply_script(session, parse_script(_script(TEXT))).ok
    clip = find_clip(session.project, "t")
    clip.graphic = replace(clip.graphic, locked=True)
    report = apply_script(session, parse_script(_script({"op": "add_keyframe", "clip": "t", "property": "graphic.width", "at": 0, "value": 400})))
    assert not report.ok and "verrouillé" in report.error and not _animation(session.project, "t")


def test_set_graphic_refuses_an_animated_property_but_not_the_others():
    session, report = _apply(_project(), TEXT, {"op": "add_keyframe", "clip": "t", "property": "graphic.width", "at": 0, "value": 400})
    assert report.ok
    before = deepcopy(find_clip(session.project, "t").graphic)
    report = apply_script(session, parse_script(_script({"op": "set_graphic", "clip": "t", "width": 500, "font_size": 90})))
    assert not report.ok and "animé" in report.error and find_clip(session.project, "t").graphic == before, "tout ou rien"
    report = apply_script(session, parse_script(_script({"op": "set_graphic", "clip": "t", "font_size": 90, "text": "Autre"})))
    assert report.ok and find_clip(session.project, "t").graphic.font_size == 90


# --- add_mask_keyframe / remove_mask_keyframe -----------------------------------------------------------------------------


def test_a_mask_keyframe_is_the_one_the_interface_writes_and_replays_to_the_same_project():
    ops = (CLIP, MASK, _kf("a", "m1", "feather", 0, 0.0), _kf("a", "m1", "feather", 2, 0.8, interpolation="hold"))
    session, report = _apply(_project(), *ops)
    assert report.ok, report.error
    reference = _project()
    assert apply_script(Session(reference), parse_script(_script(CLIP, MASK))).ok
    direct_add_keyframe(reference, "a", mask_property_id("m1", "feather"), 0.0, 0.0)
    direct_add_keyframe(reference, "a", mask_property_id("m1", "feather"), 2.0, 0.8, interpolation="hold")
    assert _animation(session.project, "a") == _animation(reference, "a")
    again, report = _apply(_project(), *ops)
    assert report.ok and _animation(again.project, "a") == _animation(session.project, "a")
    replaced, report = _apply(session.project, _kf("a", "m1", "feather", 2, 0.5))
    assert report.ok and len(_animation(replaced.project, "a")) == 2, "même instant : remplacé, pas ajouté"
    assert value_at(find_clip(session.project, "a"), mask_property_id("m1", "feather"), 2.0) == 0.5


def test_every_mask_property_is_bounded_by_the_registry_at_both_ends():
    for name in MASK_PROPERTY_ORDER:
        spec = MASK_PROPERTY_SPECS[name]
        for value in (spec.minimum, spec.maximum):
            session, report = _apply(_project(), CLIP, MASK, _kf("a", "m1", name, 0, value))
            assert report.ok, (name, value, report.error)
            assert value_at(find_clip(session.project, "a"), mask_property_id("m1", name), 0.0) == pytest.approx(value)
        for outside in (spec.minimum - 0.5, spec.maximum + 0.5):
            session, report = _apply(_project(), CLIP, MASK, _kf("a", "m1", name, 0, outside))
            assert not report.ok and "hors bornes" in report.error, (name, outside)
            assert not session.can_undo, "tout ou rien : rien n'est écrit"


def test_a_mask_keyframe_refuses_what_the_interface_does_not_animate():
    for target, text in (("crop", "recadrage"), ("trackcrop", "recadrage")):
        _s, report = _apply(_project(), CLIP, _kf("a", target, "feather", 0, 0.1))
        assert not report.ok and text in report.error, target
    _s, report = _apply(_project(), CLIP, MASK, _kf("a", "inconnu", "feather", 0, 0.1))
    assert not report.ok and "introuvable" in report.error
    _s, report = _apply(_project(), CLIP, {"op": "add_color_window", "clip": "a", "shape": "ellipse", "id": "w1"}, _kf("a", "w1", "feather", 0, 0.1))
    assert not report.ok and "fenêtre d'étalonnage" in report.error
    _s, report = _apply(_project(), CLIP, MASK, _kf("a", "m1", "feather", 10.5, 0.1))
    assert not report.ok and "dépasse la durée" in report.error


def test_the_position_of_a_tracked_mask_is_refused_since_the_tracking_would_overwrite_it():
    session = Session(_project())
    assert apply_script(session, parse_script(_script(CLIP, MASK))).ok
    find_clip(session.project, "a").tracking = ClipTracking(links=(TrackLink(id="l1", target=TrackTarget.MASK, mask_id="m1"),))
    for name in ("position_x", "position_y"):
        report = apply_script(session, parse_script(_script(_kf("a", "m1", name, 0, 0.4))))
        assert not report.ok and "suivi" in report.error
    assert apply_script(session, parse_script(_script(_kf("a", "m1", "feather", 0, 0.4)))).ok, "les autres propriétés restent libres"


def test_mask_keyframes_respect_locks_and_track_kinds():
    lock = {"op": "set_track_locked", "track": "V1", "locked": True}
    for op in (_kf("a", "m1", "feather", 0, 0.1), {"op": "remove_mask_keyframe", "clip": "a", "mask": "m1", "property": "feather", "at": 0}):
        _s, report = _apply(_project(), CLIP, MASK, _kf("a", "m1", "opacity", 0, 0.5), lock, op)
        assert not report.ok and "verrouillée" in report.error
    project = _project()
    project.media_assets.append(MediaAsset("voix", "/x/voix.wav", "voix", 10.0, 0, 0, 0.0, "audio", has_audio=True))
    _s, report = _apply(project, {"op": "add_clip", "track": "A1", "asset": "voix", "at": 0, "id": "au"}, _kf("au", "m1", "feather", 0, 0.1))
    assert not report.ok and "piste 'audio'" in report.error
    _s, report = _apply(_project(), {"op": "add_track", "type": "subtitle", "id": "S1"}, {"op": "add_subtitle", "text": "x", "at": 0, "duration": 2, "id": "st"},
                        _kf("st", "m1", "feather", 0, 0.1))
    assert not report.ok and "piste 'subtitle'" in report.error
    session = Session(_project())
    graphic = {"op": "add_graphic", "graphic": "solid", "at": 0, "duration": 4, "id": "g"}
    assert apply_script(session, parse_script(_script(graphic, {"op": "add_mask", "clip": "g", "shape": "ellipse", "id": "m1"}))).ok
    clip = find_clip(session.project, "g")
    clip.graphic = replace(clip.graphic, locked=True)
    report = apply_script(session, parse_script(_script(_kf("g", "m1", "feather", 0, 0.2))))
    assert not report.ok and "verrouillé" in report.error and not _animation(session.project, "g")
    clip = find_clip(session.project, "g")                                              # un script refusé restaure le projet : relire le clip
    clip.graphic = replace(clip.graphic, locked=False)
    assert apply_script(session, parse_script(_script(_kf("g", "m1", "feather", 0, 0.2)))).ok, "un calque graphique déverrouillé s'anime"


def test_remove_mask_keyframe_and_its_refusals():
    session, report = _apply(_project(), CLIP, MASK, _kf("a", "m1", "feather", 0, 0.1), _kf("a", "m1", "feather", 1, 0.9))
    assert report.ok
    done = apply_script(session, parse_script(_script({"op": "remove_mask_keyframe", "clip": "a", "mask": "m1", "property": "feather", "at": 1})))
    assert done.ok and [kf.time_seconds for kf in _animation(session.project, "a")] == [0.0]
    missing = apply_script(session, parse_script(_script({"op": "remove_mask_keyframe", "clip": "a", "mask": "m1", "property": "feather", "at": 1})))
    assert not missing.ok and "Aucune image clé" in missing.error
    gone = apply_script(session, parse_script(_script({"op": "remove_mask_keyframe", "clip": "a", "mask": "nope", "property": "feather", "at": 0})))
    assert not gone.ok and "introuvable" in gone.error


def test_mask_keyframes_use_the_alias_of_the_mask_and_decoding_refuses_nonsense():
    session, report = _apply(_project(), CLIP, {"op": "add_mask", "clip": "a", "shape": "ellipse", "as": "vig"}, _kf("a", {"ref": "vig"}, "opacity", 0, 0.5))
    assert report.ok, report.error
    for bad in (_kf("a", "m1", "gravite", 0, 1), _kf("a", "m1", "feather", -1, 0.5), _kf("a", "m1", "feather", 0, "1"), _kf("a", "m1", "feather", 0, True),
                {"op": "add_mask_keyframe", "clip": "a", "mask": "m1", "property": "feather", "at": 0}):
        with pytest.raises(ScriptInvalid):
            parse_script(_script(bad))
    for bad in (float("nan"), float("inf")):
        with pytest.raises(ScriptInvalid):
            parse_script(_script(_kf("a", "m1", "feather", 0, bad)))


# --- Le tableau des unités de la documentation ----------------------------------------------------------------------------


def _doc_table(marker: str) -> dict[str, tuple[float, float, float]]:
    text = (Path(__file__).resolve().parent.parent / "docs" / "scripting.md").read_text(encoding="utf-8")
    block = text[text.index(f"<!-- units:{marker} -->"):]
    block = block[:block.index("<!-- /units -->")]
    rows = re.findall(r"^\| `([a-z_.]+)` \| [^|]+ \| (-?[0-9.]+) \| (-?[0-9.]+) \| (-?[0-9.]+) \|$", block, flags=re.M)
    return {name: (float(default), float(low), float(high)) for name, default, low, high in rows}


def test_the_documented_units_table_matches_the_animation_registry():
    documented = _doc_table("keyframe")
    assert set(documented) == set(_ANIMATABLE), "chaque propriété de add_keyframe a sa ligne, et rien d'autre"
    for name, (default, low, high) in documented.items():
        spec = get_target(name).spec
        assert (default, low, high) == (spec.default, spec.minimum, spec.maximum), name
    masks = _doc_table("mask")
    assert set(masks) == set(MASK_PROPERTY_ORDER)
    for name, (default, low, high) in masks.items():
        spec = MASK_PROPERTY_SPECS[name]
        assert (default, low, high) == (spec.default, spec.minimum, spec.maximum), name


def _seeded() -> Project:
    session, report = _apply(_project(), CLIP, MASK, TEXT, _kf("a", "m1", "feather", 0, 0.2))
    assert report.ok
    return session.project


# --- Fuzz ciblé -------------------------------------------------------------------------------------------------------------


def _fuzz_value(rng: random.Random, depth: int = 0):
    choices = [
        lambda: rng.choice([0, 1, -1, 0.5, 2.5, 1e308, -1e308, float("nan"), float("inf"), 10**30, True, False, None]),
        lambda: "".join(rng.choice(string.printable) for _ in range(rng.randint(0, 12))),
        lambda: rng.choice(["a", "t", "m1", "feather", "opacity", "graphic.width", "../x", "é" * 300]),
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
    verbs = [VERBS[name] for name in ["add_keyframe", "remove_keyframe", "add_mask_keyframe", "remove_mask_keyframe", "set_graphic"]]
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
