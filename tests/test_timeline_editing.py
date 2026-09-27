"""Navigation, sélection, marqueurs et ripple de la timeline."""

from core.project_factory import create_default_project
from core.project_model import Clip, MediaAsset, Project, Track
from core.shortcuts import resolve_shortcut
from core.timeline_editing import (
    add_marker,
    clip_ids_in_range,
    delete_clips,
    move_clips,
    neighbor_marker,
    relocate_clip,
    shift_track_after,
    snap_edit_position,
    ClipPlacement,
)
from core.timeline_navigation import (
    fit_zoom,
    format_timecode,
    ruler_ticks,
    scroll_for_anchor,
    step_frames,
)
from core.timeline_view_model import build_clip_views
from core.media_previews import peaks_from_samples, thumbnail_slots, waveform_bins
from core.project_io import load_project, save_project


def test_zoom_keeps_the_instant_under_the_cursor():
    scroll = scroll_for_anchor(
        old_zoom=1.0,
        new_zoom=2.0,
        anchor_in_viewport=400,
        scroll_x=100,
        origin=200,
        pixels_per_second=100,
    )
    # Temps sous le curseur : (100 + 400 - 200) / 100 = 3 s.
    # À zoom 2 : scroll + 400 - 200 = 3 * 200 => scroll = 400.
    assert scroll == 400


def test_frame_step_and_timecode_follow_the_project_rate():
    assert step_frames(1.0, 1, 30) == 1.0 + 1 / 30
    assert step_frames(0.0, -1, 30) == 0.0
    assert format_timecode(1.0, 30) == "00:00:01:00"
    assert fit_zoom(10, 1000, 200, 100) < 1.0


def test_ruler_does_not_flood_labels_when_zoomed_out():
    ticks = ruler_ticks(0, 3600, pixels_per_second=2, fps=30)
    assert len(ticks) < 80
    assert ticks[0].seconds >= 0


def test_shift_selection_stays_on_one_track():
    project = create_default_project()
    views = build_clip_views(project)
    ids = clip_ids_in_range(views, "intro", "plan_a")
    assert ids == ["intro", "plan_a"]


def test_move_between_compatible_tracks_and_snap_to_marker():
    project = create_default_project()
    add_marker(project, 6.5, name="Plan")
    snapped = snap_edit_position(
        project,
        6.42,
        threshold_seconds=0.2,
        excluded_clip_ids={"plan_a"},
    )
    assert snapped == 6.5
    relocate_clip(project, "b_roll", "V1", 20.0)
    clip = next(c for t in project.tracks if t.id == "V1" for c in t.clips if c.id == "b_roll")
    assert clip.timeline_start == 20.0
    assert clip.track_id == "V1"


def test_group_move_rejects_a_negative_position_before_writing():
    project = create_default_project()
    before = next(c for t in project.tracks for c in t.clips if c.id == "intro").timeline_start
    try:
        move_clips(
            project,
            [
                ClipPlacement("intro", 2.0, "V1"),
                ClipPlacement("plan_a", -1.0, "V1"),
            ],
        )
    except ValueError:
        pass
    else:
        raise AssertionError("une position négative doit être refusée")
    after = next(c for t in project.tracks for c in t.clips if c.id == "intro").timeline_start
    assert after == before


def test_ripple_shift_closes_the_gap_on_the_same_track_only():
    project = create_default_project()
    shift_track_after(project, "V1", boundary=4.0, delta=-1.0, exclude_ids={"intro"})
    plan = next(c for t in project.tracks for c in t.clips if c.id == "plan_a")
    broll = next(c for t in project.tracks for c in t.clips if c.id == "b_roll")
    assert plan.timeline_start == 5.5
    assert broll.timeline_start == 2.0


def test_markers_roundtrip_and_navigation(tmp_path):
    project = create_default_project()
    first = add_marker(project, 4.0, name="Intro")
    add_marker(project, 9.0, name="Suite", category="chapter")
    assert neighbor_marker(project, 4.0, 1).name == "Suite"
    path = tmp_path / "marqueurs.kut"
    save_project(project, str(path))
    loaded = load_project(str(path))
    assert [marker.name for marker in loaded.markers] == ["Intro", "Suite"]
    assert loaded.markers[0].id == first.id
    assert loaded.tracks[0].height_mode == "normal"
    assert loaded.tracks[0].solo is False


def test_delete_several_clips_and_preview_budgets():
    project = create_default_project()
    delete_clips(project, ["intro", "plan_a"])
    assert all(clip.id not in {"intro", "plan_a"} for track in project.tracks for clip in track.clips)
    assert thumbnail_slots(100, enabled=False) == 0
    assert thumbnail_slots(400, enabled=True) >= 1
    assert waveform_bins(500, "compact") <= 64
    assert peaks_from_samples([0.0, 0.5, -1.0, 0.2], 2)[1] == 1.0


def test_audio_cannot_move_onto_a_video_track():
    asset = MediaAsset(
        id="audio",
        path="/tmp/a.wav",
        name="Voix",
        duration=4.0,
        width=0,
        height=0,
        fps=0.0,
        media_type="audio",
        has_audio=True,
    )
    clip = Clip(
        id="voice",
        asset_id="audio",
        track_id="A1",
        timeline_start=0.0,
        source_in=0.0,
        source_out=2.0,
    )
    project = Project(
        name="Mix",
        media_assets=[asset],
        tracks=[
            Track(id="V1", name="V1", type="video"),
            Track(id="A1", name="A1", type="audio", clips=[clip]),
        ],
    )
    try:
        relocate_clip(project, "voice", "V1", 1.0)
    except ValueError:
        return
    raise AssertionError("un clip audio ne doit pas aller sur une piste vidéo")


def test_shortcuts_are_resolved_in_one_place():
    assert resolve_shortcut("left", set()) == "frame_back"
    assert resolve_shortcut("left", {"shift"}) == "second_back"
    assert resolve_shortcut("b", set()) == "tool_blade"
    assert resolve_shortcut("k", {"ctrl"}) == "cut_at_playhead"
    assert resolve_shortcut("k", set()) == "play_pause"
    assert resolve_shortcut("s", {"ctrl"}) is None
