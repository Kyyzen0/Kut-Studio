"""Format déclaratif : presets intégrés d'effets et d'étalonnage (lot 10 de l'extension 0.8.0) : `apply_effect_preset`, `apply_color_preset`.

Chaque verbe appelle la fonction que la bibliothèque de l'interface appelle : le test de parité fait le même appel directement sur une
copie. Un preset de l'utilisateur (configuration hors du projet) n'est pas accessible ; un effet animé qu'un preset remplacerait est refusé.
"""

from __future__ import annotations

import copy

import pytest

from core.color_grading import (
    ColorGrade,
    ColorGradingService,
    LUTResource,
    builtin_color_presets,
)
from core.effects_library import apply_preset_to_clip, builtin_presets
from core.effects_model import EffectType, add_effect_to_clip
from core.graphics import GraphicOverlay, GraphicType
from core.keyframe_editing import add_keyframe
from core.project_model import Clip, MediaAsset, Project, Track
from core.scripting import ScriptInvalid, Session, apply_script, parse_script
from core.scripting.verbs import VERBS
from core.timeline_operations import find_clip

NEW_VERBS = ("apply_effect_preset", "apply_color_preset")
EFFECT_IDS = tuple(preset.id for preset in builtin_presets())
COLOR_IDS = tuple(preset.id for preset in builtin_color_presets())


def _project() -> Project:
    return Project(
        "Démo",
        width=320,
        height=180,
        fps=30.0,
        media_assets=[
            MediaAsset(
                "m", "/x/a.mp4", "a", 10.0, 320, 180, 30.0, "video", has_audio=True
            ),
            MediaAsset("v", "/x/v.wav", "v", 10.0, 0, 0, 0.0, "audio", has_audio=True),
        ],
        tracks=[
            Track("V1", "V1", "video", clips=[Clip("a", "m", "V1", 0.0, 0.0, 5.0)]),
            Track("A1", "A1", "audio", clips=[Clip("au", "v", "A1", 0.0, 0.0, 5.0)]),
        ],
    )


def _op(verb: str, /, **fields) -> dict:
    return {"op": verb, **fields}


def _apply(project: Project, *ops: dict):
    session = Session(project)
    return session, apply_script(
        session, parse_script({"kut_script": 1, "ops": list(ops)})
    )


def _chain(project: Project, clip_id: str = "a") -> list[tuple]:
    """La chaîne d'effets sans les identifiants (un preset en génère de neufs à chaque application)."""
    return [
        (effect.type, effect.enabled, tuple(sorted(effect.params.items())))
        for effect in find_clip(project, clip_id).effects
    ]


def _same_state(first: Project, second: Project) -> bool:
    return first.tracks == second.tracks


def _refused(project: Project, *ops: dict):
    before = copy.deepcopy(project.tracks)
    session, report = _apply(project, *ops)
    assert not report.ok
    assert session.project.tracks == before and not session.can_undo
    return report


# --- Forme -----------------------------------------------------------------------------------------------------------------


def test_the_verbs_are_in_the_table_with_the_right_shape():
    assert set(NEW_VERBS) <= set(VERBS)
    assert VERBS["apply_effect_preset"].params[1].choices == EFFECT_IDS
    assert VERBS["apply_color_preset"].params[1].choices == COLOR_IDS
    assert all(VERBS[name].creates is None and VERBS[name].doc for name in NEW_VERBS)
    assert len(EFFECT_IDS) >= 8 and len(COLOR_IDS) == 6


def test_the_json_schema_describes_both_verbs():
    from core.scripting.schema import json_schema

    titles = {
        item["title"] for item in json_schema()["properties"]["ops"]["items"]["oneOf"]
    }
    assert set(NEW_VERBS) <= titles


# --- Parité avec le modèle -------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("preset_id", EFFECT_IDS)
def test_apply_effect_preset_matches_the_model(preset_id):
    scripted = _project()
    _s, report = _apply(
        scripted, _op("apply_effect_preset", clip="a", preset=preset_id)
    )
    assert report.ok, report.error
    reference = _project()
    apply_preset_to_clip(
        reference, "a", next(item for item in builtin_presets() if item.id == preset_id)
    )
    assert _chain(scripted) == _chain(reference) != []


def test_an_effect_preset_replaces_a_single_instance_effect_and_stacks_the_others_like_the_model():
    def seeded() -> Project:
        project = _project()
        add_effect_to_clip(project, "a", EffectType.BLUR, params={"intensity": 3.0})
        add_effect_to_clip(project, "a", EffectType.SEPIA)
        return project

    for preset in builtin_presets():
        scripted, reference = seeded(), seeded()
        _s, report = _apply(
            scripted, _op("apply_effect_preset", clip="a", preset=preset.id)
        )
        assert report.ok, (preset.id, report.error)
        apply_preset_to_clip(reference, "a", preset)
        assert _chain(scripted) == _chain(reference), preset.id


@pytest.mark.parametrize("preset_id", COLOR_IDS)
def test_apply_color_preset_matches_the_model_and_keeps_the_lut(preset_id):
    def seeded() -> Project:
        project = _project()
        find_clip(project, "a").color_grade = ColorGrade.identity().with_lut(
            LUTResource(path="/x/look.cube", title="look", sha1="0" * 40)
        )
        return project

    scripted, reference = seeded(), seeded()
    _s, report = _apply(scripted, _op("apply_color_preset", clip="a", preset=preset_id))
    assert report.ok, report.error
    ColorGradingService().apply_preset(
        reference,
        "a",
        next(item for item in builtin_color_presets() if item.id == preset_id),
    )
    assert find_clip(scripted, "a").color_grade == find_clip(reference, "a").color_grade
    assert find_clip(scripted, "a").color_grade.lut is not None, (
        "le LUT du clip est gardé"
    )


def test_a_color_preset_goes_to_the_named_node_only():
    project = _project()
    _s, report = _apply(
        project,
        _op("add_color_node", clip="a", id="n2"),
        _op("apply_color_preset", clip="a", node="n2", preset=COLOR_IDS[1]),
    )
    assert report.ok, report.error
    graph = ColorGradingService().get_graph(project, "a")
    reference = next(
        item for item in builtin_color_presets() if item.id == COLOR_IDS[1]
    ).grade
    assert (
        graph.corrector("n2").grade == reference
        and graph.corrector("n1").grade != reference
    )


def test_apply_presets_end_to_end_with_undo_in_one_step():
    session, report = _apply(
        _project(),
        _op("add_effect", clip="a", type="blur", **{"as": "flou"}),
        _op("apply_effect_preset", clip="a", preset=EFFECT_IDS[0]),
        _op("apply_color_preset", clip="a", preset=COLOR_IDS[0]),
    )
    assert report.ok, report.error
    assert (
        find_clip(session.project, "a").effects
        and find_clip(session.project, "a").color_grade is not None
    )
    assert session.undo()
    assert not find_clip(session.project, "a").effects


# --- Presets de l'utilisateur et entrées hostiles -------------------------------------------------------------------------


@pytest.mark.parametrize("verb", NEW_VERBS)
@pytest.mark.parametrize(
    "preset",
    [
        "user-0123456789ab",
        "",
        "../../etc/passwd",
        "cinema ",
        None,
        3,
        ["cinema"],
        {"ref": "a"},
    ],
)
def test_only_builtin_presets_are_accepted(verb, preset):
    """Un preset utilisateur (identifiant `user-…`), un chemin ou un texte énorme ne sont pas des choix : refusés à la lecture."""
    with pytest.raises(ScriptInvalid) as caught:
        parse_script({"kut_script": 1, "ops": [_op(verb, clip="a", preset=preset)]})
    assert len(str(caught.value)) < 5_000, "le message ne recopie pas l'entrée"


@pytest.mark.parametrize("verb", NEW_VERBS)
def test_a_huge_preset_name_is_refused_without_being_copied_into_the_message(verb):
    with pytest.raises(ScriptInvalid) as caught:
        parse_script(
            {"kut_script": 1, "ops": [_op(verb, clip="a", preset="x" * 5_000_000)]}
        )
    assert len(str(caught.value)) < 5_000


@pytest.mark.parametrize("verb", NEW_VERBS)
def test_unknown_fields_and_missing_preset_are_refused(verb):
    for operation in (
        _op(verb, clip="a"),
        _op(verb, clip="a", preset=EFFECT_IDS[0], extra=1),
        _op(verb, preset=EFFECT_IDS[0]),
    ):
        with pytest.raises(ScriptInvalid):
            parse_script({"kut_script": 1, "ops": [operation]})


def test_unknown_clip_and_node_are_refused_cleanly():
    assert (
        "introuvable"
        in _refused(
            _project(), _op("apply_effect_preset", clip="nope", preset=EFFECT_IDS[0])
        ).error
    )
    assert (
        "introuvable"
        in _refused(
            _project(),
            _op("apply_color_preset", clip="a", node="nope", preset=COLOR_IDS[0]),
        ).error
    )


# --- Verrous et genres de piste -------------------------------------------------------------------------------------------

LOCK = _op("set_track_locked", track="V1", locked=True)


def test_a_locked_track_refuses_both_verbs_and_leaves_nothing():
    for operation in (
        _op("apply_effect_preset", clip="a", preset=EFFECT_IDS[0]),
        _op("apply_color_preset", clip="a", preset=COLOR_IDS[0]),
    ):
        report = _refused(_project(), LOCK, operation)
        assert "verrouillée" in report.error, operation["op"]


def test_a_locked_layer_refuses_both_verbs():
    for operation in (
        _op("apply_effect_preset", clip="a", preset=EFFECT_IDS[0]),
        _op("apply_color_preset", clip="a", preset=COLOR_IDS[0]),
    ):
        project = _project()
        find_clip(project, "a").graphic = GraphicOverlay(
            type=GraphicType.TEXT, locked=True
        )
        report = _refused(project, operation)
        assert "verrouillé" in report.error, operation["op"]


def test_effect_presets_go_on_video_clips_only_and_color_presets_not_on_audio():
    assert (
        "cette opération vise video"
        in _refused(
            _project(), _op("apply_effect_preset", clip="au", preset=EFFECT_IDS[0])
        ).error
    )
    assert (
        "cette opération vise"
        in _refused(
            _project(), _op("apply_color_preset", clip="au", preset=COLOR_IDS[0])
        ).error
    )


# --- Effets animés : rien n'est perdu en silence ---------------------------------------------------------------------------


def _animated() -> Project:
    project = _project()
    add_effect_to_clip(
        project, "a", EffectType.VIGNETTE, effect_id="fxv", params={"intensity": 0.5}
    )
    add_keyframe(project, "a", "fx.fxv.intensity", 0.5, 0.2)
    return project


def _vignette_presets() -> list[str]:
    return [
        preset.id
        for preset in builtin_presets()
        if any(effect.type == EffectType.VIGNETTE for effect in preset.effects)
    ]


def test_a_preset_that_would_replace_an_animated_effect_is_refused_while_the_model_drops_its_keyframes():
    ids = _vignette_presets()
    assert ids, "il existe un preset intégré à vignette"
    for preset_id in ids:
        reference = _animated()
        apply_preset_to_clip(
            reference,
            "a",
            next(item for item in builtin_presets() if item.id == preset_id),
        )
        assert not find_clip(reference, "a").animation, (
            "le modèle (donc l'interface) perd les images clés sans rien dire"
        )
        report = _refused(
            _animated(), _op("apply_effect_preset", clip="a", preset=preset_id)
        )
        assert "images clés" in report.error and "fxv" in report.error


def test_a_preset_without_a_conflicting_type_keeps_animation_and_a_stackable_effect_is_never_a_conflict():
    other = next(
        preset
        for preset in builtin_presets()
        if not any(effect.type == EffectType.VIGNETTE for effect in preset.effects)
    )
    project = _animated()
    _s, report = _apply(project, _op("apply_effect_preset", clip="a", preset=other.id))
    assert report.ok, report.error
    assert find_clip(project, "a").animation, "l'animation de la vignette reste"


def test_an_animated_effect_of_another_clip_does_not_block():
    project = _animated()
    project.tracks[0].clips.append(Clip("b", "m", "V1", 6.0, 0.0, 2.0))
    _s, report = _apply(
        project, _op("apply_effect_preset", clip="b", preset=_vignette_presets()[0])
    )
    assert report.ok, report.error
    assert find_clip(project, "a").animation, "l'animation du clip voisin est intacte"


def test_a_disabled_animated_effect_is_still_protected():
    project = _animated()
    _s, report = _apply(
        project,
        _op("set_effect_enabled", clip="a", effect="fxv", enabled=False),
        _op("apply_effect_preset", clip="a", preset=_vignette_presets()[0]),
    )
    assert not report.ok and "images clés" in report.error
