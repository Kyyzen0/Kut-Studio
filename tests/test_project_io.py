"""Tests pour la persistance des projets Kut-Studio au format ``.kut``."""

import json
from pathlib import Path

import pytest

from core.project_io import (
    CURRENT_VERSION,
    FORMAT_NAME,
    load_project,
    save_project,
)
from core.project_model import Clip, MediaAsset, Project, Track


# ---------------------------------------------------------------------------
# Fixtures / factories
# ---------------------------------------------------------------------------


def _empty_project() -> Project:
    return Project(name="Vide")


def _full_project() -> Project:
    asset_a = MediaAsset(
        id="asset-a",
        path="/tmp/clip_a.mp4",
        name="Clip A",
        duration=12.0,
        width=1920,
        height=1080,
        fps=30.0,
        media_type="video",
    )
    asset_b = MediaAsset(
        id="asset-b",
        path="/tmp/clip_b.mp4",
        name="Clip B",
        duration=8.0,
        width=1280,
        height=720,
        fps=60.0,
        media_type="video",
    )
    track_v1 = Track(
        id="track-v1",
        name="V1",
        type="video",
        clips=[
            Clip(
                id="clip-v1-a",
                asset_id="asset-a",
                track_id="track-v1",
                timeline_start=0.0,
                source_in=1.0,
                source_out=5.5,
                enabled=True,
            ),
            Clip(
                id="clip-v1-b",
                asset_id="asset-b",
                track_id="track-v1",
                timeline_start=5.5,
                source_in=0.0,
                source_out=8.0,
                enabled=False,
            ),
        ],
    )
    track_sub = Track(
        id="track-s1",
        name="S1",
        type="subtitle",
        clips=[
            Clip(
                id="clip-sub",
                asset_id="asset-a",
                track_id="track-s1",
                timeline_start=1.0,
                source_in=0.0,
                source_out=4.0,
                enabled=True,
            ),
        ],
    )
    return Project(
        name="Demo",
        width=1920,
        height=1080,
        fps=30.0,
        media_assets=[asset_a, asset_b],
        tracks=[track_v1, track_sub],
    )


# ---------------------------------------------------------------------------
# Roundtrip basique
# ---------------------------------------------------------------------------


def test_roundtrip_empty_project(tmp_path: Path) -> None:
    """Un projet vide est sérialisé puis rechargé sans perte."""
    target = tmp_path / "empty.kut"

    save_project(_empty_project(), str(target))
    loaded = load_project(str(target))

    assert isinstance(loaded, Project)
    assert loaded.name == "Vide"
    assert loaded.width == 1920
    assert loaded.height == 1080
    assert loaded.fps == pytest.approx(30.0)
    assert loaded.media_assets == []
    assert loaded.tracks == []


def test_roundtrip_full_project_preserves_all_values(tmp_path: Path) -> None:
    """Tous les champs (média, pistes, clips) survivent au roundtrip."""
    original = _full_project()
    target = tmp_path / "full.kut"

    save_project(original, str(target))
    loaded = load_project(str(target))

    assert isinstance(loaded, Project)
    assert loaded.name == original.name
    assert loaded.width == original.width
    assert loaded.height == original.height
    assert loaded.fps == original.fps

    assert len(loaded.media_assets) == len(original.media_assets)
    for src, dst in zip(original.media_assets, loaded.media_assets):
        assert dst.id == src.id
        assert dst.path == src.path
        assert dst.name == src.name
        assert dst.duration == src.duration
        assert dst.width == src.width
        assert dst.height == src.height
        assert dst.fps == src.fps
        assert dst.media_type == src.media_type

    assert len(loaded.tracks) == len(original.tracks)
    for src_track, dst_track in zip(original.tracks, loaded.tracks):
        assert dst_track.id == src_track.id
        assert dst_track.name == src_track.name
        assert dst_track.type == src_track.type
        assert len(dst_track.clips) == len(src_track.clips)
        for src_clip, dst_clip in zip(src_track.clips, dst_track.clips):
            assert dst_clip.id == src_clip.id
            assert dst_clip.asset_id == src_clip.asset_id
            assert dst_clip.track_id == src_clip.track_id
            assert dst_clip.timeline_start == src_clip.timeline_start
            assert dst_clip.source_in == src_clip.source_in
            assert dst_clip.source_out == src_clip.source_out
            assert dst_clip.enabled == src_clip.enabled


def test_enabled_flag_is_preserved_through_roundtrip(tmp_path: Path) -> None:
    """Le booléen ``enabled`` (True et False) est fidèlement conservé."""
    project = Project(
        name="Enabled",
        tracks=[
            Track(
                id="track-1",
                name="V1",
                type="video",
                clips=[
                    Clip(
                        id="c-on",
                        asset_id="a",
                        track_id="track-1",
                        timeline_start=0.0,
                        source_in=0.0,
                        source_out=1.0,
                        enabled=True,
                    ),
                    Clip(
                        id="c-off",
                        asset_id="a",
                        track_id="track-1",
                        timeline_start=1.0,
                        source_in=0.0,
                        source_out=1.0,
                        enabled=False,
                    ),
                ],
            ),
        ],
    )
    target = tmp_path / "enabled.kut"
    save_project(project, str(target))

    loaded = load_project(str(target))
    clips = loaded.tracks[0].clips
    assert clips[0].enabled is True
    assert clips[1].enabled is False


# ---------------------------------------------------------------------------
# Vérification du contenu brut
# ---------------------------------------------------------------------------


def test_saved_file_contains_format_version_and_project(tmp_path: Path) -> None:
    """Le fichier écrit contient bien les clés ``format``, ``version`` et ``project``."""
    target = tmp_path / "project.kut"
    save_project(_empty_project(), str(target))

    raw = json.loads(target.read_text(encoding="utf-8"))
    assert raw["format"] == FORMAT_NAME
    assert raw["version"] == CURRENT_VERSION
    assert isinstance(raw["project"], dict)


# ---------------------------------------------------------------------------
# Erreurs de chargement
# ---------------------------------------------------------------------------


def test_load_project_rejects_missing_file(tmp_path: Path) -> None:
    """Charger un fichier inexistant lève ``FileNotFoundError``."""
    with pytest.raises(FileNotFoundError):
        load_project(str(tmp_path / "ghost.kut"))


def test_load_project_rejects_invalid_json(tmp_path: Path) -> None:
    """Un JSON mal formé est rejeté avec un message compréhensible."""
    target = tmp_path / "broken.kut"
    target.write_text("{not valid json", encoding="utf-8")

    with pytest.raises(ValueError, match="JSON"):
        load_project(str(target))


def test_load_project_rejects_wrong_format(tmp_path: Path) -> None:
    """Un fichier d'un autre format est refusé."""
    target = tmp_path / "wrong.kut"
    target.write_text(
        json.dumps({"format": "other-tool", "version": 1}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Format"):
        load_project(str(target))


def test_load_project_rejects_unsupported_version(tmp_path: Path) -> None:
    """Une version non listée dans ``SUPPORTED_VERSIONS`` est refusée."""
    target = tmp_path / "future.kut"
    payload = {
        "format": FORMAT_NAME,
        "version": 999,
        "project": {"name": "future"},
    }
    target.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="Version"):
        load_project(str(target))


def test_load_project_rejects_non_object_root(tmp_path: Path) -> None:
    """La racine du JSON doit être un objet, pas une liste ou un scalaire."""
    target = tmp_path / "list.kut"
    target.write_text(json.dumps([1, 2, 3]), encoding="utf-8")

    with pytest.raises(ValueError, match="objet"):
        load_project(str(target))


def test_load_project_rejects_missing_project_section(tmp_path: Path) -> None:
    """Un envelope valide mais sans section ``project`` est rejeté."""
    target = tmp_path / "no-project.kut"
    payload = {"format": FORMAT_NAME, "version": CURRENT_VERSION}
    target.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="project"):
        load_project(str(target))


# ---------------------------------------------------------------------------
# Vérification des types reconstruits
# ---------------------------------------------------------------------------


def test_loaded_objects_are_real_dataclasses(tmp_path: Path) -> None:
    """Les objets rechargés sont bien des instances des dataclasses métier."""
    target = tmp_path / "full.kut"
    save_project(_full_project(), str(target))
    loaded = load_project(str(target))

    assert isinstance(loaded, Project)
    assert loaded.media_assets and all(
        isinstance(asset, MediaAsset) for asset in loaded.media_assets
    )
    assert loaded.tracks and all(
        isinstance(track, Track) for track in loaded.tracks
    )
    for track in loaded.tracks:
        assert all(isinstance(clip, Clip) for clip in track.clips)


def test_clip_duration_property_works_after_load(tmp_path: Path) -> None:
    """La propriété calculée ``Clip.duration`` reste cohérente après chargement."""
    target = tmp_path / "duration.kut"
    save_project(_full_project(), str(target))
    loaded = load_project(str(target))

    # clip-v1-a a source_in=1.0 et source_out=5.5 → duration = 4.5
    first_clip = loaded.tracks[0].clips[0]
    assert first_clip.duration == pytest.approx(4.5)


# ---------------------------------------------------------------------------
# Robustesse de l'écriture
# ---------------------------------------------------------------------------


def test_atomic_write_leaves_no_temp_file_on_success(tmp_path: Path) -> None:
    """Après une sauvegarde réussie, seul le fichier final existe dans le dossier."""
    target = tmp_path / "project.kut"
    save_project(_empty_project(), str(target))

    assert list(tmp_path.iterdir()) == [target]


def test_atomic_write_does_not_corrupt_target_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Si le ``rename`` final échoue, le fichier cible existant reste intact."""
    target = tmp_path / "project.kut"
    original_content = "ORIGINAL CONTENT"
    target.write_text(original_content, encoding="utf-8")

    def failing_replace(*args, **kwargs):
        raise OSError("disk full simulation")

    monkeypatch.setattr("core.project_io.os.replace", failing_replace)

    with pytest.raises(OSError):
        save_project(_empty_project(), str(target))

    # Le fichier cible initial est strictement intact.
    assert target.read_text(encoding="utf-8") == original_content
    # Aucun fichier temporaire ne doit rester.
    assert list(tmp_path.iterdir()) == [target]


def test_save_project_creates_missing_parent_directory(tmp_path: Path) -> None:
    """Les dossiers intermédiaires manquants sont créés automatiquement."""
    target = tmp_path / "subdir" / "nested" / "project.kut"

    save_project(_empty_project(), str(target))

    assert target.exists()
    # Et le fichier est re-chargeable.
    loaded = load_project(str(target))
    assert loaded.name == "Vide"
