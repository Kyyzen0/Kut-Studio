"""Mesures du flux optique et du mélange d'images : estimation, synthèse, mémoire, cache, et rendu de bout en bout.

Usage::

    python -m tools.perf.flow_bench --out docs/perf/optical-flow.json --update-doc docs/optical-flow.md
    python -m tools.perf.flow_bench --from-json docs/perf/optical-flow.json --update-doc docs/optical-flow.md   # sans mesurer
    python -m tools.perf.flow_bench --quick                         # petit jeu, sert aux tests
    python -m tools.perf.flow_bench --sizes 1280x720,1920x1080

Quatre familles de mesures ; les trois premières chacune dans un **sous-processus** (mémoire isolée) :

``estimate``    une paire d'images analysée (aller et retour), par résolution et par qualité : temps, pic de mémoire, taille
                de l'analyse **stockée** dans le cache, confiance mesurée ;
``synthesize``  une image intermédiaire fabriquée à la pleine résolution, flux optique contre mélange simple ;
``render``      un clip de 2 s ralenti à 25 % (240 % de durée) rendu de bout en bout par les trois modes : temps, images par
                seconde, taille du flux préparé, pic de mémoire ;
``accuracy``    des scènes synthétiques à **vérité terrain exacte** (``tests/flow_scenes.py``) : erreur sur la région de l'objet,
                flux contre mélange simple.

Les images d'analyse sont synthétiques mais **texturées et en mouvement** (texture lisse, objet qui se déplace de plusieurs
pixels) : un champ uniforme serait trivial. Les temps dépendent de la machine ; les tests vérifient les invariants (la qualité
coûte, le cache de vecteurs rend l'analyse suivante gratuite, la mémoire ne dépend pas du nombre d'images).
"""

from __future__ import annotations

import argparse
import json
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


def accuracy() -> list[dict]:
    """Erreur (niveaux sur 255, sur la région de l'objet) et recouvrement du flux contre le mélange, moyennés sur ``t = ¼, ½, ¾``."""
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
        rows.append({
            "scene": name, "flow_error": round(float(np.mean(flow_errors)), 2), "blend_error": round(float(np.mean(blend_errors)), 2),
            "flow_iou": round(float(np.mean(flow_overlaps)), 3), "blend_iou": round(float(np.mean(blend_overlaps)), 3),
        })
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


def run(*, sizes=((640, 360), (1280, 720), (1920, 1080)), qualities=QUALITIES, render_sizes=((1280, 720),)) -> dict:
    report: dict = {
        "meta": {"python": platform.python_version(), "machine": platform.machine(), "system": platform.system()},
        "estimate": [], "synthesize": [], "render": [], "accuracy": accuracy(),
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


def accuracy_markdown(report: dict) -> str:
    return _table(
        ["Scène", "Erreur flux", "Erreur mélange", "Recouvrement flux", "Recouvrement mélange"],
        [[row["scene"], f"{row['flow_error']:.1f}", f"{row['blend_error']:.1f}", f"{row['flow_iou']:.3f}", f"{row['blend_iou']:.3f}"]
         for row in report["accuracy"]],
    )


def update_document(document: Path, report: dict) -> None:
    """Réécrit ce qui est entre ``<!-- BENCH:NOM -->`` et ``<!-- /BENCH:NOM -->`` ; un marqueur absent est une erreur."""
    text = document.read_text(encoding="utf-8")
    for name, render in (("MEASURES", measures_markdown), ("ACCURACY", accuracy_markdown)):
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
    parser.add_argument("--worker", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.worker:
        _worker(args.worker)
        return 0
    if args.from_json:
        if not args.update_doc:
            parser.error("--from-json n'a de sens qu'avec --update-doc")
        update_document(args.update_doc, json.loads(args.from_json.read_text(encoding="utf-8")))
        return 0
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
