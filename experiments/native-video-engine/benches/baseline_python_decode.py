#!/usr/bin/env python3
"""Baseline A (host-local): Python-driven FFmpeg CLI decode to raw frames.

This is NOT the full Kut-Studio GPU monitor. It approximates the cost of an
external-process decode + CPU copy sink for fair comparison with the native
engine on the same machine. Documented Apple M4 GPU-monitor numbers remain
in docs/perf/gpu.json and are cited separately as NOT RE-MEASURED here.
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import subprocess
import sys
import time
from pathlib import Path


def rss_mb() -> float:
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return usage / (1024 * 1024) if sys.platform == "darwin" else usage / 1024


def run_ffmpeg_null(path: Path, seconds: float, hw: bool) -> dict:
    """Decode with ffmpeg to null muxer; measure process wall/CPU via wait4."""
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-y"]
    if hw:
        # Best-effort; may silently fall back depending on build/device.
        cmd += ["-hwaccel", "auto"]
    cmd += ["-i", str(path), "-t", str(seconds), "-an", "-f", "null", "-"]
    start = time.perf_counter()
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if hasattr(os, "wait4"):
        _pid, status, usage = os.wait4(proc.pid, 0)
        proc.returncode = os.waitstatus_to_exitcode(status)
        cpu = usage.ru_utime + usage.ru_stime
        child_rss = usage.ru_maxrss if sys.platform == "darwin" else usage.ru_maxrss * 1024
        child_rss_mb = child_rss / (1024 * 1024)
    else:
        _, stderr = proc.communicate()
        cpu = None
        child_rss_mb = None
        status = proc.returncode
    wall = time.perf_counter() - start
    stderr = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
    return {
        "pipeline": "A_ffmpeg_cli_null",
        "hw_requested": hw,
        "wall_s": wall,
        "cpu_s": cpu,
        "child_rss_mb": child_rss_mb,
        "returncode": proc.returncode,
        "stderr_tail": stderr[-400:],
        "note": "Decode-only; no UI present path. Estimated display FPS = n/a.",
    }


def run_python_rgb_copy(path: Path, seconds: float) -> dict:
    """Pipe ffmpeg rawvideo rgb24 into Python and copy each frame (copy-bound sink)."""
    # Probe size/fps with ffprobe
    probe = subprocess.check_output(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height,r_frame_rate",
            "-of", "csv=p=0", str(path),
        ],
        text=True,
    ).strip().split(",")
    width, height = int(probe[0]), int(probe[1])
    num, den = probe[2].split("/")
    fps = float(num) / float(den)
    frame_bytes = width * height * 3
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path),
        "-t", str(seconds), "-an", "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    ]
    start = time.perf_counter()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    assert proc.stdout is not None
    frames = 0
    copies = 0
    bytes_copied = 0
    latencies = []
    while True:
        t0 = time.perf_counter()
        buf = proc.stdout.read(frame_bytes)
        if not buf or len(buf) < frame_bytes:
            break
        # Vraie copie côté Python, comme un envoi au GPU. bytes(buf) ne copierait rien : buf est déjà un bytes immuable.
        copied = bytearray(buf)
        bytes_copied += len(copied)
        copies += 1
        frames += 1
        latencies.append((time.perf_counter() - t0) * 1e6)
    proc.wait()
    wall = time.perf_counter() - start

    def pct(q: float) -> float:
        if not latencies:
            return 0.0
        s = sorted(latencies)
        return s[min(len(s) - 1, int(q * (len(s) - 1)))]

    return {
        "pipeline": "A_ffmpeg_pipe_python_rgb_copy",
        "wall_s": wall,
        "frames": frames,
        "displayed_fps": frames / wall if wall else 0,
        "copy_ops": copies,
        "bytes_copied": bytes_copied,
        "frame_read_copy_us": {"p50": pct(0.5), "p95": pct(0.95), "p99": pct(0.99)},
        "rss_mb": rss_mb(),
        "width": width,
        "height": height,
        "source_fps": fps,
        "note": "Measured: pipe read + bytes() copy. Not Kut-Studio QRhi path.",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("media", type=Path)
    ap.add_argument("--seconds", type=float, default=4.0)
    ap.add_argument("--hw", action="store_true")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    results = {
        "cli_null": run_ffmpeg_null(args.media, args.seconds, args.hw),
        "pipe_rgb_copy": run_python_rgb_copy(args.media, args.seconds),
    }
    text = json.dumps(results, indent=2)
    print(text)
    if args.out:
        args.out.write_text(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
