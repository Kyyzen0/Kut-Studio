"""Comparaison manuelle CPU / encodeurs matériels sur une courte séquence synthétique.

Usage::

    python -m tools.perf.encode_bench                       # 5 s en 1280×720, tout ce qui est détecté
    python -m tools.perf.encode_bench --seconds 10 --size 1920x1080 --json out.json
    python -m tools.perf.encode_bench --rescan              # refait la détection d'abord

Pour chaque encodeur utilisable (CPU toujours, puis chaque backend matériel
**validé** par la détection) on encode la même séquence ``testsrc2`` avec les
arguments que produirait un vrai export (:func:`core.video_encoders.resolve_video_encoder`),
puis on rapporte la durée, les images par seconde, la vitesse (× temps réel)
et la taille du fichier.

Ce n'est **pas** un benchmark de comparaison : un encodeur matériel n'a pas la
même qualité, ni le même débit, qu'un encodeur logiciel à « qualité » égale, et
les temps absolus dépendent de la machine. L'outil sert à vérifier que chaque
chemin d'encodage fonctionne et à repérer une régression d'un build à l'autre.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.hardware_cache import default_service  # noqa: E402
from core.hardware_encoding import (  # noqa: E402
    BACKEND_LABELS,
    HardwareCapabilities,
    HardwareEncoder,
)
from core.video_encoders import resolve_video_encoder  # noqa: E402


@dataclass
class EncodeResult:
    backend: str
    encoder: str
    ok: bool
    seconds: float = 0.0
    fps: float = 0.0
    speed: float = 0.0
    size_bytes: int = 0
    error: str = ""


def bench_command(ffmpeg: list[str], choice, output: str, *, seconds: float, size: str, fps: int) -> list[str]:
    """Commande d'encodage de la séquence synthétique avec les arguments d'un vrai export."""
    command = [*ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-nostdin", *choice.pre_input_args,
               "-f", "lavfi", "-i", f"testsrc2=size={size}:rate={fps}:duration={seconds}"]
    if choice.video_filter:
        command += ["-vf", choice.video_filter]
    return [*command, *choice.args, "-an", output]


def run_one(ffmpeg: list[str], backend: HardwareEncoder, capabilities: HardwareCapabilities, *,
            seconds: float, size: str, fps: int, crf: int, workdir: Path) -> EncodeResult:
    width, height = (int(part) for part in size.lower().split("x"))
    try:
        choice = resolve_video_encoder(
            "h264", quality=crf, hardware=backend, capabilities=capabilities,
            width=width, height=height, fps=fps,
        )
    except ValueError as error:
        return EncodeResult(backend.value, "", False, error=str(error))
    output = str(workdir / f"bench-{backend.value}.mp4")
    started = time.perf_counter()
    completed = subprocess.run(
        bench_command(ffmpeg, choice, output, seconds=seconds, size=size, fps=fps),
        capture_output=True, text=True, stdin=subprocess.DEVNULL,
    )
    elapsed = time.perf_counter() - started
    if completed.returncode != 0 or not os.path.isfile(output):
        last = (completed.stderr.strip().splitlines() or [""])[-1]
        return EncodeResult(backend.value, choice.encoder, False, seconds=elapsed, error=last[:160])
    frames = seconds * fps
    return EncodeResult(
        backend.value, choice.encoder, True, seconds=round(elapsed, 3),
        fps=round(frames / elapsed, 1) if elapsed else 0.0,
        speed=round(seconds / elapsed, 2) if elapsed else 0.0,
        size_bytes=os.path.getsize(output),
    )


def run_bench(*, seconds: float = 5.0, size: str = "1280x720", fps: int = 30, crf: int = 20,
              rescan: bool = False) -> dict:
    service = default_service()
    capabilities = service.rescan() if rescan else service.capabilities()
    if not capabilities.ffmpeg_available:
        return {"error": "FFmpeg introuvable", "results": []}
    ffmpeg = [capabilities.ffmpeg_path]
    backends = [HardwareEncoder.CPU, *capabilities.usable_backends("h264")]
    with tempfile.TemporaryDirectory(prefix="kut-encode-bench-") as directory:
        results = [
            run_one(ffmpeg, backend, capabilities, seconds=seconds, size=size, fps=fps, crf=crf,
                    workdir=Path(directory))
            for backend in backends
        ]
    return {
        "ffmpeg": capabilities.ffmpeg_version,
        "platform": capabilities.platform,
        "machine": capabilities.machine,
        "sequence": {"seconds": seconds, "size": size, "fps": fps, "crf": crf},
        "results": [asdict(item) for item in results],
    }


def format_report(report: dict) -> str:
    if report.get("error"):
        return f"Erreur : {report['error']}"
    sequence = report["sequence"]
    lines = [
        f"FFmpeg {report['ffmpeg']} · {report['platform']} ({report['machine']})",
        f"Séquence : {sequence['seconds']} s, {sequence['size']}, {sequence['fps']} i/s, CRF {sequence['crf']}",
        "",
        f"{'Encodeur':<42}{'Durée':>8}{'i/s':>9}{'Vitesse':>9}{'Taille':>12}",
    ]
    for item in report["results"]:
        name = f"{BACKEND_LABELS[HardwareEncoder(item['backend'])]} ({item['encoder']})" if item["encoder"] else item["backend"]
        if item["ok"]:
            lines.append(
                f"{name:<42}{item['seconds']:>7.2f}s{item['fps']:>9.1f}{item['speed']:>8.1f}×"
                f"{item['size_bytes'] / 1024:>9.0f} Ko"
            )
        else:
            lines.append(f"{name:<42}  ÉCHEC : {item['error']}")
    lines.append("")
    lines.append("Vérification de fonctionnement uniquement : qualités et débits ne sont pas équivalents.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--seconds", type=float, default=5.0)
    parser.add_argument("--size", default="1280x720")
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--crf", type=int, default=20, help="CRF x264 (sert aussi d'intention de qualité)")
    parser.add_argument("--rescan", action="store_true", help="refaire la détection des capacités")
    parser.add_argument("--json", metavar="FICHIER", help="écrire aussi le rapport en JSON")
    args = parser.parse_args(argv)
    report = run_bench(seconds=args.seconds, size=args.size, fps=args.fps, crf=args.crf, rescan=args.rescan)
    print(format_report(report))
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2), encoding="utf-8")
    failed = report.get("error") or any(not item["ok"] for item in report["results"])
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
