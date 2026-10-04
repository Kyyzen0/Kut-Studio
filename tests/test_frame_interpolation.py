"""Le plan d'interpolation : pour chaque image de sortie, l'image source ou la paire et son poids — exact, sans pixel."""

from __future__ import annotations

import pytest

from core.animation import InterpolationType, Keyframe
from core.frame_interpolation import (
    MAX_INTERPOLATED_STEP,
    FrameSample,
    plan_interpolation,
    sample_at,
)
from core.retime_graph import frame_for_tick, ticks_in
from core.time_map import SPEED_PROPERTY, ConstantTimeMap, PiecewiseTimeMap
from core.time_remapping import TimeInterpolation

BLENDING, FLOW, SAMPLING = TimeInterpolation.BLENDING, TimeInterpolation.OPTICAL_FLOW, TimeInterpolation.SAMPLING
LAST = 299


def plan(time_map, mode=BLENDING, *, fps=30.0, source_fps=30.0, last=LAST):
    return plan_interpolation(time_map, fps=fps, source_fps=source_fps, last_frame=last, interpolation=mode)


def flat(result):
    return [sample for run in result.runs for sample in run.samples]


def weights(result):
    return [(sample.a, round(sample.t, 9)) for sample in flat(result)]


# ---------------------------------------------------------------------------
# Position -> image ou paire
# ---------------------------------------------------------------------------


def test_a_position_on_a_source_frame_is_that_frame():
    assert sample_at(7.0, LAST) == FrameSample(7)
    assert sample_at(7.0004, LAST) == FrameSample(7)                         # sous le seuil : image source
    assert sample_at(6.9996, LAST) == FrameSample(7)
    assert FrameSample(7).exact and FrameSample(7).b == 8


def test_a_position_between_two_frames_is_a_weighted_pair():
    sample = sample_at(7.25, LAST)
    assert (sample.a, sample.b, round(sample.t, 9)) == (7, 8, 0.25) and not sample.exact


def test_the_last_frame_of_the_media_has_no_successor_and_is_shown_as_it_is():
    assert sample_at(299.5, LAST) == FrameSample(299)
    assert sample_at(300.0, LAST) == FrameSample(299)
    assert sample_at(-3.0, LAST) == FrameSample(0)
    assert sample_at(298.5, LAST) == FrameSample(298, 0.5)


# ---------------------------------------------------------------------------
# Vitesses constantes : les poids sont exacts
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", [BLENDING, FLOW])
def test_half_speed_alternates_a_frame_and_the_midpoint(mode):
    result = plan(ConstantTimeMap(0.0, 3.0, 0.5), mode)
    assert weights(result)[:6] == [(0, 0.0), (0, 0.5), (1, 0.0), (1, 0.5), (2, 0.0), (2, 0.5)]


def test_quarter_speed_places_three_images_between_every_pair():
    result = plan(ConstantTimeMap(0.0, 3.0, 0.25))
    assert weights(result)[:8] == [(0, 0.0), (0, 0.25), (0, 0.5), (0, 0.75), (1, 0.0), (1, 0.25), (1, 0.5), (1, 0.75)]
    assert result.ticks == ticks_in(12.0, 30.0) == 360


def test_a_speed_that_does_not_divide_the_frame_gives_exact_fractional_weights():
    result = plan(ConstantTimeMap(0.0, 3.0, 0.4))
    assert weights(result)[:6] == [(0, 0.0), (0, 0.4), (0, 0.8), (1, 0.2), (1, 0.6), (2, 0.0)]


def test_normal_speed_and_whole_multiples_need_no_synthesis_in_any_mode():
    for speed in (1.0, 2.0, 4.0, 10.0):
        for mode in (BLENDING, FLOW):
            result = plan(ConstantTimeMap(0.0, 8.0, speed), mode)
            assert not result.needs_synthesis and result.synthetic == 0, (speed, mode)


def test_beyond_two_frames_per_image_neighbours_are_skipped_not_interpolated():
    """9,7× : les images voisines ne sont jamais montrées, inutile d'intercaler entre elles."""
    assert 9.7 >= MAX_INTERPOLATED_STEP
    result = plan(ConstantTimeMap(0.0, 8.0, 9.7), FLOW, last=10_000)
    assert not result.needs_synthesis
    time_map = ConstantTimeMap(0.0, 8.0, 9.7)
    assert [s.a for s in flat(result)] == [frame_for_tick(time_map, k, 30.0, 30.0, 10_000) for k in range(result.ticks)]


def test_between_one_and_two_frames_per_image_the_pairs_are_interpolated():
    result = plan(ConstantTimeMap(0.0, 8.0, 1.5))
    assert weights(result)[:4] == [(0, 0.0), (1, 0.5), (3, 0.0), (4, 0.5)]
    assert result.synthetic == result.ticks // 2


def test_sampling_never_synthesizes_and_matches_the_nearest_frame_rule():
    for speed in (0.25, 0.4, 0.5, 1.5, 3.0):
        time_map = ConstantTimeMap(0.0, 6.0, speed)
        result = plan(time_map, SAMPLING)
        assert not result.needs_synthesis
        assert [s.a for s in flat(result)] == [frame_for_tick(time_map, k, 30.0, 30.0, LAST) for k in range(result.ticks)]


def test_a_different_output_rate_is_a_frame_rate_conversion():
    """24 i/s sur un média de 30 : la position avance de 1,25 image par image de sortie ; les poids viennent du modèle."""
    result = plan(ConstantTimeMap(0.0, 6.0, 1.0), BLENDING, fps=24.0, source_fps=30.0)
    assert weights(result)[:5] == [(0, 0.0), (1, 0.25), (2, 0.5), (3, 0.75), (5, 0.0)]


# ---------------------------------------------------------------------------
# Sens, arrêt, courbes
# ---------------------------------------------------------------------------


def test_reverse_mirrors_the_weights_and_starts_on_the_last_existing_frame():
    """Au-delà de la dernière image (position 60, puis 59,5) il n'y a rien à mélanger : on montre l'image 59 ; ensuite les
    poids reprennent à l'envers."""
    time_map = ConstantTimeMap(0.0, 2.0, 0.5, reverse=True)
    result = plan(time_map, BLENDING, last=59)
    assert weights(result)[:7] == [(59, 0.0), (59, 0.0), (59, 0.0), (58, 0.5), (58, 0.0), (57, 0.5), (57, 0.0)]


def test_a_hold_shows_one_image_not_a_blend():
    keys = (
        Keyframe(SPEED_PROPERTY, 0.0, 0.4, InterpolationType.HOLD),
        Keyframe(SPEED_PROPERTY, 1.0, 0.0, InterpolationType.HOLD),
        Keyframe(SPEED_PROPERTY, 2.0, 0.4, InterpolationType.HOLD),
    )
    time_map = PiecewiseTimeMap(0.0, 8.0, keyframes=keys, anchor=0.0, fixed_duration=3.0)
    result = plan(time_map, FLOW)
    held = flat(result)[30:60]
    assert len({sample for sample in held}) == 1 and all(sample.exact for sample in held)
    assert any(not sample.exact for sample in flat(result)[:30]) and any(not sample.exact for sample in flat(result)[60:])


def test_a_ramp_interpolates_only_where_it_is_slow():
    keys = (
        Keyframe(SPEED_PROPERTY, 0.0, 0.25, InterpolationType.LINEAR),
        Keyframe(SPEED_PROPERTY, 4.0, 4.0, InterpolationType.LINEAR),
    )
    time_map = PiecewiseTimeMap(0.0, 12.0, keyframes=keys, anchor=0.0, fixed_duration=5.0)
    result = plan(time_map, FLOW, last=10_000)
    samples = flat(result)
    assert result.ticks == ticks_in(time_map.duration, 30.0)
    slow = [k for k in range(len(samples)) if abs(time_map.speed_at(k / 30.0)) < MAX_INTERPOLATED_STEP]
    assert slow and all(k in slow for k in range(len(samples)) if not samples[k].exact)
    assert all(samples[k].exact for k in range(len(samples)) if k not in slow)
    assert result.synthetic > 0


def test_every_run_covers_whole_images_and_the_total_is_the_clip_duration():
    keys = (Keyframe(SPEED_PROPERTY, 0.0, 1.0), Keyframe(SPEED_PROPERTY, 4.0, -1.0))
    time_map = PiecewiseTimeMap(0.0, 8.0, keyframes=keys, anchor=2.0, fixed_duration=4.0)
    result = plan(time_map, BLENDING, last=10_000)
    assert result.ticks == ticks_in(4.0, 30.0)
    assert [run.first for run in result.runs] == sorted(run.first for run in result.runs)
    assert sum(run.ticks for run in result.runs) == result.ticks


# ---------------------------------------------------------------------------
# Ce que le moteur de pixels doit lire
# ---------------------------------------------------------------------------


def test_pairs_and_frames_list_exactly_what_has_to_be_decoded():
    result = plan(ConstantTimeMap(0.0, 1.0, 0.5))
    assert result.pairs() == tuple(range(0, 30))                              # 1 s de source = 30 paires
    assert result.frames() == tuple(range(0, 31))                             # la dernière paire lit l'image 30
    assert result.describe() == {"requested": "blending", "images": 60, "synthesized": 30, "exact": 30, "pairs": 30}


def test_a_clip_with_nothing_to_synthesize_reports_it():
    result = plan(ConstantTimeMap(0.0, 8.0, 2.0), FLOW)
    assert result.describe()["synthesized"] == 0 and result.pairs() == ()
