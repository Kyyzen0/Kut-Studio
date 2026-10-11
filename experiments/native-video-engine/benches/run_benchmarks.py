#!/usr/bin/env python3
"""Reproducible benchmark harness for POC pipelines A/B/C/D."""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"
MEDIA = ROOT / "media"
OUT_DIR = ROOT / "docs" / "bench_results"

sys.path.insert(0, str(BUILD))


def run(cmd: list[str], env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=ROOT, env=env or os.environ.copy(), text=True, capture_output=True)


def generate_media() -> None:
    script = ROOT / "benches" / "generate_media.sh"
    run(["bash", str(script), "6"]).check_returncode()


def native_cli(media: Path, seconds: int, hw: bool, trials: int) -> dict:
    cli = BUILD / "kut_engine_cli"
    if not cli.exists():
        return {"status": "NOT TESTED", "reason": "kut_engine_cli missing; build first"}
    walls = []
    fps = []
    decode_p95 = []
    e2e_p95 = []
    drops = []
    copies = []
    bytes_c = []
    hw_active = None
    last_stdout = ""
    for i in range(trials):
        cmd = [str(cli), str(media), "--seconds", str(seconds)]
        if hw:
            cmd.append("--hw")
        # Warm-up discarded separately by caller; here every trial counts.
        t0 = time.perf_counter()
        proc = run(cmd)
        wall = time.perf_counter() - t0
        last_stdout = proc.stdout
        if proc.returncode != 0:
            return {
                "status": "FAILED",
                "returncode": proc.returncode,
                "stderr": proc.stderr[-500:],
                "stdout": proc.stdout[-500:],
            }
        walls.append(wall)
        # Parse key lines
        for line in proc.stdout.splitlines():
            if line.startswith("frames_decoded="):
                parts = dict(p.split("=", 1) for p in line.split() if "=" in p)
                fps.append(float(parts.get("displayed_fps", "0")))
                drops.append(int(parts.get("queue_drops", "0")))
            if line.startswith("bytes_copied="):
                parts = dict(p.split("=", 1) for p in line.split() if "=" in p)
                bytes_c.append(int(parts.get("bytes_copied", "0")))
                copies.append(int(parts.get("copy_ops", "0")))
            if "decode_latency_us:" in line:
                # decode_latency_us: p50=.. p95=.. p99=..
                bits = {b.split("=")[0]: float(b.split("=")[1]) for b in line.split() if "=" in b}
                decode_p95.append(bits.get("p95", 0.0))
            if "e2e_decode_to_present_us:" in line:
                bits = {b.split("=")[0]: float(b.split("=")[1]) for b in line.split() if "=" in b}
                e2e_p95.append(bits.get("p95", 0.0))
            if line.startswith("media:") and "hw=" in line:
                hw_active = line.split("hw=")[-1].strip()
        if i == 0:
            # keep stdout sample
            pass
    def med(xs):
        return statistics.median(xs) if xs else None

    return {
        "status": "OK",
        "trials": trials,
        "wall_s_median": med(walls),
        "displayed_fps_median": med(fps),
        "queue_drops_median": med(drops),
        "decode_latency_p95_us_median": med(decode_p95),
        "e2e_p95_us_median": med(e2e_p95),
        "copy_ops_median": med(copies),
        "bytes_copied_median": med(bytes_c),
        "hw_reported": hw_active,
        "stdout_sample": last_stdout[-800:],
        "zero_copy": False,
        "metric_class": "directly_measured",
    }


def baseline_a(media: Path, seconds: int) -> dict:
    script = ROOT / "benches" / "baseline_python_decode.py"
    proc = run([sys.executable, str(script), str(media), "--seconds", str(seconds)])
    if proc.returncode != 0:
        return {"status": "FAILED", "stderr": proc.stderr[-500:]}
    return {"status": "OK", "data": json.loads(proc.stdout), "metric_class": "directly_measured"}


def pipeline_d_pyside(media: Path, seconds: int) -> dict:
    """Headless PySide host: open, play, pump via offscreen Qt for a fixed duration."""
    try:
        import kut_engine_native as native
    except ImportError as exc:
        return {"status": "NOT TESTED", "reason": f"module missing: {exc}"}

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    cfg = native.EngineConfig()
    cfg.decode_mode = native.DecodeMode.Software
    eng = native.PreviewEngine(cfg)
    err = eng.open(str(media))
    if err != native.EngineError.Ok:
        return {"status": "FAILED", "error": native.error_name(err)}

    stalls = []
    last = time.perf_counter()
    presented = 0

    def tick():
        nonlocal last, presented
        now = time.perf_counter()
        dt = (now - last) * 1000.0
        last = now
        if dt > 20:
            eng.metrics().note_ui_stall(dt * 1000.0)
            stalls.append(dt)
        if eng.pump_present() == native.EngineError.Ok:
            presented += 1
            _ = eng.last_rgb_bytes()  # force Python bytes copy like the UI host

    eng.play()
    timer = QTimer()
    timer.setInterval(8)
    timer.timeout.connect(tick)
    timer.start()
    QTimer.singleShot(int(seconds * 1000), app.quit)
    t0 = time.perf_counter()
    app.exec()
    wall = time.perf_counter() - t0
    eng.stop()
    m = eng.metrics()
    return {
        "status": "OK",
        "wall_s": wall,
        "frames_presented_native": m.frames_presented,
        "python_present_pumps_ok": presented,
        "displayed_fps_native": m.displayed_fps,
        "queue_drops": m.frames_dropped_queue,
        "decode_p95_us": m.decode_pct().p95,
        "e2e_p95_us": m.e2e_pct().p95,
        "ui_stall_p95_ms_estimated": (sorted(stalls)[int(0.95 * (len(stalls) - 1))] if len(stalls) > 1 else (stalls[0] if stalls else 0)),
        "copy_ops": m.copy_ops,
        "bytes_copied": m.bytes_copied,
        "zero_copy": False,
        "metric_class": "mixed_direct_and_estimated_ui_stall",
        "note": "Offscreen Qt; UI stall from QTimer jitter (estimated).",
    }


def multi_stream_independent(media: Path, seconds: int, streams: int) -> dict:
    """Independent simultaneous decode (NOT timeline compositing)."""
    cli = BUILD / "kut_engine_cli"
    if not cli.exists():
        return {"status": "NOT TESTED", "reason": "cli missing"}
    procs = []
    t0 = time.perf_counter()
    for _ in range(streams):
        procs.append(
            subprocess.Popen(
                [str(cli), str(media), "--seconds", str(seconds)],
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        )
    codes = [p.wait() for p in procs]
    wall = time.perf_counter() - t0
    return {
        "status": "OK" if all(c == 0 for c in codes) else "FAILED",
        "streams": streams,
        "wall_s": wall,
        "returncodes": codes,
        "note": "Independent processes; not timeline compositing.",
        "metric_class": "directly_measured",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=4)
    ap.add_argument("--trials", type=int, default=3)
    ap.add_argument("--quick", action="store_true", help="Only 1080p30")
    args = ap.parse_args()

    generate_media()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    workloads = ["test_1080p30"]
    if not args.quick:
        workloads += ["test_1080p60", "test_4k30", "test_4k60"]

    # Warm-up
    warm = MEDIA / "test_1080p30.mp4"
    if warm.exists() and (BUILD / "kut_engine_cli").exists():
        run([str(BUILD / "kut_engine_cli"), str(warm), "--seconds", "1"])

    report = {
        "host": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "python": sys.version.split()[0],
        },
        "methodology": {
            "warmup": "1s native cli on 1080p30",
            "trials": args.trials,
            "seconds": args.seconds,
            "median_reporting": True,
            "zero_copy_default": False,
        },
        "workloads": {},
    }

    for name in workloads:
        media = MEDIA / f"{name}.mp4"
        if not media.exists():
            report["workloads"][name] = {"status": "NOT TESTED", "reason": "media missing"}
            continue
        entry = {
            "A_baseline_python_ffmpeg": baseline_a(media, args.seconds),
            "B_native_software": native_cli(media, args.seconds, hw=False, trials=args.trials),
            "C_native_hw_prefer": native_cli(media, args.seconds, hw=True, trials=args.trials),
            "D_native_pyside_offscreen": pipeline_d_pyside(media, args.seconds),
        }
        if name == "test_1080p30":
            entry["multi_stream_independent_x2"] = multi_stream_independent(media, args.seconds, 2)
            entry["multi_stream_compositing"] = {
                "status": "NOT TESTED",
                "reason": "Timeline compositing not implemented in POC v0.1",
            }
            entry["seek_stress"] = native_cli(media, args.seconds, hw=False, trials=1)
            # dedicated seek run
            cli = BUILD / "kut_engine_cli"
            if cli.exists():
                proc = run([str(cli), str(media), "--seconds", "2", "--seek-test"])
                entry["seek_stress"] = {
                    "status": "OK" if proc.returncode == 0 else "FAILED",
                    "stdout": proc.stdout[-600:],
                    "metric_class": "directly_measured",
                }
        report["workloads"][name] = entry

    # Documented production reference (not re-measured on this host)
    report["production_reference_apple_m4"] = {
        "status": "NOT RE-MEASURED_ON_THIS_HOST",
        "source": "docs/perf/gpu.json / docs/gpu-preview.md",
        "event_loop_lag_ms_p95_4k_hevc10_hw_gpu_effects": 22.64,
        "note": "This is Qt event-loop lag, not end-to-end frame latency.",
    }

    out = OUT_DIR / "results.json"
    out.write_text(json.dumps(report, indent=2))
    print(json.dumps({"wrote": str(out), "workloads": list(report["workloads"])}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
