"""Format déclaratif : compositions nodales (lot 10 de l'extension 0.8.0) : `create_composition`, `convert_to_composition`.

Chaque verbe appelle la fonction que la page Composition de l'interface appelle (``core.composition_ops``) : le test de parité fait le même
appel directement sur une copie. Les refus du modèle (effets animés ``fx.*``, imbriqué, vitesse, suivi…) et ceux du script (calque verrouillé,
transitions et réglages audio que la conversion perdrait, bornes, plafond de clips, identifiants) ne laissent rien derrière eux.
"""

from __future__ import annotations

import copy

import pytest

from core.composition import MAX_DURATION
from core.composition_ops import convert_to_composition, create_composition
from core.effects_model import EffectType, add_effect_to_clip
from core.graphics import GraphicOverlay, GraphicType, add_graphic_clip
from core.keyframe_editing import add_keyframe
from core.project_model import Clip, MediaAsset, Project, Sequence, Track
from core.scripting import (
    ScriptInvalid,
    Session,
    apply_script,
    created_ids,
    parse_script,
)
from core.scripting import resource_limits
from core.scripting.describe import describe
from core.scripting.verbs import VERBS
from core.timeline_operations import find_clip
from core.transitions import TransitionType, add_transition

NEW_VERBS = ("create_composition", "convert_to_composition")


def _project() -> Project:
    project = Project(
        "Démo",
        width=320,
        height=180,
        fps=30.0,
        media_assets=[
            MediaAsset(
                "m", "/x/a.mp4", "a", 20.0, 320, 180, 30.0, "video", has_audio=True
            )
        ],
        tracks=[
            Track(
                "V1",
                "V1",
                "video",
                clips=[
                    Clip("a", "m", "V1", 0.0, 0.0, 5.0),
                    Clip("b", "m", "V1", 5.0, 0.0, 4.0),
                ],
            ),
            Track("V2", "V2", "video", clips=[Clip("c", "m", "V2", 1.0, 0.0, 2.0)]),
            Track("V3", "V3", "video"),
            Track("A1", "A1", "audio"),
        ],
    )
    add_graphic_clip(
        project, GraphicType.TEXT, timeline_start=0.5, duration=3.0, clip_id="g"
    )
    return project


def _op(verb: str, /, **fields) -> dict:
    return {"op": verb, **fields}


def _apply(project: Project, *ops: dict):
    session = Session(project)
    return session, apply_script(
        session, parse_script({"kut_script": 1, "ops": list(ops)})
    )


def _state(project: Project):
    return (copy.deepcopy(project.sequences), copy.deepcopy(project.media_assets))


def _refused(project: Project, *ops: dict):
    before = _state(project)
    session, report = _apply(project, *ops)
    assert not report.ok
    assert _state(session.project) == before and not session.can_undo
    return report


# --- Forme -----------------------------------------------------------------------------------------------------------------


def test_the_verbs_are_in_the_table_with_the_right_shape():
    assert set(NEW_VERBS) <= set(VERBS)
    for name in NEW_VERBS:
        assert (
            VERBS[name].creates == "clip"
            and {"id", "as"} <= set(VERBS[name].accepts)
            and VERBS[name].doc
        )


def test_the_json_schema_describes_both_verbs():
    from core.scripting.schema import json_schema

    titles = {
        item["title"] for item in json_schema()["properties"]["ops"]["items"]["oneOf"]
    }
    assert set(NEW_VERBS) <= titles


# --- create_composition ----------------------------------------------------------------------------------------------------


def test_create_composition_matches_the_model_and_uses_the_given_id():
    scripted = _project()
    _s, report = _apply(
        scripted,
        _op(
            "create_composition",
            track="V3",
            at=2.0,
            duration=4.0,
            name="Titre",
            id="comp_a",
        ),
    )
    assert report.ok, report.error
    reference = _project()
    create_composition(reference, "V3", 2.0, 4.0, "Titre", clip_id="comp_a")
    assert scripted.sequences == reference.sequences
    clip = find_clip(scripted, "comp_a")
    assert (
        clip.composition is not None and clip.label == "Titre" and clip.duration == 4.0
    )


def test_the_ui_path_is_unchanged_without_a_clip_id():
    """Sans `clip_id` (l'interface) le modèle génère l'identifiant comme avant ; deux appels donnent deux identifiants distincts."""
    project = _project()
    first = create_composition(project, "V3", 0.0, 1.0)
    second = create_composition(project, "V3", 2.0, 1.0)
    assert (
        first.id.startswith("comp-")
        and second.id.startswith("comp-")
        and first.id != second.id
    )
    converted = convert_to_composition(project, ["c"])
    assert converted.clip.id.startswith("comp-")


def test_create_without_an_id_generates_one_and_a_name_defaults():
    session, report = _apply(
        _project(), _op("create_composition", track="V3", at=0.0, duration=1.0)
    )
    assert report.ok, report.error
    clip = report.results[0].value
    assert clip.id.startswith("comp-") and clip.label == "Composition"


def test_create_then_reference_the_composition_by_alias_and_undo_in_one_step():
    session, report = _apply(
        _project(),
        _op("create_composition", track="V3", at=0.0, duration=2.0, **{"as": "fond"}),
        _op("move_clip", clip={"ref": "fond"}, at=5.0),
        _op("set_clip_enabled", clip={"ref": "fond"}, enabled=False),
    )
    assert report.ok, report.error
    clip = find_clip(session.project, "fond")
    assert clip.timeline_start == 5.0 and not clip.enabled
    assert (
        created_ids(
            parse_script(
                {
                    "kut_script": 1,
                    "ops": [
                        _op(
                            "create_composition",
                            track="V3",
                            at=0.0,
                            duration=2.0,
                            **{"as": "fond"},
                        )
                    ],
                }
            ),
            _apply(
                _project(),
                _op(
                    "create_composition",
                    track="V3",
                    at=0.0,
                    duration=2.0,
                    **{"as": "fond"},
                ),
            )[1],
        )[0]["id"]
        == "fond"
    )
    assert session.undo() and not any(
        clip.composition for track in session.project.tracks for clip in track.clips
    )


def test_create_is_described_as_a_composition():
    session, report = _apply(
        _project(),
        _op("create_composition", track="V3", at=0.0, duration=2.0, id="fond"),
    )
    assert report.ok
    clips = [
        clip
        for track in describe(session.project)["sequences"][0]["tracks"]
        for clip in track["clips"]
    ]
    assert (
        next(clip for clip in clips if clip["id"] == "fond")["composition"] is not None
    )


@pytest.mark.parametrize(
    "operation",
    [
        _op("create_composition", track="V3", at=0.0, duration=0.0),
        _op(
            "create_composition", track="V3", at=0.0, duration=0.03
        ),  # le modèle ramènerait à 0,04 s
        _op("create_composition", track="V3", at=0.0, duration=MAX_DURATION + 1),
        _op("create_composition", track="V3", at=0.0, duration=float("nan")),
        _op(
            "create_composition", track="V3", at=-1.0, duration=2.0
        ),  # le modèle ramènerait à 0
        _op("create_composition", track="V3", at=0.0, duration=2.0, id="mauvais id"),
        _op("create_composition", track="V3", at=0.0, duration=2.0, name=" "),
        _op(
            "create_composition", track="V3", at=0.0, duration=2.0, name="x" * 5_000_000
        ),
    ],
)
def test_bounds_are_refused_at_reading(operation):
    with pytest.raises(ScriptInvalid) as caught:
        parse_script({"kut_script": 1, "ops": [operation]})
    assert len(str(caught.value)) < 5_000


def test_the_exact_bounds_are_accepted():
    for duration in (0.04, MAX_DURATION):
        _s, report = _apply(
            _project(), _op("create_composition", track="V3", at=0.0, duration=duration)
        )
        assert report.ok, (duration, report.error)


def test_create_refuses_locked_audio_unknown_and_busy_tracks():
    assert (
        "verrouillée"
        in _refused(
            _project(),
            _op("set_track_locked", track="V3", locked=True),
            _op("create_composition", track="V3", at=0.0, duration=1.0),
        ).error
    )
    assert (
        "piste vidéo"
        in _refused(
            _project(), _op("create_composition", track="A1", at=0.0, duration=1.0)
        ).error
    )
    assert (
        "introuvable"
        in _refused(
            _project(), _op("create_composition", track="nope", at=0.0, duration=1.0)
        ).error
    )
    assert (
        "pas libre"
        in _refused(
            _project(), _op("create_composition", track="V1", at=2.0, duration=1.0)
        ).error
    )


def test_create_refuses_a_taken_id_in_any_sequence_without_leaving_anything():
    project = _project()
    project.sequences.append(
        Sequence(
            "seq-b",
            "B",
            width=320,
            height=180,
            fps=30.0,
            tracks=[
                Track(
                    "V1", "V1", "video", clips=[Clip("autre", "m", "V1", 0.0, 0.0, 1.0)]
                )
            ],
        )
    )
    for taken in ("a", "g", "autre"):
        assert (
            "existe déjà"
            in _refused(
                project,
                _op("create_composition", track="V3", at=0.0, duration=1.0, id=taken),
            ).error
        )


def test_create_counts_the_clips_of_every_sequence_before_creating(monkeypatch):
    project = _project()
    project.sequences.append(
        Sequence(
            "seq-b",
            "B",
            width=320,
            height=180,
            fps=30.0,
            tracks=[
                Track(
                    "V1",
                    "V1",
                    "video",
                    clips=[
                        Clip(f"x{i}", "m", "V1", float(i), 0.0, 1.0) for i in range(5)
                    ],
                )
            ],
        )
    )
    total = resource_limits.total_clips(project)
    assert total == 4 + 5 + 0 or total > 5, total
    monkeypatch.setattr(resource_limits, "MAX_CLIPS_IN_PROJECT", total)
    assert (
        "dépasseraient la limite"
        in _refused(
            project, _op("create_composition", track="V3", at=0.0, duration=1.0)
        ).error
    )
    monkeypatch.setattr(resource_limits, "MAX_CLIPS_IN_PROJECT", total + 1)
    assert _apply(project, _op("create_composition", track="V3", at=0.0, duration=1.0))[
        1
    ].ok


# --- convert_to_composition ------------------------------------------------------------------------------------------------


def test_convert_matches_the_model_for_video_clips_and_a_graphic_layer():
    scripted = _project()
    _s, report = _apply(
        scripted,
        _op("convert_to_composition", clips=["a", "c", "g"], name="Mix", id="mix"),
    )
    assert report.ok, report.error
    reference = _project()
    convert_to_composition(reference, ["a", "c", "g"], "Mix", clip_id="mix")
    assert scripted.sequences == reference.sequences
    composed = find_clip(scripted, "mix")
    assert (
        composed.composition is not None
        and composed.label == "Mix"
        and composed.track_id == "V1"
    )
    ids = {clip.id for track in scripted.tracks for clip in track.clips}
    assert not ids & {"a", "c", "g"}, "les clips d'origine sont remplacés"


def test_convert_a_full_frame_background_alone_and_an_already_aligned_clip():
    project = _project()
    _s, report = _apply(project, _op("convert_to_composition", clips=["b"], id="fond"))
    assert report.ok, report.error
    clip = find_clip(project, "fond")
    assert clip.timeline_start == 5.0 and clip.duration == 4.0


def test_convert_a_rotated_animated_layer_keeps_the_models_result():
    def seeded() -> Project:
        project = _project()
        add_keyframe(project, "a", "scale", 0.0, 1.0)
        add_keyframe(project, "a", "scale", 1.0, 1.5)
        find_clip(project, "a").transform = find_clip(project, "a").transform.__class__(
            rotation=15.0
        )
        return project

    scripted, reference = seeded(), seeded()
    assert _apply(scripted, _op("convert_to_composition", clips=["a"], id="r"))[1].ok
    convert_to_composition(reference, ["a"], clip_id="r")
    assert scripted.sequences == reference.sequences


def test_convert_then_use_the_composition_by_alias_and_undo():
    session, report = _apply(
        _project(),
        _op("convert_to_composition", clips=["a", "c"], **{"as": "mix"}),
        _op("move_clip", clip={"ref": "mix"}, at=20.0),
    )
    assert report.ok, report.error
    assert find_clip(session.project, "mix").timeline_start == 20.0
    assert session.undo()
    assert {clip.id for track in session.project.tracks for clip in track.clips} >= {
        "a",
        "c",
        "g",
    }


def test_convert_refuses_live_effect_animation_like_the_model_and_accepts_it_once_removed():
    def animated() -> Project:
        project = _project()
        add_effect_to_clip(
            project,
            "a",
            EffectType.VIGNETTE,
            effect_id="fxv",
            params={"intensity": 0.5},
        )
        add_keyframe(project, "a", "fx.fxv.intensity", 0.5, 0.2)
        return project

    from core.composition_ops import CompositionError

    with pytest.raises(CompositionError):
        convert_to_composition(animated(), ["a"])
    report = _refused(animated(), _op("convert_to_composition", clips=["a"]))
    assert "effets animés" in report.error
    still_ok = animated()
    assert _apply(
        still_ok,
        _op("remove_effect", clip="a", effect="fxv"),
        _op("convert_to_composition", clips=["a"], id="ok"),
    )[1].ok
    plain = _project()
    add_effect_to_clip(plain, "a", EffectType.BLUR, params={"intensity": 3.0})
    assert _apply(plain, _op("convert_to_composition", clips=["a"]))[1].ok, (
        "un effet non animé se convertit"
    )


def test_convert_refuses_what_the_model_refuses():
    nested = _project()
    nested.sequences.append(
        Sequence(
            "seq-b",
            "B",
            width=320,
            height=180,
            fps=30.0,
            tracks=[Track("V1", "V1", "video")],
        )
    )
    find_clip(nested, "c").sequence_id = "seq-b"
    assert (
        "imbriquée"
        in _refused(nested, _op("convert_to_composition", clips=["c"])).error
    )
    slow = _project()
    assert _apply(slow, _op("set_clip_speed", clip="a", speed=2.0))[1].ok
    assert "vitesse" in _refused(slow, _op("convert_to_composition", clips=["a"])).error
    assert (
        "pas de clip vidéo"
        in _refused(_project(), _op("convert_to_composition", clips=["g"])).error
    )


def test_convert_refuses_other_clips_on_the_host_track_in_the_range():
    project = _project()
    project.tracks[0].clips.append(
        Clip("d", "m", "V1", 3.0, 0.0, 1.0)
    )  # entre a (0-5) : chevauche
    assert (
        "autres clips"
        in _refused(project, _op("convert_to_composition", clips=["a", "c"])).error
    )
    clean = _project()
    host_free = _apply(clean, _op("convert_to_composition", clips=["c"]))
    assert (
        host_free[1].ok
        and host_free[0].project.tracks[1].clips[0].composition is not None
    )


def test_convert_refuses_locked_tracks_and_locked_layers():
    assert (
        "verrouillée"
        in _refused(
            _project(),
            _op("set_track_locked", track="V2", locked=True),
            _op("convert_to_composition", clips=["a", "c"]),
        ).error
    )
    project = _project()
    find_clip(project, "g").graphic = GraphicOverlay(
        type=GraphicType.TEXT, locked=True
    )  # le modèle ne regarde que le verrou de piste
    assert (
        "verrouillé"
        in _refused(project, _op("convert_to_composition", clips=["a", "g"])).error
    )
    photo = _project()
    find_clip(photo, "c").graphic = GraphicOverlay(
        type=GraphicType.TEXT, locked=True
    )  # clip vidéo dont le calque (photo de template) est verrouillé
    assert (
        "verrouillé"
        in _refused(photo, _op("convert_to_composition", clips=["c"])).error
    )
    audio = _project()
    audio.tracks[3].clips.append(Clip("au", "m", "A1", 0.0, 0.0, 1.0))
    assert (
        "cette opération vise"
        in _refused(audio, _op("convert_to_composition", clips=["au"])).error
    )


def test_convert_refuses_what_it_would_drop_silently():
    transitioned = _project()
    add_transition(
        transitioned, "a", "b", TransitionType.CROSSFADE, 0.5, transition_id="t1"
    )
    report = _refused(transitioned, _op("convert_to_composition", clips=["a"]))
    assert "t1" in report.error and "remove_transition" in report.error
    assert _apply(
        transitioned,
        _op("remove_transition", transition="t1"),
        _op("convert_to_composition", clips=["a", "b"]),
    )[1].ok
    for field, value in (("pan", 0.5), ("fade_in", 0.5), ("fade_out", 0.5)):
        project = _project()
        setattr(find_clip(project, "a"), field, value)
        assert (
            "panoramique"
            in _refused(project, _op("convert_to_composition", clips=["a"])).error
        ), field
    # Un clip non sélectionné garde ses réglages et sa transition : rien ne l'empêche.
    other = _project()
    add_transition(other, "a", "b", TransitionType.CROSSFADE, 0.5, transition_id="t1")
    find_clip(other, "c").pan = 0.5
    assert "t1" in _refused(other, _op("convert_to_composition", clips=["b"])).error


def test_convert_list_bounds_duplicates_and_unknowns():
    assert (
        "plusieurs fois"
        in _refused(_project(), _op("convert_to_composition", clips=["a", "a"])).error
    )
    assert (
        "introuvable"
        in _refused(
            _project(), _op("convert_to_composition", clips=["a", "nope"])
        ).error
    )
    for clips in (
        [],
        ["a"] * 201,
        [str(index) for index in range(201)],
        "a",
        [3],
        [None],
        [{"ref": "x"}],
    ):
        with pytest.raises(ScriptInvalid):
            parse_script(
                {"kut_script": 1, "ops": [_op("convert_to_composition", clips=clips)]}
            )
    # Les 200 permis sont lus ; le modèle les refuse ensuite pour ce qu'ils sont (introuvables), sans rien écrire.
    parse_script(
        {
            "kut_script": 1,
            "ops": [
                _op(
                    "convert_to_composition",
                    clips=[f"x{index}" for index in range(200)],
                )
            ],
        }
    )


def test_convert_refuses_a_taken_or_malformed_id_before_touching_anything():
    for bad in ("a", "b", "g", "mauvais id", "x" * 70):
        try:
            report = _refused(
                _project(), _op("convert_to_composition", clips=["c"], id=bad)
            )
        except ScriptInvalid:
            continue
        assert "existe déjà" in report.error or "invalide" in report.error


def test_convert_can_reuse_the_id_of_a_converted_clip_only_if_it_is_free():
    """Un identifiant déjà pris reste refusé même s'il est celui d'un clip qui disparaît avec la conversion (règle simple et stricte)."""
    assert (
        "existe déjà"
        in _refused(
            _project(), _op("convert_to_composition", clips=["c"], id="c")
        ).error
    )


def _direct(project: Project, **args):
    """Appelle l'opération du verbe SANS la session : le projet doit rester strictement inchangé après le refus."""
    before = _state(project)
    with pytest.raises(ValueError) as caught:
        VERBS["convert_to_composition"].build(args)(project)
    assert _state(project) == before
    return str(caught.value)


def test_convert_refuses_what_the_composition_would_reveal_or_unmute():
    cases = {
        "désactivé": lambda p: setattr(find_clip(p, "a"), "enabled", False),
        "masquée": lambda p: setattr(p.tracks[0], "visible", False),
        "muette": lambda p: setattr(p.tracks[0], "muted", True),
        "calque masqué": lambda p: setattr(
            find_clip(p, "g"),
            "graphic",
            __import__("dataclasses").replace(find_clip(p, "g").graphic, visible=False),
        ),
        "solo": lambda p: setattr(
            p.tracks[1], "solo", True
        ),  # a (V1) est masqué par le solo de V2
    }
    for name, change in cases.items():
        project = _project()
        change(project)
        message = _direct(project, clips=["a", "g"], name=None)
        assert "composition" in message, name
        assert _refused_script(change, ["a", "g"]), name


def _refused_script(change, clips) -> bool:
    project = _project()
    change(project)
    return not _apply(project, _op("convert_to_composition", clips=clips))[1].ok


def test_convert_refuses_a_layer_hidden_by_the_solo_of_another_graphics_track():
    project = _project()
    project.tracks.append(Track("G2", "G2", "graphics", solo=True))
    assert "solo" in _direct(project, clips=["a", "g"])
    only_solo = _project()
    only_solo.tracks[1].solo = True
    assert _apply(only_solo, _op("convert_to_composition", clips=["c"], id="solo_ok"))[
        1
    ].ok, "c est visible (V2 en solo)"


def test_convert_still_accepts_enabled_visible_clips():
    assert _apply(
        _project(), _op("convert_to_composition", clips=["a", "c", "g"], id="ok")
    )[1].ok
