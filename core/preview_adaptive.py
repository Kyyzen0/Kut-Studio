"""Qualité d'aperçu adaptative : baisse temporaire sous charge, remontée progressive.

Logique **pure** et déterministe (le temps est fourni par l'appelant) :
aucun Qt, aucune horloge cachée, donc testable sans attendre.

Ce qu'on mesure, honnêtement
----------------------------

La lecture est cadencée par un timer (40 ms = 25 images/s). Quand
l'aperçu est trop lourd, la boucle d'événements prend du retard et les
ticks arrivent **après** 40 ms. L'intervalle réel entre ticks est donc le
signal de charge : on ne devine rien, on mesure ce que l'utilisateur
subit (une lecture qui n'atteint pas sa cadence).

Politique (anti-« yo-yo »)
--------------------------

- on évalue des **fenêtres** de ticks (≈ 1 s) par leur **médiane**, que
  quelques ticks isolés en retard (un seek, un autre processus) ne
  déplacent pas ;
- **baisser** d'un cran exige ``degrade_after`` fenêtres surchargées
  consécutives ; **remonter** exige ``recover_after`` fenêtres confortables
  consécutives (plus long : on remonte lentement) ;
- un **délai de repos** sépare deux changements ;
- on ne descend jamais sous ``max_divisor`` (1/4 par défaut) tout seul ;
- on ne remonte jamais au-dessus du niveau de base (profil machine ou
  choix de l'utilisateur) ;
- pause, arrêt ou tick isolé après une longue interruption : la fenêtre est
  oubliée, et :meth:`AdaptiveQuality.reset` revient au niveau de base.

Un niveau choisi **explicitement** par l'utilisateur n'est jamais modifié :
voir :class:`core.preview_quality.PreviewQualityController`.
"""

from __future__ import annotations

import statistics
from collections import deque
from dataclasses import dataclass

LADDER: tuple[int, ...] = (1, 2, 4, 8)
"""Diviseurs de résolution : plein, 1/2, 1/4, 1/8."""


@dataclass(frozen=True)
class AdaptiveConfig:
    """Réglages de l'adaptation. Les défauts sont volontairement prudents."""

    target_interval_ms: float = 40.0
    window: int = 25
    degrade_ratio: float = 1.30
    recover_ratio: float = 1.08
    degrade_after: int = 2
    recover_after: int = 6
    cooldown_seconds: float = 2.0
    max_divisor: int = 4
    gap_reset_factor: float = 6.0

    def __post_init__(self) -> None:
        if self.target_interval_ms <= 0 or self.window < 3:
            raise ValueError("Cadence cible et fenêtre doivent être strictement positives.")
        if self.recover_ratio >= self.degrade_ratio:
            raise ValueError("Le seuil de remontée doit être sous le seuil de baisse.")


def _step(divisor: int, direction: int) -> int:
    """Cran voisin de ``divisor`` dans :data:`LADDER` (``direction`` = ±1)."""
    index = LADDER.index(divisor) if divisor in LADDER else 0
    index = max(0, min(len(LADDER) - 1, index + direction))
    return LADDER[index]


class AdaptiveQuality:
    """Choisit un diviseur d'aperçu d'après la cadence réelle de la lecture."""

    def __init__(self, baseline: int = 1, config: AdaptiveConfig | None = None) -> None:
        self.config = config or AdaptiveConfig()
        self._baseline = baseline if baseline in LADDER else 1
        self.divisor = self._baseline
        self._intervals: deque[float] = deque()
        self._last_tick: float | None = None
        self._overloaded = 0
        self._comfortable = 0
        self._last_change: float | None = None
        self.changes = 0

    @property
    def baseline(self) -> int:
        return self._baseline

    @property
    def degraded(self) -> bool:
        """``True`` tant que l'aperçu est plus bas que son niveau de base."""
        return self.divisor > self._baseline

    def set_baseline(self, baseline: int) -> None:
        """Nouveau niveau de base (profil changé, choix de l'utilisateur)."""
        self._baseline = baseline if baseline in LADDER else 1
        self.reset()

    def reset(self) -> None:
        """Retour au niveau de base et oubli des mesures (pause, arrêt, nouveau projet)."""
        self.divisor = self._baseline
        self._intervals.clear()
        self._last_tick = None
        self._overloaded = 0
        self._comfortable = 0

    def pause(self) -> None:
        """Interruption de lecture : on repart de zéro mais on garde l'historique de réglage."""
        self.reset()

    def observe(self, now: float) -> int | None:
        """Enregistre un tick de lecture à l'instant ``now`` (secondes).

        Retourne le **nouveau** diviseur si cet appel le change, sinon
        ``None``.
        """
        cfg = self.config
        previous, self._last_tick = self._last_tick, float(now)
        if previous is None:
            return None
        interval_ms = (float(now) - previous) * 1000.0
        if interval_ms > cfg.target_interval_ms * cfg.gap_reset_factor or interval_ms <= 0:
            # Longue interruption (pause, seek, fenêtre cachée) : ce n'est pas de la charge.
            self._intervals.clear()
            return None
        self._intervals.append(interval_ms)
        if len(self._intervals) < cfg.window:
            return None
        ratio = statistics.median(self._intervals) / cfg.target_interval_ms
        self._intervals.clear()
        return self._evaluate(ratio, float(now))

    def note_load(self, overloaded: bool) -> None:
        """Signal de charge **en plus** de la cadence des ticks.

        Le moniteur GPU rend hors de la boucle de ticks : il peut perdre des
        images (ou dépasser son budget de rendu) alors que les ticks restent à
        l'heure. Une fenêtre où ce signal a été vu compte comme surchargée.
        """
        if overloaded:
            self._external_overload = True

    def force_degrade(self) -> int:
        """Baisse immédiate d'un cran (pression mémoire critique) ; retourne le diviseur."""
        if self.divisor < self.config.max_divisor:
            self.divisor = _step(self.divisor, +1)
            self.changes += 1
        return self.divisor

    def _evaluate(self, ratio: float, now: float) -> int | None:
        if getattr(self, "_external_overload", False):
            ratio = max(ratio, self.config.degrade_ratio)
            self._external_overload = False
        cfg = self.config
        if ratio >= cfg.degrade_ratio:
            self._overloaded += 1
            self._comfortable = 0
        elif ratio <= cfg.recover_ratio:
            self._comfortable += 1
            self._overloaded = 0
        else:
            self._overloaded = 0
            self._comfortable = 0
        resting = self._last_change is not None and now - self._last_change < cfg.cooldown_seconds
        if resting:
            return None
        if self._overloaded >= cfg.degrade_after and self.divisor < cfg.max_divisor:
            return self._change(_step(self.divisor, +1), now)
        if self._comfortable >= cfg.recover_after and self.divisor > self._baseline:
            return self._change(max(self._baseline, _step(self.divisor, -1)), now)
        return None

    def _change(self, divisor: int, now: float) -> int | None:
        if divisor == self.divisor:
            return None
        self.divisor = divisor
        self._last_change = now
        self._overloaded = 0
        self._comfortable = 0
        self.changes += 1
        return divisor


__all__ = ["LADDER", "AdaptiveConfig", "AdaptiveQuality"]
