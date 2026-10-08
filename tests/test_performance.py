"""Performances critiques : la **complexité** ne dépend pas de la taille du projet.

Ces tests ne mesurent pas de durées absolues (fragiles selon la machine) :
ils comptent le travail effectué (éléments lus, couches construites,
appels) et vérifient qu'il reste borné quand le projet grossit. Un seul
test compare des durées, avec une marge très large (voir son commentaire).
"""

from __future__ import annotations

import errno
import statistics
import time
from pathlib import Path

import pytest

from core.preview_cache import DiskPreviewCache, PreviewSegmentKey
from core.preview_engine import PreviewEngine
from core.project_model import Clip
from core.render_plan import RenderLayer, build_render_plan
from core.task_queue import TaskQueue
from core.timeline_spatial import SnapIndex, SpanIndex
from tools.perf.synthetic import build_project


class _CountingList(list):
    """Liste qui compte ses lectures et ses parcours (instrumentation de test)."""

    def __init__(self, *args):
        super().__init__(*args)
        self.reads = 0
        self.iterations = 0

    def __getitem__(self, index):
        self.reads += 1
        return super().__getitem__(index)

    def __iter__(self):
        self.iterations += 1
        return super().__iter__()


# --- index d'intervalles et d'aimantation -----------------------------------------------------------------


def _lane_spans(count):
    return [(f"c{i}", 0, i * 2.5, i * 2.5 + 2.0) for i in range(count)]


@pytest.mark.parametrize("count", [200, 2_000, 20_000])
def test_span_query_reads_only_the_visible_clips(count):
    index = SpanIndex(_lane_spans(count))
    lane = index._lanes[0]
    lane.ends, lane.keys = _CountingList(lane.ends), _CountingList(lane.keys)
    found = index.query(100.0, 140.0)
    assert 10 <= len(found) <= 20                         # ~16 clips visibles quelle que soit la taille
    assert lane.ends.reads <= 30 and lane.keys.reads <= 30   # et on ne lit que ceux-là


@pytest.mark.parametrize("count", [200, 20_000])
def test_snap_lookup_reads_only_the_edges_inside_the_threshold(count):
    project = build_project(count, "short_1t")
    index = SnapIndex(project, with_keyframes=False)
    index._owners = _CountingList(index._owners)
    index.nearest(250.0, 0.3, playhead=100.0)
    assert index._owners.reads <= 12                      # 2 bords par clip, seuil de 0,3 s


def test_snap_cost_does_not_grow_with_the_number_of_clips():
    """Seul test de durée : marge très large (×4 pour un résultat attendu ≈ ×1, contre ×10 avant)."""

    def per_call(count):
        index = SnapIndex(build_project(count, "short_1t"), with_keyframes=False)
        samples = []
        for _ in range(7):
            started = time.perf_counter()
            for step in range(300):
                index.nearest(10.0 + step * 0.37, 0.1, excluded_ids=("V1-c5",), playhead=50.0)
            samples.append(time.perf_counter() - started)
        return statistics.median(samples)

    small, large = per_call(2_000), per_call(20_000)
    assert large < small * 4 + 0.002


# --- plan de rendu fenêtré --------------------------------------------------------------------------------


def test_a_windowed_plan_builds_only_the_layers_of_the_window(monkeypatch):
    project = build_project(6_000, "short_8t")
    built = []

    def counting(*args, **kwargs):
        built.append(1)
        return RenderLayer(*args, **kwargs)

    monkeypatch.setattr("core.render_plan.RenderLayer", counting)
    plan = build_render_plan(project, window=(300.0, 302.0))
    windowed = len(built)
    assert windowed == len(plan.video_layers) and 0 < windowed < 15
    built.clear()
    full = build_render_plan(project)
    assert len(built) == len(full.video_layers) > 3_000 > windowed * 100


def test_one_preview_planning_never_builds_a_full_plan(qtbot, monkeypatch, tmp_path):
    from ui.main_window import MainWindow

    monkeypatch.setattr("ui.main_window.QMessageBox.information", lambda *_a, **_k: None)
    window = MainWindow()
    qtbot.addWidget(window)
    window._preview_pump_timer.stop()
    window.project = build_project(4_000, "short_8t", media_path=str(tmp_path / "m.mp4"))
    window.timeline_panel.set_project(window.project)
    windows = []
    import core.preview_segments as segments

    real = segments.build_render_plan
    monkeypatch.setattr(segments, "build_render_plan",
                        lambda project, **kw: (windows.append(kw.get("window")), real(project, **kw))[1])
    window._schedule_preview_around(800.0)
    assert 1 <= len(windows) <= 6 and all(w is not None for w in windows)
    windows.clear()
    window._cached_preview_at(800.0)
    assert len(windows) <= 1 and all(w is not None for w in windows)   # un seek = au plus un plan de segment


def test_seeking_does_not_rescan_the_project_duration(qtbot, monkeypatch, tmp_path):
    from ui.main_window import MainWindow

    monkeypatch.setattr("ui.main_window.QMessageBox.information", lambda *_a, **_k: None)
    window = MainWindow()
    qtbot.addWidget(window)
    window._preview_pump_timer.stop()
    window.project = build_project(1_000, "short_8t", media_path=str(tmp_path / "m.mp4"))
    window.timeline_panel.set_project(window.project)
    window._update_timeline_duration()
    window.seek_to_position(1.0)                          # construit l'index une fois

    def boom(_project):
        raise AssertionError("timeline_duration() parcourt tous les clips à chaque seek")

    monkeypatch.setattr("ui.main_window.timeline_duration", boom)
    for position in (3.0, 50.0, 300.0):
        window.seek_to_position(position)
    assert window.playhead_seconds == pytest.approx(300.0)


# --- timeline : aucun parcours de tous les clips pendant l'usage ----------------------------------------------------


def _panel(qtbot, count):
    from core.studio_runtime import StudioRuntime
    from ui.timeline_panel import TimelinePanel

    project = build_project(count, "short_8t", media_path=__file__)
    panel = TimelinePanel(project)
    qtbot.addWidget(panel)
    panel.attach_runtime(StudioRuntime(profile="balanced"))
    panel.resize(1100, 480)
    panel.show()
    qtbot.waitExposed(panel)
    panel.set_timeline_duration(max(c.timeline_start + c.duration for t in project.tracks for c in t.clips))
    panel.set_project(project)
    return panel


def test_scroll_zoom_playhead_and_selection_never_iterate_every_clip(qtbot, monkeypatch):
    import ui.timeline_panel_mixins.previews as previews

    monkeypatch.setattr(previews, "extract_thumbnail", lambda *_a, **_k: b"")
    monkeypatch.setattr(previews, "extract_waveform_peaks", lambda *_a, **_k: ())
    panel = _panel(qtbot, 3_000)
    views = _CountingList(panel.clip_views)
    panel.clip_views = views                              # même contenu, instrumenté
    bar = panel.scroll.horizontalScrollBar()
    bar.setValue(int(bar.maximum() * 0.5))                # premier passage : construit l'index
    qtbot.wait(5)
    views.reads = views.iterations = 0
    for fraction in (0.2, 0.4, 0.61, 0.8, 0.95, 0.1):
        bar.setValue(int(bar.maximum() * fraction))       # défilement
    panel.zoom_in()
    panel.zoom_out()                                      # zoom
    for step in range(30):
        panel.set_playhead_seconds(step * 3.7)            # tête de lecture
    panel._set_selection([panel.clip_views[10].id], panel.clip_views[10].id, announce=False)
    panel.find_view_by_id("V3-c40")
    panel._layout_children()
    panel._schedule_previews()
    panel.snap_position(100.0, excluded_clip_id="V1-c3")
    assert views.iterations == 0 and views.reads <= 3     # aucun balayage des 3 000 clips
    assert 0 < panel.mounted_clip_count < 400


def test_mounted_widgets_stay_bounded_when_the_project_grows(qtbot):
    counts = []
    for size in (400, 4_000):
        panel = _panel(qtbot, size)
        bar = panel.scroll.horizontalScrollBar()
        bar.setValue(int(bar.maximum() * 0.5))
        qtbot.wait(5)
        counts.append(panel.mounted_clip_count)
    assert counts[1] <= counts[0] * 2 and counts[1] < 400   # le coût visible suit l'écran, pas le projet


# --- cache disque : résistance aux incidents ------------------------------------------------------------------------


def test_a_full_disk_while_storing_a_preview_segment_is_reported_and_leaves_nothing(tmp_path, monkeypatch):
    cache = DiskPreviewCache(directory=tmp_path / "c", budget_bytes=10 ** 9)

    def render(job, token):
        out = tmp_path / "out.mp4"
        out.write_bytes(b"x")
        return str(out)

    def full_disk(*_args, **_kwargs):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr("shutil.copyfile", full_disk)
    engine = PreviewEngine(task_queue=TaskQueue(), cache=cache, render_fn=render)
    from core.preview_segments import build_segment_job

    project = build_project(10, "short_1t", media_path=str(tmp_path / "m.mp4"))
    job = build_segment_job(project, 0, quality="standard")
    engine.request(job)
    while engine.pump(1):
        pass
    assert "No space" in engine.state().last_error
    assert not [p for p in (tmp_path / "c").iterdir() if p.suffix == ".tmp"]
    assert cache.lookup(job.key) is None


def test_the_cache_folder_is_recreated_if_it_vanishes_during_the_session(tmp_path):
    import shutil

    cache = DiskPreviewCache(directory=tmp_path / "c", budget_bytes=10 ** 9)
    shutil.rmtree(tmp_path / "c")                         # nettoyage manuel pendant l'exécution
    source = tmp_path / "seg.mp4"
    source.write_bytes(b"x" * 10)
    key = PreviewSegmentKey("clip", 0.0, 2.0, "standard", "h")
    assert cache.lookup(key) is None                      # absent : un échec propre
    cache.store(key, source)                              # puis il refonctionne
    assert cache.lookup(key) is not None


def test_an_unusable_cache_folder_disables_the_faithful_preview_without_breaking_the_window(
    qtbot, monkeypatch, tmp_path
):
    from ui.main_window import MainWindow

    blocker = tmp_path / "cache-is-a-file"
    blocker.write_text("pas un dossier", encoding="utf-8")
    monkeypatch.setenv("KUT_STUDIO_CACHE_DIR", str(blocker))
    monkeypatch.setattr("ui.main_window.QMessageBox.information", lambda *_a, **_k: None)
    window = MainWindow()
    qtbot.addWidget(window)
    assert window.preview_engine is None                  # aperçu fidèle coupé
    assert window.cache_manager.usage() is not None       # la gestion de cache reste utilisable
    window.seek_to_position(0.5)                          # et l'aperçu simple fonctionne
    assert window.cache_manager.enforce() >= 0 and window.cache_manager.purge() >= 0
    assert Path(blocker).is_file()


def test_a_segment_plan_through_the_index_does_not_touch_every_clip(monkeypatch):
    from core.preview_segments import segment_plan
    from core.timeline_index import build_timeline_index

    project = build_project(6_000, "short_8t")
    index = build_timeline_index(project)
    reads = {"scan": 0, "indexed": 0}
    original = Clip.duration.fget

    def counting(self):
        reads[mode["name"]] += 1
        return original(self)

    mode = {"name": "scan"}
    monkeypatch.setattr(Clip, "duration", property(counting))
    scanned = segment_plan(project, 100.0, 102.0)
    mode["name"] = "indexed"
    indexed = segment_plan(project, 100.0, 102.0, timeline_index=index)
    assert indexed == scanned and len(indexed.video_layers) > 0
    assert reads["scan"] >= 6_000                      # le balayage lit la durée de chaque clip
    assert reads["indexed"] < 300                      # via l'index : seulement les clips proches
