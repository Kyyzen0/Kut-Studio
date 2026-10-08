"""Tests du module :mod:`core.transition_presets` (tâche 23).

Couvre :

- catalogue de presets intégrés ;
- fabrication / validation des presets (durée, type, catégorie) ;
- filtres (recherche, catégorie, favoris) ;
- application d'un preset (utilise le modèle de transitions existant) ;
- store utilisateur : favoris, presets utilisateur, CRUD, persistance
  atomique, fichiers corrompus ;
- non-régression de l'export FFmpeg (les quatre types sont toujours
  présents dans le filtergraph).
"""

from __future__ import annotations

import json

import pytest

from core.project_model import Clip, MediaAsset, Project, Track
from core.render_plan import build_render_plan
from core.transition_presets import (
    CATEGORY_DESCRIPTIONS,
    CATEGORY_LABELS,
    MAX_DURATION,
    MIN_DURATION,
    TransitionPreset,
    TransitionPresetCategory,
    TransitionPresetStore,
    apply_transition_preset,
    builtin_transition_preset_ids,
    builtin_transition_presets,
    filter_transition_presets,
    load_transition_preset_data,
    make_transition_preset,
    make_user_transition_preset,
    save_transition_preset_data,
    transition_presets_path,
)
from core.transitions import (
    TransitionType,
    add_transition,
)


# ---------------------------------------------------------------------------
# Fabriques partagées
# ---------------------------------------------------------------------------


def _asset() -> MediaAsset:
    return MediaAsset(
        id="asset-video",
        path="/tmp/video.mp4",
        name="Video",
        duration=10.0,
        width=1920,
        height=1080,
        fps=30.0,
        media_type="video",
    )


def _project_with_two_clips() -> Project:
    track = Track(
        id="V1",
        name="V1",
        type="video",
        clips=[
            Clip(
                id="clip-a",
                asset_id="asset-video",
                track_id="V1",
                timeline_start=0.0,
                source_in=0.0,
                source_out=4.0,
                label="Plan A",
            ),
            Clip(
                id="clip-b",
                asset_id="asset-video",
                track_id="V1",
                timeline_start=4.0,
                source_in=4.0,
                source_out=8.0,
                label="Plan B",
            ),
        ],
    )
    return Project(
        name="Transitions library",
        media_assets=[_asset()],
        tracks=[track],
    )


# ---------------------------------------------------------------------------
# Catalogue intégré
# ---------------------------------------------------------------------------


def test_builtin_presets_cover_the_four_ffmpeg_types() -> None:
    """Les quatre types FFmpeg supportés sont représentés."""
    types = {preset.transition_type for preset in builtin_transition_presets()}
    assert types == set(TransitionType)


def test_builtin_preset_ids_match_catalog() -> None:
    assert builtin_transition_preset_ids() == frozenset(
        preset.id for preset in builtin_transition_presets()
    )


def test_builtin_presets_are_marked_as_builtin() -> None:
    for preset in builtin_transition_presets():
        assert preset.builtin is True
        assert preset.category in TransitionPresetCategory
        assert MIN_DURATION <= preset.default_duration <= MAX_DURATION


def test_category_labels_and_descriptions_are_complete() -> None:
    for category in TransitionPresetCategory:
        assert category in CATEGORY_LABELS
        assert category in CATEGORY_DESCRIPTIONS
        assert CATEGORY_LABELS[category]
        assert CATEGORY_DESCRIPTIONS[category]


def test_make_transition_preset_rejects_out_of_range_duration() -> None:
    with pytest.raises(ValueError, match="durée"):
        make_transition_preset(
            preset_id="bad",
            name="Bad",
            description="",
            category=TransitionPresetCategory.FADE,
            transition_type=TransitionType.CROSSFADE,
            default_duration=10.0,
        )


def test_transitionpreset_rejects_empty_id_or_name() -> None:
    with pytest.raises(ValueError):
        TransitionPreset(
            id="",
            name="x",
            description="",
            category=TransitionPresetCategory.FADE,
            transition_type=TransitionType.CROSSFADE,
            default_duration=0.5,
        )
    with pytest.raises(ValueError):
        TransitionPreset(
            id="x",
            name="",
            description="",
            category=TransitionPresetCategory.FADE,
            transition_type=TransitionType.CROSSFADE,
            default_duration=0.5,
        )


# ---------------------------------------------------------------------------
# Filtrage
# ---------------------------------------------------------------------------


def test_filter_by_category_returns_only_matching() -> None:
    library = builtin_transition_presets()
    fade_presets = filter_transition_presets(
        library, category=TransitionPresetCategory.FADE
    )
    assert all(p.category is TransitionPresetCategory.FADE for p in fade_presets)
    # Tâche 26 : la catégorie ``fade`` accueille désormais ``fade_white``
    # en plus des deux transitions historiques.
    assert {p.id for p in fade_presets} == {
        "crossfade",
        "fade_black",
        "fade_white",
    }


def test_filter_by_search_matches_name_and_id() -> None:
    library = builtin_transition_presets()
    matches = filter_transition_presets(library, search="wipe")
    # Tâche 26 : 4 balayages (haut, bas, gauche, droite) au lieu de 2.
    assert {p.id for p in matches} == {
        "wipe_left",
        "wipe_right",
        "wipe_up",
        "wipe_down",
    }


def test_filter_combines_search_and_category() -> None:
    library = builtin_transition_presets()
    matches = filter_transition_presets(
        library,
        search="wipe",
        category=TransitionPresetCategory.FADE,
    )
    assert matches == []


def test_filter_favorites_only() -> None:
    library = builtin_transition_presets()
    favorites = ["crossfade"]
    matches = filter_transition_presets(
        library, favorites=favorites, favorites_only=True
    )
    assert {p.id for p in matches} == {"crossfade"}


# ---------------------------------------------------------------------------
# Application d'un preset
# ---------------------------------------------------------------------------


def test_apply_preset_uses_default_duration() -> None:
    project = _project_with_two_clips()
    preset = next(p for p in builtin_transition_presets() if p.id == "fade_black")
    transition = apply_transition_preset(project, "clip-a", "clip-b", preset)
    assert transition.type is TransitionType.FADE_BLACK
    assert transition.duration == pytest.approx(0.75)


def test_apply_preset_with_duration_override() -> None:
    project = _project_with_two_clips()
    preset = next(p for p in builtin_transition_presets() if p.id == "wipe_left")
    transition = apply_transition_preset(
        project, "clip-a", "clip-b", preset, duration=1.0
    )
    assert transition.duration == pytest.approx(1.0)
    assert transition.type is TransitionType.WIPE_LEFT


def test_apply_preset_propagates_model_errors() -> None:
    project = _project_with_two_clips()
    preset = next(p for p in builtin_transition_presets() if p.id == "crossfade")
    # Durée trop grande pour le clip court.
    with pytest.raises(ValueError, match="moitié"):
        apply_transition_preset(
            project, "clip-a", "clip-b", preset, duration=2.5
        )


# ---------------------------------------------------------------------------
# Capture / fabrication d'un preset utilisateur
# ---------------------------------------------------------------------------


def test_make_user_preset_assigns_a_user_category() -> None:
    preset = make_user_transition_preset(
        name="Mon fondu",
        description="test",
        transition_type=TransitionType.CROSSFADE,
        default_duration=0.4,
    )
    assert preset.builtin is False
    assert preset.category is TransitionPresetCategory.FADE
    assert preset.transition_type is TransitionType.CROSSFADE
    assert preset.default_duration == pytest.approx(0.4)
    assert preset.id.startswith("user-")


def test_make_user_preset_rejects_empty_name() -> None:
    with pytest.raises(ValueError, match="nom"):
        make_user_transition_preset(
            name="   ",
            description="",
            transition_type=TransitionType.CROSSFADE,
            default_duration=0.5,
        )


def test_make_user_preset_rejects_out_of_range_duration() -> None:
    with pytest.raises(ValueError):
        make_user_transition_preset(
            name="Bad",
            description="",
            transition_type=TransitionType.CROSSFADE,
            default_duration=0.0,
        )


# ---------------------------------------------------------------------------
# Persistance : favoris et presets utilisateur
# ---------------------------------------------------------------------------


def test_save_and_load_roundtrip(tmp_path) -> None:
    preset = make_user_transition_preset(
        name="Perso",
        description="roundtrip",
        transition_type=TransitionType.WIPE_RIGHT,
        default_duration=0.6,
    )
    save_transition_preset_data([preset], ["crossfade"], settings_dir=tmp_path)

    loaded_presets, loaded_favorites = load_transition_preset_data(
        settings_dir=tmp_path
    )
    assert [p.id for p in loaded_presets] == [preset.id]
    assert loaded_presets[0].name == "Perso"
    assert loaded_presets[0].transition_type is TransitionType.WIPE_RIGHT
    assert loaded_favorites == ["crossfade"]


def test_load_returns_empty_on_missing_file(tmp_path) -> None:
    assert load_transition_preset_data(settings_dir=tmp_path) == ([], [])


def test_load_handles_corrupted_json(tmp_path) -> None:
    path = transition_presets_path(settings_dir=tmp_path)
    path.write_text("{invalid", encoding="utf-8")
    assert load_transition_preset_data(settings_dir=tmp_path) == ([], [])


def test_load_drops_invalid_preset_entries(tmp_path) -> None:
    payload = {
        "version": 1,
        "favorites": ["valid", "", 42],
        "user_presets": [
            {
                "id": "user-1",
                "name": "Valid",
                "description": "",
                "transition_type": "wipe_left",
                "default_duration": 0.5,
            },
            {
                "id": "user-bad",
                "name": "Bad",
                "description": "",
                "transition_type": "not-a-type",
                "default_duration": 0.5,
            },
            {
                "id": "user-bad-duration",
                "name": "Bad",
                "description": "",
                "transition_type": "wipe_left",
                "default_duration": 99.0,
            },
            "not-a-mapping",
        ],
    }
    path = transition_presets_path(settings_dir=tmp_path)
    path.write_text(json.dumps(payload), encoding="utf-8")

    presets, favorites = load_transition_preset_data(settings_dir=tmp_path)
    assert [p.id for p in presets] == ["user-1"]
    # Les favoris non-string sont filtrés ; les chaînes vides aussi.
    assert favorites == ["valid"]


def test_save_atomic_no_tmp_files_left(tmp_path) -> None:
    preset = make_user_transition_preset(
        name="Atomic",
        description="",
        transition_type=TransitionType.CROSSFADE,
        default_duration=0.5,
    )
    save_transition_preset_data([preset], [], settings_dir=tmp_path)
    leftovers = [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []


# ---------------------------------------------------------------------------
# TransitionPresetStore
# ---------------------------------------------------------------------------


def test_store_persists_across_instances(tmp_path) -> None:
    preset = make_user_transition_preset(
        name="Perso",
        description="",
        transition_type=TransitionType.CROSSFADE,
        default_duration=0.5,
    )
    store = TransitionPresetStore(settings_dir=tmp_path)
    store.add_user_preset(preset)
    store.set_favorite("crossfade", True)

    other = TransitionPresetStore(settings_dir=tmp_path)
    assert other.all_user_presets()[0].id == preset.id
    assert "crossfade" in other.favorites()


def test_store_rejects_builtin(tmp_path) -> None:
    store = TransitionPresetStore(settings_dir=tmp_path)
    cinema = builtin_transition_presets()[0]
    with pytest.raises(ValueError, match="intégrés"):
        store.add_user_preset(cinema)


def test_store_rejects_duplicate_id(tmp_path) -> None:
    store = TransitionPresetStore(settings_dir=tmp_path)
    preset = make_user_transition_preset(
        name="Perso",
        description="",
        transition_type=TransitionType.CROSSFADE,
        default_duration=0.5,
    )
    store.add_user_preset(preset)
    # On force un second preset avec le même id en passant par le constructeur.
    from core.transition_presets import make_transition_preset

    dupe = make_transition_preset(
        preset_id=preset.id,
        name="Autre",
        description="",
        category=TransitionPresetCategory.FADE,
        transition_type=TransitionType.CROSSFADE,
        default_duration=0.4,
        builtin=False,
    )
    with pytest.raises(ValueError, match="existe déjà"):
        store.add_user_preset(dupe)


def test_store_remove_user_preset_clears_favorite(tmp_path) -> None:
    preset = make_user_transition_preset(
        name="Perso",
        description="",
        transition_type=TransitionType.CROSSFADE,
        default_duration=0.5,
    )
    store = TransitionPresetStore(settings_dir=tmp_path)
    store.add_user_preset(preset)
    store.set_favorite(preset.id, True)
    store.remove_user_preset(preset.id)
    assert preset.id not in store.favorites()


def test_store_set_favorite_rejects_unknown_id(tmp_path) -> None:
    store = TransitionPresetStore(settings_dir=tmp_path)
    with pytest.raises(KeyError):
        store.set_favorite("ghost", True)


def test_store_subscribe(tmp_path) -> None:
    store = TransitionPresetStore(settings_dir=tmp_path)
    events: list[int] = []

    def listener() -> None:
        events.append(len(store.favorites()))

    store.subscribe(listener)
    store.set_favorite("crossfade", True)
    store.set_favorite("crossfade", False)

    assert events == [1, 0]


def test_store_all_presets_returns_builtin_then_user(tmp_path) -> None:
    store = TransitionPresetStore(settings_dir=tmp_path)
    library = store.all_presets()
    assert all(p.builtin for p in library[: len(builtin_transition_presets())])
    preset = make_user_transition_preset(
        name="Perso",
        description="",
        transition_type=TransitionType.CROSSFADE,
        default_duration=0.5,
    )
    store.add_user_preset(preset)
    library = store.all_presets()
    assert library[-1].id == preset.id


def test_store_toggle_favorite_roundtrip(tmp_path) -> None:
    store = TransitionPresetStore(settings_dir=tmp_path)
    new_state = store.toggle_favorite("crossfade")
    assert new_state is True
    assert "crossfade" in store.favorites()
    new_state = store.toggle_favorite("crossfade")
    assert new_state is False
    assert "crossfade" not in store.favorites()


def test_store_get_preset_returns_builtin_or_user(tmp_path) -> None:
    store = TransitionPresetStore(settings_dir=tmp_path)
    assert store.get_preset("crossfade") is not None
    assert store.get_preset("ghost") is None


# ---------------------------------------------------------------------------
# Non-régression : les quatre types restent exportables via FFmpeg
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("preset_id", "ffmpeg_name"),
    [
        ("crossfade", "fade"),
        ("fade_black", "fadeblack"),
        ("wipe_left", "wipeleft"),
        ("wipe_right", "wiperight"),
    ],
)
def test_builtin_preset_maps_to_expected_ffmpeg_xfade(preset_id, ffmpeg_name):
    from core.export_engine import ExportEngine

    preset = next(p for p in builtin_transition_presets() if p.id == preset_id)
    project = _project_with_two_clips()
    add_transition(
        project,
        "clip-a",
        "clip-b",
        preset.transition_type,
        preset.default_duration,
    )
    plan = build_render_plan(project)
    graph, *_ = ExportEngine._build_filter_complex(plan, 1920, 1080, 30, None)
    assert (
        f"xfade=transition={ffmpeg_name}:duration="
        f"{preset.default_duration}" in graph
    )


def test_builtin_presets_are_pure_functions() -> None:
    """builtin_transition_presets() ne partage pas ses instances."""
    a = builtin_transition_presets()
    b = builtin_transition_presets()
    assert a is not b
    assert a == b


def test_no_preset_has_been_dropped(tmp_path) -> None:
    """Les presets utilisateur vides ou corrompus ne polluent pas la sortie."""
    path = transition_presets_path(settings_dir=tmp_path)
    path.write_text(
        json.dumps(
            {"version": 1, "favorites": [], "user_presets": [{"name": "no-id"}]}
        ),
        encoding="utf-8",
    )
    presets, _ = load_transition_preset_data(settings_dir=tmp_path)
    assert presets == []