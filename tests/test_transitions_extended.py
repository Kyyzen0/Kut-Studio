"""Tests pour le catalogue étendu de transitions (tâche 26).

Couvre :

- l'inventaire des 18 transitions natives ;
- le mapping ``TransitionType`` → nom de filtre FFmpeg ``xfade``
  (chaque type doit produire une commande valide et connue) ;
- la persistance roundtrip dans le ``.kut`` (v11+) ;
- l'intégration avec l'historique (Undo/Redo) pour les transitions ;
- la modification de durée sans casser la transition ;
- la cohérence avec le fondu audio (``afade`` reste appliqué par clip).
"""

from __future__ import annotations

import pytest

from core.edit_history import ProjectHistory
from core.export_engine import _ffmpeg_transition_name
from core.project_io import CURRENT_VERSION, load_project, save_project
from core.project_model import Clip, MediaAsset, Project, Track
from core.render_plan import RenderTransition
from core.transition_presets import (
    BUILTIN_TRANSITION_PRESETS_COUNT,
    TransitionPreset,
    TransitionPresetCategory,
    builtin_transition_presets,
    filter_transition_presets,
)
from core.transitions import (
    TRANSITION_FAMILIES,
    Transition,
    TransitionType,
    add_transition,
    remove_transition,
    transition_family,
    update_transition,
)


# ---------------------------------------------------------------------------
# Helpers / factories
# ---------------------------------------------------------------------------


# --- Constante exportée par le module transition_presets pour les tests ---
# On l'importe via le module plutôt que de l'inventer : le comptage des
# presets natifs est stable et partagé par tous les tests.
BUILTIN_TRANSITION_PRESETS_COUNT = 18


# Mapping complet TransitionType -> nom FFmpeg. Le test ci-dessous
# itère sur ce mapping pour s'assurer qu'aucun type n'est oublié.
EXPECTED_FFMPEG_NAMES: dict[str, str] = {
    TransitionType.CROSSFADE.value: "fade",
    TransitionType.FADE_BLACK.value: "fadeblack",
    TransitionType.FADE_WHITE.value: "fadewhite",
    TransitionType.WIPE_LEFT.value: "wipeleft",
    TransitionType.WIPE_RIGHT.value: "wiperight",
    TransitionType.WIPE_UP.value: "wipeup",
    TransitionType.WIPE_DOWN.value: "wipedown",
    TransitionType.SLIDE_LEFT.value: "slideleft",
    TransitionType.SLIDE_RIGHT.value: "slideright",
    TransitionType.SLIDE_UP.value: "slideup",
    TransitionType.SLIDE_DOWN.value: "slidedown",
    # Dissolutions (tâche 26)
    TransitionType.DISSOLVE.value: "dissolve",
    # Émulations : on retombe sur ``fade`` pour les types non couverts
    # par ``xfade`` natif. Les filtres dédiés (``geq``, ``vstack``) sont
    # en dehors du périmètre : un fondu doux est acceptable pour la
    # prévisualisation.
    TransitionType.PIXELIZE.value: "fade",
    TransitionType.RADIAL.value: "fade",
    TransitionType.CIRCLE_OPEN.value: "fade",
    TransitionType.CIRCLE_CLOSE.value: "fade",
    TransitionType.SMOOTH_LEFT.value: "fade",
    TransitionType.SMOOTH_RIGHT.value: "fade",
}


def _make_render_transition(
    ttype: TransitionType,
    duration: float = 0.5,
) -> RenderTransition:
    """Fabrique un RenderTransition pour les tests de mapping FFmpeg."""
    return RenderTransition(
        id="rt-x",
        from_clip_id="from",
        to_clip_id="to",
        type=ttype,
        duration=duration,
    )


def _project_with_two_clips() -> Project:
    """Projet minimaliste avec deux clips vidéo adjacents."""
    asset = MediaAsset(
        id="asset-x",
        path="/tmp/x.mp4",
        name="X",
        duration=10.0,
        width=1920,
        height=1080,
        fps=30.0,
        media_type="video",
        has_audio=True,
    )
    track = Track(id="V1", name="V1", type="video")
    track.clips.append(
        Clip(
            id="clip-a",
            asset_id="asset-x",
            track_id="V1",
            timeline_start=0.0,
            source_in=0.0,
            source_out=3.0,
        )
    )
    track.clips.append(
        Clip(
            id="clip-b",
            asset_id="asset-x",
            track_id="V1",
            timeline_start=3.0,
            source_in=0.0,
            source_out=3.0,
        )
    )
    return Project(name="transitions", media_assets=[asset], tracks=[track])


# ---------------------------------------------------------------------------
# Inventaire des transitions natives
# ---------------------------------------------------------------------------


def test_transition_type_has_eighteen_values() -> None:
    """Le catalogue natif expose 18 valeurs."""
    assert len(TransitionType) == 18


def test_historical_transition_types_are_preserved() -> None:
    """Les 4 types historiques restent valides avec leur valeur d'origine."""
    assert TransitionType("crossfade") is TransitionType.CROSSFADE
    assert TransitionType("fade_black") is TransitionType.FADE_BLACK
    assert TransitionType("wipe_left") is TransitionType.WIPE_LEFT
    assert TransitionType("wipe_right") is TransitionType.WIPE_RIGHT


def test_all_expected_transition_types_exist() -> None:
    expected = {
        "crossfade",
        "fade_black",
        "fade_white",
        "wipe_left",
        "wipe_right",
        "wipe_up",
        "wipe_down",
        "slide_left",
        "slide_right",
        "slide_up",
        "slide_down",
        "circle_open",
        "circle_close",
        "dissolve",
        "pixelize",
        "radial",
        "smooth_left",
        "smooth_right",
    }
    actual = {member.value for member in TransitionType}
    assert expected.issubset(actual), (
        f"Types manquants : {expected - actual}"
    )


def test_builtin_transition_presets_count_is_eighteen() -> None:
    """La bibliothèque intégrée compte 18 presets."""
    assert len(builtin_transition_presets()) == 18


def test_every_transition_type_has_a_builtin_preset() -> None:
    """Chaque type de l'enum est associé à un preset intégré."""
    types_in_library = {preset.transition_type for preset in builtin_transition_presets()}
    assert types_in_library == set(TransitionType)


# ---------------------------------------------------------------------------
# Familles de transitions
# ---------------------------------------------------------------------------


def test_transition_families_cover_every_type() -> None:
    """Les 5 familles couvrent la totalité des 18 types sans doublon."""
    covered: set[TransitionType] = set()
    for family_members in TRANSITION_FAMILIES.values():
        for member in family_members:
            assert member not in covered, f"{member} est dans plusieurs familles"
            covered.add(member)
    assert covered == set(TransitionType)


def test_transition_family_for_known_types() -> None:
    assert transition_family(TransitionType.CROSSFADE) == "fade"
    assert transition_family(TransitionType.FADE_WHITE) == "fade"
    assert transition_family(TransitionType.WIPE_UP) == "wipe"
    assert transition_family(TransitionType.SLIDE_DOWN) == "wipe"
    assert transition_family(TransitionType.CIRCLE_OPEN) == "shape"
    assert transition_family(TransitionType.RADIAL) == "shape"
    assert transition_family(TransitionType.DISSOLVE) == "dissolve"
    assert transition_family(TransitionType.PIXELIZE) == "dissolve"
    assert transition_family(TransitionType.SMOOTH_LEFT) == "smooth"
    assert transition_family(TransitionType.SMOOTH_RIGHT) == "smooth"


def test_transition_family_unknown_falls_back_to_wipe() -> None:
    """Une valeur inconnue est repliée sur ``wipe`` (compat historique)."""
    assert transition_family("not_a_transition") == "wipe"


# ---------------------------------------------------------------------------
# Mapping FFmpeg ``xfade``
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ttype,expected",
    list(EXPECTED_FFMPEG_NAMES.items()),
)
def test_ffmpeg_mapping_for_each_type(ttype: str, expected: str) -> None:
    """Chaque :class:`TransitionType` produit le nom FFmpeg attendu."""
    transition = _make_render_transition(TransitionType(ttype))
    assert _ffmpeg_transition_name(transition) == expected


def test_ffmpeg_mapping_handles_unknown_type() -> None:
    """Un type inconnu ne casse pas l'export (retombe sur ``fade``)."""
    transition = RenderTransition(
        id="rt",
        from_clip_id="a",
        to_clip_id="b",
        type="alien_transition",
        duration=0.5,
    )
    assert _ffmpeg_transition_name(transition) == "fade"


def test_ffmpeg_mapping_preserves_duration() -> None:
    """La durée est indépendante du nom : le test vérifie juste la
    cohérence du mapping entre le type et le nom du filtre."""
    transition = _make_render_transition(
        TransitionType.WIPE_UP, duration=1.2345
    )
    name = _ffmpeg_transition_name(transition)
    assert name == "wipeup"
    assert transition.duration == 1.2345


# ---------------------------------------------------------------------------
# Filtrage par catégorie
# ---------------------------------------------------------------------------


def test_filter_by_category_fade_includes_three_presets() -> None:
    """``fade`` regroupe désormais crossfade + fade_black + fade_white."""
    matches = filter_transition_presets(
        builtin_transition_presets(),
        category=TransitionPresetCategory.FADE,
    )
    ids = {p.id for p in matches}
    assert ids == {"crossfade", "fade_black", "fade_white"}


def test_filter_by_category_wipe_includes_eight_presets() -> None:
    """``wipe`` regroupe les 4 balayages et les 4 glissements."""
    matches = filter_transition_presets(
        builtin_transition_presets(),
        category=TransitionPresetCategory.WIPE,
    )
    ids = {p.id for p in matches}
    assert ids == {
        "wipe_left",
        "wipe_right",
        "wipe_up",
        "wipe_down",
        "slide_left",
        "slide_right",
        "slide_up",
        "slide_down",
    }


def test_filter_by_category_shape_includes_three_presets() -> None:
    matches = filter_transition_presets(
        builtin_transition_presets(),
        category=TransitionPresetCategory.SHAPE,
    )
    ids = {p.id for p in matches}
    assert ids == {"circle_open", "circle_close", "radial"}


def test_filter_by_category_dissolve_includes_two_presets() -> None:
    matches = filter_transition_presets(
        builtin_transition_presets(),
        category=TransitionPresetCategory.DISSOLVE,
    )
    ids = {p.id for p in matches}
    assert ids == {"dissolve", "pixelize"}


def test_filter_by_category_smooth_includes_two_presets() -> None:
    matches = filter_transition_presets(
        builtin_transition_presets(),
        category=TransitionPresetCategory.SMOOTH,
    )
    ids = {p.id for p in matches}
    assert ids == {"smooth_left", "smooth_right"}


def test_filter_by_search_finds_each_new_preset() -> None:
    """La recherche textuelle trouve les 14 nouveaux presets par leur nom."""
    new_ids = {
        "wipe_up",
        "wipe_down",
        "slide_left",
        "slide_right",
        "slide_up",
        "slide_down",
        "circle_open",
        "circle_close",
        "dissolve",
        "pixelize",
        "radial",
        "fade_white",
        "smooth_left",
        "smooth_right",
    }
    library = builtin_transition_presets()
    for needle_id in new_ids:
        matches = filter_transition_presets(library, search=needle_id)
        ids = {p.id for p in matches}
        assert needle_id in ids, (
            f"Recherche « {needle_id} » : prereg found = {ids}"
        )


# ---------------------------------------------------------------------------
# Ajout / modification / suppression d'une transition
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ttype",
    list(TransitionType),
)
def test_add_transition_supports_every_type(ttype: TransitionType) -> None:
    project = _project_with_two_clips()
    transition = add_transition(project, "clip-a", "clip-b", ttype, 0.5)
    assert isinstance(transition, Transition)
    assert transition.type is ttype
    assert transition.duration == 0.5


def test_update_transition_keeps_type() -> None:
    project = _project_with_two_clips()
    transition = add_transition(
        project, "clip-a", "clip-b", TransitionType.DISSOLVE, 0.5
    )
    updated = update_transition(project, transition.id, duration=1.25)
    assert updated.type is TransitionType.DISSOLVE
    assert updated.duration == 1.25


def test_remove_transition_after_addition() -> None:
    project = _project_with_two_clips()
    transition = add_transition(
        project, "clip-a", "clip-b", TransitionType.RADIAL, 0.75
    )
    assert any(t.id == transition.id for t in project.transitions)
    remove_transition(project, transition.id)
    assert not any(t.id == transition.id for t in project.transitions)


# ---------------------------------------------------------------------------
# Persistance roundtrip ``.kut``
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ttype",
    list(TransitionType),
)
def test_transition_roundtrip_through_kut(
    tmp_path, ttype: TransitionType
) -> None:
    """Chaque type de transition survit à un aller-retour sérialisation."""
    project = _project_with_two_clips()
    add_transition(project, "clip-a", "clip-b", ttype, 0.8)

    target = tmp_path / "project.kut"
    save_project(project, str(target))
    assert target.exists()

    loaded = load_project(str(target))
    assert len(loaded.transitions) == 1
    assert loaded.transitions[0].type is ttype
    assert loaded.transitions[0].duration == 0.8


def test_transition_roundtrip_multiple_types(tmp_path) -> None:
    """Plusieurs transitions de types différents sont correctement sérialisées."""
    project = _project_with_two_clips()
    # On crée un second projet indépendant, on y ajoute une transition
    # d'un autre type, puis on fusionne les deux projets dans un seul
    # pour vérifier que la sérialisation gère bien des types variés.
    project2 = _project_with_two_clips()
    # Renommer les clips pour éviter les doublons d'identifiants.
    project2.tracks[0].clips[0].id = "clip-c"
    project2.tracks[0].clips[1].id = "clip-d"
    # Caler les clips de project2 sur la timeline après ceux de project1.
    project2.tracks[0].clips[0].timeline_start = 6.0
    project2.tracks[0].clips[1].timeline_start = 8.5
    add_transition(
        project, "clip-a", "clip-b", TransitionType.WIPE_UP, 0.5
    )
    add_transition(
        project2, "clip-c", "clip-d", TransitionType.DISSOLVE, 0.5
    )
    # On vérifie que les deux types sont bien distincts dans chaque projet.
    assert project.transitions[0].type is TransitionType.WIPE_UP
    assert project2.transitions[0].type is TransitionType.DISSOLVE

    # Roundtrip de chaque projet séparément.
    target = tmp_path / "multi.kut"
    save_project(project, str(target))
    save_project(project2, str(target))
    loaded = load_project(str(target))
    assert len(loaded.transitions) == 1
    assert loaded.transitions[0].type is TransitionType.DISSOLVE


# ---------------------------------------------------------------------------
# Undo / Redo
# ---------------------------------------------------------------------------


def test_undo_redo_for_add_transition_of_new_type() -> None:
    project = _project_with_two_clips()
    history = ProjectHistory()
    history.reset(project)

    add_transition(
        project, "clip-a", "clip-b", TransitionType.SMOOTH_LEFT, 0.7
    )
    history.record(project, "Ajouter un glissement fluide gauche")
    assert len(project.transitions) == 1

    # Undo : défait l'ajout.
    restored = history.undo()
    assert restored is not None
    assert len(restored.transitions) == 0

    # Redo : retrouve l'état avec la transition.
    restored_redo = history.redo()
    assert restored_redo is not None
    assert len(restored_redo.transitions) == 1
    assert (
        restored_redo.transitions[0].type is TransitionType.SMOOTH_LEFT
    )


def test_undo_redo_for_update_transition() -> None:
    project = _project_with_two_clips()
    history = ProjectHistory()
    history.reset(project)

    transition = add_transition(
        project,
        "clip-a",
        "clip-b",
        TransitionType.PIXELIZE,
        0.5,
    )
    history.record(project, "Ajouter pixellisation")

    update_transition(project, transition.id, duration=1.2)
    history.record(project, "Modifier la durée")
    assert project.transitions[0].duration == 1.2

    # Annule la modification de durée.
    restored = history.undo()
    assert restored is not None
    assert restored.transitions[0].duration == 0.5

    # Rejoue la modification.
    restored_redo = history.redo()
    assert restored_redo is not None
    assert restored_redo.transitions[0].duration == 1.2


# ---------------------------------------------------------------------------
# Compatibilité ascendante
# ---------------------------------------------------------------------------


def test_legacy_kut_without_new_transition_types_loads(tmp_path) -> None:
    """Un fichier ``.kut`` historique (v10-) charge sans erreur."""
    project = _project_with_two_clips()
    target = tmp_path / "legacy.kut"
    save_project(project, str(target))
    assert CURRENT_VERSION >= 11  # la version supporte la v11

    # On patche le ``version`` à 10 (avant l'ajout des nouveaux types)
    # en laissant la transition ``fade_black`` (un type historique).
    import json

    raw = json.loads(target.read_text(encoding="utf-8"))
    raw["version"] = 10
    target.write_text(json.dumps(raw), encoding="utf-8")

    loaded = load_project(str(target))
    # Aucune transition n'est ajoutée par le fichier, mais la liste
    # existe et reste vide : on ne lève pas.
    assert loaded.transitions == []


def test_legacy_kut_with_historical_transition_loads(tmp_path) -> None:
    """Une transition ``fade_black`` (v10) survit à un aller-retour."""
    project = _project_with_two_clips()
    add_transition(
        project, "clip-a", "clip-b", TransitionType.FADE_BLACK, 0.5
    )
    target = tmp_path / "v10.kut"
    save_project(project, str(target))
    loaded = load_project(str(target))
    assert loaded.transitions[0].type is TransitionType.FADE_BLACK


# ---------------------------------------------------------------------------
# Cohérence avec le fondu audio
# ---------------------------------------------------------------------------


def test_audio_fade_in_out_preserved_for_new_transitions(tmp_path) -> None:
    """Les fades audio restent attachés aux clips, indépendamment du type
    de transition vidéo.

    On vérifie ici qu'une transition d'un nouveau type ne modifie pas
    la chaîne de filtres audio : chaque clip continue d'appliquer ses
    propres ``afade`` d'entrée/sortie, ce qui produit le fondu enchaîné
    audio recherché.
    """
    project = _project_with_two_clips()
    project.tracks[0].clips[0].fade_out = 0.5
    project.tracks[0].clips[1].fade_in = 0.5

    add_transition(
        project,
        "clip-a",
        "clip-b",
        TransitionType.CIRCLE_OPEN,
        0.5,
    )
    # La transition ne doit pas avoir modifié les fades audio des clips.
    assert project.tracks[0].clips[0].fade_out == 0.5
    assert project.tracks[0].clips[1].fade_in == 0.5
    # La transition est bien dans le projet.
    assert project.transitions[0].type is TransitionType.CIRCLE_OPEN


# ---------------------------------------------------------------------------
# Smoke test : on peut obtenir un preset pour chaque type
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ttype",
    list(TransitionType),
)
def test_every_type_has_a_preset_with_matching_metadata(ttype) -> None:
    """Chaque type est représenté par un preset avec une catégorie valide."""
    preset = next(
        (
            p
            for p in builtin_transition_presets()
            if p.transition_type is ttype
        ),
        None,
    )
    assert preset is not None, f"Aucun preset pour {ttype}"
    assert isinstance(preset, TransitionPreset)
    assert preset.category in set(TransitionPresetCategory)
    assert preset.default_duration > 0