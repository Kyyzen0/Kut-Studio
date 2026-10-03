"""Flux optique sur des scènes synthétiques à vérité terrain exacte (``tests/flow_scenes.py``).

Chaque scène est rendue à des positions fractionnaires : l'image « vraie » à ``t`` est comparée à ce que le moteur fabrique,
et à ce que ferait un simple mélange. Le flux doit **nettement** battre le mélange sur tout mouvement qu'il sait suivre, et
retomber honnêtement (``Fallback``) sur ce qu'il ne sait pas suivre : coupure, flash, images identiques.
"""

from __future__ import annotations

import numpy as np
import pytest
from flow_scenes import Body, Scene, accelerated, linear, rgb

from core import flow_numpy
from core.flow_field import FlowField
from core.optical_flow import (
    BackendPreference,
    BackendUnavailable,
    Fallback,
    FlowParams,
    NumpyBackend,
    OpticalFlowEngine,
    PairAnalysis,
    analysis_plane,
    blend_frames,
    params_for,
    select_backend,
)
from core.time_remapping import FlowQuality

W, H = 320, 180
THRESHOLD = 0.55
FULL = FlowParams(scale=1, levels=5, iterations=3, window=5, smoothing=4)


def region(scene: Scene, a: float, t: float) -> np.ndarray:
    """Pixels où l'objet se trouve à ``a``, ``a + t`` ou ``a + 1`` : c'est là que l'interpolation se joue."""
    mask = np.zeros((H, W), dtype=bool)
    for moment in (a, a + 1.0, a + t):
        mask |= scene.render(moment) > THRESHOLD
    return mask


def iou(got: np.ndarray, truth: np.ndarray) -> float:
    a, b = got > THRESHOLD, truth > THRESHOLD
    return float((a & b).sum() / max(1, (a | b).sum()))


def interpolate(scene: Scene, t: float, *, a: float = 4.0, params: FlowParams = FULL):
    engine = OpticalFlowEngine(FlowQuality.BALANCED)
    frame_a, frame_b = rgb(scene.render(a)), rgb(scene.render(a + 1.0))
    pair = NumpyBackend().analyze(analysis_plane(frame_a, 1), analysis_plane(frame_b, 1), params)
    synthesized = engine.interpolator.interpolate(frame_a, frame_b, pair, t)
    return frame_a, frame_b, pair, synthesized


def region_error(scene: Scene, t: float, **kwargs) -> tuple[float, float, float, float]:
    """(erreur du flux, erreur du mélange, IoU du flux, IoU du mélange) sur la région de l'objet."""
    a = kwargs.get("a", 4.0)
    frame_a, frame_b, _pair, synthesized = interpolate(scene, t, **kwargs)
    truth = scene.render(a + t)
    mask = region(scene, a, t)
    mixed = blend_frames(frame_a, frame_b, t)[:, :, 0]
    flow = synthesized.pixels[:, :, 0]
    return (
        float(np.abs(flow - truth)[mask].mean()), float(np.abs(mixed - truth)[mask].mean()), iou(flow, truth), iou(mixed, truth)
    )


FLAT = lambda x, y: np.full_like(x, 0.78)  # noqa: E731 - objet sans aucune texture
SCENES = {
    "carré horizontal +8 px": Scene(W, H, [Body(60, linear(100, 90, 8, 0))]),
    "carré horizontal +3 px": Scene(W, H, [Body(60, linear(100, 90, 3, 0))]),
    "grand déplacement +24 px": Scene(W, H, [Body(60, linear(80, 90, 24, 0))]),
    "diagonale (7, 5)": Scene(W, H, [Body(60, linear(100, 70, 7, 5))]),
    "accélération": Scene(W, H, [Body(60, accelerated(60, 90, 1.0, 1.2))]),
    "rotation 10°/image": Scene(W, H, [Body(70, linear(160, 90, 0, 0, spin=np.radians(10)))]),
    "objet sans texture": Scene(W, H, [Body(60, linear(100, 90, 6, 0), texture=FLAT)]),
    "deux objets opposés": Scene(W, H, [Body(40, linear(60, 55, 6, 0), depth=0), Body(40, linear(260, 125, -6, 0), depth=1)]),
    "croisement": Scene(W, H, [Body(40, linear(60, 90, 6, 0), depth=1), Body(50, linear(260, 90, -4, 0), depth=0)]),
}


@pytest.mark.parametrize("name", list(SCENES))
@pytest.mark.parametrize("t", [0.25, 0.5, 0.75])
def test_the_flow_places_moving_objects_where_they_are_at_the_requested_moment(name, t):
    flow, blended, flow_iou, blend_iou = region_error(SCENES[name], t)
    assert flow < 0.045, (name, t, flow)
    assert flow < 0.5 * blended, (name, t, flow, blended)             # nettement mieux que le mélange
    assert flow_iou >= 0.95 and flow_iou >= blend_iou - 1e-6, (name, t, flow_iou, blend_iou)


def test_a_zoom_is_followed_at_least_as_well_as_a_blend():
    scene = Scene(W, H, [Body(60, linear(160, 90, 0, 0, scale=1.0, zoom=0.06))])
    for t in (0.25, 0.5, 0.75):
        flow, blended, flow_iou, blend_iou = region_error(scene, t)
        assert flow <= blended + 1e-6 and flow_iou >= 0.99


def test_a_position_error_is_a_fraction_of_a_pixel():
    """Barycentre d'un objet : à moins de 0,3 px de la vérité, à toutes les positions testées."""
    scene = Scene(W, H, [Body(60, linear(100, 90, 8, 5))])
    for t in (0.2, 0.4, 0.6, 0.8):
        _a, _b, _pair, synthesized = interpolate(scene, t)
        got = scene.centroid(synthesized.pixels[:, :, 0])
        truth = scene.centroid(scene.render(4.0 + t))
        assert np.hypot(got[0] - truth[0], got[1] - truth[1]) < 0.3, t


def test_an_object_moving_behind_an_occluder_is_never_worse_than_a_blend():
    occluder = lambda x, y: 0.62 + 0.05 * np.sin(0.5 * x) * np.cos(0.4 * y)  # noqa: E731
    scene = Scene(W, H, [Body(40, linear(-20, 90, 40, 0), depth=0), Body(60, lambda tau: (200, 90, 0, 1), texture=occluder, depth=1)])
    for t in (0.25, 0.5, 0.75):
        flow, blended, _flow_iou, _blend_iou = region_error(scene, t)
        assert flow <= 1.3 * blended + 0.01, (t, flow, blended)


def test_static_content_is_taken_as_it_is_without_estimating_anything():
    scene = Scene(W, H, [Body(60, linear(100, 90, 0, 0))])
    frame_a, _frame_b, pair, synthesized = interpolate(scene, 0.5)
    assert pair.status is Fallback.IDENTICAL and pair.forward is None
    assert synthesized.fallback is Fallback.IDENTICAL and np.array_equal(synthesized.pixels, frame_a)


def test_a_hard_cut_is_never_blended_into_two_ghosted_shots():
    other = Scene(W, H, [Body(70, linear(220, 40, -2, 3))], background=lambda x, y: 0.55 + 0.2 * np.sin(0.6 * x) * np.sin(0.45 * y))
    frame_a = rgb(Scene(W, H, [Body(60, linear(100, 90, 3, 0))]).render(4.0))
    frame_b = rgb(other.render(5.0))
    engine = OpticalFlowEngine(FlowQuality.BALANCED)
    pair = engine.estimator.analyze(frame_a, frame_b)
    assert pair.status is Fallback.SCENE_CUT and pair.forward is None
    early = engine.interpolator.interpolate(frame_a, frame_b, pair, 0.3)
    late = engine.interpolator.interpolate(frame_a, frame_b, pair, 0.5)
    assert early.fallback is Fallback.SCENE_CUT and np.array_equal(early.pixels, frame_a)
    assert np.array_equal(late.pixels, frame_b)                       # à égalité l'image suivante, comme l'échantillonnage


def test_a_flash_is_not_a_cut_and_is_blended_because_the_flow_cannot_explain_it():
    class Flash(Scene):
        def render(self, tau: float, samples: int = 3) -> np.ndarray:
            return np.clip(super().render(tau, samples) + (0.35 if tau >= 5 else 0.0), 0, 1).astype(np.float32)

    scene = Flash(W, H, [Body(60, linear(100, 90, 4, 0))])
    frame_a, frame_b, pair, synthesized = interpolate(scene, 0.5)
    assert pair.status is Fallback.NONE and pair.forward is not None            # la structure est la même : pas une coupure
    assert synthesized.fallback is Fallback.LOW_CONFIDENCE and synthesized.confidence < 0.35
    assert np.allclose(synthesized.pixels, blend_frames(frame_a, frame_b, 0.5))


def test_a_clean_interpolation_reports_a_high_measured_confidence():
    _a, _b, _pair, synthesized = interpolate(SCENES["carré horizontal +8 px"], 0.5)
    assert synthesized.fallback is Fallback.NONE and synthesized.confidence > 0.9 and synthesized.unreliable < 0.05


def test_the_result_is_deterministic():
    scene = SCENES["diagonale (7, 5)"]
    first = interpolate(scene, 0.5)[3].pixels
    second = interpolate(scene, 0.5)[3].pixels
    assert np.array_equal(first, second)


def test_cancelling_interrupts_the_estimation():
    scene = SCENES["carré horizontal +8 px"]
    a, b = scene.render(4.0), scene.render(5.0)
    calls = {"count": 0}

    def cancel() -> bool:
        calls["count"] += 1
        return calls["count"] > 2

    with pytest.raises(flow_numpy.FlowCancelled):
        flow_numpy.estimate_pair(a, b, FULL, cancel)
    assert calls["count"] == 3                                        # arrêt dès la première vérification positive


# ---------------------------------------------------------------------------
# Champ stocké
# ---------------------------------------------------------------------------


def test_a_stored_pair_comes_back_bit_for_bit_and_makes_the_same_image():
    """La forme stockée EST le résultat : un calcul à froid et une lecture du cache fabriquent la même image, au bit près."""
    scene = SCENES["diagonale (7, 5)"]
    frame_a, frame_b, pair, direct = interpolate(scene, 0.5)
    restored = PairAnalysis.unpack(pair.pack())
    assert restored.status is Fallback.NONE and restored.forward is not None and pair.forward is not None
    assert np.array_equal(restored.forward.u, pair.forward.u) and np.array_equal(restored.backward.v, pair.backward.v)
    again = OpticalFlowEngine(FlowQuality.BALANCED).interpolator.interpolate(frame_a, frame_b, restored, 0.5)
    assert np.array_equal(again.pixels, direct.pixels) and again.confidence == direct.confidence


def test_storing_the_field_at_half_resolution_in_half_precision_does_not_cost_accuracy():
    """Mesuré : l'erreur d'une image interpolée est la même (à 0,005 près) avec le champ réduit de moitié qu'avec le champ brut."""
    scene = SCENES["rotation 10°/image"]
    frame_a, frame_b = rgb(scene.render(4.0)), rgb(scene.render(5.0))
    raw_forward, raw_backward = flow_numpy.estimate_pair(scene.render(4.0), scene.render(5.0), FULL)
    raw, _score, _unreliable = flow_numpy.synthesize(frame_a, frame_b, raw_forward, raw_backward, 0.5)
    pair = interpolate(scene, 0.5)[2]
    stored = OpticalFlowEngine(FlowQuality.BALANCED).interpolator.interpolate(frame_a, frame_b, pair, 0.5).pixels
    truth, mask = scene.render(4.5), region(scene, 4.0, 0.5)
    assert pair.forward is not None and pair.forward.width == W // 2
    assert abs(float(np.abs(stored[:, :, 0] - truth)[mask].mean()) - float(np.abs(raw[:, :, 0] - truth)[mask].mean())) < 0.005


def test_a_cut_and_identical_images_are_stored_without_any_flow():
    for status in (Fallback.SCENE_CUT, Fallback.IDENTICAL):
        stored = PairAnalysis(status).pack()
        assert stored["flow"].size == 6                                               # un seul pixel : pas de champ
        assert PairAnalysis.unpack(stored).status is status


@pytest.mark.parametrize("damage", ["shape", "dtype", "status", "nan"])
def test_a_corrupted_stored_pair_is_refused(damage):
    stored = interpolate(SCENES["carré horizontal +8 px"], 0.5)[2].pack()
    if damage == "shape":
        stored["flow"] = stored["flow"][:3]
    elif damage == "dtype":
        stored["flow"] = stored["flow"].astype(np.float32)
    elif damage == "status":
        stored["status"] = np.array([9], dtype=np.int8)
    else:
        stored["flow"][0, 0, 0] = np.nan
    with pytest.raises(ValueError):
        PairAnalysis.unpack(stored)


def test_resizing_a_flow_scales_its_vectors_with_the_grid():
    field = FlowField(np.full((10, 20), 2.0, np.float32), np.full((10, 20), -1.0, np.float32), np.ones((10, 20), np.float32))
    larger = field.resized(80, 40)
    assert (larger.width, larger.height) == (80, 40)
    assert np.allclose(larger.u, 8.0) and np.allclose(larger.v, -4.0) and np.allclose(larger.confidence, 1.0)
    assert field.resized(20, 10) is field


def test_a_flow_field_refuses_planes_of_different_shapes():
    with pytest.raises(ValueError):
        FlowField(np.zeros((4, 4), np.float32), np.zeros((4, 5), np.float32), np.zeros((4, 4), np.float32))


# ---------------------------------------------------------------------------
# Qualité, backends, grille d'analyse
# ---------------------------------------------------------------------------


def test_qualities_trade_resolution_for_effort_and_auto_is_balanced_at_export():
    draft, balanced, best = (params_for(q) for q in (FlowQuality.DRAFT, FlowQuality.BALANCED, FlowQuality.BEST))
    assert (draft.scale, balanced.scale, best.scale) == (4, 2, 1)
    assert draft.iterations < balanced.iterations < best.iterations
    assert params_for(FlowQuality.AUTO) == balanced
    assert len({draft.key(), balanced.key(), best.key()}) == 3


def test_the_engine_identity_changes_with_the_quality():
    identities = {OpticalFlowEngine(quality).identity for quality in (FlowQuality.DRAFT, FlowQuality.BALANCED, FlowQuality.BEST)}
    assert len(identities) == 3
    assert OpticalFlowEngine(FlowQuality.AUTO).identity == OpticalFlowEngine(FlowQuality.BALANCED).identity


def test_auto_and_cpu_select_the_numpy_backend_and_gpu_is_refused_with_the_cause():
    assert select_backend(BackendPreference.AUTO).name == "numpy"
    assert select_backend(BackendPreference.CPU).device == "cpu"
    with pytest.raises(BackendUnavailable, match="GPU"):
        select_backend(BackendPreference.GPU)                          # jamais de repli silencieux vers le processeur


def test_the_analysis_grid_follows_the_requested_scale():
    frame = rgb(np.random.default_rng(0).random((180, 320)).astype(np.float32))
    assert analysis_plane(frame, 1).shape == (180, 320)
    assert analysis_plane(frame, 2).shape == (90, 160)
    assert analysis_plane(frame, 4).shape == (45, 80)


def test_blend_is_exactly_a_times_one_minus_t_plus_b_times_t():
    a = np.full((4, 4, 3), 0.2, np.float32)
    b = np.full((4, 4, 3), 0.8, np.float32)
    assert np.allclose(blend_frames(a, b, 0.25), 0.2 * 0.75 + 0.8 * 0.25)
