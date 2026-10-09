"""Export réel (FFmpeg) du temps d'un clip : la séquence d'images exportée est celle que prédit le modèle.

Source : 60 images à 30 i/s dont la **luminance est l'indice** (``20 + 3·N``, sans perte) ; on lit la luminance du pixel
central de chaque image exportée, donc l'indice de l'image source montrée, et on compare, tick par tick, à
``core.retime_graph.frame_for_tick`` (« l'image source la plus proche de ``M(T)`` »). Le chemin est complet : projet →
``RenderPlan`` → graphe de l'export (le même que celui de l'aperçu fidèle) → images décodées.

La propriété centrale du chantier se vérifie ici : **couper ou rogner un clip à courbe de vitesse ne change aucune image de
la séquence exportée**.
"""

from __future__ import annotations

import shutil
import subprocess

import numpy as np
import pytest

from core.animation import InterpolationType, Keyframe
from core.export_engine import ExportEngine, input_arguments
from core.project_model import Clip, MediaAsset, Project, Track
from core.render_plan import build_render_plan
from core.retime_graph import frame_for_tick, ticks_in
from core.sequences import insert_sequence_clip
from core.time_map import SPEED_PROPERTY
from core.time_ops import add_speed_point
from core.time_remapping import TimeRemapping
from core.timeline_operations import cut_clip, set_clip_reverse, set_clip_speed, trim_clip_left, trim_clip_right

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg absent")

W, H, FPS = 64, 36, 30
SOURCE_FRAMES = 60


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    path = tmp_path_factory.mktemp("retime") / "index.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
         f"color=c=black:s={W}x{H}:r={FPS}:d={SOURCE_FRAMES / FPS},geq=lum='20+3*N':cb=128:cr=128,format=yuv420p",
         "-c:v", "libx264", "-crf", "0", "-preset", "ultrafast", str(path)],
        check=True, timeout=60,
    )
    return str(path)


def _project(media_path, *, source=(0.0, 2.0), remapping=None, animation=(), start=0.0):
    asset = MediaAsset(id="a", path=media_path, name="idx", duration=SOURCE_FRAMES / FPS, width=W, height=H,
                       fps=float(FPS), media_type="video", has_audio=False)
    clip = Clip(id="c1", asset_id="a", track_id="V1", timeline_start=start, source_in=source[0], source_out=source[1],
                time_remapping=remapping or TimeRemapping(), animation=list(animation))
    project = Project(name="p", width=W, height=H, fps=float(FPS), media_assets=[asset],
                      tracks=[Track(id="V1", name="V1", type="video", clips=[clip])])
    return project, clip


def exported_indices(project) -> list[int]:
    """Indice source (luminance du pixel central) de chaque image exportée, de 0 à la fin de la timeline."""
    plan = build_render_plan(project)
    graph, video, audio, inputs = ExportEngine._build_filter_complex(plan, W, H, FPS, None)
    graph += f";[{video}]format=yuv420p[probe]"
    command = ["ffmpeg", "-y", "-loglevel", "error"]
    for path in inputs:
        command += input_arguments(path)
    command += ["-filter_complex", graph, "-map", "[probe]", "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "yuv420p", "-"]
    if audio:                                   # 2de sortie plutôt qu'un puits : FFmpeg 7.x avorte sur un puits nourri par un son généré
        command += ["-map", f"[{audio}]", "-f", "null", "-"]
    done = subprocess.run(command, capture_output=True, timeout=120)
    assert done.returncode == 0, done.stderr.decode(errors="replace")[-800:]
    size = W * H * 3 // 2
    frames = np.frombuffer(done.stdout, dtype=np.uint8).reshape(-1, size)
    centre = frames[:, (H // 2) * W + W // 2].astype(float)
    return [int(round((value - 20.0) / 3.0)) for value in centre]


def model_indices(clip: Clip, *, count: int | None = None) -> list[int]:
    time_map = clip.time_map
    count = ticks_in(time_map.duration, float(FPS)) if count is None else count
    return [frame_for_tick(time_map, k, float(FPS), float(FPS), SOURCE_FRAMES - 1) for k in range(count)]


def assert_matches_model(project, clip, *, ties=False, tolerance=0.04):
    got = exported_indices(project)
    want = model_indices(clip)
    assert len(got) >= len(want)
    got = got[: len(want)]
    if not ties:
        assert got == want
        return
    time_map = clip.time_map
    for k, (g, w) in enumerate(zip(got, want)):
        if g == w:
            continue
        x = time_map.source_time(k / FPS) * FPS
        assert abs((x % 1.0) - 0.5) < tolerance and abs(g - w) == 1, (k, g, w, x)


# ---------------------------------------------------------------------------
# Vitesse constante
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("speed", [0.25, 0.5, 1.5, 2.0, 4.0])
def test_a_constant_speed_exports_the_nearest_source_frame_at_every_tick(media, speed):
    project, clip = _project(media, remapping=TimeRemapping(speed=speed))
    assert_matches_model(project, clip)


def test_two_times_speed_exports_frames_zero_two_four(media):
    """L'ancienne chaîne exportait 1, 3, 5… (elle sautait l'image 0 dès qu'on accélérait)."""
    project, _clip = _project(media, remapping=TimeRemapping(speed=2.0))
    assert exported_indices(project)[:6] == [0, 2, 4, 6, 8, 10]


@pytest.mark.parametrize("speed", [0.5, 1.0, 2.0])
def test_reverse_exports_the_nearest_source_frame_at_every_tick(media, speed):
    project, clip = _project(media, remapping=TimeRemapping(speed=speed, reverse=True))
    assert_matches_model(project, clip)


def test_the_exported_clip_has_exactly_its_duration_in_frames(media):
    project, clip = _project(media, remapping=TimeRemapping(speed=0.25))
    assert len(exported_indices(project)) == ticks_in(clip.duration, float(FPS)) == 240


# ---------------------------------------------------------------------------
# Courbe de vitesse
# ---------------------------------------------------------------------------


def _ramp():
    return [
        Keyframe(SPEED_PROPERTY, 0.0, 1.0, InterpolationType.LINEAR),
        Keyframe(SPEED_PROPERTY, 0.5, 1.0, InterpolationType.LINEAR),
        Keyframe(SPEED_PROPERTY, 1.0, 0.25, InterpolationType.LINEAR),
        Keyframe(SPEED_PROPERTY, 2.0, 0.25, InterpolationType.LINEAR),
        Keyframe(SPEED_PROPERTY, 2.5, 2.0, InterpolationType.LINEAR),
    ]


def test_a_speed_ramp_exports_the_nearest_frame_up_to_ties(media):
    """100 % → 25 % → 200 % : chaque image exportée est la plus proche de ``M(T)`` (un écart d'une image n'est permis
    qu'à égalité, dans la tolérance de l'approximation par morceaux)."""
    project, clip = _project(media, animation=_ramp())
    assert clip.has_speed_curve and clip.duration > 2.5
    assert_matches_model(project, clip, ties=True)


def test_100_then_0_then_minus_100_percent_exports_a_clip_that_stops_and_comes_back(media):
    keys = [Keyframe(SPEED_PROPERTY, 0.0, 1.0), Keyframe(SPEED_PROPERTY, 1.0, -1.0)]
    project, clip = _project(media, source=(0.0, 2.0), remapping=TimeRemapping(anchor=0.5), animation=keys)
    got = exported_indices(project)
    want = model_indices(clip)
    assert got[: len(want)] == want
    peak = max(got)
    assert got.index(peak) > 0 and got[-1] < peak and got[0] == want[0]


# ---------------------------------------------------------------------------
# Couper et rogner ne changent aucune image exportée
# ---------------------------------------------------------------------------


def test_cutting_a_ramped_clip_on_a_frame_leaves_the_exported_sequence_unchanged(media):
    project, clip = _project(media, animation=_ramp())
    before = exported_indices(project)
    cut_tick = 31                                                      # en plein milieu de la rampe 100 % → 25 %
    cut_clip(project, "c1", cut_tick / FPS)
    after = exported_indices(project)
    assert len(after) == len(before) and after == before


@pytest.mark.parametrize("cut_tick", [7, 14, 29, 31, 45, 57, 59])
def test_cutting_a_plain_clip_on_a_frame_leaves_the_exported_sequence_unchanged(media, cut_tick):
    """Défaut préexistant (avant ce chantier) : la seconde moitié, placée sur un tick non décimal (31/30 s), apparaissait une
    image trop tôt parce que ``setpts`` tronquait son décalage."""
    project, _clip = _project(media)
    before = exported_indices(project)
    cut_clip(project, "c1", cut_tick / FPS)
    assert exported_indices(project) == before


@pytest.mark.parametrize("reverse", [False, True], ids=["avant", "inverse"])
def test_cutting_a_reversed_ramp_leaves_the_exported_sequence_unchanged(media, reverse):
    project, clip = _project(media, animation=_ramp(), remapping=TimeRemapping(reverse=reverse))
    before = exported_indices(project)
    cut_clip(project, "c1", 47 / FPS)
    assert exported_indices(project) == before


def test_a_left_trim_exports_exactly_the_remaining_frames(media):
    """Le rognage gauche laisse le clip en place : le début devient du fond (-1), la suite est inchangée image pour image."""
    project, clip = _project(media, animation=_ramp())
    before = exported_indices(project)
    trim_clip_left(project, "c1", 40 / FPS)
    after = exported_indices(project)
    assert len(after) == len(before)
    assert set(after[:40]) == {-1}
    assert after[40:] == before[40:]


def test_a_right_trim_exports_the_beginning_unchanged(media):
    project, clip = _project(media, animation=_ramp())
    before = exported_indices(project)
    trim_clip_right(project, "c1", 70 / FPS)
    assert exported_indices(project) == before[:70]


# ---------------------------------------------------------------------------
# Opérations de l'interface
# ---------------------------------------------------------------------------


def test_a_speed_ramp_built_with_the_editing_operations_exports_what_the_model_predicts(media):
    project, clip = _project(media)
    for moment, value in ((0.0, 1.0), (0.6, 1.0), (1.0, 0.25), (2.0, 0.25), (2.4, 2.0)):
        add_speed_point(project, "c1", moment, value)
    assert_matches_model(project, clip, ties=True)


def test_setting_a_constant_speed_after_a_ramp_exports_the_constant_speed(media):
    project, clip = _project(media, animation=_ramp())
    set_clip_speed(project, "c1", 2.0)
    assert not clip.has_speed_curve
    assert_matches_model(project, clip)


def test_reversing_a_ramped_clip_exports_the_mirror_picture(media):
    project, clip = _project(media, animation=_ramp())
    set_clip_reverse(project, "c1", True)
    assert_matches_model(project, clip, ties=True)


# ---------------------------------------------------------------------------
# Séquence imbriquée remappée : sa sortie est une source temporelle
# ---------------------------------------------------------------------------


def test_a_nested_sequence_can_be_sped_up_and_its_inner_timeline_is_untouched(media):
    from core.project_model import Sequence

    project, inner_clip = _project(media)
    inner = Sequence(id="inner", name="Inner", width=W, height=H, fps=float(FPS), tracks=[
        Track(id="V1", name="V1", type="video", clips=[Clip(
            id="n1", asset_id="a", track_id="V1", timeline_start=0.0, source_in=0.0, source_out=2.0)]),
    ])
    main = Sequence(id="main", name="Main", width=W, height=H, fps=float(FPS), tracks=[
        Track(id="V1", name="V1", type="video"), Track(id="V2", name="V2", type="video"),
    ])
    project = Project(name="p", width=W, height=H, fps=float(FPS), media_assets=project.media_assets,
                      sequences=[main, inner], active_sequence_id="main")
    nested = insert_sequence_clip(project, "inner", "V2", 0.0)
    set_clip_speed(project, nested.id, 2.0)
    inner_before = [(c.source_in, c.source_out, c.timeline_start) for c in inner.tracks[0].clips]
    got = exported_indices(project)
    assert got[:6] == [0, 2, 4, 6, 8, 10]                                # la sortie de la séquence est lue à 2×
    assert [(c.source_in, c.source_out, c.timeline_start) for c in inner.tracks[0].clips] == inner_before


# ---------------------------------------------------------------------------
# Transitions : aucune image hors des limites du clip remappé
# ---------------------------------------------------------------------------


def test_a_transition_after_a_ramped_clip_uses_only_its_own_frames_and_keeps_the_exact_length(media, tmp_path):
    """Le recouvrement d'une transition vit à l'intérieur des durées des deux clips : ni image avant le début, ni après la fin
    du média, ni image tenue en trop. Avant le recouvrement : les images de la rampe ; après : uniquement celles du clip suivant."""
    from core.transitions import add_transition

    flat = tmp_path / "flat.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=gray:s={W}x{H}:r={FPS}:d=3", "-c:v", "libx264", "-crf", "0",
         "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(flat)],
        check=True, timeout=60,
    )
    project, ramp = _project(media, source=(0.0, 2.0), animation=_ramp())
    project.media_assets.append(MediaAsset(id="b", path=str(flat), name="flat", duration=3.0, width=W, height=H, fps=float(FPS),
                                           media_type="video", has_audio=False))
    follower = Clip(id="c2", asset_id="b", track_id="V1", timeline_start=ramp.duration, source_in=0.0, source_out=1.5)
    project.tracks[0].clips.append(follower)
    add_transition(project, "c1", "c2", duration=0.5)
    plan = build_render_plan(project)
    got = exported_indices(project)
    assert len(got) == round(plan.duration * FPS)                                          # durée exacte, pas une image de trop
    overlap_start = round(follower.timeline_start * FPS)
    before = model_indices(ramp, count=overlap_start)
    time_map = ramp.time_map
    for tick in range(overlap_start - 1):                                                  # avant le recouvrement : la rampe, image pour image
        if got[tick] != before[tick]:                                                      # (à une égalité exacte : l'image voisine, d'un cran)
            position = time_map.source_time(tick / FPS) * FPS
            assert abs((position % 1.0) - 0.5) < 0.04 and abs(got[tick] - before[tick]) == 1, (tick, got[tick], before[tick])
    gray = round((128 - 20) / 3)                                                           # le gris du clip suivant, lu comme un « indice »
    tail = got[round((follower.timeline_start + 0.5) * FPS) + 1:]
    assert tail and all(abs(index - gray) <= 1 for index in tail)                          # après : seulement le clip suivant
