"""Tests du modèle métier des effets de clip (tâche 21.1)."""

from __future__ import annotations

import dataclasses

import pytest

from core.effects_model import (
    EFFECT_PARAMETER_SPECS,
    SINGLE_INSTANCE_EFFECTS,
    ClipEffect,
    EffectType,
    add_effect,
    add_effect_to_clip,
    clip_effects,
    create_effect,
    default_parameters,
    effect_by_id,
    effect_by_type,
    enabled_effects,
    is_single_instance,
    move_clip_effect,
    move_effect,
    parameter_specs,
    remove_effect,
    remove_effect_from_clip,
    reorder_effect,
    set_clip_effect_enabled,
    set_effect_enabled,
    update_clip_effect_parameters,
    update_effect_parameters,
    validate_parameters,
)
from core.project_model import Clip, MediaAsset, Project, Track


# ---------------------------------------------------------------------------
# Fabriques
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


def _project() -> Project:
    """Projet avec une piste vidéo, une piste audio et une piste sous-titres."""
    video = Track(
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
            )
        ],
    )
    audio = Track(
        id="A1",
        name="A1",
        type="audio",
        clips=[
            Clip(
                id="clip-audio",
                asset_id="asset-audio",
                track_id="A1",
                timeline_start=0.0,
                source_in=0.0,
                source_out=5.0,
            )
        ],
    )
    subtitle = Track(
        id="S1",
        name="S1",
        type="subtitle",
        clips=[
            Clip(
                id="clip-subtitle",
                asset_id="asset-subtitle",
                track_id="S1",
                timeline_start=0.0,
                source_in=0.0,
                source_out=5.0,
                text="Bonjour",
            )
        ],
    )
    return Project(
        name="Test effets",
        media_assets=[_asset(), _asset("asset-audio"), _asset("asset-subtitle")],
        tracks=[video, audio, subtitle],
    )


def _effect(effect_type, effect_id: str = "fx-1", **kwargs) -> ClipEffect:
    return create_effect(effect_type, effect_id=effect_id, **kwargs)


# ---------------------------------------------------------------------------
# Catalogue et valeurs par défaut
# ---------------------------------------------------------------------------


def test_six_effect_types_are_available() -> None:
    """Le catalogue expose exactement les six effets attendus."""
    assert {member.value for member in EffectType} == {
        "color_correction",
        "blur",
        "sharpen",
        "vignette",
        "black_and_white",
        "sepia",
    }


def test_every_effect_type_has_explicit_defaults() -> None:
    """Chaque type déclare ses bornes, donc des défauts explicites."""
    for effect_type in EffectType:
        specs = parameter_specs(effect_type)
        assert specs is EFFECT_PARAMETER_SPECS[effect_type]
        defaults = default_parameters(effect_type)
        for spec in specs:
            assert defaults[spec.name] == pytest.approx(spec.default)
            assert spec.minimum <= spec.default <= spec.maximum


def test_color_correction_defaults_are_neutral() -> None:
    assert default_parameters(EffectType.COLOR_CORRECTION) == {
        "brightness": pytest.approx(0.0),
        "contrast": pytest.approx(1.0),
        "saturation": pytest.approx(1.0),
    }


def test_black_and_white_and_sepia_have_no_parameters() -> None:
    assert parameter_specs(EffectType.BLACK_AND_WHITE) == ()
    assert parameter_specs(EffectType.SEPIA) == ()
    assert default_parameters(EffectType.BLACK_AND_WHITE) == {}


def test_single_instance_classification() -> None:
    assert is_single_instance(EffectType.COLOR_CORRECTION) is True
    assert is_single_instance("sepia") is True
    assert is_single_instance(EffectType.BLUR) is False
    assert EffectType.BLACK_AND_WHITE in SINGLE_INSTANCE_EFFECTS


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_create_effect_without_parameters_uses_defaults() -> None:
    effect = create_effect(EffectType.BLUR, effect_id="fx-blur")

    assert effect.params == {"intensity": pytest.approx(2.0)}
    assert effect.enabled is True
    assert effect.parameter("intensity") == pytest.approx(2.0)


def test_create_effect_rejects_unknown_type() -> None:
    with pytest.raises(ValueError):
        create_effect("not-a-real-effect", effect_id="fx")


def test_validate_parameters_rejects_unknown_parameter() -> None:
    with pytest.raises(ValueError, match="inconnu"):
        validate_parameters(EffectType.BLUR, {"radius": 3.0})


def test_validate_parameters_rejects_non_numeric() -> None:
    with pytest.raises(ValueError, match="numérique"):
        validate_parameters(EffectType.BLUR, {"intensity": "beaucoup"})


def test_validate_parameters_rejects_bool_as_number() -> None:
    with pytest.raises(ValueError, match="numérique"):
        validate_parameters(EffectType.BLUR, {"intensity": True})


def test_validate_parameters_rejects_nan() -> None:
    with pytest.raises(ValueError, match="NaN"):
        validate_parameters(EffectType.VIGNETTE, {"intensity": float("nan")})


@pytest.mark.parametrize(
    "effect_type,params",
    [
        (EffectType.COLOR_CORRECTION, {"brightness": 2.0}),
        (EffectType.COLOR_CORRECTION, {"contrast": -1.0}),
        (EffectType.BLUR, {"intensity": -0.1}),
        (EffectType.SHARPEN, {"intensity": 9.0}),
        (EffectType.VIGNETTE, {"intensity": 1.5}),
    ],
)
def test_validate_parameters_rejects_out_of_bounds(effect_type, params) -> None:
    with pytest.raises(ValueError, match="bornes"):
        validate_parameters(effect_type, params)


def test_clip_effect_rejects_empty_id() -> None:
    with pytest.raises(ValueError, match="identifiant"):
        ClipEffect(id="", type=EffectType.BLUR)


def test_clip_effect_accepts_string_type() -> None:
    effect = ClipEffect(id="fx", type="blur")  # type: ignore[arg-type]
    assert effect.type is EffectType.BLUR


# ---------------------------------------------------------------------------
# Immutabilité et absence de mutation inattendue
# ---------------------------------------------------------------------------


def test_clip_effect_is_frozen() -> None:
    effect = _effect(EffectType.BLUR)
    with pytest.raises(dataclasses.FrozenInstanceError):
        effect.enabled = False  # type: ignore[misc]


def test_clip_effect_copies_its_parameters() -> None:
    """Mutuer le dict source ne doit pas contaminer l'effet."""
    source = {"intensity": 3.0}
    effect = ClipEffect(id="fx", type=EffectType.BLUR, params=source)
    source["intensity"] = 99.0

    assert effect.params["intensity"] == pytest.approx(3.0)


def test_with_parameter_returns_a_new_effect() -> None:
    effect = _effect(EffectType.BLUR)
    updated = effect.with_parameter("intensity", 7.0)

    assert updated is not effect
    assert updated.params["intensity"] == pytest.approx(7.0)
    assert effect.params["intensity"] == pytest.approx(2.0)


def test_pure_functions_do_not_mutate_the_input_list() -> None:
    first = _effect(EffectType.BLUR, "fx-1")
    second = _effect(EffectType.VIGNETTE, "fx-2")
    original = [first, second]

    add_effect(original, _effect(EffectType.SHARPEN, "fx-3"))
    remove_effect(original, "fx-1")
    set_effect_enabled(original, "fx-2", False)
    move_effect(original, "fx-1", 1)

    assert original == [first, second]


def test_new_clips_do_not_share_the_default_effects_list() -> None:
    """Deux clips neufs ne partagent pas la même liste d'effets."""
    a = Clip(
        id="a", asset_id="x", track_id="V1", timeline_start=0.0,
        source_in=0.0, source_out=1.0,
    )
    b = Clip(
        id="b", asset_id="x", track_id="V1", timeline_start=0.0,
        source_in=0.0, source_out=1.0,
    )
    a.effects = add_effect(a.effects, _effect(EffectType.SEPIA))

    assert len(a.effects) == 1
    assert b.effects == []


# ---------------------------------------------------------------------------
# Ordre, activation, suppression
# ---------------------------------------------------------------------------


def test_add_effect_appends_in_order() -> None:
    effects = [_effect(EffectType.BLUR, "fx-1")]
    effects = add_effect(effects, _effect(EffectType.SHARPEN, "fx-2"))
    effects = add_effect(effects, _effect(EffectType.BLUR, "fx-3"))

    assert [effect.id for effect in effects] == ["fx-1", "fx-2", "fx-3"]


def test_add_effect_rejects_duplicate_id() -> None:
    effects = [_effect(EffectType.BLUR, "fx-1")]
    with pytest.raises(ValueError, match="existe déjà"):
        add_effect(effects, _effect(EffectType.SHARPEN, "fx-1"))


def test_add_effect_rejects_duplicate_single_instance() -> None:
    effects = [_effect(EffectType.COLOR_CORRECTION, "fx-1")]
    with pytest.raises(ValueError, match="qu'une fois"):
        add_effect(effects, _effect(EffectType.COLOR_CORRECTION, "fx-2"))


def test_add_effect_allows_stacking_blur() -> None:
    effects = [_effect(EffectType.BLUR, "fx-1")]
    effects = add_effect(effects, _effect(EffectType.BLUR, "fx-2"))
    assert [effect.id for effect in effects] == ["fx-1", "fx-2"]


def test_remove_effect_keeps_order() -> None:
    effects = [
        _effect(EffectType.BLUR, "fx-1"),
        _effect(EffectType.SHARPEN, "fx-2"),
        _effect(EffectType.SEPIA, "fx-3"),
    ]
    result = remove_effect(effects, "fx-2")

    assert [effect.id for effect in result] == ["fx-1", "fx-3"]


def test_remove_effect_unknown_id_raises() -> None:
    with pytest.raises(KeyError):
        remove_effect([_effect(EffectType.BLUR, "fx-1")], "fx-9")


def test_set_effect_enabled_toggles_only_the_target() -> None:
    effects = [
        _effect(EffectType.BLUR, "fx-1"),
        _effect(EffectType.SHARPEN, "fx-2"),
    ]
    result = set_effect_enabled(effects, "fx-2", False)

    assert result[0].enabled is True
    assert result[1].enabled is False


def test_enabled_effects_filters_disabled() -> None:
    effects = [
        _effect(EffectType.BLUR, "fx-1"),
        _effect(EffectType.SHARPEN, "fx-2", enabled=False),
    ]
    assert [effect.id for effect in enabled_effects(effects)] == ["fx-1"]


def test_update_effect_parameters_merges_and_validates() -> None:
    effects = [_effect(EffectType.COLOR_CORRECTION, "fx-1")]
    result = update_effect_parameters(effects, "fx-1", {"brightness": 0.5})

    assert result[0].params == {
        "brightness": pytest.approx(0.5),
        "contrast": pytest.approx(1.0),
        "saturation": pytest.approx(1.0),
    }
    assert effects[0].params["brightness"] == pytest.approx(0.0)


def test_update_effect_parameters_rejects_invalid_values() -> None:
    effects = [_effect(EffectType.VIGNETTE, "fx-1")]
    with pytest.raises(ValueError, match="bornes"):
        update_effect_parameters(effects, "fx-1", {"intensity": 5.0})


def test_move_effect_up_and_down() -> None:
    effects = [
        _effect(EffectType.BLUR, "fx-1"),
        _effect(EffectType.SHARPEN, "fx-2"),
        _effect(EffectType.SEPIA, "fx-3"),
    ]
    moved_down = move_effect(effects, "fx-1", 1)
    assert [effect.id for effect in moved_down] == ["fx-2", "fx-1", "fx-3"]

    moved_up = move_effect(moved_down, "fx-1", -1)
    assert [effect.id for effect in moved_up] == ["fx-1", "fx-2", "fx-3"]


def test_move_effect_is_bounded_at_the_edges() -> None:
    effects = [
        _effect(EffectType.BLUR, "fx-1"),
        _effect(EffectType.SHARPEN, "fx-2"),
    ]
    assert [e.id for e in move_effect(effects, "fx-1", -5)] == ["fx-1", "fx-2"]
    assert [e.id for e in move_effect(effects, "fx-2", 5)] == ["fx-1", "fx-2"]


def test_reorder_effect_to_explicit_index() -> None:
    effects = [
        _effect(EffectType.BLUR, "fx-1"),
        _effect(EffectType.SHARPEN, "fx-2"),
        _effect(EffectType.SEPIA, "fx-3"),
    ]
    reordered = reorder_effect(effects, "fx-3", 0)
    assert [effect.id for effect in reordered] == ["fx-3", "fx-1", "fx-2"]


def test_effect_by_id_and_by_type() -> None:
    effects = [_effect(EffectType.BLUR, "fx-1")]
    assert effect_by_id(effects, "fx-1") is effects[0]
    assert effect_by_id(effects, "missing") is None
    assert effect_by_type(effects, EffectType.BLUR) is effects[0]
    assert effect_by_type(effects, EffectType.SEPIA) is None


# ---------------------------------------------------------------------------
# Opérations au niveau projet : vidéo uniquement
# ---------------------------------------------------------------------------


def test_add_effect_to_a_video_clip() -> None:
    project = _project()
    effect = add_effect_to_clip(
        project, "clip-video", EffectType.COLOR_CORRECTION, effect_id="fx-1"
    )

    assert effect.id == "fx-1"
    assert clip_effects(project, "clip-video") == [effect]


def test_add_duplicate_single_effect_to_clip_is_refused() -> None:
    project = _project()
    add_effect_to_clip(project, "clip-video", EffectType.SEPIA, effect_id="fx-1")
    with pytest.raises(ValueError):
        add_effect_to_clip(
            project, "clip-video", EffectType.SEPIA, effect_id="fx-2"
        )


def test_operations_reject_audio_clip() -> None:
    project = _project()
    with pytest.raises(ValueError, match="vidéo"):
        add_effect_to_clip(project, "clip-audio", EffectType.BLUR)
    with pytest.raises(ValueError, match="vidéo"):
        clip_effects(project, "clip-audio")


def test_operations_reject_subtitle_clip() -> None:
    project = _project()
    with pytest.raises(ValueError, match="vidéo"):
        add_effect_to_clip(project, "clip-subtitle", EffectType.BLUR)


def test_operations_reject_unknown_clip() -> None:
    project = _project()
    with pytest.raises(KeyError):
        add_effect_to_clip(project, "ghost", EffectType.BLUR)


def test_operations_reject_locked_track() -> None:
    project = _project()
    project.tracks[0].locked = True
    with pytest.raises(ValueError, match="verrouillée"):
        add_effect_to_clip(project, "clip-video", EffectType.BLUR)


def test_project_level_toggle_remove_and_reorder() -> None:
    project = _project()
    add_effect_to_clip(project, "clip-video", EffectType.BLUR, effect_id="fx-1")
    add_effect_to_clip(
        project, "clip-video", EffectType.SHARPEN, effect_id="fx-2"
    )

    set_clip_effect_enabled(project, "clip-video", "fx-1", False)
    assert clip_effects(project, "clip-video")[0].enabled is False

    move_clip_effect(project, "clip-video", "fx-2", -1)
    assert [e.id for e in clip_effects(project, "clip-video")] == ["fx-2", "fx-1"]

    removed = remove_effect_from_clip(project, "clip-video", "fx-2")
    assert removed.id == "fx-2"
    assert [e.id for e in clip_effects(project, "clip-video")] == ["fx-1"]


def test_project_level_parameter_update() -> None:
    project = _project()
    add_effect_to_clip(project, "clip-video", EffectType.BLUR, effect_id="fx-1")
    updated = update_clip_effect_parameters(
        project, "clip-video", "fx-1", {"intensity": 9.0}
    )

    assert updated.parameter("intensity") == pytest.approx(9.0)
