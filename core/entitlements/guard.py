"""Les gardes que les trois entrées appellent : ``require_edit()`` et ``require_export()``.

Une seule règle pour tous les chemins (interface, export, scripts et ligne de commande) : si le contrôle est exigé (build Store)
et que l'état ne permet pas l'action, la garde lève une exception **typée** ; l'appelant la traduit en message pour
l'utilisateur (:meth:`EntitlementRefused.user_message`, fr / en / es). Hors Store, les gardes ne font **rien**.

Le service de droits est installé une fois (:func:`install_service`, par l'application ou le pont). Contrôle exigé sans service
installé : on refuse (échec fermé, état ``UNKNOWN`` « no_service »), jamais on n'autorise en silence.
"""

from __future__ import annotations

from .model import (
    DEFAULT_POLICY, REASON_NO_SERVICE, EntitlementKind, EntitlementState, can_edit, can_export, enforced,
)
from .service import EntitlementService

ACTION_EDIT = "edit"
ACTION_EXPORT = "export"

_service: EntitlementService | None = None


def install_service(service: EntitlementService | None) -> None:
    """Installe (ou retire, avec ``None``) le service de droits de l'application."""
    global _service
    _service = service


def installed_service() -> EntitlementService | None:
    return _service


class EntitlementRefused(Exception):  # noqa: N818 - lue comme « le droit est refusé », pas comme une erreur technique
    """Une action refusée par l'état des droits. Pas une ``ValueError`` : aucun ``except ValueError`` du cœur ne l'avale."""

    def __init__(self, action: str, state: EntitlementState) -> None:
        self.action = action
        self.state = state
        super().__init__(f"Droits : {action} refusé (état {state.kind.value}, {state.reason or 'sans motif'}).")

    @property
    def message_key(self) -> str:
        """Clé i18n du message : ``entitlements.<action>.<expired|unknown>``."""
        cause = "unknown" if self.state.kind is EntitlementKind.UNKNOWN else "expired"
        return f"entitlements.{self.action}.{cause}"

    def user_message(self) -> str:
        """Le message pour l'utilisateur, dans la langue courante (les traductions vivent dans ``ui.i18n``)."""
        from ui import i18n

        return i18n.translate(self.message_key)


class ReadOnlyError(EntitlementRefused):
    """Modifier le projet est refusé (lecture seule)."""

    def __init__(self, state: EntitlementState) -> None:
        super().__init__(ACTION_EDIT, state)


class ExportBlockedError(EntitlementRefused):
    """Exporter est refusé."""

    def __init__(self, state: EntitlementState) -> None:
        super().__init__(ACTION_EXPORT, state)


def _current_state() -> tuple[EntitlementState, EntitlementService | None]:
    service = _service
    if service is None:
        return EntitlementState(EntitlementKind.UNKNOWN, reason=REASON_NO_SERVICE), None
    return service.state(), service


def edit_refusal() -> ReadOnlyError | None:
    """L'exception qu'aurait levée :func:`require_edit`, ou ``None`` si modifier est permis (sans la lever)."""
    if not enforced():
        return None
    state, service = _current_state()
    policy = service.policy if service is not None else DEFAULT_POLICY
    return None if can_edit(state, policy, is_enforced=True) else ReadOnlyError(state)


def export_refusal() -> ExportBlockedError | None:
    """Comme :func:`edit_refusal`, pour l'export."""
    if not enforced():
        return None
    state, service = _current_state()
    policy = service.policy if service is not None else DEFAULT_POLICY
    return None if can_export(state, policy, is_enforced=True) else ExportBlockedError(state)


def require_edit() -> None:
    """Lève :class:`ReadOnlyError` si le projet ne peut pas être modifié ; ne fait rien hors Store."""
    refusal = edit_refusal()
    if refusal is not None:
        raise refusal


def require_export() -> None:
    """Lève :class:`ExportBlockedError` si l'export est refusé ; ne fait rien hors Store."""
    refusal = export_refusal()
    if refusal is not None:
        raise refusal


def can_edit_now() -> bool:
    return edit_refusal() is None


def can_export_now() -> bool:
    return export_refusal() is None


__all__ = [
    "ACTION_EDIT", "ACTION_EXPORT", "EntitlementRefused", "ExportBlockedError", "ReadOnlyError", "can_edit_now",
    "can_export_now", "edit_refusal", "export_refusal", "install_service", "installed_service", "require_edit",
    "require_export",
]
