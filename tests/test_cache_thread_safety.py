"""Les caches LRU de module résistent à plusieurs threads.

Les courbes d'animation et les séries de tracking sont évaluées par l'interface **et** par les threads de rendu
(graphe d'aperçu, images de calques). ``get`` puis ``move_to_end`` sans verrou croisaient l'éviction d'un autre thread :
``KeyError`` dans un thread de rendu (le segment échouait) ou pendant une peinture.
"""

from __future__ import annotations

import sys
import threading

import pytest

from core import tracking_motion, visual_effects
from core.visual_effects import TransformKeyframe, transform_curves

THREADS = 6


@pytest.fixture
def maximum_interleaving():
    """Change de thread à chaque instruction ou presque : la course devient reproductible."""
    previous = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    yield
    sys.setswitchinterval(previous)


def _run_threads(work) -> list[str]:
    errors: list[str] = []

    def guarded(index: int) -> None:
        try:
            work(index)
        except BaseException as exc:  # noqa: BLE001 - tout échec d'un thread est le défaut recherché
            errors.append(repr(exc)[:200])

    threads = [threading.Thread(target=guarded, args=(index,)) for index in range(THREADS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return errors


def test_animation_curves_can_be_evaluated_from_several_threads(maximum_interleaving, monkeypatch):
    monkeypatch.setattr(visual_effects, "_CURVE_CACHE_SIZE", 2)             # évictions permanentes

    def work(index: int) -> None:
        for step in range(2500):
            keyframes = [
                TransformKeyframe("scale", 0.0, 1.0 + (index * 100_000 + step) % 7),
                TransformKeyframe("scale", 1.0, 2.0 + step % 5),
            ]
            transform_curves(keyframes, 3.0)
            transform_curves(list(keyframes), 3.0)

    assert _run_threads(work) == []


def test_tracking_series_cache_can_be_used_from_several_threads(maximum_interleaving, monkeypatch):
    monkeypatch.setattr(tracking_motion, "_CACHE_SIZE", 2)

    def work(index: int) -> None:
        for step in range(2500):
            key = (index, step % 9)
            value = tracking_motion._cached(key, lambda key=key: key)
            assert value == key

    assert _run_threads(work) == []


def test_the_results_stay_correct_while_threads_compete(maximum_interleaving, monkeypatch):
    """Le verrou ne change pas les valeurs : chaque thread relit toujours la courbe de ses propres keyframes."""
    monkeypatch.setattr(visual_effects, "_CURVE_CACHE_SIZE", 3)
    mistakes: list[str] = []

    def work(index: int) -> None:
        for step in range(1500):
            end = 2.0 + (index + step) % 6
            curves = transform_curves(
                [TransformKeyframe("scale", 0.0, 1.0), TransformKeyframe("scale", 1.0, end)], 3.0
            )
            value = curves["scale"].evaluate(1.0)
            if abs(value - end) > 1e-9:
                mistakes.append(f"{value} != {end}")

    assert _run_threads(work) == []
    assert mistakes == []
