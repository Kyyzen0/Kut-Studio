"""Molette de l'inspecteur : un champ ne réagit à la molette que s'il a le focus, sinon le panneau défile.

Qt donne la molette au widget sous le curseur, qu'il ait le focus ou non : faire défiler l'inspecteur au trackpad
changeait au passage « Position X », une opacité ou une liste déroulante survolée. Les bons éditeurs font l'inverse :
la molette n'agit sur un champ que si on l'a choisi (clic dans le champ, ou Tab).

Un seul filtre d'événements, posé sur l'application mais limité aux widgets sous le panneau, couvre tous les champs de
l'inspecteur (Mouvement, compositing, suivi, effets, audio, graphiques, sous-titres), y compris ceux qui sont créés
après coup (paramètres d'effet). L'événement n'est pas perdu : il est renvoyé au parent du champ, donc à la zone
défilante qui le contient.
"""

from __future__ import annotations

import shiboken6
from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QAbstractSlider, QAbstractSpinBox, QApplication, QComboBox, QScrollBar, QWidget

_WHEEL_FIELDS = (QAbstractSpinBox, QAbstractSlider, QComboBox)
"""Les champs que la molette modifie : nombres, curseurs, listes déroulantes."""


class WheelFocusGuard(QObject):
    """Filtre posé par ``install_wheel_guard`` ; sa durée de vie est celle du panneau (son parent Qt)."""

    def __init__(self, panel: QWidget) -> None:
        super().__init__(panel)
        self._panel = panel
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt
        if event.type() != QEvent.Wheel or not isinstance(watched, _WHEEL_FIELDS) or isinstance(watched, QScrollBar):
            return False   # une barre de défilement est faite pour la molette
        panel = self._panel
        if not shiboken6.isValid(panel) or not panel.isAncestorOf(watched):
            return False
        # Un dialogue ouvert depuis le panneau (fenêtre à part) garde ses champs normaux.
        if watched.window() is not panel.window() or watched.hasFocus():
            return False
        # Le panneau défile : l'événement est offert à chaque parent jusqu'à la zone défilante (qui l'accepte). Qt ne
        # remonte pas de lui-même un événement renvoyé par un filtre, d'où la boucle.
        target = watched.parentWidget()
        while target is not None:
            event.ignore()
            QApplication.sendEvent(target, event)
            if event.isAccepted():
                break
            target = target.parentWidget()
        return True


def install_wheel_guard(panel: QWidget) -> WheelFocusGuard:
    """Pose la politique « focus pour la molette » sur tous les champs sous ``panel``."""
    return WheelFocusGuard(panel)


__all__ = ["WheelFocusGuard", "install_wheel_guard"]
