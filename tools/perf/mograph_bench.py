"""Mesures du moteur motion graphics : aperçu, balayage, interface, export.

Usage::

    QT_QPA_PLATFORM=offscreen python -m tools.perf.mograph_bench --out docs/perf/mograph.json
    python -m tools.perf.mograph_bench --ffmpeg      # avec un export FFmpeg réel

Scénarios (calques 1920×1080, séquence à 30 i/s) :

- ``layers:N`` : N calques (textes et formes alternés), tous animés ;
- ``groups`` : 50 calques répartis dans 5 groupes, parentés à des contrôleurs ;
- ``masks`` : 50 calques portant chacun 3 masques adoucis (add / subtract / intersect) ;
- ``adjust`` : 50 calques et 3 adjustment layers ;
- ``blur:N`` : N calques animés avec flou de mouvement (aperçu standard / export).

Mesures (médiane, millisecondes) :

- ``preview_frame`` : une image du viewer interactif (960×540, qualité standard) ;
- ``scrub_frame`` : une image à un instant différent à chaque appel (balayage) ;
- ``ui_refresh`` : arbre du panneau Calques + scène de la séquence (sélection, poignées) ;
- ``graph_build`` / ``stream_frames`` : construction du graphe d'export 1280×720 sur 2 s
  (rendu Qt des images incluses), à froid puis avec le cache d'images ;
- ``export`` (``--ffmpeg``) : export FFmpeg réel de 2 s.

Les durées absolues dépendent de la machine : le banc documente des ordres
de grandeur et la croissance avec le nombre de calques.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.compositing import Compositing, Mask, MaskMode, MaskShape  # noqa: E402
from core.effects_model import EffectType, create_effect  # noqa: E402
from core.graphics import add_graphic_clip, update_graphic  # noqa: E402
from core.motion_blur import MotionBlurSettings  # noqa: E402
from core.project_model import Project  # noqa: E402
from core.visual_effects import ClipTransform, TransformKeyframe  # noqa: E402

DURATION = 2.0


def _median(samples: list[float]) -> float:
    return round(statistics.median(samples), 3)


def _timed(callback, repeat: int = 5) -> float:
    samples = []
    for _ in range(repeat):
        started = time.perf_counter()
        callback()
        samples.append((time.perf_counter() - started) * 1000.0)
    return _median(samples)


def _layer(project: Project, index: int, *, motion_blur: bool = False):
    kind = "text" if index % 2 == 0 else "shape"
    clip = add_graphic_clip(project, kind, timeline_start=0.0, duration=DURATION, shape="rounded_rectangle")
    if kind == "text":
        update_graphic(clip, "text", f"Calque {index}")
        update_graphic(clip, "font_size", 28)
        update_graphic(clip, "width", 300)
        update_graphic(clip, "height", 60)
    else:
        update_graphic(clip, "width", 160)
        update_graphic(clip, "height", 90)
    x = ((index * 37) % 80 - 40) / 100.0
    y = ((index * 53) % 70 - 35) / 100.0
    clip.transform = ClipTransform(position_x=x, position_y=y)
    clip.transform_keyframes = [
        TransformKeyframe("rotation", 0.0, 0.0), TransformKeyframe("rotation", DURATION, 45.0 + index),
        TransformKeyframe("position_x", 0.0, x), TransformKeyframe("position_x", DURATION, x + 0.1),
    ]
    if motion_blur:
        update_graphic(clip, "motion_blur", True)
    return clip


def scenario(name: str) -> Project:
    project = Project(name=name, width=1920, height=1080, fps=30.0)
    kind, _, count = name.partition(":")
    if kind == "layers":
        for index in range(int(count)):
            _layer(project, index)
    elif kind == "groups":
        from core.mograph_layers import group_layers, set_parent

        clips = [_layer(project, index) for index in range(50)]
        for group_index in range(5):
            members = clips[group_index * 10:(group_index + 1) * 10]
            controller = add_graphic_clip(project, "null", timeline_start=0.0, duration=DURATION)
            controller.transform_keyframes = [
                TransformKeyframe("scale", 0.0, 1.0), TransformKeyframe("scale", DURATION, 1.2),
            ]
            for member in members[:5]:
                set_parent(project, member.id, controller.id, keep_visual=False)
            group_layers(project, [m.id for m in members])
    elif kind == "masks":
        for index in range(50):
            clip = _layer(project, index)
            clip.compositing = Compositing(masks=(
                Mask(shape=MaskShape.ELLIPSE, width=0.9, height=0.9, feather=0.1),
                Mask(width=0.3, height=0.3, mode=MaskMode.SUBTRACT, feather=0.05),
                Mask(width=0.8, height=0.8, mode=MaskMode.INTERSECT),
            ))
    elif kind == "adjust":
        for index in range(50):
            _layer(project, index)
            if index % 17 == 16:
                adjustment = add_graphic_clip(project, "adjustment", timeline_start=0.0, duration=DURATION)
                adjustment.effects = [create_effect(EffectType.BLACK_AND_WHITE)]
    elif kind == "blur":
        for index in range(int(count)):
            _layer(project, index, motion_blur=True)
        project.active_sequence.motion_blur = MotionBlurSettings(samples=8)
    else:
        raise ValueError(name)
    return project


def measure(name: str, *, ffmpeg: bool) -> dict:
    from core.export_engine import ExportEngine, input_arguments
    from core.mograph_layers import layer_tree, scene_for_project
    from core.mograph_raster import MographRenderer, scene_for_plan
    from core.render_plan import build_render_plan

    project = scenario(name)
    plan = build_render_plan(project)
    scene = scene_for_plan(plan)
    preview = MographRenderer(scene, 960, 540, fps=30.0, quality="standard", motion_blur=plan.motion_blur)
    ids = scene.top_level()
    result: dict = {"layers": len(plan.graphics_layers)}
    result["preview_frame"] = _timed(lambda: preview.render(ids, 1.0))
    times = iter([0.05 + i * 0.137 for i in range(200)])
    result["scrub_frame"] = _timed(lambda: preview.render(ids, next(times) % DURATION), repeat=9)
    result["ui_refresh"] = _timed(lambda: (layer_tree(project), scene_for_project(project).evaluate(ids[-1], 1.0)))
    with tempfile.TemporaryDirectory() as cache:
        os.environ["KUT_STUDIO_CACHE_DIR"] = cache
        started = time.perf_counter()
        graph, video, audio, inputs = ExportEngine._build_filter_complex(plan, 1280, 720, 30, None)
        result["graph_build_cold"] = round((time.perf_counter() - started) * 1000.0, 1)
        frames = len(list(Path(cache, "mograph").glob("f-*.png")))
        result["stream_frames"] = frames
        result["graph_build_cached"] = _timed(
            lambda: ExportEngine._build_filter_complex(plan, 1280, 720, 30, None), repeat=3
        )
        if ffmpeg and shutil.which("ffmpeg"):
            out = Path(cache) / "out.mp4"
            command = ["ffmpeg", "-y", "-loglevel", "error"]
            for path in inputs:
                command += input_arguments(path)
            command += [
                "-filter_complex", graph, "-map", f"[{video}]", "-map", f"[{audio}]",
                "-c:v", "libx264", "-preset", "veryfast", "-t", str(DURATION), str(out),
            ]
            started = time.perf_counter()
            completed = subprocess.run(command, capture_output=True, text=True)
            result["export_ffmpeg"] = round((time.perf_counter() - started) * 1000.0, 1)
            result["export_ok"] = completed.returncode == 0
    return result


SCENARIOS = ("layers:10", "layers:50", "layers:100", "groups", "masks", "adjust", "blur:10", "blur:50")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path)
    parser.add_argument("--ffmpeg", action="store_true")
    parser.add_argument("--only", nargs="*", default=None)
    args = parser.parse_args(argv)
    from PySide6.QtGui import QGuiApplication

    _app = QGuiApplication.instance() or QGuiApplication([])
    results = {
        "machine": f"{platform.system()} {platform.machine()}",
        "python": platform.python_version(),
        "scenarios": {},
    }
    for name in args.only or SCENARIOS:
        results["scenarios"][name] = measure(name, ffmpeg=args.ffmpeg)
        print(name, json.dumps(results["scenarios"][name]))
    if args.out:
        args.out.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
