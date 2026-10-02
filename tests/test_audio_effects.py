"""Tests pour le modèle, la persistance, les filtres FFmpeg et l'undo/redo
des effets audio non destructifs (tâche 27).
"""

from __future__ import annotations

import pytest

from core.audio_effects_library import (
    AUDIO_EFFECT_PRESETS_FILE,
    AudioEffectPreset,
    AudioEffectPresetCategory,
    AudioEffectPresetStore,
    BUILTIN_AUDIO_EFFECT_PRESETS_COUNT,
    CATEGORY_DESCRIPTIONS,
    CATEGORY_LABELS,
    EFFECT_TYPE_CATEGORY,
    MAX_NAME_LENGTH,
    builtin_audio_effect_preset_ids,
    builtin_audio_effect_presets,
    filter_audio_effect_presets,
    load_audio_effect_preset_data,
    make_audio_effect_preset,
    make_user_audio_effect_preset,
    save_audio_effect_preset_data,
)
from core.audio_effects_model import (
    AUDIO_EFFECT_FFMPEG_FILTER,
    AudioEffect,
    AudioEffectType,
    SINGLE_INSTANCE_AUDIO_EFFECTS,
    add_audio_effect_to_clip,
    add_effect,
    clip_audio_effects,
    create_audio_effect,
    default_parameters,
    effect_by_id,
    effect_by_type,
    enabled_effects,
    is_single_instance,
    move_clip_audio_effect,
    move_effect,
    parameter_specs,
    remove_audio_effect_from_clip,
    remove_effect,
    reorder_effect,
    replace_effect,
    set_clip_audio_effect_enabled,
    set_effect_enabled,
    update_clip_audio_effect_parameters,
    update_effect_parameters,
    validate_parameters,
)
from core.edit_history import ProjectHistory
from core.export_engine import (
    _build_clip_audio_effect_filters,
    _format_db,
    _format_seconds,
)
from core.project_io import CURRENT_VERSION, load_project, save_project
from core.project_model import Clip, MediaAsset, Project, Track
from core.render_plan import _build_audio_layer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _audio_project() -> Project:
    asset = MediaAsset(
        id="asset-audio",
        path="/tmp/a.mp3",
        name="A",
        duration=10.0,
        width=0,
        height=0,
        fps=0.0,
        media_type="audio",
        has_audio=True,
    )
    track = Track(id="A1", name="A1", type="audio")
    track.clips.append(
        Clip(
            id="clip-1",
            asset_id="asset-audio",
            track_id="A1",
            timeline_start=0.0,
            source_in=0.0,
            source_out=5.0,
        )
    )
    return Project(name="audio", media_assets=[asset], tracks=[track])


def _project_with_video_audio() -> Project:
    """Projet contenant une piste vidéo dont le média porte de l'audio."""
    asset = MediaAsset(
        id="asset-video",
        path="/tmp/v.mp4",
        name="V",
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
            id="clip-v",
            asset_id="asset-video",
            track_id="V1",
            timeline_start=0.0,
            source_in=0.0,
            source_out=5.0,
        )
    )
    return Project(name="video-audio", media_assets=[asset], tracks=[track])


# Stubs pour les fonctions "by_id" introduites par cette tâche : on
# expose les noms attendus par les tests (cohérence avec le module
# ``effects_model`` qui n'a pas de suffixe ``by_id``).
def move_audio_effect_by_id(effects, effect_id, delta):
    return move_effect(effects, effect_id, delta)


# ---------------------------------------------------------------------------
# Modèle : AudioEffectType + AudioEffect
# ---------------------------------------------------------------------------


def test_audio_effect_type_has_ten_values() -> None:
    """Le catalogue natif expose 10 types."""
    assert len(AudioEffectType) == 10


def test_all_expected_audio_effect_types_exist() -> None:
    expected = {
        "normalize",
        "voice_enhance",
        "noise_reduce",
        "compressor",
        "limiter",
        "bass_boost",
        "treble_boost",
        "phone_effect",
        "reverb_light",
        "echo_light",
    }
    actual = {member.value for member in AudioEffectType}
    assert expected == actual


def test_audio_effect_type_default_color_in_palette() -> None:
    """Le mapping type → filtre FFmpeg existe pour tous les types."""
    for ttype in AudioEffectType:
        assert ttype in AUDIO_EFFECT_FFMPEG_FILTER


def test_parameter_specs_for_every_type() -> None:
    """Chaque type a au moins un paramètre déclaré."""
    for ttype in AudioEffectType:
        specs = parameter_specs(ttype)
        assert len(specs) >= 1, f"{ttype} n'expose aucun paramètre"


def test_default_parameters_returns_all_specs() -> None:
    """``default_parameters`` retourne une entrée par spec, dans l'ordre."""
    for ttype in AudioEffectType:
        defaults = default_parameters(ttype)
        expected_keys = {s.name for s in parameter_specs(ttype)}
        assert set(defaults) == expected_keys


def test_validate_parameters_rejects_out_of_range() -> None:
    with pytest.raises(ValueError):
        validate_parameters(
            AudioEffectType.LIMITER, {"limit_db": 12.0}  # borne max = 0.0
        )


def test_validate_parameters_rejects_unknown_key() -> None:
    with pytest.raises(ValueError):
        validate_parameters(
            AudioEffectType.LIMITER, {"not_a_param": 1.0}
        )


def test_validate_parameters_rejects_non_numeric() -> None:
    with pytest.raises(ValueError):
        validate_parameters(
            AudioEffectType.LIMITER, {"limit_db": "abc"}
        )


def test_single_instance_audio_effects_is_empty() -> None:
    """Aucun effet audio natif n'est marqué ``single instance``."""
    assert SINGLE_INSTANCE_AUDIO_EFFECTS == frozenset()


def test_create_audio_effect_assigns_id_and_validates() -> None:
    effect = create_audio_effect(AudioEffectType.NORMALIZE)
    assert effect.id.startswith("afx-")
    assert effect.type is AudioEffectType.NORMALIZE
    assert effect.enabled is True
    assert effect.params == {
        "integrated_loudness": -16.0,
        "loudness_range": 7.0,
        "true_peak": -1.0,
    }


def test_create_audio_effect_uses_explicit_id() -> None:
    effect = create_audio_effect(
        AudioEffectType.LIMITER, effect_id="custom-id"
    )
    assert effect.id == "custom-id"


def test_audio_effect_with_parameters_validation() -> None:
    effect = create_audio_effect(
        AudioEffectType.LIMITER, params={"limit_db": -3.0}
    )
    assert effect.parameter("limit_db") == -3.0


def test_audio_effect_with_enabled_toggles() -> None:
    effect = create_audio_effect(AudioEffectType.LIMITER)
    assert effect.enabled is True
    assert effect.with_enabled(False).enabled is False


def test_audio_effect_with_parameters_merges() -> None:
    effect = create_audio_effect(AudioEffectType.LIMITER)
    updated = effect.with_parameter("limit_db", -4.0)
    assert updated.parameter("limit_db") == -4.0
    # Les autres paramètres restent intacts.
    assert updated.params["limit_db"] == -4.0


def test_audio_effect_rejects_unknown_type() -> None:
    with pytest.raises(ValueError):
        AudioEffect(id="x", type="not_a_type")


# ---------------------------------------------------------------------------
# Fonctions pures
# ---------------------------------------------------------------------------


def test_add_effect_appends_in_order() -> None:
    a = create_audio_effect(AudioEffectType.NORMALIZE)
    b = create_audio_effect(AudioEffectType.LIMITER)
    result = add_effect(add_effect([], a), b)
    assert result[0].id == a.id
    assert result[1].id == b.id


def test_add_effect_rejects_duplicate_id() -> None:
    a = create_audio_effect(AudioEffectType.NORMALIZE)
    with pytest.raises(ValueError):
        add_effect([a], a)


def test_remove_effect_drops_target() -> None:
    a = create_audio_effect(AudioEffectType.NORMALIZE)
    b = create_audio_effect(AudioEffectType.LIMITER)
    result = remove_effect([a, b], a.id)
    assert result == [b]


def test_remove_effect_unknown_raises() -> None:
    with pytest.raises(KeyError):
        remove_effect([], "missing")


def test_replace_effect_swaps_target() -> None:
    a = create_audio_effect(AudioEffectType.NORMALIZE)
    b = create_audio_effect(AudioEffectType.LIMITER)
    new_a = a.with_parameter("integrated_loudness", -20.0)
    result = replace_effect([a, b], a.id, new_a)
    assert result[0].id == a.id
    assert result[0].parameter("integrated_loudness") == -20.0


def test_set_effect_enabled_toggles_flag() -> None:
    a = create_audio_effect(AudioEffectType.NORMALIZE)
    result = set_effect_enabled([a], a.id, False)
    assert result[0].enabled is False


def test_update_effect_parameters_merges_and_validates() -> None:
    a = create_audio_effect(AudioEffectType.LIMITER)
    result = update_effect_parameters([a], a.id, {"limit_db": -2.0})
    assert result[0].parameter("limit_db") == -2.0


def test_reorder_effect_moves_to_index() -> None:
    a = create_audio_effect(AudioEffectType.NORMALIZE)
    b = create_audio_effect(AudioEffectType.LIMITER)
    c = create_audio_effect(AudioEffectType.COMPRESSOR)
    result = reorder_effect([a, b, c], a.id, 2)
    assert [e.id for e in result] == [b.id, c.id, a.id]


def test_move_effect_swaps_up_and_down() -> None:
    a = create_audio_effect(AudioEffectType.NORMALIZE)
    b = create_audio_effect(AudioEffectType.LIMITER)
    moved = move_audio_effect_by_id([a, b], a.id, 1)
    assert [e.id for e in moved] == [b.id, a.id]


def test_enabled_effects_filters_disabled() -> None:
    a = create_audio_effect(AudioEffectType.NORMALIZE)
    b = create_audio_effect(AudioEffectType.LIMITER).with_enabled(False)
    assert [e.id for e in enabled_effects([a, b])] == [a.id]


def test_effect_by_id_and_effect_by_type() -> None:
    a = create_audio_effect(AudioEffectType.NORMALIZE)
    b = create_audio_effect(AudioEffectType.LIMITER)
    assert effect_by_id([a, b], a.id) is a
    assert effect_by_type([a, b], AudioEffectType.LIMITER) is b
    assert effect_by_id([a, b], "missing") is None


def test_is_single_instance_returns_false_for_all() -> None:
    for ttype in AudioEffectType:
        assert is_single_instance(ttype) is False


# ---------------------------------------------------------------------------
# Opérations au niveau projet
# ---------------------------------------------------------------------------


def test_add_audio_effect_to_clip_appends_to_list() -> None:
    project = _audio_project()
    effect = add_audio_effect_to_clip(
        project, "clip-1", AudioEffectType.NORMALIZE
    )
    assert effect.id in [e.id for e in project.tracks[0].clips[0].audio_effects]


def test_clip_audio_effects_returns_copy() -> None:
    project = _audio_project()
    add_audio_effect_to_clip(project, "clip-1", AudioEffectType.NORMALIZE)
    effects = clip_audio_effects(project, "clip-1")
    assert len(effects) == 1
    # C'est une copie.
    effects.clear()
    assert clip_audio_effects(project, "clip-1") == effects.__class__(effects) or len(
        clip_audio_effects(project, "clip-1")
    ) == 1


def test_audio_effects_accepted_on_video_clip_with_audio() -> None:
    """Les clips vidéo dont le média porte de l'audio acceptent les effets."""
    project = _project_with_video_audio()
    effect = add_audio_effect_to_clip(
        project, "clip-v", AudioEffectType.NORMALIZE
    )
    assert effect in project.tracks[0].clips[0].audio_effects


def test_audio_effects_rejected_on_subtitle_track() -> None:
    project = Project(
        name="subs",
        tracks=[
            Track(
                id="S1",
                name="S1",
                type="subtitle",
                clips=[
                    Clip(
                        id="c1",
                        asset_id="x",
                        track_id="S1",
                        timeline_start=0.0,
                        source_in=0.0,
                        source_out=1.0,
                        text="hello",
                    )
                ],
            )
        ],
    )
    with pytest.raises(ValueError):
        add_audio_effect_to_clip(project, "c1", AudioEffectType.NORMALIZE)


def test_audio_effects_rejected_on_locked_track() -> None:
    project = _audio_project()
    project.tracks[0].locked = True
    with pytest.raises(ValueError):
        add_audio_effect_to_clip(project, "clip-1", AudioEffectType.NORMALIZE)


def test_remove_audio_effect_from_clip_drops_effect() -> None:
    project = _audio_project()
    effect = add_audio_effect_to_clip(
        project, "clip-1", AudioEffectType.NORMALIZE
    )
    remove_audio_effect_from_clip(project, "clip-1", effect.id)
    assert project.tracks[0].clips[0].audio_effects == []


def test_set_clip_audio_effect_enabled_toggles_flag() -> None:
    project = _audio_project()
    effect = add_audio_effect_to_clip(
        project, "clip-1", AudioEffectType.LIMITER
    )
    updated = set_clip_audio_effect_enabled(
        project, "clip-1", effect.id, False
    )
    assert updated.enabled is False


def test_update_clip_audio_effect_parameters_validates() -> None:
    project = _audio_project()
    effect = add_audio_effect_to_clip(
        project, "clip-1", AudioEffectType.LIMITER
    )
    updated = update_clip_audio_effect_parameters(
        project, "clip-1", effect.id, {"limit_db": -3.0}
    )
    assert updated.parameter("limit_db") == -3.0


def test_move_clip_audio_effect_swaps_order() -> None:
    project = _audio_project()
    a = add_audio_effect_to_clip(project, "clip-1", AudioEffectType.NORMALIZE)
    b = add_audio_effect_to_clip(project, "clip-1", AudioEffectType.LIMITER)
    c = add_audio_effect_to_clip(project, "clip-1", AudioEffectType.COMPRESSOR)
    moved = move_clip_audio_effect(project, "clip-1", a.id, 2)
    # L'effet ``a`` est désormais en queue.
    ids = [e.id for e in project.tracks[0].clips[0].audio_effects]
    assert ids == [b.id, c.id, a.id]
    assert moved.id == a.id


# ---------------------------------------------------------------------------
# Persistance
# ---------------------------------------------------------------------------


def test_roundtrip_preserves_audio_effects(tmp_path) -> None:
    project = _audio_project()
    add_audio_effect_to_clip(project, "clip-1", AudioEffectType.NORMALIZE)
    add_audio_effect_to_clip(project, "clip-1", AudioEffectType.LIMITER)
    target = tmp_path / "audio.kut"
    save_project(project, str(target))
    loaded = load_project(str(target))
    effects = loaded.tracks[0].clips[0].audio_effects
    types = [e.type for e in effects]
    assert types == [AudioEffectType.NORMALIZE, AudioEffectType.LIMITER]
    # Paramètres intacts.
    assert effects[0].params == default_parameters(AudioEffectType.NORMALIZE)


def test_legacy_project_without_audio_effects_loads(tmp_path) -> None:
    """Un projet v10- (sans ``audio_effects``) se charge avec liste vide."""
    project = _audio_project()
    target = tmp_path / "legacy.kut"
    save_project(project, str(target))
    # On simule un ancien fichier en supprimant la clé.
    import json

    raw = json.loads(target.read_text(encoding="utf-8"))
    for track in raw["project"]["sequences"][0]["tracks"]:
        for clip in track["clips"]:
            clip.pop("audio_effects", None)
    target.write_text(json.dumps(raw), encoding="utf-8")

    loaded = load_project(str(target))
    assert loaded.tracks[0].clips[0].audio_effects == []


def test_invalid_audio_effect_entry_is_dropped(tmp_path) -> None:
    """Une entrée d'effet invalide est ignorée sans casser le clip."""
    import json

    project = _audio_project()
    add_audio_effect_to_clip(project, "clip-1", AudioEffectType.NORMALIZE)
    target = tmp_path / "broken.kut"
    save_project(project, str(target))
    raw = json.loads(target.read_text(encoding="utf-8"))
    # On injecte un effet mal formé + un effet valide (id unique).
    raw["project"]["sequences"][0]["tracks"][0]["clips"][0]["audio_effects"] = [
        {"id": "valid", "type": "limiter", "enabled": True,
         "params": {"limit_db": -3.0}},
        {"id": "broken", "type": "alien_type"},  # type inconnu
        {"id": "second_valid", "type": "normalize"},
        # params absents : defaults appliqués
    ]
    target.write_text(json.dumps(raw), encoding="utf-8")

    loaded = load_project(str(target))
    effects = loaded.tracks[0].clips[0].audio_effects
    # ``broken`` est ignoré, les deux valides survivent.
    assert len(effects) == 2
    assert effects[0].type is AudioEffectType.LIMITER


def test_audio_effect_on_subtitle_track_not_serialized(tmp_path) -> None:
    """Les effets audio sur une piste sous-titre ne sont jamais sérialisés."""
    import json

    project = _audio_project()
    target = tmp_path / "subs.kut"
    save_project(project, str(target))
    raw = json.loads(target.read_text(encoding="utf-8"))
    # Ajout artificiel d'une piste sous-titre.
    raw["project"]["sequences"][0]["tracks"].append({
        "id": "S1",
        "name": "S1",
        "type": "subtitle",
        "locked": False,
        "visible": True,
        "muted": False,
        "solo": False,
        "armed": False,
        "height_mode": "default",
        "collapsed": False,
        "volume_db": 0.0,
        "pan": 0.0,
        "clips": [
            {
                "id": "sc1",
                "asset_id": "asset-audio",
                "track_id": "S1",
                "timeline_start": 0.0,
                "source_in": 0.0,
                "source_out": 1.0,
                "enabled": True,
                "label": "",
                "text": "sub",
                "gain_db": 0.0,
                "pan": 0.0,
                "fade_in": 0.0,
                "fade_out": 0.0,
                "transform": {},
                "transform_keyframes": [],
                "time_remapping": {},
                "effects": [],
                "text_style": {},
            }
        ],
    })
    target.write_text(json.dumps(raw), encoding="utf-8")
    loaded = load_project(str(target))
    # Le clip sous-titre a une liste audio_effects vide (jamais peuplée).
    sub_clip = next(
        clip
        for clip in loaded.tracks[1].clips
        if clip.id == "sc1"
    )
    assert sub_clip.audio_effects == []


# ---------------------------------------------------------------------------
# Filtres FFmpeg
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ttype", list(AudioEffectType))
def test_build_clip_audio_effect_filters_emits_one_line(ttype) -> None:
    """Chaque type actif produit au moins un filtre FFmpeg non vide."""
    effect = create_audio_effect(ttype)
    filters = _build_clip_audio_effect_filters((effect,))
    assert len(filters) >= 1
    # Aucun séparateur de filtre ne s'infiltre.
    for line in filters:
        assert "," not in line or line.count(",") <= 4  # aecho peut empiler


def test_build_clip_audio_effect_filters_skips_disabled() -> None:
    effect = create_audio_effect(AudioEffectType.LIMITER).with_enabled(False)
    assert _build_clip_audio_effect_filters((effect,)) == []


def test_build_clip_audio_effect_filters_uses_ffmpeg_names() -> None:
    """Le filtre FFmpeg émis correspond au mapping ``AUDIO_EFFECT_FFMPEG_FILTER``."""
    for ttype in AudioEffectType:
        effect = create_audio_effect(ttype)
        filters = _build_clip_audio_effect_filters((effect,))
        expected_filter = AUDIO_EFFECT_FFMPEG_FILTER[ttype]
        assert any(
            line.startswith(expected_filter) for line in filters
        ), f"{ttype} devrait produire un filtre ``{expected_filter}``"


def test_normalize_filter_uses_loudnorm_keys() -> None:
    effect = create_audio_effect(AudioEffectType.NORMALIZE)
    [line] = _build_clip_audio_effect_filters((effect,))
    assert "I=" in line and "LRA=" in line and "TP=" in line


def test_compressor_filter_uses_acompressor_keys() -> None:
    effect = create_audio_effect(
        AudioEffectType.COMPRESSOR,
        params={
            "threshold_db": -12.0,
            "ratio": 4.0,
            "attack_ms": 10.0,
            "release_ms": 100.0,
            "makeup_db": 6.0,
        },
    )
    [line] = _build_clip_audio_effect_filters((effect,))
    for key in ("threshold", "ratio", "attack", "release", "makeup"):
        assert f"{key}=" in line


def test_reverb_filter_uses_three_aecho_stages() -> None:
    effect = create_audio_effect(
        AudioEffectType.REVERB_LIGHT,
        params={
            "in_gain": 0.5,
            "out_gain": 0.5,
            "delays_ms": 60.0,
            "decays": 0.3,
        },
    )
    [line] = _build_clip_audio_effect_filters((effect,))
    assert line.count("aecho=") == 3


def test_echo_filter_uses_single_aecho() -> None:
    effect = create_audio_effect(AudioEffectType.ECHO_LIGHT)
    [line] = _build_clip_audio_effect_filters((effect,))
    assert line.count("aecho=") == 1


def test_filters_respect_param_bounds() -> None:
    """Tous les paramètres injectés respectent leurs bornes déclarées."""
    for ttype in AudioEffectType:
        defaults = default_parameters(ttype)
        for key, value in defaults.items():
            spec = next(s for s in parameter_specs(ttype) if s.name == key)
            assert spec.minimum <= value <= spec.maximum, (
                f"{ttype}.{key}={value} hors bornes"
            )


def test_audio_layer_carries_effects_through_render_plan() -> None:
    """L'AudioLayer transporte les effets audio dans le plan de rendu."""
    project = _audio_project()
    add_audio_effect_to_clip(project, "clip-1", AudioEffectType.NORMALIZE)
    add_audio_effect_to_clip(project, "clip-1", AudioEffectType.LIMITER)
    layer = _build_audio_layer(
        project.tracks[0].clips[0],
        project.media_assets[0],
        "A1",
        0,
        project.tracks[0],
    )
    assert len(layer.audio_effects) == 2
    assert layer.audio_effects[0].type is AudioEffectType.NORMALIZE


# ---------------------------------------------------------------------------
# Bibliothèque
# ---------------------------------------------------------------------------


def test_builtin_audio_effect_presets_count_is_ten() -> None:
    assert len(builtin_audio_effect_presets()) == 10
    assert BUILTIN_AUDIO_EFFECT_PRESETS_COUNT == 10


def test_builtin_audio_effect_presets_have_unique_ids() -> None:
    ids = [p.id for p in builtin_audio_effect_presets()]
    assert len(ids) == len(set(ids))


def test_effect_type_category_covers_every_type() -> None:
    """Chaque type est associé à une catégorie valide."""
    for ttype in AudioEffectType:
        assert ttype in EFFECT_TYPE_CATEGORY
        assert EFFECT_TYPE_CATEGORY[ttype] in set(AudioEffectPresetCategory)


def test_filter_by_category_dynamics() -> None:
    matches = filter_audio_effect_presets(
        builtin_audio_effect_presets(),
        category=AudioEffectPresetCategory.DYNAMICS,
    )
    ids = {p.id for p in matches}
    assert ids == {"normalize", "compressor", "limiter"}


def test_filter_by_category_cleanup() -> None:
    matches = filter_audio_effect_presets(
        builtin_audio_effect_presets(),
        category=AudioEffectPresetCategory.CLEANUP,
    )
    ids = {p.id for p in matches}
    assert ids == {"voice_enhance", "noise_reduce", "phone_effect"}


def test_filter_by_category_eq() -> None:
    matches = filter_audio_effect_presets(
        builtin_audio_effect_presets(),
        category=AudioEffectPresetCategory.EQ,
    )
    ids = {p.id for p in matches}
    assert ids == {"bass_boost", "treble_boost"}


def test_filter_by_category_spatial() -> None:
    matches = filter_audio_effect_presets(
        builtin_audio_effect_presets(),
        category=AudioEffectPresetCategory.SPATIAL,
    )
    ids = {p.id for p in matches}
    assert ids == {"reverb_light", "echo_light"}


def test_filter_by_search_finds_each_preset() -> None:
    library = builtin_audio_effect_presets()
    for preset in library:
        matches = filter_audio_effect_presets(library, search=preset.id)
        assert preset.id in {p.id for p in matches}


def test_filter_by_favorites_only() -> None:
    library = builtin_audio_effect_presets()
    matches = filter_audio_effect_presets(
        library, favorites=["normalize", "limiter"], favorites_only=True
    )
    assert {p.id for p in matches} == {"normalize", "limiter"}


def test_preset_resolved_params_uses_defaults_plus_override() -> None:
    preset = make_audio_effect_preset(
        preset_id="custom",
        name="Custom",
        description="",
        category=AudioEffectPresetCategory.DYNAMICS,
        effect_type=AudioEffectType.LIMITER,
        params_override={"limit_db": -4.0},
        builtin=False,
    )
    params = preset.resolved_params()
    assert params["limit_db"] == -4.0


def test_preset_to_audio_effect_creates_valid_effect() -> None:
    preset = builtin_audio_effect_presets()[0]
    effect = preset.to_audio_effect()
    assert effect.type is preset.effect_type
    assert effect.enabled is True


def test_make_audio_effect_preset_rejects_empty_name() -> None:
    with pytest.raises(ValueError):
        make_audio_effect_preset(
            preset_id="x",
            name="   ",
            description="",
            category=AudioEffectPresetCategory.DYNAMICS,
            effect_type=AudioEffectType.LIMITER,
        )


def test_make_audio_effect_preset_rejects_too_long_name() -> None:
    with pytest.raises(ValueError):
        make_audio_effect_preset(
            preset_id="x",
            name="x" * (MAX_NAME_LENGTH + 1),
            description="",
            category=AudioEffectPresetCategory.DYNAMICS,
            effect_type=AudioEffectType.LIMITER,
        )


def test_make_user_audio_effect_preset_uses_category_mapping() -> None:
    preset = make_user_audio_effect_preset(
        name="Mon compresseur",
        description="perso",
        effect_type=AudioEffectType.COMPRESSOR,
        params_override={"threshold_db": -15.0},
    )
    assert preset.category is AudioEffectPresetCategory.DYNAMICS
    assert preset.builtin is False


def test_categories_have_labels_and_descriptions() -> None:
    for category in AudioEffectPresetCategory:
        assert category in CATEGORY_LABELS
        assert category in CATEGORY_DESCRIPTIONS


# ---------------------------------------------------------------------------
# Store (favoris + presets utilisateur)
# ---------------------------------------------------------------------------


def test_store_adds_user_preset() -> None:
    preset = make_user_audio_effect_preset(
        name="Perso",
        description="",
        effect_type=AudioEffectType.LIMITER,
        params_override={"limit_db": -4.0},
    )
    store = AudioEffectPresetStore()
    store.add_user_preset(preset)
    assert store.get_preset(preset.id) is preset


def test_store_rejects_builtin_as_user() -> None:
    builtin = builtin_audio_effect_presets()[0]
    store = AudioEffectPresetStore()
    with pytest.raises(ValueError):
        store.add_user_preset(builtin)


def test_store_toggle_favorite() -> None:
    store = AudioEffectPresetStore()
    assert store.toggle_favorite("normalize") is True
    assert store.is_favorite("normalize") is True
    assert store.toggle_favorite("normalize") is False


def test_store_remove_user_preset_drops_favorite() -> None:
    store = AudioEffectPresetStore()
    preset = make_user_audio_effect_preset(
        name="Perso",
        description="",
        effect_type=AudioEffectType.LIMITER,
        params_override={"limit_db": -4.0},
    )
    store.add_user_preset(preset)
    store.set_favorite(preset.id, True)
    store.remove_user_preset(preset.id)
    assert preset.id not in store.favorites()
    assert store.get_preset(preset.id) is None


def test_store_persistence_roundtrip(tmp_path) -> None:
    preset = make_user_audio_effect_preset(
        name="Perso",
        description="",
        effect_type=AudioEffectType.LIMITER,
        params_override={"limit_db": -4.0},
    )
    target = tmp_path / AUDIO_EFFECT_PRESETS_FILE
    save_audio_effect_preset_data(
        [preset], ["normalize"], settings_dir=target.parent
    )
    user_presets, favorites = load_audio_effect_preset_data(
        settings_dir=target.parent
    )
    assert any(p.id == preset.id for p in user_presets)
    assert "normalize" in favorites


def test_store_subscribe_called_on_change() -> None:
    preset = make_user_audio_effect_preset(
        name="Perso",
        description="",
        effect_type=AudioEffectType.LIMITER,
        params_override={"limit_db": -4.0},
    )
    store = AudioEffectPresetStore()
    calls = []
    store.subscribe(lambda: calls.append(1))
    store.add_user_preset(preset)
    store.toggle_favorite(preset.id)
    assert len(calls) == 2


# ---------------------------------------------------------------------------
# Undo / Redo via ProjectHistory
# ---------------------------------------------------------------------------


def test_undo_redo_add_audio_effect() -> None:
    project = _audio_project()
    history = ProjectHistory()
    history.reset(project)

    add_audio_effect_to_clip(project, "clip-1", AudioEffectType.NORMALIZE)
    history.record(project, "Ajouter normalisation audio")
    assert len(project.tracks[0].clips[0].audio_effects) == 1

    # Undo : défait l'ajout.
    restored = history.undo()
    assert restored is not None
    assert restored.tracks[0].clips[0].audio_effects == []

    # Redo : l'ajout revient.
    restored_redo = history.redo()
    assert restored_redo is not None
    assert len(restored_redo.tracks[0].clips[0].audio_effects) == 1


def test_undo_redo_remove_audio_effect() -> None:
    project = _audio_project()
    effect = add_audio_effect_to_clip(
        project, "clip-1", AudioEffectType.LIMITER
    )

    history = ProjectHistory()
    history.reset(project)

    remove_audio_effect_from_clip(project, "clip-1", effect.id)
    history.record(project, "Supprimer limiteur audio")
    assert project.tracks[0].clips[0].audio_effects == []

    restored = history.undo()
    assert restored is not None
    assert len(restored.tracks[0].clips[0].audio_effects) == 1


def test_undo_redo_update_audio_effect_parameters() -> None:
    project = _audio_project()
    effect = add_audio_effect_to_clip(
        project, "clip-1", AudioEffectType.LIMITER
    )

    history = ProjectHistory()
    history.reset(project)

    update_clip_audio_effect_parameters(
        project, "clip-1", effect.id, {"limit_db": -4.0}
    )
    history.record(project, "Modifier paramètres limiteur")
    assert (
        project.tracks[0].clips[0].audio_effects[0].parameter("limit_db")
        == -4.0
    )

    restored = history.undo()
    assert restored is not None
    assert (
        restored.tracks[0].clips[0].audio_effects[0].parameter("limit_db")
        == -1.0  # valeur par défaut
    )


def test_undo_redo_reorder_audio_effects() -> None:
    project = _audio_project()
    a = add_audio_effect_to_clip(
        project, "clip-1", AudioEffectType.NORMALIZE
    )
    b = add_audio_effect_to_clip(
        project, "clip-1", AudioEffectType.LIMITER
    )

    history = ProjectHistory()
    history.reset(project)

    move_clip_audio_effect(project, "clip-1", a.id, 1)
    history.record(project, "Réordonner les effets audio")
    assert [e.id for e in project.tracks[0].clips[0].audio_effects] == [b.id, a.id]

    restored = history.undo()
    assert restored is not None
    assert [e.id for e in restored.tracks[0].clips[0].audio_effects] == [
        a.id,
        b.id,
    ]


def test_undo_redo_disable_audio_effect() -> None:
    project = _audio_project()
    effect = add_audio_effect_to_clip(
        project, "clip-1", AudioEffectType.LIMITER
    )

    history = ProjectHistory()
    history.reset(project)

    set_clip_audio_effect_enabled(project, "clip-1", effect.id, False)
    history.record(project, "Désactiver un effet audio")
    assert project.tracks[0].clips[0].audio_effects[0].enabled is False

    restored = history.undo()
    assert restored is not None
    assert restored.tracks[0].clips[0].audio_effects[0].enabled is True