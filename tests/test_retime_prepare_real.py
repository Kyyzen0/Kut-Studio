"""Mélange d'images et flux optique de bout en bout, avec le vrai FFmpeg : projet → plan → préparation → graphe → images décodées.

Deux médias de test. Le premier a pour **luminance son indice** (``20 + 3·N``, sans perte) : un mélange ``A·(1−t) + B·t`` y vaut
exactement ``indice = N + t``, donc le poids se lit à 0,33 près sur le pixel central. Le second montre un carré texturé qui
se déplace de 8 px par image (``tests/flow_scenes.py``) : l'image « vraie » à une position fractionnaire est connue, et le flux
optique doit l'approcher bien mieux qu'un mélange.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
from dataclasses import replace

import numpy as np
import pytest
from flow_scenes import Body, Scene, linear

from core.animation import InterpolationType, Keyframe
from core.export_engine import ExportEngine
from core.flow_cache import FlowCache
from core.project_model import Clip, MediaAsset, Project, Track
from core.render_plan import build_render_plan
from core.retime_graph import RetimeError
from core.retime_layers import PrepareCancelled, plan_needs_preparation, prepare_plan
from core.time_map import SPEED_PROPERTY
from core.time_remapping import FlowQuality, TimeInterpolation, TimeRemapping

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg absent")

W, H, FPS = 64, 36, 30
SOURCE_FRAMES = 60
BLENDING, FLOW, SAMPLING = TimeInterpolation.BLENDING, TimeInterpolation.OPTICAL_FLOW, TimeInterpolation.SAMPLING
SW, SH = 320, 180
SCENE_FRAMES = 12
INDEX_TOLERANCE = 0.45 if platform.machine().lower() in ("arm64", "aarch64") else 0.9
"""Écart toléré (en indice de média : 1 niveau de luminance = 0,33) entre une image préparée et l'image idéale. Le fichier préparé
est en RVB : l'image passe par yuv → rvb → yuv. Sur arm64 la conversion de swscale arrondit au plus près (écart ≤ 1,35 niveau).
Sur x86 (Linux et Windows en intégration continue, mesurés identiques) elle retombe de façon déterministe jusqu'à un niveau plus
bas : 24,33 pour 24,8 attendus, 0,0 pour 0,5. C'est l'« un niveau de gris près » documenté, pas une erreur de poids ni de position.
Les poids eux-mêmes sont vérifiés exactement par ``tests/test_frame_interpolation.py`` ; ce test-ci vérifie que le fichier relu par
FFmpeg montre bien la bonne paire d'images (un écart d'une image vaut 1,0, donc reste détecté sur arm64, plus strict)."""


@pytest.fixture(scope="module")
def index_media(tmp_path_factory):
    path = tmp_path_factory.mktemp("prepare") / "index.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
         f"color=c=black:s={W}x{H}:r={FPS}:d={SOURCE_FRAMES / FPS},geq=lum='20+3*N':cb=128:cr=128,format=yuv420p",
         "-c:v", "libx264", "-crf", "0", "-preset", "ultrafast", str(path)],
        check=True, timeout=60,
    )
    return str(path)


SCENE = Scene(SW, SH, [Body(60, linear(60, 90, 8, 0))])


def _encode_scene(path, step: float, count: int) -> str:
    """Le carré rendu aux instants ``0, step, 2·step…`` (``count`` images), encodé sans perte par le chemin gris → YUV 4:2:0."""
    frames = np.stack([np.clip(SCENE.render(i * step) * 255.0 + 0.5, 0, 255).astype(np.uint8) for i in range(count)])
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{SW}x{SH}", "-r", str(FPS), "-i", "-",
         "-vf", "format=yuv420p", "-c:v", "libx264", "-crf", "0", "-preset", "ultrafast", str(path)],
        input=frames.tobytes(), check=True, timeout=60,
    )
    return str(path)


@pytest.fixture(scope="module")
def scene_media(tmp_path_factory):
    return _encode_scene(tmp_path_factory.mktemp("scene") / "scene.mp4", 1.0, SCENE_FRAMES)


@pytest.fixture(scope="module")
def truth_media(tmp_path_factory):
    """La même scène à la **double cadence** : l'image ``k`` est la vérité à la position ``k / 2``, par le même chemin de
    conversion que le média interpolé (plancher d'erreur nul : tout écart est celui de l'interpolation)."""
    return _encode_scene(tmp_path_factory.mktemp("truth") / "truth.mp4", 0.5, SCENE_FRAMES * 2)


@pytest.fixture
def cache(tmp_path):
    return FlowCache(tmp_path / "flow")


def make_project(path, *, frames, width, height, source=None, remapping=None, animation=()):
    duration = frames / FPS
    asset = MediaAsset(id="a", path=path, name="m", duration=duration, width=width, height=height, fps=float(FPS),
                       media_type="video", has_audio=False)
    clip = Clip(id="c1", asset_id="a", track_id="V1", timeline_start=0.0, source_in=0.0, source_out=duration if source is None else source,
                time_remapping=remapping or TimeRemapping(), animation=list(animation))
    project = Project(name="p", width=width, height=height, fps=float(FPS), media_assets=[asset],
                      tracks=[Track(id="V1", name="V1", type="video", clips=[clip])])
    return project, clip


def render(project, width, height, cache, *, prepared=None, cancelled=None, window=None):
    """Images exportées (``yuv420p``, plan Y seul) et la préparation utilisée."""
    plan = build_render_plan(project)
    preparation = prepare_plan(plan, width, height, FPS, cache, cancelled=cancelled, window=window) if prepared is None else None
    streams = preparation.streams if preparation is not None else prepared
    graph, video, audio, inputs = ExportEngine._build_filter_complex(plan, width, height, FPS, None, prepared=streams)
    graph += f";[{video}]format=yuv420p[probe]"
    if audio:
        graph += f";[{audio}]anullsink"
    command = ["ffmpeg", "-y", "-loglevel", "error"]
    for item in inputs:
        command += ["-i", item]
    command += ["-filter_complex", graph, "-map", "[probe]", "-f", "rawvideo", "-pix_fmt", "yuv420p", "-"]
    done = subprocess.run(command, capture_output=True, timeout=300)
    assert done.returncode == 0, done.stderr.decode(errors="replace")[-800:]
    size = width * height * 3 // 2
    frames = np.frombuffer(done.stdout, dtype=np.uint8).reshape(-1, size)[:, : width * height].reshape(-1, height, width)
    return frames, preparation


def index_values(frames: np.ndarray) -> list[float]:
    """Indice (réel) lu sur le pixel central : ``20 + 3·x`` -> ``x``."""
    centre = frames[:, frames.shape[1] // 2, frames.shape[2] // 2].astype(float)
    return [(value - 20.0) / 3.0 for value in centre]


# ---------------------------------------------------------------------------
# Mélange d'images : les poids sont exacts
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("speed", [0.5, 0.25, 0.4, 0.8])
def test_blending_weights_follow_the_time_map_exactly(index_media, cache, speed):
    project, clip = make_project(index_media, frames=SOURCE_FRAMES, width=W, height=H,
                                 remapping=TimeRemapping(speed=speed, interpolation=BLENDING))
    frames, preparation = render(project, W, H, cache)
    got = index_values(frames)[: len(frames)]
    assert preparation is not None and preparation.report.synthesized > 0
    for tick, value in enumerate(got[:-3]):
        expected = clip.time_map.source_time(tick / FPS) * FPS
        assert abs(value - expected) < INDEX_TOLERANCE, (speed, tick, value, expected)             # 1 niveau de luminance = 0,33 indice


def test_images_that_land_on_a_source_frame_are_identical_to_the_sampled_ones(index_media, cache):
    """À 50 % un tick sur deux tombe sur une image source : elle est celle de l'échantillonnage, au niveau de gris près."""
    blended, _ = render(make_project(index_media, frames=SOURCE_FRAMES, width=W, height=H,
                                     remapping=TimeRemapping(speed=0.5, interpolation=BLENDING))[0], W, H, cache)
    sampled, _ = render(make_project(index_media, frames=SOURCE_FRAMES, width=W, height=H,
                                     remapping=TimeRemapping(speed=0.5, interpolation=SAMPLING))[0], W, H, cache)
    assert blended.shape == sampled.shape
    exact = np.arange(0, len(blended) - 2, 2)
    assert int(np.abs(blended[exact].astype(int) - sampled[exact].astype(int)).max()) <= 1


def test_the_exported_clip_keeps_its_exact_duration_in_frames(index_media, cache):
    project, clip = make_project(index_media, frames=SOURCE_FRAMES, width=W, height=H,
                                 remapping=TimeRemapping(speed=0.25, interpolation=BLENDING))
    frames, _ = render(project, W, H, cache)
    assert len(frames) == 240 == round(clip.duration * FPS)


def test_a_reverse_blend_mirrors_the_weights(index_media, cache):
    project, clip = make_project(index_media, frames=SOURCE_FRAMES, width=W, height=H,
                                 remapping=TimeRemapping(speed=0.5, reverse=True, interpolation=BLENDING))
    frames, preparation = render(project, W, H, cache)
    assert preparation is not None
    got = index_values(frames)
    for tick in range(2, len(got) - 3):
        expected = min(59.0, clip.time_map.source_time(tick / FPS) * FPS)
        assert abs(got[tick] - expected) < INDEX_TOLERANCE, (tick, got[tick], expected)


def test_a_speed_ramp_blends_where_it_is_slow_and_samples_where_it_is_fast(index_media, cache):
    """0,5× puis 3× : le premier run est préparé, le second reste dans l'échantillonnage (aucune image intermédiaire)."""
    keys = [Keyframe(SPEED_PROPERTY, 0.0, 0.5, InterpolationType.HOLD), Keyframe(SPEED_PROPERTY, 1.0, 3.0, InterpolationType.HOLD)]
    project, clip = make_project(index_media, frames=SOURCE_FRAMES, width=W, height=H, source=1.9,
                                 remapping=TimeRemapping(interpolation=BLENDING, anchor=0.0), animation=keys)
    frames, preparation = render(project, W, H, cache)
    got = index_values(frames)
    assert preparation is not None and preparation.streams
    for tick in list(range(0, 28)) + list(range(36, len(got) - 3)):
        expected = clip.time_map.source_time(tick / FPS) * FPS
        tolerance = INDEX_TOLERANCE if tick < 30 else 0.9                                          # échantillonné : l'image la plus proche
        assert abs(got[tick] - expected) < tolerance, (tick, got[tick], expected)


# ---------------------------------------------------------------------------
# Ce qui n'a pas besoin de préparation n'en reçoit pas
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", [BLENDING, FLOW])
@pytest.mark.parametrize("speed", [1.0, 2.0, 4.0])
def test_a_clip_with_no_intermediate_image_is_not_prepared_and_matches_sampling(index_media, cache, mode, speed):
    remapping = TimeRemapping(speed=speed, interpolation=mode)
    project, _clip = make_project(index_media, frames=SOURCE_FRAMES, width=W, height=H, remapping=remapping)
    plan = build_render_plan(project)
    assert not plan_needs_preparation(plan, W, H, FPS)
    frames, preparation = render(project, W, H, cache)
    assert preparation is not None and preparation.streams == {}
    reference, _ = render(make_project(index_media, frames=SOURCE_FRAMES, width=W, height=H,
                                       remapping=replace(remapping, interpolation=SAMPLING))[0], W, H, cache)
    assert np.array_equal(frames, reference)                                            # mêmes images, au bit près


def test_the_graph_refuses_a_clip_whose_images_were_not_prepared(index_media):
    project, _clip = make_project(index_media, frames=SOURCE_FRAMES, width=W, height=H,
                                  remapping=TimeRemapping(speed=0.5, interpolation=BLENDING))
    with pytest.raises(RetimeError, match="n'ont pas été préparées"):
        ExportEngine._build_filter_complex(build_render_plan(project), W, H, FPS, None)


def test_the_graph_refuses_prepared_images_that_no_longer_match_the_clip(index_media, cache):
    project, clip = make_project(index_media, frames=SOURCE_FRAMES, width=W, height=H,
                                 remapping=TimeRemapping(speed=0.5, interpolation=BLENDING))
    stale = prepare_plan(build_render_plan(project), W, H, FPS, cache).streams
    clip.time_remapping = replace(clip.time_remapping, speed=0.25)                       # le temps change après la préparation
    with pytest.raises(RetimeError, match="périmées"):
        ExportEngine._build_filter_complex(build_render_plan(project), W, H, FPS, None, prepared=stale)


# ---------------------------------------------------------------------------
# Flux optique sur un média réel
# ---------------------------------------------------------------------------


def region_error(frames: np.ndarray, truth: np.ndarray, ticks: list[int]) -> float:
    """Erreur moyenne (0…255) sur la région de l'objet entre l'export interpolé et la vérité, tick à tick."""
    errors = []
    for tick in ticks:
        position = tick * 0.5
        mask = np.zeros((SH, SW), dtype=bool)
        for moment in (np.floor(position), np.floor(position) + 1, position):
            mask |= SCENE.render(float(moment)) > 0.55
        errors.append(float(np.abs(frames[tick].astype(float) - truth[tick].astype(float))[mask].mean()))
    return float(np.mean(errors))


def test_optical_flow_follows_the_moving_object_on_a_real_file_far_better_than_a_blend(scene_media, truth_media, cache):
    truth, _ = render(make_project(truth_media, frames=SCENE_FRAMES * 2, width=SW, height=SH,
                                   remapping=TimeRemapping(interpolation=SAMPLING))[0], SW, SH, cache)
    ticks = [1, 3, 5, 7, 9, 11, 13]                                                      # les images à 50 % : positions 0,5 ; 1,5…
    results = {}
    for mode in (FLOW, BLENDING):
        project, _clip = make_project(scene_media, frames=SCENE_FRAMES, width=SW, height=SH,
                                      remapping=TimeRemapping(speed=0.5, interpolation=mode, flow_quality=FlowQuality.BEST))
        frames, preparation = render(project, SW, SH, cache)
        assert preparation is not None and preparation.report.synthesized > 0
        results[mode] = (region_error(frames, truth, ticks), preparation)
    flow_error, flow_preparation = results[FLOW]
    blend_error, _ = results[BLENDING]
    assert flow_error < 3.0, flow_error                                                  # niveaux de gris (0…255), plancher nul
    assert flow_error < 0.25 * blend_error, (flow_error, blend_error)
    assert flow_preparation.report.mean_confidence > 0.9 and flow_preparation.report.degraded == 0
    assert flow_preparation.report.backend == "numpy" and flow_preparation.report.pairs_computed > 0


def test_changing_the_speed_reuses_every_motion_vector(scene_media, cache):
    """Le mouvement est celui du média : passer de 50 % à 40 % ne recalcule aucune paire, il refabrique seulement les images."""
    first = make_project(scene_media, frames=SCENE_FRAMES, width=SW, height=SH,
                         remapping=TimeRemapping(speed=0.5, interpolation=FLOW, flow_quality=FlowQuality.BALANCED))[0]
    preparation = prepare_plan(build_render_plan(first), SW, SH, FPS, cache)
    computed = preparation.report.pairs_computed
    assert computed > 0 and preparation.report.pairs_cached == 0
    second = make_project(scene_media, frames=SCENE_FRAMES, width=SW, height=SH,
                          remapping=TimeRemapping(speed=0.4, interpolation=FLOW, flow_quality=FlowQuality.BALANCED))[0]
    again = prepare_plan(build_render_plan(second), SW, SH, FPS, cache)
    assert again.report.pairs_computed == 0 and again.report.pairs_cached > 0
    assert not again.report.reused                                                     # d'autres images : le flux est refait


def test_an_unchanged_clip_reuses_its_prepared_stream(scene_media, cache):
    project = make_project(scene_media, frames=SCENE_FRAMES, width=SW, height=SH,
                           remapping=TimeRemapping(speed=0.5, interpolation=BLENDING))[0]
    first = prepare_plan(build_render_plan(project), SW, SH, FPS, cache)
    second = prepare_plan(build_render_plan(project), SW, SH, FPS, cache)
    assert not first.report.reused and second.report.reused
    assert next(iter(first.streams.values())).path == next(iter(second.streams.values())).path


def test_a_cold_and_a_warm_cache_export_the_same_pixels(scene_media, tmp_path):
    project = make_project(scene_media, frames=SCENE_FRAMES, width=SW, height=SH,
                           remapping=TimeRemapping(speed=0.5, interpolation=FLOW, flow_quality=FlowQuality.BALANCED))[0]
    cold, first = render(project, SW, SH, FlowCache(tmp_path / "one"))
    vectors_only = FlowCache(tmp_path / "two")
    render(project, SW, SH, vectors_only)
    for stream in (tmp_path / "two").glob("frames-*"):                                  # on garde les vecteurs, pas les images
        stream.unlink()
    warm, second = render(project, SW, SH, vectors_only)
    assert first is not None and second is not None
    assert first.report.pairs_computed > 0 and second.report.pairs_computed == 0 and second.report.pairs_cached > 0
    assert np.array_equal(cold, warm)                                                   # le cache ne change jamais le rendu


# ---------------------------------------------------------------------------
# Annulation, atomicité
# ---------------------------------------------------------------------------


def test_cancelling_keeps_nothing(scene_media, cache):
    project = make_project(scene_media, frames=SCENE_FRAMES, width=SW, height=SH,
                           remapping=TimeRemapping(speed=0.5, interpolation=FLOW))[0]
    calls = {"count": 0}

    def cancelled() -> bool:
        calls["count"] += 1
        return calls["count"] > 6

    with pytest.raises(PrepareCancelled):
        prepare_plan(build_render_plan(project), SW, SH, FPS, cache, cancelled=cancelled)
    leftovers = [item.name for item in cache.directory.iterdir() if item.name.startswith(("frames-", ".frames-"))] if cache.directory.exists() else []
    assert leftovers == []                                                              # ni flux promu, ni fichier partiel
    assert cache.has_stream("0" * 40) is False


def test_progress_reaches_the_total_in_order(scene_media, cache):
    project = make_project(scene_media, frames=SCENE_FRAMES, width=SW, height=SH,
                           remapping=TimeRemapping(speed=0.5, interpolation=BLENDING))[0]
    seen: list[tuple[int, int]] = []
    prepare_plan(build_render_plan(project), SW, SH, FPS, cache, progress=lambda done, total: seen.append((done, total)))
    assert seen and seen[-1][0] == seen[-1][1] > 0
    assert [done for done, _total in seen] == sorted(done for done, _total in seen)


# ---------------------------------------------------------------------------
# Aperçu fenêtré : seuls les ticks du segment sont fabriqués
# ---------------------------------------------------------------------------

BLACK_Y = 16                                                                             # le noir, en luminance de plage limitée


def test_a_windowed_preparation_makes_only_the_window_and_matches_the_full_one_inside_it(index_media, cache, tmp_path):
    project = make_project(index_media, frames=SOURCE_FRAMES, width=W, height=H,
                           remapping=TimeRemapping(speed=0.25, interpolation=BLENDING))[0]
    full, full_preparation = render(project, W, H, FlowCache(tmp_path / "full"))
    windowed, preparation = render(project, W, H, FlowCache(tmp_path / "window"), window=(2.0, 3.0))
    assert full_preparation is not None and preparation is not None
    assert preparation.report.images < full_preparation.report.images / 3                 # une seconde sur huit, plus la marge
    assert windowed.shape == full.shape                                                   # la durée du clip est conservée
    low, high = 2 * FPS, 3 * FPS
    assert np.array_equal(windowed[low:high], full[low:high])                             # identique, au bit près
    assert int(windowed[: low - 4].max()) == BLACK_Y and int(windowed[high + 4:].max()) == BLACK_Y   # le reste est du noir jeté


def test_a_run_the_window_does_not_reach_keeps_one_held_frame_and_its_duration(index_media, cache):
    """Deux ralentis séparés par un arrêt : la fenêtre n'atteint que le premier, le second reste valide et de même durée."""
    keys = [Keyframe(SPEED_PROPERTY, 0.0, 0.5, InterpolationType.HOLD), Keyframe(SPEED_PROPERTY, 1.0, 0.0, InterpolationType.HOLD),
            Keyframe(SPEED_PROPERTY, 2.0, 0.5, InterpolationType.HOLD)]
    project = make_project(index_media, frames=SOURCE_FRAMES, width=W, height=H, source=1.5,
                           remapping=TimeRemapping(interpolation=BLENDING, anchor=0.0), animation=keys)[0]
    clip = project.tracks[0].clips[0]
    full, _ = render(project, W, H, cache)
    windowed, preparation = render(project, W, H, cache, window=(0.0, 0.5))
    assert windowed.shape == full.shape and preparation is not None
    assert np.array_equal(windowed[: FPS // 2], full[: FPS // 2])
    runs = {run.run_index: run for stream in preparation.streams.values() for run in stream.runs}
    assert len(runs) == 2 and runs[2].frames == 1 and runs[2].trail == runs[2].ticks - 1  # le second run : une image tenue
    assert clip.duration * FPS > 90


def test_the_local_window_covers_the_segment_with_a_margin(index_media):
    from core.retime_layers import WINDOW_MARGIN_TICKS, local_window

    project = make_project(index_media, frames=SOURCE_FRAMES, width=W, height=H,
                           remapping=TimeRemapping(speed=0.5, interpolation=BLENDING))[0]
    layer = build_render_plan(project).video_layers[0]
    assert local_window(layer, (2.0, 4.0), FPS) == (2 * FPS - WINDOW_MARGIN_TICKS, 4 * FPS + WINDOW_MARGIN_TICKS)
    assert local_window(layer, (-5.0, 1.0), FPS)[0] == 0                                   # jamais avant le début du clip


# ---------------------------------------------------------------------------
# Séquences imbriquées : un clip interpolé dans l'enfant
# ---------------------------------------------------------------------------


def _nested_project(index_media, inner_remapping):
    from core.project_model import Sequence
    from core.sequences import insert_sequence_clip

    asset = MediaAsset(id="a", path=index_media, name="m", duration=SOURCE_FRAMES / FPS, width=W, height=H, fps=float(FPS),
                       media_type="video", has_audio=False)
    inner_clip = Clip(id="n1", asset_id="a", track_id="V1", timeline_start=0.0, source_in=0.0, source_out=2.0,
                      time_remapping=inner_remapping)
    inner = Sequence(id="inner", name="Inner", width=W, height=H, fps=float(FPS),
                     tracks=[Track(id="V1", name="V1", type="video", clips=[inner_clip])])
    main = Sequence(id="main", name="Main", width=W, height=H, fps=float(FPS), tracks=[Track(id="V1", name="V1", type="video")])
    project = Project(name="p", width=W, height=H, fps=float(FPS), media_assets=[asset], sequences=[main, inner],
                      active_sequence_id="main")
    insert_sequence_clip(project, "inner", "V1", 0.0)
    return project, inner_clip


def test_an_interpolating_clip_inside_a_nested_sequence_is_prepared_at_the_size_the_graph_reads(index_media, cache):
    project, inner_clip = _nested_project(index_media, TimeRemapping(speed=0.5, interpolation=BLENDING))
    plan = build_render_plan(project)
    assert plan_needs_preparation(plan, W, H, FPS)
    frames, preparation = render(project, W, H, cache)
    assert preparation is not None and list(preparation.streams) == ["n1"]
    got = index_values(frames)
    for tick in range(0, len(got) - 3):
        expected = inner_clip.time_map.source_time(tick / FPS) * FPS
        assert abs(got[tick] - expected) < INDEX_TOLERANCE, (tick, got[tick], expected)


def test_a_nested_sequence_set_to_an_interpolation_by_hand_is_refused_not_silently_sampled(index_media):
    from core.sequences import insert_sequence_clip  # noqa: F401 - la séquence imbriquée existe

    project, _inner = _nested_project(index_media, TimeRemapping())
    nested = project.tracks[0].clips[0]
    nested.time_remapping = TimeRemapping(speed=0.5, interpolation=BLENDING)               # impossible par l'interface : un .kut écrit à la main
    with pytest.raises(RetimeError, match="séquence imbriquée"):
        ExportEngine._build_filter_complex(build_render_plan(project), W, H, FPS, None)
    nested.time_remapping = TimeRemapping(speed=2.0, interpolation=FLOW)                    # 200 % : rien à fabriquer, donc rien à refuser
    ExportEngine._build_filter_complex(build_render_plan(project), W, H, FPS, None)


# ---------------------------------------------------------------------------
# Scopes : une image, sans geler l'interface
# ---------------------------------------------------------------------------


def test_the_sampling_plan_changes_nothing_but_the_interpolation_of_each_clip(index_media):
    from core.retime_layers import sampling_plan

    project, _clip = make_project(index_media, frames=SOURCE_FRAMES, width=W, height=H,
                                  remapping=TimeRemapping(speed=0.5, interpolation=FLOW, flow_quality=FlowQuality.BEST))
    plan = build_render_plan(project)
    simple = sampling_plan(plan)
    layer, original = simple.video_layers[0], plan.video_layers[0]
    assert layer.time_remapping.interpolation is SAMPLING and layer.time_remapping.flow_quality is FlowQuality.BEST
    assert layer.time_remapping.speed == original.time_remapping.speed == 0.5 and layer.time_map == original.time_map
    assert original.time_remapping.interpolation is FLOW                                    # le plan d'origine n'est pas modifié
    assert not plan_needs_preparation(simple, W, H, FPS) and plan_needs_preparation(plan, W, H, FPS)


def test_the_frame_command_of_the_scopes_never_prepares_unless_asked(index_media, tmp_path):
    from core.export_engine import ExportFormat, ExportPreset, ExportRequest

    project, _clip = make_project(index_media, frames=SOURCE_FRAMES, width=W, height=H,
                                  remapping=TimeRemapping(speed=0.25, interpolation=BLENDING))
    request = ExportRequest(render_plan=build_render_plan(project), output_path=str(tmp_path / "f.png"),
                            format=ExportFormat.MP4_H264, preset=ExportPreset("T", (W, H), 18, "96k"), fps=FPS)
    engine = ExportEngine()
    engine.flow_cache = FlowCache(tmp_path / "flow")
    done = subprocess.run(engine.build_frame_command(request, 1.0), capture_output=True, timeout=60)
    assert done.returncode == 0 and done.stdout[:4] == b"\x89PNG"
    assert not (tmp_path / "flow").exists() or not list((tmp_path / "flow").glob("frames-*"))   # rien n'a été fabriqué
    exact = subprocess.run(engine.build_frame_command(request, 1.0, interpolate=True), capture_output=True, timeout=60)
    assert exact.returncode == 0 and len(list((tmp_path / "flow").glob("frames-*.mkv"))) == 1       # sur demande : l'image est fabriquée


# ---------------------------------------------------------------------------
# Un conteneur dont le temps ne démarre pas à zéro (MPEG-TS : 1,4 s ; piste vidéo décalée par rapport à l'audio)
# ---------------------------------------------------------------------------

OFFSETS = {"one-frame": (".mkv", ["-output_ts_offset", "0.0333333"]), "one-second": (".mkv", ["-output_ts_offset", "1.0"]),
           "mpegts": (".ts", [])}


@pytest.fixture(scope="module", params=sorted(OFFSETS))
def offset_media(request, tmp_path_factory):
    extension, options = OFFSETS[request.param]
    path = tmp_path_factory.mktemp("offset") / f"index{extension}"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
         f"color=c=black:s={W}x{H}:r={FPS}:d={SOURCE_FRAMES / FPS},geq=lum='20+3*N':cb=128:cr=128,format=yuv420p",
         "-c:v", "libx264", "-crf", "0", "-preset", "ultrafast", *options, str(path)],
        check=True, timeout=60,
    )
    start = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=start_time", "-of", "csv=p=0", str(path)],
                           capture_output=True, text=True, timeout=30).stdout.strip()
    assert float(start) > 0.02, f"le média de test doit démarrer après zéro, il démarre à {start}"
    return str(path)


@pytest.mark.parametrize("source_in", [0.0, 0.4])
def test_the_prepared_images_do_not_depend_on_where_the_container_clock_starts(offset_media, cache, source_in):
    project, clip = make_project(offset_media, frames=SOURCE_FRAMES, width=W, height=H, source=1.8,
                                 remapping=TimeRemapping(speed=0.5, interpolation=BLENDING))
    clip.source_in = source_in
    frames, preparation = render(project, W, H, cache)
    assert preparation is not None and preparation.report.synthesized > 0
    got = index_values(frames)
    for tick in range(0, len(got) - 3):
        expected = clip.time_map.source_time(tick / FPS) * FPS
        assert abs(got[tick] - expected) < INDEX_TOLERANCE, (source_in, tick, got[tick], expected)


# ---------------------------------------------------------------------------
# Un son plus long que l'image : la durée du conteneur annonce des images qui n'existent pas
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def long_audio_media(tmp_path_factory):
    path = tmp_path_factory.mktemp("longaudio") / "index.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
         f"color=c=black:s={W}x{H}:r={FPS}:d={SOURCE_FRAMES / FPS},geq=lum='20+3*N':cb=128:cr=128,format=yuv420p",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={SOURCE_FRAMES / FPS + 0.5}",
         "-c:v", "libx264", "-crf", "0", "-preset", "ultrafast", "-c:a", "aac", str(path)],
        check=True, timeout=60,
    )
    duration = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                                    capture_output=True, text=True, timeout=30).stdout)
    assert duration > SOURCE_FRAMES / FPS + 0.3, "le conteneur de test doit être plus long que sa vidéo"
    return str(path), duration


@pytest.mark.parametrize("mode", [BLENDING, FLOW])
def test_a_container_longer_than_its_video_repeats_the_last_image_like_the_sampling_graph(long_audio_media, cache, mode):
    path, container = long_audio_media
    frames_announced = round(container * FPS)
    assert frames_announced > SOURCE_FRAMES                                              # c'est bien le défaut qu'on reproduit
    project, clip = make_project(path, frames=frames_announced, width=W, height=H,
                                 remapping=TimeRemapping(speed=0.5, interpolation=mode))
    frames, preparation = render(project, W, H, cache)
    assert preparation is not None and preparation.streams
    got = index_values(frames)
    assert len(got) >= 2 * SOURCE_FRAMES
    for tick in (0, 10, 40, 80, 100):
        expected = min(SOURCE_FRAMES - 1.0, clip.time_map.source_time(tick / FPS) * FPS)
        assert abs(got[tick] - expected) < 0.9, (tick, got[tick], expected)
    assert all(abs(value - (SOURCE_FRAMES - 1.0)) < 0.5 for value in got[-5:])           # le dernier repère tient jusqu'à la fin
