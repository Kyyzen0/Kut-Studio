"""Format déclaratif, lot 3 (ADR-0005 étape 4) : pistes, sous-titres, transitions, images clés, étalonnage.

Trois règles reviennent, parce que la revue des lots précédents les a trouvées chacune : un script ne reçoit **jamais** une valeur
ramenée en silence par le modèle (les bornes sont celles du modèle, relues ici), le **verrou** d'une piste vaut pour un script comme
pour l'interface, et un verbe ne s'applique qu'aux **genres de piste** qui ont un sens pour lui.
"""

from __future__ import annotations

import pytest

from core.animation_targets import get_target
from core.color_grading import ColorGradingService
from core.keyframe_editing import find_clip as find_keyframe_clip
from core.keyframe_editing import value_at
from core.project_model import MediaAsset, Project, Track
from core.scripting import ScriptInvalid, Session, apply_script, parse_script
from core.scripting.verbs import _GRADE_FIELDS, VERBS
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
        "Démo",
        width=1280,
        height=720,
        fps=30.0,
        media_assets=[
            MediaAsset(
                "m",
                "/x/intro.mp4",
                "intro",
                10.0,
                1280,
                720,
                30.0,
                "video",
                has_audio=True,
            )
        ],
        tracks=[Track("V1", "V1", "video"), Track("A1", "A1", "audio")],
    )


def _script(*ops: dict) -> dict:
    return {"kut_script": 1, "ops": list(ops)}


def _apply(project: Project, *ops: dict):
    session = Session(project)
    return session, apply_script(session, parse_script(_script(*ops)))


def _clips(*ids: str) -> list[dict]:
    """Deux clips de 10 s adjacents sur V1 (« a » puis « b »)."""
    return [
        {
            "op": "add_clip",
            "track": "V1",
            "asset": "m",
            "at": 10.0 * index,
            "id": clip_id,
        }
        for index, clip_id in enumerate(ids)
    ]


# --- Pistes ----------------------------------------------------------------------------------------------------------------


def test_a_script_adds_tracks_and_cites_them_by_alias():
    session, report = _apply(
        _project(),
        {"op": "add_track", "type": "audio", "name": "Voix", "as": "voix"},
        {"op": "add_track", "type": "subtitle", "as": "sous"},
        {"op": "set_track_muted", "track": {"ref": "voix"}, "muted": True},
    )
    assert report.ok, report.error
    tracks = {track.id: track for track in session.project.tracks}
    assert (
        tracks["voix"].name == "Voix"
        and tracks["voix"].type == "audio"
        and tracks["voix"].muted
    )
    assert tracks["sous"].type == "subtitle"


def test_a_track_name_already_taken_is_refused_not_renamed_in_silence():
    """Le modèle suffixerait le nom (« A1 2 ») : un script obtiendrait une piste qui ne porte pas le nom qu'il a écrit."""
    session, report = _apply(
        _project(), {"op": "add_track", "type": "audio", "name": "A1"}
    )
    assert (
        not report.ok
        and "s'appelle déjà" in report.error
        and len(session.project.tracks) == 2
    )
    for bad in (
        {"op": "add_track", "type": "graphics"},
        {"op": "add_track", "type": "audio", "id": "A1"},
    ):
        if bad.get("id"):
            assert not _apply(_project(), bad)[1].ok
        else:
            with pytest.raises(ScriptInvalid):
                parse_script(_script(bad))


def test_a_text_with_edge_whitespace_is_refused_since_the_model_would_strip_it():
    """Revue Codex : « A1 » avec des espaces autour passait le contrôle du doublon, puis le modèle le rognait et le suffixait en « A1 (2) »."""
    for name in (" A1 ", "A1 ", " Voix", "  ", "\n"):
        with pytest.raises(ScriptInvalid):
            parse_script(_script({"op": "add_track", "type": "audio", "name": name}))
    for op in (
        {"op": "rename_bus", "bus": "b", "name": " Voix "},
        {"op": "add_marker", "at": 1, "name": " Début"},
        {"op": "add_subtitle", "text": "\nBonjour", "at": 0, "duration": 1},
        {"op": "add_graphic", "graphic": "text", "at": 0, "duration": 1, "text": "x "},
    ):
        with pytest.raises(ScriptInvalid):
            parse_script(_script(op))
    parse_script(
        _script({"op": "add_subtitle", "text": "a\nb", "at": 0, "duration": 1})
    )  # un saut de ligne au milieu reste permis


# --- Sous-titres -----------------------------------------------------------------------------------------------------------


def test_a_subtitle_goes_on_a_subtitle_track_with_its_text_intact():
    session, report = _apply(
        _project(),
        {"op": "add_track", "type": "subtitle", "id": "S1"},
        {
            "op": "add_subtitle",
            "text": "Bonjour\nle monde",
            "at": 1.5,
            "duration": 2,
            "id": "st",
        },
    )
    assert report.ok, report.error
    clip = find_clip(session.project, "st")
    assert (
        clip.text == "Bonjour\nle monde"
        and clip.timeline_start == 1.5
        and clip.duration == 2.0
        and clip.track_id == "S1"
    )


def test_a_subtitle_needs_an_unlocked_subtitle_track():
    _session, report = _apply(
        _project(), {"op": "add_subtitle", "text": "x", "at": 0, "duration": 1}
    )
    assert not report.ok and "add_track" in report.error
    session, report = _apply(
        _project(),
        {"op": "add_track", "type": "subtitle", "id": "S1"},
        {"op": "set_track_locked", "track": "S1", "locked": True},
        {"op": "add_subtitle", "text": "x", "at": 0, "duration": 1},
    )
    assert (
        not report.ok
        and report.failed_index == 2
        and "verrouillée" in report.error
        and len(session.project.tracks) == 2
    )
    _session, report = _apply(
        _project(),
        {"op": "add_subtitle", "text": "x", "at": 0, "duration": 1, "track": "V1"},
    )
    assert not report.ok and "sous-titre" in report.error


# --- Transitions -----------------------------------------------------------------------------------------------------------


def test_a_transition_is_added_and_removed_between_adjacent_clips():
    session, report = _apply(
        _project(),
        *_clips("a", "b"),
        {
            "op": "add_transition",
            "from_clip": "a",
            "to_clip": "b",
            "transition": "crossfade",
            "duration": 1.0,
            "as": "fondu",
        },
    )
    assert report.ok, report.error
    assert [
        (t.id, t.from_clip_id, t.to_clip_id, t.type.value, t.duration)
        for t in session.project.transitions
    ] == [("fondu", "a", "b", "crossfade", 1.0)]
    _session, removed = _apply(
        session.project, {"op": "remove_transition", "transition": "fondu"}
    )
    assert removed.ok and session.project.transitions == []


def test_transitions_respect_the_model_and_the_track_lock():
    session, report = _apply(
        _project(),
        *_clips("a", "b"),
        {
            "op": "add_transition",
            "from_clip": "a",
            "to_clip": "b",
            "transition": "crossfade",
            "duration": 6,
        },
    )
    assert (
        not report.ok and "moitié" in report.error and session.project.transitions == []
    )
    session, report = _apply(
        _project(),
        *_clips("a", "b"),
        {"op": "set_track_locked", "track": "V1", "locked": True},
        {
            "op": "add_transition",
            "from_clip": "a",
            "to_clip": "b",
            "transition": "crossfade",
            "duration": 1,
        },
    )
    assert not report.ok and "verrouillée" in report.error
    with pytest.raises(ScriptInvalid):
        parse_script(
            _script(
                {
                    "op": "add_transition",
                    "from_clip": "a",
                    "to_clip": "b",
                    "transition": "tourbillon",
                    "duration": 1,
                }
            )
        )
    with pytest.raises(ScriptInvalid):
        parse_script(
            _script(
                {
                    "op": "add_transition",
                    "from_clip": "a",
                    "to_clip": "b",
                    "transition": "crossfade",
                    "duration": 0,
                }
            )
        )


def test_a_locked_track_protects_its_transitions_from_removal():
    project = _project()
    session, report = _apply(
        project,
        *_clips("a", "b"),
        {
            "op": "add_transition",
            "from_clip": "a",
            "to_clip": "b",
            "transition": "crossfade",
            "duration": 1,
            "id": "t",
        },
    )
    assert report.ok
    _session, report = _apply(
        session.project,
        {"op": "set_track_locked", "track": "V1", "locked": True},
        {"op": "remove_transition", "transition": "t"},
    )
    assert (
        not report.ok
        and "verrouillée" in report.error
        and len(session.project.transitions) == 1
    )


# --- Images clés -----------------------------------------------------------------------------------------------------------


def test_keyframes_are_relative_to_the_clip_and_animate_the_value():
    session, report = _apply(
        _project(),
        *_clips("a"),
        {
            "op": "add_keyframe",
            "clip": "a",
            "property": "opacity",
            "at": 0,
            "value": 0.0,
        },
        {
            "op": "add_keyframe",
            "clip": "a",
            "property": "opacity",
            "at": 2,
            "value": 1.0,
            "interpolation": "linear",
        },
    )
    assert report.ok, report.error
    clip = find_keyframe_clip(session.project, "a")
    assert (
        value_at(clip, "opacity", 0.0) == 0.0 and value_at(clip, "opacity", 2.0) == 1.0
    )
    assert value_at(clip, "opacity", 1.0) == pytest.approx(0.5), (
        "linéaire entre les deux images clés"
    )
    removed = apply_script(
        Session(session.project),
        parse_script(
            _script(
                {"op": "remove_keyframe", "clip": "a", "property": "opacity", "at": 2}
            )
        ),
    )
    assert (
        removed.ok
        and value_at(find_keyframe_clip(session.project, "a"), "opacity", 2.0) == 0.0
    )


def test_a_keyframe_value_outside_the_property_bounds_is_refused_not_clamped():
    """Le modèle ramène 2,0 à 1,0 pour l'opacité : le script « réussirait » en écrivant une autre valeur."""
    session, report = _apply(
        _project(),
        *_clips("a"),
        {
            "op": "add_keyframe",
            "clip": "a",
            "property": "opacity",
            "at": 0,
            "value": 2.0,
        },
    )
    assert not report.ok and "hors bornes" in report.error
    assert (
        not session.can_undo
        and next(t for t in session.project.tracks if t.id == "V1").clips == []
    ), "tout ou rien : rien n'a été posé"


_GRAPHIC_HOSTS = (
    ("text", None),
    ("shape", "rounded_rectangle"),
    ("rectangle", None),
    ("solid", None),
)


def _host(name: str) -> list[dict] | None:
    """Un clip « a » d'un genre où la propriété existe : vidéo, sinon le premier calque graphique qu'un script sait poser ; ``None`` s'il n'y en a pas
    (les paramètres de lumière : aucun verbe ne crée de calque « light », l'interface seule le fait)."""
    if get_target(name).applies_to("video"):
        return _clips("a")
    for graphic, shape in _GRAPHIC_HOSTS:
        op = {
            "op": "add_graphic",
            "graphic": graphic,
            "at": 0,
            "duration": 10,
            "id": "a",
            **({"shape": shape} if shape else {}),
        }
        session, report = _apply(_project(), op)
        if report.ok and get_target(name).applies_to_clip(
            find_clip(session.project, "a")
        ):
            return [op]
    return None


def test_a_property_must_exist_for_the_clips_track_type():
    """Revue Codex : une image clé d'opacité sur un clip de sous-titre, ou de « skew » sur une vidéo, était enregistrée puis ignorée au rendu."""
    session, report = _apply(
        _project(),
        *_clips("a"),
        {"op": "add_keyframe", "clip": "a", "property": "skew", "at": 0, "value": 10},
    )
    assert not report.ok and "ne s'applique pas" in report.error
    session, report = _apply(
        _project(),
        {"op": "add_track", "type": "subtitle", "id": "S1"},
        {"op": "add_subtitle", "text": "x", "at": 0, "duration": 2, "id": "st"},
        {
            "op": "add_keyframe",
            "clip": "st",
            "property": "opacity",
            "at": 0,
            "value": 0.5,
        },
    )
    assert (
        not report.ok
        and "ne s'applique pas" in report.error
        and report.failed_index == 2
    )
    session, report = _apply(
        _project(),
        {"op": "add_graphic", "graphic": "text", "at": 0, "duration": 2, "id": "titre"},
        {
            "op": "add_keyframe",
            "clip": "titre",
            "property": "skew",
            "at": 0,
            "value": 10,
        },
        {
            "op": "add_keyframe",
            "clip": "titre",
            "property": "opacity",
            "at": 1,
            "value": 0.5,
        },
    )
    assert report.ok, "« skew » et l'opacité valent pour un calque graphique"


def test_keyframe_time_is_not_clamped_to_the_clip_either():
    _session, report = _apply(
        _project(),
        *_clips("a"),
        {
            "op": "add_keyframe",
            "clip": "a",
            "property": "scale",
            "at": 10.5,
            "value": 1.2,
        },
    )
    assert not report.ok and "dépasse la durée du clip" in report.error
    _session, report = _apply(
        _project(),
        *_clips("a"),
        {
            "op": "add_keyframe",
            "clip": "a",
            "property": "scale",
            "at": 10,
            "value": 1.2,
        },
    )
    assert report.ok, "la fin du clip est permise"


def test_keyframes_respect_the_track_lock_and_missing_keyframes_are_refused():
    _session, report = _apply(
        _project(),
        *_clips("a"),
        {"op": "set_track_locked", "track": "V1", "locked": True},
        {"op": "add_keyframe", "clip": "a", "property": "scale", "at": 0, "value": 1.5},
    )
    assert not report.ok and "verrouillée" in report.error
    _session, report = _apply(
        _project(),
        *_clips("a"),
        {"op": "remove_keyframe", "clip": "a", "property": "scale", "at": 3},
    )
    assert not report.ok and "Aucune image clé" in report.error


def test_unknown_properties_and_interpolations_are_refused_at_decoding():
    for op in (
        {"op": "add_keyframe", "clip": "a", "property": "gravite", "at": 0, "value": 1},
        {
            "op": "add_keyframe",
            "clip": "a",
            "property": "opacity",
            "at": 0,
            "value": 1,
            "interpolation": "magique",
        },
        {
            "op": "add_keyframe",
            "clip": "a",
            "property": "opacity",
            "at": -1,
            "value": 1,
        },
        {
            "op": "add_keyframe",
            "clip": "a",
            "property": "opacity",
            "at": 0,
            "value": "1",
        },
    ):
        with pytest.raises(ScriptInvalid):
            parse_script(_script(op))


# --- Étalonnage ------------------------------------------------------------------------------------------------------------


def test_a_color_grade_is_set_then_reset():
    session, report = _apply(
        _project(),
        *_clips("a"),
        {
            "op": "set_color_grade",
            "clip": "a",
            "exposure": 0.5,
            "saturation": 1.2,
            "hue": -10,
            "shadows": 0.25,
        },
    )
    assert report.ok, report.error
    grade = ColorGradingService().get_grade(session.project, "a")
    assert (grade.exposure, grade.saturation, grade.hue, grade.shadows) == (
        0.5,
        1.2,
        -10.0,
        0.25,
    ) and grade.contrast == 0.0
    _session, report = _apply(
        session.project, {"op": "set_color_grade", "clip": "a", "enabled": False}
    )
    assert (
        report.ok
        and ColorGradingService().get_grade(session.project, "a").enabled is False
    )
    _session, report = _apply(session.project, {"op": "reset_color_grade", "clip": "a"})
    assert (
        report.ok
        and ColorGradingService().get_grade(session.project, "a").exposure == 0.0
    )


def test_every_color_bound_is_the_models_so_nothing_is_clamped_in_silence():
    for name in _GRADE_FIELDS:
        param = next(
            item for item in VERBS["set_color_grade"].params if item.name == name
        )
        for value in (param.minimum, param.maximum):
            session, report = _apply(
                _project(),
                *_clips("a"),
                {"op": "set_color_grade", "clip": "a", name: value},
            )
            assert report.ok, (name, value, report.error)
            assert (
                getattr(ColorGradingService().get_grade(session.project, "a"), name)
                == value
            ), (name, value)
        for outside in (param.minimum - 0.01, param.maximum + 0.01):
            with pytest.raises(ScriptInvalid):
                parse_script(
                    _script({"op": "set_color_grade", "clip": "a", name: outside})
                )


def test_color_needs_a_field_a_video_or_graphics_clip_and_an_unlocked_track():
    _session, report = _apply(
        _project(), *_clips("a"), {"op": "set_color_grade", "clip": "a"}
    )
    assert not report.ok and "au moins un champ" in report.error
    project = _project()
    project.media_assets.append(
        MediaAsset(
            "snd", "/x/voix.wav", "voix", 5.0, 0, 0, 0.0, "audio", has_audio=True
        )
    )
    _session, report = _apply(
        project,
        {"op": "add_clip", "track": "A1", "asset": "snd", "at": 0, "id": "son"},
        {"op": "set_color_grade", "clip": "son", "exposure": 1},
    )
    assert not report.ok and "piste 'audio'" in report.error
    _session, report = _apply(
        _project(),
        *_clips("a"),
        {"op": "set_track_locked", "track": "V1", "locked": True},
        {"op": "set_color_grade", "clip": "a", "exposure": 1},
    )
    assert not report.ok and "verrouillée" in report.error
    _session, report = _apply(
        _project(),
        *_clips("a"),
        {"op": "set_track_locked", "track": "V1", "locked": True},
        {"op": "reset_color_grade", "clip": "a"},
    )
    assert not report.ok and "verrouillée" in report.error


# --- Le catalogue ----------------------------------------------------------------------------------------------------------


def test_the_new_verbs_are_in_the_table():
    assert {
        "add_track",
        "add_subtitle",
        "add_transition",
        "remove_transition",
        "add_keyframe",
        "remove_keyframe",
        "set_color_grade",
        "reset_color_grade",
    } <= set(VERBS)
