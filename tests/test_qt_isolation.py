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
