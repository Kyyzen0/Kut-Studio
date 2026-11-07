"""Quand proposer le mur d'achat, et les liens qu'il affiche (ADR-0013, étape 15). Sans Qt, sans StoreKit.

Règle : **le mur ne se propose que lorsque l'essai est fini** (état ``EXPIRED``), jamais pendant l'essai, jamais pour un utilisateur qui
a payé, jamais quand l'état est seulement inconnu (hors ligne : on ne vend pas à quelqu'un dont on ne sait rien), et jamais au milieu
d'une édition. Les déclencheurs sont **volontaires** (démarrage, export ouvert, geste refusé) ; ``manual`` (l'utilisateur ouvre lui-même
les offres, depuis la barre d'état ou les préférences) est toujours permis.
"""

from __future__ import annotations

from .model import EntitlementKind, EntitlementState

TRIGGER_STARTUP = "startup"
TRIGGER_EXPORT = "export"
TRIGGER_REFUSED = "refused"
TRIGGER_MANUAL = "manual"
PROACTIVE_TRIGGERS = frozenset({TRIGGER_STARTUP, TRIGGER_EXPORT, TRIGGER_REFUSED})

BANNER_LAST_DAYS = 3
"""La bannière d'essai ne paraît que les trois derniers jours."""

PRIVACY_URL = "https://kyyzen0.github.io/kut-studio-privacy/"
"""Page GitHub Pages du dépôt Kyyzen0/kut-studio-privacy (vérifiée le 2026-11-07 : publique, HTTP 200)."""
TERMS_URL = "https://www.apple.com/legal/internet-services/itunes/dev/stdeula/"
"""TODO (étape 16) : CLUF standard d'Apple, valable tant qu'aucun CLUF propre n'est déposé dans App Store Connect. À confirmer."""
MANAGE_SUBSCRIPTION_URL = "https://apps.apple.com/account/subscriptions"
"""Réglages d'abonnement du compte Apple (annulation, changement de formule)."""


def may_show_paywall(state: EntitlementState, trigger: str, *, mid_edit: bool = False) -> bool:
    """Le mur d'achat peut-il s'ouvrir ? ``mid_edit`` : un geste d'édition est en cours, donc aucun déclencheur automatique
    (``manual`` n'est pas concerné : l'utilisateur ouvre lui-même les offres)."""
    if trigger == TRIGGER_MANUAL:
        return True
    if trigger not in PROACTIVE_TRIGGERS or mid_edit:
        return False
    return state.kind is EntitlementKind.EXPIRED


def banner_days(state: EntitlementState) -> int | None:
    """Jours restants à afficher dans la bannière d'essai (les trois derniers jours seulement), sinon ``None``."""
    if state.kind is EntitlementKind.TRIAL and state.days_left is not None and 1 <= state.days_left <= BANNER_LAST_DAYS:
        return state.days_left
    return None


__all__ = [
    "BANNER_LAST_DAYS", "MANAGE_SUBSCRIPTION_URL", "PRIVACY_URL", "PROACTIVE_TRIGGERS", "TERMS_URL", "TRIGGER_EXPORT",
    "TRIGGER_MANUAL", "TRIGGER_REFUSED", "TRIGGER_STARTUP", "banner_days", "may_show_paywall",
]
