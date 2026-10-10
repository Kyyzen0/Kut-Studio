"""Tracking 2D : modèle, analyse, liaisons, stabilisation, sérialisation, rendu.

Les vidéos sont synthétiques (:mod:`tests.tracking_media`) : positions,
rotations et zooms connus exactement, résultats reproductibles en CI.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest

from core.compositing import Compositing, Mask
from core.edit_history import ProjectHistory
from core.graphics import add_graphic_clip, update_graphic
from core.project_io import load_project, save_project
from core.project_model import Clip, MediaAsset, Project, Track
from core.render_plan import build_render_plan
from core.tracking_bindings import TrackingContext, effective_clip_state, simplify
from core.tracking_engine import (
    StopReason,
    TrackingCache,
    TrackingJob,
    cache_key,
    run_tracking,
)
from core.tracking_frames import analysis_geometry
from core.tracking_model import (
    BorderMode,
    ClipTracking,
    Sample,
    SampleStatus,
    Smoothing,
    StabilizationMode,
    TrackData,
    TrackTarget,
    quantize,
)
from core import tracking_ops as ops
from core.tracking_motion import (
    fit_box,
    fit_similarity,
    gaussian_smooth,
    inscribed_factor,
    source_time,
    video_layer_matrix,
)
from core.mograph_scene import mat_apply
from core.visual_effects import ClipTransform, evaluate_transform

from tests.tracking_media import HAS_FFMPEG, moving_points_video, rigid_points

np = pytest.importorskip("numpy")
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="FFmpeg absent")

W, H, FPS = 320, 180, 30.0
FRAMES = 40


def _translation(i: int) -> list[tuple[float, float]]:
    return [(80.0 + 2.0 * i, 60.0 + 1.0 * i)]


def _project(path: Path, *, frames: int = FRAMES, size=(W, H), canvas=(W, H)) -> Project:
    project = Project(name="Suivi", width=canvas[0], height=canvas[1], fps=FPS)
    project.media_assets.append(MediaAsset("a", str(path), "src", frames / FPS, size[0], size[1], FPS, "video"))
    project.tracks.insert(0, Track("V1", "V1", "video", clips=[Clip("v", "a", "V1", 0.0, 0.0, frames / FPS)]))
    return project


def _clip(project: Project, clip_id: str = "v"):
    return ops.find_clip_and_track(project, clip_id)[0]


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    if not HAS_FFMPEG:
        pytest.skip("FFmpeg absent")
    root = tmp_path_factory.mktemp("tracking-media")
    offsets = [(-45.0, -12.0), (45.0, 12.0)]

    def rigid(i):  # rotation de 1,2°/image et zoom de 0,4 %/image, loin des bords
        return rigid_points((160 + 1.0 * i, 90 + 0.2 * i), 0, 1.2 * i, 1 + 0.004 * i, offsets)

    def vanish(i):
        return _translation(i) if i < 15 else []

    def leave(i):
        return [(250.0 + 6.0 * i, 90.0)]

    return {
        "translation": moving_points_video(root / "translation.mkv", width=W, height=H, fps=FPS,
                                           count=FRAMES, points_at=_translation),
        "rigid": moving_points_video(root / "rigid.mkv", width=W, height=H, fps=FPS, count=FRAMES,
                                     points_at=rigid),
        "rigid_points": rigid,
        "vanish": moving_points_video(root / "vanish.mkv", width=W, height=H, fps=FPS, count=FRAMES,
                                      points_at=vanish),
        "leave": moving_points_video(root / "leave.mkv", width=W, height=H, fps=FPS, count=FRAMES,
                                     points_at=leave),
        "long": _long_video(root / "long.mp4"),
    }


def _long_video(path: Path) -> Path:
    """Mire 720p de 20 s (600 images) : assez longue pour annuler en cours d'analyse."""
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=1280x720:r=30:d=20",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path)],
        check=True,
    )
    return path


@pytest.fixture(autouse=True)
def _cache_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("KUT_STUDIO_CACHE_DIR", str(tmp_path / "cache"))


def _tracked_project(media, name="translation", *, points=None, frames=FRAMES):
    project = _project(media[name], frames=frames)
    ids = []
    for x, y in points or _translation(0):
        ids.append(ops.add_tracker(project, "v", timeline_time=0.0, x=x, y=y).id)
    result = run_tracking(ops.analysis_request(project, "v", ids, timeline_time=0.0, direction=1))
    ops.apply_tracking_result(project, result)
    return project, ids, result


# --- Modèle ---------------------------------------------------------------------------------------


def test_track_data_is_compact_exact_and_interpolates_between_valid_frames():
    samples = {
        10: Sample(1.25, 2.5, 1.0, SampleStatus.MANUAL),
        11: Sample(3.0, 4.0, 0.9, SampleStatus.TRACKED),
        12: Sample(99.0, 99.0, 0.1, SampleStatus.LOST),
        13: Sample(7.0, 8.0, 0.7, SampleStatus.UNCERTAIN),
    }
    data = TrackData.from_samples(30.0, samples, source_size=(640, 360))
    assert (data.first, data.last, data.count) == (10, 13, 4)
    assert data.valid_indices() == (10, 11, 13)
    # Une image perdue n'est jamais utilisée : interpolation entre 11 et 13.
    assert data.position_at_index(12) == pytest.approx((5.0, 6.0))
    assert data.position_at_index(0) == pytest.approx((1.25, 2.5))  # tenue avant
    assert data.position_at_index(100) == pytest.approx((7.0, 8.0))  # tenue après
    restored = TrackData.from_dict(json.loads(json.dumps(data.to_dict())))
    assert restored == data
    assert hash(restored) == hash(data)
    assert TrackData().position_at_index(3) is None
    assert quantize(0.1234567) == round(0.1234567 * 1024) / 1024


def test_track_data_merge_reset_and_manual_preservation():
    data = TrackData.from_samples(30.0, {i: Sample(i, i, 1.0, SampleStatus.TRACKED) for i in range(10)})
    data = data.with_samples({5: Sample(50, 50, 1.0, SampleStatus.MANUAL)})
    cleared = data.cleared(3, 8, keep_manual=True)
    assert cleared.valid_indices() == (0, 1, 2, 5, 9)
    assert cleared.status_at(5) is SampleStatus.MANUAL


def test_corrupt_tracking_entries_are_ignored_without_losing_the_clip():
    raw = {
        "trackers": [
            {"id": "a", "name": "OK", "data": TrackData.from_samples(
                30.0, {0: Sample(1, 2, 1, SampleStatus.MANUAL)}).to_dict()},
            {"id": "b", "data": {"count": 5, "x": "pas du base64"}},
            "n'importe quoi",
        ],
        "links": [{"target": "inconnu", "tracker_ids": ["a"]}, 42],
        "stabilization": {"mode": "?", "smoothing": "?", "borders": "?", "tracker_ids": ["a"]},
    }
    tracking = ClipTracking.from_dict(raw)
    assert [t.name for t in tracking.trackers] == ["OK", "Tracker"]
    assert tracking.trackers[1].data.count == 0  # données illisibles → vides, pas d'exception
    assert tracking.links[0].target == TrackTarget.TRANSFORM
    assert tracking.stabilization.mode == StabilizationMode.POSITION


# --- Géométrie --------------------------------------------------------------------------------------


def test_time_mapping_follows_trim_speed_reverse_and_freeze():
    from core.time_remapping import FreezeFrameMode, TimeRemapping

    clip = Clip("c", "a", "V1", 5.0, 2.0, 6.0)
    assert source_time(clip, 1.0) == pytest.approx(3.0)
    clip.time_remapping = TimeRemapping(speed=2.0)
    assert source_time(clip, 1.0) == pytest.approx(4.0)
    clip.time_remapping = TimeRemapping(speed=1.0, reverse=True)
    assert source_time(clip, 1.0) == pytest.approx(5.0)
    clip.time_remapping = TimeRemapping(
        freeze_mode=FreezeFrameMode.FREEZE, freeze_source_time=3.5, freeze_duration=2.0
    )
    assert source_time(clip, 1.7) == pytest.approx(3.5)


def test_similarity_fit_recovers_rotation_scale_and_translation():
    source = [(0.0, 0.0), (10.0, 0.0), (0.0, 5.0)]
    angle, k = math.radians(30), 1.5
    target = [
        (100 + k * (x * math.cos(angle) - y * math.sin(angle)), 50 + k * (x * math.sin(angle) + y * math.cos(angle)))
        for x, y in source
    ]
    cx, cy, theta, scale, gx, gy = fit_similarity(source, target)
    assert theta == pytest.approx(30.0, abs=1e-6)
    assert scale == pytest.approx(1.5, abs=1e-9)


def test_robust_fit_ignores_a_point_that_moves_on_its_own():
    from core.tracking_motion import robust_fit, similarity

    source = [(0.0, 0.0), (100.0, 0.0), (0.0, 80.0), (100.0, 80.0)]
    truth = similarity(20.0, 10.0, 5.0, 1.0, 50.0, 40.0)
    target = [mat_apply(truth, *p) for p in source]
    target[3] = (target[3][0] + 25.0, target[3][1] - 12.0)  # une vague
    cx, cy, theta, k, gx, gy = robust_fit(source, target)
    fitted = similarity(cx, cy, theta, k, gx, gy)
    for point in source[:3]:
        assert mat_apply(fitted, *point) == pytest.approx(mat_apply(truth, *point), abs=1e-6)
    plain = fit_similarity(source, target)
    assert abs(plain[2] - 5.0) > 0.5  # sans rejet, la vague fausse la rotation


def test_fit_box_matches_ffmpeg_scale_and_pad():
    fit = fit_box(3840, 2160, 1920, 1080)
    assert (fit.scale_x, fit.offset_x, fit.offset_y) == (0.5, 0.0, 0.0)
    pillar = fit_box(1080, 1920, 1920, 1080)
    assert pillar.offset_x == (1920 - 608) // 2 and pillar.height == 1080


def test_smoothing_and_inscribed_rectangle():
    noisy = [math.sin(i) * 5 + i for i in range(200)]
    smooth = gaussian_smooth(noisy, 8.0)
    assert max(abs(a - b) for a, b in zip(smooth[20:-20], range(20, 180))) < 1.0
    square = [(0, 0), (100, 0), (100, 100), (0, 100)]
    shifted = [(x + 10, y) for x, y in square]
    assert inscribed_factor([square, shifted], (0, 0, 100, 100)) == pytest.approx(0.8)


def test_simplification_keeps_values_within_tolerance():
    times = [i / 30 for i in range(300)]
    values = [math.sin(t * 0.5) for t in times]  # mouvement lent : la corde reste proche
    kept = simplify(times, values, 1e-3)
    assert len(kept) < 80
    for i, t in enumerate(times):
        right = next(k for k in kept if times[k] >= t)
        left = max(k for k in kept if times[k] <= t)
        u = 0 if right == left else (t - times[left]) / (times[right] - times[left])
        assert abs(values[left] + (values[right] - values[left]) * u - values[i]) <= 1e-3 + 1e-12


# --- Analyse ------------------------------------------------------------------------------------------


@needs_ffmpeg
def test_create_track_forward_and_backward(media):
    project, ids, result = _tracked_project(media)
    tracker = ops.tracking_of(_clip(project)).tracker(ids[0])
    assert result.state == "finished"
    assert result.outcomes[ids[0]].reason == StopReason.RANGE_END
    errors = [math.dist((tracker.data.sample(i).x, tracker.data.sample(i).y), _translation(i)[0])
              for i in range(FRAMES)]
    assert max(errors) < 0.25
    # Arrière : on efface tout sauf la dernière image, posée à la main.
    clip = _clip(project)
    last_time = (FRAMES - 1) / FPS
    ops.set_tracker_position(project, "v", ids[0], last_time, *_translation(FRAMES - 1)[0])
    ops.reset_tracker(project, "v", ids[0], timeline_time=last_time, direction=-1)
    tracker = ops.tracking_of(clip).tracker(ids[0])
    assert tracker.data.valid_indices() == (0, FRAMES - 1)  # deux corrections manuelles restent
    backward = run_tracking(ops.analysis_request(project, "v", ids, timeline_time=last_time, direction=-1))
    outcome = backward.outcomes[ids[0]]
    assert outcome.reason == StopReason.MANUAL and outcome.stop_index == 0  # arrêt sur la correction
    ops.apply_tracking_result(project, backward)
    tracker = ops.tracking_of(clip).tracker(ids[0])
    assert len(tracker.data.valid_indices()) == FRAMES
    assert tracker.data.status_at(0) is SampleStatus.MANUAL


@needs_ffmpeg
def test_lost_track_is_flagged_and_never_invented(media):
    project, ids, result = _tracked_project(media, "vanish")
    outcome = result.outcomes[ids[0]]
    assert outcome.reason == StopReason.LOST and outcome.stop_index == 15
    tracker = ops.tracking_of(_clip(project)).tracker(ids[0])
    assert tracker.data.status_at(15) is SampleStatus.LOST
    assert tracker.data.valid_range() == (0, 14)
    # Une cible liée tient la dernière position valide (aucun NaN, aucun saut inventé).
    text = add_graphic_clip(project, "text", timeline_start=0.0, duration=FRAMES / FPS)
    ops.add_link(project, text.id, "v", ids, timeline_time=0.0)
    state = effective_clip_state(text, TrackingContext(project))
    values = [evaluate_transform(text.transform, list(state.transform_keyframes), i / FPS, 2.0).position_x
              for i in range(FRAMES)]
    assert all(math.isfinite(v) for v in values)
    assert values[-1] == pytest.approx(values[14], abs=1e-4)


@needs_ffmpeg
def test_point_leaving_the_frame_stops_the_analysis(media):
    _project_, ids, result = _tracked_project(media, "leave", points=[(250.0, 90.0)])
    outcome = result.outcomes[ids[0]]
    assert outcome.reason in (StopReason.OUT_OF_FRAME, StopReason.LOST)
    assert outcome.stop_index is not None and outcome.stop_index < FRAMES - 1


@needs_ffmpeg
def test_manual_correction_then_partial_recompute(media):
    project, ids, _result = _tracked_project(media)
    clip = _clip(project)
    before = ops.tracking_of(clip).tracker(ids[0]).data
    ops.set_tracker_position(project, "v", ids[0], 20 / FPS, 121.0, 81.0)  # décalée de (+1, +1) px
    after = ops.tracking_of(clip).tracker(ids[0]).data
    assert after.status_at(20) is SampleStatus.MANUAL
    assert after.sample(25) == before.sample(25)  # le reste n'est pas effacé
    rerun = run_tracking(ops.analysis_request(project, "v", ids, timeline_time=20 / FPS, direction=1))
    ops.apply_tracking_result(project, rerun)
    final = ops.tracking_of(clip).tracker(ids[0]).data
    assert final.sample(10) == before.sample(10)  # avant la correction : intact
    assert final.sample(30).x == pytest.approx(before.sample(30).x + 1.0, abs=0.3)  # suit la correction


@needs_ffmpeg
def test_cache_hits_and_invalidation(media, tmp_path):
    project = _project(media["translation"])
    tracker_id = ops.add_tracker(project, "v", timeline_time=0.0, x=80, y=60).id
    cache = TrackingCache()
    request = ops.analysis_request(project, "v", [tracker_id], timeline_time=0.0, direction=1)
    first = run_tracking(request, cache=cache)
    second = run_tracking(request, cache=cache)
    assert not first.outcomes[tracker_id].from_cache and second.outcomes[tracker_id].from_cache
    assert second.outcomes[tracker_id].samples == first.outcomes[tracker_id].samples
    assert cache.stats()["entries"] == 1
    tracker = request.trackers[0]
    geometry = analysis_geometry(W, H)
    key = cache_key(request, tracker, geometry)
    # Réglages, position de départ, plage ou fichier différents : autre clé.
    moved = replace(tracker, data=tracker.data.with_samples({0: Sample(81, 60, 1, SampleStatus.MANUAL)}))
    assert cache_key(replace(request, trackers=(moved,)), moved, geometry) != key
    resized = replace(tracker, settings=replace(tracker.settings, pattern_width=40.0))
    assert cache_key(request, resized, geometry) != key
    assert cache_key(replace(request, end_index=20), tracker, geometry) != key
    assert cache_key(replace(request, source_token="autre"), tracker, geometry) != key
    assert cache.purge() > 0 and cache.stats()["entries"] == 0


@needs_ffmpeg
def test_cancellation_is_fast_keeps_partial_frames_and_resumes(media):
    project = _project(media["long"], frames=600, size=(1280, 720), canvas=(1280, 720))
    tracker_id = ops.add_tracker(project, "v", timeline_time=0.0, x=640, y=360).id
    ops.set_tracker_settings(project, "v", tracker_id, stop_on_loss=False, precision="full")
    job = TrackingJob(ops.analysis_request(project, "v", [tracker_id], timeline_time=0.0, direction=1))
    worker = threading.Thread(target=job.run)
    worker.start()
    deadline = time.time() + 10
    while time.time() < deadline and job.snapshot().current_index < 5:
        time.sleep(0.005)
    started = time.perf_counter()
    job.cancel()
    assert job.wait(5.0)
    assert time.perf_counter() - started < 1.0
    worker.join(2.0)
    snapshot = job.snapshot()
    assert snapshot.state == "cancelled"
    samples = snapshot.result.outcomes[tracker_id].samples
    assert 0 < len(samples) < 599
    ops.apply_tracking_result(project, snapshot.result)
    # Reprise depuis la dernière image valide (une image perdue ne sert pas de départ).
    last_valid = max(i for i, sample in samples.items() if sample.valid)
    request = ops.analysis_request(project, "v", [tracker_id], timeline_time=last_valid / FPS, direction=1)
    assert request.start_index == last_valid
    resumed = TrackingJob(replace(request, end_index=last_valid + 10))
    result = resumed.run()
    assert result.state == "finished"
    assert sorted(result.outcomes[tracker_id].samples) == list(range(last_valid + 1, last_valid + 11))


@needs_ffmpeg
def test_only_frames_shown_by_the_clip_are_analysed(media):
    project = _project(media["translation"])
    clip = _clip(project)
    clip.source_in, clip.source_out = 10 / FPS, 20 / FPS
    tracker_id = ops.add_tracker(project, "v", timeline_time=0.0, x=100, y=70).id
    request = ops.analysis_request(project, "v", [tracker_id], timeline_time=0.0, direction=1)
    assert (request.start_index, request.end_index) == (10, 19)
    result = run_tracking(request)
    assert sorted(result.outcomes[tracker_id].samples) == list(range(11, 20))


@needs_ffmpeg
def test_variable_frame_rate_source_is_read_on_the_nominal_grid(tmp_path):
    from core.tracking_frames import FrameReader

    source = tmp_path / "vfr.mkv"
    # Une image sur quatre supprimée, horodatages conservés : cadence variable.
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=160x90:r=30:d=2",
         "-vf", "select='not(eq(mod(n\\,4)\\,1))'", "-fps_mode", "vfr", "-c:v", "ffv1", str(source)],
        check=True,
    )
    reader = FrameReader(str(source), analysis_geometry(160, 90), 30.0)
    frames = list(reader.frames(10, 29))
    assert [index for index, _frame in frames] == list(range(10, 30))  # grille complète, aucun trou
    assert all(frame.shape == (90, 160) for _index, frame in frames)
    backward = list(reader.frames(29, 10))
    assert [index for index, _frame in backward] == list(range(29, 9, -1))
    assert all(np.array_equal(a, b) for (_i, a), (_j, b) in zip(frames, reversed(backward)))


@needs_ffmpeg
def test_truncated_media_stops_at_the_last_readable_frame(media, tmp_path):
    damaged = tmp_path / "tronque.mkv"
    data = Path(media["translation"]).read_bytes()
    damaged.write_bytes(data[: len(data) // 2])  # fin du fichier perdue
    project = _project(damaged)
    tracker_id = ops.add_tracker(project, "v", timeline_time=0.0, x=80, y=60).id
    result = run_tracking(ops.analysis_request(project, "v", [tracker_id], timeline_time=0.0, direction=1))
    outcome = result.outcomes[tracker_id]
    assert result.state == "finished"
    assert outcome.reason == StopReason.MEDIA_END
    assert 0 < len(outcome.samples) < FRAMES - 1
    assert all(sample.valid for sample in outcome.samples.values())


@needs_ffmpeg
def test_trimming_after_analysis_keeps_targets_on_the_right_frames(media):
    project, ids, _result = _tracked_project(media)
    text = add_graphic_clip(project, "text", timeline_start=0.0, duration=20 / FPS)
    ops.add_link(project, text.id, "v", ids, timeline_time=0.0)
    clip = _clip(project)
    clip.source_in = 10 / FPS  # trim gauche de 10 images, après l'analyse
    layer = next(item for item in build_render_plan(project).graphics_layers if item.clip_id == text.id)
    values = evaluate_transform(layer.transform, list(layer.transform_keyframes), 5 / FPS, 20 / FPS)
    # La référence (image source 0) n'est plus montrée : la cible suit l'image source 15.
    assert values.position_x * W == pytest.approx(2.0 * 15, abs=0.3)
    assert values.position_y * H == pytest.approx(1.0 * 15, abs=0.3)


@needs_ffmpeg
def test_missing_media_fails_cleanly(media, tmp_path):
    project = _project(media["translation"])
    tracker_id = ops.add_tracker(project, "v", timeline_time=0.0, x=80, y=60).id
    request = ops.analysis_request(project, "v", [tracker_id], timeline_time=0.0, direction=1)
    result = run_tracking(replace(request, media_path=str(tmp_path / "absent.mkv")))
    assert result.state == "failed" and result.message.startswith("offline")
    assert result.outcomes[tracker_id].reason == StopReason.FAILED


# --- Liaisons, bake, masques --------------------------------------------------------------------------


@needs_ffmpeg
def test_text_follows_tracker_dynamically_and_bake_freezes_it(media):
    project, ids, _result = _tracked_project(media)
    text = add_graphic_clip(project, "text", timeline_start=0.0, duration=FRAMES / FPS)
    text.transform = ClipTransform(position_x=0.1)
    link = ops.add_link(project, text.id, "v", ids, timeline_time=10 / FPS)

    def positions():
        layer = next(item for item in build_render_plan(project).graphics_layers if item.clip_id == text.id)
        return [
            evaluate_transform(layer.transform, list(layer.transform_keyframes), i / FPS, FRAMES / FPS)
            for i in (10, 30)
        ]

    at_reference, later = positions()
    assert at_reference.position_x == pytest.approx(0.1, abs=1e-4)  # référence : valeur saisie
    assert (later.position_x - 0.1) * W == pytest.approx(40.0, abs=0.3)  # +2 px / image
    assert (later.position_y * H) == pytest.approx(20.0, abs=0.3)
    # Liaison dynamique : corriger le tracker met la cible à jour.
    ops.set_tracker_position(project, "v", ids[0], 30 / FPS, 140.0 + 10, 90.0)
    assert (positions()[1].position_x - 0.1) * W == pytest.approx(50.0, abs=0.3)
    count = ops.bake_link(project, text.id, link.id)
    assert count >= 2 and text.tracking is None
    ops.set_tracker_position(project, "v", ids[0], 30 / FPS, 0.0, 0.0)
    assert (positions()[1].position_x - 0.1) * W == pytest.approx(50.0, abs=0.3)  # figé


@needs_ffmpeg
def test_mask_follows_the_subject_in_layer_space(media):
    project, ids, _result = _tracked_project(media)
    clip = _clip(project)
    clip.transform = ClipTransform(scale=0.5, position_x=0.2)  # le masque suit le contenu, pas le cadre
    mask = Mask(position_x=80 / W, position_y=60 / H, width=0.1, height=0.1)
    clip.compositing = Compositing(masks=(mask,))
    ops.add_link(project, "v", "v", ids, target=TrackTarget.MASK, mask_id=mask.id, timeline_time=0.0)
    layer = build_render_plan(project).video_layers[0]
    from core.animation_targets import animation_curves
    from core.compositing import evaluate_mask_at

    curves = animation_curves(type("V", (), {"animation": layer.animation})())
    evaluated = evaluate_mask_at(mask, curves, 20 / FPS)
    assert evaluated.position_x * W == pytest.approx(80 + 40, abs=0.3)
    assert evaluated.position_y * H == pytest.approx(60 + 20, abs=0.3)


@needs_ffmpeg
def test_a_colour_node_window_follows_the_subject_like_a_mask(media):
    """Une fenêtre d'un nœud d'étalonnage est une cible de tracking comme un masque : même liaison, mêmes images-clés
    dérivées (``mask.<id>.*``), que la matte de l'étalonnage lit à l'export."""
    from core.animation_targets import animation_curves
    from core.color_grading import ColorGrade
    from core.color_nodes import ColorNode, ColorNodeGraph
    from core.compositing import evaluate_mask_at

    project, ids, _result = _tracked_project(media)
    clip = _clip(project)
    window = Mask(position_x=80 / W, position_y=60 / H, width=0.1, height=0.1)
    clip.color_grade = ColorNodeGraph.serial((ColorNode("n1", ColorGrade(exposure=0.5), windows=(window,)),))
    entry = next(item for item in ops.link_targets(project, "v") if item["clip_id"] == "v")
    assert (TrackTarget.MASK, window.id) in entry["targets"] and "n1" in entry["masks"][window.id]
    ops.add_link(project, "v", "v", ids, target=TrackTarget.MASK, mask_id=window.id, timeline_time=0.0)
    layer = build_render_plan(project).video_layers[0]
    curves = animation_curves(type("V", (), {"animation": layer.animation})())
    evaluated = evaluate_mask_at(window, curves, 20 / FPS)
    assert evaluated.position_x * W == pytest.approx(80 + 40, abs=0.3)
    assert evaluated.position_y * H == pytest.approx(60 + 20, abs=0.3)


@needs_ffmpeg
def test_anchor_link_pins_the_tracked_point(media):
    project, ids, _result = _tracked_project(media)
    clip = _clip(project)
    ops.add_link(project, "v", "v", ids, target=TrackTarget.ANCHOR, timeline_time=0.0)
    layer = build_render_plan(project).video_layers[0]
    for i in (0, 20, 39):
        values = evaluate_transform(layer.transform, list(layer.transform_keyframes), i / FPS, clip.duration)
        point = mat_apply(video_layer_matrix(values, W, H), *_translation(i)[0])
        assert point == pytest.approx((W / 2, H / 2), abs=0.3)  # le point suivi reste au centre


@needs_ffmpeg
def test_two_point_link_applies_rotation_and_scale(media):
    rigid = media["rigid_points"]
    project, ids, _result = _tracked_project(media, "rigid", points=rigid(0))
    shape = add_graphic_clip(project, "shape", timeline_start=0.0, duration=FRAMES / FPS)
    with pytest.raises(ops.TrackingError):
        ops.add_link(project, shape.id, "v", ids[:1], rotation=True, timeline_time=0.0)
    ops.add_link(project, shape.id, "v", ids, rotation=True, scale=True, timeline_time=0.0)
    layer = next(item for item in build_render_plan(project).graphics_layers if item.clip_id == shape.id)
    values = evaluate_transform(layer.transform, list(layer.transform_keyframes), 30 / FPS, FRAMES / FPS)
    assert values.rotation == pytest.approx(36.0, abs=0.3)
    assert values.scale == pytest.approx(1.12, abs=0.005)


@needs_ffmpeg
def test_removing_a_tracker_cleans_links_and_stabilization(media):
    rigid = media["rigid_points"]
    project, ids, _result = _tracked_project(media, "rigid", points=rigid(0))
    text = add_graphic_clip(project, "text", timeline_start=0.0, duration=1.0)
    ops.add_link(project, text.id, "v", ids, rotation=True, timeline_time=0.0)
    ops.set_stabilization(project, "v", tracker_ids=tuple(ids))
    ops.remove_tracker(project, "v", ids[0])
    clip = _clip(project)
    assert [t.id for t in clip.tracking.trackers] == [ids[1]]
    assert clip.tracking.stabilization.tracker_ids == (ids[1],)
    assert text.tracking.links[0].tracker_ids == (ids[1],)  # la liaison garde le tracker restant
    ops.remove_tracker(project, "v", ids[1])
    assert clip.tracking is None  # plus rien : le clip redevient neutre (rien d'écrit dans le .kut)
    assert text.tracking is None
    # Le rendu ne casse pas : la cible garde ses propres valeurs.
    layer = next(item for item in build_render_plan(project).graphics_layers if item.clip_id == text.id)
    assert layer.transform_keyframes == ()


def test_invalid_links_are_refused():
    project = Project(name="x", width=W, height=H, fps=FPS)
    project.media_assets.append(MediaAsset("a", "/absent.mkv", "src", 1.0, W, H, FPS, "video"))
    project.tracks.insert(0, Track("V1", "V1", "video", clips=[Clip("v", "a", "V1", 0.0, 0.0, 1.0)]))
    tracker = ops.add_tracker(project, "v", timeline_time=0.0)
    with pytest.raises(ops.TrackingError, match="Analysez"):
        ops.add_link(project, "v", "v", [tracker.id], target=TrackTarget.ANCHOR)
    with pytest.raises(ops.TrackingError):
        ops.analysis_request(project, "v", ["absent"], timeline_time=0.0, direction=1)


# --- Stabilisation ----------------------------------------------------------------------------------


def _stabilized_points(project, points_at, frames=FRAMES):
    clip = _clip(project)
    state = effective_clip_state(clip, TrackingContext(project))
    result = []
    for i in range(frames):
        values = evaluate_transform(clip.transform, list(state.transform_keyframes), i / FPS, clip.duration)
        matrix = video_layer_matrix(values, W, H)
        result.append([mat_apply(matrix, *p) for p in points_at(i)])
    return result, state


@needs_ffmpeg
def test_stabilization_position_locks_the_tracked_point(media):
    project, ids, _result = _tracked_project(media)
    ops.set_stabilization(project, "v", tracker_ids=tuple(ids), smoothing=Smoothing.LOCKED,
                          borders=BorderMode.BLACK, reference_index=0)
    points, state = _stabilized_points(project, _translation)
    assert max(math.dist(p[0], _translation(0)[0]) for p in points) < 0.3
    assert state.zoom == 1.0


@needs_ffmpeg
@pytest.mark.parametrize("mode", [StabilizationMode.POSITION_ROTATION, StabilizationMode.POSITION_ROTATION_SCALE])
def test_stabilization_rotation_and_scale(media, mode):
    rigid = media["rigid_points"]
    project, ids, _result = _tracked_project(media, "rigid", points=rigid(0))
    ops.set_stabilization(project, "v", tracker_ids=tuple(ids), mode=mode, smoothing=Smoothing.LOCKED,
                          borders=BorderMode.BLACK, reference_index=0)
    points, _state = _stabilized_points(project, rigid)
    reference = rigid(0)
    if mode == StabilizationMode.POSITION_ROTATION_SCALE:
        assert max(math.dist(p, r) for frame in points for p, r in zip(frame, reference)) < 0.5
    else:
        # Rotation compensée : le segment garde son orientation ; l'échelle (+16 %) reste.
        for frame in points:
            angle = math.degrees(math.atan2(frame[1][1] - frame[0][1], frame[1][0] - frame[0][0]))
            ref = math.degrees(math.atan2(reference[1][1] - reference[0][1], reference[1][0] - reference[0][0]))
            assert angle == pytest.approx(ref, abs=0.3)


@needs_ffmpeg
def test_stabilization_survives_a_lost_tracker(media):
    rigid = media["rigid_points"]
    project, ids, _result = _tracked_project(media, "rigid", points=rigid(0))
    # Un troisième tracker perdu dès l'image 5 ne doit pas désactiver la stabilisation.
    clip = _clip(project)
    lost = ops.add_tracker(project, "v", timeline_time=0.0, x=20.0, y=20.0)
    data = lost.data.with_samples({i: Sample(20.0, 20.0, 0.9, SampleStatus.TRACKED) for i in range(1, 5)})
    data = data.with_samples({5: Sample(20.0, 20.0, 0.1, SampleStatus.LOST)})
    clip.tracking = clip.tracking.with_tracker(replace(lost, data=data))
    ops.set_stabilization(project, "v", tracker_ids=(*ids, lost.id),
                          mode=StabilizationMode.POSITION_ROTATION_SCALE, smoothing=Smoothing.LOCKED,
                          borders=BorderMode.BLACK, reference_index=0)
    points, _state = _stabilized_points(project, rigid)
    reference = rigid(0)
    # Le point immobile des 5 premières images est écarté (aberrant), puis absent.
    assert max(math.dist(p, r) for frame in points[6:] for p, r in zip(frame, reference)) < 0.6


@needs_ffmpeg
def test_stabilization_smoothing_zoom_and_crop(media):
    rigid = media["rigid_points"]
    project, ids, _result = _tracked_project(media, "rigid", points=rigid(0))
    ops.set_stabilization(project, "v", tracker_ids=tuple(ids), mode=StabilizationMode.POSITION_ROTATION,
                          smoothing=Smoothing.HIGH, borders=BorderMode.ZOOM)
    report = ops.stabilization_report(project, "v")
    assert report["active"] and report["zoom"] > 1.01
    clip = _clip(project)
    state = effective_clip_state(clip, TrackingContext(project))
    assert state.zoom == pytest.approx(report["zoom"])
    # Zoom automatique : le cadre est entièrement couvert à chaque image.
    for i in range(FRAMES):
        values = evaluate_transform(clip.transform, list(state.transform_keyframes), i / FPS, clip.duration)
        matrix = video_layer_matrix(values, W, H)
        corners = [mat_apply(matrix, x, y) for x, y in ((0, 0), (W, 0), (W, H), (0, H))]
        assert inscribed_factor([corners], (0, 0, W, H)) >= 0.999
    ops.set_stabilization(project, "v", borders=BorderMode.CROP)
    state = effective_clip_state(clip, TrackingContext(project))
    assert state.zoom == 1.0
    assert [m.id for m in state.compositing.masks] == ["trackcrop"]
    assert clip.compositing.masks == ()  # le masque de recadrage n'est jamais stocké


# --- Sérialisation, historique ------------------------------------------------------------------------


@needs_ffmpeg
def test_serialization_roundtrip_is_compact_and_survives_without_cache(media, tmp_path):
    project, ids, _result = _tracked_project(media)
    text = add_graphic_clip(project, "text", timeline_start=0.0, duration=1.0)
    ops.add_link(project, text.id, "v", ids, timeline_time=0.0)
    ops.set_stabilization(project, "v", tracker_ids=tuple(ids))
    path = tmp_path / "suivi.kut"
    save_project(project, str(path))
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["version"] == 16
    tracker_json = json.dumps(raw["project"]["sequences"][0]["tracks"][0]["clips"][0]["tracking"])
    assert len(tracker_json) < 2500  # 40 images : quelques octets par image
    TrackingCache().purge()
    loaded = load_project(str(path))
    assert _clip(loaded).tracking == _clip(project).tracking
    assert ops.find_clip_and_track(loaded, text.id)[0].tracking == text.tracking
    # Le rendu ne dépend ni du cache ni du média : mêmes images-clés dérivées.
    before = build_render_plan(project).graphics_layers
    after = build_render_plan(loaded).graphics_layers
    assert [item.transform_keyframes for item in before] == [item.transform_keyframes for item in after]


def test_projects_without_tracking_keep_their_format(tmp_path):
    project = Project(name="v15", width=W, height=H, fps=FPS)
    path = tmp_path / "plain.kut"
    save_project(project, str(path))
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert "tracking" not in json.dumps(raw)
    raw["version"] = 15
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert load_project(str(path)).name == "v15"


@needs_ffmpeg
def test_undo_redo_and_snapshots_share_tracking_data(media):
    project = _project(media["translation"])
    history = ProjectHistory()
    history.reset(project)
    tracker_id = ops.add_tracker(project, "v", timeline_time=0.0, x=80, y=60).id
    history.record(project, "Ajouter un tracker")
    result = run_tracking(ops.analysis_request(project, "v", [tracker_id], timeline_time=0.0, direction=1))
    ops.apply_tracking_result(project, result)
    history.record(project, "Tracking avant")
    assert len(history) == 3  # une seule entrée pour toute l'analyse
    snapshot = history._undo_stack[-1].project
    data = _clip(project).tracking.tracker(tracker_id).data
    assert _clip(snapshot).tracking.tracker(tracker_id).data is data  # partagé, pas copié
    restored = history.undo()
    assert len(_clip(restored).tracking.tracker(tracker_id).data.valid_indices()) == 1
    restored = history.undo()
    assert _clip(restored).tracking is None
    redone = history.redo()
    redone = history.redo()
    assert len(_clip(redone).tracking.tracker(tracker_id).data.valid_indices()) == FRAMES


@needs_ffmpeg
def test_cut_keeps_tracking_valid_for_both_halves(media):
    from core.timeline_operations import cut_clip

    project, ids, _result = _tracked_project(media)
    ops.set_stabilization(project, "v", tracker_ids=tuple(ids), smoothing=Smoothing.LOCKED,
                          borders=BorderMode.BLACK, reference_index=0)
    cut_clip(project, "v", 20 / FPS)
    right = project.tracks[0].clips[1]
    assert right.tracking == _clip(project).tracking
    state = effective_clip_state(right, TrackingContext(project))
    values = evaluate_transform(right.transform, list(state.transform_keyframes), 5 / FPS, right.duration)
    point = mat_apply(video_layer_matrix(values, W, H), *_translation(25)[0])
    assert point == pytest.approx(_translation(0)[0], abs=0.3)


@needs_ffmpeg
def test_relinked_media_with_another_resolution_scales_the_track(media):
    project, ids, _result = _tracked_project(media)
    asset = project.media_assets[0]
    project.media_assets[0] = replace(asset, width=W * 2, height=H * 2)
    context = TrackingContext(project)
    clip = _clip(project)
    tracker = clip.tracking.tracker(ids[0])
    point = context.tracker_canvas_point(clip, tracker, 10 / FPS)
    assert point == pytest.approx(_translation(10)[0], abs=0.3)  # même endroit du cadre


# --- Rendu : aperçu et export ------------------------------------------------------------------------


def _export_frame(project: Project, t: float, tmp_path: Path):
    from core.export_engine import ExportEngine, input_arguments

    plan = build_render_plan(project)
    graph, video, audio, inputs = ExportEngine._build_filter_complex(plan, W, H, int(FPS), None)
    graph += f";[{video}]trim=start={t},setpts=PTS-STARTPTS,format=gray[probe];[{audio}]anullsink"
    out = tmp_path / f"frame-{t}.raw"
    command = ["ffmpeg", "-y", "-loglevel", "error"]
    for path in inputs:
        command += input_arguments(path)
    command += ["-filter_complex", graph, "-map", "[probe]", "-frames:v", "1", "-f", "rawvideo", str(out)]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert completed.returncode == 0, completed.stderr
    return np.frombuffer(out.read_bytes(), dtype=np.uint8).reshape(H, W)


def _blob_centre(frame) -> tuple[float, float]:
    weights = np.where(frame > 100, frame.astype(np.float64), 0.0)
    ys, xs = np.mgrid[0:frame.shape[0], 0:frame.shape[1]]
    total = weights.sum()
    return float((xs * weights).sum() / total), float((ys * weights).sum() / total)


@needs_ffmpeg
def test_exported_stabilization_keeps_the_subject_still(media, tmp_path):
    project, ids, _result = _tracked_project(media)
    ops.set_stabilization(project, "v", tracker_ids=tuple(ids), smoothing=Smoothing.LOCKED,
                          borders=BorderMode.BLACK, reference_index=0)
    first = _blob_centre(_export_frame(project, 0.0, tmp_path))
    later = _blob_centre(_export_frame(project, 30 / FPS, tmp_path))
    assert math.dist(first, later) < 1.0  # sans stabilisation : 67 px


@pytest.mark.usefixtures("qapp")
@needs_ffmpeg
def test_preview_and_export_place_a_linked_layer_identically(media, tmp_path):
    from core.mograph_raster import MographRenderer, scene_for_plan

    project, ids, _result = _tracked_project(media)
    project.tracks[0].visible = False  # seul le calque lié
    shape = add_graphic_clip(project, "shape", timeline_start=0.0, duration=FRAMES / FPS, shape="rectangle")
    update_graphic(shape, "width", 20)
    update_graphic(shape, "height", 20)
    update_graphic(shape, "fill_color", "#FFFFFF")
    ops.add_link(project, shape.id, "v", ids, timeline_time=0.0)
    t = 25 / FPS
    plan = build_render_plan(project)
    scene = scene_for_plan(plan)
    image = MographRenderer(scene, W, H, fps=FPS, quality="export").render(scene.top_level(), t)
    preview = np.array([[image.pixelColor(x, y).alpha() for x in range(W)] for y in range(H)], dtype=np.uint8)
    exported = _export_frame(project, t, tmp_path)
    preview_centre, export_centre = _blob_centre(preview), _blob_centre(exported)
    assert math.dist(preview_centre, export_centre) < 0.6
    assert preview_centre[0] - W / 2 == pytest.approx(50.0, abs=0.6)  # +2 px × 25 images


# --- Performance (petit garde-fou ; mesures complètes : tools/perf/tracking_bench.py) ----------------


@needs_ffmpeg
def test_link_resolution_is_cached_between_plan_builds(media):
    project, ids, _result = _tracked_project(media)
    text = add_graphic_clip(project, "text", timeline_start=0.0, duration=FRAMES / FPS)
    ops.add_link(project, text.id, "v", ids, timeline_time=0.0)
    build_render_plan(project)
    started = time.perf_counter()
    for _ in range(20):
        build_render_plan(project)
    assert (time.perf_counter() - started) / 20 < 0.02


@needs_ffmpeg
def test_derived_keyframes_are_deterministic_for_segment_fingerprints(media):
    import core.tracking_bindings as bindings
    from core.filter_graph import fingerprint_plan

    project, ids, _result = _tracked_project(media)
    text = add_graphic_clip(project, "text", timeline_start=0.0, duration=FRAMES / FPS)
    ops.add_link(project, text.id, "v", ids, timeline_time=0.0)
    first = fingerprint_plan(build_render_plan(project, window=(0.5, 1.0)))
    bindings._STATE_CACHE.clear()  # recalcul complet (autre session, cache évincé)
    assert fingerprint_plan(build_render_plan(project, window=(0.5, 1.0))) == first


def test_self_check_used_by_the_smoke_test_passes():
    from core.tracking_match import self_check

    assert self_check() == ""


def test_numpy_is_not_imported_by_the_core_rendering_modules():
    code = (
        "import sys; import core.render_plan, core.tracking_bindings, core.tracking_ops, core.project_io;"
        "print('numpy' in sys.modules)"
    )
    completed = subprocess.run(
        [os.sys.executable, "-c", code], capture_output=True, text=True,
        cwd=str(Path(__file__).resolve().parent.parent),
    )
    assert completed.stdout.strip() == "False", completed.stderr
