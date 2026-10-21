"""Tests pour les dataclasses fondamentales définies dans ``core.project_model``."""

import pytest

from core.project_model import Clip, MediaAsset, Project, Track


# ---------------------------------------------------------------------------
# MediaAsset
# ---------------------------------------------------------------------------


def _make_asset(**overrides) -> MediaAsset:
    """Construit un MediaAsset valide avec des valeurs par défaut sobres."""
    defaults = dict(
        id="asset-1",
        path="/tmp/source.mp4",
        name="Source",
        duration=10.0,
        width=1920,
        height=1080,
        fps=30.0,
        media_type="video",
    )
    defaults.update(overrides)
    return MediaAsset(**defaults)


def test_media_asset_can_be_constructed_with_valid_values():
    """Un MediaAsset bien formé est créé sans erreur."""
    asset = _make_asset()

    assert asset.id == "asset-1"
    assert asset.path == "/tmp/source.mp4"
    assert asset.name == "Source"
    assert asset.duration == 10.0
    assert asset.width == 1920
    assert asset.height == 1080
    assert asset.fps == 30.0
    assert asset.media_type == "video"


def test_media_asset_rejects_negative_duration():
    """Une durée négative doit être refusée."""
    with pytest.raises(ValueError, match="durée"):
        _make_asset(duration=-1.0)


def test_media_asset_rejects_zero_or_negative_dimensions():
    """Les dimensions doivent être strictement positives."""
    with pytest.raises(ValueError, match="largeur"):
        _make_asset(width=0)
    with pytest.raises(ValueError, match="hauteur"):
        _make_asset(height=0)
    with pytest.raises(ValueError, match="largeur"):
        _make_asset(width=-10)
    with pytest.raises(ValueError, match="hauteur"):
        _make_asset(height=-10)


def test_media_asset_rejects_non_positive_fps():
    """Le fps doit être strictement positif."""
    with pytest.raises(ValueError, match="fps"):
        _make_asset(fps=0.0)
    with pytest.raises(ValueError, match="fps"):
        _make_asset(fps=-24.0)


# ---------------------------------------------------------------------------
# Clip
# ---------------------------------------------------------------------------


def _make_clip(**overrides) -> Clip:
    """Construit un Clip valide avec des valeurs par défaut sobres."""
    defaults = dict(
        id="clip-1",
        asset_id="asset-1",
        track_id="track-1",
        timeline_start=0.0,
        source_in=1.0,
        source_out=4.0,
    )
    defaults.update(overrides)
    return Clip(**defaults)


def test_clip_duration_is_source_out_minus_source_in():
    """La propriété ``duration`` doit retourner ``source_out - source_in``."""
    clip = _make_clip(source_in=2.0, source_out=7.5)

    assert clip.duration == pytest.approx(5.5)


def test_clip_duration_defaults_to_expected_range():
    """Un clip construit avec les valeurs par défaut a une durée de 3 secondes."""
    clip = _make_clip()

    assert clip.duration == pytest.approx(3.0)


def test_clip_enabled_defaults_to_true():
    """Un nouveau clip est actif par défaut."""
    clip = _make_clip()

    assert clip.enabled is True


def test_clip_label_and_text_default_to_empty_string():
    """Un Clip créé sans préciser ``label`` ou ``text`` doit les avoir vides."""
    clip = Clip(
        id="clip-1",
        asset_id="asset-1",
        track_id="track-1",
        timeline_start=0.0,
        source_in=0.0,
        source_out=2.0,
    )

    assert clip.label == ""
    assert clip.text == ""


def test_clip_rejects_source_out_equal_to_source_in():
    """``source_out`` strictement supérieur à ``source_in`` est obligatoire."""
    with pytest.raises(ValueError, match="source_out"):
        _make_clip(source_in=2.0, source_out=2.0)


def test_clip_rejects_source_out_lower_than_source_in():
    """Un clip inversé (source_out < source_in) doit être refusé."""
    with pytest.raises(ValueError, match="source_out"):
        _make_clip(source_in=5.0, source_out=2.0)


def test_clip_rejects_negative_source_in():
    """``source_in`` ne peut pas être négatif."""
    with pytest.raises(ValueError, match="source_in"):
        _make_clip(source_in=-0.1, source_out=2.0)


def test_clip_rejects_negative_timeline_start():
    """``timeline_start`` ne peut pas être négatif."""
    with pytest.raises(ValueError, match="timeline_start"):
        _make_clip(timeline_start=-1.0)


# ---------------------------------------------------------------------------
# Track
# ---------------------------------------------------------------------------


def test_track_defaults_to_empty_clips_list():
    """Une Track sans clips initialisés doit exposer une liste vide (pas mutable)."""
    track = Track(id="track-1", name="V1", type="video")

    assert track.clips == []


def test_track_clips_list_is_not_shared_between_instances():
    """Chaque Track doit recevoir sa propre liste (pas d'effet de bord entre instances)."""
    track_a = Track(id="a", name="A", type="video")
    track_b = Track(id="b", name="B", type="video")

    track_a.clips.append(_make_clip(id="clip-a"))

    assert track_b.clips == []
    assert track_a.clips[0].id == "clip-a"


def test_track_accepts_multiple_clips():
    """Une Track doit pouvoir stocker plusieurs clips."""
    clip_a = _make_clip(id="clip-a", track_id="track-1")
    clip_b = _make_clip(id="clip-b", track_id="track-1", source_in=10.0, source_out=15.0)
    track = Track(id="track-1", name="V1", type="video", clips=[clip_a, clip_b])

    assert [clip.id for clip in track.clips] == ["clip-a", "clip-b"]


# ---------------------------------------------------------------------------
# Project
# ---------------------------------------------------------------------------


def test_project_defaults_to_1080p_at_30fps():
    """Les valeurs par défaut d'un Project sont 1920x1080 @ 30 fps."""
    project = Project(name="Mon projet")

    assert project.width == 1920
    assert project.height == 1080
    assert project.fps == 30.0
    assert project.media_assets == []
    assert project.tracks == []


def test_project_rejects_non_positive_resolution():
    """La résolution du projet doit être strictement positive."""
    with pytest.raises(ValueError, match="largeur"):
        Project(name="p", width=0)
    with pytest.raises(ValueError, match="hauteur"):
        Project(name="p", height=0)


def test_project_rejects_non_positive_fps():
    """Le fps du projet doit être strictement positif."""
    with pytest.raises(ValueError, match="fps"):
        Project(name="p", fps=0.0)
    with pytest.raises(ValueError, match="fps"):
        Project(name="p", fps=-10.0)


def test_project_composes_full_hierarchy():
    """Un Project doit pouvoir contenir des MediaAssets et des Tracks cohérents."""
    asset = _make_asset(id="asset-1")
    clip = _make_clip(id="clip-1", asset_id="asset-1", track_id="track-1")
    track = Track(id="track-1", name="V1", type="video", clips=[clip])

    project = Project(
        name="Kut-Demo",
        width=1280,
        height=720,
        fps=60.0,
        media_assets=[asset],
        tracks=[track],
    )

    assert project.name == "Kut-Demo"
    assert project.width == 1280
    assert project.height == 720
    assert project.fps == 60.0
    assert project.media_assets == [asset]
    assert project.tracks == [track]
    assert project.tracks[0].clips[0].duration == clip.duration
