"""Benchmarks reproductibles de la couche de performance.

Usage::

    QT_QPA_PLATFORM=offscreen python -m tools.perf.bench --out docs/perf/after.json
    python -m tools.perf.bench --compare docs/perf/baseline.json docs/perf/after.json

Principe : mêmes projets synthétiques (:mod:`tools.perf.synthetic`),
même protocole, **médiane** de plusieurs répétitions (la première,
chaude, est écartée). Les métriques absentes d'une version (API qui
n'existait pas encore) sont ignorées au lieu de faire échouer le banc.
Les durées sont en millisecondes sauf mention contraire.

Ce n'est pas un test : les temps absolus dépendent de la machine. Les
tests de ``tests/test_performance.py`` vérifient la **complexité**
(le coût ne croît pas avec la taille du projet), pas des durées.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.perf.synthetic import KINDS, build_project, write_dummy_media  # noqa: E402

SIZES = (100, 1000, 10000)
WINDOW_MAX = 10000


def _timed(fn, repeats: int = 5, warmup: int = 1) -> float:
    """Médiane (ms) de ``fn()`` sur ``repeats`` exécutions après ``warmup``."""
    for _ in range(warmup):
        fn()
    samples = []
    for _ in range(repeats):
        started = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - started) * 1000.0)
    return statistics.median(samples)


def _per_call(fn, calls: int, repeats: int = 3) -> float:
    """Médiane (ms) du coût **moyen d'un appel**, ``fn`` en exécutant ``calls``."""
    return _timed(fn, repeats=repeats) / max(1, calls)


# ---------------------------------------------------------------------------
# Cœur (sans Qt)
# ---------------------------------------------------------------------------


def bench_core(kind: str, size: int, workdir: Path) -> dict[str, float]:
    from core.project_io import load_project, save_project
    from core.render_plan import build_render_plan
    from core.timeline_evaluator import evaluate_timeline
    from core.timeline_index import build_timeline_index
    from core.timeline_operations import snap_timeline_position
    from core.timeline_view_model import build_clip_views

    results: dict[str, float] = {}
    project = build_project(size, kind)
    path = workdir / f"{kind}-{size}.kut"
    results["save_kut_ms"] = _timed(lambda: save_project(project, str(path)), repeats=3)
    results["load_project_ms"] = _timed(lambda: load_project(str(path)), repeats=3)
    results["clip_views_ms"] = _timed(lambda: build_clip_views(project), repeats=3)
    results["index_build_ms"] = _timed(lambda: build_timeline_index(project), repeats=3)
    index = build_timeline_index(project)
    duration = max(index.duration, 1.0)
    instants = [duration * (i + 0.37) / 200 for i in range(200)]

    def active_queries():
        for instant in instants:
            index.active_at(project, instant)

    results["active_at_ms"] = _per_call(active_queries, len(instants))
    sample = instants[:20]

    def full_scan():
        for instant in sample:
            evaluate_timeline(project, instant)

    results["evaluate_timeline_ms"] = _per_call(full_scan, len(sample), repeats=3)
    results["render_plan_ms"] = _timed(lambda: build_render_plan(project), repeats=3)
    try:
        from core.filter_graph import fingerprint_plan

        plan = build_render_plan(project)
        results["fingerprint_ms"] = _timed(
            lambda: fingerprint_plan(plan, width=1920, height=1080, fps=25, quality="standard"),
            repeats=3,
        )
    except Exception:  # pragma: no cover - API absente
        pass
    probes = [duration * (i + 0.5) / 100 for i in range(100)]

    def snaps():
        for instant in probes:
            snap_timeline_position(project, instant, 0.1, playhead_seconds=duration / 2)

    results["snap_ms"] = _per_call(snaps, len(probes))
    results["py_peak_mb"] = _memory_peak_mb(kind, size)
    return results


def _memory_peak_mb(kind: str, size: int) -> float:
    """Pic mémoire Python du projet + index + vues, **mesuré à part** des temps.

    ``tracemalloc`` ralentit fortement l'interpréteur : l'activer pendant
    les mesures de durée les fausserait (×3 à ×5).
    """
    from core.timeline_index import build_timeline_index
    from core.timeline_view_model import build_clip_views

    tracemalloc.start()
    try:
        project = build_project(size, kind)
        build_timeline_index(project)
        build_clip_views(project)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return peak / (1024 * 1024)


# ---------------------------------------------------------------------------
# Timeline (Qt)
# ---------------------------------------------------------------------------

_APP = None


def _qt_app():
    global _APP
    from PySide6.QtWidgets import QApplication

    _APP = QApplication.instance() or QApplication([])
    return _APP


def bench_timeline(kind: str, size: int, media: str) -> dict[str, float]:
    from PySide6.QtCore import QRect

    import ui.timeline_panel_mixins.previews as previews_module
    from core.studio_runtime import StudioRuntime
    from ui.timeline_panel import TimelinePanel

    app = _qt_app()
    # Aucune extraction FFmpeg : on mesure la planification, pas le décodage.
    previews_module.extract_thumbnail = lambda *_a, **_k: b""
    previews_module.extract_waveform_peaks = lambda *_a, **_k: ()
    results: dict[str, float] = {}
    project = build_project(size, kind, media_path=media)
    panel = TimelinePanel(project)
    runtime = StudioRuntime(profile="balanced")
    panel.attach_runtime(runtime)
    panel.resize(1400, 520)
    panel.show()
    app.processEvents()
    results["panel_set_project_ms"] = _timed(lambda: panel.set_project(project), repeats=3)
    duration = max(panel.duration_seconds, 1.0)
    panel.set_timeline_duration(max(c.timeline_start + c.duration for t in project.tracks for c in t.clips))
    app.processEvents()
    panel.set_project(project)
    results["mounted_clips"] = float(panel.mounted_clip_count)
    results["clip_views"] = float(len(panel.clip_views))

    bar = panel.scroll.horizontalScrollBar()
    span = max(bar.maximum(), 1)
    # Les mesures se font à ~60 % du montage : au tout début, les clips visibles
    # sont aussi les premiers de la liste et les parcours linéaires semblent
    # bon marché (ils s'arrêtent tôt) — ce qui masquerait leur vrai coût.
    bar.setValue(int(span * 0.6))
    app.processEvents()
    panel.set_playhead_seconds(duration * 0.6)

    def scroll_steps():
        for step in range(60):
            bar.setValue(int(span * step / 60))

    results["scroll_step_ms"] = _per_call(scroll_steps, 60)
    base_value = int(span * 0.6)

    def scroll_smooth():
        # Défilement réaliste (molette / glisser lent) : quelques pixels à la fois ;
        # seuls les clips qui entrent / sortent de l'écran changent.
        for step in range(60):
            bar.setValue(base_value + step * 40)

    bar.setValue(base_value)
    app.processEvents()
    results["scroll_smooth_step_ms"] = _per_call(scroll_smooth, 60)
    bar.setValue(int(span * 0.6))
    app.processEvents()

    def zoom_steps():
        for _ in range(10):
            panel.zoom_in()
        for _ in range(10):
            panel.zoom_out()

    results["zoom_step_ms"] = _per_call(zoom_steps, 20)

    def playhead_steps():
        for step in range(200):
            panel.set_playhead_seconds(duration * step / 200)

    results["playhead_step_ms"] = _per_call(playhead_steps, 200)
    results["layout_refresh_ms"] = _timed(panel._layout_children, repeats=5)
    results["refresh_clip_widgets_ms"] = _timed(panel.refresh_clip_widgets, repeats=3)
    results["schedule_previews_ms"] = _timed(panel._schedule_previews, repeats=5)
    first_id = panel.clip_views[len(panel.clip_views) // 2].id if panel.clip_views else ""

    def select_one():
        panel._set_selection([first_id], first_id, announce=False)

    results["select_one_ms"] = _timed(select_one, repeats=5)
    ids = [view.id for view in panel.clip_views]
    last_ids = ids[len(ids) // 2 : len(ids) // 2 + 20]

    def find_views():
        for clip_id in last_ids:
            panel.find_view_by_id(clip_id)

    results["find_view_ms"] = _per_call(find_views, max(1, len(last_ids)))
    panel.begin_marquee(panel.timeline_grid.rect().topLeft())

    def marquee():
        panel._marquee_origin = panel.timeline_grid.rect().topLeft()
        panel._marquee.show()
        panel.finish_marquee(panel.timeline_grid.rect().bottomRight())

    results["select_all_marquee_ms"] = _timed(marquee, repeats=3)
    snap_probe = panel.playhead_seconds

    def snap_calls():
        for step in range(50):
            panel.snap_position(snap_probe + step * 0.37, excluded_clip_id=first_id)

    results["snap_position_ms"] = _per_call(snap_calls, 50)
    del QRect
    runtime.shutdown()
    panel.close()
    panel.deleteLater()
    app.processEvents()
    return results



# ---------------------------------------------------------------------------
# Fenêtre complète (chargement réel, déplacement de la tête en pause)
# ---------------------------------------------------------------------------


def bench_window(kind: str, size: int, media: str, workdir: Path) -> dict[str, float]:
    """Mesures de bout en bout sur une vraie ``MainWindow`` (rendu FFmpeg neutralisé)."""
    from PySide6.QtWidgets import QMessageBox

    from core.project_io import save_project
    from ui.main_window import MainWindow

    app = _qt_app()
    QMessageBox.information = staticmethod(lambda *_a, **_k: None)
    QMessageBox.critical = staticmethod(lambda *_a, **_k: None)
    QMessageBox.question = staticmethod(lambda *_a, **_k: QMessageBox.Yes)
    project = build_project(size, kind, media_path=media)
    path = workdir / f"window-{kind}-{size}.kut"
    save_project(project, str(path))
    window = MainWindow()
    window.resize(1500, 900)
    window.show()
    app.processEvents()
    timer = getattr(window, "_preview_pump_timer", None)
    if timer is not None:
        timer.stop()  # aucun rendu FFmpeg pendant la mesure
    results: dict[str, float] = {}
    results["window_load_project_ms"] = _timed(
        lambda: window._load_project_from_path(str(path)), repeats=3
    )
    app.processEvents()
    duration = max(window.timeline_panel.duration_seconds, 1.0)
    instants = [duration * (i + 0.41) / 40 for i in range(40)]

    def seeks():
        for instant in instants:
            window.seek_to_position(instant)

    results["seek_paused_ms"] = _per_call(seeks, len(instants), repeats=3)
    window.close()
    window.deleteLater()
    app.processEvents()
    return results

# ---------------------------------------------------------------------------
# Caches
# ---------------------------------------------------------------------------


def bench_caches(workdir: Path) -> dict[str, float]:
    from core.cache_store import MemoryCache
    from core.preview_cache import DiskPreviewCache, PreviewSegmentKey

    results: dict[str, float] = {}
    memory = MemoryCache(max_bytes=256 * 1024 * 1024, max_entries=200_000)
    keys = [f"thumb:/m/{i}:0:160" for i in range(20_000)]

    def fill():
        for key in keys:
            memory.put(key, b"x", size_bytes=64)

    results["memory_put_us"] = _per_call(fill, len(keys)) * 1000.0
    results["memory_get_hot_us"] = _per_call(
        lambda: [memory.get(k) for k in keys], len(keys)
    ) * 1000.0
    results["memory_get_cold_us"] = _per_call(
        lambda: [memory.get(k + "x") for k in keys], len(keys)
    ) * 1000.0

    for count in (200, 2000):
        directory = workdir / f"segments-{count}"
        cache = DiskPreviewCache(directory=directory, budget_bytes=10**12)
        source = workdir / "seg.mp4"
        source.write_bytes(b"\x00" * 2048)
        seg_keys = [
            PreviewSegmentKey(f"c{i}", float(i), float(i) + 2.0, "standard", "h")
            for i in range(count)
        ]
        for key in seg_keys[:-1]:
            cache.path_for(key).write_bytes(b"\x00" * 2048)
        started = time.perf_counter()
        cache.store(seg_keys[-1], str(source))
        results[f"segment_store_{count}_ms"] = (time.perf_counter() - started) * 1000.0
        # Régime établi : stockages suivants, l'index éventuel étant déjà chargé.
        extra = [
            PreviewSegmentKey(f"x{i}", float(i), float(i) + 2.0, "standard", "h")
            for i in range(20)
        ]
        started = time.perf_counter()
        for key in extra:
            cache.store(key, str(source))
        results[f"segment_store_steady_{count}_ms"] = (time.perf_counter() - started) * 1000.0 / len(extra)
        results[f"segment_lookup_hot_{count}_us"] = _per_call(
            lambda: [cache.lookup(k) for k in seg_keys[:200]], 200
        ) * 1000.0
        results[f"segment_stats_{count}_ms"] = _timed(cache.stats, repeats=3)
    return results


# ---------------------------------------------------------------------------
# Miniatures réelles (FFmpeg)
# ---------------------------------------------------------------------------


def bench_thumbnails(workdir: Path) -> dict[str, float]:
    import shutil
    import subprocess

    from core.cache_store import MemoryCache
    from core.media_previews import extract_thumbnail, thumbnail_cache_key

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return {}
    source = workdir / "thumb-source.mp4"
    subprocess.run(
        [ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi", "-i",
         "testsrc=s=640x360:r=25:d=12", "-pix_fmt", "yuv420p", str(source)],
        check=True, capture_output=True,
    )
    times = [i * 1.0 for i in range(12)]
    cache = MemoryCache()

    def cold():
        for instant in times:
            data = extract_thumbnail(str(source), instant, 160)
            cache.put(thumbnail_cache_key(str(source), instant, 160), data, size_bytes=len(data or b"x"))

    def hot():
        for instant in times:
            cache.get(thumbnail_cache_key(str(source), instant, 160))

    return {
        "thumbnail_cold_ms": _per_call(cold, len(times), repeats=2),
        "thumbnail_hot_us": _per_call(hot, len(times), repeats=5) * 1000.0,
    }


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


ALL_SECTIONS = ("core", "timeline", "window", "caches", "thumbnails")


def run(
    sizes=SIZES,
    kinds=KINDS,
    *,
    ui_max: int = 10000,
    quick: bool = False,
    sections=ALL_SECTIONS,
) -> dict:
    report: dict = {
        "meta": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "cpus": os.cpu_count(),
            "sizes": list(sizes),
            "kinds": list(kinds),
        },
        "core": {},
        "timeline": {},
        "window": {},
        "caches": {},
        "thumbnails": {},
    }
    with tempfile.TemporaryDirectory(prefix="kut-bench-") as tmp:
        workdir = Path(tmp)
        media = write_dummy_media(workdir / "media")
        for kind in kinds:
            for size in sizes:
                label = f"{kind}:{size}"
                if "core" in sections:
                    print(f"[core]     {label}", flush=True)
                    report["core"][label] = bench_core(kind, size, workdir)
                ui_ok = size <= ui_max and not (quick and size > 1000)
                if "timeline" in sections and ui_ok:
                    print(f"[timeline] {label}", flush=True)
                    report["timeline"][label] = bench_timeline(kind, size, media)
                if "window" in sections and ui_ok and kind != "long_8t" and size <= WINDOW_MAX:
                    print(f"[window]   {label}", flush=True)
                    report["window"][label] = bench_window(kind, size, media, workdir)
        if "caches" in sections:
            print("[caches]", flush=True)
            report["caches"] = bench_caches(workdir)
        if "thumbnails" in sections:
            print("[thumbnails]", flush=True)
            report["thumbnails"] = bench_thumbnails(workdir)
    return report


def merge_reports(existing: dict, fresh: dict) -> dict:
    """Met à jour ``existing`` avec les mesures de ``fresh`` (sans effacer le reste)."""
    merged = json.loads(json.dumps(existing))
    for section, content in fresh.items():
        if section == "meta" or not content:
            continue
        target = merged.setdefault(section, {})
        if section in ("caches", "thumbnails"):
            target.update(content)
        else:
            for scenario, metrics in content.items():
                target.setdefault(scenario, {}).update(metrics)
    return merged


def _format_value(value: float) -> str:
    return f"{value:,.3f}" if value < 10 else f"{value:,.1f}"


def compare(before: dict, after: dict) -> str:
    """Tableau avant / après ; ``x`` = facteur de gain (>1 = plus rapide)."""
    lines = ["| Section | Scénario | Mesure | Avant | Après | Gain |", "| --- | --- | --- | ---: | ---: | ---: |"]
    for section in ("core", "timeline", "window", "caches", "thumbnails"):
        old, new = before.get(section, {}), after.get(section, {})
        flat_old = _flatten(old, section == "caches" or section == "thumbnails")
        flat_new = _flatten(new, section == "caches" or section == "thumbnails")
        for key in flat_old:
            if key not in flat_new:
                continue
            scenario, metric = key
            a, b = flat_old[key], flat_new[key]
            if metric in ("mounted_clips", "clip_views"):
                continue
            gain = f"×{a / b:.1f}" if b > 0 and a > 0 else "—"
            lines.append(
                f"| {section} | {scenario} | {metric} | {_format_value(a)} | {_format_value(b)} | {gain} |"
            )
    return "\n".join(lines)


def _flatten(section: dict, flat: bool) -> dict[tuple[str, str], float]:
    out: dict[tuple[str, str], float] = {}
    if flat:
        for metric, value in section.items():
            out[("-", metric)] = value
        return out
    for scenario, metrics in section.items():
        for metric, value in metrics.items():
            out[(scenario, metric)] = value
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", help="fichier JSON de sortie")
    parser.add_argument("--sizes", type=int, nargs="+", default=list(SIZES))
    parser.add_argument("--kinds", nargs="+", default=list(KINDS), choices=KINDS)
    parser.add_argument("--quick", action="store_true", help="UI limitée à 1 000 clips")
    parser.add_argument("--sections", nargs="+", default=list(ALL_SECTIONS), choices=ALL_SECTIONS)
    parser.add_argument("--merge", action="store_true", help="fusionne dans --out au lieu de l'écraser")
    parser.add_argument("--compare", nargs=2, metavar=("AVANT", "APRES"))
    args = parser.parse_args(argv)
    if args.compare:
        before = json.loads(Path(args.compare[0]).read_text(encoding="utf-8"))
        after = json.loads(Path(args.compare[1]).read_text(encoding="utf-8"))
        print(compare(before, after))
        return 0
    report = run(tuple(args.sizes), tuple(args.kinds), quick=args.quick, sections=tuple(args.sections))
    if args.merge and args.out and Path(args.out).exists():
        report = merge_reports(json.loads(Path(args.out).read_text(encoding="utf-8")), report)
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        print(f"→ {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
