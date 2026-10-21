"""Index spatiaux de la timeline : mêmes résultats que les parcours complets."""

from __future__ import annotations

import random

import pytest

from core.project_model import Clip, MediaAsset, Marker, Project, Track
from core.timeline_editing import snap_edit_position
from core.timeline_operations import snap_timeline_position
from core.timeline_spatial import SnapIndex, SpanIndex
from core.visual_effects import TransformKeyframe


def _random_project(seed: int, clips: int = 200, tracks: int = 4) -> Project:
    rng = random.Random(seed)
    assets = [
        MediaAsset(id="m", path="/x.mp4", name="m", duration=10_000.0,
                   width=1920, height=1080, fps=25.0, media_type="video")
    ]
    built = []
    for index in range(tracks):
        items = []
        cursor = 0.0
        for number in range(clips // tracks):
            cursor += rng.choice([0.0, 0.25, 0.5, 1.0, 3.0])
            length = rng.choice([0.5, 1.0, 2.0, 5.0, 40.0])
            clip = Clip(
                id=f"t{index}-c{number}", asset_id="m", track_id=f"V{index}",
                timeline_start=cursor, source_in=0.0, source_out=length,
            )
            if rng.random() < 0.2:
                clip.transform_keyframes = [
                    TransformKeyframe(property_name="scale", time_seconds=rng.uniform(0, length), value=1.2)
                ]
            items.append(clip)
            cursor += length * rng.choice([0.0, 0.5, 1.0])  # chevauchements possibles
        built.append(Track(id=f"V{index}", name=f"V{index}", type="video", clips=items))
    return Project(name="rand", media_assets=assets, tracks=built,
                   markers=[Marker(id="mk", time_seconds=rng.uniform(0, 50), name="m", category="c")])


def _brute_overlaps(spans, t0, t1, lanes=None):
    return sorted(
        key for key, lane, start, end in spans
        if end >= t0 and start <= t1 and (lanes is None or lanes[0] <= lane <= lanes[1])
    )


# --- SpanIndex ------------------------------------------------------------------------


@pytest.mark.parametrize("seed", range(8))
def test_span_index_matches_a_full_scan(seed):
    rng = random.Random(seed)
    spans = []
    for number in range(400):
        start = rng.uniform(0, 500)
        spans.append((f"s{number}", rng.randrange(6), start, start + rng.choice([0.1, 1, 5, 80])))
    index = SpanIndex(spans)
    for _ in range(60):
        t0 = rng.uniform(-10, 520)
        t1 = t0 + rng.choice([0.0, 0.5, 20, 300])
        lanes = rng.choice([None, (0, 5), (2, 3), (4, 1)])
        assert sorted(index.query(t0, t1, lanes)) == _brute_overlaps(spans, t0, t1, lanes)


def test_span_index_bounds_are_inclusive_like_the_original_culling():
    index = SpanIndex([("a", 0, 1.0, 2.0)])
    assert index.query(2.0, 3.0) == ["a"]      # fin == début de la fenêtre
    assert index.query(0.0, 1.0) == ["a"]      # début == fin de la fenêtre
    assert index.query(2.0001, 3.0) == []
    assert index.query(0.0, 0.9999) == []


def test_span_index_handles_empty_and_single_lane_inputs():
    assert SpanIndex([]).query(0, 10) == []
    assert SpanIndex([("a", 3, 0, 1)]).query(0, 1, (0, 2)) == []


# --- SnapIndex -------------------------------------------------------------------------------


@pytest.mark.parametrize("seed", range(10))
def test_snap_index_matches_snap_timeline_position(seed):
    project = _random_project(seed)
    index = SnapIndex(project, with_keyframes=False)
    rng = random.Random(seed + 100)
    clip_ids = [clip.id for track in project.tracks for clip in track.clips]
    for _ in range(300):
        proposed = rng.uniform(-1, 120)
        threshold = rng.choice([0.0, 0.05, 0.2, 1.0])
        excluded = rng.choice([None, rng.choice(clip_ids)])
        playhead = rng.choice([None, rng.uniform(0, 100)])
        expected = snap_timeline_position(
            project, proposed, threshold, excluded_clip_id=excluded, playhead_seconds=playhead
        )
        got = index.nearest(
            proposed, threshold,
            excluded_ids=(excluded,) if excluded else (), playhead=playhead,
        )
        assert got == expected


@pytest.mark.parametrize("seed", range(10))
def test_snap_index_matches_snap_edit_position_with_keyframes_markers_and_exclusions(seed):
    project = _random_project(seed)
    index = SnapIndex(project, with_keyframes=True)
    rng = random.Random(seed + 200)
    clip_ids = [clip.id for track in project.tracks for clip in track.clips]
    markers = [marker.time_seconds for marker in project.markers]
    for _ in range(300):
        proposed = rng.uniform(-1, 120)
        threshold = rng.choice([0.05, 0.2, 1.0])
        excluded = set(rng.sample(clip_ids, rng.choice([1, 2, 5])))
        playhead = rng.choice([None, rng.uniform(0, 100)])
        expected = snap_edit_position(
            project, proposed, threshold, excluded_clip_ids=excluded, playhead_seconds=playhead
        )
        got = index.nearest(
            proposed, threshold, excluded_ids=excluded, extra_points=markers, playhead=playhead
        )
        assert got == expected


def test_snap_index_prefers_the_last_candidate_on_equal_distance_like_the_original():
    project = Project(
        name="tie",
        media_assets=[MediaAsset(id="m", path="/x", name="m", duration=100, width=1, height=1,
                                 fps=25.0, media_type="video")],
        tracks=[Track(id="V1", name="V1", type="video", clips=[
            Clip(id="a", asset_id="m", track_id="V1", timeline_start=1.0, source_in=0, source_out=1.0),
            Clip(id="b", asset_id="m", track_id="V1", timeline_start=3.0, source_in=0, source_out=1.0),
        ])],
    )
    # 2.0 est à égale distance de la fin de « a » (2.0) ... et de rien d'autre : cas simple
    # puis 2.5 entre la fin de « a » (2.0) et le début de « b » (3.0), à 0.5 des deux.
    expected = snap_timeline_position(project, 2.5, 0.5)
    assert SnapIndex(project, with_keyframes=False).nearest(2.5, 0.5) == expected == 3.0


def test_snap_index_ignores_negative_proposals_and_non_positive_thresholds():
    project = _random_project(1)
    index = SnapIndex(project, with_keyframes=False)
    assert index.nearest(-5.0, 0.0) == 0.0
    assert index.nearest(3.3, 0.0) == 3.3
    assert index.nearest(-5.0, 0.5) == 0.0


# --- TimelinePanel -----------------------------------------------------------------------------


def _panel(qtbot, project, width=900):
    from ui.timeline_panel import TimelinePanel

    panel = TimelinePanel(project)
    qtbot.addWidget(panel)
    panel.resize(width, 460)
    panel.show()
    qtbot.waitExposed(panel)
    panel.set_timeline_duration(max(c.timeline_start + c.duration for t in project.tracks for c in t.clips))
    panel.set_project(project)
    return panel


def test_mounted_clips_equal_a_brute_force_window(qtbot):
    project = _random_project(3, clips=800, tracks=8)
    panel = _panel(qtbot, project)
    bar = panel.scroll.horizontalScrollBar()
    for fraction in (0.0, 0.3, 0.77, 1.0):
        bar.setValue(int(bar.maximum() * fraction))
        qtbot.wait(5)
        time_range, row_range = panel._visibility_window()
        expected = {
            view.id for view in panel.clip_views
            if panel._clip_in_window(view, time_range, row_range)
        }
        dragging = {i for i, w in panel.clip_widgets.items() if w.drag_mode is not None}
        assert set(panel.clip_widgets) == expected | dragging
        assert 0 < panel.mounted_clip_count < len(panel.clip_views)


def test_clips_overlapping_keeps_the_original_stacking_order(qtbot):
    panel = _panel(qtbot, _random_project(4, clips=400, tracks=4))
    found = panel.clips_overlapping(5.0, 60.0)
    positions = [panel.clip_views.index(view) for view in found]
    assert positions == sorted(positions) and found
    brute = [v for v in panel.clip_views if v.end >= 5.0 and v.start <= 60.0]
    assert [v.id for v in found] == [v.id for v in brute]


def test_marquee_selection_equals_the_full_scan(qtbot):
    from PySide6.QtCore import QPoint, QRect

    panel = _panel(qtbot, _random_project(5, clips=600, tracks=6))
    rect = QRect(QPoint(300, 40), QPoint(1100, 300)).normalized()
    panel._marquee_origin = rect.topLeft()
    panel.begin_marquee(rect.topLeft())
    panel._marquee_origin = rect.topLeft()
    panel.finish_marquee(rect.bottomRight())
    expected = []
    for view in panel.clip_views:
        x, y, w, h = panel.clip_rect(view, view.start, view.end)
        if rect.intersects(QRect(x, y, w, h)):
            expected.append(view.id)
    assert set(panel.selected_clip_ids) == set(expected) and expected
    assert panel.selected_clip_id == expected[-1]


def test_view_lookups_follow_the_replaced_clip_views(qtbot):
    project = _random_project(6, clips=100, tracks=2)
    panel = _panel(qtbot, project)
    first = panel.clip_views[0]
    assert panel.find_view_by_id(first.id) is first
    assert panel.clip_model(first.id) is project.tracks[0].clips[0]
    other = _random_project(7, clips=40, tracks=2)
    panel.set_project(other)
    assert panel.find_view_by_id(first.id) is None or panel.find_view_by_id(first.id) is not first
    assert panel.find_view_by_id("t1-c3") is not None
    assert panel.clip_model("inconnu") is None


def test_snap_uses_the_index_and_matches_the_reference_function(qtbot):
    project = _random_project(8, clips=300, tracks=3)
    panel = _panel(qtbot, project)
    panel.playhead_seconds = 12.0
    rng = random.Random(8)
    for _ in range(50):
        proposed = rng.uniform(0, 100)
        threshold = panel.snap_threshold_pixels / (panel.pixels_per_second * panel.zoom)
        expected = snap_timeline_position(project, proposed, threshold, excluded_clip_id="t0-c1",
                                          playhead_seconds=12.0)
        assert panel.snap_position(proposed, excluded_clip_id="t0-c1")[0] == expected
    panel.snap_enabled = False
    assert panel.snap_position(3.3) == (3.3, None)


def test_a_huge_timeline_never_asks_qt_for_an_impossible_width(qtbot):
    from ui.timeline_panel_mixins.layout import _QT_MAX_WIDGET_SIZE

    project = _random_project(9, clips=40, tracks=1)
    panel = _panel(qtbot, project)
    panel.set_timeline_duration(10 ** 9)
    assert panel.timeline_grid.minimumWidth() <= _QT_MAX_WIDGET_SIZE


# --- Plan fenêtré via l'index de lecture ------------------------------------------------------------


@pytest.mark.parametrize("seed", range(6))
def test_windowed_plan_through_the_index_equals_the_scan(seed):
    from core.render_plan import build_render_plan
    from core.timeline_index import build_timeline_index

    project = _random_project(seed, clips=240, tracks=6)
    index = build_timeline_index(project)
    rng = random.Random(seed)
    for _ in range(40):
        start = rng.uniform(0, 150)
        window = (start, start + rng.choice([0.5, 2.0, 10.0]))
        scanned = build_render_plan(project, window=window)
        indexed = build_render_plan(project, window=window, window_index=index)
        assert [l.clip_id for l in indexed.video_layers] == [l.clip_id for l in scanned.video_layers]
        assert indexed == scanned


def test_a_stale_index_falls_back_to_scanning_the_track():
    from core.render_plan import build_render_plan
    from core.timeline_index import build_timeline_index

    project = _random_project(3, clips=80, tracks=2)
    index = build_timeline_index(project)
    project.tracks.append(Track(id="V9", name="V9", type="video", clips=[
        Clip(id="late", asset_id="m", track_id="V9", timeline_start=1.0, source_in=0, source_out=2.0)]))
    plan = build_render_plan(project, window=(0.0, 5.0), window_index=index)   # piste inconnue de l'index
    assert "late" in [layer.clip_id for layer in plan.video_layers]
    project.tracks[0] = Track(id="V0", name="V0", type="video", clips=project.tracks[0].clips[:3])
    assert build_timeline_index(project).clips_overlapping(0, 0, 5, project.tracks[0]) is not None
    assert index.clips_overlapping(0, 0, 5, project.tracks[0]) is None           # piste remplacée : repli
    assert index.clips_overlapping(99, 0, 5) is None


def test_clip_deepcopy_is_independent_but_shares_immutable_parts():
    import copy

    project = _random_project(2, clips=40, tracks=2)
    clip = next(c for t in project.tracks for c in t.clips if c.transform_keyframes)
    clone = copy.deepcopy(clip)
    assert clone == clip and clone is not clip
    assert clone.transform_keyframes is not clip.transform_keyframes
    assert clone.effects is not clip.effects and clone.audio_effects is not clip.audio_effects
    assert clone.transform is clip.transform and clone.text_style is clip.text_style   # immuables partagés
    clone.timeline_start = 999.0
    clone.transform_keyframes.append("x")
    clone.effects.append("y")
    assert clip.timeline_start != 999.0 and "x" not in clip.transform_keyframes and "y" not in clip.effects
    copied = copy.deepcopy(project)
    assert copied.tracks[0].clips[0] is not project.tracks[0].clips[0]
    assert [c.id for t in copied.tracks for c in t.clips] == [c.id for t in project.tracks for c in t.clips]
