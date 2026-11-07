"""Droits d'usage : essai, abonnement, achat unique, lecture seule (ADR-0013, étape 12). Sans Qt, sans StoreKit, sans réseau.

Voir ``docs/entitlements.md``. Points d'entrée :

* :class:`EntitlementService` (état courant, horloge injectée) ; :class:`EntitlementStore` (état signé dans le conteneur) ;
* :class:`EntitlementVerifier` (réalisé par ``storekit_verifier.StoreKitVerifier``, étape 13 ; le pont est dans ``storekit_*``) ;
* :func:`require_edit` / :func:`require_export` (les gardes des trois entrées) ; :func:`install_service`.
"""

from .commands import READ_ONLY_COMMANDS, READ_ONLY_MENU_KEYS, is_read_only_command
from .guard import (
    ACTION_EDIT, ACTION_EXPORT, EntitlementRefused, ExportBlockedError, ReadOnlyError, can_edit_now, can_export_now,
    edit_refusal, export_refusal, install_service, installed_service, require_edit, require_export,
)
from .model import (
    DEFAULT_POLICY, EntitlementKind, EntitlementPolicy, EntitlementState, can_edit, can_export, can_open, default_enforced,
    enforced, set_enforced_provider,
)
from .service import EntitlementService
from .store import EntitlementStore, VerifiedEntitlement
from .verifier import EntitlementVerifier, HmacVerifier

__all__ = [
    "ACTION_EDIT", "ACTION_EXPORT", "DEFAULT_POLICY", "EntitlementKind", "EntitlementPolicy", "EntitlementRefused",
    "EntitlementService", "EntitlementState", "EntitlementStore", "EntitlementVerifier", "ExportBlockedError", "HmacVerifier",
    "READ_ONLY_COMMANDS", "READ_ONLY_MENU_KEYS", "ReadOnlyError", "VerifiedEntitlement", "can_edit", "can_edit_now",
    "can_export", "can_export_now", "can_open", "default_enforced", "edit_refusal", "enforced", "export_refusal",
    "install_service", "installed_service", "is_read_only_command", "require_edit", "require_export",
    "set_enforced_provider",
]
