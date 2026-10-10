"""Cache de rendu des compositions : morceaux rendus par le graphe de l'export, lus en direct par le moniteur.

Le cache ne dépend que du contenu de la composition ; le moniteur le lit à la place de la source principale et
applique lui-même les réglages du clip.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from render_probe import needs_ffmpeg

from core.composition import OUTPUT_ID, Composition, CompositionGraph, MediaNode, SolidNode
from core.effects_model import ClipEffect, EffectType
from core.composition_cache import (
    CHUNK_SECONDS,
    CompositionSite,
    cache_owner,
    chunk_count,
    chunk_index,
    composition_chunk_job,
    composition_sites,
    neutral_project,
)
from core.preview_cache import DiskPreviewCache
from core.preview_engine import PreviewEngine
from core.project_model import Clip, MediaAsset, Project, Sequence, Track
from core.task_queue import TaskQueue
from core.visual_effects import ClipTransform

W, H = 64, 36


def _composition(duration: float = 25.0) -> Composition:
    graph = CompositionGraph.empty().with_added(MediaNode("m", "v", 0.0, 0.0, duration)).connected("m", OUTPUT_ID)
    return Composition(graph, duration)


def _project(**clip) -> tuple[Project, Clip]:
    comp = Clip("c", "", "V1", 3.0, 0.0, 8.0, **clip)
    comp.composition = _composition()
    assets = [MediaAsset("v", "/media/v.mp4", "v", 30.0, W, H, 25.0, "video", has_audio=True)]
    project = Project("p", width=W, height=H, fps=25.0, media_assets=assets,
                      tracks=[Track("V1", "V1", "video", clips=[comp])])
    return project, comp


def _site(project: Project, clip: Clip) -> CompositionSite:
    return CompositionSite(clip, project.active_sequence)


def test_chunks_cover_the_composition_time():
    assert chunk_count(25.0) == 3 and chunk_count(20.0) == 2 and chunk_count(0.5) == 1
    assert [chunk_index(t) for t in (0.0, 9.999, 10.0, 24.9)] == [0, 0, 1, 2]


def test_a_chunk_renders_the_composition_alone_on_a_neutral_clip():
    project, clip = _project()
    clip.transform = ClipTransform(scale=0.5, rotation=30.0)
    clip.effects = [ClipEffect("fx", EffectType.BLUR, True, {"intensity": 3.0})]
    job = composition_chunk_job(project, _site(project, clip), 1, quality="standard")
    assert job is not None and job.start == pytest.approx(CHUNK_SECONDS) and job.duration == CHUNK_SECONDS
    assert job.key.clip_id == cache_owner("c") and not job.anchored
    layer = next(layer for layer in job.plan.video_layers if layer.clip_id == "c")
    assert layer.timeline_start == 0.0 and layer.transform == ClipTransform(), "le moniteur applique le transform"
    assert not layer.effects
    assert composition_chunk_job(project, _site(project, clip), 3, quality="standard") is None


def test_the_cache_key_follows_the_composition_not_the_clip():
    project, clip = _project()
    site = _site(project, clip)
    key = composition_chunk_job(project, site, 0, quality="standard").key
    clip.timeline_start, clip.transform = 40.0, ClipTransform(position_x=0.3)        # déplacer, régler le clip
    assert composition_chunk_job(project, site, 0, quality="standard").key == key
    node = clip.composition.graph.node("m")
    clip.composition = clip.composition.with_graph(clip.composition.graph.with_node(replace(node, gain_db=-6.0)))
    assert composition_chunk_job(project, site, 0, quality="standard").key != key, "son modifié : nouveau rendu"
    assert composition_chunk_job(project, site, 0, quality="high").key != key


def test_the_neutral_project_keeps_the_frame_of_the_carrying_sequence():
    project, clip = _project()
    inner = Sequence("s2", "Inner", width=32, height=32, fps=50.0, tracks=[Track("V1", "V1", "video", clips=[clip])])
    neutral = neutral_project(project, CompositionSite(clip, inner))
    assert (neutral.width, neutral.height, neutral.fps) == (32, 32, 50.0)
    assert [c.id for track in neutral.tracks for c in track.clips] == ["c"]


def test_sites_reach_compositions_inside_nested_sequences_once():
    project, clip = _project()
    inner_clip = Clip("ic", "", "V1", 0.0, 0.0, 5.0)
    inner_clip.composition = _composition(5.0)
    inner = Sequence("s2", "Inner", width=W, height=H, fps=25.0, tracks=[Track("V1", "V1", "video", clips=[inner_clip])])
    project.sequences.append(inner)
    holder = Clip("n1", "", "V1", 20.0, 0.0, 5.0)
    holder.sequence_id = "s2"
    twice = Clip("n2", "", "V1", 30.0, 0.0, 5.0)
    twice.sequence_id = "s2"
    project.tracks[0].clips.extend([holder, twice])
    assert [(site.clip.id, site.sequence.id) for site in composition_sites(project)] == [
        ("c", project.active_sequence.id), ("ic", "s2")]
    project.tracks[0].visible = False
    assert list(composition_sites(project)) == [], "une piste masquée ne se montre pas"


# --- moteur d'aperçu -------------------------------------------------------------------------------------------------


@pytest.fixture
def engine(tmp_path):
    rendered: list = []

    def render(job, token):
        rendered.append(job.key.clip_id)
        out = tmp_path / f"out-{len(rendered)}.mp4"
        out.write_bytes(b"x" * 10)
        return str(out)

    eng = PreviewEngine(task_queue=TaskQueue(), cache=DiskPreviewCache(directory=tmp_path / "cache"), render_fn=render)
    eng.rendered = rendered
    return eng


def _drain(engine):
    while engine.pump(1):
        pass


def test_composition_chunks_do_not_follow_the_playhead_and_a_newer_version_supersedes_the_old(engine):
    project, clip = _project()
    site = _site(project, clip)
    old = composition_chunk_job(project, site, 0, quality="standard")
    engine.request(old)
    assert engine.cancel_outside(100.0, 120.0) == 0, "son début est un temps de la composition"
    node = clip.composition.graph.node("m")
    clip.composition = clip.composition.with_graph(clip.composition.graph.with_node(replace(node, muted=True)))
    new = composition_chunk_job(project, site, 0, quality="standard")
    engine.request(new)
    assert engine.cancel_owner(cache_owner("c"), keep=[new.key]) == 1
    _drain(engine)
    assert engine.rendered == [cache_owner("c")] and engine.cache.lookup(new.key) is not None
    assert engine.cache.lookup(old.key) is None
    engine.invalidate_clip("c")                            # un réglage du clip : ses segments, pas sa composition
    assert engine.cache.lookup(new.key) is not None


def test_a_solid_only_composition_still_has_a_chunk():
    graph = CompositionGraph.empty().with_added(SolidNode("s")).connected("s", OUTPUT_ID)
    clip = Clip("c", "", "V1", 0.0, 0.0, 2.0)
    clip.composition = Composition(graph, 2.0)
    project = Project("p", width=W, height=H, fps=25.0, media_assets=[],
                      tracks=[Track("V1", "V1", "video", clips=[clip])])
    job = composition_chunk_job(project, _site(project, clip), 0, quality="draft")
    assert job is not None and not job.plan.audio_layers


# --- moniteur en direct ----------------------------------------------------------------------------------------------


def test_the_live_monitor_reads_a_ready_chunk_instead_of_the_main_source():
    from core.timeline_evaluator import evaluate_timeline

    project, clip = _project()                             # clip à 3 s, composition lue de 0 à 8 s
    asked = []

    def source(comp_clip, inner_time):
        asked.append((comp_clip.id, inner_time))
        return ("/cache/c-0.mp4", 0.0, CHUNK_SECONDS) if inner_time < CHUNK_SECONDS else None

    (entry,) = evaluate_timeline(project, 5.0, composition_source=source)
    assert entry.rendered and entry.source_path == "/cache/c-0.mp4" and entry.asset_id == ""
    assert entry.source_time == pytest.approx(2.0) and entry.owner_clip_id == "c"
    assert (entry.timeline_start, entry.timeline_end) == pytest.approx((3.0, 11.0)), "borné à la fin du clip"
    assert asked == [("c", pytest.approx(2.0))]
    (fallback,) = evaluate_timeline(project, 5.0)
    assert not fallback.rendered and fallback.source_path == "/media/v.mp4", "pas prêt : la source principale"


def test_the_timeline_index_passes_the_cache_into_nested_sequences():
    from core.timeline_index import build_timeline_index

    project, clip = _project()
    project.tracks[0].clips.clear()
    inner = Sequence("s2", "Inner", width=W, height=H, fps=25.0, tracks=[Track("V1", "V1", "video", clips=[clip])])
    project.sequences.append(inner)
    holder = Clip("n", "", "V1", 10.0, 0.0, 20.0)
    holder.sequence_id = "s2"
    project.tracks[0].clips.append(holder)
    index = build_timeline_index(project)
    index.composition_source = lambda comp_clip, inner_time: ("/cache/x.mp4", 0.0, CHUNK_SECONDS)
    (entry,) = index.active_at(project, 14.0)              # séquence : 4 s, composition : 1 s
    assert entry.rendered and entry.source_time == pytest.approx(1.0) and entry.owner_clip_id == "n"
    index.composition_source = None                        # lu à chaque requête, jusque dans la séquence imbriquée
    assert not index.active_at(project, 14.0)[0].rendered


@needs_ffmpeg
def test_a_chunk_is_the_exact_composition_with_close_keyframes(tmp_path):
    import json
    import subprocess

    import numpy as np
    from render_probe import lavfi_video, render_frame

    from core.composition import TransformNode
    from core.render_plan import build_render_plan

    media = lavfi_video(tmp_path / "bars.mp4", "testsrc2=d=3", size=(W, H), seconds=3.0)
    graph = (CompositionGraph.empty().with_added(MediaNode("m", "v", 0.0, 0.0, 2.0))
             .with_added(TransformNode("t", ClipTransform(scale=0.5, rotation=20.0)))
             .connected("m", "t").connected("t", OUTPUT_ID))
    clip = Clip("c", "", "V1", 1.0, 0.0, 2.0)
    clip.composition = Composition(graph, 2.0)
    clip.transform = ClipTransform(position_x=0.4)         # appliqué par le moniteur, pas dans le cache
    assets = [MediaAsset("v", str(media), "v", 3.0, W, H, 25.0, "video")]
    project = Project("p", width=W, height=H, fps=25.0, media_assets=assets,
                      tracks=[Track("V1", "V1", "video", clips=[clip])])
    site = _site(project, clip)
    job = composition_chunk_job(project, site, 0, quality="high")
    engine = PreviewEngine(task_queue=TaskQueue(), cache=DiskPreviewCache(directory=tmp_path / "cache"))
    engine.request(job)
    _drain(engine)
    path = engine.cache.lookup(job.key)
    assert path is not None, engine.state().last_error
    decoded = subprocess.run(["ffmpeg", "-v", "error", "-ss", "1.0", "-i", str(path), "-frames:v", "1", "-f", "rawvideo",
                              "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    frame = np.frombuffer(decoded, dtype=np.uint8).reshape(H, W, 3).astype(float)
    expected = render_frame(build_render_plan(neutral_project(project, site)), W, H, 1.0).astype(float)
    assert np.abs(frame - expected).mean() < 4.0, "l'image de l'export, sans le transform du clip"
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-skip_frame", "nokey", "-show_entries",
                            "frame=pts_time", "-of", "json", str(path)], capture_output=True, check=True).stdout
    keys = [float(frame["pts_time"]) for frame in json.loads(probe)["frames"]]
    assert len(keys) > 1 and max(b - a for a, b in zip(keys, keys[1:])) <= 12 / 25 + 1e-3
