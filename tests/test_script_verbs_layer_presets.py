"""Format déclaratif : presets de calques et animations de texte (lot 4 de l'extension 0.8.0) : `apply_layer_preset`, `apply_text_animation`.

Les deux verbes font ce que la bibliothèque de presets et la section « Animation du texte » font. Seuls les presets **intégrés** sont
accessibles (ceux de l'utilisateur sont des fichiers de sa configuration, hors du dossier du script) ; `save_preset` n'est pas un verbe.
"""

from __future__ import annotations

import copy
from dataclasses import replace

import pytest

from core import mograph_layers as layers
from core.graphics import GraphicType
from core.mograph_layers import add_layer
from core.mograph_presets import apply_preset, builtin_preset_names, builtin_presets
from core.project_model import Clip, MediaAsset, Project, Track
from core.scripting import ScriptInvalid, Session, apply_script, created_ids, parse_script
from core.scripting import layer_rules
from core.scripting.schema import json_schema
from core.scripting.verbs import VERBS
from core.text_animations import TEXT_ANIMATIONS, apply_text_animation
from core.timeline_operations import find_clip
from core.visual_effects import ClipTransform, TransformKeyframe
from ui import i18n


@pytest.fixture(autouse=True)
def _french(monkeypatch):
    monkeypatch.setenv("KUT_STUDIO_LANG", "fr")
    i18n.reset_for_tests()
    yield
    i18n.reset_for_tests()


def _project(width: int = 320, height: int = 180, *, locked_graphics: bool = False) -> Project:
    project = Project(
        "Démo", width=width, height=height, fps=30.0,
        media_assets=[MediaAsset("m", "/x/a.mp4", "a", 10.0, width, height, 30.0, "video")],
        tracks=[
            Track("V1", "V1", "video", clips=[Clip("v", "m", "V1", 0.0, 0.0, 6.0)]),
            Track("G1", "G1", "graphics", locked=locked_graphics),
            Track("G2", "G2", "graphics", locked=True),
        ],
    )
    add_layer(project, "text", at=0.0, duration=4.0, track=project.tracks[1], clip_id="titre")
    title = find_clip(project, "titre")
    title.graphic = replace(title.graphic, text="un deux trois")
    add_layer(project, "shape", at=0.0, duration=4.0, track=project.tracks[1], clip_id="forme")
    add_layer(project, "text", at=0.0, duration=2.0, track=project.tracks[2], clip_id="verrou")
    return project


def _op(verb: str, /, **fields) -> dict:
    return {"op": verb, **fields}


def _apply(project: Project, *ops: dict):
    session = Session(project)
    return session, apply_script(session, parse_script({"kut_script": 1, "ops": list(ops)}))


def _snapshot(project: Project):
    return (copy.deepcopy(project.tracks), copy.deepcopy(project.media_assets))


def _refused(project: Project, *ops: dict, match: str | None = None):
    before = _snapshot(project)
    session, report = _apply(project, *ops)
    assert not report.ok, "le script aurait dû être refusé"
    assert _snapshot(session.project) == before
    if match is not None:
        assert match in report.error, report.error
    return report


def _invalid(*ops: dict) -> None:
    with pytest.raises(ScriptInvalid):
        parse_script({"kut_script": 1, "ops": list(ops)})


def _shape(project: Project):
    """Le contenu d'un projet sans les identifiants générés (clips, images clés) : ce qui doit être identique entre le script et l'appel direct."""
    rows = [
        (t.id, c.graphic.z_order, c.label, c.graphic.type, c.timeline_start, c.duration, c.transform,
         tuple((k.property_name, k.time_seconds, k.value, k.interpolation) for k in c.transform_keyframes),
         tuple((k.property_name, k.time_seconds, k.value) for k in c.animation), c.graphic.text, c.graphic.width, c.graphic.height)
        for t in project.tracks for c in t.clips if t.type == "graphics"
    ]
    return sorted(rows, key=lambda row: (row[0], row[1]))


# --- Presets -----------------------------------------------------------------------------------------------------------


def test_the_preset_verb_is_in_the_table_and_the_schema_and_save_preset_is_not_a_verb():
    verb = VERBS["apply_layer_preset"]
    assert verb.creates == "clip" and verb.group == "graphics" and "id" in verb.accepts
    assert set(next(p for p in verb.params if p.name == "preset").choices) == set(builtin_preset_names())
    titles = {item["title"] for item in json_schema()["properties"]["ops"]["items"]["oneOf"]}
    assert "apply_layer_preset" in titles and "save_preset" not in VERBS and "save_preset" not in titles
    assert "apply_text_animation" in titles and VERBS["apply_text_animation"].creates is None


@pytest.mark.parametrize("name", builtin_preset_names())
def test_every_builtin_preset_matches_the_library(name):
    scripted = _project()
    session, report = _apply(scripted, _op("apply_layer_preset", preset=name, at=1.5, id="principal"))
    assert report.ok, report.error
    reference = _project()
    preset = next(item for item in builtin_presets(reference.width, reference.height) if item.name == name)
    created = apply_preset(reference, preset, at=1.5, main_id="principal")
    assert _shape(session.project) == _shape(reference)
    assert len(created) >= 1 and find_clip(session.project, "principal").timeline_start == 1.5
    assert not find_clip(session.project, "principal").graphic.group_id


def test_the_main_layer_is_the_first_one_outside_any_group():
    session, report = _apply(_project(), _op("apply_layer_preset", preset="Lower third", at=0, id="bandeau"))
    assert report.ok
    assert find_clip(session.project, "bandeau").graphic.type == GraphicType.GROUP
    session, report = _apply(_project(), _op("apply_layer_preset", preset="Call-out", at=0, id="callout"))
    assert find_clip(session.project, "callout").graphic.type == GraphicType.NULL


def test_a_preset_is_rebuilt_on_the_canvas_of_the_project():
    horizontal, _ = _apply(_project(1920, 1080), _op("apply_layer_preset", preset="Lower third", at=0, id="b"))
    vertical, _ = _apply(_project(1080, 1920), _op("apply_layer_preset", preset="Lower third", at=0, id="b"))
    widths = lambda s: sorted(c.graphic.width for t in s.project.tracks for c in t.clips if c.graphic and c.label == "Bandeau")  # noqa: E731
    assert widths(horizontal) == [720] and widths(vertical) == [860]


def test_the_preset_layer_can_be_cited_by_alias_end_to_end():
    project = _project()
    script = parse_script({"kut_script": 1, "ops": [
        _op("apply_layer_preset", preset="Lower third", at=1, **{"as": "bandeau"}),
        _op("rename_layer", clip={"ref": "bandeau"}, name="Mon bandeau"),
        _op("set_layer_visible", clip={"ref": "bandeau"}, visible=False),
    ]})
    session = Session(project)
    report = apply_script(session, script)
    assert report.ok, report.error
    assert created_ids(script, report) == [{"op": "0:apply_layer_preset", "id": "bandeau"}]
    assert find_clip(session.project, "bandeau").label == "Mon bandeau"


def test_a_preset_without_id_returns_a_generated_main_layer():
    session, report = _apply(_project(), _op("apply_layer_preset", preset="Titre simple", at=0))
    assert report.ok and report.results[0].value.id.startswith("graphic-")


def test_preset_errors():
    _refused(_project(), _op("apply_layer_preset", preset="Titre simple", at=0, id="titre"), match="existe déjà")
    taken = _project()
    taken.media_assets.append(MediaAsset("graphic-asset-neuf", "", "x", 1.0, 1, 1, 30.0, "graphic"))
    _refused(taken, _op("apply_layer_preset", preset="Titre simple", at=0, id="neuf"), match="existe déjà")
    _invalid(_op("apply_layer_preset", preset="Mon preset", at=0))                       # un preset de l'utilisateur n'est pas accessible
    _invalid(_op("apply_layer_preset", preset="Titre simple"))
    _invalid(_op("apply_layer_preset", preset="Titre simple", at=-1))
    _invalid(_op("apply_layer_preset", preset="Titre simple", at=float("nan")))
    _invalid(_op("apply_layer_preset", preset="Titre simple", at=0, id="a b"))


def test_a_preset_never_writes_into_a_locked_graphics_track():
    project = _project(locked_graphics=True)                                             # G1 et G2 sont verrouillées
    before = {t.id: copy.deepcopy(t.clips) for t in project.tracks if t.locked}
    session, report = _apply(project, _op("apply_layer_preset", preset="Titre simple", at=0, id="titre2"))
    assert report.ok, report.error
    assert {t.id: t.clips for t in session.project.tracks if t.id in before} == before
    assert next(t for t in session.project.tracks if any(c.id == "titre2" for c in t.clips)).locked is False


def test_a_preset_stays_under_the_project_layer_limit(monkeypatch):
    monkeypatch.setattr(layer_rules, "MAX_LAYERS_IN_PROJECT", 4)                          # titre, forme, verrou : 3
    _refused(_project(), _op("apply_layer_preset", preset="Lower third", at=0), match="limite")
    session, report = _apply(_project(), _op("apply_layer_preset", preset="Titre simple", at=0))
    assert report.ok


def test_a_preset_is_one_undo_step():
    project = _project()
    before = _snapshot(project)
    session, report = _apply(project, _op("apply_layer_preset", preset="Call-out", at=0), _op("apply_layer_preset", preset="Titre simple", at=1))
    assert report.ok and session.undo() and _snapshot(session.project) == before


# --- Animations de texte -----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("animation", TEXT_ANIMATIONS)
def test_every_text_animation_matches_the_model(animation):
    scripted = _project()
    session, report = _apply(scripted, _op("apply_text_animation", clip="titre", animation=animation, start=0.5))
    assert report.ok, report.error
    reference = _project()
    apply_text_animation(find_clip(reference, "titre"), animation, start=0.5)
    assert find_clip(session.project, "titre") == find_clip(reference, "titre")


def test_the_default_start_is_zero_like_the_interface():
    scripted = _project()
    session, report = _apply(scripted, _op("apply_text_animation", clip="titre", animation="word_by_word"))
    assert report.ok
    reference = _project()
    apply_text_animation(find_clip(reference, "titre"), "word_by_word")
    assert find_clip(session.project, "titre") == find_clip(reference, "titre")
    assert find_clip(session.project, "titre").graphic.word_reveal == "word"


def test_an_animation_replaces_the_keyframes_it_animates_and_keeps_the_others():
    project = _project()
    clip = find_clip(project, "titre")
    clip.transform = ClipTransform(scale=2.0, opacity=0.8)
    clip.transform_keyframes = [TransformKeyframe("rotation", 0.0, 0.0), TransformKeyframe("rotation", 2.0, 90.0),
                                TransformKeyframe("scale", 0.0, 5.0)]
    session, report = _apply(project, _op("apply_text_animation", clip="titre", animation="pop_in"))
    assert report.ok, report.error
    keyframes = find_clip(session.project, "titre").transform_keyframes
    assert [kf.value for kf in keyframes if kf.property_name == "rotation"] == [0.0, 90.0]            # intactes
    scale = [kf.value for kf in keyframes if kf.property_name == "scale"]
    assert scale[0] == pytest.approx(0.6) and scale[-1] == 2.0                                           # celles de l'ancien jeu ont disparu


def test_text_animation_only_exists_for_unlocked_text_layers():
    _refused(_project(), _op("apply_text_animation", clip="forme", animation="pop_in"), match="calque texte")
    _refused(_project(), _op("apply_text_animation", clip="v", animation="pop_in"))
    _refused(_project(), _op("apply_text_animation", clip="verrou", animation="pop_in"))             # piste verrouillée
    locked = _project()
    layers.set_layer_locked(locked, "titre", True)
    _refused(locked, _op("apply_text_animation", clip="titre", animation="pop_in"), match="verrouillé")
    _refused(_project(), _op("apply_text_animation", clip="fantome", animation="pop_in"))


def test_text_animation_start_bounds():
    _invalid(_op("apply_text_animation", clip="titre", animation="pop_in", start=-0.1))
    _invalid(_op("apply_text_animation", clip="titre", animation="pop_in", start=float("nan")))
    _invalid(_op("apply_text_animation", clip="titre", animation="pop_in", start=float("inf")))
    _invalid(_op("apply_text_animation", clip="titre", animation="inconnue"))
    _invalid(_op("apply_text_animation", clip="titre"))
    _refused(_project(), _op("apply_text_animation", clip="titre", animation="pop_in", start=4.0), match="trop court")
    _refused(_project(), _op("apply_text_animation", clip="titre", animation="pop_in", start=5.0), match="trop court")
    _refused(_project(), _op("apply_text_animation", clip="titre", animation="pop_in", start=3.999999), match="trop court")
    session, report = _apply(_project(), _op("apply_text_animation", clip="titre", animation="pop_in", start=3.9))
    assert report.ok and len({kf.time_seconds for kf in find_clip(session.project, "titre").transform_keyframes}) >= 2


def test_the_bounce_is_bounded_so_a_huge_layer_cannot_make_millions_of_keyframes():
    project = _project()
    find_clip(project, "titre").source_out = 1_000_000.0
    _refused(project, _op("apply_text_animation", clip="titre", animation="bounce"), match="au plus 500")
    find_clip(project, "titre").source_out = 100.0
    session, report = _apply(project, _op("apply_text_animation", clip="titre", animation="bounce"))
    assert report.ok and len(find_clip(session.project, "titre").transform_keyframes) <= 401
    # les autres animations ne dépendent pas de la durée
    huge = _project()
    find_clip(huge, "titre").source_out = 1_000_000.0
    _session, done = _apply(huge, _op("apply_text_animation", clip="titre", animation="karaoke"))
    assert done.ok


def test_text_animation_is_atomic_and_undoable():
    project = _project()
    before = _snapshot(project)
    session, report = _apply(project, _op("apply_text_animation", clip="titre", animation="pop_in"),
                             _op("apply_text_animation", clip="forme", animation="pop_in"))
    assert not report.ok and _snapshot(session.project) == before
    session, report = _apply(project, _op("apply_text_animation", clip="titre", animation="pop_in"))
    assert report.ok and _snapshot(session.project) != before and session.undo() and _snapshot(session.project) == before
