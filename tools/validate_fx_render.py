#!/usr/bin/env python3
"""Banc de validation du rendu des paramètres d'effets animés (ADR-0008, étape 3) : hors ligne, jamais importé par l'application.

Question : pour une version de FFmpeg donnée, l'image ``n`` de la sortie ANIMÉE (expression du temps local, graphe de l'export)
est-elle égale à l'image ``n`` du rendu STATIQUE du même effet réglé sur la valeur de la courbe à cet instant ? Critère :
écart maximal <= 1 niveau (même critère et même méthode que ``tools/validate_sendcmd.py``).

Deux temps, parce que les chaînes viennent de l'application (``core.export_engine``) et que les versions de FFmpeg à tester
tournent dans Docker, sans l'application :

1. ``gen`` (en local, avec le dépôt) : écrit ``input.mkv`` (mire ``testsrc2``, FFV1, ``yuv420p``, encodée UNE fois : mêmes octets
   pour toutes les versions) et ``cases.json`` (la chaîne animée de chaque cas, et pour chaque image échantillonnée la chaîne
   statique à la valeur évaluée).
2. ``run`` (stdlib + numpy, dans ou hors Docker) : rejoue ``cases.json`` avec le ``ffmpeg`` donné et écrit un résultat JSON.

    python tools/validate_fx_render.py gen --work DIR
    python tools/validate_fx_render.py run --work DIR --out resultat.json [--ffmpeg ffmpeg] [--cpuflags0]

Dans Docker (voir ``docs/ffmpeg-sendcmd.md``) : ``docker run --rm --platform linux/amd64 -v "$PWD":/w:ro -v DIR:/s IMAGE python3
/w/tools/validate_fx_render.py run --work /s --out /s/r.json`` après ``apt-get install ffmpeg python3 python3-numpy``.
La brume de chaleur (non enregistrée, voir ``core.effect_targets.PENDING_EFFECT_PARAMS``) est validée en l'enregistrant le temps
du ``gen``.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
from pathlib import Path

WIDTH, HEIGHT, RATE, FRAMES = 320, 180, 10, 30
SAMPLES = (0, 1, 2, 7, 15, 22, 29)
TOLERANCE = 1


def _cases() -> dict[str, dict]:
    """Cas (effets, images clés) : construits avec l'application, donc réservés à ``gen``."""
    from core.animation import InterpolationType, Keyframe
    from core.effects_model import EffectType, create_effect, fx_property_id

    def ramp(effect, name, low, high, interpolation=InterpolationType.LINEAR):
        prop = fx_property_id(effect.id, name)
        return [Keyframe(prop, 0.0, low, interpolation), Keyframe(prop, FRAMES / RATE, high, interpolation)]

    cc = create_effect(EffectType.COLOR_CORRECTION, effect_id="cc", params={"saturation": 1.3})
    vg = create_effect(EffectType.VIGNETTE, effect_id="vg")
    hz = create_effect(EffectType.HEAT_HAZE, effect_id="hz",
                       params={"amplitude": 6.0, "frequency": 0.08, "speed": 9.0, "top": 0.2, "span": 0.5})
    ease = InterpolationType.EASE_IN_OUT
    return {
        "eq_brightness_contrast": {"effects": [cc], "animation": ramp(cc, "brightness", 0.0, 0.3) + ramp(cc, "contrast", 0.8, 1.6)},
        "eq_saturation": {"effects": [cc], "animation": ramp(cc, "saturation", 0.2, 2.5, ease)},
        "vignette": {"effects": [vg], "animation": ramp(vg, "intensity", 0.0, 1.0, ease)},
        "heat_haze_amplitude": {"effects": [hz], "animation": ramp(hz, "amplitude", 1.0, 9.0), "layer_space": True},
        "heat_haze_frequency_top_span": {"effects": [hz], "animation": ramp(hz, "frequency", 0.03, 0.12)
                                         + ramp(hz, "top", 0.1, 0.5) + ramp(hz, "span", 0.3, 0.8), "layer_space": True},
    }


def cmd_gen(args: argparse.Namespace) -> int:
    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root))
    from core.chroma_siting import siting_before_rgb
    from core.effect_targets import ANIMATABLE_EFFECT_PARAMS, evaluated_effects
    from core.effects_model import EffectType
    from core.export_engine import _build_clip_effect_filters, _layer_space_filters

    ANIMATABLE_EFFECT_PARAMS[EffectType.HEAT_HAZE] = ("amplitude", "frequency", "top", "span")   # le temps du banc

    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    source = work / "input.mkv"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"testsrc2=s={WIDTH}x{HEIGHT}:r={RATE}:d={FRAMES / RATE},noise=alls=12:allf=t:all_seed=7",
                    "-pix_fmt", "yuv420p", "-c:v", "ffv1", str(source)], check=True)

    def chain(effects, animation, layer_space: bool) -> str:
        build = (lambda: _layer_space_filters(tuple(effects), 1.0, tuple(animation))) if layer_space else (
            lambda: _build_clip_effect_filters(tuple(effects), animation=tuple(animation)))
        text = build()
        # Comme l'export : étage de préparation du clip (PTS remis à zéro après ``fps``), puis l'effet, puis la conversion RVBA
        # (4:2:0 vers RVB co-sitée devant les filtres RVB, ``siting_before_rgb``).
        graph = (text + "," if text else "") + "format=rgba"
        return f"setpts=PTS-STARTPTS,fps={RATE},setpts=PTS-STARTPTS,{siting_before_rgb(graph)}"

    cases = {}
    for name, case in _cases().items():
        layer_space = bool(case.get("layer_space"))
        statics = {}
        for n in SAMPLES:
            fixed = evaluated_effects(tuple(case["effects"]), tuple(case["animation"]), n / RATE)
            text = chain(fixed, (), layer_space)
            for effect in fixed:
                if effect.type is EffectType.VIGNETTE:
                    # L'export statique arrondit l'angle à six décimales (``_format_seconds``) ; l'expression animée est exacte.
                    # La référence prend l'angle exact : sinon un pixel sur une frontière d'arrondi bascule d'un niveau
                    # (mesuré : 1 octet sur 230 000, écart 2, qui disparaît avec l'angle exact).
                    text = re.sub(r"vignette=angle=[0-9.]+", f"vignette=angle={effect.params['intensity'] * math.pi / 4!r}", text)
            statics[str(n)] = text + f",select='eq(n,{n})'"
        cases[name] = {"animated": chain(case["effects"], case["animation"], layer_space), "static": statics}
    (work / "cases.json").write_text(json.dumps(cases, indent=1), encoding="utf-8")
    print(f"{len(cases)} cas écrits dans {work}")
    return 0


def _frames(ffmpeg: str, source: Path, vf: str, cpuflags0: bool) -> bytes:
    command = [ffmpeg, "-v", "error", *(["-cpuflags", "0"] if cpuflags0 else []), "-i", str(source), "-vf", vf, "-fps_mode", "passthrough", "-f", "rawvideo", "-"]
    return subprocess.run(command, capture_output=True, check=True).stdout


def _banner(ffmpeg: str) -> str:
    text = subprocess.run([ffmpeg, "-version"], capture_output=True, text=True, check=True).stdout.splitlines()[0]
    match = re.search(r"version (\S+)", text)
    return match.group(1) if match else text


def cmd_run(args: argparse.Namespace) -> int:
    import numpy as np

    work = Path(args.work)
    cases = json.loads((work / "cases.json").read_text(encoding="utf-8"))
    size = WIDTH * HEIGHT * 4
    report: dict = {"ffmpeg": _banner(args.ffmpeg), "cpuflags0": bool(args.cpuflags0), "cases": {}}
    for name, case in cases.items():
        animated = _frames(args.ffmpeg, work / "input.mkv", case["animated"], args.cpuflags0)
        assert len(animated) == size * FRAMES, (name, len(animated) // size)
        worst, per_frame = 0, {}
        for n, vf in case["static"].items():
            static = np.frombuffer(_frames(args.ffmpeg, work / "input.mkv", vf, args.cpuflags0), np.uint8).astype(int)
            frame = np.frombuffer(animated[int(n) * size:(int(n) + 1) * size], np.uint8).astype(int)
            diff = int(np.abs(frame - static).max())
            per_frame[n] = diff
            worst = max(worst, diff)
        report["cases"][name] = {"max": worst, "per_frame": per_frame, "valid": worst <= TOLERANCE}
    Path(args.out).write_text(json.dumps(report, indent=1), encoding="utf-8")
    for name, result in report["cases"].items():
        print(f"{report['ffmpeg']:>22} {'VALIDÉ' if result['valid'] else 'NON VALIDÉ':>11}  max {result['max']:>3}  {name}")
    return 0 if all(result["valid"] for result in report["cases"].values()) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    gen = sub.add_parser("gen", help="écrit input.mkv et cases.json (en local, avec le dépôt)")
    gen.add_argument("--work", required=True)
    gen.set_defaults(func=cmd_gen)
    run = sub.add_parser("run", help="rejoue cases.json avec un ffmpeg (stdlib + numpy)")
    run.add_argument("--work", required=True)
    run.add_argument("--out", required=True)
    run.add_argument("--ffmpeg", default="ffmpeg")
    run.add_argument("--cpuflags0", action="store_true", help="chemins C, sans SIMD")
    run.set_defaults(func=cmd_run)
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
