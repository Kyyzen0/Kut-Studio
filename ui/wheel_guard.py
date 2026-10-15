"""Molette de l'inspecteur : un champ ne réagit à la molette que s'il a le focus, sinon le panneau défile.

Qt donne la molette au widget sous le curseur, qu'il ait le focus ou non : faire défiler l'inspecteur au trackpad
changeait au passage « Position X », une opacité ou une liste déroulante survolée. Les bons éditeurs font l'inverse :
la molette n'agit sur un champ que si on l'a choisi (clic dans le champ, ou Tab).

Un seul filtre d'événements, posé champ par champ (jamais sur l'application), couvre l'inspecteur (Mouvement, compositing,
suivi, effets, audio, graphiques, sous-titres). Les champs créés après coup sont adoptés aux points de reconstruction. L'événement n'est pas perdu : il est renvoyé au
parent du champ, donc à la zone défilante qui le contient.
"""

from __future__ import annotations

import shiboken6
from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QAbstractSlider, QAbstractSpinBox, QApplication, QComboBox, QScrollBar, QWidget

_WHEEL_FIELDS = (QAbstractSpinBox, QAbstractSlider, QComboBox)
"""Les champs que la molette modifie : nombres, curseurs, listes déroulantes."""

_ADOPTED = "wheel_guard_adopted"
"""Propriété posée sur un champ qui porte déjà le filtre."""


class WheelFocusGuard(QObject):
    """Filtre posé par ``install_wheel_guard`` ; sa durée de vie est celle du panneau (son parent Qt).

    Le filtre n'est **jamais** installé sur l'application : un filtre d'application est appelé (en Python) pour chaque
    événement de chaque objet du processus, et chaque panneau vivant en ajoutait un (mesuré : 0,7 µs par événement sans
    panneau, 25 µs avec 50 panneaux). Il est installé sur chaque champ, par :meth:`adopt`, et ne s'exécute donc que pour les
    événements de ces champs.
    """

    def __init__(self, panel: QWidget) -> None:
        super().__init__(panel)
        self._panel = panel

    def adopt(self, root: QWidget) -> int:
        """Pose le filtre sur les champs de ``root`` (et de ses descendants) qui n'en ont pas encore ; retourne leur nombre.

        À rappeler quand des champs sont ajoutés après la construction (réglages d'effets, lignes créées à la sélection).
        """
        count = 0
        for kind in _WHEEL_FIELDS:
            for field in root.findChildren(kind):
                if isinstance(field, QScrollBar) or field.property(_ADOPTED):
                    continue
                field.setProperty(_ADOPTED, True)
                field.installEventFilter(self)
                count += 1
        return count

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt
        if event.type() != QEvent.Wheel or not isinstance(watched, QWidget):
            return False
        panel = self._panel
        if not shiboken6.isValid(panel):
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
    """Crée le garde du panneau et l'installe sur les champs qu'il contient déjà."""
    guard = WheelFocusGuard(panel)
    guard.adopt(panel)
    return guard


__all__ = ["WheelFocusGuard", "install_wheel_guard"]
