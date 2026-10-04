"""Mesures du flux optique et du mélange d'images : estimation, synthèse, mémoire, cache, et rendu de bout en bout.

Usage::

    python -m tools.perf.flow_bench --out docs/perf/optical-flow.json --update-doc docs/optical-flow.md
    python -m tools.perf.flow_bench --from-json docs/perf/optical-flow.json --update-doc docs/optical-flow.md   # sans mesurer
    python -m tools.perf.flow_bench --from-json docs/perf/optical-flow.json --refresh-quality --update-doc docs/optical-flow.md
    python -m tools.perf.flow_bench --quick                         # petit jeu, sert aux tests
    python -m tools.perf.flow_bench --sizes 1280x720,1920x1080

Cinq familles de mesures ; les trois premières chacune dans un **sous-processus** (mémoire isolée) :

``estimate``    une paire d'images analysée (aller et retour), par résolution et par qualité : temps, pic de mémoire, taille
                de l'analyse **stockée** dans le cache, confiance mesurée ;
``synthesize``  une image intermédiaire fabriquée à la pleine résolution, flux optique contre mélange simple ;
``render``      un clip de 2 s ralenti à 25 % (240 % de durée) rendu de bout en bout par les trois modes : temps, images par
                seconde, taille du flux préparé, pic de mémoire ;
``accuracy``    des scènes synthétiques à **vérité terrain exacte** (``tests/flow_scenes.py``) : erreur sur la région de l'objet,
                recouvrement et position (px) de l'objet, flux contre mélange simple ;
``extreme``     la même vérité exacte aux ralentis **extrêmes** (50 %, 25 %, 10 %, 5 %) sur une suite de paires : erreur, position
                maximale et à-coup de la trajectoire d'une image à l'autre.

Les images d'analyse sont synthétiques mais **texturées et en mouvement** (texture lisse, objet qui se déplace de plusieurs
pixels) : un champ uniforme serait trivial. Les temps dépendent de la machine ; les tests vérifient les invariants (la qualité
coûte, le cache de vecteurs rend l'analyse suivante gratuite, la mémoire ne dépend pas du nombre d'images).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
for _entry in (ROOT, ROOT / "tests"):
    if str(_entry) not in sys.path:
        sys.path.insert(0, str(_entry))

QUALITIES = ("draft", "balanced", "best")


def _peak_rss_mb(children: bool = False) -> float:
    import resource

    unit = 1 if sys.platform == "darwin" else 1024
    who = resource.RUSAGE_CHILDREN if children else resource.RUSAGE_SELF
    return round(resource.getrusage(who).ru_maxrss * unit / 1e6, 1)


def make_pair(width: int, height: int):
    """Deux images couleur ``(h, w, 3)`` : une texture lisse commune et un objet clair qui avance de 8 px."""
    import numpy as np

    rng = np.random.default_rng(7)
    base = rng.random((height // 8 + 2, width // 8 + 2)).astype(np.float32)
    texture = np.kron(base, np.ones((8, 8), np.float32))[:height, :width]
    from core.flow_numpy import box_mean

    texture = 0.25 + 0.4 * box_mean(box_mean(texture, 6), 6)
    frames = []
    for step in (0, 8):
        frame = texture.copy()
        size = max(24, height // 4)
        top, left = height // 2 - size // 2, width // 3 + step
        frame[top:top + size, left:left + size] = 0.85 - 0.1 * texture[top:top + size, left:left + size]
        frames.append(np.repeat(frame[:, :, None], 3, axis=2).astype(np.float32))
    return frames[0], frames[1]


def _scene_suite():
    """Les scènes de la mesure de précision : ``{nom: Scene}`` (320 × 180, objet texturé de 60 px sauf mention)."""
    import numpy as np
    from flow_scenes import Body, Scene, accelerated, linear

    flat = lambda x, y: np.full_like(x, 0.78)  # noqa: E731 - objet sans texture
    width, height = 320, 180
    return {
        "translation +3 px": Scene(width, height, [Body(60, linear(100, 90, 3, 0))]),
        "translation +8 px": Scene(width, height, [Body(60, linear(100, 90, 8, 0))]),
        "translation +24 px": Scene(width, height, [Body(60, linear(80, 90, 24, 0))]),
        "diagonale (7, 5)": Scene(width, height, [Body(60, linear(100, 70, 7, 5))]),
        "accélération": Scene(width, height, [Body(60, accelerated(60, 90, 1.0, 1.2))]),
        "rotation 10°/image": Scene(width, height, [Body(70, linear(160, 90, 0, 0, spin=np.radians(10)))]),
        "zoom +6 %/image": Scene(width, height, [Body(60, linear(160, 90, 0, 0, scale=1.0, zoom=0.06))]),
        "objet sans texture": Scene(width, height, [Body(60, linear(100, 90, 6, 0), texture=flat)]),
        "deux objets opposés": Scene(width, height, [Body(40, linear(60, 55, 6, 0), depth=0), Body(40, linear(260, 125, -6, 0), depth=1)]),
        "croisement": Scene(width, height, [Body(40, linear(60, 90, 6, 0), depth=1), Body(50, linear(260, 90, -4, 0), depth=0)]),
    }


def best_offset(made, truth, threshold: float = 0.55, radius: int = 40, vertical: int = 3):
    """Où l'image fabriquée met l'objet, par rapport à la vérité : décalage ``(dx, dy)`` en pixels (sous-pixel) qui recolle l'objet
    de ``truth`` sur ``made``. ``None`` si la vérité n'a pas d'objet.

    On apparie un gabarit plutôt que de comparer des barycentres : un mélange déplace le centre de masse **exactement** comme le
    mouvement réel (``(1-t)·xA + t·xB``) alors que l'image est doublée ; son erreur est le flou, pas le barycentre. L'appariement
    mesure où se trouve ce qui ressemble à l'objet."""
    import numpy as np

    mask = truth > threshold
    if not mask.any():
        return None
    rows, cols = np.where(mask)
    height, width = truth.shape
    y0, y1 = max(0, int(rows.min()) - 2), min(height, int(rows.max()) + 3)
    x0, x1 = max(0, int(cols.min()) - 2), min(width, int(cols.max()) + 3)
    patch = truth[y0:y1, x0:x1].astype(np.float64)
    costs: dict[tuple[int, int], float] = {}
    for dy in range(-vertical, vertical + 1):
        for dx in range(-radius, radius + 1):
            if y0 + dy < 0 or x0 + dx < 0 or y1 + dy > height or x1 + dx > width:
                continue
            costs[(dx, dy)] = float(np.mean((made[y0 + dy:y1 + dy, x0 + dx:x1 + dx].astype(np.float64) - patch) ** 2))
    best_x, best_y = min(costs, key=lambda key: costs[key])

    def refine(before, centre, after) -> float:
        """Sommet de la parabole des trois coûts voisins (0 s'il manque un voisin ou si le coût n'a pas de creux)."""
        if before is None or after is None:
            return 0.0
        curvature = before - 2.0 * centre + after
        return 0.5 * (before - after) / curvature if curvature > 1e-18 else 0.0

    centre = costs[(best_x, best_y)]
    fx = best_x + refine(costs.get((best_x - 1, best_y)), centre, costs.get((best_x + 1, best_y)))
    fy = best_y + refine(costs.get((best_x, best_y - 1)), centre, costs.get((best_x, best_y + 1)))
    return float(fx), float(fy)


def accuracy() -> list[dict]:
    """Erreur (niveaux sur 255, sur la région de l'objet), recouvrement et **position** (px) du flux contre le mélange, moyennés sur
    ``t = ¼, ½, ¾``."""
    import numpy as np
    from flow_scenes import rgb

    from core.optical_flow import FlowParams, NumpyBackend, OpticalFlowEngine, analysis_plane, blend_frames
    from core.time_remapping import FlowQuality

    params = FlowParams(scale=1, levels=5, iterations=3, window=5, smoothing=4)
    engine = OpticalFlowEngine(FlowQuality.BALANCED)
    threshold = 0.55
    rows = []
    for name, scene in _scene_suite().items():
        first, second = rgb(scene.render(4.0)), rgb(scene.render(5.0))
        pair = NumpyBackend().analyze(analysis_plane(first, 1), analysis_plane(second, 1), params)
        flow_errors, blend_errors, flow_overlaps, blend_overlaps = [], [], [], []
        flow_places, blend_places = [], []
        for t in (0.25, 0.5, 0.75):
            truth = scene.render(4.0 + t)
            made = engine.interpolator.interpolate(first, second, pair, t).pixels[:, :, 0]
            mixed = blend_frames(first, second, t)[:, :, 0]
            region = np.zeros(truth.shape, dtype=bool)
            for moment in (4.0, 5.0, 4.0 + t):
                region |= scene.render(moment) > threshold
            flow_errors.append(float(np.abs(made - truth)[region].mean()) * 255)
            blend_errors.append(float(np.abs(mixed - truth)[region].mean()) * 255)
            for result, bucket in ((made, flow_overlaps), (mixed, blend_overlaps)):
                inside, wanted = result > threshold, truth > threshold
                bucket.append(float((inside & wanted).sum() / max(1, (inside | wanted).sum())))
            if len(scene.bodies) == 1:                                       # un seul objet : « sa » position a un sens
                for result, bucket in ((made, flow_places), (mixed, blend_places)):
                    found = best_offset(result, truth, threshold)
                    if found is not None:
                        bucket.append(math.hypot(*found))
        rows.append({
            "scene": name, "flow_error": round(float(np.mean(flow_errors)), 2), "blend_error": round(float(np.mean(blend_errors)), 2),
            "flow_iou": round(float(np.mean(flow_overlaps)), 3), "blend_iou": round(float(np.mean(blend_overlaps)), 3),
            "flow_position": round(float(np.mean(flow_places)), 2) if flow_places else None,
            "blend_position": round(float(np.mean(blend_places)), 2) if blend_places else None,
        })
    return rows


EXTREME_RATIOS = (0.5, 0.25, 0.1, 0.05)
EXTREME_SCENES = ("translation +8 px", "translation +24 px", "rotation 10°/image", "croisement")


def extreme_slowdown(ratios=EXTREME_RATIOS, scenes=EXTREME_SCENES, pairs=(4, 5, 6)) -> list[dict]:
    """La qualité des images fabriquées quand le ralenti devient extrême (50 %, 25 %, 10 %, 5 %), sur une suite de paires consécutives.

    Chaque image de sortie est comparée à la vérité exacte de la scène à sa position fractionnaire. Mesure, par scène et par ratio :
    l'erreur en niveaux de gris sur la région de l'objet (moyenne et maximum sur les seules images fabriquées), la **position**
    de l'objet (maximum, en pixels, scènes à un seul objet) et l'**à-coup** : la plus grande variation de cette erreur de position
    entre deux images de sortie consécutives, y compris celles qui tombent sur une image source (erreur nulle), c'est-à-dire ce
    que l'œil voit comme un saut de vitesse du mouvement."""
    import numpy as np
    from flow_scenes import rgb

    from core.optical_flow import FlowParams, NumpyBackend, OpticalFlowEngine, analysis_plane, blend_frames
    from core.time_remapping import FlowQuality

    params = FlowParams(scale=1, levels=5, iterations=3, window=5, smoothing=4)
    engine = OpticalFlowEngine(FlowQuality.BALANCED)
    threshold = 0.55
    suite = _scene_suite()
    rows = []
    for name in scenes:
        scene = suite[name]
        single = len(scene.bodies) == 1
        frames = {index: rgb(scene.render(float(index))) for index in range(pairs[0], pairs[-1] + 2)}
        analyses = {
            index: NumpyBackend().analyze(analysis_plane(frames[index], 1), analysis_plane(frames[index + 1], 1), params)
            for index in pairs
        }
        for ratio in ratios:
            count = round(len(pairs) / ratio)
            error = {"flow": [], "blend": []}
            place = {"flow": [], "blend": []}                                   # position (px) de chaque image, fabriquée ou non
            trajectory: dict[str, list[tuple[float, float]]] = {"flow": [], "blend": []}
            for step in range(count):
                tau = pairs[0] + step * ratio
                index = min(int(math.floor(tau + 1e-9)), pairs[-1])
                t = tau - index
                if t < 1e-6:                                                    # une image source : exacte, sans erreur
                    for key in trajectory:
                        trajectory[key].append((0.0, 0.0))
                    continue
                truth = scene.render(tau)
                made = {
                    "flow": engine.interpolator.interpolate(frames[index], frames[index + 1], analyses[index], t).pixels[:, :, 0],
                    "blend": blend_frames(frames[index], frames[index + 1], t)[:, :, 0],
                }
                region = np.zeros(truth.shape, dtype=bool)
                for moment in (float(index), float(index + 1), tau):
                    region |= scene.render(moment) > threshold
                for key, image in made.items():
                    error[key].append(float(np.abs(image - truth)[region].mean()) * 255)
                    found = best_offset(image, truth, threshold) if single else None
                    trajectory[key].append(found if found is not None else (0.0, 0.0))
                    if found is not None:
                        place[key].append(math.hypot(*found))
            row: dict = {"scene": name, "ratio": ratio, "images_per_pair": round(1 / ratio) - 1}
            for key in ("flow", "blend"):
                row[f"{key}_error"] = round(float(np.mean(error[key])), 2)
                row[f"{key}_error_max"] = round(float(np.max(error[key])), 2)
                row[f"{key}_position_max"] = round(float(np.max(place[key])), 2) if place[key] else None
                steps = [math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(trajectory[key], trajectory[key][1:])]
                row[f"{key}_jerk"] = round(float(max(steps)), 2) if single and steps else None
            rows.append(row)
    return rows


def _worker(payload: str) -> None:
    args = json.loads(payload)
    kind = args["kind"]
    result = {"estimate": _estimate, "synthesize": _synthesize, "render": _render}[kind](args)
    result["peak_rss_mb"] = _peak_rss_mb()
    result["rss_largest_ffmpeg_mb"] = _peak_rss_mb(children=True)
    print(json.dumps(result))


def _estimate(args: dict) -> dict:
    import io

    import numpy as np

    from core.optical_flow import OpticalFlowEngine, analysis_plane
    from core.time_remapping import FlowQuality

    width, height = int(args["width"]), int(args["height"])
    engine = OpticalFlowEngine(FlowQuality(args["quality"]))
    first, second = make_pair(width, height)
    began = time.perf_counter()
    pair = engine.estimator.analyze(first, second)
    seconds = time.perf_counter() - began
    stored = io.BytesIO()
    packed = pair.pack()
    np.savez(stored, flow=packed["flow"], status=packed["status"])
    grid = analysis_plane(first, engine.params.scale).shape
    confidence = float(pair.forward.confidence.mean()) if pair.forward is not None else None
    return {
        "frame": f"{width}x{height}", "quality": args["quality"], "analysis_grid": f"{grid[1]}x{grid[0]}",
        "backend": engine.backend.name, "seconds": round(seconds, 3), "stored_kb": round(stored.tell() / 1024, 1),
        "mean_confidence": None if confidence is None else round(confidence, 3), "status": pair.status.value,
    }


def _synthesize(args: dict) -> dict:
    from core.optical_flow import OpticalFlowEngine, blend_frames
    from core.time_remapping import FlowQuality

    width, height = int(args["width"]), int(args["height"])
    engine = OpticalFlowEngine(FlowQuality(args["quality"]))
    first, second = make_pair(width, height)
    pair = engine.estimator.analyze(first, second)
    began = time.perf_counter()
    result = engine.interpolator.interpolate(first, second, pair, 0.5)
    flow_seconds = time.perf_counter() - began
    began = time.perf_counter()
    blend_frames(first, second, 0.5)
    blend_seconds = time.perf_counter() - began
    return {
        "frame": f"{width}x{height}", "quality": args["quality"], "flow_synthesis_s": round(flow_seconds, 3),
        "blend_s": round(blend_seconds, 4), "confidence": round(result.confidence, 3), "fallback": result.fallback.value,
    }


def _render(args: dict) -> dict:
    """Un clip de 2 s ralenti à 25 % rendu de bout en bout (préparation + graphe + FFmpeg) par le mode demandé."""
    import shutil

    from core.export_engine import ExportEngine
    from core.flow_cache import FlowCache
    from core.project_model import Clip, MediaAsset, Project, Track
    from core.render_plan import build_render_plan
    from core.retime_layers import prepare_plan
    from core.time_remapping import FlowQuality, TimeInterpolation, TimeRemapping

    width, height, fps = int(args["width"]), int(args["height"]), 30
    folder = Path(args["folder"])
    media = folder / f"source-{width}x{height}.mp4"
    if not media.exists():
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s={width}x{height}:r={fps}:d=2,format=yuv420p",
             "-c:v", "libx264", "-crf", "14", "-preset", "veryfast", str(media)],
            check=True, timeout=300,
        )
    mode = TimeInterpolation(args["mode"])
    asset = MediaAsset("a", str(media), "bench", 2.0, width, height, float(fps), "video", False)
    clip = Clip("c1", "a", "V1", 0.0, 0.0, 2.0, time_remapping=TimeRemapping(
        speed=0.25, interpolation=mode, flow_quality=FlowQuality(args["quality"])))
    project = Project("bench", width=width, height=height, fps=float(fps), media_assets=[asset],
                      tracks=[Track("V1", "V1", "video", clips=[clip])])
    plan = build_render_plan(project)
    cache_dir = folder / f"cache-{args['mode']}-{args['quality']}-{width}x{height}"
    shutil.rmtree(cache_dir, ignore_errors=True)
    cache = FlowCache(cache_dir)
    began = time.perf_counter()
    preparation = prepare_plan(plan, width, height, fps, cache)
    prepare_seconds = time.perf_counter() - began
    graph, video, audio, inputs = ExportEngine._build_filter_complex(plan, width, height, fps, None, prepared=preparation.streams)
    command = ["ffmpeg", "-v", "error", "-y"]
    for item in inputs:
        command += ["-i", item]
    command += ["-filter_complex", f"{graph};[{audio}]anullsink", "-map", f"[{video}]", "-f", "null", "-"]
    began = time.perf_counter()
    subprocess.run(command, check=True, capture_output=True, timeout=1800)
    encode_seconds = time.perf_counter() - began
    frames = round(clip.duration * fps)
    stream_mb = sum(Path(stream.path).stat().st_size for stream in preparation.streams.values()) / 1e6
    # Second passage : le flux préparé et tous les vecteurs sont en cache.
    began = time.perf_counter()
    again = prepare_plan(plan, width, height, fps, cache)
    warm_seconds = time.perf_counter() - began
    return {
        "frame": f"{width}x{height}", "mode": args["mode"], "quality": args["quality"] if mode is TimeInterpolation.OPTICAL_FLOW else "-",
        "output_frames": frames, "prepare_s": round(prepare_seconds, 2), "encode_s": round(encode_seconds, 2),
        "total_fps": round(frames / (prepare_seconds + encode_seconds), 1), "prepared_stream_mb": round(stream_mb, 1),
        "synthesized": preparation.report.synthesized, "pairs_computed": preparation.report.pairs_computed,
        "degraded": preparation.report.degraded, "fallbacks": dict(preparation.report.fallbacks),
        "warm_prepare_s": round(warm_seconds, 3), "warm_reused": again.report.reused,
        "mean_confidence": round(preparation.report.mean_confidence, 3),
    }


def _run_worker(args: dict) -> dict:
    done = subprocess.run(
        [sys.executable, "-m", "tools.perf.flow_bench", "--worker", json.dumps(args)], cwd=ROOT, capture_output=True, text=True,
        timeout=7200, check=False, env={**os.environ, "PYTHONPATH": f"{ROOT}:{ROOT / 'tests'}"},
    )
    if done.returncode != 0:
        raise RuntimeError(done.stderr[-800:])
    return json.loads(done.stdout.strip().splitlines()[-1])


def run(
    *, sizes=((640, 360), (1280, 720), (1920, 1080)), qualities=QUALITIES, render_sizes=((1280, 720),),
    extreme_scenes=EXTREME_SCENES, extreme_ratios=EXTREME_RATIOS,
) -> dict:
    report: dict = {
        "meta": {"python": platform.python_version(), "machine": platform.machine(), "system": platform.system()},
        "estimate": [], "synthesize": [], "render": [], "accuracy": accuracy(),
        "extreme": extreme_slowdown(extreme_ratios, extreme_scenes),
    }
    for width, height in sizes:
        for quality in qualities:
            report["estimate"].append(_run_worker({"kind": "estimate", "width": width, "height": height, "quality": quality}))
        report["synthesize"].append(_run_worker({"kind": "synthesize", "width": width, "height": height, "quality": "balanced"}))
    with tempfile.TemporaryDirectory(prefix="kut-flow-bench-") as workdir:
        for width, height in render_sizes:
            for mode, quality in (("sampling", "balanced"), ("blending", "balanced"), *(("optical_flow", q) for q in qualities)):
                report["render"].append(_run_worker({
                    "kind": "render", "width": width, "height": height, "mode": mode, "quality": quality, "folder": workdir,
                }))
    return report


# ---------------------------------------------------------------------------
# Tableaux du document : le JSON fait foi, le document n'est qu'une mise en forme
# ---------------------------------------------------------------------------

DOC_MARKERS = {"MEASURES": "mesures", "ACCURACY": "précision"}


def _table(header: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---:" if i else ":---" for i in range(len(header))) + "|"]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join(lines)


def _kb(value: float) -> str:
    return f"{value / 1000:.1f} Mo" if value >= 1000 else f"{value:.0f} Ko"


def measures_markdown(report: dict) -> str:
    """Estimation, synthèse et rendu de bout en bout, en trois tableaux."""
    estimate = _table(
        ["Image", "Qualité", "Grille d'analyse", "Temps / paire", "Crête mémoire", "Stocké / paire", "Confiance"],
        [[row["frame"], row["quality"], row["analysis_grid"], f"{row['seconds']:.2f} s", f"{row['peak_rss_mb']:.0f} Mo",
          _kb(row["stored_kb"]), "—" if row["mean_confidence"] is None else f"{row['mean_confidence']:.2f}"]
         for row in report["estimate"]],
    )
    synthesis = _table(
        ["Image", "Flux : synthèse d'une image", "Mélange simple", "Crête mémoire"],
        [[row["frame"], f"{row['flow_synthesis_s']:.2f} s", f"{row['blend_s'] * 1000:.1f} ms", f"{row['peak_rss_mb']:.0f} Mo"]
         for row in report["synthesize"]],
    )
    render = _table(
        ["Image", "Mode", "Qualité", "Préparation", "Encodage", "Images/s", "Flux préparé", "Crête mémoire", "Dégradées"],
        [[row["frame"], row["mode"], row["quality"], f"{row['prepare_s']:.1f} s", f"{row['encode_s']:.1f} s",
          f"{row['output_frames'] / max(1e-9, row['prepare_s'] + row['encode_s']):.1f}", f"{row['prepared_stream_mb']:.0f} Mo",
          f"{row['peak_rss_mb']:.0f} Mo", str(row["degraded"])] for row in report["render"]],
    )
    return (
        "**Estimation d'une paire** (aller et retour, sous-processus isolé) :\n\n" + estimate
        + "\n\n**Synthèse d'une image intermédiaire** à la pleine résolution :\n\n" + synthesis
        + "\n\n**Clip de 2 s ralenti à 25 %** (240 images de sortie), de bout en bout :\n\n" + render
    )


def _pixels(value) -> str:
    return "—" if value is None else f"{value:.2f}"


def _require(report: dict, key: str) -> list[dict]:
    if key not in report:
        raise SystemExit(f"le rapport n'a pas la section « {key} » : relancez avec --refresh-quality")
    return report[key]


def accuracy_markdown(report: dict) -> str:
    return _table(
        ["Scène", "Erreur flux", "Erreur mélange", "Recouvrement flux", "Recouvrement mélange", "Position flux (px)", "Position mélange (px)"],
        [[row["scene"], f"{row['flow_error']:.1f}", f"{row['blend_error']:.1f}", f"{row['flow_iou']:.3f}", f"{row['blend_iou']:.3f}",
          _pixels(row.get("flow_position")), _pixels(row.get("blend_position"))] for row in _require(report, "accuracy")],
    )


def extreme_markdown(report: dict) -> str:
    """Qualité des images fabriquées à 50 %, 25 %, 10 % et 5 % (suite de paires consécutives, vérité exacte)."""
    return _table(
        ["Scène", "Ralenti", "Images / paire", "Erreur flux moy. (max)", "Erreur mélange moy. (max)",
         "Position max flux / mélange (px)", "À-coup max flux / mélange (px)"],
        [[row["scene"], f"{row['ratio'] * 100:g} %", str(row["images_per_pair"]),
          f"{row['flow_error']:.1f} ({row['flow_error_max']:.1f})", f"{row['blend_error']:.1f} ({row['blend_error_max']:.1f})",
          f"{_pixels(row['flow_position_max'])} / {_pixels(row['blend_position_max'])}",
          f"{_pixels(row['flow_jerk'])} / {_pixels(row['blend_jerk'])}"] for row in _require(report, "extreme")],
    )


def _cost_basis(report: dict, quality: str) -> str:
    """Taille d'image sur laquelle estimer le coût : 1080p si le rapport l'a mesuré, sinon la plus grande mesurée."""
    frames = {row["frame"] for row in report["estimate"] if row["quality"] == quality}
    frames &= {row["frame"] for row in report["synthesize"]}
    if not frames:
        raise SystemExit(f"le rapport n'a ni estimation « {quality} » ni synthèse pour une même taille d'image")
    return "1920x1080" if "1920x1080" in frames else max(frames, key=lambda text: math.prod(int(part) for part in text.split("x")))


def cost_markdown(report: dict, quality: str = "balanced", fps: int = 30) -> str:
    """Coût **estimé** d'une seconde de média source à chaque ratio, calculé depuis les mesures de ce même rapport.

    Une paire coûte la même chose quel que soit le ralenti ; ce qui croît comme ``1 / ratio``, c'est le nombre d'images à fabriquer."""
    frame = _cost_basis(report, quality)
    estimate = next(row for row in report["estimate"] if row["frame"] == frame and row["quality"] == quality)
    synthesis = next(row for row in report["synthesize"] if row["frame"] == frame)
    pair, image = float(estimate["seconds"]), float(synthesis["flow_synthesis_s"])
    rows = []
    for ratio in EXTREME_RATIOS:
        fabricated = fps * (round(1 / ratio) - 1)
        output = fps * round(1 / ratio)
        seconds = fps * pair + fabricated * image
        rows.append([f"{ratio * 100:g} %", str(output), f"{fabricated} ({100 * fabricated / output:.0f} %)",
                     f"{fps * pair:.0f} s", f"{fabricated * image:.0f} s", f"{seconds:.0f} s ({seconds / 60:.1f} min)"])
    return _table(
        ["Ralenti", "Images de sortie", "Images fabriquées", f"Analyse ({fps} paires)", "Synthèse", f"Total, 1 s de source en {frame}"],
        rows,
    )


def update_document(document: Path, report: dict) -> None:
    """Réécrit ce qui est entre ``<!-- BENCH:NOM -->`` et ``<!-- /BENCH:NOM -->`` ; un marqueur absent est une erreur."""
    text = document.read_text(encoding="utf-8")
    for name, render in (("MEASURES", measures_markdown), ("ACCURACY", accuracy_markdown), ("EXTREME", extreme_markdown),
                         ("COST", cost_markdown)):
        start, end = f"<!-- BENCH:{name} -->", f"<!-- /BENCH:{name} -->"
        head, found, rest = text.partition(start)
        body, closing, tail = rest.partition(end)
        if not found or not closing:
            raise SystemExit(f"{document}: marqueur {start} … {end} introuvable")
        text = f"{head}{start}\n{render(report)}\n{end}{tail}"
    document.write_text(text, encoding="utf-8")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path)
    parser.add_argument("--quick", action="store_true", help="petit jeu de mesures (tests)")
    parser.add_argument("--sizes", default="640x360,1280x720,1920x1080,3840x2160")
    parser.add_argument("--render-sizes", default="1280x720")
    parser.add_argument("--update-doc", type=Path, help="réécrit les tableaux de ce document (marqueurs BENCH) depuis le rapport")
    parser.add_argument("--from-json", type=Path, help="ne mesure rien : relit un rapport déjà écrit (avec --update-doc)")
    parser.add_argument("--refresh-quality", action="store_true",
                        help="avec --from-json : refait seulement les sections de qualité (précision, ralenti extrême), "
                             "déterministes et rapides, et réécrit le fichier ; les temps mesurés restent ceux du rapport")
    parser.add_argument("--worker", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.worker:
        _worker(args.worker)
        return 0
    if args.from_json:
        if not (args.update_doc or args.refresh_quality):
            parser.error("--from-json n'a de sens qu'avec --update-doc ou --refresh-quality")
        stored = json.loads(args.from_json.read_text(encoding="utf-8"))
        if args.refresh_quality:
            stored["accuracy"], stored["extreme"] = accuracy(), extreme_slowdown()
            args.from_json.write_text(json.dumps(stored, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if args.update_doc:
            update_document(args.update_doc, stored)
        return 0
    if args.refresh_quality:
        parser.error("--refresh-quality demande --from-json")
    if args.quick:
        report = run(sizes=((320, 180),), qualities=("draft", "balanced"), render_sizes=((320, 180),))
    else:
        def parse(text: str):
            return tuple(tuple(int(part) for part in item.split("x")) for item in text.split(","))

        report = run(sizes=parse(args.sizes), render_sizes=parse(args.render_sizes))
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    if args.update_doc:
        update_document(args.update_doc, report)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
