#!/usr/bin/env python3
"""Coût de l'étalonnage animé (ADR-0008, étape 6) : outil hors ligne, jamais importé par l'application.

Question de la règle de l'ADR : par quel filtre animer l'exposition et les roues, que l'export statique écrit avec ``lutrgb`` (une
commande ``sendcmd`` de ``lutrgb`` recalcule sa table : ~0,2 s par image) ? Compare à 1080p, 150 images (5 s à 30 i/s, ``-f null``, meilleur
de ``--repeat``), le **même étage** statique et animé (2 ou 10 images clés, interpolation linéaire en dents de scie) :

- exposition : ``lutrgb`` + ``sendcmd`` (l'alternative écartée), ``curves`` à 256 points + ``sendcmd`` (retenue) ;
- roues : ``lutrgb`` + ``sendcmd`` (écartée), ``curves`` ``master`` (molette seule) et ``curves`` ``red`` / ``green`` / ``blue`` (retenues) ;
- contraste + saturation (``eq``, ``eval=frame``), teinte (``hue``), ombres + hautes lumières (``colorbalance`` + ``sendcmd``),
  température (``colorchannelmixer`` + ``sendcmd``) ;
- tous les étages ensemble, dans la chaîne de l'application (``core.export_engine._build_color_grade_filters``) : le critère
  « au plus ×3 la même chaîne non animée » vaut pour l'ensemble.

Les chaînes animées retenues sont **celles de l'export** (le fichier de commandes est celui que l'application écrit) ; les
alternatives écartées sont écrites ici, avec la même chaîne autour. Trois temps, parce que FFmpeg tourne parfois dans Docker sans
l'application :

    python tools/measure_grade_cost.py generate --work DIR                         # écrit DIR/cases.json et les fichiers de commandes
    python tools/measure_grade_cost.py run --work DIR [--ffmpeg ffmpeg] [--repeat 2] [--encode] [--out DIR/result.json]
    python tools/measure_grade_cost.py report DIR/a.json DIR/b.json                # tableau

``run`` n'importe que la bibliothèque standard. ``--encode`` remplace ``-f null`` par un encodage libx264 (``-preset medium``) :
le rapport animé / statique est alors celui d'un export réel, où l'encodeur domine. Dans Docker, monter ``DIR`` **au même chemin** (les
chaînes citent les chemins absolus des fichiers de commandes) :
``docker run --rm --platform linux/amd64 -v "$PWD":/w:ro -v DIR:DIR IMAGE python3 /w/tools/measure_grade_cost.py run --work DIR``.
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
FRAMES = int(RATE * DURATION)
KEYFRAME_COUNTS = (2, 10)

# (clé, libellé, [(propriété, bas, haut)]) : l'étage mesuré et les images clés qui l'animent. Le statique est réglé aux 65 % de la plage.
STAGES = {
    "exposure": ("exposition", [("color.exposure", -1.5, 1.5)]),
    "wheel_master": ("roue (molette gain.y)", [("color.gain.y", -0.5, 0.5)]),
    "wheel_rgb": ("roues colorées (gain.r, lift.b)", [("color.gain.r", -0.4, 0.4), ("color.lift.b", -0.3, 0.3)]),
    "eq": ("contraste + saturation (eq)", [("color.contrast", -0.6, 0.6), ("color.saturation", 0.4, 1.6)]),
    "hue": ("teinte (hue)", [("color.hue", -60.0, 60.0)]),
    "balance": ("ombres + hautes lumières (colorbalance)", [("color.shadows", -0.5, 0.5), ("color.highlights", -0.5, 0.5)]),
    "temperature": ("température (colorchannelmixer)", [("color.temperature", -60.0, 60.0)]),
}
ALTERNATIVES = {"exposure": "lutrgb", "wheel_master": "lutrgb", "wheel_rgb": "lutrgb"}
ALL = "all"


def _zigzag(property_id: str, low: float, high: float, count: int):
    from core.animation import InterpolationType, Keyframe

    return [Keyframe(property_id, DURATION * i / (count - 1), low if i % 2 == 0 else high, InterpolationType.LINEAR)
            for i in range(count)]


def _middle(rows):
    from core.color_grading import ColorGrade
    from core.grade_targets import write_value

    grade = ColorGrade()
    for property_id, low, high in rows:
        grade = write_value(grade, property_id, low + 0.65 * (high - low))     # jamais la valeur neutre : le statique émet son filtre
    return grade


def _app_chain(grade, frames):
    """La chaîne de l'application (conversion de chroma comprise) et ses fichiers de commandes, écrits dans ``written``."""
    from core.chroma_siting import siting_before_rgb
    from core.effect_sendcmd import command_files, context_for
    from core.export_engine import _build_color_grade_filters

    written: list[str] = []
    with command_files(written):
        text = _build_color_grade_filters(grade, "v0cg", animation=tuple(frames), sendcmd=context_for(RATE, 0.0, DURATION) if frames else None)
    return siting_before_rgb(text + ",format=rgba"), written


def _lutrgb_chain(key: str, count: int):
    """L'alternative écartée : ``lutrgb`` dont l'expression est renvoyée à chaque image par ``sendcmd`` (même chaîne autour)."""
    from core.chroma_siting import siting_before_rgb
    from core.color_grading import ColorGrade, Wheel
    from core.color_wheels import exposure_filter, wheels_filter
    from core.effect_sendcmd import sendcmd_filter, write_command_file

    rows = STAGES[key][1]
    lines = []
    for index in range(FRAMES + 2):
        phase = min(index, FRAMES - 1) / (FRAMES - 1)
        values = {}
        for property_id, low, high in rows:
            span = (count - 1) * phase
            segment = min(int(span), count - 2)
            a, b = (low, high) if segment % 2 == 0 else (high, low)
            values[property_id] = a + (b - a) * (span - segment)
        if key == "exposure":
            expression = exposure_filter(values["color.exposure"]).split("r=", 1)[1].split(":g=")[0]
            commands = ", ".join(f"lutrgb@m {channel} {expression}" for channel in "rgb")
        else:
            grade = ColorGrade(gain=Wheel(values.get("color.gain.r", 0.0), 0.0, 0.0, values.get("color.gain.y", 0.0)),
                               lift=Wheel(0.0, 0.0, values.get("color.lift.b", 0.0), 0.0))
            expressions = {part.split("=", 1)[0]: part.split("=", 1)[1] for part in _split_options(wheels_filter(grade)[len("lutrgb="):])}
            commands = ", ".join(f"lutrgb@m {channel} {expressions[channel]}" for channel in "rgb")
        instant = max(0.0, (index - 0.5) / RATE)
        lines.append(f"{instant:.9f} {commands};")
    path = write_command_file("\n".join(lines) + "\n")
    return siting_before_rgb(f"{sendcmd_filter(path)},lutrgb@m=r=val:g=val:b=val,format=rgba"), [path]


def _split_options(text: str) -> list[str]:
    """Découpe ``r='…':g='…':b='…'`` aux ``:`` hors apostrophes."""
    parts, current, quoted = [], "", False
    for char in text:
        if char == "'":
            quoted = not quoted
        if char == ":" and not quoted:
            parts.append(current)
            current = ""
        else:
            current += char
    return [*parts, current]


def generate(work: Path) -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from core.color_grading import ColorGrade
    from core.effect_sendcmd import command_files  # noqa: F401 - vérifie l'import avant d'écrire

    work.mkdir(parents=True, exist_ok=True)
    cases = []

    def add(key, label, scenario, chain, files):
        size = 0
        for path in files:                            # le fichier vit dans ``work`` : le dossier est monté tel quel dans Docker
            target = work / f"cmd-{len(cases)}-{Path(path).name}"
            target.write_bytes(Path(path).read_bytes())
            size += target.stat().st_size
            chain = chain.replace(path, str(target)).replace(Path(path).as_posix(), str(target))
            Path(path).unlink(missing_ok=True)
        cases.append({"stage": key, "label": label, "scenario": scenario, "chain": chain, "bytes": size})

    everything = [row for _label, rows in STAGES.values() for row in rows]
    for key, (label, rows) in [*STAGES.items(), (ALL, ("tous les étages ensemble", everything))]:
        grade = _middle(rows)
        add(key, label, "statique", *_app_chain(grade, ()))
        for count in KEYFRAME_COUNTS:
            frames = [kf for property_id, low, high in rows for kf in _zigzag(property_id, low, high, count)]
            add(key, label, f"{count} images clés", *_app_chain(ColorGrade(), frames))
        if key in ALTERNATIVES:
            chain, files = _lutrgb_chain(key, 2)
            add(key, label, "lutrgb 2 images clés (écartée)", chain, files)
    (work / "cases.json").write_text(json.dumps(cases, indent=1), encoding="utf-8")
    print(f"{len(cases)} cas écrits dans {work}")
    return 0


def run(work: Path, ffmpeg: str, repeat: int, out: Path, encode: bool) -> int:
    cases = json.loads((work / "cases.json").read_text(encoding="utf-8"))
    banner = subprocess.run([ffmpeg, "-version"], capture_output=True, text=True).stdout.splitlines()[0]
    sink = ["-c:v", "libx264", "-preset", "medium", "-crf", "23", "-f", "null", "-"] if encode else ["-f", "null", "-"]
    results = []
    for case in cases:
        best = float("inf")
        for _ in range(1 if "écartée" in case["scenario"] else repeat):
            start = time.perf_counter()
            completed = subprocess.run(
                [ffmpeg, "-v", "error", "-f", "lavfi", "-i", f"testsrc2=s={WIDTH}x{HEIGHT}:r={RATE}:d={DURATION}",
                 "-vf", f"format=yuv420p,{case['chain']},format=yuv420p", *sink],
                capture_output=True, text=True, timeout=3600)
            if completed.returncode:
                print(case["stage"], case["scenario"], completed.stderr[:400], file=sys.stderr)
                return 1
            best = min(best, time.perf_counter() - start)
        results.append({**{k: case[k] for k in ("stage", "label", "scenario", "bytes")}, "seconds": best})
        print(f"{case['stage']:13s} {case['scenario']:32s} {best:7.2f} s", flush=True)
    out.write_text(json.dumps({"ffmpeg": banner, "encode": encode, "results": results}, indent=1), encoding="utf-8")
    return 0


def report(files: list[Path]) -> int:
    for path in files:
        data = json.loads(path.read_text(encoding="utf-8"))
        print(f"\n{data['ffmpeg']}  ({'libx264' if data.get('encode') else '-f null'})")
        base: dict[str, float] = {}
        for row in data["results"]:
            if row["scenario"] == "statique":
                base[row["stage"]] = row["seconds"]
            ratio = row["seconds"] / base[row["stage"]]
            per_frame = (row["seconds"] - base[row["stage"]]) / FRAMES * 1000.0
            print(f"  {row['label']:42s} {row['scenario']:32s} {row['seconds']:7.2f} s  x{ratio:6.2f}  "
                  f"+{per_frame:7.2f} ms/image  fichier {row['bytes']} o")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    gen = sub.add_parser("generate")
    gen.add_argument("--work", type=Path, required=True)
    runner = sub.add_parser("run")
    runner.add_argument("--work", type=Path, required=True)
    runner.add_argument("--ffmpeg", default="ffmpeg")
    runner.add_argument("--repeat", type=int, default=2)
    runner.add_argument("--encode", action="store_true")
    runner.add_argument("--out", type=Path)
    rep = sub.add_parser("report")
    rep.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args()
    if args.command == "generate":
        return generate(args.work)
    if args.command == "run":
        return run(args.work, args.ffmpeg, args.repeat, args.out or args.work / "result.json", args.encode)
    return report(args.files)


if __name__ == "__main__":
    sys.exit(main())
