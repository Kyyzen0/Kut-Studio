"""Tests de persistance des effets dans le format ``.kut`` (tâche 21.2)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.effects_model import ClipEffect, EffectType, create_effect
from core.project_io import (
    CURRENT_VERSION,
    FORMAT_NAME,
    SUPPORTED_VERSIONS,
    load_project,
    project_payload,
    save_project,
    write_project_payload,
)
from core.project_model import Clip, MediaAsset, Project, Track


def _asset(asset_id: str = "asset-1") -> MediaAsset:
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


def _project_with_effects() -> Project:
    clip = Clip(
        id="clip-1",
        asset_id="asset-1",
        track_id="V1",
        timeline_start=0.0,
        source_in=0.0,
        source_out=10.0,
        effects=[
            create_effect(
                EffectType.COLOR_CORRECTION,
                effect_id="fx-color",
                params={"brightness": 0.25, "contrast": 1.2, "saturation": 0.8},
            ),
            create_effect(
                EffectType.BLUR, effect_id="fx-blur", params={"intensity": 6.0}
            ),
            create_effect(EffectType.SEPIA, effect_id="fx-sepia", enabled=False),
        ],
    )
    audio_clip = Clip(
        id="clip-audio",
        asset_id="asset-audio",
        track_id="A1",
        timeline_start=0.0,
        source_in=0.0,
        source_out=4.0,
    )
    return Project(
        name="Effets",
        media_assets=[_asset(), _asset("asset-audio")],
        tracks=[
            Track(id="V1", name="V1", type="video", clips=[clip]),
            Track(id="A1", name="A1", type="audio", clips=[audio_clip]),
        ],
    )


def _legacy_payload(version: int) -> dict:
    return {
        "format": FORMAT_NAME,
        "version": version,
        "project": {
            "name": f"legacy-{version}",
            "width": 1920,
            "height": 1080,
            "fps": 30.0,
            "media_assets": [
                {
                    "id": "asset-1",
                    "path": "/tmp/clip.mp4",
                    "name": "Test",
                    "duration": 10.0,
                    "width": 1920,
                    "height": 1080,
                    "fps": 30.0,
                    "media_type": "video",
                    "has_audio": False,
                }
            ],
            "tracks": [
                {
                    "id": "V1",
                    "name": "V1",
                    "type": "video",
                    "locked": False,
                    "visible": True,
                    "muted": False,
                    "clips": [
                        {
                            "id": "clip-1",
                            "asset_id": "asset-1",
                            "track_id": "V1",
                            "timeline_start": 0.0,
                            "source_in": 0.0,
                            "source_out": 10.0,
                        }
                    ],
                }
            ],
        },
    }


# ---------------------------------------------------------------------------
# Version et enveloppe
# ---------------------------------------------------------------------------


def test_format_version_is_eleven() -> None:
    """Version 11 : ajout de l'organisation de la bibliothèque (tâche 25).

    La version des styles texte (10) et des effets (9) reste lisible.
    """
    assert CURRENT_VERSION == 14
    assert 10 in SUPPORTED_VERSIONS
    assert 9 in SUPPORTED_VERSIONS


def test_payload_serialises_effects_in_order() -> None:
    payload = project_payload(_project_with_effects())
    clips = payload["project"]["sequences"][0]["tracks"][0]["clips"]
    effects = clips[0]["effects"]

    assert [effect["id"] for effect in effects] == [
        "fx-color",
        "fx-blur",
        "fx-sepia",
    ]
    assert effects[0]["type"] == "color_correction"
    assert effects[0]["params"]["brightness"] == pytest.approx(0.25)
    assert effects[1]["params"]["intensity"] == pytest.approx(6.0)
    assert effects[2]["enabled"] is False


def test_audio_clip_serialises_an_empty_effect_list() -> None:
    payload = project_payload(_project_with_effects())
    audio_clip = payload["project"]["sequences"][0]["tracks"][1]["clips"][0]
    assert audio_clip["effects"] == []


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


def test_roundtrip_preserves_every_effect(tmp_path: Path) -> None:
    target = tmp_path / "effects.kut"
    save_project(_project_with_effects(), str(target))

    loaded = load_project(str(target))
    clip = loaded.tracks[0].clips[0]

    assert [effect.id for effect in clip.effects] == [
        "fx-color",
        "fx-blur",
        "fx-sepia",
    ]
    color = clip.effects[0]
    assert color.type is EffectType.COLOR_CORRECTION
    assert color.enabled is True
    assert color.params == {
        "brightness": pytest.approx(0.25),
        "contrast": pytest.approx(1.2),
        "saturation": pytest.approx(0.8),
    }
    blur = clip.effects[1]
    assert blur.type is EffectType.BLUR
    assert blur.params["intensity"] == pytest.approx(6.0)
    assert clip.effects[2].enabled is False


def test_roundtrip_keeps_effect_objects_as_dataclasses(tmp_path: Path) -> None:
    target = tmp_path / "effects.kut"
    save_project(_project_with_effects(), str(target))
    loaded = load_project(str(target))

    assert all(
        isinstance(effect, ClipEffect) for effect in loaded.tracks[0].clips[0].effects
    )


def test_projects_without_effects_roundtrip_to_an_empty_list(tmp_path: Path) -> None:
    project = Project(name="Vide", media_assets=[_asset()], tracks=[
        Track(id="V1", name="V1", type="video", clips=[
            Clip(
                id="clip-1", asset_id="asset-1", track_id="V1",
                timeline_start=0.0, source_in=0.0, source_out=2.0,
            )
        ])
    ])
    target = tmp_path / "empty-effects.kut"
    save_project(project, str(target))

    loaded = load_project(str(target))
    assert loaded.tracks[0].clips[0].effects == []


# ---------------------------------------------------------------------------
# Compatibilité des anciennes versions (1 à 8)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("version", [1, 2, 3, 4, 5, 6, 7, 8])
def test_legacy_projects_open_with_empty_effects(tmp_path: Path, version: int) -> None:
    target = tmp_path / f"legacy-{version}.kut"
    target.write_text(json.dumps(_legacy_payload(version)), encoding="utf-8")

    loaded = load_project(str(target))

    assert loaded.tracks[0].clips[0].effects == []


# ---------------------------------------------------------------------------
# JSON partiellement invalide
# ---------------------------------------------------------------------------


def _payload_with_effects(effects: list) -> dict:
    payload = _legacy_payload(CURRENT_VERSION)
    payload["project"]["tracks"][0]["clips"][0]["effects"] = effects
    return payload


def test_partially_invalid_effects_do_not_block_loading(tmp_path: Path) -> None:
    """Seules les entrées invalides sont ignorées ; les valides restent."""
    effects = [
        "pas-un-objet",
        {"id": "fx-missing-type"},
        {"id": "fx-unknown-type", "type": "not-a-type"},
        {"id": "fx-bad-bounds", "type": "blur", "params": {"intensity": 999.0}},
        {"id": "fx-bad-param", "type": "blur", "params": {"radius": 1.0}},
        {"type": "sepia"},  # identifiant manquant
        {"id": "fx-bad-params", "type": "blur", "params": [1, 2, 3]},
        {"id": "fx-non-numeric", "type": "blur", "params": {"intensity": "x"}},
        {"id": "fx-ok-1", "type": "blur", "enabled": True, "params": {"intensity": 4.0}},
        {"id": "fx-ok-2", "type": "black_and_white", "enabled": False},
    ]
    target = tmp_path / "partial.kut"
    target.write_text(json.dumps(_payload_with_effects(effects)), encoding="utf-8")

    loaded = load_project(str(target))
    loaded_effects = loaded.tracks[0].clips[0].effects

    assert [effect.id for effect in loaded_effects] == ["fx-ok-1", "fx-ok-2"]
    assert loaded_effects[0].type is EffectType.BLUR
    assert loaded_effects[0].params["intensity"] == pytest.approx(4.0)
    assert loaded_effects[1].enabled is False


def test_duplicate_effect_ids_are_ignored_on_load(tmp_path: Path) -> None:
    effects = [
        {"id": "fx-1", "type": "blur"},
        {"id": "fx-1", "type": "sharpen"},
    ]
    target = tmp_path / "dup.kut"
    target.write_text(json.dumps(_payload_with_effects(effects)), encoding="utf-8")

    loaded = load_project(str(target))
    loaded_effects = loaded.tracks[0].clips[0].effects

    assert [effect.id for effect in loaded_effects] == ["fx-1"]
    assert loaded_effects[0].type is EffectType.BLUR


def test_effects_key_of_wrong_type_is_ignored(tmp_path: Path) -> None:
    payload = _payload_with_effects("pas-une-liste")
    target = tmp_path / "wrong-type.kut"
    target.write_text(json.dumps(payload), encoding="utf-8")

    loaded = load_project(str(target))
    assert loaded.tracks[0].clips[0].effects == []


def test_effects_on_a_non_video_track_are_dropped(tmp_path: Path) -> None:
    payload = _legacy_payload(CURRENT_VERSION)
    payload["project"]["tracks"][0]["type"] = "subtitle"
    payload["project"]["tracks"][0]["clips"][0]["effects"] = [
        {"id": "fx-1", "type": "sepia"}
    ]
    target = tmp_path / "subtitle.kut"
    target.write_text(json.dumps(payload), encoding="utf-8")

    loaded = load_project(str(target))
    assert loaded.tracks[0].clips[0].effects == []


# ---------------------------------------------------------------------------
# Écriture atomique
# ---------------------------------------------------------------------------


def test_effects_write_leaves_no_temporary_file(tmp_path: Path) -> None:
    target = tmp_path / "effects.kut"
    save_project(_project_with_effects(), str(target))

    leftovers = [
        path.name for path in tmp_path.iterdir() if path.name.endswith(".tmp")
    ]
    assert leftovers == []


def test_failed_write_keeps_the_previous_project_intact(tmp_path: Path) -> None:
    target = tmp_path / "effects.kut"
    save_project(_project_with_effects(), str(target))
    original = target.read_text(encoding="utf-8")

    with pytest.raises(TypeError):
        write_project_payload({"project": object()}, str(target))

    assert target.read_text(encoding="utf-8") == original
    assert load_project(str(target)).tracks[0].clips[0].effects
