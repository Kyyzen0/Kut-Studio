"""Les états de droits et les règles qui en découlent (ADR-0013, étape 12). Sans Qt, sans StoreKit, sans réseau, sans horloge.

Cinq états (les noms français de l'ADR entre parenthèses) :

* ``TRIAL`` (ESSAI) : l'essai gratuit court encore, ``days_left`` jours (de 1 à ``EntitlementPolicy.trial_days``) ;
* ``SUBSCRIBER`` (ABONNE) : un abonnement est actif jusqu'à ``expires_at`` (ou ``in_grace`` : l'échéance est passée, le
  renouvellement n'est pas encore confirmé, dans le délai de grâce) ;
* ``LIFETIME`` (ACHAT_UNIQUE) : l'achat unique ;
* ``EXPIRED`` (EXPIRE) : l'essai est fini et rien n'a été acheté (ou l'abonnement est vérifié comme terminé) ;
* ``UNKNOWN`` (INCONNU) : on ne sait pas (jamais vérifié, fichier illisible, signature invalide, abonnement trop ancien
  pour être cru). ``reason`` dit pourquoi.

Règle commune : **ouvrir et visualiser un projet est toujours permis** (:func:`can_open`). Modifier et exporter sont permis
en ``TRIAL``, ``SUBSCRIBER`` et ``LIFETIME`` ; refusés en ``EXPIRED`` ; en ``UNKNOWN`` selon
:attr:`EntitlementPolicy.unknown_allows_edit` (faux par défaut : lecture seule).

Hors Store (:func:`enforced` faux), ``can_edit`` et ``can_export`` sont **toujours vrais** : aucun comportement ne change.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

DAY_SECONDS = 86_400.0


class EntitlementKind(str, Enum):
    TRIAL = "trial"
    SUBSCRIBER = "subscriber"
    LIFETIME = "lifetime"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


# Causes d'un état ``UNKNOWN`` (``EntitlementState.reason``).
REASON_MISSING = "missing"
"""Aucun état n'a jamais été vérifié (premier lancement, avant la réponse du pont)."""
REASON_UNREADABLE = "unreadable"
"""Le fichier existe mais n'est pas lisible (absent du format, tronqué, modifié à la main)."""
REASON_BAD_SIGNATURE = "bad_signature"
"""Le fichier est lisible mais sa signature ne correspond pas."""
REASON_STALE = "stale"
"""Un abonnement dont l'échéance et le délai de grâce sont passés sans nouvelle vérification."""
REASON_NO_SERVICE = "no_service"
"""Le contrôle est exigé (Store) mais aucun service de droits n'est installé : on refuse (échec fermé)."""


@dataclass(frozen=True)
class EntitlementState:
    """Un état de droits, calculé (jamais lu tel quel dans un fichier modifiable).

    Attributes:
        kind: l'état.
        days_left: jours d'essai restants (``TRIAL`` seulement).
        expires_at: échéance de l'abonnement, en secondes depuis l'époque (``SUBSCRIBER`` seulement).
        in_grace: abonnement échu mais toléré le temps du délai de grâce (``SUBSCRIBER``).
        reason: cause d'un état ``UNKNOWN`` (voir les constantes ``REASON_*``) ou, pour ``EXPIRED``, ``"trial_ended"``.
        clock_rollback: l'horloge a été reculée ; le calcul est resté sur la plus grande date vue (pas de rallonge).
    """

    kind: EntitlementKind
    days_left: int | None = None
    expires_at: float | None = None
    in_grace: bool = False
    reason: str = ""
    clock_rollback: bool = False


@dataclass(frozen=True)
class EntitlementPolicy:
    """Les chiffres de la politique économique. **Aucun n'est figé** : D11 de l'ADR-0013 reste ouvert.

    Attributes:
        trial_days: durée de l'essai (D3 : 15 jours).
        grace_seconds: délai de grâce d'un abonnement dont l'échéance est passée sans confirmation (proposition de l'ADR :
            7 jours ; **valeur par défaut documentée, à décider**).
        clock_tolerance: écart toléré (secondes) entre l'horloge et la plus grande date vue avant de conclure à un recul.
        persist_interval: intervalle minimal (secondes) entre deux écritures de la plus grande date vue.
        unknown_allows_edit: ``UNKNOWN`` permet-il de modifier et d'exporter ? Faux par défaut (lecture seule) ; D11 propose
            « conserver le dernier état vérifié jusqu'au délai de grâce » : c'est ce que fait le service pour un abonnement
            (``SUBSCRIBER`` avec ``in_grace``), mais sans dernier état vérifié lisible il n'y a rien à conserver.
    """

    trial_days: int = 15
    grace_seconds: float = 7 * DAY_SECONDS
    clock_tolerance: float = 300.0
    persist_interval: float = 3600.0
    unknown_allows_edit: bool = False


DEFAULT_POLICY = EntitlementPolicy()

# --- Le contrôle est-il exigé ? ---------------------------------------------------------------------------------------

_enforced_override: Callable[[], bool] | None = None


def default_enforced() -> bool:
    """Lit ``core.build_flavor.STORE`` si ce module existe (étape 4), sinon ``False`` (import tolérant)."""
    try:
        from core import build_flavor  # type: ignore[attr-defined]  # le module vient de l'étape 4 (autre PR) : absent, on tolère
    except ImportError:
        return False
    return bool(getattr(build_flavor, "STORE", False))


def set_enforced_provider(provider: Callable[[], bool] | None) -> None:
    """Remplace la source de ``enforced()`` (tests, pont) ; ``None`` rétablit :func:`default_enforced`."""
    global _enforced_override
    _enforced_override = provider


def enforced() -> bool:
    """Vrai quand les droits sont contrôlés (build Store). Faux : l'application se comporte comme avant."""
    provider = _enforced_override
    return bool(provider()) if provider is not None else default_enforced()


# --- Ce que chaque état permet ----------------------------------------------------------------------------------------

_PAID_OR_TRIAL = frozenset({EntitlementKind.TRIAL, EntitlementKind.SUBSCRIBER, EntitlementKind.LIFETIME})


def can_open(state: EntitlementState | None = None) -> bool:
    """Ouvrir et visualiser un projet : **toujours** permis (décision D3), quel que soit l'état."""
    return True


def _allowed(state: EntitlementState, policy: EntitlementPolicy, is_enforced: bool | None) -> bool:
    if not (enforced() if is_enforced is None else is_enforced):
        return True
    if state.kind in _PAID_OR_TRIAL:
        return True
    return state.kind is EntitlementKind.UNKNOWN and policy.unknown_allows_edit


def can_edit(
    state: EntitlementState, policy: EntitlementPolicy = DEFAULT_POLICY, *, is_enforced: bool | None = None,
) -> bool:
    """Modifier un projet. ``is_enforced`` (défaut ``None`` : :func:`enforced`) : faux, toujours vrai (hors Store)."""
    return _allowed(state, policy, is_enforced)


def can_export(
    state: EntitlementState, policy: EntitlementPolicy = DEFAULT_POLICY, *, is_enforced: bool | None = None,
) -> bool:
    """Exporter (rendu, livrables, fichiers de sortie). Mêmes règles que :func:`can_edit`."""
    return _allowed(state, policy, is_enforced)


__all__ = [
    "DAY_SECONDS", "DEFAULT_POLICY", "EntitlementKind", "EntitlementPolicy", "EntitlementState", "REASON_BAD_SIGNATURE",
    "REASON_MISSING", "REASON_NO_SERVICE", "REASON_STALE", "REASON_UNREADABLE", "can_edit", "can_export", "can_open",
    "default_enforced", "enforced", "set_enforced_provider",
]
