#!/usr/bin/env python3
"""Mémoire résidente de FFmpeg pour un flou animé par ``sendcmd``, **avant** (un seul fichier de commandes) et **après** (fenêtres de
``core.effect_sendcmd.WINDOW_BYTES``) : outil hors ligne, jamais importé par l'application (ADR-0008, correctif de l'étape 5).

FFmpeg 6.1 et 7.1 lisent un fichier de commandes avec une mémoire de l'ordre de 1000 fois sa taille (``docs/ffmpeg-sendcmd.md``,
piège 9). Le cas mesuré : un flou dont la courbe est une rampe linéaire sur toute la durée (une instruction par image, le pire cas),
320x180 à 30 i/s, ``-f null``. Chaque cas tourne dans un processus enfant dédié : ``ru_maxrss`` rend le pic de FFmpeg seul.

    python tools/measure_sendcmd_memory.py generate --work DIR [--minutes 1 5 10]    # écrit DIR/cases.json et les fichiers
    python tools/measure_sendcmd_memory.py run --work DIR [--ffmpeg ffmpeg] [--out DIR/result.json]
    python tools/measure_sendcmd_memory.py report DIR/a.json DIR/b.json

``run`` n'importe que la bibliothèque standard. Dans Docker, monter ``DIR`` **au même chemin** (les chaînes citent les chemins
absolus) : ``docker run --rm --platform linux/amd64 -v "$PWD":/w:ro -v DIR:DIR IMAGE python3 /w/tools/measure_sendcmd_memory.py run
--work DIR``. Ne jamais lancer deux FFmpeg 7.1 gourmands en parallèle (un banc a été tué par le système, code 137).
"""

from __future__ import annotations

import argparse
import json
import re
import resource
import subprocess
import sys
import time
from pathlib import Path

WIDTH, HEIGHT, RATE = 320, 180, 30
_LOADS = re.compile(r"sendcmd=f='[^']*'(?:,sendcmd=f='[^']*')*")


def generate(work: Path, minutes: list[float]) -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from core.animation import InterpolationType, Keyframe
    from core.effect_sendcmd import command_files, context_for
    from core.effects_model import EffectType, create_effect
    from core.export_engine import _build_clip_effect_filters

    work.mkdir(parents=True, exist_ok=True)
    effect = create_effect(EffectType.BLUR, effect_id="e", params={"intensity": 6.0})
    cases = []
    for minute in minutes:
        duration = minute * 60.0
        frames = (Keyframe("fx.e.intensity", 0.0, 1.0, InterpolationType.LINEAR),
                  Keyframe("fx.e.intensity", duration, 20.0, InterpolationType.LINEAR))
        written: list[str] = []
        with command_files(written):
            chain = _build_clip_effect_filters((effect,), 1.0, "v0fx", animation=frames,
                                               sendcmd=context_for(RATE, 0.0, duration))
        parts = []
        for index, path in enumerate(written):
            target = work / f"w{minute:g}-{index}.cmd"
            target.write_bytes(Path(path).read_bytes())
            chain = chain.replace(path, str(target))
            parts.append(target)
            Path(path).unlink(missing_ok=True)
        single = work / f"s{minute:g}.cmd"
        single.write_bytes(b"".join(part.read_bytes() for part in parts))
        before = _LOADS.sub(lambda _m: f"sendcmd=f='{single}'", chain, count=1)
        size = single.stat().st_size
        cases.append({"minutes": minute, "scenario": "avant (un fichier)", "chain": before, "bytes": size, "files": 1,
                      "duration": duration})
        cases.append({"minutes": minute, "scenario": "après (fenêtres)", "chain": chain, "bytes": size, "files": len(parts),
                      "duration": duration})
    (work / "cases.json").write_text(json.dumps(cases, indent=1), encoding="utf-8")
    print(f"{len(cases)} cas écrits dans {work}")
    return 0


def child(ffmpeg: str, duration: str, chain: str) -> int:
    """Lance FFmpeg et rend, en JSON, son pic de mémoire résidente (Ko) et son temps."""
    start = time.perf_counter()
    completed = subprocess.run(
        [ffmpeg, "-v", "error", "-f", "lavfi", "-i", f"testsrc2=s={WIDTH}x{HEIGHT}:r={RATE}:d={duration}",
         "-vf", f"format=yuv420p,{chain},format=yuv420p", "-f", "null", "-"], capture_output=True, text=True)
    peak = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    if sys.platform == "darwin":                     # macOS : octets ; Linux : kilooctets
        peak //= 1024
    print(json.dumps({"rc": completed.returncode, "kb": peak, "seconds": time.perf_counter() - start,
                      "error": completed.stderr[:300]}))
    return 0


def run(work: Path, ffmpeg: str, out: Path) -> int:
    cases = json.loads((work / "cases.json").read_text(encoding="utf-8"))
    banner = subprocess.run([ffmpeg, "-version"], capture_output=True, text=True).stdout.splitlines()[0]
    results = []
    for case in cases:
        completed = subprocess.run([sys.executable, __file__, "child", ffmpeg, str(case["duration"]), case["chain"]],
                                   capture_output=True, text=True)
        try:
            data = json.loads(completed.stdout)
        except ValueError:
            print("échec", case["minutes"], case["scenario"], completed.stderr[:300], file=sys.stderr)
            return 1
        if data["rc"]:
            print("FFmpeg a échoué", case["minutes"], case["scenario"], data["error"], file=sys.stderr)
        results.append({**{k: case[k] for k in ("minutes", "scenario", "bytes", "files")}, **data})
        print(f"{case['minutes']:>4g} min  {case['scenario']:20s} {data['kb'] / 1024:8.0f} Mo  rc={data['rc']}  "
              f"{data['seconds']:6.1f} s", flush=True)
    out.write_text(json.dumps({"ffmpeg": banner, "results": results}, indent=1), encoding="utf-8")
    return 0


def report(files: list[Path]) -> int:
    for path in files:
        data = json.loads(path.read_text(encoding="utf-8"))
        print(f"\n{data['ffmpeg']}")
        for row in data["results"]:
            print(f"  {row['minutes']:>4g} min  {row['scenario']:20s} {row['kb'] / 1024:7.0f} Mo  "
                  f"({row['files']} fichier(s), {row['bytes']} o de commandes, rc={row['rc']})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    gen = sub.add_parser("generate")
    gen.add_argument("--work", type=Path, required=True)
    gen.add_argument("--minutes", type=float, nargs="+", default=[1, 5, 10])
    runner = sub.add_parser("run")
    runner.add_argument("--work", type=Path, required=True)
    runner.add_argument("--ffmpeg", default="ffmpeg")
    runner.add_argument("--out", type=Path)
    rep = sub.add_parser("report")
    rep.add_argument("files", nargs="+", type=Path)
    kid = sub.add_parser("child")
    kid.add_argument("ffmpeg")
    kid.add_argument("duration")
    kid.add_argument("chain")
    args = parser.parse_args()
    if args.command == "generate":
        return generate(args.work, args.minutes)
    if args.command == "run":
        return run(args.work, args.ffmpeg, args.out or args.work / "result.json")
    if args.command == "child":
        return child(args.ffmpeg, args.duration, args.chain)
    return report(args.files)


if __name__ == "__main__":
    sys.exit(main())
