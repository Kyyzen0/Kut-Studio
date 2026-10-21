"""« Analyser le flux optique » : un travail d'arrière-plan annulable dont le cache sert ensuite l'export, sans recalcul."""

from __future__ import annotations

import shutil
import time

import pytest
from test_retime_prepare_real import FPS, SCENE_FRAMES, SH, SW, _encode_scene, make_project

from core.flow_analysis import AnalysisState, FlowAnalysisJob
from core.flow_cache import FlowCache
from core.render_plan import build_render_plan
from core.retime_layers import layer_jobs, prepare_plan
from core.time_remapping import FlowQuality, TimeInterpolation, TimeRemapping

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg absent")

FLOW, BLENDING = TimeInterpolation.OPTICAL_FLOW, TimeInterpolation.BLENDING


@pytest.fixture(scope="module")
def scene_media(tmp_path_factory):
    return _encode_scene(tmp_path_factory.mktemp("analysis") / "scene.mp4", 1.0, SCENE_FRAMES)


def requests_for(path, remapping):
    project, _clip = make_project(path, frames=SCENE_FRAMES, width=SW, height=SH, remapping=remapping)
    plan = build_render_plan(project)
    return plan, [job.request for job in layer_jobs(plan, SW, SH, FPS)]


def wait(job: FlowAnalysisJob, seconds: float = 60.0):
    assert job.join(seconds), "l'analyse n'a pas fini"
    return job.snapshot()


def test_the_analysis_stores_every_pair_the_export_will_ask_for(scene_media, tmp_path):
    cache = FlowCache(tmp_path / "flow")
    plan, requests = requests_for(scene_media, TimeRemapping(speed=0.5, interpolation=FLOW, flow_quality=FlowQuality.BALANCED))
    assert len(requests) == 1
    job = FlowAnalysisJob(requests, cache)
    assert job.snapshot().state is AnalysisState.PENDING
    job.start()
    snapshot = wait(job)
    assert snapshot.state is AnalysisState.DONE and snapshot.finished and snapshot.fraction == 1.0
    assert snapshot.report is not None and snapshot.report.pairs_computed == len(requests[0].plan().pairs()) > 0
    assert snapshot.report.pairs_cached == 0 and snapshot.report.backend == "numpy"
    # L'export relit tout : aucune paire à recalculer.
    preparation = prepare_plan(plan, SW, SH, FPS, cache)
    assert preparation.report.pairs_computed == 0 and preparation.report.pairs_cached == snapshot.report.pairs_computed


def test_a_second_analysis_is_a_cache_hit_and_a_speed_change_needs_no_new_pair(scene_media, tmp_path):
    cache = FlowCache(tmp_path / "flow")
    _plan, requests = requests_for(scene_media, TimeRemapping(speed=0.5, interpolation=FLOW, flow_quality=FlowQuality.BALANCED))
    first = FlowAnalysisJob(requests, cache)
    first.start()
    computed = wait(first).report.pairs_computed
    again = FlowAnalysisJob(requests, cache)
    again.start()
    repeat = wait(again).report
    assert repeat.pairs_computed == 0 and repeat.pairs_cached == computed
    _plan, slower = requests_for(scene_media, TimeRemapping(speed=0.4, interpolation=FLOW, flow_quality=FlowQuality.BALANCED))
    other = FlowAnalysisJob(slower, cache)
    other.start()
    assert wait(other).report.pairs_computed == 0                                          # le mouvement est celui du média


def test_progress_is_reported_while_it_runs_and_reaches_the_total(scene_media, tmp_path):
    _plan, requests = requests_for(scene_media, TimeRemapping(speed=0.25, interpolation=FLOW, flow_quality=FlowQuality.BEST))
    job = FlowAnalysisJob(requests, FlowCache(tmp_path / "flow"))
    job.start()
    seen: list[int] = []
    deadline = time.time() + 60
    while not job.snapshot().finished and time.time() < deadline:
        seen.append(job.snapshot().done)
        time.sleep(0.005)
    snapshot = wait(job)
    assert snapshot.state is AnalysisState.DONE and snapshot.total > 0 and snapshot.done == snapshot.total
    assert seen == sorted(seen)                                                            # jamais en arrière


def test_cancelling_stops_the_job_and_keeps_the_pairs_already_computed(scene_media, tmp_path):
    cache = FlowCache(tmp_path / "flow")
    plan, requests = requests_for(scene_media, TimeRemapping(speed=0.25, interpolation=FLOW, flow_quality=FlowQuality.BEST))
    job = FlowAnalysisJob(requests, cache)
    job.cancel()                                                                            # annulé avant même de commencer
    job.start()
    snapshot = wait(job)
    assert snapshot.state is AnalysisState.CANCELLED and snapshot.finished
    # Une annulation ne laisse rien de cassé : l'export calcule ce qui manque.
    assert prepare_plan(plan, SW, SH, FPS, cache).streams


def test_blending_has_no_motion_to_analyze(scene_media, tmp_path):
    _plan, requests = requests_for(scene_media, TimeRemapping(speed=0.5, interpolation=BLENDING))
    job = FlowAnalysisJob(requests, FlowCache(tmp_path / "flow"))
    job.start()
    snapshot = wait(job)
    assert snapshot.state is AnalysisState.DONE and snapshot.report.pairs_computed == 0 and snapshot.total == 0


def test_a_missing_media_is_a_failure_with_its_cause_not_a_crash(tmp_path):
    _plan, requests = requests_for(str(tmp_path / "gone.mp4"), TimeRemapping(speed=0.5, interpolation=FLOW))
    job = FlowAnalysisJob(requests, FlowCache(tmp_path / "flow"))
    job.start()
    snapshot = wait(job)
    assert snapshot.state is AnalysisState.FAILED and "introuvable" in snapshot.message


def test_a_started_job_cannot_be_started_twice(scene_media, tmp_path):
    _plan, requests = requests_for(scene_media, TimeRemapping(speed=0.5, interpolation=FLOW, flow_quality=FlowQuality.DRAFT))
    job = FlowAnalysisJob(requests, FlowCache(tmp_path / "flow"))
    job.start()
    job.start()
    assert wait(job).state is AnalysisState.DONE
