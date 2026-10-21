"""Les tests partagent une vraie ``QApplication`` : l'ordre d'exécution ne doit pas changer le résultat.

Sans elle, un test de rendu passant en premier dans un worker xdist créait une ``QGuiApplication`` nue
(``ensure_qt_gui``) dont héritaient les tests d'interface suivants : plantage du worker ou échec selon la
répartition des tests, donc selon le nombre de cœurs de la machine.
"""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication, QMenu


def test_rendering_helpers_never_leave_an_application_without_widgets():
    from core.mograph_stream import ensure_qt_gui

    ensure_qt_gui()                       # sans ``qapp`` : l'application vient de la fixture de session
    assert isinstance(QCoreApplication.instance(), QApplication)
    QMenu().addAction("ok")               # créer un widget ne doit pas abattre le processus


def test_a_deleted_panel_is_unsubscribed_from_the_language_once_deferred_deletes_are_delivered(qtbot):
    """Le mécanisme dont dépend la fixture automatique de ``conftest`` : ``deleteLater`` + livraison = désabonnement.

    Sans elle, les fenêtres fermées par ``qtbot`` restaient vivantes avec leurs panneaux abonnés : chaque
    ``set_language`` les retraduisait tous (jusqu'à 70 s pour un test, 460 abonnés après une centaine de tests).
    """
    from qt_cleanup import deliver_deferred_deletes

    from ui import i18n
    from ui.layers_panel import LayersPanel

    before = len(i18n._subscribers)
    panel = LayersPanel()
    assert len(i18n._subscribers) == before + 1                 # le panneau s'abonne à la langue

    panel.deleteLater()
    assert len(i18n._subscribers) == before + 1                 # tant que la suppression est différée, il reste abonné
    deliver_deferred_deletes()

    assert len(i18n._subscribers) == before                     # livrée : le signal ``destroyed`` le désabonne


def test_windows_left_alive_by_a_module_fixture_are_destroyed_at_the_end_of_the_module(request, qtbot):
    """Une fenêtre fermée mais vivante coûte ~2 s à chaque feuille de style posée sur l'application (changement de thème).

    Les fixtures de portée module (``audit.make_main_window``) ne font que ``close()`` : sans la destruction de fin de
    module, ``test_usage_level_window`` voyait 16 185 widgets fantômes dans son worker (72 s au lieu de 37 s pour 5 fichiers).
    """
    import weakref

    from qt_cleanup import destroy_leftover_windows

    from ui.layers_panel import LayersPanel

    assert "_destroy_module_windows" in request.fixturenames       # la fixture automatique de conftest est bien posée
    panel = LayersPanel()
    panel.show()
    panel.close()                                                  # fermé, pas détruit : l'état d'une fixture de module
    alive = weakref.ref(panel)
    assert panel in QApplication.topLevelWidgets()
    assert destroy_leftover_windows() >= 1
    from shiboken6 import isValid

    assert not isValid(panel) or alive() is None
