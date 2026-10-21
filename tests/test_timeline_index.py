"""Index de lecture : même résultat que l'évaluateur, pour moins de travail."""

from __future__ import annotations

import random
import time

from core.project_model import Clip, MediaAsset, Project, Track
from core.timeline_evaluator import evaluate_timeline, timeline_duration
from core.timeline_index import build_timeline_index


def _project(tracks: list[Track], assets: list[MediaAsset] | None = None) -> Project:
    return Project(
        name="Index",
        media_assets=assets
        or [
            MediaAsset(
                id="media",
                path="/tmp/demo.mp4",
                name="Démo",
                duration=10_000.0,
                width=1920,
                height=1080,
                fps=30.0,
                media_type="video",
                has_audio=True,
            )
        ],
        tracks=tracks,
    )


def _clip(clip_id: str, start: float, duration: float, **overrides) -> Clip:
    fields = {
        "id": clip_id,
        "asset_id": "media",
        "track_id": "V1",
        "timeline_start": start,
        "source_in": 0.0,
        "source_out": duration,
        "enabled": True,
        "label": clip_id,
    }
    fields.update(overrides)
    return Clip(**fields)


def test_index_matches_evaluator_on_overlaps_gaps_and_flags():
    hidden = Track(
        id="V0",
        name="V0",
        type="video",
        visible=False,
        clips=[_clip("hidden", 0.0, 5.0, track_id="V0")],
    )
    video = Track(
        id="V1",
        name="V1",
        type="video",
        clips=[
            _clip("a", 0.0, 2.0),
            _clip("overlap", 1.0, 2.0),
            _clip("disabled", 1.5, 1.0, enabled=False),
            _clip("later", 10.0, 1.0),
        ],
    )
    audio = Track(
        id="A1",
        name="A1",
        type="audio",
        muted=True,
        clips=[_clip("sound", 0.5, 3.0, track_id="A1")],
    )
    project = _project([hidden, video, audio])
    index = build_timeline_index(project)

    assert index.duration == timeline_duration(project)
    for instant in (0.0, 1.0, 1.5, 2.0, 3.4, 9.9, 10.0, 10.5, 11.0):
        assert index.active_at(project, instant) == evaluate_timeline(project, instant)


def test_index_sees_a_path_changed_without_rebuild():
    project = _project([Track(id="V1", name="V1", type="video", clips=[_clip("a", 0.0, 2.0)])])
    index = build_timeline_index(project)
    project.media_assets[0].path = "/tmp/replaced.mp4"
    active = index.active_at(project, 0.5)
    assert active[0].source_path == "/tmp/replaced.mp4"


def test_index_is_faster_than_a_full_scan_on_a_long_timeline():
    clips = [_clip(f"c{i}", i * 1.5, 1.0) for i in range(3000)]
    project = _project([Track(id="V1", name="V1", type="video", clips=clips)])
    index = build_timeline_index(project)
    instants = [random.Random(4).uniform(0.0, 4500.0) for _ in range(40)]

    started = time.perf_counter()
    for instant in instants:
        evaluate_timeline(project, instant)
    full_scan = time.perf_counter() - started

    started = time.perf_counter()
    for instant in instants:
        index.active_at(project, instant)
    indexed = time.perf_counter() - started

    assert indexed < full_scan
