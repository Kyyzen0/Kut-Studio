"""Tests unitaires de ``core.track_operations`` (tâche 14)."""

from __future__ import annotations

import pytest

from core.project_factory import create_default_project
from core.project_io import load_project, save_project
from core.project_model import Clip, MediaAsset, Project, Track
from core.track_operations import (
    add_track,
    collect_track_ids,
    is_track_editable,
    move_track,
    remove_track,
    rename_track,
    set_track_locked,
    set_track_muted,
    set_track_visible,
    visible_video_track_ids,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _new_project() -> Project:
    """Crée un projet minimaliste à partir de la factory (V1 + A1)."""
    return create_default_project()


def _make_video_asset(asset_id: str = "asset-v", duration: float = 5.0) -> MediaAsset:
    return MediaAsset(
        id=asset_id,
        path="/tmp/v.mp4",
        name="Vid",
        duration=duration,
        width=160,
        height=90,
        fps=15.0,
        media_type="video",
    )


def _add_clip(project: Project, track_id: str, asset_id: str = "asset-v") -> Clip:
    """Ajoute un clip sur la piste ``track_id``."""
    from core.timeline_operations import add_clip_to_track

    return add_clip_to_track(project, asset_id, track_id, 0.0)


# ---------------------------------------------------------------------------
# Ajout
# ---------------------------------------------------------------------------


def test_add_video_track_assigns_next_v_index():
    project = _new_project()
    track = add_track(project, "video", name="Camera A")
    # V1 et V2 existent déjà (factory), le suivant est V3.
    assert track.id == "V3"
    assert track.name == "Camera A"


def test_add_audio_track_returns_a2():
    project = _new_project()
    track = add_track(project, "audio")
    # A1 existe déjà (factory), le suivant est A2.
    assert track.id == "A2"
    assert track.type == "audio"


def test_add_subtitle_track_returns_s2():
    project = _new_project()
    track = add_track(project, "subtitle")
    # S1 existe déjà, le suivant est S2.
    assert track.id == "S2"
    assert track.type == "subtitle"


def test_add_unknown_type_raises_value_error():
    project = _new_project()
    with pytest.raises(ValueError):
        add_track(project, "midi")


def test_add_many_tracks_assigns_increasing_indices():
    project = _new_project()
    last_seen = 2  # V2 existe par défaut.
    for expected in range(3, 8):
        track = add_track(project, "video")
        assert track.id == f"V{expected}"
        last_seen = expected


def test_add_duplicate_name_suffixes_disambiguated():
    project = _new_project()
    add_track(project, "video", name="Camera")
    add_track(project, "video", name="Camera")
    last = project.tracks[-1]
    assert last.name.startswith("Camera")
    assert last.name != "Camera"


# ---------------------------------------------------------------------------
# Suppression
# ---------------------------------------------------------------------------


def test_remove_track_drops_empty_track():
    project = _new_project()
    add_track(project, "audio", name="Tmp")
    empty_id = "A2"
    before = len(project.tracks)
    remove_track(project, empty_id)
    assert len(project.tracks) == before - 1


def test_remove_unknown_track_raises_key_error():
    project = _new_project()
    with pytest.raises(KeyError):
        remove_track(project, "V999")


def test_remove_non_empty_track_raises_value_error():
    project = _new_project()
    project.media_assets.append(_make_video_asset("v"))
    _add_clip(project, "V1", asset_id="v")
    with pytest.raises(ValueError):
        remove_track(project, "V1")


# ---------------------------------------------------------------------------
# Renommage
# ---------------------------------------------------------------------------


def test_rename_track_changes_name_only():
    project = _new_project()
    rename_track(project, "V1", "Indispensable")
    track = project.tracks[0]
    assert track.id == "V1"
    assert track.name == "Indispensable"


def test_rename_track_rejects_empty_name():
    project = _new_project()
    with pytest.raises(ValueError):
        rename_track(project, "V1", "   ")


def test_rename_track_rejects_duplicate_name():
    project = _new_project()
    add_track(project, "video", name="Camera")
    with pytest.raises(ValueError):
        rename_track(project, "V1", "Camera")


# ---------------------------------------------------------------------------
# Réorganisation (move)
# ---------------------------------------------------------------------------


def test_move_track_reorders_track_list():
    project = _new_project()
    add_track(project, "video", name="B")
    add_track(project, "video", name="C")
    move_track(project, "V3", 0)
    assert project.tracks[0].id == "V3"


def test_move_track_clamped_to_bounds():
    project = _new_project()
    add_track(project, "video", name="B")
    move_track(project, "V1", 999)
    assert project.tracks[-1].id == "V1"


def test_move_locked_track_raises_value_error():
    project = _new_project()
    set_track_locked(project, "V1", True)
    with pytest.raises(ValueError):
        move_track(project, "V1", 0)


# ---------------------------------------------------------------------------
# Verrouillage / visibilité / muet
# ---------------------------------------------------------------------------


def test_set_locked_marks_track_locked():
    project = _new_project()
    set_track_locked(project, "V1", True)
    assert project.tracks[0].locked is True


def test_set_visible_hides_track():
    project = _new_project()
    set_track_visible(project, "V1", False)
    assert project.tracks[0].visible is False


def test_set_muted_mutes_audio_track():
    project = _new_project()
    set_track_muted(project, "A1", True)
    audio_track = next(t for t in project.tracks if t.id == "A1")
    assert audio_track.muted is True
    # V1 reste intact (muet ne s'applique qu'aux pistes audio).
    video_track = next(t for t in project.tracks if t.id == "V1")
    assert video_track.muted is False


def test_is_track_editable_returns_false_for_locked_track():
    project = _new_project()
    set_track_locked(project, "V1", True)
    assert is_track_editable(project, "V1") is False
    # Piste A1 non verrouillée.
    assert is_track_editable(project, "A1") is True


# ---------------------------------------------------------------------------
# Helpers de collecte
# ---------------------------------------------------------------------------


def test_collect_track_ids_filters_by_type():
    project = _new_project()
    add_track(project, "video")
    add_track(project, "audio")
    videos = collect_track_ids(project, "video")
    audios = collect_track_ids(project, "audio")
    assert all(t.startswith("V") for t in videos)
    assert all(t.startswith("A") for t in audios)


def test_visible_video_track_ids_excludes_hidden():
    project = _new_project()
    set_track_visible(project, "V1", False)
    assert "V1" not in visible_video_track_ids(project)


# ---------------------------------------------------------------------------
# Compatibilité ascendante (.kut anciens)
# ---------------------------------------------------------------------------


def test_legacy_project_loads_with_default_track_states(tmp_path):
    """Un projet v3 sans `locked` / `visible` / `muted` charge avec defaults."""
    project = _new_project()
    out_path = tmp_path / "legacy.kut"
    save_project(project, str(out_path))
    reloaded = load_project(str(out_path))
    assert reloaded.tracks[0].locked is False
    assert reloaded.tracks[0].visible is True
    assert reloaded.tracks[0].muted is False


def test_roundtrip_preserves_track_locked_visible_muted(tmp_path):
    project = _new_project()
    add_track(project, "video", name="Locked")
    set_track_locked(project, "V2", True)
    set_track_visible(project, "V2", False)
    set_track_muted(project, "V2", True)
    out_path = tmp_path / "state.kut"
    save_project(project, str(out_path))
    reloaded = load_project(str(out_path))
    target = next(t for t in reloaded.tracks if t.id == "V2")
    assert target.locked is True
    assert target.visible is False
    assert target.muted is True
