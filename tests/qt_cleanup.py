"""Nettoyage Qt partagé par la suite de tests."""

from __future__ import annotations


def deliver_deferred_deletes(passes: int = 3) -> None:
    """Exécute les ``deleteLater()`` en attente (ce que fait une boucle d'événements à son retour au niveau 0).

    ``qtbot`` ferme les fenêtres puis appelle ``deleteLater()`` ; sans boucle d'événements, la suppression n'arrive jamais
    et les widgets « fermés » restent vivants, abonnés à la langue. Détruire une fenêtre en programme d'autres
    suppressions (menus, panneaux) : plusieurs passes.
    """
    from PySide6.QtCore import QCoreApplication, QEvent

    app = QCoreApplication.instance()
    if app is None:
        return
    for _ in range(passes):
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
