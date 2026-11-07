"""Textes des écrans d'achat et d'état du build Store (ADR-0013, étape 15), fusionnés dans :mod:`ui.i18n`.

Aucun montant ici : les prix viennent de StoreKit (``Product.displayPrice``, localisés) et s'insèrent par ``{price}``. Ton : factuel, sans
compte à rebours, sans culpabilisation ; ce qui reste possible (ouvrir, visualiser) est dit avant ce qui est bloqué.
"""

from __future__ import annotations


def _t(fr: str, en: str, es: str) -> dict[str, str]:
    return {"fr": fr, "en": en, "es": es}


STORE_SCREENS_TRANSLATIONS: dict[str, dict[str, str]] = {
    # --- mur d'achat ---
    "store.paywall.title": _t("Kut-Studio : continuer à créer", "Kut-Studio: keep creating", "Kut-Studio: sigue creando"),
    "store.paywall.expired": _t(
        "Votre essai gratuit de {days} jours est terminé. Vos projets restent à vous : vous pouvez toujours les ouvrir et les "
        "visualiser, rien n'est supprimé.",
        "Your {days}-day free trial has ended. Your projects remain yours: you can still open and view them, nothing is deleted.",
        "Su prueba gratuita de {days} días ha terminado. Sus proyectos siguen siendo suyos: puede abrirlos y verlos, no se borra nada.",
    ),
    "store.paywall.blocked": _t(
        "Bloqué sans abonnement ni achat : modifier un projet et l'exporter. Toujours possible : ouvrir et visualiser.",
        "Blocked without a subscription or purchase: editing a project and exporting it. Still possible: opening and viewing.",
        "Bloqueado sin suscripción ni compra: editar un proyecto y exportarlo. Sigue siendo posible: abrir y ver.",
    ),
    "store.paywall.trial": _t(
        "Votre essai gratuit se poursuit (jours restants : {days}). Rien n'est bloqué pour le moment.",
        "Your free trial continues (days left: {days}). Nothing is blocked for now.",
        "Su prueba gratuita continúa (días restantes: {days}). Por ahora no se bloquea nada.",
    ),
    "store.paywall.paid": _t(
        "Merci : votre accès à Kut-Studio est actif. Rien n'est bloqué.",
        "Thank you: your access to Kut-Studio is active. Nothing is blocked.",
        "Gracias: su acceso a Kut-Studio está activo. No se bloquea nada.",
    ),
    "store.paywall.unknown": _t(
        "Votre accès n'a pas pu être vérifié (êtes-vous hors ligne ?). Le projet reste en lecture seule le temps de la vérification ; "
        "vous pouvez réessayer ou restaurer vos achats.",
        "Your access could not be verified (are you offline?). The project stays read-only until it is verified; "
        "you can try again or restore your purchases.",
        "No se pudo verificar su acceso (¿está sin conexión?). El proyecto sigue en solo lectura hasta la verificación; "
        "puede reintentarlo o restaurar sus compras.",
    ),
    # --- offres ---
    "store.offer.monthly.name": _t("Abonnement mensuel", "Monthly subscription", "Suscripción mensual"),
    "store.offer.monthly.detail": _t(
        "Se renouvelle automatiquement chaque mois. Annulable à tout moment.",
        "Renews automatically every month. Cancel any time.",
        "Se renueva automáticamente cada mes. Se puede cancelar en cualquier momento.",
    ),
    "store.offer.monthly.price": _t("{price} par mois", "{price} per month", "{price} al mes"),
    "store.offer.yearly.name": _t("Abonnement annuel", "Yearly subscription", "Suscripción anual"),
    "store.offer.yearly.detail": _t(
        "Se renouvelle automatiquement chaque année. Annulable à tout moment.",
        "Renews automatically every year. Cancel any time.",
        "Se renueva automáticamente cada año. Se puede cancelar en cualquier momento.",
    ),
    "store.offer.yearly.price": _t("{price} par an", "{price} per year", "{price} al año"),
    "store.offer.lifetime.name": _t("Achat unique", "One-time purchase", "Compra única"),
    "store.offer.lifetime.detail": _t(
        "Un seul paiement, sans renouvellement ni abonnement.",
        "A single payment, with no renewal or subscription.",
        "Un solo pago, sin renovación ni suscripción.",
    ),
    "store.offer.lifetime.price": _t("{price}, paiement unique", "{price}, one-time payment", "{price}, pago único"),
    "store.offer.price_unavailable": _t("Prix indisponible", "Price unavailable", "Precio no disponible"),
    "store.offer.choose": _t("Choisir", "Choose", "Elegir"),
    "store.offer.choose.accessible": _t("Choisir : {name}", "Choose: {name}", "Elegir: {name}"),
    # --- conditions, liens ---
    "store.terms.subscription": _t(
        "Abonnement : le paiement est prélevé sur votre compte Apple à la confirmation de l'achat. L'abonnement se renouvelle "
        "automatiquement, au prix indiqué, sauf si vous l'annulez au moins 24 heures avant la fin de la période en cours. "
        "Vous gérez et annulez vos abonnements dans les réglages de votre compte Apple.",
        "Subscription: payment is charged to your Apple account when you confirm the purchase. The subscription renews "
        "automatically, at the price shown, unless you cancel it at least 24 hours before the end of the current period. "
        "You manage and cancel your subscriptions in your Apple account settings.",
        "Suscripción: el pago se carga en su cuenta de Apple al confirmar la compra. La suscripción se renueva "
        "automáticamente, al precio indicado, salvo que la cancele al menos 24 horas antes del final del período en curso. "
        "Puede gestionar y cancelar sus suscripciones en los ajustes de su cuenta de Apple.",
    ),
    "store.link.terms": _t("Conditions d'utilisation", "Terms of use", "Condiciones de uso"),
    "store.link.privacy": _t("Politique de confidentialité", "Privacy policy", "Política de privacidad"),
    "store.link.manage": _t("Gérer l'abonnement", "Manage subscription", "Gestionar la suscripción"),
    # --- boutons ---
    "store.button.restore": _t("Restaurer les achats", "Restore purchases", "Restaurar compras"),
    "store.button.readonly": _t("Continuer en lecture seule", "Continue in read-only mode", "Continuar en solo lectura"),
    "store.button.close": _t("Fermer", "Close", "Cerrar"),
    "store.button.offers": _t("Voir les offres", "See the offers", "Ver las ofertas"),
    # --- résultats ---
    "store.msg.busy": _t("En attente de l'App Store…", "Waiting for the App Store…", "Esperando a la App Store…"),
    "store.msg.busy_other": _t("Une opération est déjà en cours.", "An operation is already in progress.", "Ya hay una operación en curso."),
    "store.msg.purchase_done": _t(
        "Merci ! Votre achat est confirmé : Kut-Studio est débloqué.",
        "Thank you! Your purchase is confirmed: Kut-Studio is unlocked.",
        "¡Gracias! Su compra está confirmada: Kut-Studio está desbloqueado.",
    ),
    "store.msg.cancelled": _t("Achat annulé. Rien n'a été débité.", "Purchase cancelled. Nothing was charged.", "Compra cancelada. No se ha cobrado nada."),
    "store.msg.pending": _t(
        "Achat en attente d'approbation (par exemple par le contrôle parental). Votre accès s'ouvrira dès que l'App Store aura "
        "confirmé l'achat.",
        "Purchase awaiting approval (for example parental controls). Your access will open as soon as the App Store confirms it.",
        "Compra pendiente de aprobación (por ejemplo, control parental). Su acceso se abrirá en cuanto la App Store la confirme.",
    ),
    "store.msg.not_confirmed": _t(
        "L'achat n'a pas encore pu être vérifié. Rien n'est débloqué pour l'instant : votre accès s'ouvrira dès que l'App Store "
        "l'aura confirmé. Si cela dure, utilisez « Restaurer les achats ».",
        "The purchase could not be verified yet. Nothing is unlocked for now: your access will open as soon as the App Store "
        "confirms it. If this lasts, use “Restore purchases”.",
        "Aún no se ha podido verificar la compra. Por ahora no se desbloquea nada: su acceso se abrirá en cuanto la App Store "
        "la confirme. Si se prolonga, use «Restaurar compras».",
    ),
    "store.msg.failed": _t(
        "L'achat n'a pas pu être effectué. Réessayez plus tard.",
        "The purchase could not be completed. Try again later.",
        "No se pudo completar la compra. Inténtelo de nuevo más tarde.",
    ),
    "store.msg.unavailable": _t(
        "L'App Store n'est pas joignable pour le moment. Vérifiez votre connexion Internet, puis réessayez.",
        "The App Store cannot be reached right now. Check your internet connection, then try again.",
        "No se puede contactar con la App Store en este momento. Compruebe su conexión a Internet y vuelva a intentarlo.",
    ),
    "store.msg.restore_done": _t(
        "Achats restaurés : Kut-Studio est débloqué.",
        "Purchases restored: Kut-Studio is unlocked.",
        "Compras restauradas: Kut-Studio está desbloqueado.",
    ),
    "store.msg.restore_none": _t(
        "Aucun achat à restaurer pour ce compte Apple.",
        "There is no purchase to restore for this Apple account.",
        "No hay compras que restaurar para esta cuenta de Apple.",
    ),
    "store.msg.restore_failed": _t(
        "La restauration a échoué. Réessayez plus tard.",
        "Restoring failed. Try again later.",
        "No se pudo restaurar. Inténtelo de nuevo más tarde.",
    ),
    # --- préférences, zone « Licence » ---
    "store.license.title": _t("Licence", "License", "Licencia"),
    "store.license.trial": _t(
        "Essai gratuit (jours restants : {days}).", "Free trial (days left: {days}).", "Prueba gratuita (días restantes: {days}).",
    ),
    "store.license.subscriber": _t("Abonné jusqu'au {date}.", "Subscribed until {date}.", "Suscrito hasta el {date}."),
    "store.license.subscriber_grace": _t(
        "Abonnement à confirmer : l'échéance était le {date} et l'App Store n'a pas encore confirmé le renouvellement.",
        "Subscription to be confirmed: it was due on {date} and the App Store has not confirmed the renewal yet.",
        "Suscripción por confirmar: vencía el {date} y la App Store aún no ha confirmado la renovación.",
    ),
    "store.license.lifetime": _t("Achat unique : accès permanent.", "One-time purchase: permanent access.", "Compra única: acceso permanente."),
    "store.license.expired": _t(
        "Essai terminé : lecture seule (ouvrir et visualiser, sans modifier ni exporter).",
        "Trial ended: read-only (open and view, without editing or exporting).",
        "Prueba terminada: solo lectura (abrir y ver, sin editar ni exportar).",
    ),
    "store.license.unknown": _t(
        "Accès non vérifié (hors ligne ?) : lecture seule en attendant la vérification.",
        "Access not verified (offline?): read-only until it is verified.",
        "Acceso no verificado (¿sin conexión?): solo lectura hasta la verificación.",
    ),
    # --- barre d'état ---
    "store.chip.trial": _t("Essai : {days} j", "Trial: {days} d", "Prueba: {days} d"),
    "store.chip.trial.tip": _t(
        "Votre essai gratuit se termine bientôt. Rien n'est bloqué ; cliquez pour voir les offres.",
        "Your free trial ends soon. Nothing is blocked; click to see the offers.",
        "Su prueba gratuita termina pronto. No se bloquea nada; haga clic para ver las ofertas.",
    ),
    "store.chip.readonly": _t("Lecture seule · Voir les offres", "Read-only · See the offers", "Solo lectura · Ver las ofertas"),
    "store.chip.readonly.tip": _t(
        "Modifier et exporter sont bloqués. Cliquez pour voir les offres ou restaurer vos achats.",
        "Editing and exporting are blocked. Click to see the offers or restore your purchases.",
        "Editar y exportar están bloqueados. Haga clic para ver las ofertas o restaurar sus compras.",
    ),
    "store.chip.unknown": _t("Accès non vérifié", "Access not verified", "Acceso no verificado"),
    "store.chip.unknown.tip": _t(
        "L'App Store n'a pas confirmé votre accès. Cliquez pour réessayer ou restaurer vos achats.",
        "The App Store has not confirmed your access. Click to try again or restore your purchases.",
        "La App Store no ha confirmado su acceso. Haga clic para reintentarlo o restaurar sus compras.",
    ),
}
