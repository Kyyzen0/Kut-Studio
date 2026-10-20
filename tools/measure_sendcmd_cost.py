#!/usr/bin/env python3
"""Coût d'un effet animé par ``sendcmd`` (ADR-0008, étape 5) : outil hors ligne, jamais importé par l'application.

Compare, à 1080p, 150 images (5 s à 30 i/s, ``-f null``), le temps du **flou**, de l'**aberration chromatique** et du **rayon de
la lueur** statiques et animés (2 ou 10 images clés, interpolation linéaire en dents de scie). Les chaînes sont celles de
l'export (``core.export_engine._build_clip_effect_filters``) : le fichier de commandes est celui que l'application écrit.
Critère de l'ADR : un paramètre qui coûte plus de ×3 le statique n'est pas enregistré (règle de l'étape 1 pour ``geq``).

Deux temps, parce que FFmpeg tourne parfois dans Docker sans l'application :

    python tools/measure_sendcmd_cost.py generate --work DIR            # écrit DIR/cases.json et les fichiers de commandes
    python tools/measure_sendcmd_cost.py run --work DIR [--ffmpeg ffmpeg] [--repeat 3] [--out DIR/result.json]
    python tools/measure_sendcmd_cost.py report DIR/a.json DIR/b.json   # tableau

``run`` n'importe que la bibliothèque standard. Dans Docker, monter ``DIR`` **au même chemin** (les chaînes citent les
chemins absolus des fichiers de commandes) :
``docker run --rm --platform linux/amd64 -v "$PWD":/w:ro -v DIR:DIR IMAGE python3 /w/tools/measure_sendcmd_cost.py run --work DIR``.
Les temps d'une image émulée (amd64 sur arm64) comparent animé et statique, pas les versions entre elles.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

WIDTH, HEIGHT, RATE, DURATION = 1920, 1080, 30, 5.0
KEYFRAME_COUNTS = (2, 10)
SIGMA = {"blur": (1.0, 12.0), "glow": (2.0, 30.0), "chromatic": (0.0, 14.0)}
LABELS = {"blur": "flou (gblur)", "glow": "rayon de la lueur (gblur)", "chromatic": "aberration chromatique (rgbashift)"}


def generate(work: Path) -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from core.animation import InterpolationType, Keyframe
    from core.effect_sendcmd import command_files, context_for
    from core.effects_model import EffectType, create_effect
    from core.export_engine import _build_clip_effect_filters

    work.mkdir(parents=True, exist_ok=True)
    kinds = {"blur": (EffectType.BLUR, "intensity"), "glow": (EffectType.GLOW, "radius"),
             "chromatic": (EffectType.CHROMATIC_ABERRATION, "intensity")}
    cases = []
    for key, (kind, name) in kinds.items():
        low, high = SIGMA[key]
        effect = create_effect(kind, effect_id="e", params={name: (low + high) / 2.0})    # statique : la valeur moyenne
        scenarios = [("statique", ())]
        for count in KEYFRAME_COUNTS:
            frames = tuple(Keyframe(f"fx.e.{name}", DURATION * i / (count - 1), low if i % 2 == 0 else high,
                                    InterpolationType.LINEAR) for i in range(count))
            scenarios.append((f"{count} images clés", frames))
        for label, frames in scenarios:
            written: list[str] = []
            with command_files(written):
                chain = _build_clip_effect_filters(
                    (effect,), 1.0, "v0fx", animation=frames, sendcmd=context_for(RATE, 0.0, DURATION) if frames else None)
            size = 0
            for path in written:                 # le fichier vit dans ``work`` : le dossier est monté tel quel dans Docker
                target = work / f"cmd-{len(cases)}.cmd"
                target.write_bytes(Path(path).read_bytes())
                size += target.stat().st_size
                chain = chain.replace(path, str(target))
                Path(path).unlink(missing_ok=True)
            cases.append({"effect": key, "scenario": label, "chain": chain, "bytes": size})
    (work / "cases.json").write_text(json.dumps(cases, indent=1), encoding="utf-8")
    print(f"{len(cases)} cas écrits dans {work}")
    return 0


def run(work: Path, ffmpeg: str, repeat: int, out: Path) -> int:
    cases = json.loads((work / "cases.json").read_text(encoding="utf-8"))
    banner = subprocess.run([ffmpeg, "-version"], capture_output=True, text=True).stdout.splitlines()[0]
    results = []
    for case in cases:
        best = float("inf")
        for _ in range(repeat):
            start = time.perf_counter()
            completed = subprocess.run(
                [ffmpeg, "-v", "error", "-f", "lavfi", "-i", f"testsrc2=s={WIDTH}x{HEIGHT}:r={RATE}:d={DURATION}",
                 "-vf", f"format=yuv420p,{case['chain']},format=yuv420p", "-f", "null", "-"],
                capture_output=True, text=True, timeout=3600)
            if completed.returncode:
                print(case["effect"], case["scenario"], completed.stderr[:400], file=sys.stderr)
                return 1
            best = min(best, time.perf_counter() - start)
        results.append({**{k: case[k] for k in ("effect", "scenario", "bytes")}, "seconds": best})
        print(f"{case['effect']:10s} {case['scenario']:16s} {best:6.2f} s", flush=True)
    out.write_text(json.dumps({"ffmpeg": banner, "results": results}, indent=1), encoding="utf-8")
    return 0


def report(files: list[Path]) -> int:
    for path in files:
        data = json.loads(path.read_text(encoding="utf-8"))
        print(f"\n{data['ffmpeg']}")
        base: dict[str, float] = {}
        for row in data["results"]:
            if row["scenario"] == "statique":
                base[row["effect"]] = row["seconds"]
            ratio = row["seconds"] / base[row["effect"]]
            print(f"  {LABELS[row['effect']]:40s} {row['scenario']:16s} {row['seconds']:6.2f} s  x{ratio:4.2f}  "
                  f"fichier {row['bytes']} o")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    gen = sub.add_parser("generate")
    gen.add_argument("--work", type=Path, required=True)
    runner = sub.add_parser("run")
    runner.add_argument("--work", type=Path, required=True)
    runner.add_argument("--ffmpeg", default="ffmpeg")
    runner.add_argument("--repeat", type=int, default=3)
    runner.add_argument("--out", type=Path)
    rep = sub.add_parser("report")
    rep.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args()
    if args.command == "generate":
        return generate(args.work)
    if args.command == "run":
        return run(args.work, args.ffmpeg, args.repeat, args.out or args.work / "result.json")
    return report(args.files)


if __name__ == "__main__":
    sys.exit(main())
