"""Identifiants explicites des fonctions de création (ADR-0005) : un script désigne ce qu'il crée, et rejoué il rend le même projet.

Chaque fonction de création de ``core/`` accepte un identifiant fourni (``clip_id=``…). Absent, rien ne change. Fourni, il suit la
règle des identifiants de nœuds (lettres, chiffres, ``_``, 64 caractères au plus : un identifiant peut devenir une étiquette de graphe
FFmpeg), il est libre dans le projet, et **un refus ne modifie rien**.
"""

from __future__ import annotations

import re

import pytest

from core.graphics import add_graphic_clip
from core.object_ids import InvalidObjectId, clip_ids, resolve_object_id, sequence_ids
from core.project_io import project_payload
from core.project_model import MediaAsset, Project, Track
from core.sequences import create_sequence, create_sequence_from_selection
from core.timeline_editing import add_marker
from core.timeline_operations import (
    add_clip_to_track,
    add_subtitle_clip,
    duplicate_clip,
    duplicate_clip_preserving_transform,
)
from core.track_operations import add_track
from core.transitions import add_transition


def _project() -> Project:
    """Un projet vide de pistes V1, A1, S1 et d'un média vidéo ``m`` de 10 s."""
    return Project(
        "p",
        width=1920,
        height=1080,
        fps=30.0,
        media_assets=[
            MediaAsset(
                "m", "/x/m.mp4", "m", 10.0, 1920, 1080, 30.0, "video", has_audio=False
            )
        ],
        tracks=[
            Track("V1", "V1", "video"),
            Track("A1", "A1", "audio"),
            Track("S1", "S1", "subtitle"),
        ],
    )


def _snapshot(project: Project) -> dict:
    return project_payload(project)


# --- La règle ---------------------------------------------------------------------------------------------------------


def test_without_a_requested_id_one_is_generated_in_the_historical_form():
    assert re.fullmatch(
        r"clip-[0-9a-f]{12}",
        resolve_object_id(None, prefix="clip", kind="clip", taken=set()),
    )
    assert re.fullmatch(
        r"marker-[0-9a-f]{8}",
        resolve_object_id(None, prefix="marker", kind="repère", taken=set(), length=8),
    )


@pytest.mark.parametrize("value", ["intro", "A1", "clip_2", "x" * 64, "Voix_Off_3"])
def test_a_safe_id_is_accepted_as_is(value):
    assert resolve_object_id(value, prefix="clip", kind="clip", taken=set()) == value


@pytest.mark.parametrize(
    "value",
    [
        "",
        "clip-1",
        "a b",
        "a]b",
        "a;b",
        "a'b",
        "a,b",
        "x" * 65,
        "é",
        "a\n",
        "../x",
        "a[0]",
    ],
)
def test_an_id_that_could_become_a_filter_injection_or_is_malformed_is_refused(value):
    with pytest.raises(InvalidObjectId, match="invalide"):
        resolve_object_id(value, prefix="clip", kind="clip", taken=set())


def test_a_taken_id_is_refused_never_overwritten():
    with pytest.raises(InvalidObjectId, match="existe déjà"):
        resolve_object_id("intro", prefix="clip", kind="clip", taken={"intro"})


def test_a_refused_id_is_a_value_error_so_existing_handlers_already_catch_it():
    assert issubclass(InvalidObjectId, ValueError)


# --- Clips ------------------------------------------------------------------------------------------------------------


def test_add_clip_to_track_takes_an_explicit_id_and_refuses_a_duplicate_without_touching_the_project():
    project = _project()
    clip = add_clip_to_track(project, "m", "V1", 0.0, clip_id="intro")
    assert clip.id == "intro" and project.tracks[0].clips == [clip]
    before = _snapshot(project)
    with pytest.raises(InvalidObjectId, match="existe déjà"):
        add_clip_to_track(project, "m", "V1", 5.0, clip_id="intro")
    with pytest.raises(InvalidObjectId, match="invalide"):
        add_clip_to_track(project, "m", "V1", 5.0, clip_id="bad id")
    assert _snapshot(project) == before


def test_without_an_id_the_clip_is_generated_exactly_as_before():
    clip = add_clip_to_track(_project(), "m", "V1", 0.0)
    assert re.fullmatch(r"clip-[0-9a-f]{12}", clip.id)


def test_clip_ids_are_unique_across_every_sequence_of_the_project():
    project = _project()
    add_clip_to_track(project, "m", "V1", 0.0, clip_id="intro")
    other = create_sequence(project, "Autre")
    project.active_sequence_id = other.id
    with pytest.raises(InvalidObjectId, match="existe déjà"):
        add_clip_to_track(project, "m", other.tracks[0].id, 0.0, clip_id="intro")
    assert clip_ids(project) == {"intro"}


def test_a_subtitle_clip_and_its_technical_media_get_ids_derived_from_the_requested_one():
    project = _project()
    clip = add_subtitle_clip(project, "Bonjour", 1.0, 2.0, "S1", clip_id="titre_1")
    assert clip.id == "titre_1" and clip.asset_id == "asset-subtitle-titre_1"
    assert any(asset.id == "asset-subtitle-titre_1" for asset in project.media_assets)
    before = _snapshot(project)
    with pytest.raises(InvalidObjectId):
        add_subtitle_clip(project, "Autre", 4.0, 1.0, "S1", clip_id="titre_1")
    assert _snapshot(project) == before, (
        "ni clip ni média technique créé par un identifiant refusé"
    )
    generated = add_subtitle_clip(project, "Sans id", 6.0, 1.0, "S1")
    assert (
        re.fullmatch(r"clip-[0-9a-f]{12}", generated.id)
        and generated.asset_id == "asset-subtitle-" + generated.id[5:]
    )


@pytest.mark.parametrize(
    "duplicate", [duplicate_clip, duplicate_clip_preserving_transform]
)
def test_a_duplicate_can_be_given_its_id(duplicate):
    project = _project()
    add_clip_to_track(project, "m", "V1", 0.0, clip_id="original")
    copy = duplicate(project, "original", new_clip_id="copie")
    assert copy.id == "copie" and copy.track_id == "V1"
    before = _snapshot(project)
    with pytest.raises(InvalidObjectId):
        duplicate(project, "original", new_clip_id="copie")
    assert _snapshot(project) == before


# --- Pistes, transitions, repères -------------------------------------------------------------------------------------


def test_a_track_can_be_given_its_id_and_the_default_stays_the_first_free_one():
    project = _project()
    assert add_track(project, "video", track_id="titres").id == "titres"
    assert add_track(project, "video").id == "V2", "le défaut est inchangé"
    before = _snapshot(project)
    with pytest.raises(InvalidObjectId, match="existe déjà"):
        add_track(project, "audio", track_id="titres")
    assert _snapshot(project) == before


def test_a_transition_can_be_given_its_id():
    project = _project()
    add_clip_to_track(project, "m", "V1", 0.0, clip_id="a")
    add_clip_to_track(project, "m", "V1", 10.0, clip_id="b")
    transition = add_transition(project, "a", "b", duration=0.5, transition_id="fondu")
    assert transition.id == "fondu"
    # Une autre paire, disjointe : le modèle refuse déjà qu'un clip porte deux transitions de suite, ce qui n'a rien à voir ici.
    add_clip_to_track(project, "m", "V1", 30.0, clip_id="c")
    add_clip_to_track(project, "m", "V1", 40.0, clip_id="d")
    with pytest.raises(InvalidObjectId, match="existe déjà"):
        add_transition(project, "c", "d", duration=0.5, transition_id="fondu")
    assert len(project.transitions) == 1, "la transition refusée n'a rien ajouté"
    assert (
        add_transition(project, "c", "d", duration=0.5, transition_id="fondu_2").id
        == "fondu_2"
    )


def test_a_marker_can_be_given_its_id():
    project = _project()
    assert add_marker(project, 2.0, "Début", marker_id="debut").id == "debut"
    with pytest.raises(InvalidObjectId, match="existe déjà"):
        add_marker(project, 3.0, "Autre", marker_id="debut")
    assert re.fullmatch(r"marker-[0-9a-f]{8}", add_marker(project, 4.0).id)


# --- Séquences --------------------------------------------------------------------------------------------------------


def test_a_sequence_can_be_given_its_id():
    project = _project()
    assert create_sequence(project, "Scène", sequence_id="scene_1").id == "scene_1"
    with pytest.raises(InvalidObjectId, match="existe déjà"):
        create_sequence(project, "Autre", sequence_id="scene_1")
    assert sequence_ids(project) >= {"scene_1"} and len(project.sequences) == 2


def test_nesting_a_selection_takes_both_ids_and_a_refusal_leaves_the_clips_where_they_were():
    project = _project()
    add_clip_to_track(project, "m", "V1", 0.0, clip_id="a")
    add_clip_to_track(project, "m", "V1", 10.0, clip_id="b")
    before = _snapshot(project)
    for bad in (
        {"sequence_id": "bad id"},
        {"nested_clip_id": "a"},
        {"nested_clip_id": "bad]id"},
    ):
        with pytest.raises(InvalidObjectId):
            create_sequence_from_selection(project, ["a", "b"], "Scène", **bad)
        assert _snapshot(project) == before, f"{bad} : rien ne doit avoir bougé"
    result = create_sequence_from_selection(
        project, ["a", "b"], "Scène", sequence_id="scene", nested_clip_id="scene_clip"
    )
    assert result.clip.id == "scene_clip" and result.clip.sequence_id == "scene"
    assert {
        clip.id for track in project.sequences[-1].tracks for clip in track.clips
    } == {"a", "b"}


# --- Graphiques -------------------------------------------------------------------------------------------------------


def test_a_graphic_clip_takes_its_id_and_a_refusal_creates_neither_track_nor_media():
    project = _project()
    clip = add_graphic_clip(
        project, "shape", timeline_start=1.0, duration=2.0, clip_id="logo"
    )
    assert clip.id == "logo" and clip.asset_id == "graphic-asset-logo"
    before = _snapshot(project)
    with pytest.raises(InvalidObjectId, match="existe déjà"):
        add_graphic_clip(
            project, "shape", timeline_start=1.0, duration=2.0, clip_id="logo"
        )
    assert _snapshot(project) == before
    assert re.fullmatch(
        r"graphic-[0-9a-f]{12}",
        add_graphic_clip(project, "shape", timeline_start=5.0).id,
    )


def test_a_refused_graphic_id_does_not_create_the_graphics_track():
    project = _project()
    assert not any(track.type == "graphics" for track in project.tracks)
    before = _snapshot(project)
    with pytest.raises(InvalidObjectId, match="invalide"):
        add_graphic_clip(project, "shape", timeline_start=0.0, clip_id="mauvais id")
    assert _snapshot(project) == before


# --- La propriété qui compte pour un script ---------------------------------------------------------------------------


def _build() -> Project:
    project = _project()
    add_track(project, "video", track_id="titres")
    add_clip_to_track(project, "m", "V1", 0.0, clip_id="intro")
    add_clip_to_track(project, "m", "V1", 10.0, clip_id="suite")
    add_transition(project, "intro", "suite", duration=0.5, transition_id="fondu")
    add_subtitle_clip(project, "Bonjour", 1.0, 2.0, "S1", clip_id="sous_titre")
    add_graphic_clip(project, "shape", timeline_start=0.0, duration=3.0, clip_id="logo")
    add_marker(project, 5.0, "Milieu", marker_id="milieu")
    duplicate_clip(project, "intro", new_clip_id="intro_bis")
    return project


def test_the_same_script_replayed_gives_the_same_project_down_to_every_identifier():
    """Sans identifiants fournis, deux exécutions donnent deux projets aux identifiants aléatoires : un script ne pourrait ni
    désigner ce qu'il crée ni être rejoué. Avec, le projet est identique."""
    assert _snapshot(_build()) == _snapshot(_build())


# --- Médias techniques dérivés (revue Codex de la PR 122) -------------------------------------------------------------------


def test_a_derived_technical_media_id_that_is_already_taken_is_refused_before_anything_changes():
    """L'identifiant du clip est libre, celui de son média technique (``asset-subtitle-<id>``, ``graphic-asset-<id>``) ne l'est pas :
    deux médias de même identifiant ne sont pas lus pareil partout, un clip existant pourrait se mettre à rendre le nouveau."""
    project = _project()
    project.media_assets.append(
        MediaAsset(
            "asset-subtitle-titre",
            "",
            "pris",
            1.0,
            0,
            0,
            0.0,
            "subtitle",
            has_audio=False,
        )
    )
    project.media_assets.append(
        MediaAsset(
            "graphic-asset-logo",
            "",
            "pris",
            1.0,
            100,
            100,
            30.0,
            "graphic",
            has_audio=False,
        )
    )
    before = _snapshot(project)
    with pytest.raises(InvalidObjectId, match="média"):
        add_subtitle_clip(project, "Bonjour", 0.0, 1.0, "S1", clip_id="titre")
    with pytest.raises(InvalidObjectId, match="média"):
        add_graphic_clip(
            project, "shape", timeline_start=0.0, duration=1.0, clip_id="logo"
        )
    assert _snapshot(project) == before, (
        "ni clip, ni média, ni piste de graphiques créés"
    )
    assert (
        add_subtitle_clip(project, "Bonjour", 0.0, 1.0, "S1", clip_id="autre").asset_id
        == "asset-subtitle-autre"
    )
    assert (
        add_graphic_clip(
            project, "shape", timeline_start=0.0, duration=1.0, clip_id="autre_logo"
        ).asset_id
        == "graphic-asset-autre_logo"
    )
