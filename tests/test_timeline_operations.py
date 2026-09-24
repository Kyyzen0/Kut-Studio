"""Tests pour les opérations métier de timeline (Project / Track / Clip)."""

import pytest

from core.project_model import Clip, MediaAsset, Project, Track
from core.timeline_operations import (
    cut_clip,
    delete_clip,
    find_clip,
    find_track,
    move_clip,
    trim_clip_left,
    trim_clip_right,
)


# ---------------------------------------------------------------------------
# Fixtures / factories
# ---------------------------------------------------------------------------


def _make_project() -> Project:
    """Projet de test avec 2 médias (asset-a 10s, asset-b 8s) et 2 pistes.

    V1 contient v1-a (lié à asset-a) et v1-b (lié à asset-b).
    V2 contient v2-a (lié à asset-a), ce qui permet de vérifier que
    les opérations ciblent bien le bon clip parmi plusieurs pistes.
    """
    asset_a = MediaAsset(
        id="asset-a",
        path="/tmp/a.mp4",
        name="A",
        duration=10.0,
        width=1920,
        height=1080,
        fps=30.0,
        media_type="video",
    )
    asset_b = MediaAsset(
        id="asset-b",
        path="/tmp/b.mp4",
        name="B",
        duration=8.0,
        width=1280,
        height=720,
        fps=24.0,
        media_type="video",
    )
    track_v1 = Track(
        id="V1",
        name="V1",
        type="video",
        clips=[
            Clip(
                id="v1-a",
                asset_id="asset-a",
                track_id="V1",
                timeline_start=0.0,
                source_in=0.0,
                source_out=4.0,
            ),
            Clip(
                id="v1-b",
                asset_id="asset-b",
                track_id="V1",
                timeline_start=4.0,
                source_in=0.0,
                source_out=4.0,
            ),
        ],
    )
    track_v2 = Track(
        id="V2",
        name="V2",
        type="video",
        clips=[
            Clip(
                id="v2-a",
                asset_id="asset-a",
                track_id="V2",
                timeline_start=1.0,
                source_in=5.0,
                source_out=9.0,
            ),
        ],
    )
    return Project(
        name="Test",
        width=1920,
        height=1080,
        fps=30.0,
        media_assets=[asset_a, asset_b],
        tracks=[track_v1, track_v2],
    )


# ---------------------------------------------------------------------------
# Recherche
# ---------------------------------------------------------------------------


def test_find_track_returns_matching_track() -> None:
    project = _make_project()
    track = find_track(project, "V1")
    assert track.id == "V1"
    assert track.name == "V1"


def test_find_track_raises_for_unknown_track() -> None:
    project = _make_project()
    with pytest.raises(KeyError, match="V999"):
        find_track(project, "V999")


def test_find_clip_finds_clip_in_any_track() -> None:
    project = _make_project()
    clip_v1 = find_clip(project, "v1-a")
    assert clip_v1.id == "v1-a"
    assert clip_v1.timeline_start == 0.0

    clip_v2 = find_clip(project, "v2-a")
    assert clip_v2.id == "v2-a"
    assert clip_v2.timeline_start == 1.0


def test_find_clip_raises_for_unknown_clip() -> None:
    project = _make_project()
    with pytest.raises(KeyError, match="ghost"):
        find_clip(project, "ghost")


# ---------------------------------------------------------------------------
# Déplacement
# ---------------------------------------------------------------------------


def test_move_clip_updates_position_without_changing_source_or_duration() -> None:
    project = _make_project()
    clip = move_clip(project, "v1-a", 2.5)
    assert clip.timeline_start == pytest.approx(2.5)
    assert clip.source_in == 0.0
    assert clip.source_out == pytest.approx(4.0)
    assert clip.duration == pytest.approx(4.0)
    # L'état du projet reflète bien la modification.
    assert find_clip(project, "v1-a").timeline_start == pytest.approx(2.5)


def test_move_clip_rejects_negative_position() -> None:
    project = _make_project()
    with pytest.raises(ValueError, match="0.0"):
        move_clip(project, "v1-a", -1.0)


def test_move_clip_raises_for_unknown_clip() -> None:
    project = _make_project()
    with pytest.raises(KeyError):
        move_clip(project, "ghost", 1.0)


def test_move_clip_targets_correct_track_in_multi_track_project() -> None:
    project = _make_project()
    move_clip(project, "v2-a", 3.0)

    v1 = find_track(project, "V1")
    v2 = find_track(project, "V2")

    # Aucun clip de V1 n'a été affecté.
    assert all(c.timeline_start != 3.0 for c in v1.clips)
    # Le clip déplacé est bien dans V2.
    moved = find_clip(project, "v2-a")
    assert moved.timeline_start == pytest.approx(3.0)
    assert moved in v2.clips


def test_move_clip_does_not_affect_other_clips() -> None:
    project = _make_project()
    initial = {
        clip.id: clip.timeline_start
        for track in project.tracks
        for clip in track.clips
    }
    move_clip(project, "v1-a", 10.0)

    for track in project.tracks:
        for clip in track.clips:
            if clip.id == "v1-a":
                assert clip.timeline_start == pytest.approx(10.0)
            else:
                assert clip.timeline_start == pytest.approx(initial[clip.id])


# ---------------------------------------------------------------------------
# Trim gauche
# ---------------------------------------------------------------------------


def test_trim_left_shifts_timeline_start_and_source_in() -> None:
    project = _make_project()
    # v2-a : timeline_start=1.0, source_in=5.0, source_out=9.0, duration=4.0
    clip = trim_clip_left(project, "v2-a", 2.5)
    # delta = 1.5 → source_in = 5.0 + 1.5 = 6.5 ; source_out reste à 9.0
    assert clip.timeline_start == pytest.approx(2.5)
    assert clip.source_in == pytest.approx(6.5)
    assert clip.source_out == pytest.approx(9.0)
    assert clip.duration == pytest.approx(2.5)


def test_trim_left_rejects_new_position_before_current() -> None:
    project = _make_project()
    with pytest.raises(ValueError, match="trim gauche"):
        trim_clip_left(project, "v2-a", 0.5)


def test_trim_left_rejects_new_position_at_or_past_timeline_end() -> None:
    project = _make_project()
    # v2-a : timeline_end = 1.0 + 4.0 = 5.0
    with pytest.raises(ValueError):
        trim_clip_left(project, "v2-a", 5.0)  # pile à la fin → durée 0
    with pytest.raises(ValueError):
        trim_clip_left(project, "v2-a", 6.0)  # après la fin → durée négative


def test_trim_left_rejects_negative_position() -> None:
    project = _make_project()
    with pytest.raises(ValueError, match="0.0"):
        trim_clip_left(project, "v2-a", -1.0)


def test_trim_left_preserves_other_clips_state() -> None:
    project = _make_project()
    initial_v1_a = find_clip(project, "v1-a")
    trim_clip_left(project, "v2-a", 2.5)
    after_v1_a = find_clip(project, "v1-a")
    assert after_v1_a.timeline_start == initial_v1_a.timeline_start
    assert after_v1_a.source_in == initial_v1_a.source_in
    assert after_v1_a.source_out == initial_v1_a.source_out


# ---------------------------------------------------------------------------
# Trim droit
# ---------------------------------------------------------------------------


def test_trim_right_shortens_source_out() -> None:
    project = _make_project()
    # v1-a : timeline_start=0.0, source_in=0.0, source_out=4.0
    clip = trim_clip_right(project, "v1-a", 2.0)
    # delta = -2.0 → source_out = 4.0 - 2.0 = 2.0
    assert clip.timeline_start == 0.0
    assert clip.source_in == 0.0
    assert clip.source_out == pytest.approx(2.0)
    assert clip.duration == pytest.approx(2.0)


def test_trim_right_can_extend_within_media_duration() -> None:
    project = _make_project()
    # v1-b (asset-b, duration=8.0) : timeline_start=4.0, source_in=0.0, source_out=4.0
    # timeline_end = 8.0 ; on étend à 12.0 → delta=4.0 → source_out=8.0 (OK, == asset.duration)
    clip = trim_clip_right(project, "v1-b", 12.0)
    assert clip.timeline_start == 4.0
    assert clip.source_in == 0.0
    assert clip.source_out == pytest.approx(8.0)
    assert clip.duration == pytest.approx(8.0)


def test_trim_right_rejects_new_position_at_or_before_timeline_start() -> None:
    project = _make_project()
    # v1-a : timeline_start=0.0
    with pytest.raises(ValueError):
        trim_clip_right(project, "v1-a", 0.0)
    with pytest.raises(ValueError):
        trim_clip_right(project, "v1-a", -1.0)


def test_trim_right_rejects_exceeding_media_duration() -> None:
    project = _make_project()
    # v2-a (asset-a, duration=10.0) : timeline_start=1.0, timeline_end=5.0
    # on étend à 7.0 → delta=2.0 → source_out=11.0 > 10.0 → rejet
    with pytest.raises(ValueError, match="durée"):
        trim_clip_right(project, "v2-a", 7.0)


def test_trim_right_uses_correct_asset_per_clip() -> None:
    """trim_clip_right borne la durée selon le média référencé par chaque clip."""
    project = _make_project()
    # v1-b est lié à asset-b (8.0) : extension jusqu'à la limite → OK.
    extended = trim_clip_right(project, "v1-b", 12.0)
    assert extended.source_out == pytest.approx(8.0)

    # v1-a est lié à asset-a (10.0) : extension au-delà → rejet.
    with pytest.raises(ValueError, match="durée"):
        trim_clip_right(project, "v1-a", 15.0)


def test_trim_right_raises_for_unknown_asset() -> None:
    project = _make_project()
    project.tracks[1].clips.append(
        Clip(
            id="orphan",
            asset_id="asset-missing",
            track_id="V2",
            timeline_start=20.0,
            source_in=0.0,
            source_out=1.0,
        )
    )
    with pytest.raises(KeyError):
        trim_clip_right(project, "orphan", 22.0)


# ---------------------------------------------------------------------------
# Coupe
# ---------------------------------------------------------------------------


def test_cut_clip_creates_two_valid_clips_with_correct_source_segments() -> None:
    project = _make_project()
    # v1-a : timeline_start=0.0, source_in=0.0, source_out=4.0
    left, right = cut_clip(project, "v1-a", 2.0)

    assert left.id == "v1-a"
    assert left.timeline_start == 0.0
    assert left.source_in == 0.0
    assert left.source_out == pytest.approx(2.0)
    assert left.duration == pytest.approx(2.0)

    assert right.id == "v1-a-split-2"
    assert right.timeline_start == pytest.approx(2.0)
    assert right.source_in == pytest.approx(2.0)
    assert right.source_out == pytest.approx(4.0)
    assert right.duration == pytest.approx(2.0)

    # La durée totale est strictement conservée.
    assert left.duration + right.duration == pytest.approx(4.0)


def test_cut_clip_places_both_clips_on_same_track() -> None:
    project = _make_project()
    left, right = cut_clip(project, "v1-a", 2.0)
    v1 = find_track(project, "V1")
    assert left in v1.clips
    assert right in v1.clips
    # L'ancien clip simple a bien été remplacé par les deux nouveaux.
    assert len(v1.clips) == 3  # v1-a (gauche), v1-a-split-2 (droite), v1-b


def test_cut_clip_preserves_enabled_flag() -> None:
    project = _make_project()
    # Désactiver v1-a puis couper.
    find_clip(project, "v1-a").enabled = False
    left, right = cut_clip(project, "v1-a", 2.0)
    assert left.enabled is False
    assert right.enabled is False


def test_cut_clip_rejects_cut_at_start_or_end() -> None:
    project = _make_project()
    # v1-a : timeline_start=0.0, timeline_end=4.0
    with pytest.raises(ValueError):
        cut_clip(project, "v1-a", 0.0)  # au début exact
    with pytest.raises(ValueError):
        cut_clip(project, "v1-a", 4.0)  # à la fin exacte


def test_cut_clip_rejects_when_generated_id_already_exists() -> None:
    project = _make_project()
    project.tracks[1].clips.append(
        Clip(
            id="v1-a-split-2",
            asset_id="asset-a",
            track_id="V2",
            timeline_start=20.0,
            source_in=0.0,
            source_out=1.0,
        )
    )
    with pytest.raises(ValueError, match="split-2"):
        cut_clip(project, "v1-a", 2.0)


def test_cut_clip_raises_for_unknown_clip() -> None:
    project = _make_project()
    with pytest.raises(KeyError):
        cut_clip(project, "ghost", 1.0)


# ---------------------------------------------------------------------------
# Suppression
# ---------------------------------------------------------------------------


def test_delete_clip_removes_from_track_and_returns_it() -> None:
    project = _make_project()
    removed = delete_clip(project, "v1-b")
    assert removed.id == "v1-b"

    v1 = find_track(project, "V1")
    assert all(c.id != "v1-b" for c in v1.clips)
    assert len(v1.clips) == 1  # il ne reste que v1-a

    with pytest.raises(KeyError):
        find_clip(project, "v1-b")


def test_delete_clip_only_affects_target_track() -> None:
    project = _make_project()
    delete_clip(project, "v2-a")

    v1 = find_track(project, "V1")
    v2 = find_track(project, "V2")
    # V1 inchangée.
    assert {clip.id for clip in v1.clips} == {"v1-a", "v1-b"}
    # V2 vidée.
    assert v2.clips == []


def test_delete_clip_raises_for_unknown_id() -> None:
    project = _make_project()
    with pytest.raises(KeyError):
        delete_clip(project, "ghost")
