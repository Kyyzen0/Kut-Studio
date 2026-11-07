"""Textes calculés des écrans du build Store (ADR-0013, étape 15) : état en phrase, résultat d'une action, ouverture d'un lien."""

from __future__ import annotations

from PySide6.QtCore import QDate, QDateTime, QLocale, QUrl
from PySide6.QtGui import QDesktopServices

from core.entitlements import DEFAULT_POLICY, EntitlementKind, EntitlementState
from core.entitlements.storekit_actions import ActionKind, ActionResult, ActionStatus
from ui import i18n

_RESULT_KEYS: dict[tuple[ActionKind, ActionStatus], str] = {
    (ActionKind.PURCHASE, ActionStatus.DONE): "store.msg.purchase_done",
    (ActionKind.PURCHASE, ActionStatus.CANCELLED): "store.msg.cancelled",
    (ActionKind.PURCHASE, ActionStatus.PENDING): "store.msg.pending",
    (ActionKind.PURCHASE, ActionStatus.NOT_CONFIRMED): "store.msg.not_confirmed",
    (ActionKind.RESTORE, ActionStatus.DONE): "store.msg.restore_done",
    (ActionKind.RESTORE, ActionStatus.NOTHING_TO_RESTORE): "store.msg.restore_none",
    (ActionKind.RESTORE, ActionStatus.FAILED): "store.msg.restore_failed",
}


def result_message(result: ActionResult) -> str:
    """La phrase qui dit le résultat d'un achat ou d'une restauration (rien pour un simple chargement de produits)."""
    if result.status is ActionStatus.BUSY:
        return i18n.translate("store.msg.busy_other")
    if result.status is ActionStatus.UNAVAILABLE:
        return i18n.translate("store.msg.unavailable")
    key = _RESULT_KEYS.get((result.kind, result.status))
    if key is not None:
        return i18n.translate(key)
    return i18n.translate("store.msg.failed") if result.kind is ActionKind.PURCHASE and result.status is ActionStatus.FAILED else ""


def format_date(stamp: float | None) -> str:
    if stamp is None:
        return ""
    date = QDateTime.fromSecsSinceEpoch(int(stamp)).date()
    return QLocale(i18n.current_language()).toString(date if isinstance(date, QDate) else QDate(), QLocale.LongFormat)


def license_text(state: EntitlementState) -> str:
    """L'état courant en une phrase (zone « Licence » des préférences et mur d'achat)."""
    kind = state.kind
    if kind is EntitlementKind.TRIAL:
        return i18n.translate("store.license.trial", days=state.days_left or 0)
    if kind is EntitlementKind.SUBSCRIBER:
        key = "store.license.subscriber_grace" if state.in_grace else "store.license.subscriber"
        return i18n.translate(key, date=format_date(state.expires_at))
    if kind is EntitlementKind.LIFETIME:
        return i18n.translate("store.license.lifetime")
    return i18n.translate("store.license.expired" if kind is EntitlementKind.EXPIRED else "store.license.unknown")


def trial_days() -> int:
    return DEFAULT_POLICY.trial_days


def open_link(url: str) -> bool:
    """Ouvre ``url`` dans le navigateur du système (jamais dans l'application)."""
    return bool(QDesktopServices.openUrl(QUrl(url)))
