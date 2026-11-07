"""Messages des droits d'usage (lecture seule, export bloqué), fusionnés dans :mod:`ui.i18n` (ADR-0013, étape 12).

Aucun lien ni bouton d'achat externe ici (règle 3.1.1 du Store) : le message dit seulement ce qui est refusé et pourquoi.
"""

from __future__ import annotations


def _t(fr: str, en: str, es: str) -> dict[str, str]:
    return {"fr": fr, "en": en, "es": es}


ENTITLEMENTS_TRANSLATIONS: dict[str, dict[str, str]] = {
    "entitlements.edit.expired": _t(
        "Votre essai est terminé : le projet est en lecture seule. Vous pouvez l'ouvrir et le visualiser ; "
        "abonnez-vous ou achetez Kut-Studio pour le modifier.",
        "Your trial has ended: the project is read-only. You can still open and view it; "
        "subscribe or buy Kut-Studio to edit it.",
        "Su prueba ha terminado: el proyecto es de solo lectura. Aún puede abrirlo y verlo; "
        "suscríbase o compre Kut-Studio para editarlo.",
    ),
    "entitlements.edit.unknown": _t(
        "Impossible de vérifier votre accès : le projet est en lecture seule. "
        "Connectez-vous à Internet, puis relancez Kut-Studio.",
        "Your access could not be verified: the project is read-only. Connect to the internet, then restart Kut-Studio.",
        "No se pudo verificar su acceso: el proyecto es de solo lectura. Conéctese a Internet y reinicie Kut-Studio.",
    ),
    "entitlements.export.expired": _t(
        "Votre essai est terminé : l'export est bloqué. Vous pouvez ouvrir et visualiser vos projets ; "
        "abonnez-vous ou achetez Kut-Studio pour exporter.",
        "Your trial has ended: export is blocked. You can still open and view your projects; "
        "subscribe or buy Kut-Studio to export.",
        "Su prueba ha terminado: la exportación está bloqueada. Aún puede abrir y ver sus proyectos; "
        "suscríbase o compre Kut-Studio para exportar.",
    ),
    "entitlements.export.unknown": _t(
        "Impossible de vérifier votre accès : l'export est bloqué. Connectez-vous à Internet, puis relancez Kut-Studio.",
        "Your access could not be verified: export is blocked. Connect to the internet, then restart Kut-Studio.",
        "No se pudo verificar su acceso: la exportación está bloqueada. Conéctese a Internet y reinicie Kut-Studio.",
    ),
}
