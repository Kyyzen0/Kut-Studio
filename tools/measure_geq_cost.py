#!/usr/bin/env python3
"""Coût de ``geq`` quand la brume de chaleur est animée (ADR-0008, étape 3) : hors ligne, jamais importé par l'application.

``geq`` réévalue son expression pour chaque pixel de chaque plan : y mettre la courbe d'un paramètre multiplie le coût. Ce banc
mesure, sur 1280x720 et 5 s à 30 i/s (``-f null``), le temps de la brume statique et animée (2 ou 10 images clés ; un ou
quatre paramètres). Critère de l'ADR : animée à 10 images clés <= 2 fois le statique.

    python tools/measure_geq_cost.py [--ffmpeg ffmpeg] [--interpolation linear|bezier]

La brume animée n'est pas dans la liste enregistrée (``core.effect_targets.PENDING_EFFECT_PARAMS``) : le banc l'y met le temps de la
mesure. Compter plusieurs minutes pour le cas à dix images clés sur quatre paramètres ; sur une machine chargée, seuls les rapports
comptent.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

RANGES = {"amplitude": (2.0, 12.0), "frequency": (0.03, 0.09), "top": (0.1, 0.6), "span": (0.2, 0.9)}
DURATION = 5.0


def animation(count: int, names, interpolation):
    from core.animation import Keyframe

    return tuple(
        Keyframe(f"fx.h.{name}", DURATION * i / (count - 1), RANGES[name][0] + (RANGES[name][1] - RANGES[name][0]) * (i % 2),
                 interpolation)
        for name in names for i in range(count)
    )


def run(ffmpeg: str, chain: str, repeat: int) -> float | None:
    best = float("inf")
    for _ in range(repeat):
        start = time.perf_counter()
        completed = subprocess.run(
            [ffmpeg, "-v", "error", "-f", "lavfi", "-i", f"testsrc2=s=1280x720:r=30:d={DURATION},format=yuv420p", "-vf", chain,
             "-f", "null", "-"], capture_output=True, text=True, timeout=3600)
        if completed.returncode:
            print(completed.stderr[:500])
            return None
        best = min(best, time.perf_counter() - start)
    return best


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--interpolation", choices=("linear", "bezier"), default="linear")
    args = parser.parse_args()

    from core.animation import InterpolationType
    from core.effect_targets import ANIMATABLE_EFFECT_PARAMS
    from core.effects_model import EffectType, create_effect
    from core.export_engine import _layer_space_filters

    ANIMATABLE_EFFECT_PARAMS[EffectType.HEAT_HAZE] = tuple(RANGES)
    interpolation = InterpolationType(args.interpolation)
    haze = create_effect(EffectType.HEAT_HAZE, effect_id="h", params={"amplitude": 4.0, "frequency": 0.05, "speed": 9.0,
                                                                     "top": 0.3, "span": 0.4})
    cases = {
        "statique": (),
        "2 images clés, amplitude seule": animation(2, ["amplitude"], interpolation),
        "10 images clés, amplitude seule": animation(10, ["amplitude"], interpolation),
        "2 images clés, 4 paramètres": animation(2, RANGES, interpolation),
        "10 images clés, 4 paramètres": animation(10, RANGES, interpolation),
    }
    base = None
    for label, frames in cases.items():
        chain = _layer_space_filters((haze,), 1.0, frames)
        seconds = run(args.ffmpeg, chain, 2 if not frames else 1)
        if seconds is None:
            return 1
        base = base or seconds
        print(f"{label}: {seconds:.2f} s  x{seconds / base:.2f}  (chaîne de {len(chain)} caractères)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
