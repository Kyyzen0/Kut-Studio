"""Mesures des séquences imbriquées : évaluation, plan, graphe, aperçu, navigation, export.

Usage::

    QT_QPA_PLATFORM=offscreen python -m tools.perf.nested_bench --out docs/perf/nested.json
    python -m tools.perf.nested_bench --ffmpeg --ui   # avec rendus réels et navigation UI

Scénarios :

- ``chain:N`` : N niveaux d'imbrication (1, 3, 10), un média à la feuille ;
- ``instances:N`` : une séquence utilisée N fois dans la séquence parente ;
- ``heavy:N`` : une séquence de N clips (montage lourd) utilisée 3 fois.

Pour chaque scénario : construction du plan (export et segment d'aperçu),
taille du graphe FFmpeg et **nombre de compositions imbriquées** réellement
émises (une par séquence, quelle que soit le nombre d'instances), coût d'une
évaluation temps réel (index) et empreinte de segment. Médiane de plusieurs
répétitions ; les durées sont en millisecondes. Les temps absolus dépendent
de la machine : ce banc documente des ordres de grandeur, les tests
vérifient les invariants (pas de recalcul par instance).
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

from core.export_engine import ExportEngine, input_arguments  # noqa: E402
from core.filter_graph import fingerprint_plan  # noqa: E402
from core.preview_segments import segment_plan  # noqa: E402
from core.project_model import Clip, MediaAsset, Project, Sequence, Track  # noqa: E402
from core.render_plan import build_render_plan  # noqa: E402
from core.timeline_index import build_timeline_index  # noqa: E402
from tools.perf.bench import _per_call, _timed  # noqa: E402


def _asset(path: str = "/media/leaf.mp4") -> MediaAsset:
    return MediaAsset("leaf", path, "leaf", 600.0, 320, 180, 25.0, "video", True)


def chain_project(levels: int, media_path: str = "/media/leaf.mp4") -> Project:
    sequences = []
    for index in range(levels + 1):
        if index == levels:
            clips = [Clip(f"leaf{index}", "leaf", "V1", 0.0, 0.0, 4.0)]
        else:
            clips = [Clip(f"n{index}", "", "V1", 0.0, 0.0, 4.0, sequence_id=f"seq{index + 1}")]
        sequences.append(
            Sequence(f"seq{index}", f"S{index}", 320, 180, 25.0, tracks=[Track("V1", "V1", "video", clips=clips)])
        )
    return Project("chain", media_assets=[_asset(media_path)], sequences=sequences, active_sequence_id="seq0")


def instances_project(count: int, inner_clips: int = 4, media_path: str = "/media/leaf.mp4") -> Project:
    inner = Sequence("intro", "Intro", 320, 180, 25.0, tracks=[
        Track("V1", "V1", "video", clips=[
            Clip(f"i{k}", "leaf", "V1", float(k), float(k), float(k) + 1.0) for k in range(inner_clips)
        ]),
        Track("A1", "A1", "audio"),
    ])
    parent_clips = [
        Clip(f"inst{k}", "", "V1", k * float(inner_clips), 0.0, float(inner_clips), sequence_id="intro")
        for k in range(count)
    ]
    main = Sequence("main", "Master", 320, 180, 25.0, tracks=[Track("V1", "V1", "video", clips=parent_clips)])
    return Project("instances", media_assets=[_asset(media_path)], sequences=[main, inner], active_sequence_id="main")


def heavy_project(inner_clips: int) -> Project:
    project = instances_project(3, inner_clips=inner_clips)
    # Trois pistes intérieures pour un montage « lourd » réaliste.
    inner = project.get_sequence("intro")
    for track_id in ("V2", "V3"):
        inner.tracks.append(Track(track_id, track_id, "video", clips=[
            Clip(f"{track_id}-{k}", "leaf", track_id, float(k), 0.0, 0.8) for k in range(0, inner_clips, 3)
        ]))
    return project


def bench_scenario(project: Project) -> dict[str, float]:
    plan = build_render_plan(project)
    graph, *_ = ExportEngine._build_filter_complex(plan, 320, 180, 25, None)
    index = build_timeline_index(project)
    duration = max(1.0, plan.duration)
    moments = [duration * k / 50.0 for k in range(50)]
    nested_layers = sum(1 for layer in plan.video_layers if layer.nested_key)
    return {
        "nested_instances": float(nested_layers),
        "nested_compositions": float(graph.count("color=c=black@0")),
        "graph_kb": len(graph) / 1024.0,
        "render_plan_ms": _timed(lambda: build_render_plan(project)),
        "filter_graph_ms": _timed(lambda: ExportEngine._build_filter_complex(plan, 320, 180, 25, None)),
        "segment_plan_ms": _timed(lambda: segment_plan(project, 2.0, 4.0, timeline_index=index)),
        "segment_fingerprint_ms": _timed(lambda: fingerprint_plan(segment_plan(project, 2.0, 4.0))),
        "active_at_us": _per_call(lambda: [index.active_at(project, t) for t in moments], len(moments)) * 1000.0,
    }


def bench_navigation() -> dict[str, float]:
    """Changer de séquence dans la vraie fenêtre (timeline, aperçu, mixeur)."""
    from PySide6.QtWidgets import QApplication

    from ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.timeline_panel._set_selection(["intro", "b_roll"], "intro", announce=False)
    result = window.nest_selected_clips(name="Bench")
    parent = window.project.sequences[0].id

    def round_trip():
        window.open_nested_clip(result.clip.id)
        window.go_to_parent_sequence()

    measured = {
        "open_and_return_ms": _timed(round_trip, repeats=5),
        "nest_selection_ms": 0.0,
    }
    window.undo_last()
    window.timeline_panel._set_selection(["plan_a"], "plan_a", announce=False)
    started = time.perf_counter()
    window.nest_selected_clips(name="Bench 2")
    measured["nest_selection_ms"] = (time.perf_counter() - started) * 1000.0
    assert window.project.get_sequence(parent) is not None
    window.close()
    app.processEvents()
    return measured


def bench_export(workdir: Path) -> dict[str, float]:
    """Rendus FFmpeg réels (320×180, 4 s) : 1, 3 et 10 niveaux, 6 instances."""
    media = workdir / "leaf.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=25:d=24",
         "-f", "lavfi", "-i", "sine=duration=24", "-c:v", "libx264", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(media)],
        check=True, capture_output=True,
    )
    results = {}
    scenarios = {
        "export_chain1_ms": chain_project(1, str(media)),
        "export_chain3_ms": chain_project(3, str(media)),
        "export_chain10_ms": chain_project(10, str(media)),
        "export_instances6_ms": instances_project(6, media_path=str(media)),
    }
    for name, project in scenarios.items():
        plan = build_render_plan(project)
        graph, video, audio, inputs = ExportEngine._build_filter_complex(plan, 320, 180, 25, None)
        command = ["ffmpeg", "-v", "error", "-y"]
        for path in inputs:
            command += input_arguments(path)
        command += ["-filter_complex", graph, "-map", f"[{video}]", "-map", f"[{audio}]",
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(workdir / f"{name}.mp4")]
        started = time.perf_counter()
        completed = subprocess.run(command, capture_output=True, text=True)
        results[name] = (time.perf_counter() - started) * 1000.0
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr[-800:])
    return results


def run(*, chains=(1, 3, 10), instances=(1, 3, 20), heavy=(500, 2000), ui=False, ffmpeg=False) -> dict:
    report: dict = {
        "meta": {"python": platform.python_version(), "machine": platform.machine(), "system": platform.system()},
        "scenarios": {},
    }
    for levels in chains:
        report["scenarios"][f"chain:{levels}"] = bench_scenario(chain_project(levels))
    for count in instances:
        report["scenarios"][f"instances:{count}"] = bench_scenario(instances_project(count))
    for size in heavy:
        report["scenarios"][f"heavy:{size}"] = bench_scenario(heavy_project(size))
    if ui:
        report["navigation"] = bench_navigation()
    if ffmpeg and shutil.which("ffmpeg"):
        with tempfile.TemporaryDirectory(prefix="kut-nested-bench-") as workdir:
            report["export"] = bench_export(Path(workdir))
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path)
    parser.add_argument("--ui", action="store_true", help="mesure la navigation dans la fenêtre")
    parser.add_argument("--ffmpeg", action="store_true", help="mesure des exports FFmpeg réels")
    args = parser.parse_args(argv)
    report = run(ui=args.ui, ffmpeg=args.ffmpeg)
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
