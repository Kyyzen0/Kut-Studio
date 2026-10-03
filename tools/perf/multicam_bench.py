"""Mesures du Multicam : plan de rendu, bascule d'angle, flux d'images des tuiles (FPS, CPU, mémoire, retard).

Usage::

    QT_QPA_PLATFORM=offscreen python -m tools.perf.multicam_bench --out docs/perf/multicam.json
    python -m tools.perf.multicam_bench --quick                 # petit jeu, sert aux tests
    python -m tools.perf.multicam_bench --feeds 4x1080p,8x4K+proxy --seconds 6

Sections :

* ``plan:A×C`` : A angles, C coupes. Construction du plan et du graphe FFmpeg, nombre de **compositions** réellement émises
  (une par angle *montré*, jamais une par coupe ni une par angle de la source), fichiers lus, plan d'un segment d'aperçu,
  empreinte de segment, évaluation temps réel (``active_at``) ;
* ``switch:A×C`` : coût d'une bascule d'angle (l'opération ``switch_angle`` seule, puis avec l'instantané d'historique) sur
  un montage de C coupes ; il ne doit pas croître avec C ;
* ``window`` (``--ui``) : une bascule complète dans la fenêtre (opération, historique, timeline) et le **retour immédiat**
  du moniteur (mise en évidence de la tuile cliquée) ;
* ``feeds:AxR`` : A angles de résolution R (``1080p`` ou ``4K``, avec ``+proxy`` : les tuiles lisent un proxy 480×270), lus
  en temps réel par des :class:`~core.multicam_feed.AngleFeed` pendant quelques secondes : images par seconde par angle,
  part de sondages en retard, CPU (en % d'un cœur, FFmpeg compris), mémoire (processus et plus gros FFmpeg), première image
  d'un angle froid, et coût d'un changement d'angle déjà en flux.

Les temps absolus dépendent de la machine : on documente des ordres de grandeur ; les tests vérifient les invariants (une
composition par angle montré, une bascule qui ne dépend pas du nombre de coupes).
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

from core.export_engine import ExportEngine  # noqa: E402
from core.filter_graph import fingerprint_plan  # noqa: E402
from core.multicam_ops import AngleSpec, create_multicam_source, insert_multicam_clip, switch_angle  # noqa: E402
from core.preview_segments import segment_plan  # noqa: E402
from core.project_model import MediaAsset, Project, Track  # noqa: E402
from core.render_plan import build_render_plan  # noqa: E402
from core.timeline_index import build_timeline_index  # noqa: E402
from core.timeline_operations import trim_clip_right  # noqa: E402
from tools.perf.bench import _per_call, _timed  # noqa: E402

RESOLUTIONS = {"1080p": (1920, 1080), "4K": (3840, 2160)}
PROXY_SIZE = (480, 270)


def multicam_project(angles: int, cuts: int, *, path: str = "/media/cam.mp4") -> Project:
    """Un montage de ``cuts`` coupes réparties sur ``angles`` angles (médias fictifs : aucun fichier n'est lu)."""
    project = Project(
        "bench", width=1920, height=1080, fps=25.0,
        media_assets=[MediaAsset(f"cam{i}", path.replace(".mp4", f"{i}.mp4"), f"cam{i}", 7200.0, 1920, 1080, 25.0, "video", True)
                      for i in range(angles)],
        tracks=[Track("V1", "V1", "video"), Track("A1", "A1", "audio")],
    )
    source = create_multicam_source(
        project, [AngleSpec(asset_id=f"cam{i}", name=f"Cam {i + 1}", offset=0.5 * i) for i in range(angles)], name="Concert",
    )
    segment = insert_multicam_clip(project, source.id, "V1", 0.0, angle_id="angle-1")
    length = max(10.0, cuts * 2.0 + 2.0)
    trim_clip_right(project, segment.id, length)
    for index in range(cuts):
        switch_angle(project, 2.0 * (index + 1), f"angle-{(index + 1) % angles + 1}")   # jamais deux fois le même d'affilée
    return project


def bench_plan(angles: int, cuts: int) -> dict[str, float]:
    project = multicam_project(angles, cuts)
    plan = build_render_plan(project)
    graph, _video, _audio, inputs = ExportEngine._build_filter_complex(plan, 320, 180, 25, None)
    index = build_timeline_index(project)
    duration = max(1.0, plan.duration)
    moments = [duration * k / 50.0 for k in range(50)]
    window_start = min(duration - 4.0, 10.0)
    return {
        "segments": float(len(plan.video_layers)),
        "angles_rendered": float(len(plan.nested_sequences)),
        "compositions": float(graph.count("color=c=black@0")),
        "input_files": float(len(inputs)),
        "graph_kb": len(graph) / 1024.0,
        "render_plan_ms": _timed(lambda: build_render_plan(project)),
        "filter_graph_ms": _timed(lambda: ExportEngine._build_filter_complex(plan, 320, 180, 25, None)),
        "segment_plan_ms": _timed(lambda: segment_plan(project, window_start, window_start + 2.0, timeline_index=index)),
        "segment_fingerprint_ms": _timed(lambda: fingerprint_plan(segment_plan(project, window_start, window_start + 2.0))),
        "active_at_us": _per_call(lambda: [index.active_at(project, t) for t in moments], len(moments)) * 1000.0,
    }


def bench_switch(angles: int, cuts: int) -> dict[str, float]:
    from core.edit_history import ProjectHistory

    project = multicam_project(angles, cuts)
    history = ProjectHistory()
    history.reset(project)
    length = max(10.0, cuts * 2.0 + 2.0)
    state = {"time": 1.0, "angle": 0}

    def switch() -> None:
        state["angle"] = (state["angle"] + 1) % angles
        state["time"] = (state["time"] + 0.37) % (length - 1.0) + 0.1
        try:
            switch_angle(project, state["time"], f"angle-{state['angle'] + 1}")
        except Exception:      # noqa: BLE001 - un instant trop près d'une fin de segment : sans importance pour la mesure
            pass

    def switch_and_record() -> None:
        switch()
        history.record(project, "bascule")

    return {
        "switch_ms": _timed(switch, repeats=15, warmup=2),
        "switch_with_history_ms": _timed(switch_and_record, repeats=15, warmup=2),
    }


_ISOLATION = ("KUT_STUDIO_CONFIG_DIR", "KUT_STUDIO_CACHE_DIR", "KUT_STUDIO_PROXY_DIR", "KUT_STUDIO_LOG_DIR",
              "KUT_STUDIO_HARDWARE_ENCODING", "KUT_STUDIO_HARDWARE_DECODING")


def bench_window(angles: int = 4, cuts: int = 40) -> dict[str, float]:
    """Une bascule complète dans la vraie fenêtre : opération, historique, timeline ; et le retour immédiat du moniteur.

    La fenêtre tourne sur des dossiers jetables (ni les préférences ni les caches de l'utilisateur) et **l'environnement est
    rétabli à la sortie** : les mesures de flux qui suivent partent du même processus et ne doivent pas hériter de
    ``KUT_STUDIO_HARDWARE_*=off`` ni de dossiers déjà supprimés.
    """
    from PySide6.QtWidgets import QApplication

    from tools.ui_audit import ensure_offscreen_fonts
    from ui.main_window import MainWindow

    scratch = tempfile.mkdtemp(prefix="kut-multicam-ui-")
    saved = {name: os.environ.get(name) for name in _ISOLATION}
    os.environ.update({
        "KUT_STUDIO_CONFIG_DIR": str(Path(scratch) / "config"),
        "KUT_STUDIO_CACHE_DIR": str(Path(scratch) / "cache"),
        "KUT_STUDIO_PROXY_DIR": str(Path(scratch) / "proxies"),
        "KUT_STUDIO_LOG_DIR": str(Path(scratch) / "logs"),
        "KUT_STUDIO_HARDWARE_ENCODING": "off",
        "KUT_STUDIO_HARDWARE_DECODING": "off",
    })
    try:
        ensure_offscreen_fonts()                # Windows : sans police, la fenêtre se mesure autrement
        _app = QApplication.instance() or QApplication([])
        window = MainWindow()
        window.timeline_timer.stop()
        window.project = multicam_project(angles, cuts)
        window.history.reset(window.project)
        window.timeline_panel.set_project(window.project)
        window._update_timeline_duration()          # noqa: SLF001
        state = {"time": 1.0, "angle": 0}
        hint: list[float] = []

        def full_switch() -> None:
            state["angle"] = state["angle"] % angles + 1
            state["time"] += 2.0 if state["time"] < cuts * 2.0 - 4.0 else 0.0
            window.playhead_seconds = state["time"] + 0.5
            started = time.perf_counter()
            window.multicam_viewer.set_active_hint(state["angle"] - 1)
            hint.append((time.perf_counter() - started) * 1000.0)
            window.switch_multicam_angle(state["angle"])

        result = {"window_switch_ms": _timed(full_switch, repeats=10, warmup=1),
                  "viewer_feedback_ms": sorted(hint)[len(hint) // 2] if hint else 0.0}
        window.project_dirty = False            # projet jetable : sinon la fermeture attend la réponse à « enregistrer ? »
        window.close()
        del _app
        return result
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        shutil.rmtree(scratch, ignore_errors=True)


# ---------------------------------------------------------------------------
# Flux d'images réels
# ---------------------------------------------------------------------------


def _make_media(folder: Path, angles: int, size: tuple[int, int], *, proxy: bool, seconds: float = 12.0) -> list[str]:
    """Un média de la résolution voulue (et son proxy 480×270), lié sous ``angles`` noms : un chemin par angle."""
    name = f"src-{size[0]}x{size[1]}"
    original = folder / f"{name}.mp4"
    if not original.exists():
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s={size[0]}x{size[1]}:r=25:d={seconds}",
             "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-g", "12", str(original)], check=True,
        )
    source = original
    if proxy:
        small = folder / f"{name}-proxy.mp4"
        if not small.exists():
            subprocess.run(
                ["ffmpeg", "-v", "error", "-y", "-i", str(original), "-vf", f"scale={PROXY_SIZE[0]}:{PROXY_SIZE[1]}",
                 "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-g", "12", str(small)], check=True,
            )
        source = small
    paths = []
    for index in range(angles):
        link = folder / f"{source.stem}-{index}.mp4"
        if not link.exists():
            try:
                os.link(source, link)
            except OSError:
                shutil.copy(source, link)
        paths.append(str(link))
    return paths


def _feed_worker(payload: str) -> None:
    """Sous-processus : lit ``angles`` flux en temps réel ; mesures en JSON sur la sortie standard."""
    import resource

    from core.multicam_feed import AngleFeed, tile_profile

    args = json.loads(payload)
    paths: list[str] = args["paths"]
    seconds = float(args["seconds"])
    profile = tile_profile(len(paths), active=False)
    active_profile = tile_profile(len(paths), active=True)
    feeds = [AngleFeed(path, active_profile if index == 0 else profile) for index, path in enumerate(paths)]
    seen: list[set[float]] = [set() for _ in feeds]
    late = polls = 0
    before_self = resource.getrusage(resource.RUSAGE_SELF)
    before_children = resource.getrusage(resource.RUSAGE_CHILDREN)
    started = time.perf_counter()
    cold_first_frame_ms = None
    while True:
        elapsed = time.perf_counter() - started
        if elapsed >= seconds:
            break
        for index, feed in enumerate(feeds):
            feed.update(elapsed, True)
            frame = feed.frame_at(elapsed)
            if frame is not None:
                seen[index].add(round(frame.time, 4))
                if cold_first_frame_ms is None and index == 0:
                    cold_first_frame_ms = elapsed * 1000.0
                polls += 1
                late += 1 if elapsed - frame.time > 4.0 / feed.profile.fps else 0
            else:
                polls += 1
                late += 1
        time.sleep(0.04)
    wall = time.perf_counter() - started
    # changement d'angle déjà en flux : seul le choix de l'image à montrer coûte quelque chose
    switch_us = []
    for _ in range(50):
        began = time.perf_counter()
        feeds[1 % len(feeds)].frame_at(wall)
        switch_us.append((time.perf_counter() - began) * 1e6)
    for feed in feeds:
        feed.close()
    after_self = resource.getrusage(resource.RUSAGE_SELF)
    after_children = resource.getrusage(resource.RUSAGE_CHILDREN)
    cpu = (after_self.ru_utime + after_self.ru_stime - before_self.ru_utime - before_self.ru_stime
           + after_children.ru_utime + after_children.ru_stime - before_children.ru_utime - before_children.ru_stime)
    unit = 1 if sys.platform == "darwin" else 1024
    delivered = sum(len(frames) for frames in seen)
    print(json.dumps({
        "angles": len(paths),
        "tile": f"{profile.width}x{profile.height}@{profile.fps:g}",
        "fps_per_angle": round(delivered / len(feeds) / wall, 1),
        "expected_fps": profile.fps,
        "late_poll_ratio": round(late / max(1, polls), 3),
        "cpu_percent_of_one_core": round(100.0 * cpu / wall, 1),
        "rss_self_mb": round(after_self.ru_maxrss * unit / 1e6, 1),
        "rss_largest_ffmpeg_mb": round(after_children.ru_maxrss * unit / 1e6, 1),
        "cold_first_frame_ms": round(cold_first_frame_ms, 1) if cold_first_frame_ms is not None else None,
        "warm_switch_us": round(sorted(switch_us)[len(switch_us) // 2], 1),
    }))


def bench_feeds(spec: str, folder: Path, seconds: float) -> dict[str, float]:
    """``spec`` : ``4x1080p``, ``8x4K+proxy``… ; un sous-processus par scénario (mémoire et CPU isolés)."""
    count_text, _, rest = spec.partition("x")
    resolution, _, tail = rest.partition("+")
    paths = _make_media(folder, int(count_text), RESOLUTIONS[resolution], proxy=tail == "proxy")
    payload = json.dumps({"paths": paths, "seconds": seconds})
    done = subprocess.run(
        [sys.executable, "-m", "tools.perf.multicam_bench", "--feed-worker", payload],
        cwd=ROOT, capture_output=True, text=True, timeout=300, check=False,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
    )
    if done.returncode != 0:
        raise RuntimeError(done.stderr[-800:])
    return json.loads(done.stdout.strip().splitlines()[-1])


def run(*, angles=(4, 8, 16), cuts=(10, 100, 1000), feeds=("4x1080p", "8x1080p", "4x4K", "8x4K+proxy"),
        seconds: float = 6.0, ui: bool = False) -> dict:
    report: dict = {
        "meta": {"python": platform.python_version(), "machine": platform.machine(), "system": platform.system()},
        "plan": {}, "switch": {},
    }
    for count in angles:
        for number in cuts:
            report["plan"][f"{count}x{number}"] = bench_plan(count, number)
    for count in angles:
        for number in cuts:
            report["switch"][f"{count}x{number}"] = bench_switch(count, number)
    if ui:
        report["window"] = bench_window()
    if feeds and shutil.which("ffmpeg"):
        report["feeds"] = {}
        with tempfile.TemporaryDirectory(prefix="kut-multicam-bench-") as workdir:
            for spec in feeds:
                report["feeds"][spec] = bench_feeds(spec, Path(workdir), seconds)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path)
    parser.add_argument("--quick", action="store_true", help="petit jeu de mesures (tests)")
    parser.add_argument("--ui", action="store_true", help="mesure une bascule complète dans la fenêtre")
    parser.add_argument("--feeds", default="", help="scénarios de flux, ex. 4x1080p,8x4K+proxy (défaut : les quatre habituels)")
    parser.add_argument("--seconds", type=float, default=6.0, help="durée de lecture de chaque scénario de flux")
    parser.add_argument("--feed-worker", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.feed_worker:
        _feed_worker(args.feed_worker)
        return 0
    if args.quick:
        report = run(angles=(4,), cuts=(10, 100), feeds=(), ui=args.ui)
    else:
        selected = tuple(item for item in args.feeds.split(",") if item) or ("4x1080p", "8x1080p", "4x4K", "8x4K+proxy")
        report = run(feeds=selected, seconds=args.seconds, ui=args.ui)
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
