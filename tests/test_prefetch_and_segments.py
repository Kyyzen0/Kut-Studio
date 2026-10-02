"""Préchargement intelligent, grille de segments et empreintes par segment."""

from __future__ import annotations

import pytest

from core.prefetch import (
    PRIORITY_CURRENT,
    PRIORITY_NEAR,
    PrefetchPlanner,
)
from core.preview_cache import SEGMENT_SECONDS, DiskPreviewCache
from core.preview_engine import PreviewEngine, PreviewJob
from core.preview_segments import (
    apply_path_resolver,
    build_segment_job,
    segment_params_hash,
    segment_plan,
)
from core.project_model import Clip, MediaAsset, Project, Track
from core.render_plan import build_render_plan
from core.task_queue import TaskQueue
from tools.perf.synthetic import build_project

# --- helpers ----------------------------------------------------------------------------


def _project(clips: list[tuple[str, float, float]], paths: dict[str, str] | None = None) -> Project:
    """Une piste vidéo : ``(id, début, durée)`` ; un média par clip."""
    assets, items = [], []
    for clip_id, start, length in clips:
        asset_id = f"a-{clip_id}"
        path = (paths or {}).get(clip_id, f"/media/{clip_id}.mp4")
        assets.append(MediaAsset(id=asset_id, path=path, name=clip_id, duration=1000.0,
                                 width=1920, height=1080, fps=25.0, media_type="video"))
        items.append(Clip(id=clip_id, asset_id=asset_id, track_id="V1", timeline_start=start,
                          source_in=0.0, source_out=length))
    return Project(name="p", width=1280, height=720, fps=25.0, media_assets=assets,
                   tracks=[Track(id="V1", name="V1", type="video", clips=items)])


# --- PrefetchPlanner ------------------------------------------------------------------------


def test_still_playhead_requests_current_then_ahead_and_behind():
    planner = PrefetchPlanner()
    plan = planner.plan(5.0, duration=60.0, velocity=0.0)
    assert [r.index for r in plan] == [2, 3, 1, 4]
    assert plan[0].priority == PRIORITY_CURRENT and plan[0].role == "current"
    assert [r.priority for r in plan] == [0, PRIORITY_NEAR, 2 * PRIORITY_NEAR, 3 * PRIORITY_NEAR]


def test_moving_forward_only_prefetches_ahead():
    planner = PrefetchPlanner()
    plan = planner.plan(5.0, duration=60.0, velocity=2.0)
    assert [r.index for r in plan] == [2, 3, 4, 5]


def test_moving_backward_only_prefetches_behind_in_the_direction_of_motion():
    planner = PrefetchPlanner()
    plan = planner.plan(9.0, duration=60.0, velocity=-2.0)
    assert [r.index for r in plan] == [4, 3, 2, 1]


def test_fast_scrubbing_only_requests_the_current_segment():
    planner = PrefetchPlanner()
    plan = planner.plan(30.0, duration=60.0, velocity=50.0)
    assert [r.index for r in plan] == [15]


def test_plan_stays_inside_the_timeline():
    planner = PrefetchPlanner()
    assert [r.index for r in planner.plan(0.2, duration=60.0, velocity=0.0)] == [0, 1, 2]
    last = planner.plan(59.9, duration=60.0, velocity=0.0)
    assert max(r.index for r in last) == 29
    assert planner.plan(500.0, duration=60.0) == []  # au-delà du montage


def test_velocity_is_smoothed_and_forgotten_after_a_pause():
    planner = PrefetchPlanner(smoothing=0.5, max_gap_seconds=1.0)
    planner.note_position(0.0, now=0.0)
    first = planner.note_position(1.0, now=0.1)       # 10 s/s instantané
    second = planner.note_position(1.0, now=0.2)      # arrêt : la vitesse retombe, sans à-coup
    assert 0 < second < first
    assert planner.note_position(1.5, now=10.0) == 0.0  # long silence = à l'arrêt


def test_keep_range_covers_the_planned_segments():
    planner = PrefetchPlanner()
    low, high = planner.keep_range(5.0, duration=60.0, velocity=2.0)
    assert (low, high) == (2, 5)
    assert planner.keep_range(30.0, duration=60.0, velocity=99.0) == (15, 15)


def test_grid_helpers_are_aligned_on_segment_seconds():
    planner = PrefetchPlanner(segment_seconds=2.0)
    assert planner.index_of(0.0) == 0 and planner.index_of(1.999) == 0 and planner.index_of(2.0) == 1
    assert planner.bounds_of(3) == (6.0, 8.0)
    assert planner.last_index(60.0) == 29 and planner.last_index(0.0) == 0
    with pytest.raises(ValueError):
        PrefetchPlanner(segment_seconds=0)


# --- Grille et empreintes par segment ----------------------------------------------------------------


def test_two_nearby_playheads_share_the_same_segment_key():
    project = _project([("a", 0.0, 20.0)])
    first = build_segment_job(project, 0, quality="standard")
    second = build_segment_job(project, 0, quality="standard")
    assert first.key == second.key and first.start == 0.0 and first.duration == SEGMENT_SECONDS
    # Avant, deux positions de tête différentes produisaient des segments non alignés :
    third = build_segment_job(project, 1, quality="standard")
    assert third.key != first.key and third.start == SEGMENT_SECONDS


def test_a_gap_in_the_timeline_has_no_segment_to_render():
    project = _project([("a", 0.0, 2.0), ("b", 10.0, 2.0)])
    assert build_segment_job(project, 2, quality="standard") is None  # 4–6 s : trou
    assert build_segment_job(project, 5, quality="standard") is not None  # 10–12 s


def test_editing_a_clip_only_invalidates_the_segments_that_show_it():
    project = _project([("a", 0.0, 4.0), ("b", 20.0, 4.0)])
    near = build_segment_job(project, 0, quality="standard")       # montre « a »
    far = build_segment_job(project, 10, quality="standard")       # montre « b »
    project.tracks[0].clips[0].source_out = 3.0                    # on retaille « a »
    near_after = build_segment_job(project, 0, quality="standard")
    far_after = build_segment_job(project, 10, quality="standard")
    assert near_after.key.params_hash != near.key.params_hash      # le segment touché change
    assert far_after.key == far.key                                # les autres gardent leur cache


def test_changing_a_media_path_only_changes_the_segments_that_use_it():
    project = _project([("a", 0.0, 4.0), ("b", 20.0, 4.0)])
    before_a = build_segment_job(project, 0, quality="standard").key
    before_b = build_segment_job(project, 10, quality="standard").key
    project.media_assets[1].path = "/ailleurs/b.mp4"
    assert build_segment_job(project, 0, quality="standard").key == before_a
    assert build_segment_job(project, 10, quality="standard").key != before_b


def test_the_render_quality_is_part_of_the_segment_key():
    project = _project([("a", 0.0, 4.0)])
    assert (build_segment_job(project, 0, quality="draft").key
            != build_segment_job(project, 0, quality="high").key)


def test_a_segment_plan_only_contains_the_layers_that_overlap_it():
    project = build_project(2000, "short_8t")
    plan = segment_plan(project, 100.0, 102.0)
    full = build_render_plan(project)
    assert 0 < len(plan.video_layers) < 20 < len(full.video_layers)
    assert plan.duration == full.duration  # la durée du montage ne change pas
    for layer in plan.video_layers:
        assert layer.timeline_end > 100.0 and layer.timeline_start < 102.0
    # Mêmes couches que le plan complet restreint à la fenêtre :
    expected = [l.clip_id for l in full.video_layers if l.timeline_end > 100.0 and l.timeline_start < 102.0]
    assert [l.clip_id for l in plan.video_layers] == expected


def test_windowed_plan_never_changes_the_export_plan():
    project = build_project(300, "short_8t")
    assert build_render_plan(project) == build_render_plan(project, window=None)


def test_windowed_plan_only_reports_missing_media_inside_the_window():
    project = _project([("a", 0.0, 2.0), ("b", 50.0, 2.0)])
    kept = project.media_assets[0]
    project.media_assets = [kept]  # « b » n'a plus son média
    # Tolérant à l'écran : le plan se construit sans le clip orphelin et le signale (l'export, lui, le refuse).
    full = build_render_plan(project)
    assert len(full.video_layers) == 1 and len(full.missing_media) == 1 and kept.id not in full.missing_media
    windowed = build_render_plan(project, window=(0.0, 2.0))
    assert len(windowed.video_layers) == 1 and windowed.missing_media == ()


def test_path_resolver_replaces_video_paths_and_changes_the_fingerprint():
    project = _project([("a", 0.0, 4.0)])
    seen = []

    def resolver(path, need_audio=False):
        seen.append((path, need_audio))
        return path.replace(".mp4", ".proxy.mp4") if not need_audio else path

    plan = segment_plan(project, 0.0, 2.0, resolver=resolver)
    assert plan.video_layers[0].source_path == "/media/a.proxy.mp4"
    assert ("/media/a.mp4", False) in seen
    assert (segment_params_hash(plan, project, "standard")
            != segment_params_hash(segment_plan(project, 0.0, 2.0), project, "standard"))
    assert apply_path_resolver(plan, None) is plan


# --- Moteur d'aperçu : priorité, abandon des demandes lointaines -----------------------------------------


class _Cache(DiskPreviewCache):
    pass


@pytest.fixture
def engine(tmp_path):
    rendered: list[float] = []

    def render(job, token):
        rendered.append(job.start)
        out = tmp_path / f"out-{job.start}.mp4"
        out.write_bytes(b"x" * 10)
        return str(out)

    tasks = TaskQueue()
    cache = DiskPreviewCache(directory=tmp_path / "cache")
    eng = PreviewEngine(task_queue=tasks, cache=cache, render_fn=render)
    eng.rendered = rendered
    return eng


def _drain(engine):
    """Rend tout ce qui est en file (le moteur n'exécute qu'un rendu à la fois)."""
    while engine.pump(1):
        pass


def _jobs(project, indices):
    return [build_segment_job(project, i, quality="standard") for i in indices]


def test_current_segment_is_rendered_before_background_work(engine):
    project = _project([("a", 0.0, 100.0)])
    far, current = _jobs(project, [20, 2])
    engine.request(far)                                   # priorité de fond
    engine.request(current, PRIORITY_CURRENT)             # urgent
    _drain(engine)
    assert engine.rendered == [current.start, far.start]


def test_a_queued_request_is_promoted_when_asked_again_with_a_higher_priority(engine):
    project = _project([("a", 0.0, 100.0)])
    first, second = _jobs(project, [3, 4])
    engine.request(first, 50)
    engine.request(second, 50)
    engine.request(second, PRIORITY_CURRENT)              # la tête s'est rapprochée de « second »
    _drain(engine)
    assert engine.rendered[0] == second.start


def test_segments_far_from_the_playhead_are_abandoned_without_touching_started_work(engine):
    project = _project([("a", 0.0, 200.0)])
    jobs = _jobs(project, [1, 2, 3, 40, 41])
    for job in jobs:
        engine.request(job)
    assert engine.pending_starts() == [job.start for job in jobs]
    dropped = engine.cancel_outside(2.0, 8.0)             # fenêtre utile : segments 1 à 3
    assert dropped == 2
    assert engine.pending_starts() == [jobs[0].start, jobs[1].start, jobs[2].start]
    _drain(engine)
    assert sorted(engine.rendered) == [jobs[0].start, jobs[1].start, jobs[2].start]
    assert engine.cancel_outside(0.0, 0.0) == 0           # plus rien en file


def test_a_cancelled_request_never_writes_into_the_cache(engine):
    project = _project([("a", 0.0, 200.0)])
    job = _jobs(project, [50])[0]
    engine.request(job)
    engine.cancel_outside(0.0, 1.0)
    _drain(engine)
    assert engine.rendered == [] and engine.cache.lookup(job.key) is None


def test_requesting_the_same_segment_twice_never_duplicates_the_render(engine):
    project = _project([("a", 0.0, 100.0)])
    job = _jobs(project, [1])[0]
    engine.request(job)
    engine.request(job)
    _drain(engine)
    assert engine.rendered == [job.start]
    assert engine.request(job)["status"] == "cached"


def test_task_queue_reprioritize_and_contains():
    queue = TaskQueue()
    ran: list[str] = []
    queue.submit("a", lambda _t: ran.append("a"), priority=50)
    queue.submit("b", lambda _t: ran.append("b"), priority=50)
    assert queue.contains("a") and not queue.contains("zzz")
    assert queue.reprioritize("b", 0) and not queue.reprioritize("zzz", 0)
    queue.pump(10)
    assert ran == ["b", "a"] and not queue.contains("a")


def test_preview_job_default_priority_is_background():
    assert PreviewJob(key=None, plan=None).priority == 100
