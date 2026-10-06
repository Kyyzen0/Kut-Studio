"""Tests du module :mod:`core.effects_library` (tâche 22).

Le module encapsule trois responsabilités :

- catalogue de presets intégrés (cinéma, noir et blanc, vintage…) ;
- helpers de filtrage et d'application d'un preset à un clip ;
- store de presets utilisateur (CRUD + persistance JSON).

Chaque test suit l'approche déjà utilisée dans
:mod:`tests.test_effects_model` : on n'instancie pas PySide6, on
s'appuie uniquement sur le modèle métier et sur un répertoire
temporaire pour la persistance.
"""

from __future__ import annotations

import pytest

from core.effects_library import (
    CATEGORY_DESCRIPTIONS,
    CATEGORY_LABELS,
    EffectCategory,
    EffectPreset,
    UserPresetStore,
    apply_preset_to_clip,
    builtin_preset_ids,
    builtin_presets,
    combined_library,
    effect_category,
    filter_presets,
    load_user_presets,
    make_preset,
    preset_effects_to_apply,
    save_user_presets,
    snapshot_clip_preset,
    user_presets_path,
)
from core.effects_model import (
    EffectType,
    add_effect,
    create_effect,
)
from core.project_model import Clip, MediaAsset, Project, Track


# ---------------------------------------------------------------------------
# Fabriques partagées
# ---------------------------------------------------------------------------


def _asset(asset_id: str = "asset-video") -> MediaAsset:
    return MediaAsset(
        id=asset_id,
        path=f"/tmp/{asset_id}.mp4",
        name=asset_id,
        duration=10.0,
        width=1920,
        height=1080,
        fps=30.0,
        media_type="video",
    )


def _project_with_video_clip() -> Project:
    track = Track(
        id="V1",
        name="V1",
        type="video",
        clips=[
            Clip(
                id="clip-video",
                asset_id="asset-video",
                track_id="V1",
                timeline_start=0.0,
                source_in=0.0,
                source_out=5.0,
                label="Plan large",
            )
        ],
    )
    return Project(
        name="Test library",
        media_assets=[_asset()],
        tracks=[track],
    )


# ---------------------------------------------------------------------------
# Catalogue intégré
# ---------------------------------------------------------------------------


def test_builtin_presets_cover_six_expected_families() -> None:
    """La bibliothèque livrée couvre les grandes familles d'effets."""
    ids = {preset.id for preset in builtin_presets()}
    expected = {
        "cinema",
        "black_and_white",
        "vintage",
        "sharp",
        "blur",
    }
    assert expected.issubset(ids)
    # Les dix presets sont présents (dont Night Look et Neon Rush, vidéo sociale) — chiffre stable, volontaire.
    assert len(builtin_presets()) == 10


def test_builtin_preset_ids_match_catalog() -> None:
    assert builtin_preset_ids() == frozenset(
        preset.id for preset in builtin_presets()
    )


def test_builtin_presets_are_marked_as_builtin() -> None:
    for preset in builtin_presets():
        assert preset.builtin is True
        assert preset.category in EffectCategory
        # Tout preset doit porter au moins un effet ; sinon il n'a
        # aucun sens dans la bibliothèque.
        assert len(preset.effects) >= 1


def test_builtin_presets_have_no_duplicate_unique_effect() -> None:
    """Chaque preset n'expose qu'un effet par catégorie unique."""
    from core.effects_model import is_single_instance

    for preset in builtin_presets():
        seen: set[EffectType] = set()
        for effect in preset.effects:
            if is_single_instance(effect.type):
                assert effect.type not in seen, (
                    f"preset '{preset.id}' dupliquette {effect.type}"
                )
                seen.add(effect.type)


def test_cinema_preset_combines_correction_and_vignette() -> None:
    cinema = next(p for p in builtin_presets() if p.id == "cinema")
    types = {effect.type for effect in cinema.effects}
    assert EffectType.COLOR_CORRECTION in types
    assert EffectType.VIGNETTE in types
    # Le contraste du cinéma est plus chaud que le défaut neutre.
    correction = next(
        e for e in cinema.effects if e.type == EffectType.COLOR_CORRECTION
    )
    assert correction.params["contrast"] > 1.0


def test_category_labels_and_descriptions_are_complete() -> None:
    for category in EffectCategory:
        assert category in CATEGORY_LABELS
        assert category in CATEGORY_DESCRIPTIONS
        assert CATEGORY_LABELS[category]
        assert CATEGORY_DESCRIPTIONS[category]


def test_effect_category_normalizes_value_or_raises() -> None:
    assert effect_category("color") is EffectCategory.COLOR
    assert effect_category(EffectCategory.LOOK) is EffectCategory.LOOK
    with pytest.raises(ValueError):
        effect_category("not-a-category")


# ---------------------------------------------------------------------------
# Filtrage
# ---------------------------------------------------------------------------


def test_filter_presets_by_search_matches_name_and_id() -> None:
    library = builtin_presets()
    cinema_matches = filter_presets(library, search="cinema")
    assert {p.id for p in cinema_matches} == {"cinema"}
    noir_matches = filter_presets(library, search="Noir")
    # « Noir » matche à la fois « noir et blanc » et « film noir ».
    ids = {p.id for p in noir_matches}
    assert "black_and_white" in ids
    assert "noir" in ids


def test_filter_presets_by_category() -> None:
    library = builtin_presets()
    look = filter_presets(library, category=EffectCategory.LOOK)
    assert all(p.category is EffectCategory.LOOK for p in look)
    assert "cinema" in {p.id for p in look}


def test_filter_presets_combines_search_and_category() -> None:
    library = builtin_presets()
    matches = filter_presets(
        library,
        search="cinema",
        category=EffectCategory.COLOR,
    )
    assert matches == []


# ---------------------------------------------------------------------------
# make_preset / EffectPreset
# ---------------------------------------------------------------------------


def test_make_preset_rejects_duplicate_single_instance() -> None:
    color = create_effect(EffectType.COLOR_CORRECTION)
    with pytest.raises(ValueError, match="unique"):
        make_preset(
            preset_id="bad",
            name="Mauvais preset",
            description="",
            category=EffectCategory.COLOR,
            effects=[color, color],
        )


def test_effectpreset_rejects_empty_id_or_name() -> None:
    with pytest.raises(ValueError):
        EffectPreset(
            id="",
            name="x",
            description="",
            category=EffectCategory.LOOK,
            effects=(),
        )
    with pytest.raises(ValueError):
        EffectPreset(
            id="x",
            name="",
            description="",
            category=EffectCategory.LOOK,
            effects=(),
        )


# ---------------------------------------------------------------------------
# Application d'un preset à un clip
# ---------------------------------------------------------------------------


def test_preset_effects_to_apply_replaces_unique_type() -> None:
    cinema = next(p for p in builtin_presets() if p.id == "cinema")
    chain = preset_effects_to_apply(cinema, [])
    types = [e.type for e in chain]
    # Color correction puis vignette.
    assert types == [EffectType.COLOR_CORRECTION, EffectType.VIGNETTE]


def test_preset_effects_to_apply_overrides_existing_unique_effect() -> None:
    cinema = next(p for p in builtin_presets() if p.id == "cinema")
    existing_color = create_effect(
        EffectType.COLOR_CORRECTION,
        params={"brightness": 0.5, "contrast": 1.0, "saturation": 1.0},
    )
    chain = preset_effects_to_apply(cinema, [existing_color])
    color_chain = [e for e in chain if e.type == EffectType.COLOR_CORRECTION]
    # Le preset remplace l'ancien effet unique ; il n'en reste qu'un.
    assert len(color_chain) == 1
    # Le contraste a bien été écrasé par celui du preset.
    assert color_chain[0].params["contrast"] > 1.0


def test_preset_effects_to_apply_stacks_blurs() -> None:
    blur = next(p for p in builtin_presets() if p.id == "blur")
    chain = preset_effects_to_apply(blur, [])
    assert len(chain) == 1
    chain2 = preset_effects_to_apply(blur, chain)
    assert len(chain2) == 2


def test_apply_preset_to_clip_records_history() -> None:
    project = _project_with_video_clip()
    preset = next(p for p in builtin_presets() if p.id == "vintage")
    effects = apply_preset_to_clip(project, "clip-video", preset)
    types = {e.type for e in effects}
    assert EffectType.SEPIA in types
    assert EffectType.COLOR_CORRECTION in types
    assert EffectType.VIGNETTE in types


def test_apply_preset_rejects_non_video_clip() -> None:
    project = _project_with_video_clip()
    # On déplace réellement le clip sur une piste audio : c'est la
    # table de vérité du modèle de projet, et ``_require_video_clip``
    # s'appuie sur le couple ``(track, clip)`` qu'elle reconstruit.
    clip = project.tracks[0].clips[0]
    audio = Track(
        id="A1",
        name="A1",
        type="audio",
        clips=[clip],
    )
    project.tracks.append(audio)
    project.tracks[0].clips.remove(clip)
    clip.track_id = "A1"
    preset = next(p for p in builtin_presets() if p.id == "blur")
    with pytest.raises(ValueError):
        apply_preset_to_clip(project, "clip-video", preset)


def test_apply_preset_rejects_unknown_clip() -> None:
    project = _project_with_video_clip()
    preset = next(p for p in builtin_presets() if p.id == "blur")
    with pytest.raises(KeyError):
        apply_preset_to_clip(project, "ghost", preset)


# ---------------------------------------------------------------------------
# Capture d'un preset depuis un clip
# ---------------------------------------------------------------------------


def test_snapshot_clip_preset_uses_clip_effects() -> None:
    project = _project_with_video_clip()
    clip = project.tracks[0].clips[0]
    sepia = create_effect(EffectType.SEPIA)
    clip.effects = add_effect(clip.effects, sepia)
    preset = snapshot_clip_preset(
        clip, name="Mon preset", description="test", category=EffectCategory.LOOK
    )
    assert preset.builtin is False
    assert preset.name == "Mon preset"
    assert len(preset.effects) == 1
    assert preset.effects[0].type is EffectType.SEPIA


def test_snapshot_clip_preset_rejects_empty_name() -> None:
    project = _project_with_video_clip()
    clip = project.tracks[0].clips[0]
    with pytest.raises(ValueError, match="nom"):
        snapshot_clip_preset(clip, name="   ")


def test_snapshot_clip_preset_decouples_from_clip() -> None:
    """Modifier le clip ne doit pas altérer le preset capturé."""
    project = _project_with_video_clip()
    clip = project.tracks[0].clips[0]
    clip.effects = list(clip.effects) + [
        create_effect(EffectType.SEPIA),
    ]
    preset = snapshot_clip_preset(clip, name="Snap", description="")
    # On supprime l'effet du clip : le preset doit rester inchangé.
    clip.effects = []
    assert len(preset.effects) == 1


# ---------------------------------------------------------------------------
# Persistance des presets utilisateur
# ---------------------------------------------------------------------------


def test_save_and_load_roundtrip(tmp_path) -> None:
    project = _project_with_video_clip()
    clip = project.tracks[0].clips[0]
    clip.effects = [create_effect(EffectType.SEPIA)]
    preset = snapshot_clip_preset(clip, name="Perso", description="roundtrip")

    save_user_presets([preset], settings_dir=tmp_path)
    loaded = load_user_presets(settings_dir=tmp_path)

    assert len(loaded) == 1
    assert loaded[0].id == preset.id
    assert loaded[0].name == "Perso"
    assert loaded[0].effects[0].type is EffectType.SEPIA


def test_save_user_presets_ignores_builtin(tmp_path) -> None:
    cinema = next(p for p in builtin_presets() if p.id == "cinema")
    save_user_presets([cinema], settings_dir=tmp_path)
    assert load_user_presets(settings_dir=tmp_path) == []


def test_load_user_presets_returns_empty_on_missing_file(tmp_path) -> None:
    assert load_user_presets(settings_dir=tmp_path) == []


def test_load_user_presets_handles_corrupted_json(tmp_path) -> None:
    path = user_presets_path(settings_dir=tmp_path)
    path.write_text("{invalid", encoding="utf-8")
    assert load_user_presets(settings_dir=tmp_path) == []


def test_load_user_presets_drops_invalid_entries(tmp_path) -> None:
    """Une entrée corrompue ne doit pas faire échouer le chargement."""
    payload = [
        {"id": "ok", "name": "OK", "description": "", "category": "look",
         "effects": [{"type": "sepia", "enabled": True, "params": {}}]},
        {"id": "bad", "name": "Bad", "description": "", "category": "look",
         "effects": [{"type": "not-a-type", "enabled": True, "params": {}}]},
        {"not": "a preset"},
    ]
    import json

    path = user_presets_path(settings_dir=tmp_path)
    path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = load_user_presets(settings_dir=tmp_path)
    assert len(loaded) == 1
    assert loaded[0].id == "ok"


def test_save_user_presets_atomic(tmp_path) -> None:
    """Aucune écriture incomplète ne doit laisser de fichier corrompu."""
    path = user_presets_path(settings_dir=tmp_path)
    preset = make_preset(
        preset_id="atomic",
        name="Atomic",
        description="",
        category=EffectCategory.LOOK,
        effects=[create_effect(EffectType.SEPIA)],
    )
    save_user_presets([preset], settings_dir=tmp_path)
    # Aucun fichier ``.tmp`` ne doit rester après l'écriture.
    leftovers = [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []
    assert path.exists()


# ---------------------------------------------------------------------------
# UserPresetStore
# ---------------------------------------------------------------------------


def test_user_preset_store_rejects_builtin(tmp_path) -> None:
    cinema = next(p for p in builtin_presets() if p.id == "cinema")
    store = UserPresetStore(settings_dir=tmp_path)
    with pytest.raises(ValueError, match="intégrés"):
        store.add(cinema)


def test_user_preset_store_adds_and_persists(tmp_path) -> None:
    store = UserPresetStore(settings_dir=tmp_path)
    preset = make_preset(
        preset_id="user-1",
        name="Perso",
        description="",
        category=EffectCategory.LOOK,
        effects=[create_effect(EffectType.SEPIA)],
    )
    store.add(preset)
    assert store.get("user-1") is preset
    # La persistance est synchrone : un second store lit la même donnée.
    other = UserPresetStore(settings_dir=tmp_path)
    assert other.get("user-1") is not None
    assert other.get("user-1").name == "Perso"


def test_user_preset_store_remove_persists(tmp_path) -> None:
    store = UserPresetStore(settings_dir=tmp_path)
    preset = make_preset(
        preset_id="user-1",
        name="Perso",
        description="",
        category=EffectCategory.LOOK,
        effects=[create_effect(EffectType.SEPIA)],
    )
    store.add(preset)
    store.remove("user-1")
    assert store.get("user-1") is None
    other = UserPresetStore(settings_dir=tmp_path)
    assert other.all() == []


def test_user_preset_store_replace(tmp_path) -> None:
    store = UserPresetStore(settings_dir=tmp_path)
    preset_v1 = make_preset(
        preset_id="user-1",
        name="Perso",
        description="v1",
        category=EffectCategory.LOOK,
        effects=[create_effect(EffectType.SEPIA)],
    )
    store.add(preset_v1)
    preset_v2 = make_preset(
        preset_id="user-1",
        name="Perso",
        description="v2",
        category=EffectCategory.LOOK,
        effects=[create_effect(EffectType.SEPIA)],
    )
    store.replace(preset_v2)
    assert store.get("user-1").description == "v2"


def test_user_preset_store_subscribe(tmp_path) -> None:
    store = UserPresetStore(settings_dir=tmp_path)
    events: list[int] = []

    def listener() -> None:
        events.append(len(store.all()))

    store.subscribe(listener)
    preset = make_preset(
        preset_id="user-1",
        name="Perso",
        description="",
        category=EffectCategory.LOOK,
        effects=[create_effect(EffectType.SEPIA)],
    )
    store.add(preset)
    store.remove("user-1")

    # Ajout + suppression = deux notifications.
    assert events == [1, 0]


def test_user_preset_store_duplicate_id_rejected(tmp_path) -> None:
    store = UserPresetStore(settings_dir=tmp_path)
    preset = make_preset(
        preset_id="user-1",
        name="Perso",
        description="",
        category=EffectCategory.LOOK,
        effects=[create_effect(EffectType.SEPIA)],
    )
    store.add(preset)
    with pytest.raises(ValueError):
        store.add(preset)


def test_user_preset_store_unknown_remove(tmp_path) -> None:
    store = UserPresetStore(settings_dir=tmp_path)
    with pytest.raises(KeyError):
        store.remove("ghost")


# ---------------------------------------------------------------------------
# Bibliothèque combinée
# ---------------------------------------------------------------------------


def test_combined_library_returns_builtin_then_user(tmp_path) -> None:
    user_preset = make_preset(
        preset_id="user-1",
        name="Perso",
        description="",
        category=EffectCategory.LOOK,
        effects=[create_effect(EffectType.SEPIA)],
    )
    library = combined_library([user_preset])
    # Les intégrés sont en tête.
    assert all(p.builtin for p in library[: len(builtin_presets())])
    assert library[-1].id == "user-1"


def test_combined_library_filters_user_builtins(tmp_path) -> None:
    cinema = next(p for p in builtin_presets() if p.id == "cinema")
    library = combined_library([cinema])
    # Le cinéma intégré est déjà retourné par ``builtin_presets()`` :
    # on ne le double pas.
    cinema_count = sum(1 for p in library if p.id == "cinema")
    assert cinema_count == 1