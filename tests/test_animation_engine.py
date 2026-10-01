"""Moteur d'animation : keyframes, interpolations, courbes, expressions FFmpeg."""

from __future__ import annotations

import math

import pytest
from ffmpeg_expr import evaluate as evaluate_expression

from core.animation import (
    AnimatableProperty,
    AnimationCurve,
    InterpolationType,
    Keyframe,
    TangentMode,
    ValueKind,
    normalize_time,
    snap_local_time,
)
from core.animation_ffmpeg import curve_expression

I = InterpolationType  # noqa: E741 - lisibilité des tableaux


def kf(t, v, interpolation=I.LINEAR, **kwargs):
    return Keyframe("p", t, v, interpolation, **kwargs)


def curve(*frames, kind=ValueKind.FLOAT):
    return AnimationCurve(frames, kind)


# --- Valeur statique, ajout, suppression, ordre -------------------------------------------------


def test_a_property_without_keyframes_uses_its_static_value():
    spec = AnimatableProperty("p", "k", default=1.0, minimum=0.0, maximum=2.0)
    assert spec.evaluate(None, 0.7, 3.0) == 0.7
    assert spec.evaluate(AnimationCurve(), 0.7, 3.0) == 0.7
    assert AnimationCurve().evaluate(1.0, default="x") == "x"


def test_a_single_keyframe_is_a_constant():
    c = curve(kf(2.0, 5.0))
    assert [c.evaluate(t) for t in (0.0, 2.0, 9.0)] == [5.0, 5.0, 5.0]


def test_keyframes_are_sorted_and_a_keyframe_at_the_same_instant_replaces_the_other():
    c = curve(kf(3.0, 3.0), kf(1.0, 1.0), kf(2.0, 2.0), kf(1.0000001, 9.0))
    assert c.times == (1.0, 2.0, 3.0)
    assert c.keyframe_at(1.0).value == 9.0                       # dernier fourni gagne
    added = c.with_keyframe(kf(2.0, 7.0))
    assert len(added) == 3 and added.keyframe_at(2.0).value == 7.0
    assert len(c.with_keyframe(kf(2.5, 0.0))) == 4
    removed = c.without_times([2.0])
    assert removed.times == (1.0, 3.0)
    assert c.without_ids([c.keyframes[0].id]).times == (2.0, 3.0)


def test_before_first_after_last_and_exactly_on_a_keyframe():
    c = curve(kf(1.0, 10.0), kf(2.0, 20.0))
    assert c.evaluate(0.0) == 10.0 and c.evaluate(-5.0) == 10.0
    assert c.evaluate(2.0) == 20.0 and c.evaluate(100.0) == 20.0
    assert c.evaluate(1.0) == 10.0 and c.evaluate(1.5) == pytest.approx(15.0)


# --- Interpolations --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("interpolation", "expected"),
    [
        (I.HOLD, [0.0, 0.0, 0.0, 0.0, 10.0]),
        (I.LINEAR, [0.0, 2.5, 5.0, 7.5, 10.0]),
        (I.EASE_IN, [0.0, 0.625, 2.5, 5.625, 10.0]),
        (I.EASE_OUT, [0.0, 4.375, 7.5, 9.375, 10.0]),
        (I.EASE_IN_OUT, [0.0, 1.5625, 5.0, 8.4375, 10.0]),
    ],
)
def test_each_interpolation_curve(interpolation, expected):
    c = curve(kf(0.0, 0.0, interpolation), kf(1.0, 10.0))
    assert [c.evaluate(t / 4) for t in range(5)] == pytest.approx(expected)


def test_hold_jumps_exactly_at_the_next_keyframe():
    c = curve(kf(0.0, 1.0, I.HOLD), kf(1.0, 2.0, I.HOLD), kf(2.0, 3.0))
    assert c.evaluate(0.999999) == 1.0 and c.evaluate(1.0) == 2.0 and c.evaluate(1.5) == 2.0


def test_ease_in_starts_slowly_and_ease_out_arrives_slowly():
    ease_in = curve(kf(0.0, 0.0, I.EASE_IN), kf(1.0, 1.0))
    ease_out = curve(kf(0.0, 0.0, I.EASE_OUT), kf(1.0, 1.0))
    assert ease_in.derivative_components(0.001)[0] == pytest.approx(0.0, abs=0.01)
    assert ease_out.derivative_components(0.999)[0] == pytest.approx(0.0, abs=0.01)
    assert ease_in.evaluate(0.5) < 0.5 < ease_out.evaluate(0.5)


def test_bezier_follows_its_tangents():
    flat = curve(kf(0.0, 0.0, I.BEZIER, out_slope=0.0), kf(2.0, 10.0, in_slope=0.0))
    assert flat.evaluate(1.0) == pytest.approx(5.0)
    assert flat.derivative_components(0.0001)[0] == pytest.approx(0.0, abs=0.01)
    steep = curve(kf(0.0, 0.0, I.BEZIER, out_slope=20.0), kf(2.0, 10.0, in_slope=0.0))
    assert steep.derivative_components(0.0)[0] == pytest.approx(20.0)
    assert steep.evaluate(0.5) > flat.evaluate(0.5)
    # Pentes automatiques : Catmull-Rom (pente entre voisins), nulles aux extrémités.
    auto = curve(kf(0.0, 0.0, I.BEZIER), kf(1.0, 5.0, I.BEZIER), kf(2.0, 10.0))
    assert auto.resolved_slopes(1) == ((5.0,), (5.0,))
    assert auto.resolved_slopes(0)[1] == (0.0,)


def test_linked_tangents_share_one_slope_and_broken_tangents_are_independent():
    linked = curve(kf(0.0, 0.0, I.BEZIER), kf(1.0, 5.0, I.BEZIER, in_slope=1.0, out_slope=9.0), kf(2.0, 0.0))
    assert linked.resolved_slopes(1) == ((9.0,), (9.0,))
    broken = curve(
        kf(0.0, 0.0, I.BEZIER),
        kf(1.0, 5.0, I.BEZIER, in_slope=1.0, out_slope=9.0, tangent_mode=TangentMode.BROKEN),
        kf(2.0, 0.0),
    )
    assert broken.resolved_slopes(1) == ((1.0,), (9.0,))
    assert broken.derivative_components(0.9999)[0] == pytest.approx(1.0, abs=0.01)
    assert broken.derivative_components(1.0001)[0] == pytest.approx(9.0, abs=0.01)


def test_vec2_int_and_bool_values():
    vec = curve(kf(0.0, (0.0, 10.0)), kf(1.0, (1.0, 20.0)))
    assert vec.kind is ValueKind.VEC2 and vec.evaluate(0.5) == pytest.approx((0.5, 15.0))
    ints = curve(kf(0.0, 0), kf(1.0, 10), kind=ValueKind.INT)
    assert ints.evaluate(0.26) == 3 and isinstance(ints.evaluate(0.26), int)
    flags = curve(kf(0.0, False, I.LINEAR), kf(1.0, True), kind=ValueKind.BOOL)
    assert flags.evaluate(0.9) is False and flags.evaluate(1.0) is True   # toujours « hold »


def test_property_bounds_are_applied_after_evaluation():
    spec = AnimatableProperty("o", "k", minimum=0.0, maximum=1.0)
    overshoot = curve(kf(0.0, 0.0, I.BEZIER, out_slope=10.0), kf(1.0, 1.0, in_slope=10.0))
    assert max(overshoot.evaluate(t / 20) for t in range(21)) > 1.0
    assert all(0.0 <= spec.evaluate(overshoot, 0.0, t / 20) <= 1.0 for t in range(21))


# --- Temps et FPS -----------------------------------------------------------------------------------


def test_times_are_normalised_to_the_microsecond():
    assert normalize_time(0.1 + 0.2) == 0.3
    assert kf(1 / 3, 0.0).time_seconds == 0.333333
    assert kf(-1e-9, 0.0).time_seconds == 0.0
    with pytest.raises(ValueError):
        kf(-1.0, 0.0)
    with pytest.raises(ValueError):
        kf(math.nan, 0.0)


@pytest.mark.parametrize("fps", [24, 25, 30, 60, 30000 / 1001])
def test_snapping_lands_on_timeline_frames_for_any_fps(fps):
    clip_start = 1.23456
    for local in (0.0, 0.4, 1.0, 2.71828):
        snapped = snap_local_time(local, clip_start, fps)
        frames = (clip_start + snapped) * fps
        assert abs(frames - round(frames)) < 1e-3
        assert abs(snapped - local) <= 0.5 / fps + 1e-6


@pytest.mark.parametrize("fps", [24, 25, 30, 60, 30000 / 1001])
def test_evaluation_does_not_depend_on_the_fps(fps):
    c = curve(kf(0.0, 0.0, I.EASE_IN_OUT), kf(2.0, 100.0))
    for frame in range(int(2 * fps)):
        t = frame / fps
        expected = 100.0 * (3 * (t / 2) ** 2 - 2 * (t / 2) ** 3)
        assert c.evaluate(t) == pytest.approx(expected)


# --- Insertion et découpe sans changer l'animation -------------------------------------------------


@pytest.mark.parametrize("interpolation", list(I))
def test_inserting_a_keyframe_keeps_the_animation(interpolation):
    c = curve(kf(0.0, 0.0, interpolation), kf(2.0, 10.0, I.BEZIER, out_slope=3.0), kf(4.0, -2.0))
    inserted = c.inserted_preserving_shape(1.3)
    assert len(inserted) == 4
    for step in range(81):
        t = step / 20
        assert inserted.evaluate(t) == pytest.approx(c.evaluate(t), abs=1e-9)


@pytest.mark.parametrize("interpolation", list(I))
def test_splitting_keeps_both_halves(interpolation):
    c = curve(kf(0.0, 0.0, interpolation), kf(2.0, 10.0, interpolation), kf(4.0, 3.0))
    left, right = c.split(2.7)
    for step in range(81):
        t = step / 20
        part = left.evaluate(t) if t <= 2.7 else right.evaluate(t - 2.7)
        assert part == pytest.approx(c.evaluate(t), abs=1e-9)


# --- Expressions FFmpeg : mêmes valeurs que Python ---------------------------------------------------


@pytest.mark.parametrize("interpolation", list(I))
def test_ffmpeg_expression_matches_python_at_every_frame(interpolation):
    c = curve(
        kf(0.5, 0.2, interpolation, out_slope=1.5),
        kf(1.7, 0.9, interpolation, in_slope=-0.5, out_slope=2.0, tangent_mode=TangentMode.BROKEN),
        kf(3.0, 0.1),
    )
    expression = curve_expression(c, time_var="T", minimum=0.0, maximum=1.0)
    spec = AnimatableProperty("o", "k", minimum=0.0, maximum=1.0)
    for fps in (24, 30000 / 1001, 60):
        for frame in range(int(3.5 * fps)):
            t = frame / fps
            assert evaluate_expression(expression, T=t) == pytest.approx(spec.evaluate(c, 0.0, t), abs=1e-6)


def test_ffmpeg_expression_is_flat_and_has_no_scientific_notation():
    c = curve(*(kf(i * 0.001, (i % 7) * 1e-7, I.EASE_IN_OUT) for i in range(200)))
    expression = curve_expression(c)
    assert "e-" not in expression and "e+" not in expression
    assert "if(" not in expression                                  # pas d'imbrication profonde
    assert expression.count("gte(T,") == 200


def test_ffmpeg_expression_of_a_vec2_component():
    c = curve(kf(0.0, (0.0, 10.0)), kf(1.0, (1.0, 20.0)))
    assert evaluate_expression(curve_expression(c, component=1), T=0.5) == pytest.approx(15.0)


def test_hold_switches_on_the_same_frame_in_python_and_ffmpeg_despite_float_noise():
    c = curve(kf(0.0, 10.0, I.HOLD), kf(1.3, 3.0))
    noisy = 4.0 - 2.7                                               # 1.2999999999999998
    assert c.evaluate(noisy) == 3.0
    assert evaluate_expression(curve_expression(c), T=noisy) == 3.0


def test_evaluation_cost_does_not_grow_with_the_number_of_keyframes():
    """Recherche dichotomique : 1 000 keyframes ne coûtent pas 100× plus que 10."""
    import time

    def cost(count):
        c = curve(*(kf(i * 0.01, float(i % 9), list(I)[i % 6]) for i in range(count)))
        end = c.times[-1]
        instants = [end * k / 997 for k in range(997)]
        best = float("inf")
        for _ in range(5):
            start = time.perf_counter()
            for t in instants:
                c.evaluate(t)
            best = min(best, time.perf_counter() - start)
        return best

    assert cost(1000) < cost(10) * 10


# --- Retours de revue : bornes et insertion hors de la courbe ------------------------------------


def test_inserting_where_a_bounded_curve_overshoots_uses_the_displayed_value():
    from core.visual_effects import TRANSFORM_PROPERTIES, TransformKeyframe

    spec = TRANSFORM_PROPERTIES["opacity"]
    c = AnimationCurve([
        TransformKeyframe("opacity", 0.0, 0.0, I.BEZIER, out_slope=10.0),
        TransformKeyframe("opacity", 1.0, 1.0, in_slope=10.0),
    ])
    peak = max(range(101), key=lambda i: c.evaluate(i / 100)) / 100
    assert c.evaluate(peak) > 1.0                                      # la courbe brute déborde
    inserted = c.inserted_preserving_shape(peak, clamp=spec.clamp)     # pas de ValueError
    assert inserted.keyframe_at(peak).value == 1.0
    left, right = c.split(peak, clamp=spec.clamp)
    assert left.keyframes[-1].value == 1.0 and right.keyframes[0].value == 1.0


@pytest.mark.parametrize("interpolation", list(I))
def test_inserting_before_the_first_or_after_the_last_keyframe_stays_flat(interpolation):
    c = curve(
        kf(1.0, 5.0, interpolation, out_slope=10.0),
        kf(2.0, 8.0, interpolation, in_slope=-4.0, out_slope=10.0),
        kf(3.0, 2.0, interpolation),
    )
    expected = [c.evaluate(t / 20) for t in range(81)]
    for t in (0.4, 3.6):
        inserted = c.inserted_preserving_shape(t)
        assert len(inserted) == 4
        assert [inserted.evaluate(x / 20) for x in range(81)] == pytest.approx(expected, abs=1e-9)
    # Les deux à la fois (découpe hors de l'intervalle animé).
    both = c.inserted_preserving_shape(0.4).inserted_preserving_shape(3.6)
    assert [both.evaluate(x / 20) for x in range(81)] == pytest.approx(expected, abs=1e-9)


def test_inserting_next_to_linked_and_automatic_tangents_keeps_neighbouring_segments():
    c = curve(kf(0.0, 0.0, I.BEZIER), kf(1.0, 4.0, I.BEZIER, out_slope=9.0), kf(2.0, 1.0, I.BEZIER), kf(3.0, 3.0))
    expected = [c.evaluate(t / 20) for t in range(61)]
    for t in (0.3, 1.5, 2.6):
        inserted = c.inserted_preserving_shape(t)
        assert [inserted.evaluate(x / 20) for x in range(61)] == pytest.approx(expected, abs=1e-9)
