"""Niveaux de qualité d'aperçu.

Les niveaux sont des diviseurs de résolution, pas des scores de
fluidité. ``auto`` reprend le diviseur du profil machine. Aucune
fonction ici ne prétend détecter des images perdues : ce détecteur
n'existe pas encore, et un seuil inventé ferait varier l'image sans
raison vérifiable.

Quand un vrai compteur d'images existera, il pourra appeler
:meth:`PreviewQualityController.suggest` et proposer un niveau. Tant
que cette méthode retourne ``None``, l'interface ne change rien toute
seule.
"""

from __future__ import annotations

from dataclasses import dataclass

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
    """Garde le choix utilisateur et le diviseur résolu.

    :meth:`suggest` est le point d'extension du futur mode Auto réactif.
    Il retourne ``None`` tant qu'on ne lui a pas fourni de mesure réelle.
    """

    requested: str = QUALITY_AUTO
    divisor: int = 1

    def apply(self, quality: str, profile: PerformanceProfile) -> int:
        self.requested = coerce_quality(quality)
        self.divisor = resolve_divisor(self.requested, profile)
        return self.divisor

    def suggest(self, measured_frame_ms: float | None = None) -> str | None:
        """Proposition de qualité, ou ``None`` sans mesure exploitable.

        ``measured_frame_ms`` est accepté pour figer la signature.
        L'ignorer est voulu : aucune heuristique de chute d'images
        n'est branchée dans cette passe.
        """
        del measured_frame_ms
        return None
