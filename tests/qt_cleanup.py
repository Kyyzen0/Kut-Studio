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


def destroy_leftover_windows() -> int:
    """Détruit les fenêtres de l'application restées vivantes à la fin d'un module de tests ; renvoie leur nombre.

    Une fixture de portée module (``audit.make_main_window``) ne fait que ``close()`` : la fenêtre reste vivante, avec
    ses ~2 500 widgets. Or toute feuille de style posée sur l'application (changement de thème) repolit **tous** les
    widgets vivants du processus : mesuré, ~2 s par fenêtre fantôme et par application de style. Après 10 fenêtres
    fantômes dans un worker (``test_inspector_speed_layout``, ``test_design_components``, ``test_ui_small_windows``…),
    un test de préférences prenait 58 s au lieu de 3 s. À appeler quand plus aucun test du module ne tourne.
    """
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtWidgets import QApplication, QWidget

    app = QCoreApplication.instance()
    if app is None:
        return 0
    count = 0
    for widget in QApplication.topLevelWidgets():
        if not isinstance(widget, QWidget) or type(widget).__module__.split(".")[0] != "ui":
            continue
        widget.close()
        widget.deleteLater()
        count += 1
    deliver_deferred_deletes()
    return count
