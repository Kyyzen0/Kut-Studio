"""Scripts, animation de l'étalonnage et des effets (ADR-0008, étape 9) : les verbes d'images clés acceptent les cibles ``color.*`` avec les
mêmes verrous, bornes et plafonds que les paramètres d'effet.

Chaque cas est une propriété animable (les 23 ``color.*`` et les 7 paramètres d'effet de l'étape 5 : flou, aberration chromatique, rayon du
Bloom, correction de couleur, vignette) ; chaque verbe est testé en création, édition, déplacement, suppression et activation. Un refus laisse le
projet strictement inchangé. L'aller-retour avec le rendu compare un script à l'appel direct du modèle (ce que l'interface appelle), jusqu'à la
chaîne FFmpeg de l'export.
"""

from __future__ import annotations

import copy
import math
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, replace

import pytest

from core import keyframe_editing as model
from core.animation import InterpolationType, Keyframe
from core.color_grading import ColorGrade
from core.color_nodes import ColorNode, ColorNodeGraph
from core.effect_sendcmd import command_files, context_for
from core.effects_model import EffectType, add_effect_to_clip
from core.export_engine import _build_clip_effect_filters, _build_color_grade_filters
from core.grade_targets import ANIMATABLE_GRADE_PARAMS, effective_grade, grade_spec
from core.project_model import Clip, MediaAsset, Project, Track
from core.scripting import ScriptInvalid, Session, apply_script, parse_script
from core.scripting.describe import describe
from core.scripting.resource_limits import MAX_KEYFRAMES_PER_CLIP
from core.scripting.verbs import VERBS
from core.timeline_operations import find_clip
from ui import i18n

# --- Le projet et les cas ----------------------------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _french(monkeypatch):
    monkeypatch.setenv("KUT_STUDIO_LANG", "fr")
    i18n.reset_for_tests()
    yield
    i18n.reset_for_tests()


def _project() -> Project:
    project = Project(
        "Démo", width=320, height=180, fps=30.0,
        media_assets=[
            MediaAsset("m", "/x/intro.mp4", "intro", 20.0, 320, 180, 30.0, "video", has_audio=True),
            MediaAsset("s", "/x/son.wav", "son", 20.0, 0, 0, 0.0, "audio", has_audio=True),
        ],
        tracks=[
            Track("V1", "V1", "video", clips=[Clip("a", "m", "V1", 0.0, 0.0, 10.0)]),
            Track("V3", "V3", "video", clips=[Clip("d", "m", "V3", 0.0, 0.0, 4.0)]),
            Track("A1", "A1", "audio", clips=[Clip("sound", "s", "A1", 0.0, 0.0, 4.0)]),
        ],
    )
    for clip_id in ("a", "d"):
        add_effect_to_clip(project, clip_id, EffectType.VIGNETTE, effect_id="v1")
        add_effect_to_clip(project, clip_id, EffectType.COLOR_CORRECTION, effect_id="cc")
        add_effect_to_clip(project, clip_id, EffectType.BLUR, effect_id="bl")
        add_effect_to_clip(project, clip_id, EffectType.CHROMATIC_ABERRATION, effect_id="ca")
        add_effect_to_clip(project, clip_id, EffectType.GLOW, effect_id="gl")
    project.tracks[1].locked = True                                                                      # V3 : verrouillée après coup
    return project


@dataclass(frozen=True)
class Case:
    """Une propriété animable et la façon de la poser / de la retirer par un script."""

    prop: str
    effect: str | None = None
    parameter: str | None = None

    def add(self, clip: str, at: float, value: float, **extra) -> dict:
        if self.effect is None:
            return {"op": "add_keyframe", "clip": clip, "property": self.prop, "at": at, "value": value, **extra}
        return {"op": "add_effect_keyframe", "clip": clip, "effect": self.effect, "parameter": self.parameter, "at": at, "value": value, **extra}

    def remove(self, clip: str, at: float) -> dict:
        if self.effect is None:
            return {"op": "remove_keyframe", "clip": clip, "property": self.prop, "at": at}
        return {"op": "remove_effect_keyframe", "clip": clip, "effect": self.effect, "parameter": self.parameter, "at": at}

    def spec(self, project: Project):
        from core.animation_targets import get_target

        return get_target(self.prop, find_clip(project, "a")).spec

    @property
    def id(self) -> str:
        return self.prop


def _fx(effect: str, parameter: str) -> Case:
    return Case(f"fx.{effect}.{parameter}", effect, parameter)


COLOR_CASES = [Case(name) for name in ANIMATABLE_GRADE_PARAMS]
EFFECT_CASES = [
    _fx("v1", "intensity"), _fx("cc", "brightness"), _fx("cc", "contrast"), _fx("cc", "saturation"),
    _fx("bl", "intensity"), _fx("ca", "intensity"), _fx("gl", "radius"),
]
CASES = EFFECT_CASES + COLOR_CASES
# Représentants pour les cas qui ne dépendent pas de la propriété (verrous, plafonds…) : un réglage simple, une roue, un effet de chaque rendu.
SAMPLE = [Case("color.exposure"), Case("color.contrast"), Case("color.lift.r"), _fx("v1", "intensity"), _fx("bl", "intensity")]


def _apply(project: Project, *ops: dict):
    session = Session(project)
    return session, apply_script(session, parse_script({"kut_script": 1, "ops": list(ops)}))


def _snapshot(project: Project):
    return copy.deepcopy(project.tracks)


def _refused(project: Project, *ops: dict, match: str | None = None):
    before = _snapshot(project)
    session, report = _apply(project, *ops)
    assert not report.ok, "le script aurait dû être refusé"
    assert _snapshot(session.project) == before, "un refus doit laisser le projet strictement inchangé"
    if match is not None:
        assert match in report.error, report.error
    return report


def _animated(case: Case, project: Project | None = None) -> Project:
    """Le clip `a` (10 s) avec deux images clés Bézier de la propriété, à 1 s et 3 s (valeurs à 20 % et 80 % de la plage)."""
    project = project or _project()
    spec = case.spec(project)
    low, span = spec.minimum, spec.maximum - spec.minimum
    for at, fraction in ((1.0, 0.2), (3.0, 0.8)):
        model.add_keyframe(project, "a", case.prop, at, low + fraction * span, interpolation=InterpolationType.BEZIER)
    return project


def _values(project: Project, case: Case) -> list[tuple[float, float, str]]:
    return [(k.time_seconds, k.value, k.interpolation.value) for k in find_clip(project, "a").animation if k.property_name == case.prop]


def _mid(case: Case, fraction: float = 0.5) -> float:
    spec = case.spec(_project())
    return spec.minimum + fraction * (spec.maximum - spec.minimum)


# --- Création, édition, déplacement, suppression, activation : parité avec le modèle ---------------------------------------------


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_every_verb_matches_the_model_for_every_animatable_property(case):
    """Un script qui crée, édite, déplace, retire et active/désactive l'animation écrit exactement ce que l'interface écrit (les mêmes fonctions de
    `core.keyframe_editing`, au même instant, à la même valeur) : clip, étalonnage et effets égaux."""
    low = _mid(case, 0.1)
    high = _mid(case, 0.9)
    middle = _mid(case)
    script = [
        case.add("a", 0.0, low, interpolation="hold"),                                                            # création
        case.add("a", 2.0, high),
        case.add("a", 4.0, middle, interpolation="bezier"),
        {"op": "set_keyframe_value", "clip": "a", "property": case.prop, "at": 2.0, "value": middle},               # édition
        {"op": "set_keyframe_interpolation", "clip": "a", "property": case.prop, "at": 2.0, "interpolation": "ease_in_out"},
        {"op": "set_keyframe_tangents", "clip": "a", "property": case.prop, "at": 4.0, "mode": "broken"},
        {"op": "set_keyframe_time", "clip": "a", "property": case.prop, "at": 4.0, "to": 5.0},                       # déplacement
        {"op": "move_keyframes", "clip": "a", "delta": 0.5, "keyframes": [{"property": case.prop, "at": 2.0}]},
        case.remove("a", 0.0),                                                                                      # suppression
        {"op": "set_animation_enabled", "clip": "a", "property": case.prop, "enabled": False, "at": 2.5},           # désactivation
        {"op": "set_animation_enabled", "clip": "a", "property": case.prop, "enabled": True, "at": 1.0},             # activation
    ]
    session, report = _apply(_project(), *script)
    assert report.ok, report.error

    reference = _project()
    ref = lambda moment: next(  # noqa: E731
        model.KeyframeRef("a", case.prop, k.id) for k in find_clip(reference, "a").animation
        if k.property_name == case.prop and abs(k.time_seconds - moment) < 1e-9
    )
    model.add_keyframe(reference, "a", case.prop, 0.0, low, interpolation="hold")
    model.add_keyframe(reference, "a", case.prop, 2.0, high)
    model.add_keyframe(reference, "a", case.prop, 4.0, middle, interpolation="bezier")
    model.set_keyframe_values(reference, {ref(2.0): middle})
    model.set_interpolation(reference, [ref(2.0)], "ease_in_out")
    model.set_tangents(reference, ref(4.0), mode=model.TangentMode.BROKEN)
    model.set_keyframe_time(reference, ref(4.0), 5.0)
    model.move_keyframes(reference, [ref(2.0)], 0.5)
    model.remove_keyframe_at(reference, "a", case.prop, 0.0)
    model.set_animation_enabled(reference, "a", case.prop, False, 2.5)
    model.set_animation_enabled(reference, "a", case.prop, True, 1.0)

    assert _snapshot(session.project) == _snapshot(reference)
    assert [k.property_name for k in find_clip(session.project, "a").animation] == [case.prop]       # une seule image clé : celle de l'activation
    assert session.undo() and not find_clip(session.project, "a").animation                            # un script = une étape d'annulation


@pytest.mark.parametrize("case", COLOR_CASES, ids=lambda c: c.id)
def test_disabling_the_animation_keeps_the_value_shown_at_that_instant(case):
    project = _animated(case)
    clip = find_clip(project, "a")
    shown = effective_grade(clip, 2.0)
    session, report = _apply(project, {"op": "set_animation_enabled", "clip": "a", "property": case.prop, "enabled": False, "at": 2.0})
    assert report.ok, report.error
    after = find_clip(session.project, "a")
    assert not after.animation
    value = lambda grade: (getattr(grade.__getattribute__(case.prop.split(".")[1]), case.prop.split(".")[2])  # noqa: E731
                           if case.prop.count(".") == 2 else getattr(grade, case.prop.split(".")[1]))
    assert value(after.color_grade) == pytest.approx(value(shown), abs=1e-9)


# --- Refus : le projet reste strictement inchangé ---------------------------------------------------------------------------------


def _verbs(case: Case, clip: str = "a") -> list[dict]:
    """Un exemplaire de chaque verbe qui écrit une image clé, sur une propriété qui porte déjà des images clés à 1 s et 3 s."""
    ops = [
        case.add(clip, 2.0, _mid(case)),
        case.remove(clip, 1.0),
        {"op": "set_keyframe_value", "clip": clip, "property": case.prop, "at": 1.0, "value": _mid(case, 0.4)},
        {"op": "set_keyframe_time", "clip": clip, "property": case.prop, "at": 1.0, "to": 2.0},
        {"op": "move_keyframes", "clip": clip, "delta": 0.5, "keyframes": [{"property": case.prop, "at": 1.0}]},
        {"op": "set_keyframe_interpolation", "clip": clip, "property": case.prop, "at": 1.0, "interpolation": "hold"},
        {"op": "set_keyframe_tangents", "clip": clip, "property": case.prop, "at": 1.0, "out_slope": 0.1},
        {"op": "set_animation_enabled", "clip": clip, "property": case.prop, "enabled": False, "at": 1.0},
        {"op": "set_animation_enabled", "clip": clip, "property": case.prop, "enabled": True, "at": 1.0},
    ]
    if case.effect is None:
        ops.append({"op": "add_keyframe", "clip": clip, "property": case.prop, "at": 2.0, "value": _mid(case)})
    return ops


@pytest.mark.parametrize("case", SAMPLE, ids=lambda c: c.id)
def test_a_locked_track_refuses_every_verb(case):
    """Le clip `a` le devient ; le clip `d` l'est dès le départ (piste V3) : ni l'un ni l'autre n'est modifié."""
    project = _animated(case)
    project.tracks[0].locked = True
    for operation in _verbs(case, "a"):
        assert "verrouill" in _refused(project, operation).error, operation
    fresh = _project()
    for operation in _verbs(case, "d"):
        assert "verrouill" in _refused(fresh, operation).error, operation


@pytest.mark.parametrize("case", SAMPLE, ids=lambda c: c.id)
def test_a_locked_layer_refuses_every_verb(case):
    project = _animated(case)
    find_clip(project, "a").graphic = replace(_a_graphic(), locked=True)
    for operation in _verbs(case):
        assert "verrouill" in _refused(project, operation).error, operation


def _a_graphic():
    donor = Project("g", width=320, height=180, fps=30.0, tracks=[Track("G1", "G1", "graphics")])
    session, report = _apply(donor, {"op": "add_graphic", "graphic": "text", "at": 0, "duration": 3, "id": "t1", "text": "A", "track": "G1"})
    assert report.ok, report.error
    return find_clip(session.project, "t1").graphic


@pytest.mark.parametrize("case", SAMPLE, ids=lambda c: c.id)
def test_an_audio_clip_refuses_every_verb(case):
    project = _animated(case)
    for operation in _verbs(case, "sound"):
        _refused(project, operation)


@pytest.mark.parametrize("case", COLOR_CASES[:: max(1, len(COLOR_CASES) // 6)], ids=lambda c: c.id)
def test_a_node_graph_refuses_every_verb_with_a_clear_message_and_changes_nothing(case):
    """Le rendu ignore les images clés `color.*` d'un graphe de nœuds : un script ne les crée donc pas (jamais plus permissif que l'interface)."""
    for graph in (
        ColorNodeGraph.serial((ColorNode("n1", ColorGrade()), ColorNode("n2", ColorGrade(contrast=0.2)))),     # deux nœuds
        ColorNodeGraph.serial((ColorNode("n1", ColorGrade(), label="Peau"),)),                                    # un nœud nommé
    ):
        project = _project()
        find_clip(project, "a").color_grade = graph
        for operation in _verbs(case):
            if operation["op"] == "remove_keyframe":
                continue                                                      # le nettoyage d'une clé restée : test suivant
            report = _refused(project, operation, match="graphe de nœuds")
            assert len(report.error) < 600


def test_a_node_graph_does_not_hide_the_other_properties_of_the_clip():
    project = _project()
    find_clip(project, "a").color_grade = ColorNodeGraph.serial((ColorNode("n1", ColorGrade()), ColorNode("n2", ColorGrade())))
    _session, report = _apply(project, {"op": "add_keyframe", "clip": "a", "property": "position_x", "at": 1.0, "value": 0.1},
                              {"op": "add_effect_keyframe", "clip": "a", "effect": "v1", "parameter": "intensity", "at": 1.0, "value": 0.5})
    assert report.ok, report.error


def test_a_stale_color_keyframe_of_a_node_graph_can_be_removed_but_not_edited():
    """Un clip « épargné » doit être réellement nettoyable : la clé restée dans un clip devenu graphe est ignorée au rendu, mais `remove_keyframe`
    la retire (sans écrire de valeur statique) ; les autres verbes la refusent."""
    project = _project()
    clip = find_clip(project, "a")
    clip.animation = [Keyframe("color.exposure", 1.0, 0.3, id="k1"), Keyframe("color.exposure", 2.0, 0.6, id="k2")]
    clip.color_grade = ColorNodeGraph.serial((ColorNode("n1", ColorGrade()), ColorNode("n2", ColorGrade())))
    graph = clip.color_grade
    assert describe(project)["sequences"][0]["tracks"][0]["clips"][0]["color_animation_live"] is False
    for operation in _verbs(Case("color.exposure")):
        if operation["op"] != "remove_keyframe":
            _refused(project, operation, match="graphe de nœuds")
    _refused(project, {"op": "remove_keyframe", "clip": "a", "property": "color.exposure", "at": 5.0}, match="Aucune image clé")
    session, report = _apply(project, {"op": "remove_keyframe", "clip": "a", "property": "color.exposure", "at": 1.0})
    assert report.ok, report.error
    after = find_clip(session.project, "a")
    assert [(k.time_seconds, k.value) for k in after.animation] == [(2.0, 0.6)]
    assert after.color_grade == graph                                                        # aucune valeur statique écrite
    locked = _project()
    find_clip(locked, "a").animation = [Keyframe("color.exposure", 1.0, 0.3, id="k1")]
    find_clip(locked, "a").color_grade = graph
    locked.tracks[0].locked = True
    _refused(locked, {"op": "remove_keyframe", "clip": "a", "property": "color.exposure", "at": 1.0}, match="verrouill")


@pytest.mark.parametrize("case", COLOR_CASES, ids=lambda c: c.id)
def test_a_value_out_of_the_bounds_of_the_setting_is_refused_not_clamped(case):
    project = _animated(case)
    spec = case.spec(project)
    for bad in (spec.maximum + 1.0, spec.minimum - 1.0):
        _refused(project, case.add("a", 2.0, bad), match="hors bornes")
        _refused(project, {"op": "set_keyframe_value", "clip": "a", "property": case.prop, "at": 1.0, "value": bad}, match="hors bornes")
    for edge in (spec.minimum, spec.maximum):                                                  # les bornes elles-mêmes sont acceptées
        _session, report = _apply(project, case.add("a", 2.0, edge))
        assert report.ok, report.error


@pytest.mark.parametrize("case", SAMPLE, ids=lambda c: c.id)
@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf], ids=["nan", "inf", "-inf"])
def test_a_non_finite_value_is_refused_by_the_schema_and_by_the_operation(case, bad):
    project = _animated(case)
    before = _snapshot(project)
    with pytest.raises(ScriptInvalid):
        parse_script({"kut_script": 1, "ops": [case.add("a", 2.0, bad)]})
    with pytest.raises(ScriptInvalid):
        parse_script({"kut_script": 1, "ops": [{"op": "set_keyframe_value", "clip": "a", "property": case.prop, "at": 1.0, "value": bad}]})
    # Défense en profondeur : l'opération elle-même (construite sans le schéma) refuse aussi.
    first = case.add("a", 2.0, bad)
    operation = VERBS[first.pop("op")].build(first)
    with pytest.raises(ValueError):
        operation(project)
    operation = VERBS["set_keyframe_value"].build({"clip": "a", "property": case.prop, "at": 1.0, "value": bad})
    with pytest.raises(ValueError):
        operation(project)
    assert _snapshot(project) == before


@pytest.mark.parametrize("case", SAMPLE, ids=lambda c: c.id)
def test_a_time_outside_the_clip_is_refused(case):
    project = _animated(case)
    _refused(project, case.add("a", 11.0, _mid(case)))                                                   # le clip dure 10 s
    _refused(project, {"op": "set_keyframe_time", "clip": "a", "property": case.prop, "at": 1.0, "to": 12.0})
    _refused(project, {"op": "move_keyframes", "clip": "a", "delta": 20.0, "keyframes": [{"property": case.prop, "at": 1.0}]})
    _refused(project, {"op": "set_animation_enabled", "clip": "a", "property": case.prop, "enabled": True, "at": 11.0})
    _refused(project, {"op": "set_keyframe_time", "clip": "a", "property": case.prop, "at": 1.0, "to": 3.0}, match="remplacerait")   # arrive sur une autre clé
    before = _snapshot(project)
    for first in (case.add("a", math.nan, _mid(case)), {"op": "set_animation_enabled", "clip": "a", "property": case.prop, "enabled": True, "at": math.nan}):
        with pytest.raises(ValueError):                                                                  # un instant non fini : refusé par l'opération elle-même
            VERBS[first.pop("op")].build(first)(project)
    assert _snapshot(project) == before


@pytest.mark.parametrize("case", SAMPLE, ids=lambda c: c.id)
def test_an_unknown_keyframe_or_property_is_refused(case):
    project = _animated(case)
    _refused(project, {"op": "set_keyframe_value", "clip": "a", "property": case.prop, "at": 2.0, "value": _mid(case)})   # pas de clé à 2 s
    _refused(project, case.remove("a", 2.0))
    _refused(project, {"op": "set_keyframe_value", "clip": "a", "property": "color.curves", "at": 1.0, "value": 0.1})
    _refused(project, {"op": "set_keyframe_value", "clip": "a", "property": "color.nope.r", "at": 1.0, "value": 0.1})


@pytest.mark.parametrize("case", SAMPLE, ids=lambda c: c.id)
def test_the_keyframe_ceiling_counts_color_and_effect_keys_alike(case, monkeypatch):
    """Un seul plafond par clip (transform + animation générique) : une clé `color.*` ou `fx.*` de plus que le plafond est refusée par chaque
    verbe qui en ajoute une, une qui en remplace une est acceptée."""
    from core.scripting import resource_limits

    monkeypatch.setattr(resource_limits, "MAX_KEYFRAMES_PER_CLIP", 2)
    project = _animated(case)                                                                              # le clip en porte déjà deux
    _refused(project, case.add("a", 2.0, _mid(case)), match="images clés")
    other = Case("color.hue") if case.prop != "color.hue" else Case("color.saturation")
    _refused(project, {"op": "set_animation_enabled", "clip": "a", "property": other.prop, "enabled": True, "at": 0.0}, match="images clés")
    _session, report = _apply(project, case.add("a", 1.0, _mid(case, 0.3)))                                 # remplace : accepté
    assert report.ok, report.error
    assert MAX_KEYFRAMES_PER_CLIP > 2
    # Une clé qui existe déjà (animation déjà posée) ne coûte rien : `enabled: true` ne rajoute pas de clé.
    _session, report = _apply(project, {"op": "set_animation_enabled", "clip": "a", "property": case.prop, "enabled": True, "at": 0.0})
    assert report.ok, report.error


# --- La description et le schéma ----------------------------------------------------------------------------------------------


def test_add_keyframe_lists_every_grade_property_of_the_registry():
    choices = {p.name: p for p in VERBS["add_keyframe"].params}["property"].choices
    assert set(ANIMATABLE_GRADE_PARAMS) <= set(choices)
    assert "color.lift.r" in choices and "color.exposure" in choices
    assert not any(name.startswith("fx.") for name in choices)                      # les effets ont add_effect_keyframe


def test_describe_shows_the_color_keyframes_and_whether_they_are_live():
    project = _project()
    session, report = _apply(project, {"op": "add_keyframe", "clip": "a", "property": "color.exposure", "at": 1.0, "value": 0.4})
    assert report.ok, report.error
    data = describe(session.project)
    clip = next(c for t in data["sequences"][0]["tracks"] for c in t["clips"] if c["id"] == "a")
    assert [k["property_name"] for k in clip["animation"]] == ["color.exposure"]
    assert clip["color_grade_simple"] is True and clip["color_animation_live"] is True and clip["has_keyframes"] is True
    other = next(c for t in data["sequences"][0]["tracks"] for c in t["clips"] if c["id"] == "d")
    assert other["color_grade_simple"] is True and other["color_animation_live"] is False
    assert data["describe_version"] == 1


def test_a_described_color_animation_is_rewritten_by_the_verbs():
    """Aller-retour avec `describe` : les images clés `color.*` de la description, rejouées par `add_keyframe`, redonnent la même animation."""
    original = _project()
    for prop, moment, value in (("color.exposure", 0.0, -0.5), ("color.exposure", 2.0, 0.5), ("color.lift.g", 1.0, 0.2), ("color.hue", 3.0, 30.0)):
        model.add_keyframe(original, "a", prop, moment, value, interpolation="ease_in_out")
    wanted = next(c for t in describe(original)["sequences"][0]["tracks"] for c in t["clips"] if c["id"] == "a")["animation"]
    rebuilt = _project()
    ops = [{"op": "add_keyframe", "clip": "a", "property": k["property_name"], "at": k["time_seconds"], "value": k["value"],
            "interpolation": k["interpolation"]} for k in wanted]
    session, report = _apply(rebuilt, *ops)
    assert report.ok, report.error
    again = next(c for t in describe(session.project)["sequences"][0]["tracks"] for c in t["clips"] if c["id"] == "a")["animation"]
    strip = lambda items: [{key: value for key, value in k.items() if key != "id"} for k in items]  # noqa: E731
    assert strip(again) == strip(wanted)


# --- Aller-retour avec le rendu ---------------------------------------------------------------------------------------------------


def _stable(text: str, written: list[str]) -> tuple[str, list[str]]:
    """La chaîne, avec les noms des fichiers de commandes (temporaires, au hasard) remplacés par leur rang, et le contenu de ces fichiers."""
    # Le chemin écrit dans la chaîne est échappé pour FFmpeg (`C\\:/Users/…` sous Windows) : il ne ressemble donc pas au chemin de la liste. On
    # remplace chaque `f='….cmd'` par son rang, quelle que soit la plateforme ; le contenu des fichiers, lui, est comparé tel quel.
    ranks = iter(range(10_000))
    text = re.sub(r"f='[^']*\.cmd'", lambda _m: f"f='<fichier {next(ranks)}>'", text)
    assert text.count("<fichier ") == len(written), (text, written)
    contents = []
    for path in written:
        with open(path, encoding="utf-8") as handle:
            contents.append(handle.read())
        os.remove(path)
    return text, contents


def _grade_chain(clip: Clip, duration: float = 4.0) -> tuple[str, list[str]]:
    written: list[str] = []
    with command_files(written):
        text = _build_color_grade_filters(clip.color_grade, "v0cg", animation=tuple(clip.animation), sendcmd=context_for(30, 0.0, duration))
    return _stable(text, written)


def _effects_chain(clip: Clip, duration: float = 4.0) -> tuple[str, list[str]]:
    written: list[str] = []
    with command_files(written):
        text = _build_clip_effect_filters(tuple(clip.effects), 1.0, "v0fx", animation=tuple(clip.animation), sendcmd=context_for(30, 0.0, duration))
    return _stable(text, written)


_KEYS = (("color.contrast", 0.0, -0.5), ("color.contrast", 2.0, 0.6), ("color.exposure", 0.5, -0.4), ("color.exposure", 3.0, 0.5),
         ("color.shadows", 1.0, 0.3), ("color.lift.b", 1.0, 0.2), ("color.hue", 0.0, -20.0), ("color.hue", 2.5, 25.0))


def test_a_color_key_created_by_script_is_rendered_like_one_created_by_the_interface():
    """Le script et l'interface (`core.keyframe_editing`) écrivent les mêmes images clés : la chaîne de l'export (expressions, fichiers `sendcmd`)
    est la même caractère pour caractère, et la valeur évaluée (`effective_grade`) aussi."""
    session, report = _apply(_project(), *[{"op": "add_keyframe", "clip": "a", "property": p, "at": t, "value": v, "interpolation": "bezier"}
                                           for p, t, v in _KEYS])
    assert report.ok, report.error
    reference = _project()
    for prop, moment, value in _KEYS:
        model.add_keyframe(reference, "a", prop, moment, value, interpolation="bezier")
    scripted, direct = find_clip(session.project, "a"), find_clip(reference, "a")
    text, files = _grade_chain(scripted)
    reference_text, reference_files = _grade_chain(direct)
    assert "sendcmd" in text and "eq=" in text, text                       # l'animation est bien rendue (expression et fichier de commandes)
    assert (text, files) == (reference_text, reference_files)
    for moment in (0.0, 0.7, 1.3, 2.0, 2.9, 3.6):
        assert effective_grade(scripted, moment) == effective_grade(direct, moment)
    # Le même résultat par un clip qui n'a pas d'animation : la chaîne statique n'est pas touchée.
    assert _grade_chain(find_clip(_project(), "a")) == ("", [])


def test_an_effect_key_created_by_script_is_rendered_like_one_created_by_the_interface():
    keys = (("v1", "intensity", 0.0, 0.2), ("v1", "intensity", 2.0, 0.9), ("bl", "intensity", 0.0, 1.0), ("bl", "intensity", 3.0, 20.0),
            ("ca", "intensity", 1.0, 5.0), ("gl", "radius", 0.0, 4.0), ("gl", "radius", 2.0, 30.0), ("cc", "contrast", 1.0, 1.5))
    session, report = _apply(_project(), *[
        {"op": "add_effect_keyframe", "clip": "a", "effect": e, "parameter": n, "at": t, "value": v} for e, n, t, v in keys])
    assert report.ok, report.error
    reference = _project()
    for effect, name, moment, value in keys:
        model.add_keyframe(reference, "a", f"fx.{effect}.{name}", moment, value)
    scripted, direct = find_clip(session.project, "a"), find_clip(reference, "a")
    text, files = _effects_chain(scripted)
    assert "sendcmd" in text, text
    assert (text, files) == _effects_chain(direct)


def test_removing_the_keys_by_script_leaves_a_static_chain():
    """Comme dans l'interface, une propriété dont on retire la dernière image clé garde la valeur qu'elle montrait : la chaîne n'a plus ni
    `sendcmd` ni fichier de commandes."""
    session, report = _apply(_project(), *[{"op": "add_keyframe", "clip": "a", "property": p, "at": t, "value": v} for p, t, v in _KEYS],
                             *[{"op": "remove_keyframe", "clip": "a", "property": p, "at": t} for p, t, _v in _KEYS])
    assert report.ok, report.error
    clip = find_clip(session.project, "a")
    text, files = _grade_chain(clip)
    assert not clip.animation and "sendcmd" not in text and "eq=" in text and files == []


# --- Mutation après validation ------------------------------------------------------------------------------------------------------


def test_a_script_cannot_alter_the_project_after_commit_through_a_shared_value():
    """Le script s'applique sur une copie, puis est validé : modifier ensuite les objets qu'il a posés (images clés, étalonnage) n'atteint pas
    l'annulation (l'instantané d'avant), et la valeur lue reste celle qui a été écrite."""
    session, report = _apply(_project(), {"op": "add_keyframe", "clip": "a", "property": "color.exposure", "at": 1.0, "value": 0.4})
    assert report.ok, report.error
    clip = find_clip(session.project, "a")
    clip.animation[0] = replace(clip.animation[0], value=0.0)                                                # mutation APRÈS le commit
    assert session.undo()
    assert not find_clip(session.project, "a").animation and find_clip(session.project, "a").color_grade is None
    assert session.redo()
    assert [(k.time_seconds, k.value) for k in find_clip(session.project, "a").animation] == [(1.0, 0.4)]   # le rétablissement rejoue l'état validé


def _all_chains(case: Case) -> Callable[[], None]:       # pragma: no cover - garde de lecture : le registre est la liste des cas
    return lambda: None


def test_the_cases_cover_the_whole_registry():
    from core.effect_targets import ANIMATABLE_EFFECT_PARAMS

    assert {c.prop for c in COLOR_CASES} == set(ANIMATABLE_GRADE_PARAMS) and len(COLOR_CASES) == 23
    covered = {(c.prop.split(".")[1], c.parameter) for c in EFFECT_CASES}
    clip = find_clip(_project(), "a")
    wanted = {(effect.id, name) for effect in clip.effects for name in ANIMATABLE_EFFECT_PARAMS.get(effect.type, ())}
    assert covered == wanted, (covered ^ wanted)
    assert grade_spec("color.exposure").maximum > 0
