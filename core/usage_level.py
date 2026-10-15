"""Niveau d'usage de l'interface (ADR-0006, étape 4) : simple, Studio, avancé.

Module **pur** (aucune dépendance Qt). Le niveau est une **préférence
utilisateur** (``user_settings.json``, champ ``usage_level``) : il
n'entre jamais dans le ``.kut``. Il ne retire aucune fonction, il décide
seulement ce qui est affiché directement (voir ``docs/usage-levels.md``).

Contrat de :func:`coerce_usage_level` : ne lève jamais ; renvoie le
niveau pour une valeur valide (un :class:`UsageLevel` ou sa chaîne
exacte ``simple`` / ``studio`` / ``advanced``), sinon ``None``. Le
choix du défaut appartient à l'appelant, car il dépend du contexte
(installation neuve ou fichier existant, voir ``core.user_settings``).
"""

from __future__ import annotations

from enum import Enum


class UsageLevel(str, Enum):
    """Niveaux d'usage, du plus simple au plus complet."""

    SIMPLE = "simple"
    STUDIO = "studio"
    ADVANCED = "advanced"

    @property
    def rank(self) -> int:
        """Rang dans l'ordre simple < Studio < avancé."""
        return _ORDER.index(self)

    def at_least(self, minimum: "UsageLevel") -> bool:
        """Vrai si ce niveau (le niveau courant) atteint ``minimum`` (niveau minimal d'un élément)."""
        return self.rank >= minimum.rank


_ORDER: tuple[UsageLevel, ...] = (UsageLevel.SIMPLE, UsageLevel.STUDIO, UsageLevel.ADVANCED)

VALID_USAGE_LEVELS: tuple[str, ...] = tuple(level.value for level in _ORDER)
"""Valeurs de chaîne stables, dans l'ordre croissant."""

DEFAULT_NEW_INSTALL: UsageLevel = UsageLevel.SIMPLE
"""Installation neuve (aucun ``user_settings.json``)."""

DEFAULT_EXISTING: UsageLevel = UsageLevel.ADVANCED
"""Utilisateur d'avant la fonctionnalité, valeur absente ou invalide : rien ne change pour lui."""


def coerce_usage_level(value: object) -> UsageLevel | None:
    """Niveau reconnu, ou ``None`` ; jamais d'exception (booléens, nombres, listes, textes démesurés compris)."""
    if isinstance(value, UsageLevel):
        return value
    if isinstance(value, str) and len(value) <= 16 and value in VALID_USAGE_LEVELS:
        return UsageLevel(value)
    return None


__all__ = [
    "DEFAULT_EXISTING",
    "DEFAULT_NEW_INSTALL",
    "VALID_USAGE_LEVELS",
    "UsageLevel",
    "coerce_usage_level",
]
