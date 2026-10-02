"""Banc du décodage matériel et du moniteur GPU.

Usage (plateforme Qt native : le GPU est requis pour la partie moniteur) ::

    python -m tools.perf.gpu_bench --out docs/perf/gpu.json
    python -m tools.perf.gpu_bench --quick          # médias plus courts

Mesures :

1. **Décodage FFmpeg** CPU contre matériel (1080p H.264, 4K H.264, 4K HEVC,
   4K HEVC 10 bits, proxy 720p, 4 clips 1080p simultanés) : images/s, temps CPU
   par image, mémoire max du processus ;
2. **Scrubbing** : délai jusqu'à la première image après un saut (CPU / matériel) ;
3. **Aperçu fidèle** (segments FFmpeg, graphe de l'export) : effets,
   compositing à deux pistes, flou de mouvement — décodage CPU contre matériel ;
4. **Moniteur temps réel**, dans un sous-processus par combinaison (le décodage
   de Qt se règle au démarrage) : CPU decode + CPU preview, HW decode + CPU
   preview, HW decode + GPU preview (avec et sans effets) — images reçues et
   perdues, temps CPU du processus, retard de la boucle d'événements, coût de
   rendu GPU ;
5. **Cache GPU** : rendu avec une matte de masque fixe, sans puis avec cache ;
6. **VRAM** : octets de textures suivis par Kut-Studio (la VRAM système n'est pas
   lisible de façon portable ; mémoire unifiée sur Apple Silicon).

Les temps absolus dépendent de la machine : comparer des colonnes, pas des machines.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import resource
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MEDIA = {
    "1080p_h264": ("1920x1080", "libx264", "yuv420p", "mp4"),
    "4k_h264": ("3840x2160", "libx264", "yuv420p", "mp4"),
    "4k_hevc": ("3840x2160", "libx265", "yuv420p", "mp4"),
    "4k_hevc10": ("3840x2160", "libx265", "yuv420p10le", "mp4"),
    "proxy_720p": ("1280x720", "libx264", "yuv420p", "mp4"),
}


def make_media(directory: Path, seconds: float) -> dict[str, Path]:
    """Médias de test : ``testsrc2`` + bruit (contenu plus réaliste qu'une mire seule)."""
    paths = {}
    for name, (size, encoder, pix, ext) in MEDIA.items():
        path = directory / f"{name}.{ext}"
        if not path.exists():
            extra = ["-x265-params", "log-level=error", "-tag:v", "hvc1"] if encoder == "libx265" else []
            gop = ["-g", "15"] if name == "proxy_720p" else ["-g", "120"]
            subprocess.run([
                "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=30",
                "-t", str(seconds), "-vf", "noise=alls=12:allf=t", "-c:v", encoder, "-preset", "fast",
                "-crf", "20", "-pix_fmt", pix, *gop, *extra, str(path),
            ], check=True)
        paths[name] = path
    return paths


def _rusage_run(command: list[str]) -> tuple[int, float, float | None, int | None]:
    """``(code, mur, CPU, mémoire max)`` d'un processus (``wait4`` : ce processus seul)."""
    started = time.perf_counter()
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if hasattr(os, "wait4"):
        _pid, status, usage = os.wait4(process.pid, 0)
        process.returncode = os.waitstatus_to_exitcode(status)
        rss = usage.ru_maxrss if sys.platform == "darwin" else usage.ru_maxrss * 1024
        return process.returncode, time.perf_counter() - started, usage.ru_utime + usage.ru_stime, rss
    process.wait()
    return process.returncode, time.perf_counter() - started, None, None


def bench_decode(media: dict[str, Path], backend_args: list[str], frames: int) -> dict:
    out = {}
    for name, path in media.items():
        best = None
        for _ in range(2):
            code, wall, cpu, rss = _rusage_run(["ffmpeg", "-v", "error", "-nostdin", *backend_args, "-i", str(path),
                                                "-frames:v", str(frames), "-f", "null", "-"])
            if code != 0:
                break
            if best is None or wall < best[0]:
                best = (wall, cpu, rss)
        if best is None:
            out[name] = {"error": "échec"}
            continue
        wall, cpu, rss = best
        out[name] = {"fps": round(frames / wall, 1), "cpu_ms_per_frame": round(cpu * 1000 / frames, 2) if cpu else None,
                     "max_rss_mb": round(rss / 2**20, 1) if rss else None}
    # Plusieurs clips : quatre décodages 1080p simultanés.
    path = media["1080p_h264"]
    started = time.perf_counter()
    processes = [subprocess.Popen(["ffmpeg", "-v", "error", "-nostdin", *backend_args, "-i", str(path),
                                   "-frames:v", str(frames), "-f", "null", "-"]) for _ in range(4)]
    for process in processes:
        process.wait()
    wall = time.perf_counter() - started
    out["4x_1080p_h264"] = {"fps_total": round(4 * frames / wall, 1),
                            "ok": all(p.returncode == 0 for p in processes)}
    return out


def bench_scrub(path: Path, backend_args: list[str], duration: float) -> dict:
    samples = []
    for i in range(8):
        position = (i * 0.37 % 1.0) * max(0.1, duration - 0.5)
        started = time.perf_counter()
        subprocess.run(["ffmpeg", "-v", "error", "-nostdin", *backend_args, "-ss", f"{position:.3f}", "-i", str(path),
                        "-frames:v", "1", "-f", "null", "-"], check=False)
        samples.append((time.perf_counter() - started) * 1000)
    return {"median_ms": round(statistics.median(samples), 1), "max_ms": round(max(samples), 1)}


def bench_segments(media: dict[str, Path], mode: str) -> dict:
    """Segments d'aperçu fidèles (graphe de l'export) avec décodage ``mode``."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    from core.decode_policy import DecodeContext, set_default_context
    from core.effects_model import ClipEffect, EffectType
    from core.graphics import GraphicOverlay, GraphicType
    from core.hardware_cache import default_service
    from core.hardware_decoding import DecodeMode
    from core.motion_blur import MotionBlurSettings
    from core.preview_engine import PreviewEngine, PreviewJob
    from core.project_model import Clip, MediaAsset, Project, Track
    from core.render_plan import build_render_plan
    from core.visual_effects import ClipTransform, TransformKeyframe

    set_default_context(DecodeContext(mode=DecodeMode(mode), capabilities_provider=lambda: default_service().capabilities()))

    def project(kind: str):
        source = media["4k_hevc10"]
        p = Project(name=kind, width=1920, height=1080, fps=30)
        p.media_assets.append(MediaAsset("a", str(source), "a", 4.0, 3840, 2160, 30, "video"))
        p.media_assets.append(MediaAsset("b", str(media["4k_h264"]), "b", 4.0, 3840, 2160, 30, "video"))
        clip = Clip("v", "a", "V1", 0.0, 0.0, 2.0)
        if kind == "effects":
            clip.effects = [ClipEffect("c", EffectType.COLOR_CORRECTION, True, {"contrast": 1.2}),
                            ClipEffect("b", EffectType.BLUR, True, {"intensity": 3.0}),
                            ClipEffect("v", EffectType.VIGNETTE, True, {"intensity": 0.5})]
        tracks = [Track("V1", "V1", "video", clips=[clip])]
        if kind == "compositing":
            top = Clip("w", "b", "V2", 0.0, 0.0, 2.0)
            top.transform = ClipTransform(scale=0.5, rotation=10.0, opacity=0.7)
            tracks.insert(0, Track("V2", "V2", "video", clips=[top]))
        if kind == "motion_blur":
            graphic = GraphicOverlay(type=GraphicType.SHAPE, motion_blur=True)
            shape = Clip("g", "", "G1", 0.0, 0.0, 2.0)
            shape.graphic = graphic
            shape.transform_keyframes = [TransformKeyframe("position_x", 0.0, -0.4), TransformKeyframe("position_x", 2.0, 0.4)]
            tracks.insert(0, Track("G1", "G1", "graphics", clips=[shape]))
            p.tracks = tracks + p.tracks
            for owner in (p, getattr(p, "active_sequence", None)):
                if owner is not None and hasattr(owner, "motion_blur"):
                    owner.motion_blur = MotionBlurSettings(enabled=True, samples=8)
            return p
        p.tracks = tracks + p.tracks
        return p

    engine = PreviewEngine(cache=None)
    out = {}
    for kind in ("effects", "compositing", "motion_blur"):
        try:
            plan = build_render_plan(project(kind))
        except Exception as error:  # le banc ne doit pas s'arrêter sur un cas
            out[kind] = {"error": str(error)}
            continue
        samples = []
        for _ in range(2):
            job = PreviewJob(key=None, plan=plan, width=1920, height=1080, fps=30, quality="standard",
                             start=0.0, duration=2.0)
            started = time.perf_counter()
            path = engine._default_render(job, None)
            samples.append(time.perf_counter() - started)
            if path:
                os.remove(path)
        out[kind] = {"seconds_per_2s_segment": round(min(samples), 2)}
    set_default_context(None)
    return out


MONITOR_SCRIPT = r'''
import json, os, resource, sys, time
sys.path.insert(0, sys.argv[1])
from PySide6.QtCore import QTimer, QUrl
from PySide6.QtWidgets import QApplication
app = QApplication([])
from ui.preview_panel import PreviewPanel
path, backend, effects, seconds = sys.argv[2], sys.argv[3], sys.argv[4] == "1", float(sys.argv[5])
panel = PreviewPanel(lambda: None, lambda: None, lambda d: None, lambda: None, lambda: None)
panel.resize(1100, 700); panel.show(); panel.set_canvas_size(1920, 1080)
panel.audio_output.setVolume(0.0)
received = [0]
if backend == "gpu":
    assert panel.enable_gpu("metal" if sys.platform == "darwin" else ("d3d11" if sys.platform == "win32" else "opengl"))
else:
    panel.video_item.videoSink().videoFrameChanged.connect(lambda f: received.__setitem__(0, received[0] + 1))
if effects:
    from core.effects_model import ClipEffect, EffectType
    panel.set_effects([ClipEffect("c", EffectType.COLOR_CORRECTION, True, {"contrast": 1.2, "saturation": 1.3}),
                       ClipEffect("b", EffectType.BLUR, True, {"intensity": 4.0}),
                       ClipEffect("v", EffectType.VIGNETTE, True, {"intensity": 0.5})])
lags, last = [], [None]
def probe():
    now = time.perf_counter()
    if last[0] is not None:
        lags.append((now - last[0]) * 1000 - 5.0)
    last[0] = now
timer = QTimer(); timer.setInterval(5); timer.timeout.connect(probe)
state = {}
def start():
    panel.load_video(path)
    panel._timeline_preview_path = path
    state["cpu0"] = resource.getrusage(resource.RUSAGE_SELF); state["t0"] = time.perf_counter()
    timer.start()
def stop():
    r = resource.getrusage(resource.RUSAGE_SELF); wall = time.perf_counter() - state["t0"]
    out = {"wall": wall, "process_cpu_percent": round(100 * ((r.ru_utime + r.ru_stime) - (state["cpu0"].ru_utime + state["cpu0"].ru_stime)) / wall, 1),
           "event_loop_lag_ms_p95": round(sorted(lags)[int(0.95 * (len(lags) - 1))], 2) if lags else None,
           "max_rss_mb": round(r.ru_maxrss / (2**20 if sys.platform == "darwin" else 1024), 1)}
    view = panel.gpu_view
    if view is not None:
        s = view.stats
        out.update(frames_received=s.received, frames_presented=s.presented, frames_dropped=s.dropped,
                   gpu_render_ms_avg=round(s.average_render_ms() or 0, 2), gpu_render_ms_p95=round(s.p95_render_ms() or 0, 2),
                   texture_mb=round((view.executor.texture_bytes if view.executor else 0) / 2**20, 1), device=view.device_label())
    else:
        out.update(frames_received=received[0])
    out["fps"] = round(out.get("frames_presented", out["frames_received"]) / wall, 1)
    print(json.dumps(out)); app.quit()
QTimer.singleShot(800, start)
QTimer.singleShot(int(800 + seconds * 1000), stop)
app.exec()
'''


def bench_monitor(path: Path, seconds: float) -> dict:
    combos = {
        "cpu_decode+cpu_preview": ("none", "cpu", "0"),
        "hw_decode+cpu_preview": (None, "cpu", "0"),
        "cpu_decode+gpu_preview": ("none", "gpu", "0"),
        "hw_decode+gpu_preview": (None, "gpu", "0"),
        "hw_decode+gpu_preview+effects": (None, "gpu", "1"),
    }
    out = {}
    for name, (qt_devices, backend, effects) in combos.items():
        env = {k: v for k, v in os.environ.items() if k not in ("QT_QPA_PLATFORM", "QT_FFMPEG_DECODING_HW_DEVICE_TYPES")}
        if qt_devices is not None:
            env["QT_FFMPEG_DECODING_HW_DEVICE_TYPES"] = qt_devices
        completed = subprocess.run([sys.executable, "-c", MONITOR_SCRIPT, str(ROOT), str(path), backend, effects,
                                    str(seconds)], env=env, capture_output=True, text=True, timeout=120)
        lines = [line for line in completed.stdout.splitlines() if line.startswith("{")]
        out[name] = json.loads(lines[-1]) if lines else {"error": completed.stderr[-300:]}
    return out


CACHE_SCRIPT = r'''
import json, sys, time
sys.path.insert(0, sys.argv[1])
from PySide6.QtCore import QSize
from PySide6.QtGui import QImage, QColor, QPainter
from PySide6.QtWidgets import QApplication
app = QApplication([])
from core.gpu_composite import CompositeFrame, CompositeLayer
from tests.gpu_harness import make_frame, test_pattern
from ui.gpu_preview import GpuPreviewWidget
W, H = 1920, 1080
codes = test_pattern(W, H)
matte = QImage(W, H, QImage.Format.Format_ARGB32_Premultiplied); matte.fill(QColor(0, 0, 0, 0))
p = QPainter(matte); p.setBrush(QColor(255, 255, 255)); p.drawEllipse(200, 100, 1500, 880); p.end()
results = {}
for mode in ("no_cache", "cache"):
    widget = GpuPreviewWidget(api=sys.argv[2])
    widget.setFixedColorBufferSize(QSize(W, H))
    dpr = widget.devicePixelRatioF() or 1.0
    widget.set_canvas_rect((0, 0, W / dpr, H / dpr), (0, 0, 0))
    frame = make_frame(codes, "nv12")
    layer = CompositeLayer("v", (1, 0, 0, 1, 0, 0), (0, 0, W, H), matte="m")
    times = []
    for i in range(12):
        widget.set_video_frame("v", frame)
        key = f"m{i}" if mode == "no_cache" else "m"
        from dataclasses import replace
        widget.set_composite(CompositeFrame(W, H, 1.0, (replace(layer, matte=key),)), {key: matte})
        started = time.perf_counter(); widget.grabFramebuffer(); times.append((time.perf_counter() - started) * 1000)
    stats = widget.cache.stats()
    results[mode] = {"ms_per_frame_median": round(sorted(times[2:])[len(times[2:]) // 2], 2),
                     "cache_entries": stats.entries, "cache_mb": round(stats.bytes / 2**20, 1)}
    widget.release_gpu()
print(json.dumps(results))
'''


def bench_gpu_cache() -> dict:
    env = {k: v for k, v in os.environ.items() if k != "QT_QPA_PLATFORM"}
    api = "metal" if sys.platform == "darwin" else ("d3d11" if sys.platform == "win32" else "opengl")
    completed = subprocess.run([sys.executable, "-c", CACHE_SCRIPT, str(ROOT), api], env=env, capture_output=True,
                               text=True, timeout=120)
    lines = [line for line in completed.stdout.splitlines() if line.startswith("{")]
    return json.loads(lines[-1]) if lines else {"error": completed.stderr[-300:]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default="")
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--media-dir", default="")
    args = parser.parse_args(argv)
    seconds = 3.0 if args.quick else 6.0
    directory = Path(args.media_dir or tempfile.mkdtemp(prefix="kut-gpu-bench-"))
    directory.mkdir(parents=True, exist_ok=True)
    media = make_media(directory, seconds)

    from core.hardware_cache import CapabilityService

    capabilities = CapabilityService(cache_path=directory / "caps.json", environment={}).capabilities(refresh=True)
    backends = capabilities.decode_backends()
    hw_args = ["-hwaccel", backends[0].value] if backends else []
    frames = int(seconds * 30) - 5
    report = {
        "machine": f"{platform.platform()} · {platform.machine()} · {os.cpu_count()} cœurs",
        "ffmpeg": capabilities.ffmpeg_version,
        "decode_backend": backends[0].value if backends else "aucun",
        "decode": {"cpu": bench_decode(media, [], frames)},
        "scrub": {"cpu": bench_scrub(media["4k_hevc10"], [], seconds)},
    }
    if hw_args:
        report["decode"]["hardware"] = bench_decode(media, hw_args, frames)
        report["scrub"]["hardware"] = bench_scrub(media["4k_hevc10"], hw_args, seconds)
    report["segments"] = {"cpu": bench_segments(media, "cpu")}
    if hw_args:
        report["segments"]["hardware"] = bench_segments(media, "auto")
    report["monitor_4k_hevc10"] = bench_monitor(media["4k_hevc10"], min(4.0, seconds - 1))
    report["monitor_1080p_h264"] = bench_monitor(media["1080p_h264"], min(4.0, seconds - 1))
    report["gpu_cache"] = bench_gpu_cache()
    report["peak_rss_mb_bench"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (2**20 if sys.platform == "darwin" else 1024), 1)
    text = json.dumps(report, indent=1, ensure_ascii=False)
    print(text)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
