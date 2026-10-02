"""Flou de mouvement des calques motion graphics.

Le flou est **optionnel** à deux niveaux : un interrupteur global par
séquence (:class:`MotionBlurSettings`) et une case par calque
(``GraphicOverlay.motion_blur``). Il ne concerne que les transforms
(position, rotation, échelle, et tout parent qui bouge).

Principe : l'image du temps ``t`` est la **moyenne** de ``n`` rendus du
calque répartis sur l'intervalle d'obturation, centré sur ``t`` ::

    décalage_i = (i / (n − 1) − 0,5) × (angle / 360) / fps

Un calque immobile pendant l'obturation est rendu une seule fois (aucun
surcoût). Le nombre d'échantillons suit la qualité : aperçu brouillon →
désactivé, standard → réduit, haute qualité et export → réglage complet.
"""

from __future__ import annotations

from dataclasses import dataclass

MAX_SAMPLES = 32
DEFAULT_SAMPLES = 8
DEFAULT_SHUTTER_ANGLE = 180.0

QUALITY_DRAFT = "draft"
QUALITY_STANDARD = "standard"
QUALITY_HIGH = "high"
QUALITY_EXPORT = "export"


@dataclass(frozen=True)
class MotionBlurSettings:
    """Réglages de flou d'une séquence.

    Attributes:
        enabled: interrupteur global (désactiver soulage les machines modestes).
        shutter_angle: angle d'obturation en degrés (180° = demi-image).
        samples: échantillons à l'export (2–32).
    """

    enabled: bool = True
    shutter_angle: float = DEFAULT_SHUTTER_ANGLE
    samples: int = DEFAULT_SAMPLES

    def __post_init__(self) -> None:
        object.__setattr__(self, "enabled", bool(self.enabled))
        try:
            angle = float(self.shutter_angle)
        except (TypeError, ValueError):
            angle = DEFAULT_SHUTTER_ANGLE
        object.__setattr__(self, "shutter_angle", max(0.0, min(720.0, angle if angle == angle else 180.0)))
        try:
            samples = int(self.samples)
        except (TypeError, ValueError):
            samples = DEFAULT_SAMPLES
        object.__setattr__(self, "samples", max(2, min(MAX_SAMPLES, samples)))

    def samples_for(self, quality: str) -> int:
        """Échantillons effectifs pour une qualité de rendu (1 = pas de flou)."""
        if not self.enabled or self.shutter_angle <= 0.0:
            return 1
        if quality == QUALITY_DRAFT:
            return 1
        if quality == QUALITY_STANDARD:
            return max(2, min(4, self.samples))
        return self.samples

    def offsets(self, fps: float, quality: str) -> tuple[float, ...]:
        """Décalages temporels (secondes) des échantillons autour de ``t``."""
        count = self.samples_for(quality)
        if count <= 1 or fps <= 0:
            return (0.0,)
        span = (self.shutter_angle / 360.0) / float(fps)
        return tuple((i / (count - 1) - 0.5) * span for i in range(count))


def settings_to_dict(value: MotionBlurSettings | None) -> dict:
    value = value or MotionBlurSettings()
    return {"enabled": value.enabled, "shutter_angle": value.shutter_angle, "samples": value.samples}


def settings_from_dict(raw: object) -> MotionBlurSettings:
    if not isinstance(raw, dict):
        return MotionBlurSettings()
    return MotionBlurSettings(
        enabled=raw.get("enabled", True),
        shutter_angle=raw.get("shutter_angle", DEFAULT_SHUTTER_ANGLE),
        samples=raw.get("samples", DEFAULT_SAMPLES),
    )


__all__ = [
    "DEFAULT_SAMPLES", "DEFAULT_SHUTTER_ANGLE", "MAX_SAMPLES", "MotionBlurSettings",
    "QUALITY_DRAFT", "QUALITY_EXPORT", "QUALITY_HIGH", "QUALITY_STANDARD",
    "settings_from_dict", "settings_to_dict",
]
