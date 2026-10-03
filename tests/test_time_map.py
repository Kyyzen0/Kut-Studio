"""``core.time_map`` : l'unique modèle « temps de la timeline → temps source ».

Le contrôle d'ensemble compare le mapping à un **oracle indépendant** : l'intégrale de la vitesse recalculée par quadrature
de Gauss-Legendre sur ``AnimationCurve.evaluate`` (qui ne partage rien avec l'intégration exacte du module), segment de
keyframe par segment. La quadrature est exacte pour un polynôme de degré ≤ 9 : l'oracle est donc lui-même rigoureux,
hors des morceaux bornés (où le balayage est fin).
"""

from __future__ import annotations

import math
import random

import pytest

from core.animation import AnimationCurve, InterpolationType, Keyframe
from core.project_model import Clip
from core.time_map import (
    SPEED_LIMIT,
    SPEED_PROPERTY,
    ConstantTimeMap,
    PiecewiseTimeMap,
    RunKind,
    has_speed_curve,
    real_roots,
    time_map_for_clip,
)
from core.time_remapping import FreezeFrameMode, TimeRemapping

LINEAR, HOLD = InterpolationType.LINEAR, InterpolationType.HOLD
EASE_IN, EASE_OUT = InterpolationType.EASE_IN, InterpolationType.EASE_OUT
EASE_IN_OUT, BEZIER = InterpolationType.EASE_IN_OUT, InterpolationType.BEZIER


def key(time, value, interpolation=LINEAR, **extra) -> Keyframe:
    return Keyframe(SPEED_PROPERTY, time, value, interpolation, **extra)


_GAUSS_LEGENDRE_5 = (
    (-0.9061798459386640, 0.2369268850561891), (-0.5384693101056831, 0.4786286704993665),
    (0.0, 0.5688888888888889),
    (0.5384693101056831, 0.4786286704993665), (0.9061798459386640, 0.2369268850561891),
)


def oracle_source_time(keys, t, *, anchor, direction=1, panels=40):
    """``anchor + ∫₀ᵗ direction·clamp(f)`` par Gauss-Legendre à 5 points, coupé aux keyframes.

    Les nœuds sont strictement intérieurs : jamais d'évaluation sur un saut de vitesse (``AnimationCurve.evaluate`` rend,
    à un instant de keyframe, la valeur du keyframe suivant). Exact jusqu'au degré 9 : l'oracle est rigoureux sur les
    polynômes de segment ; les panneaux fins ne servent qu'aux morceaux bornés (coudes).
    """
    curve = AnimationCurve(keys)

    def v(x: float) -> float:
        return direction * max(-SPEED_LIMIT, min(SPEED_LIMIT, curve.evaluate(x)))

    cuts = sorted({0.0, t, *[k for k in curve.times if 0.0 < k < t]})
    total = anchor
    for a, b in zip(cuts, cuts[1:]):
        width = (b - a) / panels
        for index in range(panels):
            p = a + index * width
            half = width / 2.0
            mid = p + half
            total += half * sum(weight * v(mid + half * node) for node, weight in _GAUSS_LEGENDRE_5)
    return total


# ---------------------------------------------------------------------------
# Racines
# ---------------------------------------------------------------------------


def test_real_roots_of_a_cubic_are_found_in_order_and_only_inside_the_range():
    coeffs = (-6.0, 11.0, -6.0, 1.0)                      # (x−1)(x−2)(x−3)
    assert real_roots(coeffs, 0.0, 10.0) == pytest.approx([1.0, 2.0, 3.0], abs=1e-12)
    assert real_roots(coeffs, 1.5, 2.5) == pytest.approx([2.0], abs=1e-12)
    assert real_roots((1.0, 0.0, 1.0), -5.0, 5.0) == []   # x² + 1 : aucune racine réelle


def test_a_tangential_root_counts_as_a_turning_point():
    assert real_roots((0.25, -1.0, 1.0), 0.0, 2.0) == pytest.approx([0.5], abs=1e-7)   # (x − ½)²


# ---------------------------------------------------------------------------
# Mapping constant : les formules historiques, à l'identique
# ---------------------------------------------------------------------------


def test_the_constant_map_reproduces_the_historical_formulas_bit_for_bit():
    forward = ConstantTimeMap(2.0, 12.0, 2.5)
    assert forward.duration == (12.0 - 2.0) / 2.5
    assert forward.source_time(1.7) == 2.0 + 1.7 * 2.5
    backward = ConstantTimeMap(2.0, 12.0, 2.5, reverse=True)
    assert backward.source_time(1.7) == 2.0 + ((12.0 - 2.0) - 1.7 * 2.5)
    assert backward.source_time(0.0) == 12.0 and backward.source_time(backward.duration) == pytest.approx(2.0, abs=1e-12)


def test_the_constant_map_never_leaves_its_window_and_a_freeze_holds_one_instant():
    forward = ConstantTimeMap(0.0, 4.0, 1.0)
    assert forward.source_time(-3.0) == 0.0 and forward.source_time(99.0) == 4.0
    frozen = ConstantTimeMap(0.0, 4.0, hold=True, hold_source=1.25, hold_duration=3.0)
    assert frozen.duration == 3.0 and {frozen.source_time(t) for t in (0.0, 1.0, 3.0)} == {1.25}
    assert frozen.speed_at(1.0) == 0.0 and frozen.runs()[0].kind is RunKind.HOLD


def test_the_strict_variant_raises_outside_the_window_like_the_old_api():
    with pytest.raises(ValueError):
        ConstantTimeMap(0.0, 4.0, 1.0).strict_source_time(5.0)


def test_a_constant_map_has_one_run_and_the_inverse_finds_one_instant():
    tm = ConstantTimeMap(10.0, 20.0, 2.0)
    assert tm.times_at_source(14.0) == [2.0] and tm.times_at_source(25.0) == []
    assert tm.extent() == (10.0, 20.0)
    (run,) = tm.runs()
    assert (run.kind, run.t0, run.t1, run.s0, run.s1) == (RunKind.FORWARD, 0.0, 5.0, 10.0, 20.0)


# ---------------------------------------------------------------------------
# Courbe de vitesse : intégrale exacte
# ---------------------------------------------------------------------------


def test_the_speed_ramp_of_the_brief_integrates_exactly():
    """0 s 100 % · 2 s 100 % · 3 s 25 % · 6 s 25 % · 7 s 200 % (linéaire) : le temps source se calcule à la main."""
    keys = (key(0, 1.0), key(2, 1.0), key(3, 0.25), key(6, 0.25), key(7, 2.0))
    tm = PiecewiseTimeMap(0.0, 1000.0, keyframes=keys, fixed_duration=7.0)
    assert tm.source_time(2.0) == pytest.approx(2.0, abs=1e-12)                     # 100 % pendant 2 s
    assert tm.source_time(3.0) == pytest.approx(2.0 + (1.0 + 0.25) / 2.0, abs=1e-12)  # rampe linéaire : moyenne
    assert tm.source_time(6.0) == pytest.approx(2.625 + 3 * 0.25, abs=1e-12)         # 25 % pendant 3 s
    assert tm.source_time(7.0) == pytest.approx(3.375 + (0.25 + 2.0) / 2.0, abs=1e-12)
    assert tm.duration == 7.0


def test_the_duration_is_derived_from_the_source_that_runs_out():
    """Sans durée imposée, le clip s'arrête quand la fenêtre est épuisée : pas de durée stockée à part."""
    keys = (key(0, 1.0), key(2, 1.0), key(3, 0.25))            # 100 % puis 25 % (tenu après le dernier point)
    tm = PiecewiseTimeMap(0.0, 6.0, keyframes=keys)
    # 2 s à 100 % (2.0) + rampe 1 s (0.625) = 2.625 à t = 3 ; il reste 3.375 à 25 % : 13.5 s
    assert tm.duration == pytest.approx(3.0 + 3.375 / 0.25, abs=1e-9)
    assert tm.source_time(tm.duration) == pytest.approx(6.0, abs=1e-9)
    assert not tm.underdetermined


def test_a_curve_that_never_exhausts_the_source_is_flagged_and_needs_a_fixed_duration():
    keys = (key(0, 1.0), key(2, 0.0))                          # finit à l'arrêt : la fenêtre n'est jamais épuisée
    open_ended = PiecewiseTimeMap(0.0, 100.0, keyframes=keys)
    assert open_ended.underdetermined
    fixed = PiecewiseTimeMap(0.0, 100.0, keyframes=keys, fixed_duration=5.0)
    assert fixed.duration == 5.0 and not fixed.underdetermined
    assert fixed.source_time(5.0) == pytest.approx(1.0, abs=1e-12)      # 1·2 − ½·2 = 1 puis arrêt


def test_a_fixed_duration_can_only_shorten_the_clip():
    keys = (key(0, 2.0),)
    assert PiecewiseTimeMap(0.0, 10.0, keyframes=keys, fixed_duration=99.0).duration == pytest.approx(5.0)
    assert PiecewiseTimeMap(0.0, 10.0, keyframes=keys, fixed_duration=2.0).duration == 2.0


# ---------------------------------------------------------------------------
# Sens, retournements, arrêt
# ---------------------------------------------------------------------------


def test_100_then_0_then_minus_100_percent_stops_and_comes_back():
    """v(t) = 1 − t sur [0, 2] : M = t − t²/2, maximum ½ à t = 1, retour au point de départ à t = 2."""
    keys = (key(0, 1.0), key(2, -1.0))
    tm = PiecewiseTimeMap(0.0, 10.0, keyframes=keys)
    assert tm.duration == pytest.approx(2.0, abs=1e-9)          # épuisée en revenant à la borne basse
    assert tm.source_time(1.0) == pytest.approx(0.5, abs=1e-12)
    assert tm.extent() == pytest.approx((0.0, 0.5), abs=1e-9)
    forward, backward = tm.runs()
    assert (forward.kind, backward.kind) == (RunKind.FORWARD, RunKind.BACKWARD)
    assert forward.t1 == pytest.approx(1.0, abs=1e-9) and backward.t0 == pytest.approx(1.0, abs=1e-9)
    # deux instants montrent le même temps source : à l'aller et au retour
    first, second = tm.times_at_source(0.25)
    assert first == pytest.approx(1.0 - math.sqrt(0.5), abs=1e-9) and second == pytest.approx(1.0 + math.sqrt(0.5), abs=1e-9)


def test_the_mapping_stays_continuous_through_the_turn_and_never_jumps():
    keys = (key(0, 1.0), key(2, -1.0))
    tm = PiecewiseTimeMap(0.0, 10.0, keyframes=keys)
    step = 1e-4
    ts = [i * step for i in range(int(tm.duration / step))]
    jumps = [abs(tm.source_time(b) - tm.source_time(a)) for a, b in zip(ts, ts[1:])]
    assert max(jumps) <= SPEED_LIMIT * step * 1.0001


def test_a_reversed_clip_with_a_curve_plays_the_mirror_image():
    keys = (key(0, 1.0), key(2, 0.5))
    forward = PiecewiseTimeMap(0.0, 6.0, keyframes=keys)
    backward = PiecewiseTimeMap(0.0, 6.0, reverse=True, keyframes=keys)
    assert backward.duration == pytest.approx(forward.duration, abs=1e-9)
    for t in (0.0, 0.7, 1.9, 3.3, forward.duration):
        assert backward.source_time(t) == pytest.approx(6.0 - forward.source_time(t), abs=1e-9)


def test_a_zero_speed_stretch_is_a_hold_run_and_the_picture_does_not_move():
    keys = (key(0, 1.0, HOLD), key(1, 0.0, HOLD), key(3, 1.0, HOLD))
    tm = PiecewiseTimeMap(0.0, 50.0, keyframes=keys, fixed_duration=5.0)
    assert [run.kind for run in tm.runs()] == [RunKind.FORWARD, RunKind.HOLD, RunKind.FORWARD]
    assert tm.source_time(1.0) == tm.source_time(2.5) == tm.source_time(3.0) == pytest.approx(1.0, abs=1e-12)
    assert tm.speed_at(2.0) == 0.0


def test_a_step_in_speed_changes_the_slope_but_never_the_position():
    keys = (key(0, 1.0, HOLD), key(2, 4.0, HOLD))
    tm = PiecewiseTimeMap(0.0, 100.0, keyframes=keys, fixed_duration=4.0)
    assert tm.source_time(2.0) == pytest.approx(2.0, abs=1e-12)
    assert tm.source_time(2.0 + 1e-9) == pytest.approx(2.0, abs=1e-6)       # continu au saut de vitesse
    assert tm.speed_at(1.999) == 1.0 and tm.speed_at(2.001) == 4.0


def test_a_window_below_the_curve_start_does_not_produce_an_empty_clip():
    keys = (key(0, -1.0), key(1, -1.0))                        # recule dès l'instant 0, depuis la borne basse
    tm = PiecewiseTimeMap(0.0, 10.0, keyframes=keys)
    assert tm.duration > 0.0 and tm.source_time(0.0) == 0.0


# ---------------------------------------------------------------------------
# Précision : oracle indépendant
# ---------------------------------------------------------------------------


SHAPES = {
    "linéaire": (key(0, 1.0), key(2, 3.0)),
    "palier": (key(0, 1.0, HOLD), key(1, 4.0, HOLD), key(2, 0.5, HOLD)),
    "ease_in": (key(0, 0.25, EASE_IN), key(3, 2.0)),
    "ease_out": (key(0, 0.25, EASE_OUT), key(3, 2.0)),
    "ease_in_out": (key(0, 1.0, EASE_IN_OUT), key(2, 0.25, EASE_IN_OUT), key(5, 2.5)),
    "bezier": (key(0, 1.0, BEZIER, out_slope=2.0), key(2, 0.25, BEZIER, in_slope=-1.0, out_slope=3.0), key(4, 2.0)),
}


@pytest.mark.parametrize("name", list(SHAPES))
@pytest.mark.parametrize("reverse", [False, True])
def test_the_mapping_matches_an_independent_integration(name, reverse):
    keys = SHAPES[name]
    tm = PiecewiseTimeMap(0.0, 1000.0, reverse=reverse, keyframes=keys, fixed_duration=6.0)
    direction = -1 if reverse else 1
    anchor = 1000.0 if reverse else 0.0
    for t in [0.0, 0.37, 1.0, 1.9999, 2.0, 2.5, 3.14, 4.0, 5.2, 6.0]:
        assert tm.source_time(t) == pytest.approx(
            oracle_source_time(keys, t, anchor=anchor, direction=direction), abs=1e-7
        ), (name, t)


def test_a_bezier_overshoot_is_clamped_to_the_speed_limit_like_the_property():
    """Une tangente qui sort de ±10 est bornée comme la propriété l'est à l'évaluation : le mapping suit ce qui est affiché."""
    keys = (key(0, 8.0, BEZIER, out_slope=40.0), key(1, 9.0, BEZIER, in_slope=40.0), key(2, 1.0))
    tm = PiecewiseTimeMap(0.0, 10000.0, keyframes=keys, fixed_duration=2.0)
    assert max(tm.speed_at(i / 200.0) for i in range(401)) <= SPEED_LIMIT + 1e-9
    for t in (0.2, 0.5, 0.8, 1.0, 1.5, 2.0):
        assert tm.source_time(t) == pytest.approx(oracle_source_time(keys, t, anchor=0.0, panels=4000), abs=1e-5), t


def test_random_curves_match_the_oracle_in_both_directions():
    rng = random.Random(7)
    kinds = [LINEAR, HOLD, EASE_IN, EASE_OUT, EASE_IN_OUT, BEZIER]
    for _case in range(40):
        times = sorted(rng.sample(range(0, 40), rng.randint(2, 6)))
        keys = tuple(
            key(t / 4.0, rng.uniform(-4.0, 4.0), rng.choice(kinds)) for t in times
        )
        reverse = rng.random() < 0.5
        # ancre au milieu de la fenêtre : partir d'une borne vers l'extérieur épuiserait la source à l'instant 0
        tm = PiecewiseTimeMap(-500.0, 500.0, reverse=reverse, keyframes=keys, anchor=0.0, fixed_duration=12.0)
        for _ in range(8):
            t = rng.uniform(0.0, 12.0)
            expected = oracle_source_time(keys, t, anchor=0.0, direction=-1 if reverse else 1)
            assert tm.source_time(t) == pytest.approx(expected, abs=2e-6), (keys, reverse, t)


def test_the_inverse_finds_every_instant_that_shows_a_source_time():
    keys = (key(0, 1.0), key(2, -1.0), key(4, 1.5))
    tm = PiecewiseTimeMap(0.0, 100.0, keyframes=keys, anchor=50.0, fixed_duration=6.0)   # ancre au milieu : pas de sortie
    rng = random.Random(3)
    for _ in range(60):
        t = rng.uniform(0.0, 6.0)
        assert any(abs(found - t) < 1e-6 for found in tm.times_at_source(tm.source_time(t))), t


def test_the_window_of_a_timeline_span_covers_the_turn_not_just_its_ends():
    keys = (key(0, 1.0), key(2, -1.0))
    tm = PiecewiseTimeMap(0.0, 10.0, keyframes=keys)
    low, high = tm.window_for(0.5, 1.5)
    assert high == pytest.approx(0.5, abs=1e-9)                # le sommet (t = 1) est dans la fenêtre
    assert low == pytest.approx(min(tm.source_time(0.5), tm.source_time(1.5)), abs=1e-9)


# ---------------------------------------------------------------------------
# Morceaux à vitesse constante (export)
# ---------------------------------------------------------------------------


def test_the_linearization_keeps_exact_endpoints_and_stays_within_tolerance():
    keys = SHAPES["bezier"]
    tm = PiecewiseTimeMap(0.0, 1000.0, keyframes=keys, fixed_duration=4.0)
    tolerance = 0.02 / 30.0
    for run, pieces in tm.pieces(tolerance):
        assert pieces[0].t0 == run.t0 and pieces[-1].t1 == run.t1
        assert pieces[0].s0 == run.s0 and pieces[-1].s1 == run.s1
        for a, b in zip(pieces, pieces[1:]):
            assert a.t1 == b.t0 and a.s1 == b.s0                           # raccordés : jamais de saut
        for piece in pieces:
            for k in range(1, 20):
                t = piece.t0 + (piece.t1 - piece.t0) * k / 20.0
                approx = piece.s0 + (piece.s1 - piece.s0) * k / 20.0
                assert abs(tm.source_time(t) - approx) <= tolerance * 1.2   # (échantillons plus fins que ceux du découpage)


def test_a_constant_speed_is_a_single_piece_and_a_flat_ramp_needs_few():
    assert [len(p) for _r, p in ConstantTimeMap(0.0, 8.0, 2.0).pieces()] == [1]
    flat = PiecewiseTimeMap(0.0, 100.0, keyframes=(key(0, 1.0), key(2, 1.0)), fixed_duration=4.0)
    assert sum(len(p) for _r, p in flat.pieces()) <= 3


# ---------------------------------------------------------------------------
# Depuis un clip
# ---------------------------------------------------------------------------


def _clip(**fields) -> Clip:
    base = {"id": "c", "asset_id": "a", "track_id": "V1", "timeline_start": 0.0, "source_in": 2.0, "source_out": 12.0}
    base.update(fields)
    return Clip(**base)


def test_a_clip_without_a_curve_gets_the_constant_map_and_the_historical_duration():
    clip = _clip(time_remapping=TimeRemapping(speed=2.0))
    tm = time_map_for_clip(clip)
    assert isinstance(tm, ConstantTimeMap) and tm.duration == (12.0 - 2.0) / 2.0 and not has_speed_curve(clip)


def test_a_frozen_clip_gets_a_hold_map():
    clip = _clip(time_remapping=TimeRemapping(freeze_mode=FreezeFrameMode.FREEZE, freeze_source_time=5.0, freeze_duration=2.0))
    tm = time_map_for_clip(clip)
    assert tm.duration == 2.0 and tm.source_time(1.0) == 5.0


def test_a_clip_with_speed_keyframes_gets_a_piecewise_map_that_starts_at_its_in_point():
    clip = _clip(animation=[key(0, 1.0), key(2, 2.0)])
    tm = time_map_for_clip(clip)
    assert isinstance(tm, PiecewiseTimeMap) and has_speed_curve(clip)
    assert tm.source_time(0.0) == 2.0 and tm.source_time(tm.duration) == pytest.approx(12.0, abs=1e-9)


def test_the_compiled_map_is_shared_between_identical_clips():
    one = _clip(animation=[key(0, 1.0), key(2, 2.0)])
    other = _clip(id="other", timeline_start=40.0, animation=[key(0, 1.0), key(2, 2.0)])
    assert time_map_for_clip(one) is time_map_for_clip(other)               # même mapping, une seule compilation


def test_other_properties_in_the_animation_list_do_not_make_a_speed_curve():
    other = Keyframe("graphic.width", 0.0, 3.0, LINEAR)
    clip = _clip(animation=[other])
    assert not has_speed_curve(clip) and isinstance(time_map_for_clip(clip), ConstantTimeMap)


def test_an_explicit_anchor_starts_the_mapping_inside_the_window():
    remapping = TimeRemapping(anchor=7.0)
    clip = _clip(time_remapping=remapping, animation=[key(0, 1.0)])
    tm = time_map_for_clip(clip)
    assert tm.source_time(0.0) == 7.0 and tm.duration == pytest.approx(5.0, abs=1e-9)   # de 7 s à la fin (12 s) à 100 %


def test_the_signature_changes_with_every_input_that_changes_the_picture():
    base = PiecewiseTimeMap(0.0, 10.0, keyframes=(key(0, 1.0), key(2, 2.0)), fixed_duration=3.0)
    variants = [
        PiecewiseTimeMap(0.0, 10.0, keyframes=(key(0, 1.0), key(2, 2.5)), fixed_duration=3.0),
        PiecewiseTimeMap(0.0, 10.0, keyframes=(key(0, 1.0), key(2, 2.0, HOLD)), fixed_duration=3.0),
        PiecewiseTimeMap(0.0, 11.0, keyframes=(key(0, 1.0), key(2, 2.0)), fixed_duration=3.0),
        PiecewiseTimeMap(0.0, 10.0, reverse=True, keyframes=(key(0, 1.0), key(2, 2.0)), fixed_duration=3.0),
        PiecewiseTimeMap(0.0, 10.0, keyframes=(key(0, 1.0), key(2, 2.0)), fixed_duration=2.5),
    ]
    assert len({repr(base), *map(repr, variants)}) == 1 + len(variants)
