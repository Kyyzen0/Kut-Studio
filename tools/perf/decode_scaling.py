"""Décodage simultané, CPU contre matériel : débit agrégé (ADR-0003, B2).

Usage ::

    python -m tools.perf.decode_scaling --out docs/perf/decode-scaling.json
    python -m tools.perf.decode_scaling --hwaccel videotoolbox --hw-output-format videotoolbox_vld
    python -m tools.perf.decode_scaling --media ~/Movies/clip.mov --streams 1 2 4 --hwaccel videotoolbox

Pour N flux simultanés (1, 2, 4, 6 par défaut), chaque flux décode le même média jusqu'au bout vers ``null``.
Le banc rapporte le débit agrégé (N × images / temps mural), le débit par flux et le temps CPU consommé par image.
Le temps inclut le lancement de FFmpeg, non isolé : les chiffres sont donc prudents, surtout pour les clips courts.

Sans ``--media``, un clip 4K HEVC 10 bits est généré dans le dossier de travail, avec les réglages de
``tools/perf/gpu_bench.py`` (même contenu, même encodeur) pour que les deux bancs se comparent. Le FFmpeg utilisé
est celui de l'application (``core/tool_paths.py``, variable ``KUT_STUDIO_FFMPEG``).

Ce que le banc ne fait pas : il ne mesure ni le moniteur ni l'image affichée. Il dit combien de flux un décodeur
soutient avant de saturer le processeur ou le matériel. Les noms de ``-hwaccel`` dépendent de la plateforme
(``videotoolbox``, ``d3d11va``, ``cuda``, ``vaapi``, ``qsv``) ; un nom indisponible fait échouer le banc au lieu de
masquer le repli CPU.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import resource
import statistics
import subprocess
import tempfile
import time
from pathlib import Path

from core.tool_paths import find_media_tool

FULL_SECONDS = 6.0
QUICK_SECONDS = 2.0
DEFAULT_STREAMS = (1, 2, 4, 6)


def decode_command(ffmpeg: str, media: str | Path, hwaccel: str | None = None,
                   hw_output_format: str | None = None) -> list[str]:
    """Commande de décodage complet vers ``null`` ; les options matérielles n'apparaissent que si demandées."""
    if hw_output_format and not hwaccel:
        raise ValueError("--hw-output-format demande un --hwaccel")
    command = [ffmpeg, "-v", "error", "-nostdin"]
    if hwaccel:
        command += ["-hwaccel", hwaccel]
    if hw_output_format:
        command += ["-hwaccel_output_format", hw_output_format]
    return [*command, "-i", str(media), "-map", "0:v:0", "-f", "null", "-"]


def throughput(frames: int, streams: int, wall_seconds: float) -> dict:
    """Débits agrégé et par flux, pour ``streams`` décodages de ``frames`` images chacun."""
    return {
        "aggregate_fps": round(streams * frames / wall_seconds, 1),
        "per_stream_fps": round(frames / wall_seconds, 1),
    }


def frame_count(media: Path) -> int:
    """Nombre d'images, lu par ``ffprobe`` sans décoder (pour ces codecs, un paquet vaut une image)."""
    ffprobe = find_media_tool("ffprobe")
    if ffprobe is None:
        raise RuntimeError("ffprobe introuvable (installer FFmpeg ou définir KUT_STUDIO_FFPROBE)")
    completed = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "v:0", "-count_packets",
         "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", str(media)],
        capture_output=True, text=True, check=True)
    return int(completed.stdout.strip())


def generate_clip(directory: Path, seconds: float) -> Path:
    """Clip 4K HEVC 10 bits, réglages de ``gpu_bench`` ; écrit d'abord sous un autre nom, puis renommé."""
    path = directory / f"4k_hevc10_{seconds:g}s.mp4"
    if path.exists():
        return path
    ffmpeg = find_media_tool("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("ffmpeg introuvable (installer FFmpeg ou définir KUT_STUDIO_FFMPEG)")
    partial = path.with_name(path.stem + ".partial.mp4")
    subprocess.run([
        ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=3840x2160:rate=30",
        "-t", f"{seconds:g}", "-vf", "noise=alls=12:allf=t", "-c:v", "libx265", "-preset", "fast",
        "-crf", "20", "-pix_fmt", "yuv420p10le", "-g", "120", "-x265-params", "log-level=error",
        "-tag:v", "hvc1", str(partial),
    ], check=True)
    partial.replace(path)
    return path


def run_parallel(command: list[str], streams: int) -> tuple[float, float]:
    """Lance ``streams`` décodages en même temps ; renvoie (temps mural, temps CPU des enfants) en secondes."""
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    started = time.perf_counter()
    processes = [subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                 for _ in range(streams)]
    failures = []
    for process in processes:
        _, stderr = process.communicate()
        if process.returncode != 0:
            failures.append(stderr.decode(errors="replace").strip()[-300:])
    wall = time.perf_counter() - started
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    if failures:
        raise RuntimeError(f"décodage en échec : {failures[0]}")
    cpu = (after.ru_utime + after.ru_stime) - (before.ru_utime + before.ru_stime)
    return wall, cpu


def measure(command: list[str], frames: int, streams: int, repeat: int) -> dict:
    """Une répétition non mesurée (cache de fichier), puis la médiane de ``repeat`` mesures."""
    run_parallel(command, streams)
    samples = [run_parallel(command, streams) for _ in range(repeat)]
    wall = statistics.median(wall for wall, _ in samples)
    cpu = statistics.median(cpu for _, cpu in samples)
    total = streams * frames
    return {
        "streams": streams,
        "wall_s": round(wall, 3),
        **throughput(frames, streams, wall),
        "cpu_ms_per_frame": round(cpu / total * 1000, 2),
    }


def _ffmpeg_version(ffmpeg: str) -> str:
    completed = subprocess.run([ffmpeg, "-version"], capture_output=True, text=True, check=True)
    return completed.stdout.splitlines()[0]


def run(media: Path, streams: tuple[int, ...], repeat: int, hwaccel: str | None,
        hw_output_format: str | None) -> dict:
    ffmpeg = find_media_tool("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("ffmpeg introuvable (installer FFmpeg ou définir KUT_STUDIO_FFMPEG)")
    frames = frame_count(media)
    configs = {"cpu": decode_command(ffmpeg, media)}
    if hwaccel:
        configs[f"{hwaccel}_download"] = decode_command(ffmpeg, media, hwaccel)
    if hwaccel and hw_output_format:
        configs[f"{hwaccel}_{hw_output_format}"] = decode_command(ffmpeg, media, hwaccel, hw_output_format)
    report: dict = {
        "meta": {"platform": platform.platform(), "machine": platform.machine(), "cpus": os.cpu_count(),
                 "ffmpeg": _ffmpeg_version(ffmpeg), "media": str(media), "frames": frames, "repeat": repeat},
        "configs": {},
    }
    for name, command in configs.items():
        rows = []
        for count in streams:
            row = measure(command, frames, count, repeat)
            rows.append(row)
            print(f"{name:<28} N={count}  {row['aggregate_fps']:>7.1f} fps au total "
                  f"({row['per_stream_fps']:.1f} par flux, {row['cpu_ms_per_frame']:.1f} ms CPU/image)", flush=True)
        report["configs"][name] = rows
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Débit de décodage simultané, CPU et matériel (ADR-0003, B2).")
    parser.add_argument("--media", type=Path, help="média à décoder (sinon, un clip 4K HEVC 10 bits est généré)")
    parser.add_argument("--streams", type=int, nargs="+", default=list(DEFAULT_STREAMS))
    parser.add_argument("--hwaccel", help="backend matériel FFmpeg, par exemple videotoolbox")
    parser.add_argument("--hw-output-format", help="format gardé sur le GPU, par exemple videotoolbox_vld")
    parser.add_argument("--workdir", type=Path, default=Path(tempfile.gettempdir()) / "kut-decode-scaling")
    parser.add_argument("--repeat", type=int, default=3, help="répétitions mesurées (médiane retenue)")
    parser.add_argument("--quick", action="store_true", help="clip plus court, 1 et 2 flux, une répétition")
    parser.add_argument("--out", type=Path, help="fichier JSON de sortie (sinon, rien n'est écrit)")
    args = parser.parse_args(argv)
    if args.hw_output_format and not args.hwaccel:
        parser.error("--hw-output-format demande un --hwaccel")
    args.workdir.mkdir(parents=True, exist_ok=True)
    media = args.media or generate_clip(args.workdir, QUICK_SECONDS if args.quick else FULL_SECONDS)
    streams = (1, 2) if args.quick else tuple(args.streams)
    report = run(media, streams, 1 if args.quick else args.repeat, args.hwaccel, args.hw_output_format)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"écrit : {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
