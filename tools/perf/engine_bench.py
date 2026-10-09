"""Mesures du moteur de rendu de bout en bout : calques, graphe, export, segments d'aperçu fidèle.

Usage::

    QT_QPA_PLATFORM=offscreen python -m tools.perf.engine_bench --out docs/perf/engine-after.json
    python -m tools.perf.engine_bench --project ~/Movies/montage.kut      # un vrai montage (lecture seule)

Scénario ``social`` (déterministe, médias générés par FFmpeg) : 1080×1920, 30 i/s, 12 s ; trois plans dont un
Ken Burns, un sous-titre karaoké contouré sur toute la durée, un titre animé, quatre flashs en Addition de 0,3 s,
du grain en Incrustation et une musique. C'est la forme des montages sociaux mesurés sur machine (titres, flashs,
lumière par-dessus quelques plans).

Mesures, le cache des calques vide puis plein :

- ``layers_cold_s`` / ``layers_warm_s`` : préparation des calques (rendu Qt, PNG) et construction du graphe ;
- ``export`` : l'export FFmpeg réel (H.264 CPU, CRF 18, préréglage ``medium``) : durée, temps CPU, mémoire maximale ;
- ``export_cold_s`` : ``layers_cold_s`` + durée de l'export, ce qu'attend l'utilisateur au premier export ;
- ``segments`` : trois segments d'aperçu fidèle de 2 s (début, milieu, fin), qualité standard.

Le banc ne lit que des API présentes avant et après les changements qu'il mesure (``ExportEngine._build_command``,
``build_preview_command``) : le lancer depuis un autre arbre de travail donne la référence. Le temps CPU et la
mémoire viennent de ``wait4`` (macOS, Linux) ; sous Windows, seule la durée est mesurée.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

W, H, FPS, LENGTH = 1080, 1920, 30, 12.0


def _run(command: list[str]) -> dict:
    """Lance ``command`` (sortie ignorée) : durée, temps CPU et mémoire maximale du processus."""
    started = time.perf_counter()
    process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if hasattr(os, "wait4"):
        _pid, status, usage = os.wait4(process.pid, 0)
        code = os.waitstatus_to_exitcode(status)
        rss = usage.ru_maxrss / (1024 * 1024 if sys.platform == "darwin" else 1024)
        measured = {"cpu_s": round(usage.ru_utime + usage.ru_stime, 2), "rss_mb": round(rss)}
    else:
        code = process.wait()
        measured = {}
    error = process.stderr.read().decode("utf-8", "replace") if process.stderr else ""
    if code != 0:
        raise RuntimeError(error[-800:])
    return {"wall_s": round(time.perf_counter() - started, 2), **measured}


def _media(workdir: Path) -> dict[str, Path]:
    clips = {}
    for index, source in enumerate(("testsrc2", "mandelbrot", "testsrc2")):
        path = workdir / f"plan{index}.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"{source}=s={W}x{H}:r={FPS}",
                        "-t", "4", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path)],
                       check=True)
        clips[f"plan{index}"] = path
    music = workdir / "musique.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency=220:duration={LENGTH}",
                    str(music)], check=True)
    clips["musique"] = music
    return clips


def social_project(media: dict[str, Path]):
    from core.blend_modes import BlendMode
    from core.compositing import Compositing
    from core.graphics import add_graphic_clip, update_graphic
    from core.project_model import Clip, MediaAsset, Project, Track
    from core.text_animations import apply_text_animation
    from core.visual_effects import TransformKeyframe

    project = Project(name="Banc social", width=W, height=H, fps=float(FPS))
    clips = []
    for index in range(3):
        project.media_assets.append(MediaAsset(f"plan{index}", str(media[f"plan{index}"]), f"plan{index}", 4.0, W, H,
                                               float(FPS), "video"))
        clips.append(Clip(f"c{index}", f"plan{index}", "V1", index * 4.0, 0.0, 4.0))
    clips[1].transform_keyframes = [TransformKeyframe("scale", 0.0, 1.0), TransformKeyframe("scale", 4.0, 1.1)]
    project.media_assets.append(MediaAsset("musique", str(media["musique"]), "musique", LENGTH, 0, 0, 0.0, "audio",
                                           True))
    project.tracks.extend([
        Track("V1", "V1", "video", clips=clips),
        Track("A1", "A1", "audio", clips=[Clip("m", "musique", "A1", 0.0, 0.0, LENGTH)]),
    ])
    caption = add_graphic_clip(project, "text", timeline_start=0.0, duration=LENGTH)
    for field, value in (("text", "Le moteur rend chaque mot du sous-titre à son instant"), ("font_size", 72),
                         ("stroke_width", 8)):
        update_graphic(caption, field, value)
    apply_text_animation(caption, "karaoke")
    title = add_graphic_clip(project, "text", timeline_start=1.0, duration=2.0)
    update_graphic(title, "text", "KUT STUDIO")
    update_graphic(title, "font_size", 120)
    apply_text_animation(title, "bounce")
    for start in (2.0, 5.0, 8.0, 10.5):
        flash = add_graphic_clip(project, "light", timeline_start=start, duration=0.3)
        update_graphic(flash, "light_kind", "flash")
        flash.compositing = Compositing(blend_mode=BlendMode.ADD)
    grain = add_graphic_clip(project, "light", timeline_start=0.0, duration=LENGTH)
    update_graphic(grain, "light_kind", "grain")
    grain.compositing = Compositing(blend_mode=BlendMode.OVERLAY)
    return project


def measure(project, workdir: Path, *, segments: int = 3) -> dict:
    from core.export_engine import ExportEngine, ExportFormat, ExportPreset, ExportRequest
    from core.filter_graph import build_preview_command
    from core.preview_segments import build_segment_job
    from core.render_plan import build_render_plan

    os.environ["KUT_STUDIO_CACHE_DIR"] = str(Path(tempfile.mkdtemp(prefix="cache-", dir=workdir)))
    plan = build_render_plan(project)
    width, height, fps = project.width, project.height, project.fps
    result: dict = {"size": [width, height, fps, round(plan.duration, 2)],
                    "layers": [len(plan.video_layers), len(plan.graphics_layers), len(plan.audio_layers)]}
    started = time.perf_counter()
    ExportEngine._build_filter_complex(plan, width, height, fps, None)
    result["layers_cold_s"] = round(time.perf_counter() - started, 2)
    started = time.perf_counter()
    ExportEngine._build_filter_complex(plan, width, height, fps, None)
    result["layers_warm_s"] = round(time.perf_counter() - started, 2)
    engine = ExportEngine()
    engine._prepare_temporary_files(plan)
    request = ExportRequest(plan, str(workdir / "export.mp4"), ExportFormat.MP4_H264,
                            ExportPreset("banc", (width, height), 18, "192k"), fps=fps, hardware="cpu")
    try:
        result["export"] = _run(engine._build_command(request))
    finally:
        engine._cleanup_temporary_files()
    result["export_cold_s"] = round(result["layers_cold_s"] + result["export"]["wall_s"], 2)
    count = max(1, int(plan.duration // 2))
    measured = {}
    for index in sorted({0, count // 2, max(0, count - 1)})[:segments]:
        job = build_segment_job(project, index, quality="standard")
        if job is None:
            continue
        temporary: list[str] = []
        command = build_preview_command(job.plan, width=job.width, height=job.height, fps=job.fps, quality="standard",
                                        start=job.start, duration=job.duration, temporary_files=temporary,
                                        output_path=str(workdir / f"segment{index}.mp4"))
        measured[str(index)] = _run(command)
        for path in temporary:
            Path(path).unlink(missing_ok=True)
    result["segments"] = measured
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path)
    parser.add_argument("--project", type=Path, action="append", default=[], help="vrai montage .kut (lecture seule)")
    args = parser.parse_args(argv)
    if not shutil.which("ffmpeg"):
        print("FFmpeg introuvable : le banc du moteur exporte réellement.", file=sys.stderr)
        return 1
    from PySide6.QtGui import QGuiApplication

    _app = QGuiApplication.instance() or QGuiApplication([])
    report: dict = {
        "machine": f"{platform.system()} {platform.machine()}, {os.cpu_count()} cœurs",
        "python": platform.python_version(),
        "ffmpeg": subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True).stdout.split("\n")[0],
        "scenarios": {},
    }
    with tempfile.TemporaryDirectory(prefix="kut-engine-bench-") as folder:
        workdir = Path(folder)
        report["scenarios"]["social"] = measure(social_project(_media(workdir)), workdir)
        print("social", json.dumps(report["scenarios"]["social"]))
        for path in args.project:
            from core.project_io import load_project

            report["scenarios"][path.stem] = measure(load_project(str(path)), workdir)
            print(path.stem, json.dumps(report["scenarios"][path.stem]))
    if args.out:
        args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
