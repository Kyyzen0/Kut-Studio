"""Mesures du tracking 2D : analyse, mémoire, proxy, trackers simultanés, liaisons.

Usage::

    python -m tools.perf.tracking_bench --out docs/perf/tracking.json
    python -m tools.perf.tracking_bench --real "/chemin/vers/un/plan.mov"   # cas réel en plus

Scénarios synthétiques (mire ``testsrc2`` encodée en H.264, 6 s à 30 i/s) :

- ``analysis:<résolution>:<précision>`` : un tracker sur 150 images ;
- ``trackers:<n>`` : n trackers simultanés en 1080p (décodage partagé) ;
- ``proxy`` : un 4K analysé depuis l'original puis depuis un proxy 1080p ;
- ``cancel`` : délai entre l'annulation et l'arrêt effectif ;
- ``bindings`` : liaison d'un calque à un tracker de 10 min (18 000 images),
  construction du plan à froid puis avec le cache.

Chaque analyse tourne dans un **processus séparé** : ``peak_rss_mb`` est le
pic mémoire de cette analyse seule (décodage FFmpeg non compris, il vit
dans son propre processus). Les durées dépendent de la machine : le banc
documente des ordres de grandeur et leur évolution.

``--real`` : un vrai plan (par exemple le fond d'écran animé « Golden Gate »
de macOS, 4K HEVC 10 bits). Deux points saillants sont choisis
automatiquement, suivis 2 s en avant puis contrôlés par un suivi arrière
(erreur aller-retour), puis la stabilisation et une liaison sont mesurées.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RESOLUTIONS = {"720p": (1280, 720), "1080p": (1920, 1080), "4k": (3840, 2160)}
FPS = 30.0
FRAMES = 150


def _make_media(directory: Path, name: str, size: tuple[int, int], seconds: float = 6.0) -> Path:
    path = directory / f"{name}.mp4"
    if not path.exists():
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
             f"testsrc2=s={size[0]}x{size[1]}:r={FPS:g}:d={seconds}",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", str(path)],
            check=True,
        )
    return path


def _project(path: Path, size: tuple[int, int], frames: int, fps: float = FPS):
    from core.project_model import Clip, MediaAsset, Project, Track

    project = Project(name="bench", width=1920, height=1080, fps=30.0)
    project.media_assets.append(MediaAsset("a", str(path), "m", frames / fps, size[0], size[1], fps, "video"))
    project.tracks.insert(0, Track("V1", "V1", "video", clips=[Clip("v", "a", "V1", 0.0, 0.0, frames / fps)]))
    return project


def _analysis_worker(payload: str) -> None:
    """Exécuté dans un sous-processus : une analyse, mesures sur stdout (JSON)."""
    import resource

    from core import tracking_ops as ops
    from core.tracking_engine import run_tracking

    args = json.loads(payload)
    size = tuple(args["size"])
    project = _project(Path(args["path"]), size, args["frames"])
    ids = []
    for index in range(args["trackers"]):
        x = size[0] * (0.2 + 0.6 * ((index * 7) % 10) / 10)
        y = size[1] * (0.25 + 0.5 * ((index * 3) % 10) / 10)
        tracker = ops.add_tracker(project, "v", timeline_time=0.0, x=x, y=y)
        ops.set_tracker_settings(project, "v", tracker.id, precision=args["precision"], stop_on_loss=False)
        ids.append(tracker.id)
    request = ops.analysis_request(
        project, "v", ids, timeline_time=0.0, direction=1, proxy_path=args.get("proxy", ""),
    )
    before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    started = time.perf_counter()
    result = run_tracking(request)
    elapsed = time.perf_counter() - started
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    unit = 1 if sys.platform == "darwin" else 1024
    frames = result.frames_analyzed
    print(json.dumps({
        "state": result.state,
        "frames": frames,
        "seconds": round(elapsed, 3),
        "fps": round(frames / elapsed, 1) if elapsed > 0 else None,
        "ms_per_frame_per_tracker": round(elapsed * 1000 / max(1, frames) / max(1, len(ids)), 3),
        "peak_rss_mb": round(peak * unit / 1e6, 1),
        "rss_growth_mb": round((peak - before) * unit / 1e6, 1),
    }))


def _run_analysis(path: Path, size, *, precision="auto", trackers=1, frames=FRAMES, proxy="") -> dict:
    payload = json.dumps({
        "path": str(path), "size": list(size), "precision": precision, "trackers": trackers,
        "frames": frames, "proxy": proxy,
    })
    completed = subprocess.run(
        [sys.executable, "-m", "tools.perf.tracking_bench", "--worker", payload],
        capture_output=True, text=True, cwd=str(ROOT), env={**os.environ, "KUT_STUDIO_CACHE_DIR": tempfile.mkdtemp()},
    )
    if completed.returncode != 0:
        return {"error": completed.stderr.strip()[-400:]}
    return json.loads(completed.stdout.strip().splitlines()[-1])


def _cancel_latency(path: Path, size) -> dict:
    import threading

    from core import tracking_ops as ops
    from core.tracking_engine import TrackingJob

    project = _project(path, size, FRAMES)
    tracker = ops.add_tracker(project, "v", timeline_time=0.0)
    ops.set_tracker_settings(project, "v", tracker.id, stop_on_loss=False, precision="full")
    job = TrackingJob(ops.analysis_request(project, "v", [tracker.id], timeline_time=0.0, direction=1))
    thread = threading.Thread(target=job.run)
    thread.start()
    while job.snapshot().current_index < 10 and thread.is_alive():
        time.sleep(0.002)
    started = time.perf_counter()
    job.cancel()
    job.wait(10)
    latency = (time.perf_counter() - started) * 1000
    thread.join(5)
    return {"cancel_ms": round(latency, 1), "frames_before_cancel": job.snapshot().current_index}


def _bindings() -> dict:
    from core import tracking_ops as ops
    from core.graphics import add_graphic_clip
    from core.render_plan import build_render_plan
    from core.tracking_model import Sample, SampleStatus, TrackData

    frames = 18000  # 10 min à 30 i/s
    project = _project(Path("/absent.mp4"), (1920, 1080), frames)
    tracker = ops.add_tracker(project, "v", timeline_time=0.0)
    samples = {
        i: Sample(960 + 300 * math.sin(i / 90) + 3 * math.sin(i * 1.7), 540 + 200 * math.cos(i / 70),
                  0.95, SampleStatus.TRACKED)
        for i in range(frames)
    }
    clip = ops.find_clip_and_track(project, "v")[0]
    clip.tracking = clip.tracking.with_tracker(
        type(tracker)(**{**tracker.__dict__, "data": TrackData.from_samples(30.0, samples, source_size=(1920, 1080))})
    )
    text = add_graphic_clip(project, "text", timeline_start=0.0, duration=frames / FPS)
    ops.add_link(project, text.id, "v", [tracker.id], timeline_time=0.0)
    started = time.perf_counter()
    plan = build_render_plan(project)
    cold = (time.perf_counter() - started) * 1000
    started = time.perf_counter()
    build_render_plan(project)
    warm = (time.perf_counter() - started) * 1000
    layer = next(item for item in plan.graphics_layers if item.clip_id == text.id)
    from core.project_io import project_payload

    tracking_json = json.dumps(project_payload(project)["project"]["sequences"][0]["tracks"][0]["clips"][0]["tracking"])
    return {
        "track_frames": frames,
        "plan_cold_ms": round(cold, 1),
        "plan_cached_ms": round(warm, 3),
        "derived_keyframes": len(layer.transform_keyframes),
        "kut_bytes_per_frame": round(len(tracking_json) / frames, 2),
    }


def _real_case(path: Path, seconds: float = 2.0) -> dict:
    """Vrai plan : points saillants automatiques, aller-retour, stabilisation, liaison."""
    from core import tracking_ops as ops
    from core.graphics import add_graphic_clip
    from core.media_probe import probe_media
    from core.render_plan import build_render_plan
    from core.tracking_engine import run_tracking
    from core.tracking_frames import FrameReader, analysis_geometry
    from core.tracking_match import suggest_features
    from core.tracking_model import BorderMode, Smoothing, StabilizationMode

    info = probe_media(str(path))
    width, height = int(info.width), int(info.height)
    rate = float(info.fps)
    frames = int(seconds * rate)
    project = _project(path, (width, height), frames, rate)
    geometry = analysis_geometry(width, height)
    frame = FrameReader(str(path), geometry, rate).read(0)
    points = [geometry.to_media(x, y) for x, y in suggest_features(frame, 2)]
    ids = [ops.add_tracker(project, "v", timeline_time=0.0, x=x, y=y).id for x, y in points]
    started = time.perf_counter()
    forward = run_tracking(ops.analysis_request(project, "v", ids, timeline_time=0.0, direction=1))
    forward_seconds = time.perf_counter() - started
    ops.apply_tracking_result(project, forward)
    clip = ops.find_clip_and_track(project, "v")[0]
    # Aller-retour : on repart de la dernière position suivie, à rebours, sur un tracker neuf.
    fb_errors = []
    for tracker_id in ids:
        data = clip.tracking.tracker(tracker_id).data
        last = data.valid_range()[1]
        end = data.sample(last)
        twin = ops.add_tracker(project, "v", timeline_time=last / rate, x=end.x, y=end.y)
        backward = run_tracking(ops.analysis_request(project, "v", [twin.id], timeline_time=last / rate, direction=-1))
        samples = backward.outcomes[twin.id].samples
        if 0 in samples:
            start = data.sample(0)
            fb_errors.append(math.dist((samples[0].x, samples[0].y), (start.x, start.y)))
        ops.remove_tracker(project, "v", twin.id)
    confidences = [
        clip.tracking.tracker(t).data.sample(i).confidence
        for t in ids for i in clip.tracking.tracker(t).data.valid_indices()
    ]
    ops.set_stabilization(project, "v", tracker_ids=tuple(ids), mode=StabilizationMode.POSITION_ROTATION,
                          smoothing=Smoothing.MEDIUM, borders=BorderMode.ZOOM)
    report = ops.stabilization_report(project, "v")
    text = add_graphic_clip(project, "text", timeline_start=0.0, duration=frames / rate)
    ops.add_link(project, text.id, "v", ids[:1], timeline_time=0.0)
    started = time.perf_counter()
    build_render_plan(project)
    plan_ms = (time.perf_counter() - started) * 1000
    return {
        "media": f"{width}x{height} @ {rate:g} i/s",
        "frames": frames,
        "trackers": len(ids),
        "analysis_fps": round(forward.frames_analyzed / forward_seconds, 1),
        "reasons": sorted({o.reason for o in forward.outcomes.values()}),
        "mean_confidence": round(sum(confidences) / max(1, len(confidences)), 3),
        "forward_backward_error_px": [round(e, 2) for e in fb_errors],
        "stabilization_zoom": round(report["zoom"], 4),
        "plan_with_link_ms": round(plan_ms, 1),
        "shaky": _shaky_case(path),
    }


def _shaky_case(path: Path) -> dict:
    """Le vrai plan secoué artificiellement (recadrage mobile), puis stabilisé.

    Mesure la gigue des points suivis (écart quadratique à leur trajectoire
    lissée sur ±3 images) avant et après correction.
    """
    from core import tracking_ops as ops
    from core.mograph_scene import mat_apply
    from core.tracking_bindings import TrackingContext
    from core.tracking_engine import run_tracking
    from core.tracking_frames import FrameReader, analysis_geometry
    from core.tracking_match import suggest_features
    from core.tracking_model import BorderMode, Smoothing, StabilizationMode
    from core.tracking_motion import gaussian_smooth, source_time

    shaky = Path(tempfile.mkdtemp()) / "shaky.mp4"
    shake = (
        "crop=1600:900:x='160+60*sin(t*13)+25*sin(t*31)':y='90+40*sin(t*17)+20*cos(t*29)'"
    )
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-ss", "5", "-t", "3", "-i", str(path),
         "-vf", f"fps=30,scale=1920:1080,{shake}", "-an", "-c:v", "libx264", "-preset", "veryfast",
         "-crf", "18", "-pix_fmt", "yuv420p", str(shaky)],
        check=True,
    )
    frames = 90
    project = _project(shaky, (1600, 900), frames, 30.0)
    geometry = analysis_geometry(1600, 900)
    frame = FrameReader(str(shaky), geometry, 30.0).read(0)
    points = [geometry.to_media(x, y) for x, y in suggest_features(frame, 4)]
    ids = [ops.add_tracker(project, "v", timeline_time=0.0, x=x, y=y).id for x, y in points]
    ops.apply_tracking_result(project, run_tracking(ops.analysis_request(project, "v", ids, timeline_time=0.0, direction=1)))
    ops.set_stabilization(project, "v", tracker_ids=tuple(ids), mode=StabilizationMode.POSITION_ROTATION,
                          smoothing=Smoothing.MEDIUM, borders=BorderMode.ZOOM)
    context = TrackingContext(project)
    clip = ops.find_clip_and_track(project, "v")[0]
    result = context.stabilization(clip)

    def jitter(track: list[tuple[float, float]]) -> float:
        xs, ys = [p[0] for p in track], [p[1] for p in track]
        sx, sy = gaussian_smooth(xs, 3.0), gaussian_smooth(ys, 3.0)
        return math.sqrt(sum((a - b) ** 2 + (c - d) ** 2 for a, b, c, d in zip(xs, sx, ys, sy)) / len(xs))

    before, after = [], []
    for tracker_id in ids:
        data = clip.tracking.tracker(tracker_id).data
        raw, corrected = [], []
        for index in data.valid_indices():
            point = context.source_layer_point(clip, data.sample(index).x, data.sample(index).y, data)
            raw.append(point)
            corrected.append(mat_apply(result.correction_at(index), *point))
        before.append(jitter(raw))
        after.append(jitter(corrected))
    del source_time
    return {
        "source": "3 s du plan réel, 1080p, secousse de ±85 px ajoutée",
        "jitter_before_px": round(sum(before) / len(before), 2),
        "jitter_after_px": round(sum(after) / len(after), 2),
        "zoom": round(result.zoom, 3),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path)
    parser.add_argument("--real", type=Path)
    parser.add_argument("--worker")
    parser.add_argument("--quick", action="store_true", help="720p et 1080p seulement")
    args = parser.parse_args(argv)
    if args.worker:
        _analysis_worker(args.worker)
        return 0
    results: dict = {
        "machine": f"{platform.system()} {platform.machine()}",
        "python": platform.python_version(),
        "scenarios": {},
    }
    scenarios = results["scenarios"]
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        names = ["720p", "1080p"] if args.quick else list(RESOLUTIONS)
        for name in names:
            size = RESOLUTIONS[name]
            media = _make_media(root, name, size)
            for precision in ("fast", "auto", "full"):
                key = f"analysis:{name}:{precision}"
                scenarios[key] = _run_analysis(media, size, precision=precision)
                print(key, json.dumps(scenarios[key]), flush=True)
        hd = _make_media(root, "1080p", RESOLUTIONS["1080p"])
        for count in (1, 4, 8, 16):
            key = f"trackers:{count}"
            scenarios[key] = _run_analysis(hd, RESOLUTIONS["1080p"], trackers=count)
            print(key, json.dumps(scenarios[key]), flush=True)
        if not args.quick:
            original = _make_media(root, "4k", RESOLUTIONS["4k"])
            proxy = root / "4k-proxy.mp4"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(original), "-vf", "scale=1920:1080",
                            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", str(proxy)], check=True)
            scenarios["proxy:original"] = _run_analysis(original, RESOLUTIONS["4k"])
            scenarios["proxy:proxy_1080p"] = _run_analysis(original, RESOLUTIONS["4k"], proxy=str(proxy))
            print("proxy", json.dumps([scenarios["proxy:original"], scenarios["proxy:proxy_1080p"]]), flush=True)
        scenarios["cancel"] = _cancel_latency(hd, RESOLUTIONS["1080p"])
        print("cancel", json.dumps(scenarios["cancel"]), flush=True)
    scenarios["bindings"] = _bindings()
    print("bindings", json.dumps(scenarios["bindings"]), flush=True)
    if args.real is not None and args.real.exists():
        os.environ.setdefault("KUT_STUDIO_CACHE_DIR", tempfile.mkdtemp())
        results["real_case"] = _real_case(args.real)
        print("real", json.dumps(results["real_case"]), flush=True)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
