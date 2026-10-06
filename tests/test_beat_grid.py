"""Grille rythmique : modèle, sérialisation, couper sur les temps, répartir sur la grille."""

from __future__ import annotations

import json

import pytest

from core.beat_edit import cut_on_beats, distribute_on_grid
from core.beat_grid import BeatGrid, beat_grid_from_dict, tap_tempo
from core.project_io import load_project, save_project
from core.project_model import Clip, MediaAsset, Project, Track


def _project(*clips: tuple[str, float, float]) -> Project:
    asset = MediaAsset(id="a", path="/nonexistent/m.mp4", name="m", duration=60.0, width=1920, height=1080, fps=30.0,
                       media_type="video")
    items = [Clip(id=cid, asset_id="a", track_id="V1", timeline_start=start, source_in=0.0, source_out=length)
             for cid, start, length in clips]
    return Project(name="g", width=1080, height=1920, fps=30.0, media_assets=[asset],
                   tracks=[Track(id="V1", name="V1", type="video", clips=items)])


def test_beats_bars_and_rounding():
    grid = BeatGrid(120.0, offset=0.25)
    assert grid.beat == 0.5 and grid.bar == 2.0
    assert grid.beat_times(0.0, 2.0) == [0.25, 0.75, 1.25, 1.75]
    assert grid.beat_times(0.0, 4.5, every=4) == [0.25, 2.25, 4.25]
    assert grid.is_downbeat(round(grid.index_at(2.25))) and not grid.is_downbeat(round(grid.index_at(2.75)))
    assert grid.nearest_beat(1.1) == 1.25 and grid.nearest_beat(1.1, subdivision=4) == 1.125


@pytest.mark.parametrize("raw", [{"bpm": 5}, {"bpm": "x"}, {"offset": 1.0}, None, {"bpm": 120, "beats_per_bar": 40}])
def test_a_damaged_grid_is_simply_absent(raw):
    assert beat_grid_from_dict(raw) is None


def test_the_grid_round_trips_and_is_absent_from_old_files(tmp_path):
    project = _project(("c", 0.0, 4.0))
    path = tmp_path / "g.kut"
    save_project(project, path)
    assert "beat_grid" not in json.loads(path.read_text(encoding="utf-8"))["project"]["sequences"][0]
    project.active_sequence.beat_grid = BeatGrid(128.0, 0.1, 3)
    save_project(project, path)
    assert load_project(path).active_sequence.beat_grid == BeatGrid(128.0, 0.1, 3)


def test_tap_tempo_averages_and_restarts_after_a_pause():
    assert tap_tempo([0.0, 0.5]) is None
    assert tap_tempo([0.0, 0.5, 1.0, 1.5]) == 120.0
    assert tap_tempo([0.0, 0.5, 1.0, 9.0, 9.4, 9.8, 10.2]) == 150.0


def test_cut_on_beats_cuts_inside_the_clip_only():
    project = _project(("c", 0.1, 2.0))
    pieces = cut_on_beats(project, "c", BeatGrid(120.0))
    starts = [clip.timeline_start for clip in project.tracks[0].clips]
    assert len(pieces) == 5 and sorted(starts) == pytest.approx([0.1, 0.5, 1.0, 1.5, 2.0])
    assert sum(clip.duration for clip in project.tracks[0].clips) == pytest.approx(2.0)
    with pytest.raises(ValueError):
        cut_on_beats(_project(("c", 0.0, 0.4)), "c", BeatGrid(120.0))


def test_distribute_places_clips_end_to_end_on_the_grid():
    project = _project(("a", 0.3, 3.0), ("b", 4.0, 0.7), ("c", 6.1, 5.0))
    distribute_on_grid(project, ["c", "a", "b"], BeatGrid(120.0), beats_each=2)
    clips = {clip.id: clip for clip in project.tracks[0].clips}
    assert clips["a"].timeline_start == pytest.approx(0.5) and clips["a"].duration == pytest.approx(1.0)
    assert clips["b"].timeline_start == pytest.approx(1.5) and clips["b"].duration == pytest.approx(0.7)
    assert clips["c"].timeline_start == pytest.approx(2.5) and clips["c"].duration == pytest.approx(1.0)


def test_distribute_refuses_to_land_on_an_unselected_clip():
    project = _project(("a", 0.0, 3.0), ("b", 3.0, 3.0), ("keep", 1.6, 0.2))
    with pytest.raises(ValueError):
        distribute_on_grid(project, ["a", "b"], BeatGrid(120.0), beats_each=4)
