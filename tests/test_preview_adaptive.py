"""Qualité d'aperçu adaptative : baisse temporaire sous charge, remontée progressive."""

from __future__ import annotations

import pytest

from core.preview_adaptive import AdaptiveConfig, AdaptiveQuality
from core.preview_quality import QUALITY_AUTO, QUALITY_HALF, PreviewQualityController
from core.runtime_profile import MachineResources, resolve_profile

FAST = 0.040   # 25 Hz : la cadence cible
SLOW = 0.065   # lecture qui ne tient pas : 15 Hz


class _Clock:
    """Génère des ticks de lecture à intervalle choisi, sans jamais dormir."""

    def __init__(self, quality, start=100.0):
        self.quality = quality
        self.now = start
        self.changes: list[int] = []

    def ticks(self, count, interval):
        for _ in range(count):
            self.now += interval
            changed = self.quality.observe(self.now)
            if changed is not None:
                self.changes.append(changed)
        return self.changes


def test_sustained_lateness_degrades_one_level_at_a_time():
    quality = AdaptiveQuality(baseline=1)
    clock = _Clock(quality)
    clock.ticks(30, FAST)
    assert quality.divisor == 1
    clock.ticks(120, SLOW)          # ~8 s de lecture trop lourde
    assert clock.changes[0] == 2    # d'abord 1/2, jamais 1/8 d'un coup
    assert quality.degraded


def test_isolated_slow_ticks_do_not_change_the_quality():
    quality = AdaptiveQuality()
    clock = _Clock(quality)
    for _ in range(10):
        clock.ticks(20, FAST)
        clock.ticks(1, 0.200)       # un tick très en retard (seek, autre processus)
    assert quality.divisor == 1 and quality.changes == 0


def test_a_seek_or_pause_gap_is_not_load():
    quality = AdaptiveQuality()
    clock = _Clock(quality)
    clock.ticks(10, SLOW)
    clock.ticks(1, 5.0)             # fenêtre cachée, pause : intervalle énorme
    clock.ticks(10, FAST)
    assert quality.divisor == 1


def test_it_never_goes_below_the_automatic_floor():
    quality = AdaptiveQuality(config=AdaptiveConfig(max_divisor=4))
    clock = _Clock(quality)
    clock.ticks(2000, 0.12)
    assert quality.divisor == 4     # 1/4 au plus ; 1/8 reste un choix explicite de l'utilisateur


def test_cooldown_separates_two_changes():
    config = AdaptiveConfig(cooldown_seconds=10.0, degrade_after=1, window=5)
    quality = AdaptiveQuality(config=config)
    clock = _Clock(quality)
    clock.ticks(6, SLOW)            # le premier tick ne fait qu'amorcer la mesure
    assert clock.changes == [2]
    clock.ticks(20, SLOW)           # 20 × 65 ms = 1,3 s < 10 s de repos
    assert clock.changes == [2]
    clock.ticks(200, SLOW)          # le repos est passé
    assert clock.changes[:2] == [2, 4]


def test_recovery_is_slower_than_degradation_and_stops_at_the_baseline():
    quality = AdaptiveQuality(baseline=1)
    clock = _Clock(quality)
    clock.ticks(120, SLOW)
    assert quality.divisor >= 2
    degraded_at = clock.now
    clock.ticks(40, FAST)           # ~1,6 s de lecture facile : trop tôt pour remonter
    assert quality.divisor >= 2
    clock.ticks(1500, FAST)
    assert quality.divisor == 1 and not quality.degraded
    assert clock.now - degraded_at > 5.0   # la remontée a pris plusieurs secondes
    clock.ticks(500, FAST)
    assert quality.divisor == 1     # jamais au-dessus du niveau de base


def test_recovery_never_exceeds_the_machine_baseline():
    quality = AdaptiveQuality(baseline=2)
    clock = _Clock(quality)
    clock.ticks(120, SLOW)
    assert quality.divisor == 4
    clock.ticks(3000, FAST)
    assert quality.divisor == 2     # le profil machine reste le plafond de qualité


def test_reset_returns_to_the_baseline_immediately():
    quality = AdaptiveQuality(baseline=1)
    _Clock(quality).ticks(120, SLOW)
    assert quality.divisor > 1
    quality.reset()
    assert quality.divisor == 1 and quality.observe(1.0) is None


def test_a_mild_overload_between_the_thresholds_changes_nothing():
    quality = AdaptiveQuality()
    clock = _Clock(quality)
    clock.ticks(600, 0.046)         # 1,15× la cible : entre « confortable » et « surchargé »
    assert quality.divisor == 1 and quality.changes == 0


def test_config_is_validated():
    with pytest.raises(ValueError):
        AdaptiveConfig(target_interval_ms=0)
    with pytest.raises(ValueError):
        AdaptiveConfig(degrade_ratio=1.0, recover_ratio=1.2)


# --- Contrôleur : choix de l'utilisateur ----------------------------------------------------------------


PROFILE = resolve_profile("balanced", MachineResources(logical_cpus=8, memory_bytes=16 * 1024 ** 3))


def test_auto_mode_adapts_and_returns_to_the_profile_baseline():
    controller = PreviewQualityController()
    controller.apply(QUALITY_AUTO, PROFILE)
    assert controller.divisor == controller.baseline == PROFILE.preview_divisor
    clock = _Clock(controller.adaptive)
    now = 0.0
    changed = []
    for _ in range(150):
        now += SLOW
        result = controller.observe_tick(now)
        if result is not None:
            changed.append(result)
    assert changed and controller.degraded and controller.divisor > controller.baseline
    assert controller.suggest() is not None
    assert controller.reset_adaptation() is True
    assert controller.divisor == controller.baseline and not controller.degraded
    assert controller.reset_adaptation() is False
    del clock


def test_a_forced_quality_is_never_modified():
    controller = PreviewQualityController()
    controller.apply(QUALITY_HALF, PROFILE)
    now = 0.0
    for _ in range(500):
        now += 0.2
        assert controller.observe_tick(now) is None
    assert controller.divisor == 2 and not controller.degraded and controller.suggest() is None


def test_a_single_slow_measurement_never_suggests_a_change():
    assert PreviewQualityController().suggest(500.0) is None
