"""Niveaux de qualité d'aperçu.

Les niveaux sont des diviseurs de résolution, pas des scores de
fluidité. ``auto`` part du diviseur du profil machine et peut, **pendant
la lecture seulement**, le monter temporairement quand la cadence réelle
des ticks de lecture ne tient pas (voir :mod:`core.preview_adaptive`),
puis le ramener progressivement. Un niveau choisi explicitement par
l'utilisateur n'est jamais modifié.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .preview_adaptive import AdaptiveQuality
from .runtime_profile import PerformanceProfile


QUALITY_FULL = "full"
QUALITY_HALF = "half"
QUALITY_QUARTER = "quarter"
QUALITY_EIGHTH = "eighth"
QUALITY_AUTO = "auto"

VALID_QUALITIES: tuple[str, ...] = (
    QUALITY_AUTO,
    QUALITY_FULL,
    QUALITY_HALF,
    QUALITY_QUARTER,
    QUALITY_EIGHTH,
)

_DIVISORS: dict[str, int] = {
    QUALITY_FULL: 1,
    QUALITY_HALF: 2,
    QUALITY_QUARTER: 4,
    QUALITY_EIGHTH: 8,
}


def coerce_quality(value: object) -> str:
    """Ramène une valeur inconnue sur ``auto``."""
    if isinstance(value, str) and value in VALID_QUALITIES:
        return value
    return QUALITY_AUTO


def resolve_divisor(quality: str, profile: PerformanceProfile) -> int:
    """Diviseur effectif. ``auto`` suit le profil, le reste est fixe."""
    chosen = coerce_quality(quality)
    if chosen == QUALITY_AUTO:
        return profile.preview_divisor
    return _DIVISORS[chosen]


def output_size(width: int, height: int, divisor: int) -> tuple[int, int]:
    """Taille d'aperçu entière, au moins 2×2 pour rester décodable."""
    step = max(1, int(divisor))
    return (max(2, int(width) // step), max(2, int(height) // step))


def quality_label(divisor: int) -> str:
    """Libellé court : ``Plein``, ``1/2``, ``1/4``, ``1/8``."""
    if divisor <= 1:
        return "Plein"
    return f"1/{int(divisor)}"


@dataclass
class PreviewQualityController:
    """Garde le choix utilisateur, le diviseur de base et le diviseur effectif.

    - ``requested`` : choix de l'utilisateur (``auto``, ``full``, ``half``…) ;
    - ``baseline`` : diviseur de ce choix (pour ``auto``, celui du profil
      machine) ;
    - ``divisor`` : diviseur **effectif**. Il vaut ``baseline`` sauf en mode
      ``auto`` pendant une lecture trop lourde, où
      :class:`core.preview_adaptive.AdaptiveQuality` peut le monter
      temporairement (jamais au-delà de 1/4 tout seul) puis le ramener
      progressivement.

    Un niveau **forcé** par l'utilisateur n'est jamais modifié.
    """

    requested: str = QUALITY_AUTO
    divisor: int = 1
    baseline: int = 1
    adaptive: AdaptiveQuality = field(default_factory=AdaptiveQuality, repr=False)

    def apply(self, quality: str, profile: PerformanceProfile) -> int:
        self.requested = coerce_quality(quality)
        self.baseline = resolve_divisor(self.requested, profile)
        self.adaptive.set_baseline(self.baseline)
        self.divisor = self.baseline
        return self.divisor

    @property
    def adaptive_enabled(self) -> bool:
        return self.requested == QUALITY_AUTO

    @property
    def degraded(self) -> bool:
        return self.adaptive_enabled and self.divisor > self.baseline

    def observe_tick(self, now: float) -> int | None:
        """Signale un tick de lecture ; retourne le nouveau diviseur s'il change.

        Sans effet hors du mode ``auto`` : un niveau choisi par
        l'utilisateur est respecté.
        """
        if not self.adaptive_enabled:
            return None
        changed = self.adaptive.observe(now)
        if changed is not None:
            self.divisor = changed
        return changed

    def reset_adaptation(self) -> bool:
        """Retour au niveau de base (pause, arrêt). ``True`` si le diviseur a changé."""
        self.adaptive.reset()
        changed = self.divisor != self.baseline
        self.divisor = self.baseline
        return changed

    def suggest(self, measured_frame_ms: float | None = None) -> str | None:
        """Qualité conseillée, ou ``None`` tant que rien ne justifie un changement.

        ``measured_frame_ms`` est un intervalle entre images : il est
        rapporté à la cadence cible, mais **une seule mesure ne suffit
        jamais** à proposer un changement (voir :class:`AdaptiveQuality`,
        qui exige des fenêtres de mesures).
        """
        del measured_frame_ms
        if not self.degraded:
            return None
        for name, divisor in _DIVISORS.items():
            if divisor == self.divisor:
                return name
        return None
