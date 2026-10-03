"""``core.retime_graph`` avec le vrai FFmpeg : l'image montrée à chaque tick est celle que prédit le modèle.

Source de test : chaque image a pour **luminance son propre indice** (``geq lum=mod(N,256)``) ; relire le plan Y d'une image
de sortie donne donc, sans conversion de plage, l'indice de l'image source qui a été montrée. Pour chaque vitesse, sens, rampe,
retournement et arrêt, la suite d'indices obtenue est comparée à celle du modèle (:func:`core.retime_graph.frame_for_tick`,
« l'image source la plus proche de ``M(T)``, à égalité la suivante ») et le nombre d'images doit être exact.

``setpts`` + ``fps`` n'a jamais eu cette propriété (à 2× il montre 1, 3, 5… au lieu de 0, 2, 4…) : ces tests existent
précisément pour qu'on n'y revienne pas.
"""

from __future__ import annotations

import shutil
import subprocess

import numpy as np
import pytest

from core.animation import InterpolationType, Keyframe
from core.retime_graph import MAX_REVERSE_BYTES, RetimeError, frame_for_tick, ticks_in, video_stage
from core.time_map import SPEED_PROPERTY, ConstantTimeMap, PiecewiseTimeMap

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg indisponible")

SIZE = 32
LINEAR, HOLD = InterpolationType.LINEAR, InterpolationType.HOLD


def key(time, value, interpolation=LINEAR) -> Keyframe:
    return Keyframe(SPEED_PROPERTY, time, value, interpolation)


def rendered_indices(time_map, *, fps: float = 30.0, source_fps: float = 30.0, frames: int = 240) -> list[float]:
    """Indice source (luminance moyenne) de chaque image produite par l'étage vidéo pour ``time_map``."""
    stage = video_stage(
        time_map, source_label="0:v", prefix="t", fps=fps, source_fps=source_fps, prepare="",
        frame_bytes=SIZE * SIZE * 3 // 2, last_frame=frames - 1,
    )
    graph = ";".join(stage.chains)
    command = [
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i",
        f"color=c=black:s={SIZE}x{SIZE}:r={source_fps}:d={frames / source_fps},geq=lum='mod(N,256)':cb=128:cr=128,format=yuv420p",
        "-filter_complex", graph, "-map", f"[{stage.label}]", "-f", "rawvideo", "-pix_fmt", "yuv420p", "-",
    ]
    done = subprocess.run(command, capture_output=True, check=False)
    assert done.returncode == 0, done.stderr.decode()[-600:]
    size = SIZE * SIZE * 3 // 2
    raw = np.frombuffer(done.stdout, dtype=np.uint8).reshape(-1, size)[:, : SIZE * SIZE]
    return [float(v) for v in raw.mean(axis=1)]


def expected_indices(time_map, *, fps: float = 30.0, source_fps: float = 30.0, frames: int = 240) -> list[int]:
    return [
        frame_for_tick(time_map, k, fps, source_fps, frames - 1) for k in range(ticks_in(time_map.duration, fps))
    ]


def assert_exact(time_map, **kwargs):
    got = rendered_indices(time_map, **kwargs)
    want = expected_indices(time_map, **{k: v for k, v in kwargs.items() if k in {"fps", "source_fps"}})
    assert len(got) == len(want), (len(got), len(want))
    assert [round(g) for g in got] == want


def assert_within_ties(time_map, *, fps=30.0, source_fps=30.0, tolerance=0.04):
    """Variante pour une rampe approchée par morceaux : un écart d'une image n'est permis qu'à une égalité (± ``tolerance``)."""
    got = rendered_indices(time_map, fps=fps, source_fps=source_fps)
    want = expected_indices(time_map, fps=fps, source_fps=source_fps)
    assert len(got) == len(want)
    for k, (g, w) in enumerate(zip(got, want)):
        if round(g) == w:
            continue
        x = time_map.source_time(k / fps) * source_fps
        assert abs((x % 1.0) - 0.5) < tolerance and abs(round(g) - w) == 1, (k, round(g), w, x)


# ---------------------------------------------------------------------------
# Vitesse constante, dans les deux sens
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("speed", [0.25, 0.4, 0.5, 1.0, 1.5, 2.0, 2.5, 4.0])
def test_constant_speed_shows_the_nearest_source_frame_at_every_tick(speed):
    assert_exact(ConstantTimeMap(0.0, 4.0, speed))


@pytest.mark.parametrize("speed", [0.5, 1.0, 2.0, 3.0])
def test_reverse_shows_the_nearest_source_frame_at_every_tick(speed):
    assert_exact(ConstantTimeMap(0.0, 4.0, speed, reverse=True))


def test_a_window_that_does_not_start_at_zero_selects_the_same_frames():
    assert_exact(ConstantTimeMap(1.013, 3.4, 1.5))
    assert_exact(ConstantTimeMap(1.013, 3.4, 1.5, reverse=True))


def test_two_times_speed_starts_on_frame_zero_not_frame_one():
    """Défaut de l'ancienne chaîne (``setpts`` + ``fps``) : à 2× la première image montrée était la numéro 1."""
    got = [round(v) for v in rendered_indices(ConstantTimeMap(0.0, 4.0, 2.0))]
    assert got[:6] == [0, 2, 4, 6, 8, 10]


@pytest.mark.parametrize(("fps", "source_fps"), [(25.0, 30.0), (30.0, 24.0), (60.0, 30.0), (24.0, 25.0)])
def test_the_output_and_source_frame_rates_may_differ(fps, source_fps):
    assert_exact(ConstantTimeMap(0.0, 3.0, 0.7), fps=fps, source_fps=source_fps)
    assert_exact(ConstantTimeMap(0.0, 3.0, 2.2), fps=fps, source_fps=source_fps)


def test_the_stream_has_exactly_the_clip_duration_in_frames_even_for_extreme_slow_motion():
    """À 25 % le clip dure 4× plus : la dernière image est tenue jusqu'au dernier tick (l'ancienne chaîne en perdait deux)."""
    time_map = ConstantTimeMap(0.0, 2.0, 0.25)
    assert len(rendered_indices(time_map)) == ticks_in(time_map.duration, 30.0) == 240


# ---------------------------------------------------------------------------
# Courbes de vitesse
# ---------------------------------------------------------------------------


def test_the_speed_ramp_of_the_brief_shows_the_nearest_frame_up_to_ties():
    keys = (key(0, 1.0), key(2, 1.0), key(3, 0.25), key(6, 0.25), key(7, 2.0))
    tm = PiecewiseTimeMap(0.0, 8.0, keyframes=keys, anchor=0.0, fixed_duration=8.5)
    assert_within_ties(tm)


def test_bezier_and_ease_ramps_stay_within_ties():
    from core.animation import InterpolationType as I

    keys = (
        Keyframe(SPEED_PROPERTY, 0, 1.0, I.BEZIER, out_slope=1.5),
        Keyframe(SPEED_PROPERTY, 2.0, 0.3, I.EASE_IN_OUT),
        Keyframe(SPEED_PROPERTY, 4.0, 2.0),
    )
    assert_within_ties(PiecewiseTimeMap(0.0, 8.0, keyframes=keys, anchor=0.0, fixed_duration=5.0))


def test_100_then_0_then_minus_100_percent_plays_forward_stops_and_comes_back():
    tm = PiecewiseTimeMap(0.0, 8.0, keyframes=(key(0, 1.0), key(4, -1.0)), anchor=2.0, fixed_duration=4.0)
    got = [round(v) for v in rendered_indices(tm)]
    want = expected_indices(tm)
    assert len(got) == len(want) == 120
    assert got == want
    peak = max(got)
    assert got[0] == want[0] and got.index(peak) > 0 and got[-1] < peak                 # monte, s'arrête, redescend


def test_a_zero_speed_stretch_holds_one_frame_between_two_plays():
    keys = (key(0, 1.0, HOLD), key(1, 0.0, HOLD), key(2, 1.0, HOLD))
    tm = PiecewiseTimeMap(0.0, 8.0, keyframes=keys, anchor=0.0, fixed_duration=3.0)
    got = [round(v) for v in rendered_indices(tm)]
    assert got == expected_indices(tm)
    hold = got[30:60]
    assert len(set(hold)) == 1                                                          # une seule image pendant l'arrêt


def test_a_curve_that_starts_in_reverse_is_rendered_through_the_reverse_filter():
    tm = PiecewiseTimeMap(0.0, 6.0, reverse=True, keyframes=(key(0, 1.0), key(3, 2.0)))
    assert_within_ties(tm)


# ---------------------------------------------------------------------------
# Limites
# ---------------------------------------------------------------------------


def test_a_reverse_run_that_would_not_fit_in_memory_is_refused_with_the_cause():
    huge_frame = MAX_REVERSE_BYTES // 100                                              # ~21 Mo par image
    with pytest.raises(RetimeError, match="Gio"):
        video_stage(
            ConstantTimeMap(0.0, 10.0, 1.0, reverse=True), source_label="0:v", prefix="t", fps=30.0,
            source_fps=30.0, prepare="", frame_bytes=huge_frame,
        )


def test_the_same_run_forward_is_never_refused_for_memory():
    video_stage(ConstantTimeMap(0.0, 10.0, 1.0), source_label="0:v", prefix="t", fps=30.0, source_fps=30.0,
                prepare="", frame_bytes=10**9)
