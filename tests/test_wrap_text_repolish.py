"""Un libellé qui passe à la ligne ne se recalcule jamais pendant que Qt repolit les widgets (crash intermittent de la CI)."""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from ui.wrap_text import WrappingPushButton


def test_a_style_change_rewraps_after_the_event_not_during_it(qtbot):
    """``app.setStyleSheet`` envoie ``StyleChange`` à tous les widgets vivants ; recalculer le minimum et le texte à ce moment
    (``setMinimumWidth``, ``setText``) corrompait le repolissage de Qt : segfault au hasard du ramasse-miettes, sur trois systèmes.
    Le recalcul est reporté au tour de boucle suivant (une seule fois, même après plusieurs changements)."""
    button = WrappingPushButton("Un libellé assez long pour passer à la ligne")
    qtbot.addWidget(button)
    button.resize(80, 30)
    calls: list[int] = []
    original = button._rewrap
    button._rewrap = lambda: (calls.append(1), original())[1]           # type: ignore[method-assign]
    QApplication.sendEvent(button, QEvent(QEvent.Type.StyleChange))
    QApplication.sendEvent(button, QEvent(QEvent.Type.FontChange))
    assert calls == [], "le recalcul ne doit pas avoir lieu pendant l'événement"
    QCoreApplication.processEvents()
    assert len(calls) == 1, "un seul recalcul après les deux événements"
