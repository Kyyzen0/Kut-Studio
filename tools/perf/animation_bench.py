"""Mesure du moteur d'animation : évaluation, construction, expression FFmpeg.

Usage::

    python -m tools.perf.animation_bench
    python -m tools.perf.animation_bench --json docs/perf/animation.json

Pour 10, 100 et 1 000 keyframes (interpolations mélangées), mesure la
médiane de :

- ``evaluate`` : une valeur à un instant aléatoire (une image de lecture) ;
- ``evaluate_transform`` : les cinq propriétés d'un clip animées ensemble ;
- ``build`` : construction de la courbe (résolution des segments) ;
- ``ffmpeg`` : génération de l'expression d'export.

Les temps absolus dépendent de la machine ; ce qui compte est que
l'évaluation reste quasi constante quand le nombre de keyframes croît
(recherche dichotomique, ``O(log n)``).
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.animation import AnimationCurve, InterpolationType, Keyframe  # noqa: E402
from core.animation_ffmpeg import curve_expression  # noqa: E402
from core.visual_effects import ANIMATABLE_PROPERTIES, ClipTransform, TransformKeyframe, evaluate_transform  # noqa: E402

SIZES = (10, 100, 1000)
KINDS = list(InterpolationType)


def _keyframes(count: int, name: str = "opacity", seed: int = 7) -> list[Keyframe]:
    rng = random.Random(seed)
    return [
        TransformKeyframe(name, i * 0.04, 0.1 + 0.8 * rng.random(), KINDS[i % len(KINDS)])
        for i in range(count)
    ]


def _median_us(fn, repeats: int) -> float:
    fn()
    samples = []
    for _ in range(repeats):
        start = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - start) * 1e6)
    return statistics.median(samples)


def run() -> dict:
    results = {}
    rng = random.Random(1)
    for size in SIZES:
        frames = _keyframes(size)
        curve = AnimationCurve(frames)
        end = frames[-1].time_seconds
        instants = [rng.uniform(0, end) for _ in range(1000)]
        position = iter(instants * 1000)
        all_props = [
            kf
            for name in ANIMATABLE_PROPERTIES for kf in _keyframes(size, name)
        ]
        transform = ClipTransform()
        evaluate_transform(transform, all_props, 0.0)  # remplit le cache des courbes
        results[size] = {
            "evaluate_us": round(_median_us(lambda: curve.evaluate(next(position)), 2000), 3),
            "evaluate_transform_5_props_us": round(
                _median_us(lambda: evaluate_transform(transform, all_props, next(position)), 500), 2
            ),
            "build_curve_us": round(_median_us(lambda: AnimationCurve(frames), 30), 1),
            "ffmpeg_expression_us": round(_median_us(lambda: curve_expression(curve), 30), 1),
            "ffmpeg_expression_chars": len(curve_expression(curve)),
        }
    return results


def format_report(results: dict) -> str:
    lines = [f"{'keyframes':>10} {'evaluate':>10} {'5 props':>10} {'build':>10} {'ffmpeg':>10} {'expr chars':>11}"]
    for size, row in results.items():
        lines.append(
            f"{size:>10} {row['evaluate_us']:>8.2f}µs {row['evaluate_transform_5_props_us']:>8.1f}µs "
            f"{row['build_curve_us']:>8.0f}µs {row['ffmpeg_expression_us']:>8.0f}µs {row['ffmpeg_expression_chars']:>11}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--json", metavar="FICHIER")
    args = parser.parse_args(argv)
    results = run()
    print(format_report(results))
    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
