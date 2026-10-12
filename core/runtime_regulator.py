"""Régulateur d'aperçu : échelle de dégradation liée au banc (ADR-0001 étape 1).

Descend du **moins visible** au **plus visible**, remonte via la qualité
adaptative (:mod:`core.preview_adaptive`) :

1. proxies préférés pour l'aperçu ;
2. effets de voisinage (flou, glow…) à demi-résolution de travail ;
3. flou de mouvement coupé en lecture (déjà : :func:`core.motion_blur.preview_quality`) ;
4. résolution d'aperçu (diviseur du profil + cran énergie) ;
5. cadence de lecture (jamais à l'arrêt ni en scrubbing — :class:`AdaptiveQuality`) ;
6. segments fidèles : préchargement réduit ou suspendu.

L'**export** n'est jamais dégradé. Le banc machine fixe le cran de base ;
batterie / économie / chauffe ajoutent un cran ; la charge de lecture peut
encore monter le diviseur temporairement.
"""

from __future__ import annotations

from dataclasses import dataclass

from .power_state import PowerState, power_requires_step_down, prefetch_suspended
from .preview_adaptive import LADDER
from .runtime_profile import (
    PROFILE_BALANCED,
    PROFILE_HIGH,
    PROFILE_LOW,
    PerformanceProfile,
)

# Crans de base imposés par le profil mesuré (ou heuristique).
_PROFILE_STEPS: dict[str, int] = {
    PROFILE_HIGH: 0,
    PROFILE_BALANCED: 1,
    PROFILE_LOW: 2,
}


@dataclass(frozen=True)
class RegulatorPolicy:
    """Leviers d'aperçu actifs pour l'instant courant."""

    steps: int
    prefer_proxies: bool
    neighborhood_half: bool
    motion_blur_in_playback: bool
    preview_divisor: int
    prefetch_enabled: bool
    prefetch_max_extra: int
    allow_cadence_drop: bool

    @property
    def neighborhood_scale(self) -> float:
        return 0.5 if self.neighborhood_half else 1.0


@dataclass
class OnDemandGate:
    """Évite de recalculer à l'arrêt quand rien n'a changé.

    Mémorise une empreinte (position grille + paramètres). Un nouvel appel
    avec la même empreinte est ignoré ; toute autre valeur relance le travail.
    """

    _fingerprint: object | None = None

    def should_run(self, fingerprint: object) -> bool:
        if fingerprint == self._fingerprint:
            return False
        self._fingerprint = fingerprint
        return True

    def invalidate(self) -> None:
        self._fingerprint = None


def profile_base_steps(profile_name: str) -> int:
    return _PROFILE_STEPS.get(profile_name, 1)


def bump_divisor(divisor: int, steps: int = 1) -> int:
    """Monte ``steps`` crans dans :data:`LADDER` (jamais au-delà de 1/8)."""
    current = divisor if divisor in LADDER else 1
    index = LADDER.index(current)
    index = min(len(LADDER) - 1, index + max(0, int(steps)))
    return LADDER[index]


def compute_policy(
    profile: PerformanceProfile | str,
    *,
    power: PowerState | None = None,
    preview_divisor: int | None = None,
) -> RegulatorPolicy:
    """Politique d'aperçu d'après le profil (banc) et l'état énergie.

    ``preview_divisor`` : diviseur déjà choisi (profil + Auto adaptatif) ;
    sans argument, celui du profil. Un cran énergie le monte d'un pas de
    plus (sans toucher à un choix utilisateur explicite — l'appelant ne
    passe le pouvoir que pour Auto).
    """
    if isinstance(profile, PerformanceProfile):
        name = profile.name
        base_divisor = profile.preview_divisor
    else:
        name = str(profile)
        base_divisor = {PROFILE_HIGH: 1, PROFILE_BALANCED: 2, PROFILE_LOW: 4}.get(name, 2)
    steps = profile_base_steps(name)
    energy_bump = 0
    if power is not None and power_requires_step_down(power):
        steps += 1
        energy_bump = 1
    steps = min(6, max(0, steps))

    divisor = int(preview_divisor) if preview_divisor is not None else int(base_divisor)
    if energy_bump:
        divisor = bump_divisor(divisor, energy_bump)

    # Prefetch : suspendu sur batterie ; sinon rayon selon les crans.
    if power is not None and prefetch_suspended(power):
        prefetch_enabled = False
        prefetch_max_extra = 0
    elif steps >= 5:
        prefetch_enabled = True
        prefetch_max_extra = 0  # segment courant seulement
    elif steps >= 3:
        prefetch_enabled = True
        prefetch_max_extra = 1
    else:
        prefetch_enabled = True
        prefetch_max_extra = 3

    return RegulatorPolicy(
        steps=steps,
        prefer_proxies=True,
        neighborhood_half=steps >= 1,
        motion_blur_in_playback=False,  # déjà coupé en lecture (motion_blur)
        preview_divisor=divisor,
        prefetch_enabled=prefetch_enabled,
        prefetch_max_extra=prefetch_max_extra,
        allow_cadence_drop=steps >= 4,
    )


def limit_prefetch_requests(requests: list, *, max_extra: int):
    """Garde le segment courant + au plus ``max_extra`` voisins (ordre du plan)."""
    if not requests:
        return []
    kept = []
    extras = 0
    for request in requests:
        role = getattr(request, "role", "")
        if role == "current":
            kept.append(request)
            continue
        if extras < max_extra:
            kept.append(request)
            extras += 1
    return kept


__all__ = [
    "OnDemandGate",
    "RegulatorPolicy",
    "bump_divisor",
    "compute_policy",
    "limit_prefetch_requests",
    "profile_base_steps",
]
